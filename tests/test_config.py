"""Tests for ConfigManager and data models."""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config import AppEntry, ConfigManager, _collapse_path


class TestConfigManager(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.config_dir = Path(self.test_dir) / "autolaunch"

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_default_initialization(self):
        mgr = ConfigManager(self.config_dir)
        self.assertEqual(mgr.active_profile, "Default")
        self.assertEqual(mgr.countdown_seconds, 5)
        self.assertTrue(mgr.enable_countdown)
        self.assertTrue(mgr.countdown_autostart_only)
        self.assertIn("Default", mgr.profiles)
        self.assertTrue((self.config_dir / "config.json").is_file())

    def test_add_and_remove_profile(self):
        mgr = ConfigManager(self.config_dir)
        self.assertTrue(mgr.add_profile("Work"))
        self.assertIn("Work", mgr.profiles)
        self.assertFalse(mgr.add_profile("Work"))

        self.assertTrue(mgr.set_active_profile("Work"))
        self.assertEqual(mgr.active_profile, "Work")

        self.assertTrue(mgr.remove_profile("Default"))
        self.assertNotIn("Default", mgr.profiles)
        self.assertFalse(mgr.remove_profile("Work"))

    def test_duplicate_profile(self):
        mgr = ConfigManager(self.config_dir)
        app = AppEntry(name="Test", command="true", delay_seconds=3)
        mgr.add_app_to_profile("Default", app)
        self.assertTrue(mgr.duplicate_profile("Default", "Backup"))
        self.assertIn("Backup", mgr.profiles)
        self.assertEqual(len(mgr.profiles["Backup"].apps), 1)
        self.assertNotEqual(mgr.profiles["Backup"].apps[0].id, app.id)
        self.assertEqual(mgr.profiles["Backup"].apps[0].delay_seconds, 3)

    def test_app_entries_in_profile(self):
        mgr = ConfigManager(self.config_dir)
        app = AppEntry(
            name="Zen Browser (YouTube)",
            command="zen-youtube",
            desktop_file="zen-youtube.desktop",
            icon="zen-youtube",
            enabled=True,
            delay_seconds=2,
        )
        self.assertTrue(mgr.add_app_to_profile("Default", app))
        prof = mgr.get_current_profile()
        self.assertEqual(len(prof.apps), 1)
        self.assertEqual(prof.apps[0].name, "Zen Browser (YouTube)")
        self.assertEqual(prof.apps[0].delay_seconds, 2)

        mgr2 = ConfigManager(self.config_dir)
        prof2 = mgr2.get_current_profile()
        self.assertEqual(len(prof2.apps), 1)
        self.assertEqual(prof2.apps[0].name, "Zen Browser (YouTube)")

        app.name = "Zen YouTube Updated"
        self.assertTrue(mgr2.update_app_in_profile("Default", app))
        self.assertEqual(mgr2.get_current_profile().apps[0].name, "Zen YouTube Updated")

        self.assertTrue(mgr2.remove_app_from_profile("Default", app.id))
        self.assertEqual(len(mgr2.get_current_profile().apps), 0)

    def test_minimization_settings(self):
        mgr = ConfigManager(self.config_dir)
        self.assertFalse(mgr.launch_minimized)

        mgr.launch_minimized = True
        mgr.save()

        mgr2 = ConfigManager(self.config_dir)
        self.assertTrue(mgr2.launch_minimized)

        app = AppEntry(
            name="Minimized App",
            command="true",
            start_minimized=True,
        )
        self.assertTrue(mgr2.add_app_to_profile("Default", app))
        mgr3 = ConfigManager(self.config_dir)
        saved_app = mgr3.get_current_profile().apps[0]
        self.assertTrue(saved_app.start_minimized)

    def test_countdown_settings(self):
        mgr = ConfigManager(self.config_dir)
        self.assertTrue(mgr.countdown_autostart_only)

        mgr.countdown_autostart_only = False
        mgr.countdown_seconds = 10
        mgr.save()

        mgr2 = ConfigManager(self.config_dir)
        self.assertFalse(mgr2.countdown_autostart_only)
        self.assertEqual(mgr2.countdown_seconds, 10)

    def test_default_profile_lifecycle(self):
        mgr = ConfigManager(self.config_dir)
        self.assertEqual(mgr.default_profile, "Default")

        self.assertTrue(mgr.add_profile("Work"))
        self.assertTrue(mgr.set_default_profile("Work"))
        self.assertEqual(mgr.default_profile, "Work")
        self.assertFalse(mgr.set_default_profile("NonExistent"))

        mgr2 = ConfigManager(self.config_dir)
        self.assertEqual(mgr2.default_profile, "Work")

        self.assertTrue(mgr2.rename_profile("Work", "Office"))
        self.assertEqual(mgr2.default_profile, "Office")

        mgr3 = ConfigManager(self.config_dir)
        self.assertEqual(mgr3.default_profile, "Office")

        self.assertTrue(mgr3.remove_profile("Office"))
        self.assertEqual(mgr3.default_profile, "Default")

    def test_collapse_path_boundary_and_corrupt_backup(self):
        fake_home = Path(self.test_dir) / "alice"
        with patch("config.Path.home", return_value=fake_home):
            self.assertEqual(_collapse_path(f"{fake_home}/apps/run"), "~/apps/run")
            self.assertEqual(_collapse_path(f"{fake_home}2/apps/run"), f"{fake_home}2/apps/run")
            self.assertEqual(_collapse_path(f"cmd {fake_home}/a"), "cmd $HOME/a")

        self.config_dir.mkdir(parents=True, exist_ok=True)
        (self.config_dir / "config.json").write_text("{corrupt json", encoding="utf-8")
        mgr = ConfigManager(self.config_dir)
        self.assertTrue((self.config_dir / "config.json.corrupt.bak").is_file())
        self.assertEqual(mgr.active_profile, "Default")

    def test_window_geometry_clamping_and_malformed_numbers(self):
        self.config_dir.mkdir(parents=True, exist_ok=True)
        (self.config_dir / "config.json").write_text(
            '{"countdown_seconds": null, "window_width": 100, "window_height": 900, '
            '"profiles": {"Default": {"apps": [{"name": "A", "command": "true", "delay_seconds": null}]}}}',
            encoding="utf-8",
        )
        mgr = ConfigManager(self.config_dir)
        self.assertEqual(mgr.countdown_seconds, 5)
        self.assertEqual(mgr.window_width, 480)
        self.assertEqual(mgr.window_height, 900)
        self.assertEqual(mgr.get_current_profile().apps[0].delay_seconds, 0)


if __name__ == "__main__":
    unittest.main()
