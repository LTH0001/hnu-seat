import unittest
from unittest.mock import Mock, patch
from pathlib import Path
from PIL import Image
from wechat_navigation import ASSETS, match_anchor, needs_login_confirmation, Navigator


class NavigationTests(unittest.TestCase):
    def login_navigation(self):
        nav = Navigator.__new__(Navigator)
        nav.emit = Mock()
        nav.focus = Mock()
        nav.logged_in = Mock(return_value=False)
        login = Mock()
        login.rectangle.return_value.width.return_value = 283
        login.rectangle.return_value.height.return_value = 383
        main = Mock()
        main.rectangle.return_value.width.return_value = 1047
        main.rectangle.return_value.height.return_value = 677
        main.descendants.return_value = []
        return nav, login, main

    def test_delayed_enter_button_is_waited_for(self):
        nav, login, main = self.login_navigation()
        button = Mock()
        button.window_text.return_value = ' 进入微信 '
        attempts = iter([[], [button]])
        login.descendants.side_effect = lambda **kw: next(attempts) if kw['control_type'] == 'Button' else []
        nav.wait_window = Mock(side_effect=[login, login, main])
        nav.click_anchor = Mock(side_effect=RuntimeError('未识别到进入微信按钮'))
        with patch('wechat_navigation.time.sleep'):
            self.assertIs(nav.enter_remembered_account(login), main)
        button.click_input.assert_called_once()

    def test_enter_image_fallback_when_button_text_is_missing(self):
        nav, login, main = self.login_navigation()
        login.descendants.return_value = []
        nav.wait_window = Mock(side_effect=[login, main])
        nav.click_anchor = Mock()
        with patch('wechat_navigation.time.sleep'):
            self.assertIs(nav.enter_remembered_account(login), main)
        nav.click_anchor.assert_called_once_with(login, 'enter.png', seconds=1)

    def test_phone_confirmation_stops_without_click(self):
        nav, login, _ = self.login_navigation()
        text = Mock()
        text.window_text.return_value = '请在手机确认登录'
        login.descendants.return_value = [text]
        nav.wait_window = Mock(return_value=login)
        nav.click_anchor = Mock()
        with self.assertRaisesRegex(RuntimeError, '手机确认'):
            nav.enter_remembered_account(login)
        nav.click_anchor.assert_not_called()

    def test_logged_in_account_never_clicks_enter(self):
        nav, _, main = self.login_navigation()
        nav.logged_in.return_value = True
        nav.click_anchor = Mock()
        self.assertIs(nav.enter_remembered_account(main), main)
        nav.click_anchor.assert_not_called()

    def test_new_wechat_main_window_without_pane_is_logged_in(self):
        nav = Navigator.__new__(Navigator)
        window = Mock()
        window.rectangle.return_value.width.return_value = 1047
        window.rectangle.return_value.height.return_value = 677
        search = Mock()
        search.window_text.return_value = '搜索'
        window.descendants.side_effect = lambda **kw: [search] if kw['control_type'] == 'Edit' else []
        self.assertTrue(nav.logged_in(window))

    def test_search_clears_old_query_before_typing(self):
        nav = Navigator.__new__(Navigator)
        main = Mock()
        nav.native = Mock(return_value=main)
        nav.focus = Mock()
        nav.click_anchor = Mock()
        clipboard = Mock()
        clipboard.IsClipboardFormatAvailable.return_value = False
        with patch('wechat_navigation.time.sleep'), patch.dict('sys.modules', {'win32clipboard': clipboard}):
            nav.search_account(main)
        self.assertEqual([call.args[0] for call in main.type_keys.call_args_list],
                         ['^a{BACKSPACE}', '^v'])
        self.assertEqual([call.args[1] for call in nav.click_anchor.call_args_list],
                         ['search_icon.png', 'account.png'])
    def test_logged_in_window_reused_without_launch_or_login(self):
        nav = Navigator.__new__(Navigator)
        nav.emit = Mock()
        nav.logged_in = Mock(return_value=True)
        nav.interface_ready = Mock(return_value=True)
        window = Mock()
        window.rectangle.return_value.width.return_value = 1047
        window.rectangle.return_value.height.return_value = 677
        window.descendants.return_value = []
        window.is_minimized.return_value = False
        nav.unique = Mock(return_value=window)
        with patch('wechat_navigation.subprocess.Popen') as launch:
            self.assertIs(nav.ensure_main(), window)
            launch.assert_not_called()
        self.assertIn('已登录', nav.emit.call_args.args[0])

    def test_running_without_window_does_not_start_second_instance(self):
        nav = Navigator.__new__(Navigator)
        nav.emit = Mock()
        nav.unique = Mock(return_value=None)
        nav.running = Mock(return_value=True)
        nav.restore_from_tray = Mock(side_effect=RuntimeError('不会重复启动'))
        with patch('wechat_navigation.subprocess.Popen') as launch:
            with self.assertRaisesRegex(RuntimeError, '不会重复启动'):
                nav.ensure_main()
            launch.assert_not_called()

    def test_minimized_logged_in_window_is_restored(self):
        nav = Navigator.__new__(Navigator)
        nav.emit = Mock()
        nav.logged_in = Mock(return_value=True)
        nav.interface_ready = Mock(return_value=True)
        window = Mock()
        window.is_minimized.return_value = True
        window.rectangle.return_value.width.return_value = 1047
        nav.native = Mock(return_value=window)
        nav.unique = Mock(return_value=window)
        nav.wait_window = Mock(return_value=window)
        nav.restore_from_tray = Mock()
        nav.focus = Mock()
        nav.ensure_main()
        nav.focus.assert_called_once_with(window)
        nav.restore_from_tray.assert_not_called()

    def test_each_real_anchor_recognized_at_arbitrary_location(self):
        for name in ['enter.png', 'search.png', 'search_icon.png', 'account.png', 'service.png', 'booking.png']:
            with self.subTest(name=name), Image.open(ASSETS / name) as template:
                canvas = Image.new('RGB', (400, 250), '#cccccc')
                canvas.paste(template, (77, 91))
                self.assertEqual(match_anchor(canvas, template),
                                 (77 + template.width // 2, 91 + template.height // 2))

    def test_duplicate_anchor_refuses_click(self):
        with Image.open(ASSETS / 'booking.png') as template:
            canvas = Image.new('RGB', (400, 250), '#cccccc')
            canvas.paste(template, (20, 20))
            canvas.paste(template, (220, 160))
            self.assertIsNone(match_anchor(canvas, template))

    def test_no_anchor_refuses_click(self):
        with Image.open(ASSETS / 'search.png') as template:
            self.assertIsNone(match_anchor(Image.new('RGB', (400, 250), 'white'), template))

    def test_main_chat_text_is_not_login_confirmation(self):
        self.assertFalse(needs_login_confirmation('请在手机扫描二维码', 1047, 677))
        self.assertTrue(needs_login_confirmation('请在手机确认登录', 280, 380))
        self.assertFalse(needs_login_confirmation('正在进入', 280, 380))

    def test_scaled_anchor_is_found(self):
        import cv2
        import numpy as np
        with Image.open(ASSETS / 'search.png') as template:
            scaled = Image.fromarray(cv2.resize(np.asarray(template.convert('RGB')), None,
                                               fx=1.5, fy=1.5, interpolation=cv2.INTER_CUBIC))
            canvas = Image.new('RGB', (500, 300), '#cccccc')
            canvas.paste(scaled, (80, 100))
            point = match_anchor(canvas, template)
            self.assertIsNotNone(point)
            self.assertLessEqual(abs(point[0] - (80 + scaled.width//2)), 1)
            self.assertLessEqual(abs(point[1] - (100 + scaled.height//2)), 1)


if __name__ == '__main__':
    unittest.main()
