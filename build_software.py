"""构建不包含登录凭据的免安装软件。"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def main():
    os.chdir(ROOT)
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--output-dir', default=str(ROOT / 'software'))
    parser.add_argument('--include-node', action='store_true', help='可选：嵌入本机 Node.js，发布时需附带其许可证')
    options = parser.parse_args()
    output_dir = options.output_dir
    cfg = json.loads((ROOT / 'config.example.json').read_text(encoding='utf-8'))
    (ROOT / 'app_defaults.json').write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8')
    args = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--windowed',
            '--name', '图书馆预约', '--distpath', output_dir, '--workpath', str(ROOT / '.software-build'),
            '--specpath', str(ROOT / '.software-build'), '--add-data', f'{ROOT / "app_defaults.json"};.',
            '--hidden-import', 'comtypes.stream', '--hidden-import', 'win32timezone',
            '--collect-submodules', 'pywinauto', '--collect-submodules', 'comtypes',
            '--exclude-module', 'pytest', '--exclude-module', 'matplotlib', '--exclude-module', 'scipy']
    for name in ['enter.png', 'search.png', 'search_icon.png', 'account.png', 'service.png', 'booking.png']:
        args += ['--add-data', f'{ROOT / "wechat_assets" / name};wechat_assets']
    node = shutil.which('node')
    if node and options.include_node:
        args += ['--add-binary', f'{node};node_runtime']
    args.append(str(ROOT / 'app_entry.py'))
    return subprocess.call(args)


if __name__ == '__main__':
    raise SystemExit(main())
