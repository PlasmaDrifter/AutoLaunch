"""Dialog for adding or editing an application entry in AutoLaunch.

Provides a modern, high-efficiency two-pane picker:
- Top pane: Instant live search, async rescan, category chips, and 2-column app grid.
- Bottom pane: Application inspector and settings (preview icon, startup delay steppers,
  start minimized toggle, and optional custom command override).
- Supports batch "Add to Profile" without closing the dialog.
- Clean dedicated edit mode when modifying an existing application.
"""

import html
import threading
import uuid
from pathlib import Path
from typing import Callable, List, Optional

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QFileDialog, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget
)

from config import AppEntry
from desktop_scanner import DesktopAppInfo, DesktopScanner
from ui.frameless import FramelessDialogBase
from ui.icon_utils import resolve_icon
from ui.themes import CHECKMARK_ICON_PATH, build_primary_btn_stylesheet, get_theme
from ui.widgets import ElidedLabel, FlowLayout, StepperSpinBox


class TwoColumnAppTableWidget(QTableWidget):
    """Rigid two-column grid table for application picker with automatic text elision."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setColumnCount(2)
        h_header = self.horizontalHeader()
        if h_header is not None:
            h_header.setVisible(False)
            h_header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        v_header = self.verticalHeader()
        if v_header is not None:
            v_header.setVisible(False)
            v_header.setDefaultSectionSize(36)
        self.setShowGrid(False)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.setIconSize(QSize(26, 26))
        self.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.setStyleSheet("""
            QTableWidget {
                border: 1px solid #2f2f37;
                border-radius: 8px;
                background-color: #18181b;
                padding: 3px;
                outline: none;
            }
            QTableWidget::item {
                border: 1px solid transparent;
                border-radius: 5px;
                padding: 3px 8px;
                margin: 1px;
                color: #d4d4d8;
                background: transparent;
            }
            QTableWidget::item:hover {
                background-color: #28282e;
                border-color: #2f2f37;
                color: #ffffff;
            }
            QTableWidget::item:selected {
                background-color: rgba(226, 232, 240, 0.15);
                border-color: rgba(226, 232, 240, 0.45);
                color: #ffffff;
            }
        """)

    def count(self) -> int:
        """Returns total valid items in table."""
        total = 0
        for r in range(self.rowCount()):
            for c in range(2):
                item = self.item(r, c)
                if item is not None and item.data(Qt.ItemDataRole.UserRole) is not None:
                    total += 1
        return total


class AppDialog(FramelessDialogBase):
    """Modern two-pane application addition and editing dialog."""

    app_added = pyqtSignal(AppEntry)
    _scan_done_signal = pyqtSignal(int, list)

    CATEGORIES = [
        ("All", None),
        ("Internet", ["Network", "WebBrowser", "Email", "Chat", "Feed"]),
        ("Development", ["Development", "IDE", "Building", "Debugger"]),
        ("Utilities", ["Utility", "System", "Settings", "FileManager", "FileTools", "Archiving", "Compression"]),
        ("Media", ["AudioVideo", "Audio", "Video", "Player", "Recorder", "Graphics"]),
        ("Games", ["Game", "Emulator"]),
    ]

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        app_entry: Optional[AppEntry] = None,
        on_app_added: Optional[Callable[[AppEntry], None]] = None,
        theme_key: str = "modern_minimalist",
    ):
        self.is_edit_mode = app_entry is not None
        title = "Edit Application" if self.is_edit_mode else "Add Application"
        super().__init__(parent, title=title)
        self.existing_entry = app_entry
        self.on_app_added_callback = on_app_added
        self.theme_key = theme_key
        self.result_entry: Optional[AppEntry] = None

        self.selected_scanner_app: Optional[DesktopAppInfo] = None
        self.scanned_apps: List[DesktopAppInfo] = []
        self.current_category_filter: Optional[List[str]] = None
        self.is_custom_mode: bool = False
        self.added_count: int = 0
        self._scan_generation: int = 0

        self._scan_done_signal.connect(self._on_scan_finished)

        self.setMinimumSize(480, 700)
        self.resize(600, 780)

        self._init_ui()
        self._load_scanned_apps(force_refresh=False)

        if self.is_edit_mode and self.existing_entry:
            self._populate_edit_mode()
        elif self.app_list_widget.count() > 0:
            self.app_list_widget.setCurrentCell(0, 0)

    def _init_ui(self) -> None:
        theme = get_theme(self.theme_key)
        self.setStyleSheet(f"background-color: {theme.bg_base};")
        main_layout = self.content_layout
        main_layout.setSpacing(10)

        # ----------------- TOP AREA: Search, Categories, Full-Width App List -----------------
        search_box = QHBoxLayout()
        search_box.setSpacing(6)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search applications by name, generic name, or command...")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self._on_search_text_changed)
        self.search_input.setFixedHeight(34)
        self.search_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {theme.bg_surface};
                color: #ffffff;
                border: 1px solid {theme.border_subtle};
                border-radius: 6px;
                padding: 6px 12px;
                font-size: 13px;
            }}
            QLineEdit:focus {{
                border-color: {theme.accent_hover};
                background-color: {theme.bg_card};
            }}
        """)
        search_box.addWidget(self.search_input, stretch=1)

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setFixedHeight(34)
        self.refresh_btn.setToolTip("Rescan installed .desktop applications")
        self.refresh_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {theme.bg_card};
                color: {theme.text_secondary};
                border: 1px solid {theme.border_subtle};
                border-radius: 6px;
                padding: 0 12px;
                font-size: 12px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background-color: {theme.card_hover};
                border-color: {theme.border_focus};
                color: #ffffff;
            }}
            QPushButton:disabled {{
                color: {theme.text_muted};
            }}
        """)
        self.refresh_btn.clicked.connect(lambda: self._load_scanned_apps(force_refresh=True))
        search_box.addWidget(self.refresh_btn)
        main_layout.addLayout(search_box)

        # Category Chips
        category_container = QWidget()
        category_layout = FlowLayout(category_container, margin=0, spacing=6)

        self.category_group = QButtonGroup(self)
        self.category_group.setExclusive(True)

        chip_style = f"""
            QPushButton {{
                background-color: {theme.bg_card};
                color: {theme.text_muted};
                border: 1px solid {theme.border_subtle};
                border-radius: 13px;
                padding: 4px 11px;
                font-size: 11px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background-color: {theme.card_hover};
                color: #ffffff;
                border-color: {theme.border_focus};
            }}
            QPushButton:checked {{
                background-color: {theme.accent_subtle};
                border-color: {theme.accent_border};
                color: #ffffff;
                font-weight: bold;
            }}
        """

        for idx, (label, filter_val) in enumerate(self.CATEGORIES):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setStyleSheet(chip_style)
            if idx == 0:
                btn.setChecked(True)
            btn.clicked.connect(lambda _checked, val=filter_val: self._on_category_clicked(val))
            self.category_group.addButton(btn, idx)
            category_layout.addWidget(btn)

        main_layout.addWidget(category_container)

        # 2-Column Application Table Widget
        self.app_list_widget = TwoColumnAppTableWidget()
        self.app_list_widget.itemSelectionChanged.connect(self._on_app_selected)
        self.app_list_widget.itemDoubleClicked.connect(self._on_app_double_clicked)
        main_layout.addWidget(self.app_list_widget, stretch=1)

        # Summary label
        self.list_summary_label = QLabel("Loading applications...")
        self.list_summary_label.setStyleSheet(f"color: {theme.text_muted}; font-size: 11px;")
        main_layout.addWidget(self.list_summary_label)

        # ----------------- BOTTOM AREA: Inspector & Configuration -----------------
        self.inspector_frame = QFrame()
        self.inspector_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {theme.bg_header};
                border: 1px solid {theme.border_subtle};
                border-radius: 10px;
            }}
        """)
        inspector_layout = QVBoxLayout(self.inspector_frame)
        inspector_layout.setContentsMargins(14, 10, 14, 10)
        inspector_layout.setSpacing(8)

        # Selected App Preview Header
        header_card = QHBoxLayout()
        header_card.setSpacing(10)
        header_card.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        self.preview_icon_label = QLabel()
        self.preview_icon_label.setFixedSize(40, 40)
        self.preview_icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_icon_label.setStyleSheet(f"""
            QLabel {{
                background-color: {theme.bg_base};
                border: 1px solid {theme.border_subtle};
                border-radius: 8px;
            }}
        """)
        header_card.addWidget(self.preview_icon_label)

        header_text_layout = QVBoxLayout()
        header_text_layout.setSpacing(2)
        self.preview_title_label = ElidedLabel("Select an Application")
        self.preview_title_label.setFont(QFont("Inter, Segoe UI, sans-serif", 12, QFont.Weight.Bold))
        self.preview_title_label.setStyleSheet("color: #ffffff; border: none; background: transparent;")
        header_text_layout.addWidget(self.preview_title_label)

        self.preview_desc_label = ElidedLabel("Pick from installed applications or create a custom command.")
        self.preview_desc_label.setStyleSheet(f"color: {theme.text_muted}; font-size: 11px; border: none; background: transparent;")
        header_text_layout.addWidget(self.preview_desc_label)

        header_card.addLayout(header_text_layout, stretch=1)

        self.feedback_label = QLabel()
        self.feedback_label.setStyleSheet(
            "color: #34d399; font-size: 11px; font-weight: 600; border: none; background: transparent; padding-right: 4px;"
        )
        self.feedback_label.hide()
        header_card.addWidget(self.feedback_label)

        inspector_layout.addLayout(header_card)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet(f"background-color: {theme.border_subtle}; max-height: 1px; border: none;")
        inspector_layout.addWidget(divider)

        # Row 1: Display Name + Startup Delay + Checkboxes
        row1_layout = QHBoxLayout()
        row1_layout.setSpacing(10)
        row1_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        name_lbl = QLabel("Display Name:")
        name_lbl.setStyleSheet(f"color: {theme.text_primary}; font-size: 12px; font-weight: 500; border: none; background: transparent;")
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Display name")
        self.name_input.setFixedHeight(30)
        self.name_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {theme.bg_surface};
                color: #ffffff;
                border: 1px solid {theme.border_subtle};
                border-radius: 6px;
                padding: 4px 10px;
                font-size: 12px;
            }}
            QLineEdit:focus {{
                border-color: {theme.accent_hover};
                background-color: {theme.bg_card};
            }}
        """)
        row1_layout.addWidget(name_lbl)
        row1_layout.addWidget(self.name_input, stretch=3)

        delay_lbl = QLabel("Delay:")
        delay_lbl.setStyleSheet(f"color: {theme.text_primary}; font-size: 12px; font-weight: 500; border: none; background: transparent;")
        self.delay_spin = StepperSpinBox(minimum=0, maximum=300, value=0, suffix="s", step=1, compact=True)
        row1_layout.addWidget(delay_lbl)
        row1_layout.addWidget(self.delay_spin)

        chk_style = f"""
            QCheckBox {{
                color: {theme.text_primary};
                font-size: 12px;
                font-weight: 500;
                spacing: 7px;
                background: transparent;
                border: none;
            }}
            QCheckBox::indicator {{
                width: 18px;
                height: 18px;
                border-radius: 4px;
                border: 2px solid #64748b;
                background-color: {theme.bg_surface};
            }}
            QCheckBox::indicator:hover {{
                border: 2px solid {theme.accent_hover};
                background-color: {theme.bg_card};
            }}
            QCheckBox::indicator:checked {{
                border: 2px solid #d4d4d8;
                background-color: #334155;
                image: url("{CHECKMARK_ICON_PATH}");
            }}
            QCheckBox::indicator:checked:hover {{
                border: 2px solid #d4d4d8;
                background-color: #475569;
                image: url("{CHECKMARK_ICON_PATH}");
            }}
        """

        self.minimized_check = QCheckBox("Start min")
        self.minimized_check.setChecked(False)
        self.minimized_check.setStyleSheet(chk_style)
        row1_layout.addWidget(self.minimized_check)

        self.enabled_check = QCheckBox("Enabled")
        self.enabled_check.setChecked(True)
        self.enabled_check.setStyleSheet(chk_style)
        row1_layout.addWidget(self.enabled_check)

        inspector_layout.addLayout(row1_layout)

        # Row 2: Full-Width Command / Exec + Optional Custom Override Button
        row2_layout = QHBoxLayout()
        row2_layout.setSpacing(8)
        row2_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        cmd_lbl = QLabel("Command / Exec:")
        cmd_lbl.setStyleSheet(f"color: {theme.text_primary}; font-size: 12px; font-weight: 500; border: none; background: transparent;")
        self.cmd_input = QLineEdit()
        self.cmd_input.setPlaceholderText("Command to execute")
        self.cmd_input.setFixedHeight(32)
        self.cmd_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {theme.bg_surface};
                color: #ffffff;
                border: 1px solid {theme.border_subtle};
                border-radius: 6px;
                padding: 4px 10px;
                font-size: 12px;
            }}
            QLineEdit:focus {{
                border-color: {theme.accent_hover};
                background-color: {theme.bg_card};
            }}
            QLineEdit:read-only {{
                color: #94a3b8;
                background-color: {theme.bg_base};
            }}
        """)
        self.override_cmd_btn = QPushButton("Override")
        self.override_cmd_btn.setFixedHeight(32)
        self.override_cmd_btn.setToolTip(
            "Desktop apps launch via gtk-launch/gio to preserve icons. "
            "Click Override to convert to a custom editable command."
        )
        self.override_cmd_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {theme.bg_card};
                color: {theme.text_secondary};
                border: 1px solid {theme.border_subtle};
                border-radius: 6px;
                padding: 0 10px;
                font-size: 11px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background-color: {theme.card_hover};
                color: #ffffff;
                border-color: {theme.border_focus};
            }}
        """)
        self.override_cmd_btn.clicked.connect(self._on_override_command_clicked)
        self.override_cmd_btn.hide()

        row2_layout.addWidget(cmd_lbl)
        row2_layout.addWidget(self.cmd_input, stretch=1)
        row2_layout.addWidget(self.override_cmd_btn)
        inspector_layout.addLayout(row2_layout)

        # Row 3: Custom Icon Row (visible for custom commands)
        self.icon_row_widget = QWidget()
        self.icon_row_widget.setStyleSheet("border: none; background: transparent;")
        icon_row_layout = QHBoxLayout(self.icon_row_widget)
        icon_row_layout.setContentsMargins(0, 0, 0, 0)
        icon_row_layout.setSpacing(10)
        icon_lbl = QLabel("Custom Icon:")
        icon_lbl.setStyleSheet(f"color: {theme.text_primary}; font-size: 12px; font-weight: 500; border: none; background: transparent;")
        self.custom_icon_input = QLineEdit()
        self.custom_icon_input.setPlaceholderText("Icon name or image path (.png, .svg)")
        self.custom_icon_input.setFixedHeight(30)
        self.custom_icon_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {theme.bg_surface};
                color: #ffffff;
                border: 1px solid {theme.border_subtle};
                border-radius: 6px;
                padding: 4px 10px;
                font-size: 12px;
            }}
            QLineEdit:focus {{
                border-color: {theme.accent_hover};
                background-color: {theme.bg_card};
            }}
        """)
        self.custom_icon_input.textChanged.connect(self._on_custom_icon_text_changed)
        browse_icon_btn = QPushButton("Browse...")
        browse_icon_btn.setFixedHeight(30)
        browse_icon_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {theme.bg_card};
                color: {theme.text_primary};
                border: 1px solid {theme.border_subtle};
                border-radius: 6px;
                padding: 0 10px;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background-color: {theme.card_hover};
                color: #ffffff;
                border-color: {theme.border_focus};
            }}
        """)
        browse_icon_btn.clicked.connect(self._browse_custom_icon)
        icon_row_layout.addWidget(icon_lbl)
        icon_row_layout.addWidget(self.custom_icon_input, stretch=1)
        icon_row_layout.addWidget(browse_icon_btn)
        inspector_layout.addWidget(self.icon_row_widget)
        self.icon_row_widget.hide()

        main_layout.addWidget(self.inspector_frame)

        # ----------------- BOTTOM DIALOG ACTIONS -----------------
        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(0, 4, 0, 0)
        btn_layout.setSpacing(10)

        if self.is_edit_mode:
            self.save_btn = QPushButton("Save Changes")
            self.save_btn.setFixedHeight(36)
            self.save_btn.setDefault(True)
            self.save_btn.clicked.connect(self._on_save_and_close)
            self.save_btn.setStyleSheet(build_primary_btn_stylesheet(theme))
            btn_layout.addWidget(self.save_btn, stretch=1)

            self.cancel_btn = QPushButton("Cancel")
            self.cancel_btn.setFixedHeight(36)
            self.cancel_btn.setMinimumWidth(100)
            self.cancel_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {theme.bg_card};
                    color: {theme.text_primary};
                    border: 1px solid {theme.border_subtle};
                    border-radius: 6px;
                    font-size: 13px;
                }}
                QPushButton:hover {{
                    background-color: {theme.card_hover};
                    color: {theme.text_primary};
                }}
            """)
            self.cancel_btn.clicked.connect(self.reject)
            btn_layout.addWidget(self.cancel_btn, stretch=0)
        else:
            self.add_btn = QPushButton("+ Add Application to Profile")
            self.add_btn.setFixedHeight(36)
            self.add_btn.setDefault(True)
            self.add_btn.clicked.connect(self._on_add_clicked)
            self.add_btn.setStyleSheet(build_primary_btn_stylesheet(theme))
            btn_layout.addWidget(self.add_btn, stretch=1)

            self.close_btn = QPushButton("Close")
            self.close_btn.setFixedHeight(36)
            self.close_btn.setMinimumWidth(100)
            self.close_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {theme.bg_card};
                    color: {theme.text_primary};
                    border: 1px solid {theme.border_subtle};
                    border-radius: 6px;
                    font-size: 13px;
                }}
                QPushButton:hover {{
                    background-color: {theme.card_hover};
                    color: {theme.text_primary};
                }}
            """)
            self.close_btn.clicked.connect(self._on_close_clicked)
            btn_layout.addWidget(self.close_btn, stretch=0)

            self.cancel_btn = self.close_btn
            self.save_btn = self.add_btn

        main_layout.addLayout(btn_layout)

    def _load_scanned_apps(self, force_refresh: bool = False) -> None:
        if not force_refresh and DesktopScanner._cached_apps is not None:
            self.scanned_apps = DesktopScanner._cached_apps
            self._populate_list(self.scanned_apps, select_first_match=False)
            return

        # Populate CUSTOM immediately so dialog is instantly usable while scanning runs off-thread
        self._scan_generation += 1
        generation = self._scan_generation
        self.refresh_btn.setEnabled(False)
        self.refresh_btn.setText("Scanning...")
        self.list_summary_label.setText("Scanning installed applications...")
        self._populate_list([], select_first_match=False)

        def _worker() -> None:
            apps = DesktopScanner.scan_all(force_refresh=force_refresh)
            self._scan_done_signal.emit(generation, apps)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_scan_finished(self, generation: int, apps: list) -> None:
        if generation != self._scan_generation:
            return
        self.refresh_btn.setEnabled(True)
        self.refresh_btn.setText("Refresh")
        self.scanned_apps = apps
        self._filter_list(preserve_inputs=True)
        if self.is_edit_mode and self.existing_entry and self.existing_entry.desktop_file:
            self._select_matching_desktop_item(self.existing_entry.desktop_file, restore_entry_fields=True)

    def _populate_list(
        self,
        apps: List[DesktopAppInfo],
        select_first_match: bool = False,
        preserve_inputs: bool = False,
    ) -> None:
        self.app_list_widget.blockSignals(True)
        self.app_list_widget.clearSelection()
        self.app_list_widget.clear()
        total_items = 1 + len(apps)
        rows = (total_items + 1) // 2
        self.app_list_widget.setRowCount(rows)

        custom_item = QTableWidgetItem("+ Custom Command / Shell Script")
        custom_item.setIcon(resolve_icon("utilities-terminal", ""))
        custom_item.setToolTip("Create a custom command or shell script entry")
        custom_item.setData(Qt.ItemDataRole.UserRole, "CUSTOM")
        custom_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        self.app_list_widget.setItem(0, 0, custom_item)

        for idx, app in enumerate(apps):
            pos = idx + 1
            r = pos // 2
            c = pos % 2

            item = QTableWidgetItem(app.name.strip())
            icon = resolve_icon(app.icon_name, app.icon_path)
            item.setIcon(icon)

            escaped_name = html.escape(app.name.strip())
            tooltip_html = [
                '<div style="font-family: sans-serif; line-height: 1.4;">',
                f'  <div style="font-size: 14px; font-weight: bold; color: #f8fafc; margin-bottom: 4px;">{escaped_name}</div>',
            ]
            if app.generic_name:
                escaped_cat = html.escape(app.generic_name.strip())
                tooltip_html.append(
                    f'  <div style="font-size: 13px; color: #7bb2db;">Category: <span style="color: #f8fafc;">{escaped_cat}</span></div>'
                )
            if app.comment:
                escaped_desc = html.escape(app.comment.strip())
                tooltip_html.append(
                    f'  <div style="font-size: 13px; color: #7bb2db;">Description: <span style="color: #f8fafc;">{escaped_desc}</span></div>'
                )
            if app.clean_command:
                escaped_cmd = html.escape(app.clean_command.strip())
                tooltip_html.append(
                    f'  <div style="font-size: 13px; color: #7bb2db;">Command: <span style="color: #6ee7b7;">{escaped_cmd}</span></div>'
                )
            tooltip_html.append("</div>")

            item.setToolTip("\n".join(tooltip_html))
            item.setData(Qt.ItemDataRole.UserRole, app)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.app_list_widget.setItem(r, c, item)

        count = len(apps)
        self.list_summary_label.setText(f"{count} application{'s' if count != 1 else ''} available")

        vbar = self.app_list_widget.verticalScrollBar()
        if vbar is not None:
            vbar.setValue(0)
        hbar = self.app_list_widget.horizontalScrollBar()
        if hbar is not None:
            hbar.setValue(0)

        if self.app_list_widget.rowCount() > 0:
            top_item = self.app_list_widget.item(0, 0)
            if top_item is not None:
                self.app_list_widget.scrollToItem(top_item, QAbstractItemView.ScrollHint.PositionAtTop)

        self.app_list_widget.blockSignals(False)

        if preserve_inputs or self.is_edit_mode:
            return

        if select_first_match:
            if apps:
                self.app_list_widget.setCurrentCell(0, 1)
        elif self.app_list_widget.rowCount() > 0:
            self.app_list_widget.setCurrentCell(0, 0)

    def _on_category_clicked(self, category_filter) -> None:
        self.current_category_filter = category_filter
        self._filter_list(preserve_inputs=self.is_edit_mode)

    def _on_search_text_changed(self, _query: str) -> None:
        self._filter_list(preserve_inputs=self.is_edit_mode)

    def _filter_list(self, preserve_inputs: bool = False) -> None:
        q = self.search_input.text().strip().lower()

        filtered: List[DesktopAppInfo] = []
        for app in self.scanned_apps:
            if q and q not in app.search_text():
                continue

            if self.current_category_filter:
                cats = app.categories or []
                if not any(c in self.current_category_filter for c in cats):
                    continue

            filtered.append(app)

        has_filter = bool(q or self.current_category_filter)
        self._populate_list(
            filtered,
            select_first_match=has_filter,
            preserve_inputs=preserve_inputs,
        )

    def _apply_scanner_app_to_inspector(
        self, app_info: DesktopAppInfo, populate_inputs: bool = True
    ) -> None:
        self.is_custom_mode = False
        self.selected_scanner_app = app_info
        self.icon_row_widget.hide()
        self.cmd_input.setReadOnly(True)
        self.override_cmd_btn.show()

        icon = resolve_icon(app_info.icon_name, app_info.icon_path)
        self.preview_icon_label.setPixmap(icon.pixmap(48, 48))
        self.preview_title_label.setText(app_info.name)
        desc = app_info.generic_name or app_info.comment or Path(app_info.desktop_file).name
        self.preview_desc_label.setText(desc)

        if populate_inputs:
            self.name_input.setText(app_info.name)
            self.cmd_input.setText(app_info.clean_command)

    def _on_app_selected(self) -> None:
        selected_items = self.app_list_widget.selectedItems()
        if not selected_items:
            return

        data = selected_items[0].data(Qt.ItemDataRole.UserRole)
        if not data:
            return

        if data == "CUSTOM":
            self._set_custom_mode(clear_inputs=not self.is_edit_mode)
            return

        self._apply_scanner_app_to_inspector(data, populate_inputs=True)

    def _set_custom_mode(self, clear_inputs: bool = True) -> None:
        self.is_custom_mode = True
        self.selected_scanner_app = None
        self.icon_row_widget.show()
        self.cmd_input.setReadOnly(False)
        self.override_cmd_btn.hide()

        icon_name = self.custom_icon_input.text().strip() or "utilities-terminal"
        icon = resolve_icon(icon_name, icon_name)
        self.preview_icon_label.setPixmap(icon.pixmap(48, 48))
        self.preview_title_label.setText("Custom Command")
        self.preview_desc_label.setText("Run any system executable, Python script, or terminal tool.")

        if clear_inputs:
            self.name_input.clear()
            self.cmd_input.clear()
            self.custom_icon_input.clear()
            term_icon = resolve_icon("utilities-terminal", "")
            self.preview_icon_label.setPixmap(term_icon.pixmap(48, 48))

    def _on_override_command_clicked(self) -> None:
        """Converts the selected .desktop entry into an editable custom command preserving its fields."""
        current_icon = ""
        if self.selected_scanner_app:
            current_icon = self.selected_scanner_app.icon_name or self.selected_scanner_app.icon_path or ""
        elif self.existing_entry:
            current_icon = self.existing_entry.icon

        self.is_custom_mode = True
        self.selected_scanner_app = None
        self.icon_row_widget.show()
        self.cmd_input.setReadOnly(False)
        self.override_cmd_btn.hide()
        if current_icon and not self.custom_icon_input.text().strip():
            self.custom_icon_input.setText(current_icon)
        self.preview_desc_label.setText("Custom command override (bypasses .desktop launcher).")
        self.cmd_input.setFocus()

    def _browse_custom_icon(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Icon File",
            str(Path.home()),
            "Images (*.png *.svg *.xpm *.jpg *.jpeg);;All Files (*)"
        )
        if file_path:
            self.custom_icon_input.setText(file_path)

    def _on_custom_icon_text_changed(self, text: str) -> None:
        if self.is_custom_mode:
            icon = resolve_icon(text.strip(), text.strip())
            self.preview_icon_label.setPixmap(icon.pixmap(48, 48))

    def _select_matching_desktop_item(
        self, desktop_target: str, restore_entry_fields: bool = False
    ) -> bool:
        target_expanded = str(Path(desktop_target).expanduser())
        target_name = Path(desktop_target).expanduser().name

        for r in range(self.app_list_widget.rowCount()):
            for c in range(2):
                item = self.app_list_widget.item(r, c)
                if not item:
                    continue
                data = item.data(Qt.ItemDataRole.UserRole)
                if not isinstance(data, DesktopAppInfo):
                    continue
                if (
                    data.desktop_file == desktop_target
                    or data.desktop_file == target_expanded
                    or data.desktop_id == desktop_target
                    or data.desktop_id == target_name
                ):
                    self.app_list_widget.blockSignals(True)
                    self.app_list_widget.setCurrentItem(item)
                    self.app_list_widget.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtCenter)
                    self.app_list_widget.blockSignals(False)
                    self._apply_scanner_app_to_inspector(
                        data, populate_inputs=not restore_entry_fields
                    )
                    return True
        return False

    def _populate_edit_mode(self) -> None:
        entry = self.existing_entry
        if not entry:
            return

        self.name_input.setText(entry.name)
        self.cmd_input.setText(entry.command)
        self.delay_spin.setValue(entry.delay_seconds)
        self.minimized_check.setChecked(entry.start_minimized)
        self.enabled_check.setChecked(entry.enabled)

        if entry.desktop_file:
            self.is_custom_mode = False
            self.icon_row_widget.hide()
            self.cmd_input.setReadOnly(True)
            self.override_cmd_btn.show()

            matched = self._select_matching_desktop_item(
                entry.desktop_file, restore_entry_fields=True
            )
            if not matched:
                icon = resolve_icon(entry.icon, entry.desktop_file)
                self.preview_icon_label.setPixmap(icon.pixmap(48, 48))
                self.preview_title_label.setText(entry.name or Path(entry.desktop_file).name)
                self.preview_desc_label.setText(entry.desktop_file)
        else:
            self.custom_icon_input.setText(entry.icon)
            self._set_custom_mode(clear_inputs=False)
            self.preview_title_label.setText(entry.name or "Custom Command")
            icon = resolve_icon(entry.icon or "utilities-terminal", entry.icon)
            self.preview_icon_label.setPixmap(icon.pixmap(48, 48))

    def _create_app_entry_from_form(self) -> Optional[AppEntry]:
        name = self.name_input.text().strip()
        cmd = self.cmd_input.text().strip()
        delay = self.delay_spin.value()
        enabled = self.enabled_check.isChecked()
        minimized = self.minimized_check.isChecked()

        if not name:
            QMessageBox.warning(self, "Validation Error", "Please provide a display name for the application.")
            return None

        if self.is_custom_mode:
            if not cmd:
                QMessageBox.warning(self, "Validation Error", "Please enter a valid command line.")
                return None
            icon = self.custom_icon_input.text().strip()
            desktop_file = ""
            entry_id = self.existing_entry.id if self.existing_entry else str(uuid.uuid4())
        else:
            if self.selected_scanner_app:
                desktop_file = self.selected_scanner_app.desktop_file
                icon = self.selected_scanner_app.icon_name or self.selected_scanner_app.icon_path or ""
                if not cmd:
                    cmd = self.selected_scanner_app.clean_command
            elif self.existing_entry:
                desktop_file = self.existing_entry.desktop_file
                icon = self.existing_entry.icon
            else:
                desktop_file = ""
                icon = ""

            if not cmd and not desktop_file:
                QMessageBox.warning(self, "Validation Error", "Please select an application or specify a command.")
                return None

            entry_id = self.existing_entry.id if self.existing_entry else str(uuid.uuid4())

        return AppEntry(
            id=entry_id or str(uuid.uuid4()),
            name=name,
            command=cmd,
            desktop_file=desktop_file,
            icon=icon,
            enabled=enabled,
            delay_seconds=delay,
            start_minimized=minimized,
        )

    def _on_add_clicked(self) -> None:
        """Adds current configuration to the profile, updates feedback, and remains open to add more."""
        entry = self._create_app_entry_from_form()
        if not entry:
            return

        self.added_count += 1
        self.result_entry = entry
        self.app_added.emit(entry)
        if self.on_app_added_callback:
            self.on_app_added_callback(entry)

        count_text = f"{self.added_count} added" if self.added_count > 1 else "1 added"
        self.feedback_label.setText(f"Added '{entry.name}' ({count_text})")
        self.feedback_label.show()

    def _on_add_to_profile_only(self) -> None:
        """Compatibility wrapper for batch adding without closing."""
        self._on_add_clicked()

    def _on_save_and_close(self) -> None:
        """Saves current entry and closes dialog."""
        entry = self._create_app_entry_from_form()
        if not entry:
            return

        self.result_entry = entry
        self.app_added.emit(entry)
        if not self.is_edit_mode and self.on_app_added_callback:
            self.on_app_added_callback(entry)

        self.accept()

    def _on_app_double_clicked(self, item: QTableWidgetItem) -> None:
        """Double clicking an application adds/saves it; double clicking CUSTOM focuses name."""
        if not item or not item.data(Qt.ItemDataRole.UserRole):
            return
        if item.data(Qt.ItemDataRole.UserRole) == "CUSTOM":
            self.name_input.setFocus()
            return
        if self.is_edit_mode:
            self._on_save_and_close()
        else:
            self._on_add_clicked()

    def _on_close_clicked(self) -> None:
        """Closes the dialog."""
        if self.added_count > 0:
            self.accept()
        else:
            self.reject()

    def get_result(self) -> Optional[AppEntry]:
        return self.result_entry
