"""只从明确清单生成分享包，不复制工作目录或个人配置。"""
import argparse
import hashlib
import importlib.metadata as metadata
import json
from pathlib import Path
import shutil
import sys
import zipfile

ROOT = Path(__file__).resolve().parent
ASSETS = ['enter.png', 'search.png', 'search_icon.png', 'account.png', 'service.png', 'booking.png']
SOURCE_FILES = '''app_entry.py app_runtime.py book.py booking_recovery.py daily_schedule.py
environment_setup.py extract_cookie.py fetch_key.py libseat.py quick_book.py renew_cookie.py
scheduled_book.py signer_node.py startup_booking.py wechat_navigation.py build_software.py
package_release.py config.example.json requirements.txt requirements-build.txt requirements-tested.txt
README.md LICENSE.md THIRD_PARTY_NOTICES.md CHANGELOG.md CONTRIBUTING.md 一键预约.bat 微信导航测试.bat'''.split()


def clean_defaults(path):
    cfg = json.loads(path.read_text(encoding='utf-8-sig'))
    assert cfg.get('cookie') == '' and cfg.get('proxy') == ''
    assert cfg.get('candidates') == [] and not cfg.get('startup_check_enabled', True)
    assert not cfg.get('daily_schedule', {}).get('enabled', True)
    assert cfg.get('daily_schedule', {}).get('candidates') == []
    assert cfg.get('wechat_navigation', {}).get('exe') == ''
    assert not any(key.startswith('_') for key in cfg)


def collect_licenses(destination, software):
    destination.mkdir(parents=True)
    versions = {}
    for dist in metadata.distributions():
        name = dist.metadata['Name']
        versions[name] = dist.version
        for entry in dist.files or []:
            if not any(word in Path(str(entry)).name.lower() for word in ('license', 'copying', 'notice')):
                continue
            source = Path(dist.locate_file(entry)).resolve()
            if not source.is_file() or source.suffix.lower() not in ('', '.txt', '.md', '.rst', '.terms'):
                continue
            # 不保留安装机器的绝对路径。
            target = destination / name / (hashlib.sha256(str(entry).encode()).hexdigest()[:8] + '-' + source.name)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    python_license = Path(sys.base_prefix) / 'LICENSE_PYTHON.txt'
    if not python_license.is_file():
        python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    if not python_license.is_file():
        raise RuntimeError('缺少构建用 Python 的许可文件，停止发布。')
    shutil.copy2(python_license, destination / 'Python-LICENSE.txt')
    for source in software.rglob('*'):
        if source.is_file() and source.name.lower() in ('license.terms', 'license.txt', 'copying.txt'):
            target = destination / 'bundled' / source.relative_to(software)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    (destination / 'components.json').write_text(json.dumps(versions, ensure_ascii=False, indent=2), encoding='utf-8')


def audit(directory):
    private = ROOT / 'config.json'
    cookie = json.loads(private.read_text(encoding='utf-8')).get('cookie', '') if private.exists() else ''
    for file in directory.rglob('*'):
        if not file.is_file():
            continue
        if file.name == 'config.json' or file.suffix.lower() in ('.pcap', '.pcapng', '.har', '.saz', '.log'):
            raise RuntimeError('分享包混入运行数据：' + str(file.relative_to(directory)))
        if cookie and len(cookie) > 12 and cookie.encode() in file.read_bytes():
            raise RuntimeError('分享包发现本机 Cookie，停止发布。')


def archive(folder, destination):
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as output:
        for file in sorted(folder.rglob('*')):
            if file.is_file():
                output.write(file, Path(folder.name) / file.relative_to(folder))
    with zipfile.ZipFile(destination) as result:
        assert result.testzip() is None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--software-dir', default='software_release')
    parser.add_argument('--output-dir', default=str(ROOT.parent / 'releases'))
    options = parser.parse_args()
    output = Path(options.output_dir).resolve()
    source = output / 'ZZLibrary-source'
    binary = output / 'ZZLibrary-Windows-x64'
    if source.exists() or binary.exists():
        raise RuntimeError('输出目录已有发布文件，请使用新的 --output-dir，防止混入旧文件。')
    software = (ROOT / options.software_dir / '图书馆预约').resolve()
    if not (software / '图书馆预约.exe').is_file():
        raise RuntimeError('请先构建 Windows 软件。')
    if (software / '_internal/node_runtime').exists():
        raise RuntimeError('分享包默认不嵌入 Node；请不加 --include-node 重新构建。')
    clean_defaults(ROOT / 'config.example.json')
    clean_defaults(software / '_internal/app_defaults.json')
    source.mkdir(parents=True)
    for name in SOURCE_FILES + [file.name for file in ROOT.glob('test_*.py')]:
        shutil.copy2(ROOT / name, source / name)
    (source / 'wechat_assets').mkdir()
    for name in ASSETS:
        shutil.copy2(ROOT / 'wechat_assets' / name, source / 'wechat_assets' / name)
    (source / '.gitignore').write_text('config.json\napp_defaults.json\n*.log\n*.pcap*\n*.har\n*.saz\nlast_*.json\n*check.json\n__pycache__/\n.venv/\n.software-build/\nsoftware*/\nvendor/\nnavigation_diagnostics/\n', encoding='utf-8')
    shutil.copytree(software, binary)
    for name in ('README.md', 'LICENSE.md', 'THIRD_PARTY_NOTICES.md', 'CHANGELOG.md'):
        shutil.copy2(ROOT / name, binary / name)
    collect_licenses(binary / 'third_party_licenses', software)
    for folder in (source, binary):
        audit(folder)
        archive(folder, output / (folder.name + '.zip'))
    checksums = []
    for name in ('ZZLibrary-source.zip', 'ZZLibrary-Windows-x64.zip'):
        digest = hashlib.sha256((output / name).read_bytes()).hexdigest()
        checksums.append(digest + '  ' + name)
    (output / 'SHA256SUMS.txt').write_text('\n'.join(checksums) + '\n', encoding='utf-8')
    print('发布包生成完成；凭据、抓包及压缩包完整性检查通过。')
    print(output)


if __name__ == '__main__':
    main()
