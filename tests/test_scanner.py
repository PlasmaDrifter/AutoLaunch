"""Tests for DesktopScanner and desktop file parsing."""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from desktop_scanner import DesktopScanner, clean_desktop_exec


class TestDesktopScanner(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.desktop_file = Path(self.test_dir) / "test-app.desktop"
        content = """[Desktop Entry]
Version=1.0
Type=Application
Name=Test Application
GenericName=Test Generic
Comment=A sample app
Exec=/usr/bin/test-app --arg %u %F
Icon=test-icon
StartupWMClass=testapp
X-KDE-Wayland-AppId=testapp
"""
        with open(self.desktop_file, "w", encoding="utf-8") as f:
            f.write(content)

    def tearDown(self):
        DesktopScanner._cached_apps = None
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_clean_exec(self):
        raw = "/usr/bin/zen --no-remote -P YT %u %F"
        self.assertEqual(clean_desktop_exec(raw), "/usr/bin/zen --no-remote -P YT")

        literal_pct = "/usr/bin/app --fmt 100%% %U"
        self.assertEqual(clean_desktop_exec(literal_pct), "/usr/bin/app --fmt 100%")

        flatpak_cmd = "/usr/bin/flatpak run --command=Telegram org.telegram.desktop -- @@u %U @@"
        self.assertEqual(
            clean_desktop_exec(flatpak_cmd),
            "/usr/bin/flatpak run --command=Telegram org.telegram.desktop",
        )

    def test_parse_desktop_file(self):
        info = DesktopScanner.parse_desktop_file(self.desktop_file)
        self.assertIsNotNone(info)
        assert info is not None
        self.assertEqual(info.name, "Test Application")
        self.assertEqual(info.generic_name, "Test Generic")
        self.assertEqual(info.clean_command, "/usr/bin/test-app --arg")
        self.assertEqual(info.icon_name, "test-icon")
        self.assertEqual(info.startup_wm_class, "testapp")
        self.assertEqual(info.kde_app_id, "testapp")

    def test_hidden_user_override_shadows_system_entry(self):
        user_dir = Path(self.test_dir) / "user_apps"
        sys_dir = Path(self.test_dir) / "sys_apps"
        user_dir.mkdir()
        sys_dir.mkdir()

        (user_dir / "shadowed.desktop").write_text(
            "[Desktop Entry]\nType=Application\nName=Shadowed\nExec=true\nHidden=true\n",
            encoding="utf-8",
        )
        (sys_dir / "shadowed.desktop").write_text(
            "[Desktop Entry]\nType=Application\nName=Shadowed\nExec=true\n",
            encoding="utf-8",
        )
        (sys_dir / "visible.desktop").write_text(
            "[Desktop Entry]\nType=Application\nName=Visible App\nExec=true\n",
            encoding="utf-8",
        )

        with patch.object(DesktopScanner, "get_search_directories", return_value=[user_dir, sys_dir]):
            apps = DesktopScanner.scan_all(force_refresh=True)
            ids = [a.desktop_id for a in apps]
            self.assertNotIn("shadowed.desktop", ids)
            self.assertIn("visible.desktop", ids)


if __name__ == "__main__":
    unittest.main()
