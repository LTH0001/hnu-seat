"""新电脑的微信安装路径检测，不使用开发者的私人安装路径。"""
import os
from pathlib import Path


def find_wechat(configured=''):
    if configured and Path(configured).is_file():
        return str(Path(configured))
    import psutil
    for process in psutil.process_iter(['name', 'exe']):
        if (process.info.get('name') or '').lower() in ('weixin.exe', 'wechat.exe'):
            executable = process.info.get('exe')
            if executable and Path(executable).is_file():
                return executable
    for name in ('ProgramFiles', 'ProgramFiles(x86)', 'LOCALAPPDATA'):
        base = os.environ.get(name)
        if not base:
            continue
        for relative in ('Tencent/Weixin/Weixin.exe', 'Tencent/WeChat/WeChat.exe', 'Weixin/Weixin.exe'):
            path = Path(base) / relative
            if path.is_file():
                return str(path)
    raise RuntimeError('未找到微信。请先安装并打开微信，或点击“微信程序”选择 Weixin.exe。')
