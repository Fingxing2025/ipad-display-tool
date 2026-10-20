"""Regression checks for layout preservation and misleading 'started' states."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import ipad_display as d


def make_state(rotated=False, mirrored=False):
    main = ('eDP-1', 'BOE', 'Built-in display', 'main')
    external = ('DP-1', 'Dell', 'External display', 'external')
    modes = [(main, [('main-mode', 2560, 1600, 143.999, 1.0, [], {'is-current': True})], {}),
             (external, [('external-mode', 1920, 1080, 60.0, 1.0, [], {'is-current': True})], {})]
    logical = [(200, 100, 1.25, 0, True, [main], {}),
               (2248, 100, 1.0, 1 if rotated else 0, False, [external], {})]
    if mirrored:
        logical = [(200, 100, 1.25, 0, True, [main, external], {})]
    return (1, modes, logical, {})


def parse_layout(args):
    result = []
    for arg in args[2:]:
        if arg == '--logical-monitor':
            result.append([])
        else:
            result[-1].append(arg)
    return result


class LayoutTests(unittest.TestCase):
    def test_all_sides_preserve_primary_mode_scale_and_existing_spacing(self):
        for side in d.SIDES:
            with self.subTest(side=side):
                layout = parse_layout(d.layout_command(make_state(), 'Meta-0',
                                      dict(width=2360, height=1640, fps=60, side=side)))
                main, external, ipad = layout
                self.assertIn('--primary', main)
                self.assertEqual(main[main.index('--mode')+1], 'main-mode')
                self.assertEqual(main[main.index('--scale')+1], '1.25')
                main_x = int(main[main.index('--x')+1])
                external_x = int(external[external.index('--x')+1])
                self.assertEqual(external_x-main_x, 2048)
                self.assertEqual(main_x, 2360 if side == 'left' else 0)
                self.assertEqual(int(main[main.index('--y')+1]), 1640 if side == 'above' else 0)
                self.assertEqual(int(ipad[ipad.index('--x')+1]), 3968 if side == 'right' else 0)
                self.assertEqual(int(ipad[ipad.index('--y')+1]), 1280 if side == 'below' else 0)

    def test_rotated_output_bounds_are_not_overlapped(self):
        layout = parse_layout(d.layout_command(make_state(rotated=True), 'Meta-0',
                              dict(d.DEFAULTS, side='right')))
        self.assertIn('90', layout[1])
        self.assertEqual(int(layout[-1][layout[-1].index('--x')+1]), 3128)
        below = parse_layout(d.layout_command(make_state(rotated=True), 'Meta-0',
                             dict(d.DEFAULTS, side='below')))
        self.assertEqual(int(below[-1][below[-1].index('--y')+1]), 1920)

    def test_mirrored_group_is_preserved_and_cannot_duplicate_primary(self):
        layout = parse_layout(d.layout_command(make_state(mirrored=True), 'Meta-0', d.DEFAULTS))
        self.assertEqual(len(layout), 2)
        self.assertEqual(layout[0].count('--monitor'), 2)
        self.assertEqual(sum(row.count('--primary') for row in layout), 1)

    def test_old_virtual_monitor_is_excluded_from_preserved_layout(self):
        state = make_state()
        virtual = ('Meta-0', 'MetaVendor', 'Virtual remote monitor', 'old')
        state[1].append((virtual, [('virtual-mode', 1920, 1080, 60.0, 1, [], {'is-current': True})], {}))
        state[2].append((0, 100, 1, 0, False, [virtual], {}))
        layout = parse_layout(d.layout_command(state, 'Meta-0', d.DEFAULTS))
        self.assertEqual(sum(row.count('Meta-0') for row in layout), 1)

    def test_scaled_ipad_uses_logical_dimensions_and_keeps_main_scale(self):
        for side in ('left', 'above'):
            settings = dict(d.DEFAULTS, width=2400, height=1500, scale=1.25, side=side)
            layout = parse_layout(d.layout_command(make_state(), 'Meta-0', settings))
            self.assertEqual(layout[-1][layout[-1].index('--scale')+1], '1.25')
            self.assertEqual(layout[0][layout[0].index('--scale')+1], '1.25')
            axis, expected = ('--x', 1920) if side == 'left' else ('--y', 1200)
            self.assertEqual(int(layout[0][layout[0].index(axis)+1]), expected)

    def test_scaled_ipad_keeps_existing_multimonitor_layout_to_right_or_below(self):
        for side in ('right', 'below'):
            settings = dict(d.DEFAULTS, width=2400, height=1500, scale=1.5, side=side)
            layout = parse_layout(d.layout_command(make_state(), 'Meta-0', settings))
            self.assertEqual(layout[-1][layout[-1].index('--scale')+1], '1.5')
            axis, expected = ('--x', 3968) if side == 'right' else ('--y', 1280)
            self.assertEqual(int(layout[-1][layout[-1].index(axis)+1]), expected)


class SettingsTests(unittest.TestCase):
    def test_invalid_change_does_not_destroy_saved_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'settings.json'
            d.save_settings(d.DEFAULTS, path)
            for change in (dict(width=1921), dict(fps=0), dict(fps=361), dict(fps=True),
                           dict(fps=float('nan')), dict(fps=float('inf')),
                           dict(scale=0), dict(scale=5), dict(scale=True), dict(scale=1.75),
                           dict(side='up'), dict(height=True), dict(width=12000)):
                with self.assertRaises(ValueError):
                    d.save_settings(dict(d.DEFAULTS, **change), path)
                self.assertEqual(d.load_settings(path), d.DEFAULTS)

    def test_portrait_and_high_refresh_settings_survive_save(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'settings.json'
            chosen = dict(d.DEFAULTS, width=1640, height=2360, fps=120, side='above')
            d.save_settings(chosen, path)
            self.assertEqual(d.load_settings(path), chosen)

    def test_custom_integer_and_fractional_refresh_rates_survive_save(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'settings.json'
            for fps in (1, 48, 59.94, 72, 75, 119.88, 144, 165, 240, 360):
                chosen = dict(d.DEFAULTS, fps=fps)
                d.save_settings(chosen, path)
                self.assertEqual(d.load_settings(path), chosen)

    def test_old_profile_keeps_parameters_and_gains_default_scale(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'settings.json'
            original = dict(width=2408, height=1506, fps=90, side='left')
            path.write_text(json.dumps(original))
            self.assertEqual(d.load_settings(path), dict(original, scale=1.0))
            self.assertEqual(json.loads(path.read_text()), original)

    def test_scaling_choices_follow_resolution_and_real_system_capabilities(self):
        self.assertEqual(d.supported_scales(2408, 1506), [1.0, 2.0])
        for scale in (1, 1.25, 1.5, 2):
            self.assertIn(scale, d.supported_scales(2400, 1500))
            self.assertEqual(d.validate(dict(d.DEFAULTS, width=2400, height=1500, scale=scale))['scale'], scale)
        state = make_state()
        virtual = ('Meta-0', 'MetaVendor', 'Virtual remote monitor', 'current')
        state[1].append((virtual, [('mode', 2400, 1500, 90, 1, [1, 2], {})], {}))
        self.assertEqual(d.supported_scales(2400, 1500, state), [1, 2])

    def test_unsupported_scaling_is_rejected_before_saving(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'settings.json'
            d.save_settings(d.DEFAULTS, path)
            with self.assertRaises(ValueError):
                d.save_settings(dict(d.DEFAULTS, width=2408, height=1506, scale=1.25), path)
            self.assertEqual(d.load_settings(path), d.DEFAULTS)

    def test_scale_rounding_in_system_report_does_not_force_recreation(self):
        settings = dict(d.DEFAULTS, scale=4/3)
        monitor = dict(settings, scale=1.3333333730697632, connector='Meta-0')
        self.assertTrue(d.matches(settings, monitor))

    def test_fractional_refresh_is_preserved_in_pipewire_caps_and_monitor_check(self):
        self.assertEqual(d.frame_rate_fraction(59.94), '2997/50')
        self.assertEqual(d.frame_rate_fraction(165), '165/1')
        state = make_state()
        virtual = ('Meta-0', 'MetaVendor', 'Virtual remote monitor', 'current')
        state[1].append((virtual, [('virtual-mode', 1920, 1080, 59.9399986, 1, [], {'is-current': True})], {}))
        state[2].append((0, 100, 1, 0, False, [virtual], {}))
        monitor = d.virtual_monitor(state)
        self.assertEqual(monitor['fps'], 59.94)
        self.assertTrue(d.matches(dict(d.DEFAULTS, fps=59.94), monitor))


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.monitor = dict(d.DEFAULTS, connector='Meta-0')
        self.patches = [patch.object(d, 'virtual_monitor', return_value=self.monitor),
                        patch.object(d, 'service_properties', return_value={'ActiveState': 'active', 'MainPID': '123'}),
                        patch.object(d, 'pending_authorization', return_value=False),
                        patch.object(d, 'urlopen', side_effect=lambda *a, **kw: io.BytesIO(b'<root status_code="200"/>'))]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def status(self, messages):
        with patch.object(d, 'command', return_value=type('Result', (), {'stdout': '\n'.join(messages)})()):
            return d.health()['state']

    def test_active_process_and_open_port_do_not_mean_capture_is_ready(self):
        self.assertEqual(self.status([]), 'error')
        self.assertEqual(self.status(['Fatal: Unable to find display or encoder']), 'error')

    def test_pending_consent_is_reported_even_when_process_is_active(self):
        with patch.object(d, 'pending_authorization', return_value=True):
            self.assertEqual(self.status([]), 'authorization')

    def test_sharing_primary_is_not_reported_as_working_secondary(self):
        self.assertEqual(self.status(["Found stream for display id/name: 'eDP-1'"]), 'wrong-source')

    def test_disconnect_updates_connected_state_without_destroying_monitor(self):
        messages = ["Streaming display 'Meta-0'", 'CLIENT CONNECTED']
        self.assertEqual(self.status(messages), 'connected')
        self.assertEqual(self.status(messages+['CLIENT DISCONNECTED']), 'ready')

    def test_successful_recovery_overrides_old_start_failure(self):
        self.assertEqual(self.status(['Fatal: Unable to find display',
                                     "Found stream for display id/name: 'Meta-0'"]), 'ready')


if __name__ == '__main__':
    unittest.main()
