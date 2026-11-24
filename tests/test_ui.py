"""Tests for MainWindow, dialogs, widgets, and autostart behavior."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QEvent, QPoint, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication

from autostart import AutostartManager
from config import AppEntry, ConfigManager
from desktop_scanner import DesktopAppInfo, DesktopScanner
from ui.app_dialog import AppDialog
from ui.main_window import AppCardWidget, MainWindow
from ui.settings_dialog import SettingsDialog

app = QApplication.instance()
if app is None:
    app = QApplication([])


class TestMainWindowShortcuts(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.config_dir = Path(self.test_dir) / "autolaunch"
        self.cfg = ConfigManager(self.config_dir)
        self.cfg.countdown_seconds = 10
        self.cfg.enable_countdown = True
        self.cfg.countdown_autostart_only = False
        self._prev_cached_apps = DesktopScanner._cached_apps
        DesktopScanner._cached_apps = []

    def tearDown(self):
        DesktopScanner._cached_apps = self._prev_cached_apps
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_space_bar_pauses_and_resumes_countdown(self):
        with patch.object(MainWindow, "_start_background_update_check"):
            win = MainWindow(self.cfg, autostart_mode=True)
        win.show()
        self.assertTrue(win.countdown_card.isVisible())
        self.assertFalse(win.is_paused)

        evt_press = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier)
        app.sendEvent(win, evt_press)
        self.assertTrue(win.is_paused)
        self.assertIn("Paused", win.countdown_status_label.text())

        app.sendEvent(win, evt_press)
        self.assertFalse(win.is_paused)
        self.assertIn("Auto-launching", win.countdown_status_label.text())
        win.close()

    def test_disable_countdown_flag_does_not_mutate_config(self):
        with patch.object(MainWindow, "_start_background_update_check"):
            win = MainWindow(self.cfg, autostart_mode=True, disable_countdown=True)
        self.assertFalse(win.countdown_card.isVisible())
        self.assertTrue(self.cfg.enable_countdown)
        win.on_app_toggled(AppEntry(name="X", command="true"))
        reloaded = ConfigManager(self.config_dir)
        self.assertTrue(reloaded.enable_countdown)
        win.close()

    def test_card_buttons_and_truncation(self):
        long_app = AppEntry(
            id="long-app",
            name="Super Extremely Long Application Title That Exceeds Normal Window Width",
            command="/usr/bin/test",
            delay_seconds=12,
            start_minimized=True,
        )
        self.cfg.profiles[self.cfg.active_profile].apps.append(long_app)

        with patch.object(MainWindow, "_start_background_update_check"):
            win = MainWindow(self.cfg, autostart_mode=False)
        win.show()
        app.processEvents()

        cards = win.findChildren(AppCardWidget)
        self.assertGreater(len(cards), 0)
        card = cards[-1]

        self.assertTrue(card.run_btn.isVisible())
        self.assertTrue(card.edit_btn.isVisible())
        self.assertTrue(card.delete_btn.isVisible())
        self.assertLess(card.edit_btn.geometry().right(), card.width())
        self.assertLess(card.delete_btn.geometry().right(), card.width())
        win.close()

    def test_window_default_widths_and_resizer(self):
        with patch.object(MainWindow, "_start_background_update_check"):
            win = MainWindow(self.cfg, autostart_mode=False)
        self.assertGreaterEqual(win.width(), 600)
        self.assertTrue(hasattr(win, "resize_handler"))

        handler = win.resize_handler
        self.assertIn("T", handler._get_edge(win, QPoint(300, 2)))
        self.assertIn("B", handler._get_edge(win, QPoint(300, win.height() - 2)))
        self.assertIn("L", handler._get_edge(win, QPoint(2, 400)))
        self.assertIn("R", handler._get_edge(win, QPoint(win.width() - 2, 400)))

        dlg = AppDialog(win)
        self.assertEqual(dlg.width(), win.width())
        self.assertEqual(dlg.height(), win.height())
        self.assertEqual(dlg.minimumSize(), win.minimumSize())
        self.assertTrue(hasattr(dlg, "resize_handler"))

        dlg_handler = dlg.resize_handler
        self.assertIn("T", dlg_handler._get_edge(dlg, QPoint(300, 2)))
        self.assertIn("B", dlg_handler._get_edge(dlg, QPoint(300, dlg.height() - 2)))
        self.assertIn("L", dlg_handler._get_edge(dlg, QPoint(2, 400)))
        self.assertIn("R", dlg_handler._get_edge(dlg, QPoint(dlg.width() - 2, 400)))

        dlg.close()
        self.assertFalse(dlg.resize_handler._filter_installed)
        win.close()

    def test_app_dialog_add_and_close_workflow(self):
        added_apps = []
        dlg = AppDialog(on_app_added=lambda entry: added_apps.append(entry))
        dlg.show()
        self.assertEqual(dlg.add_btn.text(), "+ Add Application to Profile")
        self.assertEqual(dlg.close_btn.text(), "Close")

        dlg.name_input.setText("App One")
        dlg.cmd_input.setText("app-one")
        dlg.add_btn.click()

        self.assertEqual(len(added_apps), 1)
        self.assertEqual(added_apps[0].name, "App One")
        self.assertTrue(dlg.feedback_label.isVisible())
        self.assertIn("1 added", dlg.feedback_label.text())

        dlg.name_input.setText("App Two")
        dlg.cmd_input.setText("app-two")
        dlg.add_btn.click()

        self.assertEqual(len(added_apps), 2)
        self.assertEqual(added_apps[1].name, "App Two")
        self.assertIn("2 added", dlg.feedback_label.text())

        dlg.close_btn.click()
        self.assertFalse(dlg.isVisible())

        existing = AppEntry(name="Existing App", command="existing-cmd")
        edit_dlg = AppDialog(app_entry=existing)
        self.assertEqual(edit_dlg.cancel_btn.text(), "Cancel")
        self.assertEqual(edit_dlg.save_btn.text(), "Save Changes")
        edit_dlg.close()

    def test_app_dialog_search_does_not_reset_inputs_and_edit_matches_tilde(self):
        fake_home = Path(self.test_dir) / "fake_user"
        fake_desktop_path = fake_home / ".local" / "share" / "applications" / "sample.desktop"
        fake_desktop = DesktopAppInfo(
            desktop_file=str(fake_desktop_path),
            desktop_id="sample.desktop",
            name="Sample App",
            generic_name="Utility",
            comment="Sample",
            exec_command="sample-bin %u",
            clean_command="sample-bin",
            icon_name="sample",
        )
        old_cache = DesktopScanner._cached_apps
        DesktopScanner._cached_apps = [fake_desktop]
        try:
            with patch.object(DesktopScanner, "scan_all", return_value=[fake_desktop]), \
                 patch.dict(os.environ, {"HOME": str(fake_home)}):
                dlg = AppDialog()
                dlg.search_input.setText("sample")
                self.assertFalse(dlg.is_custom_mode)
                self.assertEqual(dlg.name_input.text(), "Sample App")
                self.assertTrue(dlg.cmd_input.isReadOnly())

                # Search with no matches must not wipe inputs
                dlg.search_input.setText("zzzz_nonexistent")
                self.assertEqual(dlg.name_input.text(), "Sample App")

                # Override converts .desktop selection to editable custom mode
                dlg.search_input.setText("sample")
                dlg.override_cmd_btn.click()
                self.assertTrue(dlg.is_custom_mode)
                self.assertFalse(dlg.cmd_input.isReadOnly())
                dlg.close()

                # Edit mode matches collapsed ~/... desktop path & preserves custom name
                entry = AppEntry(
                    name="My Custom Sample Name",
                    command="sample-bin",
                    desktop_file="~/.local/share/applications/sample.desktop",
                    icon="sample",
                )
                edit_dlg = AppDialog(app_entry=entry)
                self.assertEqual(edit_dlg.name_input.text(), "My Custom Sample Name")
                self.assertIsNotNone(edit_dlg.selected_scanner_app)
                self.assertEqual(edit_dlg.preview_title_label.text(), "Sample App")
                edit_dlg.close()
        finally:
            DesktopScanner._cached_apps = old_cache

    def test_default_profile_ui_workflow(self):
        self.cfg.add_profile("Work")
        with patch.object(MainWindow, "_start_background_update_check"):
            win = MainWindow(self.cfg, autostart_mode=False)
        win.show()

        self.assertEqual(self.cfg.default_profile, "Default")
        self.assertEqual(win.set_default_btn.text(), "Default")
        self.assertFalse(win.set_default_btn.isEnabled())
        self.assertIn("Default (Default)", win.profile_combo.currentText())

        work_idx = win.profile_combo.findData("Work")
        self.assertGreaterEqual(work_idx, 0)
        win.profile_combo.setCurrentIndex(work_idx)

        self.assertEqual(self.cfg.active_profile, "Work")
        self.assertEqual(win.set_default_btn.text(), "Set Default")
        self.assertTrue(win.set_default_btn.isEnabled())

        win.set_default_btn.click()
        self.assertEqual(self.cfg.default_profile, "Work")
        self.assertEqual(win.set_default_btn.text(), "Default")
        self.assertFalse(win.set_default_btn.isEnabled())
        self.assertIn("Work (Default)", win.profile_combo.currentText())

        with patch("ui.settings_dialog.AutostartManager.set_autostart"):
            settings_dlg = SettingsDialog(self.cfg, win)
            self.assertEqual(settings_dlg.default_profile_combo.currentText(), "Work")
            def_idx = settings_dlg.default_profile_combo.findText("Default")
            settings_dlg.default_profile_combo.setCurrentIndex(def_idx)
            settings_dlg._on_save()
            self.assertEqual(self.cfg.default_profile, "Default")
            settings_dlg.close()
        win.close()

    def test_app_dialog_two_column_table(self):
        dlg = AppDialog()
        self.assertEqual(dlg.app_list_widget.columnCount(), 2)
        self.assertGreater(dlg.app_list_widget.count(), 0)
        item_0_0 = dlg.app_list_widget.item(0, 0)
        self.assertIsNotNone(item_0_0)
        assert item_0_0 is not None
        self.assertIn("Custom Command", item_0_0.text())
        dlg.close()

    def test_autostart_respects_hidden_and_disabled_flags(self):
        autostart_dir = Path(self.test_dir) / "xdg_config" / "autostart"
        autostart_dir.mkdir(parents=True, exist_ok=True)
        desktop_file = autostart_dir / "autolaunch.desktop"

        with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(Path(self.test_dir) / "xdg_config")}):
            self.assertFalse(AutostartManager.is_autostart_enabled())

            desktop_file.write_text("[Desktop Entry]\nType=Application\nX-GNOME-Autostart-enabled=true\n", encoding="utf-8")
            self.assertTrue(AutostartManager.is_autostart_enabled())

            desktop_file.write_text("[Desktop Entry]\nType=Application\nX-GNOME-Autostart-enabled=false\n", encoding="utf-8")
            self.assertFalse(AutostartManager.is_autostart_enabled())

            desktop_file.write_text("[Desktop Entry]\nType=Application\nHidden=true\n", encoding="utf-8")
            self.assertFalse(AutostartManager.is_autostart_enabled())
