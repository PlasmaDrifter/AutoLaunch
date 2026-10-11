"""Tests for AppLauncher command generation."""

import unittest

from config import AppEntry
from launcher import AppLauncher


class TestAppLauncher(unittest.TestCase):

    def test_launch_command_desktop_entry(self):
        app = AppEntry(
            name="Zen Browser (YouTube)",
            desktop_file="zen-youtube.desktop",
            delay_seconds=0,
        )
        args, use_shell = AppLauncher.get_launch_command(app)
        self.assertFalse(use_shell)
        self.assertEqual(args[0], "/bin/sh")
        self.assertEqual(args[1], "-c")
        self.assertIn('gtk-launch "zen-youtube.desktop"', args[2])

    def test_launch_command_with_delay(self):
        app = AppEntry(
            name="Zen Browser (YouTube)",
            desktop_file="zen-youtube.desktop",
            delay_seconds=5,
        )
        args, use_shell = AppLauncher.get_launch_command(app)
        self.assertIn("sleep 5 && exec", args[2])

    def test_launch_custom_command(self):
        app = AppEntry(
            name="Custom Script",
            command="python3 my_script.py",
            desktop_file="",
            delay_seconds=0,
        )
        args, use_shell = AppLauncher.get_launch_command(app)
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
            desktop_file="~/.local/share/applications/org.mozilla.firefox.desktop",
        )
        target_act = AppLauncher.get_app_target_activity(app)
        # In user configuration, Firefox is pinned to Second activity (33d46b90-fd3d-4586-9888-c103c2a62f4d)
        self.assertEqual(target_act, "33d46b90-fd3d-4586-9888-c103c2a62f4d")

    def test_cross_activity_launch_command(self):
        app = AppEntry(
            name="Firefox",
            command="firefox",
            desktop_file="~/.local/share/applications/org.mozilla.firefox.desktop",
            delay_seconds=1,
        )
        # Mock get_current_activity to return Default activity
        original_get_curr = AppLauncher.get_current_activity
        try:
            AppLauncher.get_current_activity = staticmethod(lambda: "027ddadb-5273-4710-aff6-ec33a2da7a60")
            args, use_shell = AppLauncher.get_launch_command(app)
            self.assertFalse(use_shell)
            cmd_str = args[2]
            self.assertIn('SetCurrentActivity "33d46b90-fd3d-4586-9888-c103c2a62f4d"', cmd_str)
            self.assertIn('SetCurrentActivity "027ddadb-5273-4710-aff6-ec33a2da7a60"', cmd_str)
            self.assertIn('gtk-launch "org.mozilla.firefox.desktop"', cmd_str)
        finally:
            AppLauncher.get_current_activity = original_get_curr

    def test_clear_attention_deferred_does_not_crash(self):
        app = AppEntry(
            name="Firefox",
            command="firefox",
            desktop_file="~/.local/share/applications/org.mozilla.firefox.desktop",
        )
        # Verify method runs cleanly without unhandled exception
        try:
            AppLauncher.clear_attention_deferred(app)
        except Exception as e:
            self.fail(f"clear_attention_deferred raised an exception: {e}")


if __name__ == "__main__":
    unittest.main()


