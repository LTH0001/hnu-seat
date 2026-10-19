"""桌面预约入口：登录检查、自动捕获本站 Cookie、预约校验与提交。"""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import threading
import sys
import time
import datetime as dt
import tkinter as tk
from tkinter import ttk
from tkinter import filedialog
from environment_setup import find_wechat
from urllib.parse import urlsplit

import requests
from book import parse_date, parse_time
from libseat import Seat, SeatError, client_from_config, load_config
from renew_cookie import cookie_header
from booking_recovery import submit_with_recovery
import daily_schedule
from app_runtime import DATA_ROOT, launch_command
import tempfile

ROOT = DATA_ROOT
CONFIG = ROOT / 'config.json'
HIDDEN = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def tshark_path():
    found = shutil.which('tshark')
    if found:
        return found
    candidates = [Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Wireshark/tshark.exe']
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\wireshark.exe') as key:
            candidates.insert(0, Path(winreg.QueryValue(key, None)).with_name('tshark.exe'))
    except (ImportError, OSError):
        pass
    for path in candidates:
        if path.is_file():
            return str(path)
    raise RuntimeError('未找到 tshark.exe，请安装 Wireshark（包含 TShark 和 Npcap）。')


def validated_client(cfg, cookie=None):
    client = client_from_config({**cfg, **({'cookie': cookie} if cookie is not None else {})})
    try:
        rows = client.current()
        return client, rows
    except Exception:
        client.session.close()
        raise


def save_cookie(client):
    save_settings({'cookie': cookie_header(client)})


def save_settings(changes):
    raw = json.loads(CONFIG.read_text(encoding='utf-8'))
    raw.update(changes)
    temp = CONFIG.with_suffix('.json.tmp')
    temp.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temp.replace(CONFIG)


def capture_login(cfg, emit):
    tool = tshark_path()
    result = subprocess.run([tool, '-D'], capture_output=True, encoding='utf-8',
                            errors='replace', timeout=15, creationflags=HIDDEN)
    interfaces = re.findall(r'^(\d+)\. (.+)$', result.stdout, re.M)
    loopbacks = [number for number, name in interfaces if 'loopback' in name.lower()]
    import psutil
    active_names = [name for name, stats in psutil.net_if_stats().items() if stats.isup]
    chosen = list(dict.fromkeys(loopbacks + [
        number for number, name in interfaces
        if any('(' + active + ')' in name for active in active_names)
        and not any(s in name.lower() for s in ('etwdump', 'sshdump', 'udpdump', 'ciscodump', 'randpkt', 'usbpcap'))]))
    if not chosen:
        raise RuntimeError('没有可捕获网卡，请检查 Npcap 安装。')
    host = urlsplit(cfg['base_url']).hostname
    if not host or not re.fullmatch(r'[A-Za-z0-9.-]+', host):
        raise RuntimeError('base_url 的域名格式无效。')
    command = [tool, '-n', '-l']
    proxy_port = urlsplit(cfg.get('proxy', '')).port
    if proxy_port:
        command += ['-d', f'tcp.port=={proxy_port},http']
    for number in chosen:
        command += ['-i', number]
    automatic = cfg.get('wechat_navigation', {}).get('enabled', False)
    duration = 45 if automatic else 40
    command += ['-a', f'duration:{duration}', '-Y', f'http.host == "{host}" && http.cookie',
                '-T', 'fields', '-e', 'http.cookie', '-E', 'occurrence=f']
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               encoding='utf-8', errors='replace', creationflags=HIDDEN)
    emit('正在自动进入微信预约页面并获取登录信息…' if automatic else
         '正在获取登录信息（40 秒）：请现在切到微信，从正式入口重新进入并点击“座位预约”。')
    try:
        if automatic:
            time.sleep(1)
            with tempfile.TemporaryDirectory(prefix='zzlibrary-navigation-') as directory:
                output_file = Path(directory) / 'navigation.txt'
                navigation = subprocess.run(
                    launch_command('--navigate', '--exe', find_wechat(cfg.get('wechat_navigation', {}).get('exe', '')),
                                   '--nav-output', str(output_file)),
                    capture_output=True, encoding='utf-8', errors='replace', timeout=85,
                    creationflags=HIDDEN, env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
                navigation.stdout = output_file.read_text(encoding='utf-8') if output_file.exists() else navigation.stdout
            if navigation.returncode:
                process.kill()
                process.communicate()
                raise RuntimeError((navigation.stdout.strip() or '微信导航失败。') +
                                   ' 可关闭自动导航，使用“更新登录”手动进入。')
            emit(navigation.stdout.strip())
        stdout, stderr = process.communicate(timeout=duration + 15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise RuntimeError('抓取超时，请重试。') from None
    except Exception:
        if process.poll() is None:
            process.kill()
            process.communicate()
        raise
    if process.returncode:
        raise RuntimeError('抓取失败：' + stderr.strip()[-500:])
    # 只收集目标 Host 请求的 Cookie；不写原始流量或打印凭据。
    cookies = list(dict.fromkeys(line.strip() for line in stdout.splitlines() if line.strip()))
    emit(f'已捕获 {len(cookies)} 组本站登录信息，正在验证（不显示 Cookie）。')
    for cookie in reversed(cookies):
        try:
            client, rows = validated_client(cfg, cookie)
        except SeatError:
            continue
        save_cookie(client)
        emit('新登录已验证，Cookie 已自动保存。')
        return client, rows
    raise RuntimeError('未获取到有效登录。请确认微信能进入预约页面；代理关闭时请同步清空配置 proxy。')


def same_booking(row, seats, day, start, end):
    return (row.get('SeatName') in seats and str(row.get('BookDate', ''))[:10] == day
            and str(row.get('StartTime', ''))[11:16] == f'{start // 60:02d}:{start % 60:02d}'
            and str(row.get('EndTime', ''))[11:16] == f'{end // 60:02d}:{end % 60:02d}')


class App:
    def __init__(self, root):
        self.root = root
        self.events = queue.Queue()
        self.busy = False
        root.title('图书馆一键预约')
        root.geometry('720x760')
        cfg = load_config(str(CONFIG))
        panel = ttk.Frame(root, padding=18)
        panel.pack(fill='both', expand=True)
        self.seats = tk.StringVar(value=','.join(dict.fromkeys(x['seat_name'] for x in cfg.get('candidates', []))))
        initial_seats = self.seats.get().split(',') if self.seats.get() else []
        self.seat_rows = [tk.StringVar(value=initial_seats[i] if i < len(initial_seats) else '') for i in range(3)]
        self.date = tk.StringVar(value=cfg.get('seatdate', 'auto'))
        self.date_choice = tk.StringVar()
        t0, t1 = parse_time(cfg, None)
        self.start = tk.StringVar(value=f'{t0//60:02d}:{t0%60:02d}')
        self.end = tk.StringVar(value=f'{t1//60:02d}:{t1%60:02d}')
        self.auto_navigation = tk.BooleanVar(value=cfg.get('wechat_navigation', {}).get('enabled', False))
        self._save_after = None
        self.seats.trace_add('write', self.queue_save_seats)
        root.protocol('WM_DELETE_WINDOW', self.close)
        ttk.Label(panel, text='候选座位（从上到下尝试，成功即停；可留空，修改自动保存）').pack(anchor='w')
        for i, variable in enumerate(self.seat_rows, 1):
            row = ttk.Frame(panel)
            row.pack(fill='x', pady=(0, 7))
            ttk.Label(row, text=f'第 {i} 优先', width=10).pack(side='left')
            ttk.Entry(row, textvariable=variable).pack(side='left', fill='x', expand=True)
            variable.trace_add('write', self.combine_seat_rows)
        ttk.Label(panel, text='预约日期').pack(anchor='w')
        self.date_picker = ttk.Combobox(panel, textvariable=self.date_choice, state='readonly',
                                       postcommand=self.refresh_date_options)
        self.date_picker.pack(fill='x', pady=(0, 7))
        self.date_picker.bind('<<ComboboxSelected>>', self.select_date)
        self.refresh_date_options()
        for label, variable in [('开始时间', self.start), ('结束时间', self.end)]:
            ttk.Label(panel, text=label).pack(anchor='w')
            ttk.Entry(panel, textvariable=variable).pack(fill='x', pady=(0, 7))
        ttk.Checkbutton(panel, text='登录失效时自动打开微信公众号获取 Cookie',
                        variable=self.auto_navigation).pack(anchor='w')
        bar = ttk.Frame(panel)
        bar.pack(fill='x', pady=5)
        self.buttons = []
        for label, action in [('一键预约（立即提交）', 'book'), ('查询当前预约', 'status'), ('更新登录', 'login')]:
            button = ttk.Button(bar, text=label, command=lambda a=action: self.run(a))
            button.pack(side='left', padx=(0, 8))
            self.buttons.append(button)
        ttk.Button(bar, text='微信程序', command=self.choose_wechat).pack(side='left')
        schedule = ttk.LabelFrame(panel, text='每天定时预约明天的座位', padding=10)
        schedule.pack(fill='x', pady=8)
        preferences = cfg.get('daily_schedule', {})
        self.schedule_enabled = tk.BooleanVar(value=preferences.get('enabled', False))
        self.schedule_clock = tk.StringVar(value=preferences.get('clock', '22:00'))
        scheduled_times = preferences.get('datetime', [self.start.get(), self.end.get()])
        self.schedule_start = tk.StringVar(value=scheduled_times[0])
        self.schedule_end = tk.StringVar(value=scheduled_times[1])
        self.startup_enabled = tk.BooleanVar(value=cfg.get('startup_check_enabled', True))
        ttk.Checkbutton(schedule, text='启用每日预约', variable=self.schedule_enabled).grid(row=0, column=0, sticky='w')
        ttk.Label(schedule, text='自动运行时间').grid(row=0, column=1, padx=8)
        ttk.Entry(schedule, textvariable=self.schedule_clock, width=8).grid(row=0, column=2)
        button = ttk.Button(schedule, text='保存定时设置', command=self.save_schedule)
        button.grid(row=0, column=3, padx=10)
        self.buttons.append(button)
        ttk.Button(schedule, text='查看定时日志', command=self.show_schedule_log).grid(row=0, column=4)
        times = ttk.Frame(schedule)
        times.grid(row=1, column=0, columnspan=5, sticky='w', pady=6)
        for label, variable in [('预约开始', self.schedule_start), ('预约结束', self.schedule_end)]:
            ttk.Label(times, text=label).pack(side='left', padx=(0, 6))
            choices = list(dict.fromkeys([f'{minute//60:02d}:{minute%60:02d}' for minute in range(0, 1440, 30)] + [variable.get()]))
            ttk.Combobox(times, textvariable=variable, values=sorted(choices), width=7).pack(side='left', padx=(0, 16))
        ttk.Label(times, text='北京时间，HH:MM').pack(side='left')
        self.schedule_summary = tk.StringVar(value='正在读取计划…')
        ttk.Label(schedule, textvariable=self.schedule_summary, wraplength=650).grid(row=2, column=0, columnspan=5, sticky='w', pady=5)
        ttk.Label(schedule, text='修改时间后点“保存定时设置”；提前 5 分钟准备登录，失败后自动检查补跑。').grid(row=3, column=0, columnspan=5, sticky='w')
        ttk.Label(schedule, text='电脑需开机、已登录 Windows；微信导航需要桌面解锁。').grid(row=4, column=0, columnspan=5, sticky='w')
        ttk.Checkbutton(schedule, text='打开程序时检查固定预约，缺少时自动补约最近时段',
                        variable=self.startup_enabled,
                        command=lambda: save_settings({'startup_check_enabled': self.startup_enabled.get()})).grid(row=5, column=0, columnspan=5, sticky='w')
        self.output = tk.Text(panel, height=9, wrap='word', state='disabled')
        self.output.pack(fill='both', expand=True, pady=8)
        self.log('Cookie 有效时直接预约；失效时自动获取，请按提示在微信重新授权。')
        root.after(100, self.poll)
        root.after(200, self.refresh_schedule)
        root.after(30000, self.schedule_tick)
        if '--self-check' not in sys.argv and '--login' not in sys.argv:
            root.after(1000, self.check_startup_booking)

    def check_startup_booking(self):
        if not self.startup_enabled.get() or not self.root.winfo_viewable():
            return
        if self.busy:
            self.root.after(1000, self.check_startup_booking)
            return
        self.log('正在检查每日固定预约，缺少时自动尝试最近时段…')
        self.run('startup')

    def schedule_tick(self):
        self.refresh_schedule()
        self.root.after(30000, self.schedule_tick)

    def choose_wechat(self):
        path = filedialog.askopenfilename(title='选择微信主程序 Weixin.exe', filetypes=[('微信程序', '*.exe')])
        if path:
            if Path(path).name.lower() not in ('weixin.exe', 'wechat.exe'):
                self.log('请选择微信主程序 Weixin.exe 或 WeChat.exe。')
                return
            cfg = load_config(str(CONFIG))
            save_settings({'wechat_navigation': {**cfg.get('wechat_navigation', {}), 'exe': path}})
            self.log('微信程序路径已保存。自动导航主要适配 Weixin.exe；其他版本需实际验证。')

    def refresh_date_options(self):
        today = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date()
        self.date_options = {f'{name}（{today + dt.timedelta(days=offset)}）': token
                             for offset, name, token in [(0, '今天', 'today'), (1, '明天', 'tomorrow'), (2, '后天', 'dayafter')]}
        self.date_options['自动（最远可预约日期）'] = 'auto'
        raw = self.date.get()
        if raw not in self.date_options.values():
            try:
                day = parse_date(load_config(str(CONFIG)), raw)
                self.date_options[f'指定日期（{day}）'] = raw
            except SystemExit:
                raw = 'today'
                self.date.set(raw)
        self.date_picker.configure(values=list(self.date_options))
        self.date_choice.set(next(label for label, token in self.date_options.items() if token == raw))

    def select_date(self, _=None):
        self.date.set(self.date_options[self.date_choice.get()])

    def queue_save_seats(self, *_):
        if self._save_after is not None:
            self.root.after_cancel(self._save_after)
        self._save_after = self.root.after(600, self.persist_seats)

    def combine_seat_rows(self, *_):
        self.seats.set(','.join(variable.get().strip() for variable in self.seat_rows if variable.get().strip()))

    def persist_seats(self):
        self._save_after = None
        names = list(dict.fromkeys(x.strip() for x in self.seats.get().replace('，', ',').split(',') if x.strip()))
        if not names or any(not re.fullmatch(r'(?:\d+-[A-Za-z0-9]+-\d+|HNU[A-Za-z0-9]+)', n) for n in names):
            return  # 未输入完整时保留上一次有效座位。
        try:
            cfg = load_config(str(CONFIG))
            previous = cfg.get('candidates', []) + cfg.get('daily_schedule', {}).get('candidates', [])
            candidates = [next((s for s in previous if s['seat_name'] == name), {'seat_name': name}) for name in names]
            changes = {'candidates': candidates}
            if 'daily_schedule' in cfg:
                changes['daily_schedule'] = {**cfg['daily_schedule'], 'candidates': candidates}
            save_settings(changes)
            self.refresh_schedule()
        except (OSError, ValueError):
            self.log('座位保存失败，请重试。')

    def close(self):
        if self._save_after is not None:
            self.root.after_cancel(self._save_after)
        self.persist_seats()
        self.root.destroy()

    def show_schedule_log(self):
        try:
            report = json.loads((ROOT / 'last_scheduled_result.json').read_text(encoding='utf-8'))
            self.log('定时执行：' + report['finished_at'])
            for message in report['messages']:
                self.log(message)
        except FileNotFoundError:
            self.log('还没有定时执行记录。')
        except (OSError, ValueError, KeyError):
            self.log('无法读取定时执行记录。')

    def refresh_schedule(self):
        try:
            cfg = load_config(str(CONFIG)).get('daily_schedule', {})
            state = daily_schedule.status()
            if state['enabled']:
                names = ', '.join(s['seat_name'] for s in cfg.get('candidates', []))
                times = cfg.get('datetime', [])
                self.schedule_summary.set(f"已启用：每天 {cfg.get('clock', '22:00')} → 明天 {names}，{'–'.join(times)}；下次 {state.get('next_run', '')}")
            else:
                self.schedule_summary.set('每日预约已停用。')
            result = ROOT / 'last_scheduled_result.json'
            if result.exists():
                report = json.loads(result.read_text(encoding='utf-8'))
                self.schedule_summary.set(self.schedule_summary.get() + '\n最近执行：' + report['finished_at'] + '，' + ('成功' if report['exit_code'] == 0 else '失败，请查看运行记录'))
        except Exception:
            self.schedule_summary.set('无法读取计划状态，请检查 Windows 任务计划服务。')

    def save_schedule(self):
        if self.busy:
            return
        try:
            clock = daily_schedule.parse_clock(self.schedule_clock.get())
            start, end = parse_time({}, self.schedule_start.get() + ',' + self.schedule_end.get())
            names = list(dict.fromkeys(x.strip() for x in self.seats.get().replace('，', ',').split(',') if x.strip()))
            if self.schedule_enabled.get() and not names:
                raise ValueError('请填写候选座位。')
            cfg = load_config(str(CONFIG))
            candidates = [next((s for s in cfg.get('candidates', []) if s['seat_name'] == name), {'seat_name': name}) for name in names]
            settings = {'enabled': self.schedule_enabled.get(), 'clock': clock,
                        'candidates': candidates,
                        'datetime': [f'{start//60:02d}:{start%60:02d}', f'{end//60:02d}:{end%60:02d}'],
                        'auto_navigation': self.auto_navigation.get()}
            old_schedule = cfg.get('daily_schedule', {})
            settings['enabled_since'] = old_schedule.get('enabled_since', __import__('datetime').datetime.now().isoformat(timespec='seconds'))
            if not old_schedule.get('enabled') or old_schedule.get('clock') != clock:
                settings['enabled_since'] = __import__('datetime').datetime.now().isoformat(timespec='seconds')
            # 先保存目标；任务安装失败时保持 disabled，避免界面错误宣称启用。
            save_settings({'daily_schedule': {**settings, 'enabled': False}})
            try:
                daily_schedule.configure(settings['enabled'], clock)
            except Exception:
                save_settings({'daily_schedule': cfg.get('daily_schedule', {'enabled': False})})
                raise
            save_settings({'daily_schedule': settings})
            self.log('定时设置已保存；不会立即提交预约。')
            self.refresh_schedule()
        except (ValueError, SystemExit) as error:
            self.log(str(error))
        except Exception as error:
            self.log('定时设置失败：' + type(error).__name__)
            self.refresh_schedule()

    def log(self, text):
        self.output.configure(state='normal')
        self.output.insert('end', text + '\n')
        self.output.see('end')
        self.output.configure(state='disabled')

    def poll(self):
        while not self.events.empty():
            item = self.events.get()
            if item is None:
                self.busy = False
                for b in self.buttons:
                    b.configure(state='normal')
            else:
                self.log(item)
        self.root.after(100, self.poll)

    def run(self, action):
        if self.busy:
            return
        values = (self.seats.get(), self.date.get(), self.start.get(), self.end.get(), self.auto_navigation.get())
        self.busy = True
        for b in self.buttons:
            b.configure(state='disabled')
        threading.Thread(target=self.work, args=(action, values), daemon=True).start()

    def work(self, action, values):
        client = None
        booking_mutex = None
        self.submission_started = False
        emit = self.events.put
        try:
            if action in ('book', 'startup'):
                import win32event, win32api
                booking_mutex = win32event.CreateMutex(None, False, 'Local\\ZZLibrary-Booking')
                if win32event.WaitForSingleObject(booking_mutex, 0) not in (0, 128):
                    win32api.CloseHandle(booking_mutex)
                    booking_mutex = None
                    raise RuntimeError('另一个预约任务正在执行，本次跳过，避免重复提交。')
            cfg = load_config(str(CONFIG))
            if len(values) > 4:
                cfg['wechat_navigation'] = {**cfg.get('wechat_navigation', {}), 'enabled': values[4]}
            if len(values) > 5:
                cfg['candidates'] = values[5]
            names = list(dict.fromkeys(x.strip() for x in values[0].replace('，', ',').split(',') if x.strip()))
            start, end = parse_time(cfg, values[2] + ',' + values[3])
            parse_date(cfg, values[1])
            if action == 'book' and not names:
                raise RuntimeError('请填写至少一个候选座位。')
            if action == 'login':
                client, rows = capture_login(cfg, emit)
            else:
                emit('正在检查登录状态…')
                try:
                    client, rows = validated_client(cfg)
                except SeatError as e:
                    if e.code != -401 and not any(x in e.msg for x in ('登录', '授权', '会话')):
                        raise
                    client, rows = capture_login(cfg, emit)
            if action == 'startup':
                from startup_booking import recover
                schedule = cfg.get('daily_schedule', {})
                def submit_startup(seat, day, begin, finish):
                    nonlocal client
                    self.submission_started = True
                    client = submit_with_recovery(client, cfg, seat, day, begin, finish, emit,
                        capture_login, lambda row: same_booking(row, [seat.seat_name], day, begin, finish), save_settings)
                    save_cookie(client)
                recover(cfg, client, rows, emit, submit_startup, ROOT / 'last_startup_booking.json')
                return
            if action != 'book':
                emit('当前没有预约。' if not rows else '\n'.join(
                    f"{r.get('SeatName')}  {str(r.get('BookDate'))[:10]}  "
                    f"{str(r.get('StartTime'))[11:16]}–{str(r.get('EndTime'))[11:16]}" for r in rows))
                return
            cfg['_book_ahead_days'] = int(client.seat_config().get('BookAheadDays', 2))
            day = parse_date(cfg, values[1])
            if any(same_booking(r, names, day, start, end) for r in rows):
                emit('已有相同座位和时段的预约，无需重复提交。')
                return
            for name in names:
                saved = next((s for s in cfg.get('candidates', []) if s['seat_name'] == name), {})
                seat = Seat(name, saved.get('area_code', ''), saved.get('address_code', ''), saved.get('seatno', '')).resolve()
                emit(f'正在预约 {name}，{day}，{values[2]}–{values[3]}…')
                try:
                    before_submit = getattr(self, 'before_submit', None)
                    if before_submit:
                        before_submit()
                    self.submission_started = True
                    client = submit_with_recovery(
                        client, cfg, seat, day, start, end, emit, capture_login,
                        lambda row: same_booking(row, [name], day, start, end), save_settings)
                except SeatError as e:
                    if e.code == -401 or any(x in e.msg for x in ('登录', '授权', '会话')):
                        raise
                    emit(f'{name}：{e.msg}')
                    continue
                emit('服务器已返回预约成功。')
                try:
                    save_cookie(client)
                except OSError:
                    emit('预约成功，但 Cookie 保存失败；下次可能需要更新登录。')
                try:
                    client.current()
                    emit('当前预约查询通过。')
                except (SeatError, requests.RequestException):
                    emit('预约已提交成功，但后续查询失败，请在微信核实；不要重复提交。')
                return
            emit('候选座位均未预约成功，请根据上方服务器提示调整。')
        except SystemExit as e:
            emit(str(e))
        except SeatError as e:
            emit('操作失败：' + e.msg)
        except requests.RequestException:
            emit('网络请求失败，请检查代理；若发生在提交阶段，请先查询预约再重试。')
        except Exception as e:
            emit(str(e) if isinstance(e, RuntimeError) else '操作失败：' + type(e).__name__)
        finally:
            if client:
                client.session.close()
            if booking_mutex is not None:
                win32event.ReleaseMutex(booking_mutex)
                win32api.CloseHandle(booking_mutex)
            self.events.put(None)


if __name__ == '__main__':
    os.chdir(ROOT)
    window = tk.Tk()
    app = App(window)
    if '--login' in sys.argv:
        window.after(500, lambda: app.run('login'))
    window.mainloop()
