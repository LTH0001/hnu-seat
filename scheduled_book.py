"""定时任务入口：在执行当天计算明天，只预约一次并输出结果。"""
import os
from pathlib import Path
import sys
import json
import datetime as dt
from book import parse_time
from libseat import load_config
from quick_book import App, ROOT
from daily_schedule import parse_clock


def due_booking(schedule, now, previous=None):
    """取最近一次已到点的任务，目标为该触发日的明天。"""
    hour, minute = map(int, parse_clock(schedule.get('clock', '22:00')).split(':'))
    fire = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if fire > now:
        fire -= dt.timedelta(days=1)
    since = schedule.get('enabled_since')
    if since and fire < dt.datetime.fromisoformat(since):
        return None
    slot = fire.isoformat(timespec='seconds')
    if previous and previous.get('slot') == slot and not previous.get('retryable', False):
        return None  # 成功或提交结果不明时，不盲目重复提交。
    target = (fire.date() + dt.timedelta(days=1)).isoformat()
    return slot, target


class Output:
    def __init__(self):
        self.messages = []

    def put(self, message):
        if message is not None:
            self.messages.append(message)
            if sys.stdout is not None:
                print(message, flush=True)


def main():
    os.chdir(ROOT)
    def audit(message):
        with (ROOT / 'scheduled_history.log').open('a', encoding='utf-8') as stream:
            stream.write(dt.datetime.now().isoformat(timespec='seconds') + ' ' + message + '\n')
    audit('开始检查定时预约。')
    cfg = load_config(str(ROOT / 'config.json'))
    audit('配置路径 ' + str(ROOT / 'config.json') + '；enabled=' + str(cfg.get('daily_schedule', {}).get('enabled')))
    slot = None
    day = 'tomorrow'
    if '--scheduled' in sys.argv:
        schedule = cfg.get('daily_schedule', {})
        if not schedule.get('enabled'):
            audit('配置中未启用定时预约。')
            return 0
        now = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).replace(tzinfo=None)
        hour, minute = map(int, parse_clock(schedule.get('clock', '22:00')).split(':'))
        fire = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if 0 < (fire - now).total_seconds() <= 300:
            # 提前预热授权；不向尚未开放的预约接口提交。
            from quick_book import validated_client, capture_login
            output = Output()
            client = None
            try:
                try:
                    client, _ = validated_client(cfg)
                    output.put('预约前登录检查通过。')
                except Exception:
                    client, _ = capture_login(cfg, output.put)
            except Exception as error:
                output.put(str(error))
            finally:
                if client:
                    client.session.close()
                (ROOT / 'last_preparation_result.json').write_text(json.dumps({'messages': output.messages, 'at': now.isoformat()}, ensure_ascii=False, indent=2), encoding='utf-8')
            return 0
        previous = None
        result_path = ROOT / 'last_scheduled_result.json'
        if result_path.exists():
            previous = json.loads(result_path.read_text(encoding='utf-8'))
        now = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).replace(tzinfo=None)
        due = due_booking(schedule, now, previous)
        if due is None:
            audit('本时段无需重复执行。')
            return 0
        slot, day = due
        cfg = {**cfg, 'candidates': schedule['candidates'], 'datetime': schedule['datetime'],
               'wechat_navigation': {**cfg.get('wechat_navigation', {}), 'enabled': schedule.get('auto_navigation', True)}}
    start, end = parse_time(cfg, None)
    names = ','.join(dict.fromkeys(x['seat_name'] for x in cfg.get('candidates', [])))
    app = App.__new__(App)
    app.events = Output()
    if slot:
        app.events.put(f'执行计划 {slot}，预约日期 {day}（补跑保持原日期、座位和时段）。')
        target_start, _ = parse_time(cfg, None)
        begin = dt.datetime.fromisoformat(day) + dt.timedelta(minutes=target_start)
        if now >= begin:
            app.events.put('原预约开始时间已过，无法保留完整时段；本次跳过，不改约其他日期。')
            report = {'finished_at': now.isoformat(timespec='seconds'), 'slot': slot,
                      'exit_code': 1, 'messages': app.events.messages}
            result_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            return 1
    # App.work 会读取凭据，定时目标通过覆盖参数传入。
    if slot:
        def mark_submission():
            # 即使提交途中进程被终止，后续任务也不能盲目重发。
            marker = {'slot': slot, 'exit_code': 1, 'retryable': False,
                      'status': 'submitting', 'messages': ['预约请求即将发送；如进程中断，请先核实预约结果。']}
            result_path.write_text(json.dumps(marker, ensure_ascii=False, indent=2), encoding='utf-8')
        app.before_submit = mark_submission
    app.work('book', (names, day, f'{start//60:02d}:{start%60:02d}',
                     f'{end//60:02d}:{end%60:02d}',
                     cfg.get('wechat_navigation', {}).get('enabled', False), cfg['candidates']))
    success = ('服务器已返回预约成功。', '已有相同座位和时段的预约，无需重复提交。',
               '重新登录后查到目标预约，停止重复提交。')
    code = 0 if any(x in app.events.messages for x in success) else 1
    report = {'finished_at': dt.datetime.now().isoformat(timespec='seconds'),
              'exit_code': code, 'messages': app.events.messages, 'slot': slot,
              'retryable': code != 0 and not getattr(app, 'submission_started', any(x.startswith('正在预约 ') for x in app.events.messages))}
    (ROOT / 'last_scheduled_result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    audit(f'执行结束，退出码 {code}，允许补跑 {report["retryable"]}。')
    for message in app.events.messages:
        audit(message)
    return code


if __name__ == '__main__':
    if sys.stdout is not None:
        sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
