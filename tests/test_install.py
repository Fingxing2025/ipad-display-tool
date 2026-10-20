"""Check portable launchers and installation without touching the real desktop."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('installer', Path(__file__).resolve().parents[1]/'install.py')
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


class InstallTests(unittest.TestCase):
    def test_install_to_home_with_spaces_keeps_user_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)/'Home With Spaces'
            preferences = home/'.config/ipad-display/settings.json'
            preferences.parent.mkdir(parents=True)
            preferences.write_text('{"fps":75}')
            plan = installer.install_plan(home, home/'Desktop')
            installer.apply_plan(plan, home/'backups')
            self.assertEqual(preferences.read_text(), '{"fps":75}')
            entry = home/'.local/share/applications/local.ipad.DisplaySettings.desktop'
            self.assertIn(f'"{home}/.local/libexec/ipad-display-settings.py"', entry.read_text())
            desktop = home/'Desktop/iPad 副屏设置.desktop'
            self.assertEqual(desktop.read_bytes(), entry.read_bytes())
            self.assertTrue(desktop.stat().st_mode & 0o111)
            self.assertNotIn(str(installer.ROOT.parent), entry.read_text())

    def test_existing_managed_file_is_backed_up_before_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            script = home/'.local/libexec/ipad-display-settings.py'
            script.parent.mkdir(parents=True)
            script.write_text('original version')
            script.chmod(0o700)
            backup = home/'backups'
            installer.apply_plan(installer.install_plan(home), backup)
            saved = backup/Path(*script.parts[1:])
            self.assertEqual(saved.read_text(), 'original version')
            self.assertEqual(saved.stat().st_mode & 0o777, 0o700)
            self.assertIn('class Application', script.read_text())

    def test_moonlight_entry_preserves_existing_apps_and_does_not_duplicate(self):
        original = {'env': {'PATH':'custom'}, 'apps':[{'name':'Existing','detached':['run app']}]}
        result = installer.add_moonlight_entry(original)
        self.assertEqual(result['apps'][1:], original['apps'])
        self.assertEqual(result['env'], original['env'])
        self.assertEqual(installer.add_moonlight_entry(result), result)
        self.assertEqual(len(original['apps']), 1)

    def test_dry_run_does_not_touch_home_or_require_sunshine(self):
        result = subprocess.run(['/usr/bin/python3', str(installer.ROOT/'install.py'), '--dry-run'],
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('local.ipad.DisplaySettings.desktop', result.stdout)


if __name__ == '__main__':
    unittest.main()
