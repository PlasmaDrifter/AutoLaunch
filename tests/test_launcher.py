"""Tests for AppLauncher command generation, KWin rules, and window helpers."""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config import AppEntry
from launcher import AppLauncher


FIXTURE_KWINRULES = """[General]
Order=disabled-rule,zen-rule,firefox-rule,remmina-viewer,remmina-main,neg-rule,max-rule

[disabled-rule]
Description=Zen Disabled
Enabled=false
activity=99999999-9999-9999-9999-999999999999
position=10,10
size=100,100
wmclass=zen
wmclassmatch=1

[zen-rule]
Description=Zen
activity=027ddadb-5273-4710-aff6-ec33a2da7a60
activityrule=2
maximizehoriz=false
maximizevert=false
position=2305,14
size=1787,1359
title=Zen Browser 100%
titlematch=3
wmclass=zen
wmclassmatch=1

[firefox-rule]
Description=Firefox
activity=33d46b90-fd3d-4586-9888-c103c2a62f4d
activityrule=3
position=2334,32
size=1920,1297
wmclass=org.mozilla.firefox
wmclassmatch=1

[remmina-viewer]
Description=Remmina - Kubuntu
position=2146,57
size=2122,1231
title=^(?!Remmina Remote Desktop Client).*$
titlematch=2
wmclass=org.remmina.Remmina

[remmina-main]
Description=Remmina - viewer
position=2571,641
size=1035,535
title=Remmina Remote Desktop Client
titlematch=2
wmclass=org.remmina.Remmina

[neg-rule]
Description=LeftMonitorApp
position=-1920,0
size=1280,720
wmclass=leftapp

[max-rule]
Description=MaximizedApp
maximizevert=true
position=0,0
size=1920,1080
wmclass=maxapp
"""


class TestAppLauncher(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.fake_home = Path(self.test_dir)
        cfg_dir = self.fake_home / ".config"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "kwinrulesrc").write_text(FIXTURE_KWINRULES, encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_launch_command_desktop_entry(self):
        app = AppEntry(
            name="Zen Browser (YouTube)",
            desktop_file="zen-youtube.desktop",
            delay_seconds=0,
        )
        with patch("launcher.Path.home", return_value=self.fake_home):
            args, use_shell = AppLauncher.get_launch_command(app)
        self.assertFalse(use_shell)
        self.assertEqual(args[0], "/bin/sh")
        self.assertEqual(args[1], "-c")
        self.assertIn('gtk-launch "zen-youtube.desktop"', args[2])

    def test_launch_command_non_xdg_desktop_uses_gio(self):
        custom_desktop = self.fake_home / "custom.desktop"
        custom_desktop.write_text("[Desktop Entry]\nType=Application\nName=Custom\nExec=true\n", encoding="utf-8")
        app = AppEntry(name="Custom", desktop_file=str(custom_desktop))
        with patch("launcher.Path.home", return_value=self.fake_home), \
             patch.object(AppLauncher, "_has_binary", side_effect=lambda b: b in ("gtk-launch", "gio")):
            args, _ = AppLauncher.get_launch_command(app)
        self.assertIn(f'gio launch "{custom_desktop}"', args[2])

    def test_launch_command_with_delay(self):
        app = AppEntry(
            name="Zen Browser (YouTube)",
            desktop_file="zen-youtube.desktop",
            delay_seconds=5,
        )
        with patch("launcher.Path.home", return_value=self.fake_home):
            args, _ = AppLauncher.get_launch_command(app)
        self.assertIn("sleep 5 && exec", args[2])

    def test_launch_custom_command(self):
        app = AppEntry(
            name="Custom Script",
            command="python3 my_script.py",
            desktop_file="",
            delay_seconds=0,
        )
        with patch("launcher.Path.home", return_value=self.fake_home):
            args, _ = AppLauncher.get_launch_command(app)
        self.assertIn("exec python3 my_script.py", args[2])

    def test_target_window_classes(self):
        app = AppEntry(
            name="Zen YouTube",
            command="zen-youtube --class zen-youtube",
            desktop_file="zen-youtube.desktop",
        )
        classes = AppLauncher.get_target_window_classes(app)
        self.assertIn("zen-youtube", classes)

    def test_get_app_target_activity_firefox(self):
        app = AppEntry(
            name="Firefox",
            command="firefox",
            desktop_file="org.mozilla.firefox.desktop",
        )
        with patch("launcher.Path.home", return_value=self.fake_home):
            target_act = AppLauncher.get_app_target_activity(app)
        self.assertEqual(target_act, "33d46b90-fd3d-4586-9888-c103c2a62f4d")

    def test_cross_activity_launch_command(self):
        app = AppEntry(
            name="Firefox",
            command="firefox",
            desktop_file="org.mozilla.firefox.desktop",
            delay_seconds=1,
        )
        with patch("launcher.Path.home", return_value=self.fake_home), \
             patch.object(AppLauncher, "get_current_activity", return_value="027ddadb-5273-4710-aff6-ec33a2da7a60"), \
             patch.object(AppLauncher, "_get_qdbus_bin", return_value="qdbus-qt6"):
            args, use_shell = AppLauncher.get_launch_command(app)
        self.assertFalse(use_shell)
        cmd_str = args[2]
        self.assertIn('SetCurrentActivity "33d46b90-fd3d-4586-9888-c103c2a62f4d"', cmd_str)
        self.assertIn('SetCurrentActivity "027ddadb-5273-4710-aff6-ec33a2da7a60"', cmd_str)
        self.assertIn('gtk-launch "org.mozilla.firefox.desktop"', cmd_str)

    def test_kwin_rules_disabled_percent_title_and_maximize(self):
        zen = AppEntry(name="Zen Browser", command="zen", desktop_file="zen-browser.desktop")
        remmina = AppEntry(name="Remmina", command="remmina", desktop_file="org.remmina.Remmina.desktop")
        neg = AppEntry(name="LeftApp", command="leftapp")
        maxed = AppEntry(name="MaxApp", command="maxapp")

        with patch("launcher.Path.home", return_value=self.fake_home):
            # Skips disabled-rule and tolerates '%' in title
            self.assertEqual(AppLauncher.get_app_target_activity(zen), "027ddadb-5273-4710-aff6-ec33a2da7a60")
            self.assertEqual(
                AppLauncher.get_app_target_geometry(zen),
                {"position": (2305, 14), "size": (1787, 1359)},
            )
            # Prefers positive title match for Remmina over negative lookahead viewer rule
            self.assertEqual(
                AppLauncher.get_app_target_geometry(remmina),
                {"position": (2571, 641), "size": (1035, 535)},
            )
            # Parses negative X coordinates cleanly
            self.assertEqual(
                AppLauncher.get_app_target_geometry(neg),
                {"position": (-1920, 0), "size": (1280, 720)},
            )
            # Respects maximizevert=true without forcing unmaximized geometry
            self.assertIsNone(AppLauncher.get_app_target_geometry(maxed))

    def test_deferred_helpers_mocked(self):
        app = AppEntry(name="LeftApp", command="leftapp")
        with patch.object(AppLauncher, "_spawn_detached_script") as mock_spawn, \
             patch.object(AppLauncher, "_has_binary", return_value=True), \
             patch.object(AppLauncher, "_get_qdbus_bin", return_value="qdbus"):
            AppLauncher.minimize_window_deferred(app)
            AppLauncher.clear_attention_deferred(app)
            AppLauncher.enforce_geometry_deferred(app, (-1920, 0), (1280, 720))
            AppLauncher.restore_activity_deferred("027ddadb-5273-4710-aff6-ec33a2da7a60", duration_seconds=20.0)

        self.assertEqual(mock_spawn.call_count, 4)
        geom_script = mock_spawn.call_args_list[2][0][0]
        self.assertIn("-?[0-9]+", geom_script)
        self.assertIn("--remove MAXIMIZED_HORZ", geom_script)
        restore_script = mock_spawn.call_args_list[3][0][0]
        self.assertIn("seq 1 40", restore_script)
        self.assertIn("qdbus org.kde.ActivityManager", restore_script)

    def test_validate_app(self):
        valid_cmd = AppEntry(name="Python", command="python3 --version")
        self.assertIsNone(AppLauncher.validate_app(valid_cmd))

        missing_cmd = AppEntry(name="Broken", command="definitely_nonexistent_binary_xyz_123")
        self.assertIsNotNone(AppLauncher.validate_app(missing_cmd))

        missing_desktop = AppEntry(name="MissingDesktop", desktop_file=str(self.fake_home / "no_such.desktop"))
        self.assertIsNotNone(AppLauncher.validate_app(missing_desktop))


if __name__ == "__main__":
    unittest.main()
