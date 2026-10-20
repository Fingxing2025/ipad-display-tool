#!/usr/bin/python3
"""Install this application for the current user, without starting a stream."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parent
APP_ID = 'local.ipad.DisplaySettings'


def quote_exec(path):
    value = str(path).replace('%', '%%')
    for char in ('\\', '"', '`', '$'):
        value = value.replace(char, '\\'+char)
    return '"'+value+'"'


def desktop_entry(name, description, script, arguments=''):
    return (f'[Desktop Entry]\nType=Application\nName={name}\nComment={description}\n'
            f'Exec=/usr/bin/python3 {quote_exec(script)}{arguments}\n'
            f'Icon={APP_ID}\nTerminal=false\nCategories=Utility;\n'
            'Keywords=iPad;Moonlight;Sunshine;副屏;屏幕;\nStartupNotify=true\n').encode()


def install_plan(home, desktop=None):
    libexec = home/'.local/libexec'
    applications = home/'.local/share/applications'
    plan = {}
    for name in ('ipad_display.py', 'ipad-display-settings.py',
                 'ipad-display-control.py', 'ipad-virtual-display.py'):
        plan[libexec/name] = ((ROOT/'src'/name).read_bytes(), 0o755 if name != 'ipad_display.py' else 0o644)
    definitions = [
        (APP_ID+'.desktop', 'iPad 副屏设置', '调整副屏分辨率、刷新率、缩放和位置', 'ipad-display-settings.py', ''),
        ('ipad-display-start.desktop', '启动 iPad 副屏', '按保存的参数启动副屏', 'ipad-display-settings.py', ' --start'),
        ('ipad-display-stop.desktop', '关闭 iPad 副屏', '关闭串流及虚拟副屏', 'ipad-display-control.py', ' stop'),
    ]
    for filename, name, description, script, arguments in definitions:
        plan[applications/filename] = (desktop_entry(name, description, libexec/script, arguments), 0o644)
    plan[home/'.config/systemd/user/ipad-virtual-display.service'] = (
        (ROOT/'systemd/ipad-virtual-display.service').read_bytes(), 0o644)
    icons = home/'.local/share/icons/hicolor'
    plan[icons/'scalable/apps'/f'{APP_ID}.svg'] = ((ROOT/'assets'/f'{APP_ID}.svg').read_bytes(), 0o644)
    plan[icons/'256x256/apps'/f'{APP_ID}.png'] = ((ROOT/'assets/icon.png').read_bytes(), 0o644)
    if desktop:
        plan[desktop/'iPad 副屏设置.desktop'] = plan[applications/(APP_ID+'.desktop')][0], 0o755
    return plan


def add_moonlight_entry(document):
    result = dict(document)
    apps = list(result.get('apps', []))
    if not any(app.get('name') == 'iPad 副屏' for app in apps):
        apps.insert(0, {'name': 'iPad 副屏', 'image-path': 'desktop.png'})
    result['apps'] = apps
    return result


def apply_plan(plan, backup):
    for path, (content, mode) in plan.items():
        if path.exists() and path.read_bytes() != content:
            # Keep original files outside the repository; preserve their permissions.
            relative = Path(*path.parts[1:])
            original = backup/relative
            original.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, original)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        path.chmod(mode)


def check_dependencies():
    missing = [name for name in ('gdctl', 'systemctl', 'sunshine', 'notify-send', 'gio')
               if shutil.which(name) is None]
    probe = subprocess.run(['/usr/bin/python3', '-c',
        "import gi; gi.require_version('Gtk','4.0'); gi.require_version('Adw','1'); "
        "gi.require_version('Gst','1.0'); from gi.repository import Gtk,Adw,Gst; "
        "assert hasattr(Adw,'SpinRow')"], capture_output=True, text=True)
    if missing or probe.returncode:
        details = ', '.join(missing) or 'Python GI / GTK 4 / libadwaita / GStreamer'
        raise RuntimeError('缺少依赖：'+details+'。请先阅读 README 的安装要求。')
    service = subprocess.run(['systemctl', '--user', 'cat',
        'app-dev.lizardbyte.app.Sunshine.service'], capture_output=True, text=True)
    if service.returncode:
        raise RuntimeError('没有找到原生 Sunshine 用户服务，请先完成 Sunshine 安装和配对。')


def optional_run(*args):
    if shutil.which(args[0]):
        result = subprocess.run(args, capture_output=True, text=True)
        if result.returncode:
            print('提示：'+(result.stderr.strip() or ' '.join(args)+' 未完成'), file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--desktop', action='store_true', help='同时创建桌面快捷方式')
    parser.add_argument('--check', action='store_true', help='只检查依赖')
    parser.add_argument('--dry-run', action='store_true', help='只列出要安装的文件')
    args = parser.parse_args()
    home = Path.home()
    desktop = None
    if args.desktop:
        result = subprocess.run(['xdg-user-dir', 'DESKTOP'], capture_output=True, text=True)
        if result.returncode or not result.stdout.strip():
            raise RuntimeError('没有找到系统桌面目录。')
        desktop = Path(result.stdout.strip())
        if desktop == home:
            raise RuntimeError('系统没有启用桌面目录，请去掉 --desktop。')
    plan = install_plan(home, desktop)
    if args.dry_run:
        for path in plan:
            print(path)
        return
    check_dependencies()
    if args.check:
        print('依赖检查通过。')
        return
    config_dir = home/'.config/sunshine'
    apps_path = config_dir/'apps.json'
    if not (config_dir/'sunshine.conf').exists() or not apps_path.exists():
        raise RuntimeError('请先完成 Sunshine 初始化，再安装本工具。')
    apps = json.loads(apps_path.read_text())
    updated_apps = add_moonlight_entry(apps)
    if updated_apps != apps:
        plan[apps_path] = (json.dumps(updated_apps, ensure_ascii=False, indent=2).encode()+b'\n',
                           apps_path.stat().st_mode & 0o777)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:6]
    backup = home/'.local/state/ipad-display/install-backups'/stamp
    apply_plan(plan, backup)
    # Files are installed without stopping an existing display or starting Sunshine.
    subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
    optional_run('update-desktop-database', str(home/'.local/share/applications'))
    optional_run('gtk-update-icon-cache', '-f', '-t', str(home/'.local/share/icons/hicolor'))
    if desktop:
        optional_run('gio', 'set', '-t', 'string', str(desktop/'iPad 副屏设置.desktop'),
                     'metadata::trusted', 'true')
    print('安装完成。在应用列表搜索“iPad 副屏设置”。')
    print('被替换文件的备份目录：'+str(backup))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('安装失败：'+str(error), file=sys.stderr)
        sys.exit(1)
