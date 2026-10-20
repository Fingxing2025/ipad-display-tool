#!/usr/bin/python3
"""A small native GNOME settings window for the iPad secondary display."""
import json
from pathlib import Path
import subprocess
import sys
import threading

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio, GLib, Gtk

from ipad_display import (DEFAULTS, HOME, SETTINGS, SIDES, STATE, SUNSHINE,
                          health, load_settings, save_settings, validate, virtual_monitor,
                          display_state, supported_scales)

CONTROL = Path(__file__).with_name('ipad-display-control.py')
RESOLUTIONS = [('1920 × 1080', 1920, 1080), ('1920 × 1200', 1920, 1200),
               ('2048 × 1536', 2048, 1536), ('2360 × 1640', 2360, 1640),
               ('2400 × 1500', 2400, 1500),
               ('2560 × 1600', 2560, 1600), ('1280 × 720', 1280, 720),
               ('自定义', None, None)]
FREQUENCIES = (30, 60, 90, 120, 144, 240)
SIDE_NAMES = ('左侧', '右侧', '上方', '下方')


def idle(callback, *args):
    GLib.idle_add(callback, *args)


class SettingsWindow(Adw.ApplicationWindow):
    def __init__(self, application):
        super().__init__(application=application, title='iPad 副屏设置',
                         default_width=600, default_height=850)
        self.app = application
        self.busy = False
        self.polling = False
        self.closed = False
        self.generation = 0
        self.changing_resolution = False
        self.updating_scales = False
        self.scale_options = [1.0]
        try:
            self.known_display_state = display_state()
        except Exception:
            self.known_display_state = None
        self.last_status = None
        self.diagnostics_text = ''
        self.connect('close-request', self.on_close)
        self.overlay = Adw.ToastOverlay()
        self.set_content(self.overlay)
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        self.overlay.set_child(toolbar)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        toolbar.set_content(scroller)
        clamp = Adw.Clamp(maximum_size=560, tightening_threshold=480)
        scroller.set_child(clamp)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=22,
                          margin_top=12, margin_bottom=24, margin_start=20, margin_end=20)
        clamp.set_child(content)

        heading = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        self.indicator = Gtk.Image.new_from_icon_name('video-display-symbolic')
        self.indicator.set_pixel_size(36)
        heading.append(self.indicator)
        status_text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5, hexpand=True)
        self.title_label = Gtk.Label(label='正在检查副屏…', xalign=0, wrap=True)
        self.title_label.add_css_class('title-2')
        self.detail_label = Gtk.Label(xalign=0, wrap=True)
        self.detail_label.add_css_class('dim-label')
        self.metadata = Gtk.Label(xalign=0, wrap=True, selectable=True)
        self.metadata.add_css_class('caption')
        status_text.append(self.title_label)
        status_text.append(self.detail_label)
        status_text.append(self.metadata)
        heading.append(status_text)
        self.spinner = Gtk.Spinner()
        heading.append(self.spinner)
        content.append(heading)
        self.error_label = Gtk.Label(xalign=0, wrap=True, selectable=True, visible=False)
        self.error_label.add_css_class('error')
        content.append(self.error_label)

        self.preview = Gtk.Grid(column_spacing=12, row_spacing=12,
                                halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.preview.set_size_request(-1, 140)
        content.append(self.preview)

        self.group = Adw.PreferencesGroup(title='副屏参数',
                         description='修改后点击“应用并启动”。主屏的画质设置会保留。')
        content.append(self.group)
        self.resolution = Adw.ComboRow(title='分辨率',
                          model=Gtk.StringList.new([r[0] for r in RESOLUTIONS]))
        self.group.add(self.resolution)
        self.width_row = Adw.SpinRow(title='宽度', subtitle='像素，使用偶数',
                         adjustment=Gtk.Adjustment(lower=640, upper=3840, step_increment=2, page_increment=100))
        self.height_row = Adw.SpinRow(title='高度', subtitle='像素，使用偶数',
                          adjustment=Gtk.Adjustment(lower=480, upper=3840, step_increment=2, page_increment=100))
        self.group.add(self.width_row)
        self.group.add(self.height_row)
        self.scale_row = Adw.ComboRow(title='副屏缩放',
                          subtitle='比例越大，文字和窗口越大。',
                          model=Gtk.StringList.new(['100%']))
        self.group.add(self.scale_row)
        self.fps_row = Adw.SpinRow(title='刷新率', subtitle='1–360 Hz，可直接输入，支持两位小数',
                       digits=2, numeric=True,
                       adjustment=Gtk.Adjustment(lower=1, upper=360, step_increment=1, page_increment=10))
        self.group.add(self.fps_row)
        presets = Adw.ActionRow(title='常用值')
        preset_buttons = Gtk.Box(spacing=4, valign=Gtk.Align.CENTER)
        for fps in FREQUENCIES:
            button = Gtk.Button(label=str(fps), tooltip_text=f'{fps} Hz')
            button.add_css_class('flat')
            button.connect('clicked', lambda _, value=fps: self.fps_row.set_value(value))
            preset_buttons.append(button)
        presets.add_suffix(preset_buttons)
        self.group.add(presets)
        self.side_row = Adw.ComboRow(title='相对主屏的位置', model=Gtk.StringList.new(list(SIDE_NAMES)))
        self.group.add(self.side_row)
        self.unsaved_label = Gtk.Label(xalign=0, wrap=True)
        self.unsaved_label.add_css_class('caption')
        self.group.add(self.unsaved_label)

        help_text = Gtk.Label(label='启动会自动请求屏幕共享。弹窗中请选择“Virtual remote monitor”，再点“共享”。副屏保持开启时，重连会优先复用已有授权。',
                              xalign=0, wrap=True)
        help_text.add_css_class('dim-label')
        content.append(help_text)
        self.change_note = Gtk.Label(label='更改参数或重新授权会中断当前串流，需要在 iPad 上重新连接。实际串流帧率还需在 Moonlight 中设置。',
                                    xalign=0, wrap=True)
        self.change_note.add_css_class('caption')
        self.change_note.add_css_class('dim-label')
        content.append(self.change_note)

        actions = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                          margin_start=20, margin_end=20, margin_top=12, margin_bottom=12)
        footer = Adw.Clamp(maximum_size=560, tightening_threshold=480, child=actions)
        toolbar.add_bottom_bar(footer)
        self.start_button = Gtk.Button(label='应用并启动')
        self.start_button.add_css_class('suggested-action')
        self.start_button.add_css_class('pill')
        self.start_button.set_size_request(-1, 44)
        self.start_button.connect('clicked', lambda _: self.action('start', save=True))
        actions.append(self.start_button)
        secondary = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, homogeneous=True)
        actions.append(secondary)
        self.save_button = Gtk.Button(label='仅保存参数')
        self.save_button.connect('clicked', lambda _: self.save_only())
        secondary.append(self.save_button)
        self.stop_button = Gtk.Button(label='关闭副屏')
        self.stop_button.connect('clicked', lambda _: self.action('stop'))
        secondary.append(self.stop_button)
        self.authorize_button = Gtk.Button(label='重新授权')
        self.authorize_button.set_tooltip_text('按已保存的参数重建副屏，再弹出系统共享窗口')
        self.authorize_button.connect('clicked', lambda _: self.action('authorize'))
        secondary.append(self.authorize_button)
        self.diagnostic_button = Gtk.Button(label='查看诊断详情', halign=Gtk.Align.CENTER)
        self.diagnostic_button.add_css_class('flat')
        self.diagnostic_button.connect('clicked', lambda _: self.show_diagnostics())
        actions.append(self.diagnostic_button)

        self.resolution.connect('notify::selected', self.on_resolution)
        self.side_row.connect('notify::selected', self.on_side)
        for row in (self.width_row, self.height_row):
            row.connect('notify::value', self.on_dimensions)
        self.scale_row.connect('notify::selected', self.edited)
        self.fps_row.connect('notify::value', self.edited)
        self.loading = True
        self.initial_error = None
        try:
            if SETTINGS.exists():
                settings = load_settings()
            else:
                monitor = virtual_monitor()
                settings = {k: monitor[k] for k in DEFAULTS} if monitor else dict(DEFAULTS)
                save_settings(settings)
        except Exception as error:
            settings = dict(DEFAULTS)
            self.initial_error = str(error)
        self.saved = dict(settings)
        self.populate(settings)
        self.loading = False
        self.edited()
        self.poll_source = GLib.timeout_add_seconds(3, self.refresh)
        self.refresh()
        if self.initial_error:
            self.toast(self.initial_error+' 请重新保存参数。')

    def toast(self, message):
        self.overlay.add_toast(Adw.Toast.new(message))

    def populate(self, settings):
        self.width_row.set_value(settings['width'])
        self.height_row.set_value(settings['height'])
        index = next((i for i, r in enumerate(RESOLUTIONS)
                      if r[1:] == (settings['width'], settings['height'])), len(RESOLUTIONS)-1)
        self.resolution.set_selected(index)
        self.fps_row.set_value(settings['fps'])
        self.side_row.set_selected(SIDES.index(settings['side']))
        self.on_resolution()
        self.update_scale_options(preferred=settings['scale'])
        self.update_preview()

    def values(self):
        # Commit typed text before reading spin values.
        self.width_row.update()
        self.height_row.update()
        self.fps_row.update()
        return validate(dict(width=round(self.width_row.get_value()),
                             height=round(self.height_row.get_value()),
                             fps=round(self.fps_row.get_value(), 2),
                             side=SIDES[self.side_row.get_selected()],
                             scale=self.selected_scale()))

    def selected_scale(self):
        index = self.scale_row.get_selected()
        return self.scale_options[index] if index < len(self.scale_options) else 1.0

    def update_scale_options(self, preferred=None):
        if self.updating_scales:
            return
        self.updating_scales = True
        try:
            previous = self.selected_scale() if preferred is None else preferred
            width, height = round(self.width_row.get_value()), round(self.height_row.get_value())
            self.scale_options = supported_scales(width, height, self.known_display_state)
            labels = [f'{round(scale*100, 2):g}%' for scale in self.scale_options]
            self.scale_row.set_model(Gtk.StringList.new(labels))
            self.scale_row.set_selected(min(range(len(self.scale_options)),
                                            key=lambda i: abs(self.scale_options[i]-previous)))
            subtitle = '比例越大，文字和窗口越大。可选比例随分辨率变化。'
            if len(self.scale_options) <= 2 and width > 1600:
                subtitle = '想用 125% 或 150%，可选 2400 × 1500 或 1920 × 1200 分辨率。'
            self.scale_row.set_subtitle(subtitle)
        finally:
            self.updating_scales = False

    def on_dimensions(self, *_):
        if getattr(self, 'loading', True) or self.changing_resolution:
            return
        self.update_scale_options()
        self.edited()

    def on_resolution(self, *_):
        row = RESOLUTIONS[self.resolution.get_selected()]
        custom = row[1] is None
        self.width_row.set_visible(custom)
        self.height_row.set_visible(custom)
        if not custom:
            self.changing_resolution = True
            try:
                self.width_row.set_value(row[1])
                self.height_row.set_value(row[2])
            finally:
                self.changing_resolution = False
        self.update_scale_options()
        self.edited()

    def on_side(self, *_):
        self.update_preview()
        self.edited()

    def update_preview(self):
        child = self.preview.get_first_child()
        while child:
            next_child = child.get_next_sibling()
            self.preview.remove(child)
            child = next_child
        side = SIDES[self.side_row.get_selected()]
        positions = {'left': ((0, 0), (1, 0)), 'right': ((1, 0), (0, 0)),
                     'above': ((0, 0), (0, 1)), 'below': ((0, 1), (0, 0))}
        ipad_position, main_position = positions[side]
        for title, icon, position, width, css in (
                ('iPad 副屏', 'tablet-symbolic', ipad_position, 140, 'accent'),
                ('电脑主屏', 'computer-symbolic', main_position, 160, 'dim-label')):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                          halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                          margin_start=10, margin_end=10, margin_top=10, margin_bottom=10)
            image = Gtk.Image.new_from_icon_name(icon)
            image.set_pixel_size(32)
            box.append(image)
            box.append(Gtk.Label(label=title))
            box.add_css_class(css)
            frame = Gtk.Frame(child=box)
            frame.set_size_request(width, 88)
            self.preview.attach(frame, *position, 1, 1)

    def edited(self, *_):
        if getattr(self, 'loading', True) or self.updating_scales:
            return
        try:
            current = dict(width=round(self.width_row.get_value()), height=round(self.height_row.get_value()),
                           fps=round(self.fps_row.get_value(), 2), side=SIDES[self.side_row.get_selected()],
                           scale=self.selected_scale())
            different = current != self.saved
            self.unsaved_label.set_label('有未保存的修改。应用时会重新创建副屏。' if different else '')
            self.unsaved_label.set_visible(different)
        except (AttributeError, IndexError):
            pass

    def save_only(self):
        try:
            self.saved = save_settings(self.values())
            self.edited()
            self.toast('参数已保存，下次启动时生效。')
            return True
        except Exception as error:
            self.toast(str(error))
            return False

    def action(self, action, save=False):
        if self.busy:
            self.toast('正在处理上一项操作，请稍候。')
            return
        if save:
            try:
                self.saved = save_settings(self.values())
                self.edited()
            except Exception as error:
                self.toast(str(error))
                return
        self.busy = True
        self.app.hold()
        self.error_label.set_visible(False)
        self.generation += 1
        self.group.set_sensitive(False)
        for button in (self.start_button, self.stop_button, self.save_button, self.authorize_button):
            button.set_sensitive(False)
        self.spinner.start()
        self.set_progress('正在关闭副屏…' if action == 'stop' else '正在启动副屏…')
        threading.Thread(target=self.run_action, args=(action,), daemon=True).start()

    def set_progress(self, message):
        if not self.closed:
            self.title_label.set_label(message)
            self.detail_label.set_label('')
        return False

    def run_action(self, action):
        error = None
        try:
            # The child survives closing this window and completes its safe sequence.
            process = subprocess.Popen(['/usr/bin/python3', str(CONTROL), action, '--quiet'],
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       text=True, start_new_session=True)
            lines = []
            for line in process.stdout:
                lines.append(line)
                try:
                    event = json.loads(line)
                    idle(self.set_progress, event['message'])
                except (ValueError, KeyError):
                    pass
            process.wait()
            self.diagnostics_text = ''.join(lines)
            if process.returncode:
                error = next((json.loads(line)['message'] for line in reversed(lines)
                              if line.startswith('{') and json.loads(line).get('stage') in ('error', 'busy')),
                             '操作没有完成，请查看诊断详情。')
        except Exception as exception:
            error = str(exception)
        idle(self.action_done, error)

    def action_done(self, error):
        self.busy = False
        self.app.release()
        if self.closed:
            if error:
                subprocess.Popen(['notify-send', '--urgency=critical', 'iPad 副屏', error])
            return False
        self.group.set_sensitive(True)
        for button in (self.start_button, self.stop_button, self.save_button, self.authorize_button):
            button.set_sensitive(True)
        self.spinner.stop()
        if error:
            self.title_label.set_label('操作未完成')
            self.detail_label.set_label(error)
            self.error_label.set_label(error)
            self.error_label.set_visible(True)
            self.toast(error)
        self.refresh()
        return False

    def refresh(self):
        if self.closed:
            return False
        if not self.polling and not self.busy:
            self.polling = True
            generation = self.generation
            threading.Thread(target=self.read_health, args=(generation,), daemon=True).start()
        return True

    def read_health(self, generation):
        try:
            status = health()
        except Exception as error:
            status = dict(state='error', title='暂时无法检查状态', detail=str(error), monitor=None)
        idle(self.show_health, status, generation)

    def show_health(self, status, generation):
        self.polling = False
        if self.closed or self.busy or generation != self.generation:
            return False
        self.last_status = status
        self.title_label.set_label(status['title'])
        self.detail_label.set_label(status['detail'])
        for css in ('success', 'warning', 'error'):
            self.indicator.remove_css_class(css)
        state = status['state']
        icon = 'emblem-ok-symbolic' if state in ('ready', 'connected') else 'video-display-symbolic'
        self.indicator.set_from_icon_name(icon)
        if state in ('ready', 'connected'):
            self.indicator.add_css_class('success')
        elif state in ('error', 'wrong-source'):
            self.indicator.add_css_class('error')
        elif state == 'authorization':
            self.indicator.add_css_class('warning')
        monitor = status.get('monitor')
        if monitor:
            side = SIDE_NAMES[SIDES.index(monitor['side'])]
            self.metadata.set_label(f"当前：{monitor['width']} × {monitor['height']}，{monitor['fps']:g} Hz，缩放 {round(monitor['scale']*100, 2):g}%，主屏{side}")
        else:
            self.metadata.set_label('')
        self.spinner.set_spinning(state == 'starting')
        return False

    def show_diagnostics(self):
        dialog = Adw.Dialog(title='副屏诊断', content_width=640, content_height=480)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        dialog.set_child(view)
        scroll = Gtk.ScrolledWindow()
        view.set_content(scroll)
        text = Gtk.TextView(editable=False, cursor_visible=False, monospace=True,
                            wrap_mode=Gtk.WrapMode.WORD_CHAR, left_margin=14, right_margin=14,
                            top_margin=12, bottom_margin=12)
        scroll.set_child(text)
        text.get_buffer().set_text('正在读取诊断…')
        dialog.present(self)

        def read():
            parts = ['当前状态\n'+json.dumps(self.last_status, ensure_ascii=False, indent=2),
                     '\n最近一次操作\n'+self.diagnostics_text]
            try:
                log = subprocess.run(['journalctl', '--user', '-u', SUNSHINE, '-u',
                        'ipad-virtual-display.service', '--no-pager', '-n', '65', '-o', 'cat'],
                        capture_output=True, text=True, timeout=8)
                parts.append('\n运行日志\n'+log.stdout)
                if (STATE/'control.log').exists():
                    parts.append('\n控制日志\n'+'\n'.join((STATE/'control.log').read_text().splitlines()[-30:]))
            except Exception as error:
                parts.append(str(error))
            idle(lambda: (text.get_buffer().set_text('\n'.join(parts)), False)[1])
        threading.Thread(target=read, daemon=True).start()

    def on_close(self, *_):
        self.closed = True
        GLib.source_remove(self.poll_source)
        self.get_application().window = None
        return False


class Application(Adw.Application):
    def __init__(self):
        super().__init__(application_id='local.ipad.DisplaySettings',
                         flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        Gtk.Window.set_default_icon_name('local.ipad.DisplaySettings')
        self.window = None

    def do_activate(self):
        if self.window is None:
            self.window = SettingsWindow(self)
        self.window.present()

    def do_command_line(self, command_line):
        self.activate()
        if '--start' in command_line.get_arguments():
            self.window.action('start')
        return 0


if __name__ == '__main__':
    sys.exit(Application().run(sys.argv))
