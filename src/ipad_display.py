"""Shared settings, monitor layout and live health checks for the iPad display."""
import json
import math
import os
from fractions import Fraction
from pathlib import Path
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from urllib.request import urlopen

from gi.repository import Gio

HOME = Path.home()
SETTINGS = HOME / '.config/ipad-display/settings.json'
STATE = HOME / '.local/state/ipad-display'
RUNTIME = Path(os.environ.get('XDG_RUNTIME_DIR', '/tmp')) / f'ipad-display-{os.getuid()}.json'
SUNSHINE = 'app-dev.lizardbyte.app.Sunshine.service'
DISPLAY = 'ipad-virtual-display.service'
DEFAULTS = dict(width=1920, height=1080, fps=60, side='left', scale=1.0)
SIDES = ('left', 'right', 'above', 'below')


def validate(value):
    result = dict(DEFAULTS)
    if not isinstance(value, dict):
        raise ValueError('设置文件格式不正确。')
    result.update({k: v for k, v in value.items() if k in DEFAULTS})
    for key in ('width', 'height'):
        if type(result[key]) is not int:
            raise ValueError('分辨率必须是整数。')
    if not 640 <= result['width'] <= 3840 or not 480 <= result['height'] <= 3840:
        raise ValueError('宽度需在 640–3840，高度需在 480–3840 之间。')
    if result['width'] % 2 or result['height'] % 2:
        raise ValueError('宽度和高度需为偶数，才能正常编码画面。')
    fps = result['fps']
    if type(fps) not in (int, float) or not math.isfinite(fps) or not 1 <= fps <= 360:
        raise ValueError('刷新率需在 1–360 Hz 之间，可输入小数。')
    result['fps'] = round(fps, 2)
    scale = result['scale']
    if type(scale) not in (int, float) or not math.isfinite(scale) or not 1 <= scale <= 4:
        raise ValueError('副屏缩放需在 100%–400% 之间。')
    if not any(math.isclose(scale, supported, abs_tol=1e-5)
               for supported in supported_scales(result['width'], result['height'])):
        raise ValueError('该分辨率不支持所选缩放，请在工具中重新选择缩放比例。')
    if result['side'] not in SIDES:
        raise ValueError('请选择左、右、上或下。')
    return result


def frame_rate_fraction(fps):
    rate = Fraction(str(fps)).limit_denominator(10000)
    return f'{rate.numerator}/{rate.denominator}'


def supported_scales(width, height, state=None):
    """Use real mode capabilities, or GNOME 50's rational scale constraints."""
    if state is not None:
        for spec, modes, props in state[1]:
            if spec[2] == 'Virtual remote monitor':
                for mode in modes:
                    if mode[1:3] == (width, height) and mode[5]:
                        return list(mode[5])
    # Mutter 50 permits rational factors with denominator <= 4. The result
    # must have integral logical dimensions and at least 800*480 logical pixels.
    # Reference: GNOME/mutter 50.1, meta-monitor.c, for_each_scale().
    scales = set()
    for denominator in range(1, 5):
        for numerator in range(denominator, 4*denominator+1):
            if width*denominator % numerator or height*denominator % numerator:
                continue
            logical_width = width*denominator//numerator
            logical_height = height*denominator//numerator
            if logical_width*logical_height >= 800*480:
                scales.add(numerator/denominator)
    return sorted(scales) or [1.0]


def load_settings(path=SETTINGS):
    return validate(json.loads(path.read_text())) if path.exists() else dict(DEFAULTS)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def save_settings(value, path=SETTINGS):
    result = validate(value)
    write_json(path, result)
    return result


def command(*args, timeout=8):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def display_state():
    bus = Gio.bus_get_sync(Gio.BusType.SESSION)
    return bus.call_sync('org.gnome.Mutter.DisplayConfig',
                         '/org/gnome/Mutter/DisplayConfig',
                         'org.gnome.Mutter.DisplayConfig', 'GetCurrentState',
                         None, None, Gio.DBusCallFlags.NONE, 3000, None).unpack()


def virtual_monitor(state=None):
    state = display_state() if state is None else state
    for spec, modes, props in state[1]:
        if spec[2] != 'Virtual remote monitor':
            continue
        mode = next((m for m in modes if m[-1].get('is-current')), None)
        if mode is None:
            continue
        logical = next((m for m in state[2] if any(s[0] == spec[0] for s in m[5])), None)
        others = [m for m in state[2] if not any(s[0] == spec[0] for s in m[5])]
        side = 'right'
        if logical and others:
            if logical[0] < min(m[0] for m in others):
                side = 'left'
            elif logical[1] < min(m[1] for m in others):
                side = 'above'
            elif logical[1] > max(m[1] for m in others):
                side = 'below'
        return dict(connector=spec[0], width=mode[1], height=mode[2],
                    fps=round(mode[3], 2), side=side, scale=logical[2] if logical else 1.0)
    return None


def matches(settings, monitor):
    return monitor is not None and all(
        math.isclose(monitor.get(k, 1.0), v, abs_tol=1e-5) if k == 'scale'
        else monitor.get(k) == v for k, v in settings.items())


def layout_command(state, connector, settings):
    """Preserve existing modes, scales, rotation, primary and relative positions."""
    current = {spec[0]: next((m for m in modes if m[-1].get('is-current')), None)
               for spec, modes, props in state[1]}
    existing = [m for m in state[2] if not any(s[0] == connector for s in m[5])]
    if not existing:
        raise ValueError('未找到电脑主屏，暂不改变屏幕布局。')
    min_x, min_y = min(m[0] for m in existing), min(m[1] for m in existing)
    right, bottom = 0, 0
    for x, y, scale, transform, primary, specs, props in existing:
        for spec in specs:
            mode = current.get(spec[0])
            if mode is None:
                raise ValueError('无法读取主屏当前模式。')
            width, height = mode[1:3]
            if transform in (1, 3, 5, 7):
                width, height = height, width
            right = max(right, x - min_x + round(width / scale))
            bottom = max(bottom, y - min_y + round(height / scale))
    side = settings['side']
    ipad_scale = settings.get('scale', 1.0)
    shift_x = round(settings['width']/ipad_scale) if side == 'left' else 0
    shift_y = round(settings['height']/ipad_scale) if side == 'above' else 0
    transforms = ('normal', '90', '180', '270', 'flipped', 'flipped-90',
                  'flipped-180', 'flipped-270')
    args = ['gdctl', 'set']
    for x, y, scale, transform, primary, specs, props in existing:
        args += ['--logical-monitor', '--x', str(x-min_x+shift_x),
                 '--y', str(y-min_y+shift_y), '--scale', str(scale),
                 '--transform', transforms[transform]]
        if primary:
            args += ['--primary']
        for spec in specs:
            args += ['--monitor', spec[0], '--mode', current[spec[0]][0]]
    x = right if side == 'right' else 0
    y = bottom if side == 'below' else 0
    return args + ['--logical-monitor', '--monitor', connector, '--scale', str(ipad_scale),
                   '--x', str(x), '--y', str(y)]


def service_properties():
    result = command('systemctl', '--user', 'show', SUNSHINE,
                     '-p', 'ActiveState', '-p', 'MainPID')
    return dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)


def pending_authorization():
    # Introspection reads request paths without opening any new portal session.
    bus = Gio.bus_get_sync(Gio.BusType.SESSION)
    root = '/org/freedesktop/portal/desktop/request'
    try:
        def nodes(path):
            result = bus.call_sync('org.freedesktop.portal.Desktop', path,
                                  'org.freedesktop.DBus.Introspectable', 'Introspect',
                                  None, None, Gio.DBusCallFlags.NONE, 1000, None)
            return [n.attrib['name'] for n in ET.fromstring(result.unpack()[0]).findall('node')]
        for sender in nodes(root):
            if any(name.startswith('Sunshine') for name in nodes(root+'/'+sender)):
                return True
    except Exception:
        pass
    return False


def health():
    monitor = virtual_monitor()
    properties = service_properties()
    active = properties.get('ActiveState') in ('active', 'activating')
    result = dict(monitor=monitor, state='stopped', title='副屏未开启',
                  detail='选择参数后，点击“应用并启动”。')
    if monitor is None:
        if active:
            result.update(state='starting', title='正在创建副屏', detail='请稍候。')
        return result
    if not active:
        result.update(state='error', title='连接服务未运行', detail='点击“应用并启动”恢复连接服务。')
        return result
    if pending_authorization():
        result.update(state='authorization', title='等待共享授权',
                      detail='在系统窗口选择“Virtual remote monitor”，点击“共享”。')
        return result
    pid = properties.get('MainPID', '0')
    messages = []
    if pid != '0':
        log = command('journalctl', '--user', '-u', SUNSHINE, '_PID='+pid,
                      '--no-pager', '-n', '40', '-o', 'cat',
                      '--grep=CLIENT (CONNECTED|DISCONNECTED)|Streaming display |Found stream for display|Fatal:|Unavailable cursor mode|Start failed|Could not start .*session')
        messages = log.stdout.splitlines()
    latest_capture = next((m for m in reversed(messages) if
                           'Streaming display ' in m or 'Found stream for display' in m), '')
    latest_fatal = next((i for i in range(len(messages)-1, -1, -1)
                         if 'Fatal:' in messages[i] or 'Unavailable cursor mode' in messages[i]
                         or 'Start failed' in messages[i] or 'Could not start ' in messages[i]), -1)
    last_capture_index = next((i for i in range(len(messages)-1, -1, -1)
                               if 'Streaming display ' in messages[i]
                               or 'Found stream for display' in messages[i]), -1)
    if latest_fatal > last_capture_index:
        result.update(state='error', title='屏幕共享未就绪', detail='点击“重新授权”，重新选择虚拟副屏。')
        return result
    if latest_capture and monitor['connector'] not in latest_capture:
        result.update(state='wrong-source', title='当前共享了其他屏幕',
                      detail='点击“重新授权”，选择“Virtual remote monitor”。')
        return result
    try:
        with urlopen('http://127.0.0.1:47989/serverinfo', timeout=1.5) as response:
            server = ET.fromstring(response.read(65536))
        if server.get('status_code') != '200':
            raise ValueError('Sunshine 尚未就绪')
    except Exception:
        result.update(state='starting', title='连接服务正在启动',
                      detail='系统可能弹出共享窗口，请在电脑主屏上确认。')
        return result
    if not latest_capture:
        result.update(state='error', title='尚未确认共享来源', detail='点击“重新授权”，选择虚拟副屏。')
        return result
    client_event = next((m for m in reversed(messages) if 'CLIENT CONNECTED' in m
                         or 'CLIENT DISCONNECTED' in m), '')
    connected = 'CLIENT CONNECTED' in client_event
    result.update(state='connected' if connected else 'ready',
                  title='iPad 已连接' if connected else '副屏已就绪',
                  detail='鼠标和窗口可移入副屏。' if connected else '在 iPad 的 Moonlight 中打开“iPad 副屏”。')
    return result
