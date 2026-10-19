"""独立微信导航脚本，只打开预约页面，不提交预约或操作身份验证。"""
from __future__ import annotations

import argparse
import ctypes
from pathlib import Path
import subprocess
import time
from app_runtime import DATA_ROOT, RESOURCE_ROOT

ROOT = DATA_ROOT
ASSETS = RESOURCE_ROOT / 'wechat_assets'


def needs_login_confirmation(texts, width, height):
    return width < 700 and height < 950 and any(
        text in texts for text in ('扫码登录', '请在手机', '手机上确认', '扫描二维码'))


def match_anchor(image, template, threshold=0.94):
    """返回唯一高置信度锚点；缩放或歧义时拒绝点击。"""
    import cv2
    import numpy as np
    source = cv2.cvtColor(np.asarray(image.convert('RGB')), cv2.COLOR_RGB2GRAY)
    target = cv2.cvtColor(np.asarray(template.convert('RGB')), cv2.COLOR_RGB2GRAY)
    hits = []
    for scale in (1.0, 0.5, 2/3, 0.75, 0.8, 1.25, 4/3, 1.5, 5/3, 1.75, 2.0):
        scaled = cv2.resize(target, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        h, w = scaled.shape
        if h > source.shape[0] or w > source.shape[1]:
            continue
        scores = cv2.matchTemplate(source, scaled, cv2.TM_CCOEFF_NORMED)
        _, score, _, point = cv2.minMaxLoc(scores)
        if score < threshold:
            continue
        x, y = point
        scores[max(0, y-h):y+h+1, max(0, x-w):x+w+1] = -1
        if cv2.minMaxLoc(scores)[1] >= threshold:
            return None
        hits.append((score, x+w//2, y+h//2, w, h))
    if not hits:
        return None
    hits.sort(reverse=True)
    _, x, y, w, h = hits[0]
    if any(abs(x-other[1]) > w or abs(y-other[2]) > h for other in hits[1:]):
        return None
    return x, y


class Navigator:
    @staticmethod
    def native(window):
        from pywinauto.controls.hwndwrapper import HwndWrapper
        return HwndWrapper(window.handle)

    @staticmethod
    def focus(window):
        import win32gui, win32con, win32api, win32process
        if win32gui.GetForegroundWindow() == window.handle:
            return
        if win32gui.IsIconic(window.handle):
            win32gui.ShowWindow(window.handle, win32con.SW_RESTORE)
        foreground = win32gui.GetForegroundWindow()
        attached = False
        current = win32api.GetCurrentThreadId()
        other = win32process.GetWindowThreadProcessId(foreground)[0] if foreground else current
        try:
            if current != other:
                try:
                    win32process.AttachThreadInput(current, other, True)
                    attached = True
                except Exception:
                    pass
            win32gui.BringWindowToTop(window.handle)
            try:
                win32gui.SetForegroundWindow(window.handle)
            except Exception:
                if win32gui.GetForegroundWindow() != window.handle:
                    from pywinauto import mouse
                    left, top, right, bottom = win32gui.GetWindowRect(window.handle)
                    mouse.click(coords=(left + min(100, (right-left)//2), top + 12))
                    time.sleep(0.2)
                    if win32gui.GetForegroundWindow() != window.handle:
                        raise RuntimeError('Windows 未允许切换到微信，请保持桌面解锁并展开微信。') from None
        finally:
            if attached:
                win32process.AttachThreadInput(current, other, False)
    def __init__(self, executable, emit=print):
        # 截图、窗口矩形、鼠标坐标必须使用一致的物理像素。
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except (AttributeError, OSError):
            pass
        # 延迟导入，便于离线测试不连接桌面。
        from pywinauto import Desktop
        self.desktop = Desktop(backend='uia')
        self.executable = Path(executable)
        self.emit = emit

    def unique(self, title, process_name=None):
        import psutil
        candidates = []
        for window in self.desktop.windows(title=title, visible_only=False):
            try:
                name = psutil.Process(window.process_id()).name().lower()
                rect = window.rectangle()
                if (rect.width() < 100 or rect.height() < 100) and not window.is_minimized():
                    continue
                if process_name is None or name == process_name.lower():
                    candidates.append(window)
            except (psutil.Error, RuntimeError):
                continue
        if not candidates and process_name == 'Weixin.exe':
            # UI Automation 会漏掉隐藏到托盘的 Qt 窗口，用原生句柄找回同一窗口。
            import win32gui, win32process
            handles = []
            def collect(handle, _):
                try:
                    if win32gui.GetWindowText(handle) == title:
                        _, pid = win32process.GetWindowThreadProcessId(handle)
                        if psutil.Process(pid).name().lower() == process_name.lower():
                            handles.append(handle)
                except psutil.Error:
                    pass
            win32gui.EnumWindows(collect, None)
            candidates = [self.desktop.window(handle=h).wrapper_object() for h in handles]
        if len(candidates) > 1:
            if title == '微信' and process_name == 'Weixin.exe':
                logged = [w for w in candidates if self.logged_in(w)]
                if len(logged) == 1:
                    return logged[0]
            raise RuntimeError('同名窗口不唯一，请关闭多余微信窗口。')
        return candidates[0] if candidates else None

    def logged_in(self, window):
        rect = window.rectangle()
        # 微信主界面和小型登录窗口区别明确；排除登录按钮。
        has_main = bool(window.descendants(control_type='Pane')) or any(
            edit.window_text().strip() in ('搜索', 'Search') for edit in window.descendants(control_type='Edit'))
        return rect.width() >= 700 and rect.height() >= 450 and has_main and not any(
            b.window_text() == '进入微信' and b.is_visible()
            for b in window.descendants(control_type='Button'))

    def running(self):
        import psutil
        for process in psutil.process_iter(['name']):
            if (process.info.get('name') or '').lower() == 'weixin.exe':
                return True
        return False

    def interface_ready(self, main):
        from PIL import Image, ImageGrab
        import win32gui
        self.focus(main)
        time.sleep(0.3)
        left, top, right, bottom = win32gui.GetWindowRect(main.handle)
        if right <= left or bottom <= top:
            return False
        image = ImageGrab.grab(bbox=(left, top, min(right, left+500), min(bottom, top+140)), all_screens=True)
        with Image.open(ASSETS / 'search_icon.png') as template:
            return match_anchor(image, template) is not None

    def restore_from_tray(self):
        """通过微信自己的托盘事件恢复 Qt 会话，不直接显示隐藏的原生窗口。"""
        bars = self.desktop.windows(class_name='Shell_TrayWnd')
        for bar in bars:
            buttons = bar.descendants(control_type='Button')
            direct = [b for b in buttons if b.window_text().strip() in ('微信', 'WeChat', 'Weixin')]
            if len(direct) == 1:
                direct[0].double_click_input()
                time.sleep(0.7)
                return
            expand = [b for b in buttons if b.window_text() in ('显示隐藏的图标', 'Show hidden icons')]
            if len(expand) != 1:
                continue
            expand[0].click_input()
            time.sleep(0.5)
            for cls in ('TopLevelWindowForOverflowXamlIsland', 'NotifyIconOverflowWindow'):
                for popup in self.desktop.windows(class_name=cls):
                    icons = [b for b in popup.descendants(control_type='Button')
                             if b.window_text().strip().lower().startswith(('微信', 'wechat', 'weixin'))]
                    if len(icons) == 1:
                        icons[0].double_click_input()
                        time.sleep(0.7)
                        return
        raise RuntimeError('未找到微信托盘图标，请从右下角托盘双击微信；不会再次启动或登录。')

    def ensure_main(self):
        main = self.unique('微信', 'Weixin.exe')
        if main is None:
            if self.running():
                self.emit('微信正在后台运行，正在通过托盘恢复现有会话…')
                self.restore_from_tray()
                main = self.wait_window('微信', 'Weixin.exe')
            else:
                if not self.executable.is_file():
                    raise RuntimeError('微信程序路径不存在，请修改 wechat_navigation 配置。')
                self.emit('微信未运行，正在启动…')
                subprocess.Popen([str(self.executable)])
                main = self.wait_window('微信', 'Weixin.exe')
        if main.is_minimized():
            # 最小化到任务栏与隐藏到托盘不同，无需寻找托盘图标。
            self.focus(main)
            main = self.wait_window('微信', 'Weixin.exe')
            time.sleep(0.5)
        if not main.is_visible():
            self.restore_from_tray()
            main = self.wait_window('微信', 'Weixin.exe')
            time.sleep(0.5)
        if main.rectangle().width() >= 700 and not self.interface_ready(main):
            self.emit('微信窗口未正常显示，正在通过托盘恢复界面…')
            self.restore_from_tray()
            main = self.wait_window('微信', 'Weixin.exe')
            time.sleep(0.5)
        if self.logged_in(main):
            self.emit('检测到微信已登录，复用当前窗口，跳过启动和登录。')
        else:
            self.emit('检测到微信登录界面，检查是否可直接进入已记住账号。')
        return main

    def wait_window(self, title, process_name, seconds=12):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            window = self.unique(title, process_name)
            if window:
                return window
            time.sleep(0.4)
        raise RuntimeError('未找到目标窗口；如微信要求扫码或手机确认，请手动完成登录后重试。')

    def click_anchor(self, window, filename, seconds=8):
        from PIL import Image, ImageGrab
        import win32gui
        with Image.open(ASSETS / filename) as template:
            template = template.copy()
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            native = self.native(window)
            if native.is_minimized() or not native.is_visible():
                native.restore()
            self.focus(window)
            time.sleep(0.25)  # 等待桌面完成前台窗口切换后再截屏。
            if not win32gui.IsWindowVisible(window.handle) or win32gui.IsIconic(window.handle):
                raise RuntimeError('微信窗口已隐藏，停止输入；请展开微信后重试。')
            # PIL 处理虚拟桌面原点，避免多显示器时截到另一屏的壁纸。
            picture = ImageGrab.grab(bbox=win32gui.GetWindowRect(window.handle), all_screens=True)
            point = match_anchor(picture, template)
            if point:
                if filename == 'search_icon.png':
                    point = (point[0] + 55, point[1])  # 点输入区域，图标自身不一定接受焦点。
                # 坐标仅由当前窗口画面中的唯一匹配生成。
                left, top, _, _ = win32gui.GetWindowRect(window.handle)
                native.click_input(coords=(left + point[0], top + point[1]), absolute=True)
                return
            time.sleep(0.4)
        labels = {'enter.png': '进入微信按钮', 'search.png': '微信搜索框', 'search_icon.png': '微信搜索框', 'account.png': '公众号搜索结果',
                  'service.png': '微服务大厅菜单', 'booking.png': '座位预约菜单'}
        if filename in ('search.png', 'search_icon.png'):
            diagnostics = ROOT / 'navigation_diagnostics'
            diagnostics.mkdir(exist_ok=True)
            picture.crop((0, 0, min(picture.width, 500), min(picture.height, 140))).save(diagnostics / 'search_diagnostic.png')
        raise RuntimeError(f"未识别到{labels.get(filename, filename)}，已保存局部诊断画面；请保留微信主窗口，以便检查。")

    def search_account(self, main):
        native = self.native(main)
        self.focus(main)
        # 只匹配稳定的放大镜，不依赖占位文字、光标或旧搜索内容。
        self.click_anchor(main, 'search_icon.png')
        native.type_keys('^a{BACKSPACE}', set_foreground=False)
        # 微信新版不稳定支持 VK_PACKET 中文输入，用 Unicode 剪贴板粘贴。
        import win32clipboard
        win32clipboard.OpenClipboard()
        old_text = None
        try:
            if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
                old_text = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText('湖南大学图书馆', win32clipboard.CF_UNICODETEXT)
        finally:
            win32clipboard.CloseClipboard()
        try:
            native.type_keys('^v', set_foreground=False)
            self.click_anchor(main, 'account.png')
        finally:
            if old_text is not None:
                win32clipboard.OpenClipboard()
                try:
                    win32clipboard.EmptyClipboard()
                    win32clipboard.SetClipboardText(old_text, win32clipboard.CF_UNICODETEXT)
                finally:
                    win32clipboard.CloseClipboard()

    def enter_remembered_account(self, main):
        if self.logged_in(main):
            return main
        deadline = time.monotonic() + 12
        clicked = False
        while time.monotonic() < deadline:
            main = self.wait_window('微信', 'Weixin.exe', seconds=3)
            if self.logged_in(main):
                return main
            rect = main.rectangle()
            texts = ' '.join(x.window_text() for x in main.descendants(control_type='Text'))
            if needs_login_confirmation(texts, rect.width(), rect.height()):
                raise RuntimeError('微信需要扫码或手机确认，请手动完成后重试。')
            enter = [b for b in main.descendants(control_type='Button')
                     if b.window_text().strip() == '进入微信' and b.is_visible() and b.is_enabled()]
            if len(enter) > 1:
                raise RuntimeError('进入微信按钮不唯一，停止点击。')
            if len(enter) == 1:
                self.emit('正在进入微信已记住的账号…')
                self.focus(main)
                enter[0].click_input()
                clicked = True
                break
            # 部分微信版本加载迟缓或未暴露按钮文字，仅在登录窗口内识别按钮图像。
            if rect.width() < 700 and rect.height() < 950:
                try:
                    self.click_anchor(main, 'enter.png', seconds=1)
                except RuntimeError as error:
                    if '未识别到进入微信按钮' not in str(error):
                        raise
                else:
                    self.emit('已识别并点击进入微信按钮。')
                    clicked = True
                    break
            time.sleep(0.4)
        if not clicked:
            raise RuntimeError('等待进入微信按钮超时，未继续搜索公众号；请检查微信登录窗口。')
        if clicked:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                try:
                    main = self.wait_window('微信', 'Weixin.exe', seconds=3)
                    texts = ' '.join(x.window_text() for x in main.descendants(control_type='Text'))
                    rect = main.rectangle()
                except Exception:
                    # 登录窗口销毁后重新获取主窗口，避免使用过期的 UIA 对象。
                    time.sleep(0.5)
                    continue
                login_sized = rect.width() < 700 and rect.height() < 950
                if needs_login_confirmation(texts, rect.width(), rect.height()):
                    raise RuntimeError('微信需要扫码或手机确认，请手动完成后重试。')
                if not login_sized and '正在进入' not in texts:
                    break
                time.sleep(0.5)
            else:
                raise RuntimeError('进入微信超时，请检查手机确认提示。')
        return main

    def open_booking(self):
        main = self.enter_remembered_account(self.ensure_main())
        account = self.unique('湖南大学图书馆', 'Weixin.exe')
        if account is None:
            self.emit('正在搜索已关注的湖南大学图书馆公众号…')
            self.search_account(main)
            account = self.wait_window('湖南大学图书馆', 'Weixin.exe')
        self.emit('正在打开微服务大厅 → 座位预约…')
        self.focus(account)
        self.click_anchor(account, 'service.png')
        self.click_anchor(account, 'booking.png')
        browser = self.wait_window('微信', 'WeChatAppEx.exe', seconds=18)
        browser.set_focus()
        deadline = time.monotonic() + 18
        while time.monotonic() < deadline:
            documents = browser.descendants(control_type='Document')
            owned = [d for d in documents if d.window_text() == '湖南大学图书馆']
            for document in owned:
                # 限定为图书馆首页，避免操作聊天中的同名文字。
                labels = document.descendants(control_type='Text')
                if not any(t.window_text() == '座位服务' for t in labels):
                    continue
                buttons = [t for t in labels if t.window_text() == '座位预约' and t.is_visible()]
                if len(buttons) == 1:
                    buttons[0].click_input()
                    # 菜单页可能完全来自缓存；进入按图预约，触发实际查询请求。
                    sub_deadline = time.monotonic() + 10
                    while time.monotonic() < sub_deadline:
                        links = [link for link in browser.descendants(control_type='Hyperlink')
                                 if link.window_text() == '按图预约' and link.is_visible()]
                        if len(links) == 1:
                            links[0].click_input()
                            self.emit('已进入按图预约并触发座位查询，等待 Cookie 校验。')
                            return
                        time.sleep(0.3)
                    raise RuntimeError('座位预约菜单未加载，请检查授权状态。')
            time.sleep(0.5)
        raise RuntimeError('预约首页没有加载或需要身份验证，请在微信完成后重试。')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--exe', default='')
    args = parser.parse_args()
    try:
        if not args.exe:
            from environment_setup import find_wechat
            args.exe = find_wechat()
        # 桌面锁定时不尝试输入。
        user32 = ctypes.windll.user32
        user32.OpenInputDesktop.restype = ctypes.c_void_p
        user32.SwitchDesktop.argtypes = [ctypes.c_void_p]
        user32.CloseDesktop.argtypes = [ctypes.c_void_p]
        handle = user32.OpenInputDesktop(0, False, 0x0100)
        if not handle:
            raise RuntimeError('桌面锁定或不可访问，请先解锁。')
        try:
            if not user32.SwitchDesktop(handle):
                raise RuntimeError('桌面锁定，请先解锁。')
        finally:
            user32.CloseDesktop(handle)
        Navigator(args.exe).open_booking()
        return 0
    except Exception as error:
        import traceback
        (ROOT / 'navigation_error.log').write_text(traceback.format_exc(), encoding='utf-8')
        print(str(error) if isinstance(error, RuntimeError) else
              '微信导航失败：' + type(error).__name__)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
