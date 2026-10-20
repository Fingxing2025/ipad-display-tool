#!/usr/bin/python3
"""Create, verify and stop the local iPad display; JSON progress for the GUI."""
import argparse
import fcntl
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from ipad_display import (DISPLAY, SUNSHINE, STATE, SETTINGS, RUNTIME, HOME,
                          health, load_settings, matches, save_settings, virtual_monitor)

CONFIG = HOME / '.config/sunshine/sunshine.conf'
DROPIN = HOME / '.config/systemd/user' / (SUNSHINE + '.d/ipad-display.conf')
DEPENDENCIES = ('[Unit]\nRequires=ipad-virtual-display.service\n'
                'After=ipad-virtual-display.service\n'
                '[Service]\nEnvironment=SUNSHINE_ALLOW_LAN=1\n')


def emit(stage, message):
    print(json.dumps(dict(stage=stage, message=message), ensure_ascii=False), flush=True)
    logging.info('%s: %s', stage, message)


def active(unit):
    return subprocess.run(['systemctl', '--user', 'is-active', '--quiet', unit],
                          stdout=subprocess.DEVNULL).returncode == 0


def run(*args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=50)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or '操作未成功：' + ' '.join(args))


def configured():
    return (DROPIN.exists() and DROPIN.read_text() == DEPENDENCIES and CONFIG.exists()
            and re.search(r'^\s*capture\s*=\s*portal\s*$', CONFIG.read_text(), re.M))


def ensure_setup():
    if not CONFIG.exists():
        raise RuntimeError('没有找到 Sunshine 配置，请先完成 Sunshine 的配对设置。')
    text = CONFIG.read_text()
    if re.search(r'^\s*capture\s*=', text, re.M):
        text = re.sub(r'^\s*capture\s*=.*$', 'capture = portal', text, flags=re.M)
    else:
        text += '\ncapture = portal\n'
    CONFIG.write_text(text)
    DROPIN.parent.mkdir(parents=True, exist_ok=True)
    DROPIN.write_text(DEPENDENCIES)
    run('systemctl', '--user', 'daemon-reload')


def stop_display():
    emit('stopping', '正在关闭连接服务和副屏…')
    # Sunshine must release PipeWire before Mutter removes its monitor.
    run('systemctl', '--user', 'stop', SUNSHINE)
    run('systemctl', '--user', 'stop', DISPLAY)
    deadline = time.monotonic() + 8
    while virtual_monitor() is not None:
        if time.monotonic() > deadline:
            raise RuntimeError('系统尚未移除虚拟副屏。请稍后重试；详情可在工具中查看。')
        time.sleep(.25)
    RUNTIME.unlink(missing_ok=True)


def start_display(force=False):
    settings = load_settings()
    monitor = virtual_monitor()
    if not force and configured() and active(DISPLAY) and active(SUNSHINE) and matches(settings, monitor):
        current = health()
        if current['state'] in ('connected', 'ready', 'authorization', 'starting'):
            emit(current['state'], current['title']+'。'+current['detail'])
            return
    stop_display()
    ensure_setup()
    emit('creating', f"正在创建 {settings['width']}×{settings['height']}、{settings['fps']} Hz 副屏…")
    run('systemctl', '--user', 'reset-failed', DISPLAY, SUNSHINE)
    run('systemctl', '--user', 'start', DISPLAY)
    if not matches(settings, virtual_monitor()):
        run('systemctl', '--user', 'stop', DISPLAY)
        raise RuntimeError('系统没有应用所选屏幕参数。请改用 1920×1080、60 Hz 后重试。')
    # GNOME 50 tracks outputs correctly if the portal is initialized after layout.
    emit('sharing', '正在准备共享并请求系统授权…')
    run('systemctl', '--user', 'stop', 'xdg-desktop-portal.service')
    run('systemctl', '--user', 'restart', 'xdg-desktop-portal-gnome.service')
    run('systemctl', '--user', 'start', 'xdg-desktop-portal.service')
    run('systemctl', '--user', 'start', SUNSHINE)
    if not active(DISPLAY) or not active(SUNSHINE) or virtual_monitor() is None:
        raise RuntimeError('副屏或连接服务未能保持运行，请查看诊断详情。')
    # service=active is insufficient: Start may still be waiting for user consent.
    deadline = time.monotonic()+12
    while True:
        current = health()
        if current['state'] in ('authorization', 'connected', 'ready'):
            emit(current['state'], current['title']+'。'+current['detail'])
            return
        if current['state'] in ('error', 'wrong-source', 'stopped'):
            raise RuntimeError(current['title']+'。'+current['detail'])
        if time.monotonic() >= deadline:
            emit('starting', '副屏已创建，共享仍在初始化。请留意主屏上的系统授权窗口。')
            return
        time.sleep(.5)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('start', 'stop', 'authorize', 'status'), nargs='?', default='start')
    parser.add_argument('--quiet', action='store_true')
    args = parser.parse_args()
    if args.action == 'status':
        print(json.dumps(health(), ensure_ascii=False))
        return 0
    STATE.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=STATE/'control.log', level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')
    lock_path = Path(os.environ.get('XDG_RUNTIME_DIR', '/tmp')) / f'ipad-display-control-{os.getuid()}.lock'
    with lock_path.open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            emit('busy', '另一个副屏操作正在进行，请稍候。')
            return 3
        try:
            if args.action == 'stop':
                stop_display()
                emit('stopped', '副屏已关闭。')
            else:
                if not SETTINGS.exists():
                    monitor = virtual_monitor()
                    initial = {k: monitor[k] for k in ('width', 'height', 'fps', 'side', 'scale')} if monitor else load_settings()
                    save_settings(initial)
                start_display(force=args.action == 'authorize')
            if not args.quiet:
                message = '副屏已关闭。' if args.action == 'stop' else '副屏启动已处理。可打开“iPad 副屏设置”查看连接与授权状态。'
                subprocess.run(['notify-send', 'iPad 副屏', message], check=False)
            return 0
        except Exception as error:
            logging.exception('iPad display control failed')
            emit('error', str(error))
            if not args.quiet:
                subprocess.run(['notify-send', '--urgency=critical', 'iPad 副屏',
                                str(error)], check=False)
            return 1


if __name__ == '__main__':
    sys.exit(main())
