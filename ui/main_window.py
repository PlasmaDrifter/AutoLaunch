"""Modern, portrait desktop interface for AutoLaunch.

Features a tall, narrow aspect ratio with compact cards, animated toggle switches,
badge pills, glowing progress indicators, and streamlined vertical layout.
Complies with zero-emoji guidelines and dynamic path resolution.
"""

import threading
from typing import Optional

from PyQt6.QtCore import QEvent, QObject, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QKeyEvent
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QLayout,
    QLineEdit, QListView, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QProgressBar, QPushButton, QSizePolicy, QTextEdit, QVBoxLayout, QWidget
)

from config import AppEntry, ConfigManager
from launcher import AppLauncher
from ui.app_dialog import AppDialog
from ui.frameless import DraggableHeader, FramelessResizeHandler, WindowControls
from ui.icon_utils import resolve_icon
from ui.profile_dialog import ProfileDialog
from ui.settings_dialog import SettingsDialog
from ui.themes import (
    Theme,
    build_card_stylesheet,
    build_default_btn_active_stylesheet,
    build_delete_btn_stylesheet,
    build_header_btn_stylesheet,
    build_micro_btn_stylesheet,
    build_micro_delete_btn_stylesheet,
    build_primary_btn_stylesheet,
    get_theme,
)
from ui.widgets import BadgePill, ElidedLabel, ToggleSwitch
from updater import APP_VERSION, check_github_update


class AppCardWidget(QFrame):
    """Compact elevated card representing a configured application."""

    def __init__(
        self,
        app: AppEntry,
        parent_window: "MainWindow",
        is_first: bool = False,
        is_last: bool = False,
    ):
        super().__init__()
        self.app = app
        self.parent_window = parent_window
        self._is_first = is_first
        self._is_last = is_last
        self._validation_warning = AppLauncher.validate_app(app)
        self._init_ui()

    def _init_ui(self) -> None:
        self.setObjectName("AppCard")

        card_layout = QHBoxLayout(self)
        card_layout.setContentsMargins(8, 4, 8, 4)
        card_layout.setSpacing(8)

        # 1. Compact Animated Toggle Switch
        self.toggle = ToggleSwitch(width=36, height=20)
        self.toggle.setChecked(self.app.enabled)
        self.toggle.toggled.connect(self._on_toggle)
        self.toggle.setToolTip("Enable or disable this application on startup")
        card_layout.addWidget(self.toggle)

        # 2. Compact Icon Box
        self.icon_box = QFrame()
        self.icon_box.setFixedSize(34, 34)
        icon_box_layout = QVBoxLayout(self.icon_box)
        icon_box_layout.setContentsMargins(0, 0, 0, 0)
        icon_box_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.icon_label = QLabel()
        self.icon_label.setFixedSize(24, 24)
        qicon = resolve_icon(self.app.icon, self.app.desktop_file)
        self.icon_label.setPixmap(qicon.pixmap(QSize(24, 24)))
        self.icon_label.setScaledContents(True)
        self.icon_label.setStyleSheet("background: transparent; border: none;")
        icon_box_layout.addWidget(self.icon_label)

        card_layout.addWidget(self.icon_box)

        # 3. Compact App Details
        details_layout = QVBoxLayout()
        details_layout.setSpacing(0)
        details_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        title_row = QHBoxLayout()
        title_row.setSpacing(6)

        self.name_label = ElidedLabel(self.app.name)
        title_font = QFont("Inter, Segoe UI, sans-serif", 12, QFont.Weight.DemiBold)
        self.name_label.setFont(title_font)
        if self.app.command or self.app.desktop_file:
            tip_target = self.app.desktop_file or self.app.command
            self.name_label.setToolTip(f"{self.app.name}\n{tip_target}")
        title_row.addWidget(self.name_label, stretch=1)

        self.delay_badge: Optional[BadgePill] = None
        if self.app.delay_seconds > 0:
            self.delay_badge = BadgePill(
                f"+{self.app.delay_seconds}s",
                bg_color="rgba(16, 185, 129, 0.12)",
                text_color="#10b981",
                border_color="rgba(16, 185, 129, 0.35)",
            )
            title_row.addWidget(self.delay_badge, stretch=0)

        self.min_badge: Optional[BadgePill] = None
        if self.app.start_minimized:
            self.min_badge = BadgePill(
                "Min",
                bg_color="rgba(59, 130, 246, 0.12)",
                text_color="#3b82f6",
                border_color="rgba(59, 130, 246, 0.35)",
            )
            title_row.addWidget(self.min_badge, stretch=0)

        self.warn_badge: Optional[BadgePill] = None
        if self._validation_warning:
            self.warn_badge = BadgePill(
                "Missing",
                bg_color="rgba(245, 158, 11, 0.14)",
                text_color="#fbbf24",
                border_color="rgba(245, 158, 11, 0.45)",
            )
            self.warn_badge.setToolTip(self._validation_warning)
            title_row.addWidget(self.warn_badge, stretch=0)

        title_row.addStretch(0)
        details_layout.addLayout(title_row)

        card_layout.addLayout(details_layout, stretch=1)

        # 4. Action Buttons
        action_layout = QHBoxLayout()
        action_layout.setSpacing(5)
        action_layout.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)

        self.up_btn = QPushButton("▲")
        self.up_btn.setFixedSize(28, 28)
        self.up_btn.setToolTip("Move up")
        self.up_btn.setEnabled(not self._is_first)
        self.up_btn.clicked.connect(self._on_move_up)
        action_layout.addWidget(self.up_btn)

        self.down_btn = QPushButton("▼")
        self.down_btn.setFixedSize(28, 28)
        self.down_btn.setToolTip("Move down")
        self.down_btn.setEnabled(not self._is_last)
        self.down_btn.clicked.connect(self._on_move_down)
        action_layout.addWidget(self.down_btn)

        self.run_btn = QPushButton("Run")
        self.run_btn.setFixedSize(38, 28)
        self.run_btn.setToolTip("Launch this application now (test)")
        self.run_btn.clicked.connect(self._on_run_single)
        action_layout.addWidget(self.run_btn)

        self.edit_btn = QPushButton("Edit")
        self.edit_btn.setFixedSize(42, 28)
        self.edit_btn.setToolTip("Edit application details")
        self.edit_btn.clicked.connect(self._on_edit)
        action_layout.addWidget(self.edit_btn)

        self.delete_btn = QPushButton("✕")
        self.delete_btn.setFixedSize(28, 28)
        self.delete_btn.setToolTip("Remove application")
        self.delete_btn.clicked.connect(self._on_delete)
        action_layout.addWidget(self.delete_btn)

        card_layout.addLayout(action_layout)

    def apply_theme(self, theme: Theme) -> None:
        self.setStyleSheet(build_card_stylesheet(theme))
        self.icon_box.setStyleSheet(f"""
            background-color: {theme.bg_surface};
            border: 1px solid {theme.border_subtle};
            border-radius: 8px;
        """)
        name_color = theme.text_primary if self.app.enabled else theme.text_muted
        self.name_label.setStyleSheet(f"color: {name_color}; background: transparent; border: none;")
        self.up_btn.setStyleSheet(build_micro_btn_stylesheet(theme))
        self.down_btn.setStyleSheet(build_micro_btn_stylesheet(theme))
        self.run_btn.setStyleSheet(build_micro_btn_stylesheet(theme))
        self.edit_btn.setStyleSheet(build_micro_btn_stylesheet(theme))
        self.delete_btn.setStyleSheet(build_micro_delete_btn_stylesheet(theme))
        self.toggle.set_track_colors(QColor("#19945C"), QColor(theme.border_subtle))
        if self.delay_badge:
            self.delay_badge.setStyleSheet("""
                QLabel {
                    background-color: rgba(16, 185, 129, 0.12);
                    color: #10b981;
                    border: 1px solid rgba(16, 185, 129, 0.35);
                    border-radius: 4px;
                    padding: 1px 6px;
                    font-size: 10px;
                    font-weight: bold;
                }
            """)
        if self.min_badge:
            self.min_badge.setStyleSheet("""
                QLabel {
                    background-color: rgba(59, 130, 246, 0.12);
                    color: #3b82f6;
                    border: 1px solid rgba(59, 130, 246, 0.35);
                    border-radius: 4px;
                    padding: 1px 6px;
                    font-size: 10px;
                    font-weight: bold;
                }
            """)
        if self.warn_badge:
            self.warn_badge.setStyleSheet("""
                QLabel {
                    background-color: rgba(245, 158, 11, 0.14);
                    color: #fbbf24;
                    border: 1px solid rgba(245, 158, 11, 0.45);
                    border-radius: 4px;
                    padding: 1px 6px;
                    font-size: 10px;
                    font-weight: bold;
                }
            """)

    def _on_toggle(self, checked: bool) -> None:
        self.app.enabled = checked
        theme = getattr(self.parent_window, "current_theme", get_theme("modern_minimalist"))
        name_color = theme.text_primary if checked else theme.text_muted
        self.name_label.setStyleSheet(f"color: {name_color}; background: transparent; border: none;")
        self.parent_window.on_app_toggled(self.app)

    def _on_move_up(self) -> None:
        self.parent_window.move_app(self.app, -1)

    def _on_move_down(self) -> None:
        self.parent_window.move_app(self.app, 1)

    def _on_run_single(self) -> None:
        self.parent_window.run_single_app(self.app)

    def _on_edit(self) -> None:
        self.parent_window.edit_app(self.app)

    def _on_delete(self) -> None:
        self.parent_window.delete_app(self.app)


class MainWindow(QMainWindow):
    """Main window for AutoLaunch with portrait, narrow orientation."""

    _update_check_done_signal = pyqtSignal(dict)

    def __init__(
        self,
        config_manager: ConfigManager,
        autostart_mode: bool = False,
        disable_countdown: bool = False,
    ):
        super().__init__()
        self.config_manager = config_manager
        self.autostart_mode = autostart_mode
        self.disable_countdown = disable_countdown
        self._last_update_result: Optional[dict] = None

        self._update_check_done_signal.connect(self._handle_background_update_result)

        self.remaining_seconds = self.config_manager.countdown_seconds
        self.is_paused = False
        self.current_theme = get_theme(self.config_manager.theme)

        self.setWindowTitle("AutoLaunch")
        self.setMinimumSize(480, 700)
        self.resize(self.config_manager.window_width, self.config_manager.window_height)
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window
        if self.autostart_mode:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._on_timer_tick)

        self._init_ui()
        self.apply_theme(self.config_manager.theme)
        self._load_profiles_combo()
        self._refresh_app_list()
        self._start_background_update_check()

        should_start_countdown = (
            not self.disable_countdown
            and self.config_manager.enable_countdown
            and self.config_manager.countdown_seconds > 0
            and (not self.config_manager.countdown_autostart_only or self.autostart_mode)
        )
        if should_start_countdown:
            self._start_countdown()
        else:
            self.countdown_card.hide()

        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def _init_ui(self) -> None:
        self.central_widget = QWidget()
        self.central_widget.setObjectName("CentralWidget")
        self.setCentralWidget(self.central_widget)

        self.resize_handler = FramelessResizeHandler(self)
        self.resize_handler.attach_to_widget(self.central_widget)

        main_layout = QVBoxLayout(self.central_widget)
        main_layout.setContentsMargins(14, 12, 14, 14)
        main_layout.setSpacing(10)

        # Top Header Bar
        self.header_frame = DraggableHeader()
        self.header_frame.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        header_layout = QVBoxLayout(self.header_frame)
        header_layout.setContentsMargins(10, 8, 10, 8)
        header_layout.setSpacing(8)

        # Row 1: Brand & Window Controls
        row1 = QHBoxLayout()
        row1.setSpacing(6)
        row1.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        title_box = QHBoxLayout()
        title_box.setSpacing(6)
        title_box.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        self.title_lbl = QLabel("AutoLaunch")
        title_font = QFont("Inter, Segoe UI, sans-serif", 16, QFont.Weight.Bold)
        self.title_lbl.setFont(title_font)

        self.version_lbl = QLabel(f"v{APP_VERSION}")
        version_font = QFont("Inter, Segoe UI, sans-serif", 10, QFont.Weight.Normal)
        self.version_lbl.setFont(version_font)

        title_box.addWidget(self.title_lbl)
        title_box.addWidget(self.version_lbl)

        self.update_badge = QPushButton("update available")
        update_font = QFont("Inter, Segoe UI, sans-serif", 9, QFont.Weight.Medium)
        self.update_badge.setFont(update_font)
        self.update_badge.setFlat(True)
        self.update_badge.setStyleSheet("""
            QPushButton {
                color: #4ade80;
                border: none;
                background: transparent;
                padding: 4px 2px 0px 2px;
                text-align: left;
            }
            QPushButton:hover {
                color: #86efac;
                text-decoration: underline;
            }
        """)
        self.update_badge.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_badge.setToolTip("Update available! Click to open Settings and update or dismiss.")
        self.update_badge.clicked.connect(self._on_open_settings)
        self.update_badge.hide()
        title_box.addWidget(self.update_badge)

        row1.addLayout(title_box)
        row1.addStretch()

        self.enabled_count_lbl = QLabel("")
        self.enabled_count_lbl.setFont(QFont("Inter, Segoe UI, sans-serif", 10, QFont.Weight.Medium))
        row1.addWidget(self.enabled_count_lbl, alignment=Qt.AlignmentFlag.AlignVCenter)

        self.window_controls = WindowControls(self, show_minimize=True)
        row1.addWidget(self.window_controls, alignment=Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
        header_layout.addLayout(row1)

        # Row 2: Profile Selector & Quick Actions
        row2 = QHBoxLayout()
        row2.setSpacing(6)

        self.prof_lbl = QLabel("Profile:")
        row2.addWidget(self.prof_lbl)

        self.profile_combo = QComboBox()
        self.profile_combo.setView(QListView())
        self.profile_combo.setFixedHeight(28)
        self.profile_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.profile_combo.currentIndexChanged.connect(self._on_profile_index_changed)
        row2.addWidget(self.profile_combo)

        self.set_default_btn = QPushButton("Default")
        self.set_default_btn.setFixedHeight(28)
        self.set_default_btn.clicked.connect(self._on_set_default_profile)
        row2.addWidget(self.set_default_btn)

        self.new_profile_btn = QPushButton("+ New")
        self.new_profile_btn.setFixedHeight(28)
        self.new_profile_btn.setToolTip("Create a new profile")
        self.new_profile_btn.clicked.connect(self._on_new_profile)
        row2.addWidget(self.new_profile_btn)

        self.rename_profile_btn = QPushButton("Rename")
        self.rename_profile_btn.setFixedHeight(28)
        self.rename_profile_btn.setToolTip("Rename active profile")
        self.rename_profile_btn.clicked.connect(self._on_rename_profile)
        row2.addWidget(self.rename_profile_btn)

        self.delete_profile_btn = QPushButton("Delete")
        self.delete_profile_btn.setFixedHeight(28)
        self.delete_profile_btn.setToolTip("Delete active profile")
        self.delete_profile_btn.clicked.connect(self._on_delete_profile)
        row2.addWidget(self.delete_profile_btn)

        self.settings_btn = QPushButton("Settings")
        self.settings_btn.setFixedHeight(28)
        self.settings_btn.clicked.connect(self._on_open_settings)
        row2.addWidget(self.settings_btn)

        header_layout.addLayout(row2)
        main_layout.addWidget(self.header_frame)

        # Center: Application Cards List
        self.app_list_widget = QListWidget()
        self.app_list_widget.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.app_list_widget.setSpacing(2)
        self.app_list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        main_layout.addWidget(self.app_list_widget, stretch=1)

        # Empty State Card
        self.empty_card = QFrame()
        empty_layout = QVBoxLayout(self.empty_card)
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.setSpacing(10)

        self.empty_title = QLabel("No applications configured")
        self.empty_title.setFont(QFont("Inter, Segoe UI, sans-serif", 12, QFont.Weight.Bold))
        self.empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.empty_desc = QLabel(
            "Add your startup applications, Zen Browser profiles, or custom scripts."
        )
        self.empty_desc.setWordWrap(True)
        self.empty_desc.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.add_first_btn = QPushButton("+ Add First Application")
        self.add_first_btn.setFixedSize(180, 34)
        self.add_first_btn.clicked.connect(self._on_add_app)

        empty_layout.addWidget(self.empty_title)
        empty_layout.addWidget(self.empty_desc)
        empty_layout.addWidget(self.add_first_btn)

        self.empty_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        main_layout.addWidget(self.empty_card, stretch=1)

        # Countdown Progress Card
        self.countdown_card = QFrame()
        self.countdown_card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        countdown_layout = QVBoxLayout(self.countdown_card)
        countdown_layout.setContentsMargins(12, 8, 12, 8)
        countdown_layout.setSpacing(6)

        countdown_header = QHBoxLayout()
        self.countdown_status_label = QLabel("Auto-launching in 5s...")
        self.countdown_status_label.setFont(QFont("Inter, Segoe UI, sans-serif", 10, QFont.Weight.Medium))
        countdown_header.addWidget(self.countdown_status_label)

        countdown_header.addStretch()

        self.pause_resume_btn = QPushButton("Pause")
        self.pause_resume_btn.setFixedSize(60, 24)
        self.pause_resume_btn.clicked.connect(self._toggle_pause_countdown)
        countdown_header.addWidget(self.pause_resume_btn)

        self.cancel_countdown_btn = QPushButton("Cancel")
        self.cancel_countdown_btn.setFixedSize(60, 24)
        self.cancel_countdown_btn.clicked.connect(self._cancel_countdown)
        countdown_header.addWidget(self.cancel_countdown_btn)

        countdown_layout.addLayout(countdown_header)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, max(1, self.config_manager.countdown_seconds))
        self.progress_bar.setValue(self.config_manager.countdown_seconds)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(6)
        countdown_layout.addWidget(self.progress_bar)

        main_layout.addWidget(self.countdown_card)

        # Bottom Action Bar
        bottom_layout = QHBoxLayout()
        bottom_layout.setSpacing(8)

        self.add_app_btn = QPushButton("+ Add Application")
        self.add_app_btn.setFixedHeight(36)
        self.add_app_btn.clicked.connect(self._on_add_app)
        bottom_layout.addWidget(self.add_app_btn, stretch=1)

        self.launch_now_btn = QPushButton("Launch Selected")
        self.launch_now_btn.setFixedHeight(36)
        self.launch_now_btn.clicked.connect(lambda: self.launch_selected_apps(manual=True))
        bottom_layout.addWidget(self.launch_now_btn, stretch=1)

        self.close_btn = QPushButton("Close")
        self.close_btn.setFixedSize(60, 36)
        self.close_btn.clicked.connect(self.close)
        bottom_layout.addWidget(self.close_btn)

        main_layout.addLayout(bottom_layout)

    def _start_countdown(self) -> None:
        self.remaining_seconds = self.config_manager.countdown_seconds
        self.is_paused = False
        self.progress_bar.setRange(0, max(1, self.config_manager.countdown_seconds))
        self.progress_bar.setValue(self.remaining_seconds)
        self.pause_resume_btn.setText("Pause")
        self.pause_resume_btn.setToolTip("Pause countdown (Space)")
        self._update_countdown_label()
        self.countdown_card.show()
        self.pause_resume_btn.setFocus()
        self.raise_()
        self.activateWindow()
        self.timer.start(1000)

    def _update_countdown_label(self) -> None:
        current_profile = self.config_manager.get_current_profile()
        enabled_count = sum(1 for a in current_profile.apps if a.enabled)
        theme = getattr(self, "current_theme", get_theme(self.config_manager.theme))
        base_color = theme.accent_hover
        light_red = "#f87171"
        self.countdown_status_label.setText(
            f'<span style="color: {base_color};">Auto-launching in {self.remaining_seconds}s </span>'
            f'<span style="color: {light_red};">[Space to pause]</span>'
            f'<span style="color: {base_color};"> ({enabled_count} apps enabled)</span>'
        )
        self.progress_bar.setValue(self.remaining_seconds)

    def _on_timer_tick(self) -> None:
        if self.is_paused:
            return

        self.remaining_seconds -= 1
        self._update_countdown_label()

        if self.remaining_seconds <= 0:
            self.timer.stop()
            self.launch_selected_apps(manual=False)

    def _toggle_pause_countdown(self) -> None:
        self.is_paused = not self.is_paused
        theme = getattr(self, "current_theme", get_theme(self.config_manager.theme))
        base_color = theme.accent_hover
        light_red = "#f87171"
        if self.is_paused:
            self.pause_resume_btn.setText("Resume")
            self.pause_resume_btn.setToolTip("Resume countdown (Space)")
            self.countdown_status_label.setText(
                f'<span style="color: {base_color};">Paused at {self.remaining_seconds}s </span>'
                f'<span style="color: {light_red};">[Space to resume]</span>'
            )
        else:
            self.pause_resume_btn.setText("Pause")
            self.pause_resume_btn.setToolTip("Pause countdown (Space)")
            self._update_countdown_label()

    def keyPressEvent(self, a0: Optional[QKeyEvent]) -> None:
        if a0 is not None and a0.key() == Qt.Key.Key_Space and self.countdown_card.isVisible():
            self._toggle_pause_countdown()
            a0.accept()
            return
        super().keyPressEvent(a0)

    def eventFilter(self, a0: Optional[QObject], a1: Optional[QEvent]) -> bool:
        if a0 is not None and a1 is not None:
            if a1.type() == QEvent.Type.KeyPress and isinstance(a1, QKeyEvent):
                if a1.key() == Qt.Key.Key_Space and self.countdown_card.isVisible():
                    if isinstance(a0, QWidget) and a0.window() == self:
                        if not isinstance(
                            a0, (QLineEdit, QTextEdit, QComboBox, QCheckBox, ToggleSwitch)
                        ) and (not isinstance(a0, QPushButton) or a0 is self.pause_resume_btn):
                            self._toggle_pause_countdown()
                            return True
        return super().eventFilter(a0, a1)

    def closeEvent(self, a0) -> None:
        self.timer.stop()
        w, h = self.width(), self.height()
        if (w, h) != (self.config_manager.window_width, self.config_manager.window_height):
            if w >= 480 and h >= 700:
                self.config_manager.window_width = w
                self.config_manager.window_height = h
                try:
                    self.config_manager.save()
                except Exception:
                    pass
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        super().closeEvent(a0)

    def _cancel_countdown(self) -> None:
        self.timer.stop()
        self.countdown_card.hide()

    def _pause_for_user_action(self) -> None:
        """Pauses the countdown when the user interacts with the UI."""
        if self.timer.isActive() and not self.is_paused:
            self._toggle_pause_countdown()

    def _load_profiles_combo(self) -> None:
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        target_idx = 0
        for idx, name in enumerate(sorted(self.config_manager.profiles.keys())):
            display_name = f"{name} (Default)" if name == self.config_manager.default_profile else name
            self.profile_combo.addItem(display_name, userData=name)
            if name == self.config_manager.active_profile:
                target_idx = idx
        self.profile_combo.setCurrentIndex(target_idx)
        self.profile_combo.blockSignals(False)
        self._update_profile_buttons()

    def _update_profile_buttons(self) -> None:
        has_multiple = len(self.config_manager.profiles) > 1
        self.delete_profile_btn.setEnabled(has_multiple)
        self._update_default_button()

    def _update_default_button(self) -> None:
        is_default = (self.config_manager.active_profile == self.config_manager.default_profile)
        theme = getattr(self, "current_theme", get_theme(self.config_manager.theme))
        if is_default:
            self.set_default_btn.setText("Default")
            self.set_default_btn.setStyleSheet(build_default_btn_active_stylesheet(theme))
            self.set_default_btn.setToolTip("Currently the default startup profile")
            self.set_default_btn.setEnabled(False)
        else:
            self.set_default_btn.setText("Set Default")
            self.set_default_btn.setStyleSheet(build_header_btn_stylesheet(theme))
            self.set_default_btn.setToolTip("Set active profile as default startup profile")
            self.set_default_btn.setEnabled(True)

    def _update_enabled_count_label(self) -> None:
        profile = self.config_manager.get_current_profile()
        total = len(profile.apps)
        enabled = sum(1 for a in profile.apps if a.enabled)
        self.enabled_count_lbl.setText(f"{enabled}/{total} enabled" if total else "")

    def apply_theme(self, theme_key: str) -> None:
        self.current_theme = get_theme(theme_key)
        theme = self.current_theme

        app = QApplication.instance()
        if isinstance(app, QApplication):
            app.setStyleSheet(theme.build_stylesheet())

        self.central_widget.setStyleSheet(f"""
            QWidget#CentralWidget {{
                background-color: {theme.bg_base};
            }}
        """)

        self.header_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {theme.bg_header};
                border: 1px solid {theme.border_subtle};
                border-radius: 10px;
                padding: 4px;
            }}
        """)

        self.title_lbl.setStyleSheet(f"color: {theme.text_primary}; border: none; background: transparent;")
        self.version_lbl.setStyleSheet(f"color: {theme.text_muted}; border: none; background: transparent; padding-top: 4px;")
        self.enabled_count_lbl.setStyleSheet(
            f"color: {theme.text_muted}; border: none; background: transparent; padding-right: 4px;"
        )
        self.prof_lbl.setStyleSheet(
            f"color: {theme.text_muted}; font-size: 11px; font-weight: bold; border: none; background: transparent;"
        )

        self.app_list_widget.setStyleSheet(f"""
            QListWidget {{
                border: 1px solid {theme.border_subtle};
                border-radius: 8px;
                background-color: {theme.bg_surface};
                padding: 2px;
            }}
            QListWidget::item {{
                border: none;
                background: transparent;
                padding: 0px;
                margin: 0px;
            }}
        """)

        self.empty_card.setStyleSheet(f"""
            QFrame {{
                border: 1px solid {theme.border_subtle};
                border-radius: 10px;
                background-color: {theme.bg_header};
                padding: 24px;
            }}
        """)
        self.empty_title.setStyleSheet(f"color: {theme.text_secondary}; border: none; background: transparent;")
        self.empty_desc.setStyleSheet(f"color: {theme.text_muted}; font-size: 11px; border: none; background: transparent;")
        self.add_first_btn.setStyleSheet(build_primary_btn_stylesheet(theme))

        self.countdown_card.setStyleSheet(f"""
            QFrame {{
                background-color: {theme.bg_header};
                border: 1px solid {theme.border_subtle};
                border-radius: 10px;
            }}
        """)
        self.countdown_status_label.setStyleSheet("border: none; background: transparent;")
        if self.countdown_card.isVisible():
            self._update_countdown_label()
        self.pause_resume_btn.setStyleSheet(build_header_btn_stylesheet(theme))
        self.cancel_countdown_btn.setStyleSheet(build_header_btn_stylesheet(theme))
        self.progress_bar.setStyleSheet(f"""
            QProgressBar {{
                border: none;
                border-radius: 3px;
                background-color: {theme.bg_surface};
            }}
            QProgressBar::chunk {{
                background: {theme.accent_primary};
                border-radius: 3px;
            }}
        """)

        self.add_app_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {theme.bg_card};
                color: {theme.accent_hover};
                border: 1px solid {theme.accent_border};
                border-radius: 6px;
                font-weight: bold;
                padding: 0 12px;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background-color: {theme.accent_subtle};
                border-color: {theme.accent_hover};
                color: #ffffff;
            }}
        """)

        self.launch_now_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {theme.bg_card};
                color: {theme.success};
                border: 1px solid {theme.success};
                border-radius: 6px;
                font-weight: bold;
                font-size: 12px;
                padding: 0 14px;
            }}
            QPushButton:hover {{
                background-color: rgba(74, 222, 128, 0.15);
                color: {theme.success_hover};
                border-color: {theme.success_hover};
            }}
            QPushButton:pressed {{
                background-color: {theme.bg_surface};
                border-color: {theme.success};
            }}
        """)

        self.close_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {theme.bg_card};
                color: {theme.text_secondary};
                border: 1px solid {theme.border_subtle};
                border-radius: 6px;
                font-size: 12px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background-color: {theme.accent_subtle};
                border-color: {theme.accent_hover};
                color: #ffffff;
            }}
            QPushButton:pressed {{
                background-color: {theme.bg_surface};
            }}
        """)

        self.new_profile_btn.setStyleSheet(build_header_btn_stylesheet(theme))
        self.rename_profile_btn.setStyleSheet(build_header_btn_stylesheet(theme))
        self.delete_profile_btn.setStyleSheet(build_delete_btn_stylesheet(theme))
        self.settings_btn.setStyleSheet(build_header_btn_stylesheet(theme))
        self._update_default_button()

        for i in range(self.app_list_widget.count()):
            item = self.app_list_widget.item(i)
            widget = self.app_list_widget.itemWidget(item)
            if isinstance(widget, AppCardWidget):
                widget.apply_theme(theme)

    def _on_profile_index_changed(self, index: int) -> None:
        if index < 0:
            return
        profile_name = self.profile_combo.itemData(index)
        if profile_name and profile_name in self.config_manager.profiles:
            self.config_manager.set_active_profile(profile_name)
            self._refresh_app_list()
            self._update_profile_buttons()

    def _on_profile_changed(self, profile_name: str) -> None:
        """Fallback compatibility handler if invoked by text name."""
        if not profile_name:
            return
        if profile_name.endswith(" (Default)") and profile_name not in self.config_manager.profiles:
            profile_name = profile_name[:-10]
        if profile_name in self.config_manager.profiles:
            self.config_manager.set_active_profile(profile_name)
            self._refresh_app_list()
            self._update_profile_buttons()

    def _on_set_default_profile(self) -> None:
        self._pause_for_user_action()
        active = self.config_manager.active_profile
        if self.config_manager.set_default_profile(active):
            self._load_profiles_combo()

    def _on_new_profile(self) -> None:
        self._pause_for_user_action()
        dialog = ProfileDialog(self, title="Create New Profile")
        if dialog.exec():
            new_name = dialog.get_profile_name()
            if self.config_manager.add_profile(new_name):
                self.config_manager.set_active_profile(new_name)
                self._load_profiles_combo()
                self._refresh_app_list()
            else:
                QMessageBox.warning(self, "Error", f"A profile named '{new_name}' already exists.")

    def _on_rename_profile(self) -> None:
        self._pause_for_user_action()
        current = self.config_manager.active_profile
        dialog = ProfileDialog(self, current_name=current, title="Rename Profile")
        if dialog.exec():
            new_name = dialog.get_profile_name()
            if self.config_manager.rename_profile(current, new_name):
                self._load_profiles_combo()
            else:
                QMessageBox.warning(self, "Error", f"Could not rename profile to '{new_name}'.")

    def _on_delete_profile(self) -> None:
        self._pause_for_user_action()
        current = self.config_manager.active_profile
        if len(self.config_manager.profiles) <= 1:
            QMessageBox.information(self, "Info", "Cannot delete the only remaining profile.")
            return

        reply = QMessageBox.question(
            self,
            "Confirm Delete",
            f"Are you sure you want to delete profile '{current}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.config_manager.remove_profile(current)
            self._load_profiles_combo()
            self._refresh_app_list()

    def _on_open_settings(self) -> None:
        self._pause_for_user_action()
        dialog = SettingsDialog(self.config_manager, self)
        dialog.dismiss_changed.connect(self._refresh_update_badge)
        dialog._update_check_done_signal.connect(self._handle_background_update_result)
        if dialog.exec():
            self._load_profiles_combo()
            if not self.config_manager.enable_countdown or self.config_manager.countdown_seconds <= 0:
                self._cancel_countdown()
            elif self.config_manager.countdown_autostart_only and not self.autostart_mode:
                self._cancel_countdown()
            else:
                self.remaining_seconds = self.config_manager.countdown_seconds
                self.progress_bar.setRange(0, max(1, self.config_manager.countdown_seconds))
                self.progress_bar.setValue(self.remaining_seconds)
                self.is_paused = True
                self.timer.stop()
                self.pause_resume_btn.setText("Resume")
                self.pause_resume_btn.setToolTip("Resume countdown (Space)")
                base_color = self.current_theme.accent_hover
                self.countdown_status_label.setText(
                    f'<span style="color: {base_color};">Timer reset to {self.remaining_seconds}s (Paused)</span>'
                )
                self.countdown_card.show()
        self._refresh_update_badge()

    def _start_background_update_check(self) -> None:
        def _worker() -> None:
            res = check_github_update(force=False)
            self._update_check_done_signal.emit(res)

        threading.Thread(target=_worker, daemon=True).start()

    def _handle_background_update_result(self, res: dict) -> None:
        self._last_update_result = res
        self._refresh_update_badge()

    def _refresh_update_badge(self) -> None:
        res = self._last_update_result
        if not res or not res.get("ok") or not res.get("has_update"):
            self.update_badge.hide()
            return

        latest = res.get("latest_version", "")
        if self.config_manager.dismissed_update_version == latest:
            self.update_badge.hide()
        else:
            self.update_badge.show()

    def _refresh_app_list(self) -> None:
        vbar = self.app_list_widget.verticalScrollBar()
        saved_scroll = vbar.value() if vbar is not None else 0

        for i in range(self.app_list_widget.count()):
            item = self.app_list_widget.item(i)
            widget = self.app_list_widget.itemWidget(item)
            if widget is not None:
                widget.deleteLater()
        self.app_list_widget.clear()
        profile = self.config_manager.get_current_profile()

        if not profile.apps:
            self.app_list_widget.hide()
            self.empty_card.show()
        else:
            self.empty_card.hide()
            self.app_list_widget.show()

            total = len(profile.apps)
            for idx, app in enumerate(profile.apps):
                item = QListWidgetItem(self.app_list_widget)
                card_widget = AppCardWidget(
                    app,
                    self,
                    is_first=(idx == 0),
                    is_last=(idx == total - 1),
                )
                card_widget.apply_theme(self.current_theme)
                item.setSizeHint(card_widget.sizeHint())
                self.app_list_widget.setItemWidget(item, card_widget)

            if vbar is not None:
                vbar.setValue(min(saved_scroll, vbar.maximum()))

        self._update_enabled_count_label()

    def on_app_toggled(self, app: AppEntry) -> None:
        self.config_manager.save()
        self._update_enabled_count_label()
        if self.timer.isActive():
            self._update_countdown_label()

    def move_app(self, app: AppEntry, direction: int) -> None:
        self._pause_for_user_action()
        profile = self.config_manager.get_current_profile()
        idx = -1
        for i, a in enumerate(profile.apps):
            if a.id == app.id:
                idx = i
                break

        if idx == -1:
            return

        new_idx = idx + direction
        if 0 <= new_idx < len(profile.apps):
            profile.apps[idx], profile.apps[new_idx] = profile.apps[new_idx], profile.apps[idx]
            self.config_manager.save()
            self._refresh_app_list()

    def run_single_app(self, app: AppEntry) -> None:
        """Test-launches a single application immediately without quitting AutoLaunch."""
        self._pause_for_user_action()
        ok = AppLauncher.launch(app, global_launch_minimized=self.config_manager.launch_minimized)
        if not ok:
            QMessageBox.warning(
                self,
                "Launch Failed",
                f"Could not launch '{app.name}'. Verify the command or .desktop entry exists.",
            )

    def _on_add_app(self) -> None:
        self._pause_for_user_action()

        def _handle_batch_added(app: AppEntry) -> None:
            profile_name = self.config_manager.active_profile
            self.config_manager.add_app_to_profile(profile_name, app)
            self._refresh_app_list()
            if self.timer.isActive():
                self._update_countdown_label()

        dialog = AppDialog(
            self,
            on_app_added=_handle_batch_added,
            theme_key=self.config_manager.theme,
        )
        dialog.exec()

    def edit_app(self, app: AppEntry) -> None:
        self._pause_for_user_action()
        dialog = AppDialog(self, app_entry=app, theme_key=self.config_manager.theme)
        if dialog.exec():
            updated_app = dialog.get_result()
            if updated_app:
                profile_name = self.config_manager.active_profile
                self.config_manager.update_app_in_profile(profile_name, updated_app)
                self._refresh_app_list()
                if self.timer.isActive():
                    self._update_countdown_label()

    def delete_app(self, app: AppEntry) -> None:
        self._pause_for_user_action()
        reply = QMessageBox.question(
            self,
            "Confirm Remove",
            f"Remove '{app.name}' from profile '{self.config_manager.active_profile}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            profile_name = self.config_manager.active_profile
            self.config_manager.remove_app_from_profile(profile_name, app.id)
            self._refresh_app_list()
            if self.timer.isActive():
                self._update_countdown_label()

    def launch_selected_apps(self, manual: bool = False) -> None:
        """Launches all enabled applications in the active profile and exits cleanly."""
        self.timer.stop()
        profile = self.config_manager.get_current_profile()
        enabled_apps = [a for a in profile.apps if a.enabled]

        if not enabled_apps:
            if manual:
                QMessageBox.information(
                    self,
                    "No Applications Enabled",
                    f"No applications are enabled in profile '{profile.name}'.",
                )
                return
            self.close()
            QApplication.quit()
            return

        launched_count = AppLauncher.launch_many(
            enabled_apps, global_launch_minimized=self.config_manager.launch_minimized
        )
        if manual and launched_count < len(enabled_apps):
            QMessageBox.warning(
                self,
                "Partial Launch",
                f"Started {launched_count} of {len(enabled_apps)} applications. "
                "Check ~/.local/state/autolaunch/autolaunch.log for details.",
            )
            return

        self.close()
        QApplication.quit()
