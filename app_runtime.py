"""源码运行与打包软件共用的资源、数据和子进程路径。"""
import json
import os
from pathlib import Path
import sys

FROZEN = bool(getattr(sys, 'frozen', False))
SOURCE_ROOT = Path(__file__).resolve().parent
RESOURCE_ROOT = Path(getattr(sys, '_MEIPASS', SOURCE_ROOT))
DATA_ROOT = (Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) / 'ZZLibrary') if FROZEN else SOURCE_ROOT
if '--data-dir' in sys.argv:
    index = sys.argv.index('--data-dir')
    DATA_ROOT = Path(sys.argv[index + 1]).resolve()
    del sys.argv[index:index + 2]


def initialize():
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    config = DATA_ROOT / 'config.json'
    if not config.exists():
        template = RESOURCE_ROOT / ('app_defaults.json' if FROZEN else 'config.example.json')
        defaults = json.loads(template.read_text(encoding='utf-8'))
        config.write_text(json.dumps(defaults, ensure_ascii=False, indent=2), encoding='utf-8')
    node = RESOURCE_ROOT / 'node_runtime'
    if node.exists():
        os.environ['PATH'] = str(node) + os.pathsep + os.environ.get('PATH', '')
    os.chdir(DATA_ROOT)


def launch_command(mode, *args):
    if FROZEN:
        return [sys.executable, mode, *args]
    return [sys.executable, str(SOURCE_ROOT / 'app_entry.py'), mode, *args]


def scheduled_command():
    if FROZEN:
        return str(Path(sys.executable)), f'--scheduled --data-dir "{DATA_ROOT}"'
    pythonw = Path(sys.executable).with_name('pythonw.exe')
    return str(pythonw if pythonw.exists() else Path(sys.executable)), f'"{SOURCE_ROOT / "app_entry.py"}" --scheduled --data-dir "{DATA_ROOT}"'
