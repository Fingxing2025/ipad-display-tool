#!/usr/bin/python3
"""Hold a configurable GNOME virtual display for Sunshine's portal capture."""
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gio, GLib, GLibUnix, Gst
from ipad_display import load_settings, layout_command, RUNTIME, write_json, frame_rate_fraction

settings = load_settings()
width, height, fps = (settings[k] for k in ('width', 'height', 'fps'))

SC = 'org.gnome.Mutter.ScreenCast'
DC = 'org.gnome.Mutter.DisplayConfig'
bus = Gio.bus_get_sync(Gio.BusType.SESSION)
loop = GLib.MainLoop()
pipeline = None
session = None
ready = False
failed = False
deadline = time.monotonic() + 20

def call(dest, path, iface, method, args=None):
    return bus.call_sync(dest, path, iface, method, args, None,
                         Gio.DBusCallFlags.NONE, 10000, None).unpack()

def monitors():
    return call(DC, '/org/gnome/Mutter/DisplayConfig', DC,
                'GetCurrentState')[1]

before_state = call(DC, '/org/gnome/Mutter/DisplayConfig', DC, 'GetCurrentState')
before = {m[0][0] for m in before_state[1]}

def preserve_layout(connector, apply_side=True):
    subprocess.run(layout_command(before_state, connector, settings),
                   check=True, timeout=10)

def notify(message):
    address = os.environ.get('NOTIFY_SOCKET')
    if address:
        if address.startswith('@'):
            address = '\0' + address[1:]
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.sendto(message.encode(), address)

def check_display():
    global ready, failed
    for spec, modes, props in monitors():
        if spec[0] not in before:
            try:
                preserve_layout(spec[0], apply_side=True)
            except Exception as error:
                print('Could not restore main display layout:', error, flush=True)
                failed = True
                loop.quit()
                return False
            print('Virtual display ready:', spec[0], spec[2], flush=True)
            write_json(RUNTIME, dict(settings, connector=spec[0], pid=os.getpid()))
            ready = True
            # A recreated monitor needs a new screen-sharing selection.
            (Path.home()/'.config/sunshine/portal_token').unlink(missing_ok=True)
            notify(f'READY=1\nSTATUS=iPad virtual display: {width}x{height} at {fps} Hz')
            return False
    if time.monotonic() > deadline:
        failed = True
        print('Virtual display did not appear', file=sys.stderr, flush=True)
        loop.quit()
        return False
    return True

def stream_added(conn, sender, path, iface, name, params):
    global pipeline
    node = params.unpack()[0]
    pipeline = Gst.parse_launch(
        f'pipewiresrc path={node} always-copy=false ! '
        f'video/x-raw,width={width},height={height},max-framerate={frame_rate_fraction(fps)} ! '
        'fakesink sync=false')
    pipeline.get_bus().add_signal_watch()
    pipeline.get_bus().connect('message::error', pipeline_error)
    pipeline.set_state(Gst.State.PLAYING)
    GLib.timeout_add(200, check_display)

def pipeline_error(bus_, message):
    global failed
    err, debug = message.parse_error()
    print('PipeWire error:', err, debug, file=sys.stderr, flush=True)
    failed = True
    loop.quit()

def stop(*args):
    print('Virtual display stop event:', repr(args), flush=True)
    loop.quit()
    return False

Gst.init(None)
try:
    session = call(SC, '/org/gnome/Mutter/ScreenCast', SC, 'CreateSession',
                   GLib.Variant('(a{sv})', ({},)))[0]
    stream = call(SC, session, SC+'.Session', 'RecordVirtual',
                  GLib.Variant('(a{sv})', ({
                      'cursor-mode': GLib.Variant('u', 1),
                      'is-platform': GLib.Variant('b', True)},)))[0]
    bus.signal_subscribe(SC, SC+'.Stream', 'PipeWireStreamAdded', stream,
                         None, Gio.DBusSignalFlags.NONE, stream_added)
    bus.signal_subscribe(SC, SC+'.Session', 'Closed', session,
                         None, Gio.DBusSignalFlags.NONE, stop)
    GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, stop)
    GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, stop)
    call(SC, session, SC+'.Session', 'Start')
    loop.run()
finally:
    # Removing a display while the capture client still uses it is unsafe.
    if subprocess.run(['pgrep', '-x', 'sunshine'],
                      stdout=subprocess.DEVNULL).returncode == 0:
        subprocess.run(['systemctl', '--user', 'stop', '--no-block',
                        'app-dev.lizardbyte.app.Sunshine.service'], check=False)
    for _ in range(100):
        if subprocess.run(['pgrep', '-x', 'sunshine'],
                          stdout=subprocess.DEVNULL).returncode:
            break
        time.sleep(.1)
    else:
        print('Waiting for Sunshine to release screen capture', flush=True)
        while not subprocess.run(['pgrep', '-x', 'sunshine'],
                                 stdout=subprocess.DEVNULL).returncode:
            time.sleep(.25)
    if pipeline:
        pipeline.set_state(Gst.State.NULL)
        pipeline.get_state(5 * Gst.SECOND)
    if session:
        try:
            call(SC, session, SC+'.Session', 'Stop')
        except GLib.Error:
            pass
    try:
        import json
        if json.loads(RUNTIME.read_text()).get('pid') == os.getpid():
            RUNTIME.unlink(missing_ok=True)
    except (FileNotFoundError, ValueError):
        pass
sys.exit(1 if failed or not ready else 0)
