import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from environment_setup import find_wechat


class EnvironmentTests(unittest.TestCase):
    def test_valid_configured_path_wins(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'Weixin.exe'
            path.touch()
            self.assertEqual(find_wechat(str(path)), str(path))

    def test_running_wechat_can_be_detected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'Weixin.exe'
            path.touch()
            with patch('psutil.process_iter', return_value=[SimpleNamespace(info={'name': 'Weixin.exe', 'exe': str(path)})]):
                self.assertEqual(find_wechat(), str(path))

    def test_missing_wechat_has_actionable_message(self):
        with patch('psutil.process_iter', return_value=[]), patch('environment_setup.Path.is_file', return_value=False):
            with self.assertRaisesRegex(RuntimeError, '微信程序'):
                find_wechat()
