"""Main application window — streamlined for gameplay (enable bar, preview, slots, last action, next intention, priority, status bar)."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional

from PyQt6.QtCore import QPoint, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFontMetrics, QImage, QPixmap
from PyQt6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

import numpy as np

from src.models import BoundingBox
from src.ui.priority_panel import (
    MIME_PRIORITY_ITEM,
    PriorityPanel,
    SlotButton,
)
from src.automation.global_hotkey import format_bind_for_display
from src.automation.binds import normalize_bind, normalize_bind_from_parts
from src.automation.priority_rules import (
    manual_item_is_eligible,
    normalize_activation_rule,
    normalize_ready_source,
    slot_item_is_eligible_for_state_dict,
)

if TYPE_CHECKING:
    from src.automation.key_sender import KeySender

# Theme and accent colors (used when setting dynamic styles not in QSS)
KEY_CYAN = "#66eeff"
KEY_GREEN = "#88ff88"
KEY_YELLOW = "#eecc55"
KEY_BLUE = "#7db5ff"

SECTION_BG = "#252535"
SECTION_BG_DARK = "#1e1e2e"
SECTION_BORDER = "#3a3a4a"


def _load_main_window_theme() -> str:
    """Load dark theme QSS for the main window."""
    try:
        from src.ui.themes import load_theme

        return load_theme("dark")
    except Exception:
        return ""


class _SlotStatesRow(QWidget):
    """Horizontal row of slot buttons that stay square and fit the left column width."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(3)
        self._buttons: list[SlotButton] = []
        self._gap = 3

    def set_buttons(self, buttons: list[SlotButton]) -> None:
        for b in self._buttons:
            b.setParent(None)
            b.deleteLater()
        self._buttons = list(buttons)
        for b in self._buttons:
            b.setParent(self)
            self._layout.addWidget(b)
        self._update_sizes()

    def _update_sizes(self) -> None:
        n = len(self._buttons)
        if n == 0:
            return
        w = self.width()
        if w <= 0:
            return
        total_gap = (n - 1) * self._gap
        # Keep this row height stable; very large squares can push the lower panel
        # over the scroll threshold and cause resize/scrollbar oscillation.
        side = max(24, min(34, (w - total_gap) // n))
        for b in self._buttons:
            b.setFixedSize(side, side)

    def minimumSizeHint(self) -> QSize:
        n = len(self._buttons)
        if n == 0:
            return super().minimumSizeHint()
        # Report a small width so the left panel can shrink when window is narrowed.
        min_side = 24
        total_gap = (n - 1) * self._gap
        return QSize(min_side * n + total_gap, min_side)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_sizes()


class _LeftPanel(QWidget):
    """Left content area; accepts drops of priority items to remove them from the list."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._on_priority_drop_remove: Callable[[str], None] = lambda _: None

    def set_drop_remove_callback(self, callback: Callable[[str], None]) -> None:
        self._on_priority_drop_remove = callback

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(MIME_PRIORITY_ITEM):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        if event.mimeData().hasFormat(MIME_PRIORITY_ITEM):
            try:
                item_key = str(
                    event.mimeData().data(MIME_PRIORITY_ITEM).data().decode() or ""
                )
                if item_key:
                    self._on_priority_drop_remove(item_key)
            except (ValueError, TypeError, UnicodeDecodeError):
                pass
        event.acceptProposedAction()


class _ActionEntryRow(QWidget):
    """One row: key (colored), name + status, time. Used for Last Action and Next Intention."""

    def __init__(
        self,
        key: str,
        name: str,
        status: str,
        time_text: str = "",
        key_color: str = KEY_CYAN,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setObjectName("actionEntryRow")
        self.setStyleSheet(
            f"background: {SECTION_BG}; border-radius: 3px; padding: 4px 6px;"
        )
        self.setMinimumHeight(52)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(8)
        self._key_label = QLabel(key)
        self._key_label.setObjectName("actionKey")
        self._key_label.setStyleSheet(
            f"font-family: monospace; font-size: 14px; font-weight: bold; color: {key_color}; min-width: 24px;"
        )
        self._key_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._key_label)
        info = QVBoxLayout()
        info.setSpacing(2)
        self._name_label = QLabel(name)
        self._name_label.setObjectName("actionName")
        self._name_label.setStyleSheet("font-size: 11px; color: #ccc;")
        self._name_label.setMinimumWidth(0)
        self._name_label.setMinimumHeight(18)
        self._name_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum
        )
        self._name_label.setWordWrap(False)
        info.addWidget(self._name_label)
        self._status_label = QLabel(status)
        self._status_label.setObjectName("actionMeta")
        self._status_label.setMinimumHeight(14)
        self._status_label.setStyleSheet(
            "font-size: 9px; color: #666; font-family: monospace;"
        )
        info.addWidget(self._status_label)
        layout.addLayout(info, 1)
        self._time_label = QLabel(time_text)
        self._time_label.setObjectName("actionTime")
        self._time_label.setStyleSheet(
            "font-size: 9px; color: #555; font-family: monospace;"
        )
        self._time_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        # Keep row geometry stable while this text updates every 100 ms.
        self._time_label.setFixedWidth(
            max(42, QFontMetrics(self._time_label.font()).horizontalAdvance("0000.0s"))
        )
        layout.addWidget(self._time_label)

    def set_time(self, text: str) -> None:
        self._time_label.setText(text)

    def set_content(
        self, key: str, name: str, status: str, key_color: str = KEY_CYAN
    ) -> None:
        self._key_label.setText(key)
        self._key_label.setStyleSheet(
            f"font-family: monospace; font-size: 14px; font-weight: bold; color: {key_color}; min-width: 24px;"
        )
        self._name_label.setText(name)
        self._status_label.setText(status)


class LastActionHistoryWidget(QWidget):
    """Last Action section: sent actions with fixed duration (time to fire). N placeholder rows when empty; no live counter."""

    def __init__(
        self,
        max_rows: int = 3,
        parent: Optional[QWidget] = None,
        show_title: bool = True,
    ):
        super().__init__(parent)
        self._max_rows = max(1, max_rows)
        self._entries: list[tuple[QWidget, QGraphicsOpacityEffect]] = (
            []
        )  # (row, opacity_effect) — time is fixed per row
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        # Min height so rows can't collapse and overlap: (row_height * n) + (spacing * (n-1)) + top margin
        self._update_min_height()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(4)
        if show_title:
            title = QLabel("LAST ACTION")
            title.setObjectName("sectionTitle")
            title.setFixedHeight(28)
            title.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            title.setStyleSheet(
                "font-family: monospace; font-size: 10px; color: #666; font-weight: bold; letter-spacing: 1.5px;"
            )
            layout.addWidget(title)
        self._rows_container = QVBoxLayout()
        self._rows_container.setSpacing(4)
        layout.addLayout(self._rows_container)
        self._placeholder_rows: list[QWidget] = []
        for _ in range(self._max_rows):
            ph = _ActionEntryRow(
                "—", "No actions recorded", "", "", key_color="#555", parent=self
            )
            ph.setStyleSheet(ph.styleSheet() + " opacity: 0.7;")
            self._placeholder_rows.append(ph)
            self._rows_container.addWidget(ph)

    def set_max_rows(self, n: int) -> None:
        n = max(1, min(10, n))
        if n < self._max_rows:
            for i in range(self._max_rows - n):
                ph = self._placeholder_rows.pop()
                self._rows_container.removeWidget(ph)
                ph.deleteLater()
            while len(self._entries) > n:
                row, eff = self._entries.pop()
                self._rows_container.removeWidget(row)
                row.deleteLater()
        elif n > self._max_rows:
            for i in range(n - self._max_rows):
                ph = _ActionEntryRow(
                    "—", "No actions recorded", "", "", key_color="#555", parent=self
                )
                ph.setStyleSheet(ph.styleSheet() + " opacity: 0.7;")
                self._placeholder_rows.append(ph)
                self._rows_container.addWidget(ph)
        self._max_rows = n
        self._update_min_height()
        for i, ph in enumerate(self._placeholder_rows):
            ph.setVisible(i >= len(self._entries))
        self._update_opacities()

    def add_entry(
        self, keybind: str, display_name: str, duration_seconds: float
    ) -> None:
        """Add a sent action; duration_seconds is shown once (time since previous fire), not updated."""
        row = _ActionEntryRow(
            keybind,
            display_name or "Unidentified",
            "sent",
            f"{duration_seconds:.1f}s",
            KEY_CYAN,
            self,
        )
        eff = QGraphicsOpacityEffect(self)
        row.setGraphicsEffect(eff)
        self._entries.insert(0, (row, eff))
        self._rows_container.insertWidget(0, row)
        for i in range(min(len(self._entries), len(self._placeholder_rows))):
            self._placeholder_rows[i].hide()
        while len(self._entries) > self._max_rows:
            old_row, old_eff = self._entries.pop()
            self._rows_container.removeWidget(old_row)
            old_row.deleteLater()
            if len(self._entries) < len(self._placeholder_rows):
                self._placeholder_rows[len(self._entries)].show()
        self._update_opacities()

    def _update_min_height(self) -> None:
        """Set minimum height so rows cannot collapse and overlap (row 52px + 4px spacing between)."""
        row_h = 52
        spacing = 4
        top_margin = 4
        self.setMinimumHeight(top_margin + self._max_rows * row_h + max(0, self._max_rows - 1) * spacing)

    def _update_opacities(self) -> None:
        for i, (row, eff) in enumerate(self._entries):
            op = max(0.2, 1.0 - (i * 0.25))
            eff.setOpacity(op)


logger = logging.getLogger(__name__)

class MainWindow(QMainWindow):
    """Primary control panel for Cooldown Reader."""

    # Emitted when bounding box changes, so overlay can update
    bounding_box_changed = pyqtSignal(BoundingBox)
    config_updated = pyqtSignal(object)  # root config dict when something changes
    # Emitted when slot layout changes (count, gap, padding) for overlay slot outlines
    slot_layout_changed = pyqtSignal(
        int, int, int
    )  # slot_count, slot_gap_pixels, slot_padding
    # Emitted when overlay visibility is toggled (True = show, False = hide)
    overlay_visibility_changed = pyqtSignal(bool)
    monitor_changed = pyqtSignal(int)
    # Emitted when user chooses "Calibrate This Slot" for a slot index
    calibrate_slot_requested = pyqtSignal(int)
    start_capture_requested = pyqtSignal()

    def __init__(
        self,
        core: Any,
        module_manager: Any,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._core = core
        self._module_manager = module_manager
        self._before_save_callback: Optional[Callable[[], None]] = None
        self.setWindowTitle("Cooldown Reader")
        self.setMinimumSize(580, 400)
        self.resize(800, 700)

        self._build_ui()
        _qss = _load_main_window_theme()
        if _qss:
            self.setStyleSheet(self.styleSheet() + "\n" + _qss)
        self.setStatusBar(QStatusBar())
        self._profile_status_label = QLabel("Profile: —")
        self._profile_status_label.setStyleSheet(
            "font-size: 10px; font-family: monospace; color: #555;"
        )
        self.statusBar().addWidget(self._profile_status_label)
        self._status_message_label = QLabel()
        self._status_message_label.setStyleSheet("color: #555; font-size: 10px;")
        self.statusBar().addWidget(self._status_message_label, 1)
        self._gcd_label = QLabel("Est. GCD: —")
        self._gcd_label.setStyleSheet(
            "font-size: 10px; font-family: monospace; color: #555;"
        )
        self.statusBar().addPermanentWidget(self._gcd_label)
        self._cast_bar_debug_label = QLabel("Cast ROI: off")
        self._cast_bar_debug_label.setStyleSheet("font-size: 10px; font-family: monospace; color: #666;")
        self.statusBar().addPermanentWidget(self._cast_bar_debug_label)
        self._connect_signals()
        self._refresh_from_core()

    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName("centralWidget")
        self.setCentralWidget(central)
        top_layout = QVBoxLayout(central)
        top_layout.setContentsMargins(16, 16, 16, 16)
        top_layout.setSpacing(14)

        # --- Enable bar ---
        enable_bar = QHBoxLayout()
        enable_bar.setSpacing(10)
        self._btn_automation_toggle = QPushButton()
        self._btn_automation_toggle.setObjectName("enableToggle")
        self._btn_automation_toggle.setMinimumHeight(32)
        self._btn_automation_toggle.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self._btn_automation_toggle.clicked.connect(self._on_automation_toggle_clicked)
        self._btn_automation_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        enable_bar.addWidget(self._btn_automation_toggle)
        self._bind_display = QLabel("Toggle: —")
        self._bind_display.setObjectName("bindDisplay")
        self._bind_display.setAlignment(Qt.AlignmentFlag.AlignCenter)
        enable_bar.addWidget(self._bind_display)
        top_layout.addLayout(enable_bar)

        # Button row: Start Capture + Settings
        button_row = QHBoxLayout()
        button_row.setSpacing(10)
        self._btn_start = QPushButton("▶ Start Capture")
        self._btn_start.setObjectName("btnStartCapture")
        self._btn_start.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_start.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        button_row.addWidget(self._btn_start)
        self._btn_settings = QPushButton("⚙ Settings")
        self._btn_settings.setObjectName("btnSettings")
        self._btn_settings.setCursor(Qt.CursorShape.PointingHandCursor)
        button_row.addWidget(self._btn_settings)
        top_layout.addLayout(button_row)

        # Module status area (e.g. Cooldown Rotation: preview, slots, last action, next intention, priority)
        module_scroll = QScrollArea()
        module_scroll.setWidgetResizable(True)
        module_scroll.setFrameShape(QFrame.Shape.NoFrame)
        module_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        module_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        module_content = QWidget()
        module_layout = QVBoxLayout(module_content)
        module_layout.setContentsMargins(0, 0, 0, 0)
        module_layout.setSpacing(14)
        for _name, widget in self._module_manager.get_status_widgets():
            module_layout.addWidget(widget)
        module_layout.addStretch(1)
        module_scroll.setWidget(module_content)
        top_layout.addWidget(module_scroll, 1)

    def _connect_signals(self) -> None:
        pass  # Start/Settings/automation connected by main; module widgets own their signals

    def _cooldown_status_widget(self) -> Optional[QWidget]:
        """First status widget (cooldown_rotation) for delegation. Main can also get from module_manager.get_status_widgets()."""
        for name, widget in self._module_manager.get_status_widgets():
            return widget
        return None

    def _cooldown_config(self) -> dict:
        """Cooldown rotation config slice."""
        return self._core.get_config("cooldown_rotation")

    def _active_priority_profile(self) -> dict:
        """Active automation profile from cooldown_rotation config."""
        cfg = self._cooldown_config()
        profiles = cfg.get("priority_profiles") or []
        active_id = (cfg.get("active_priority_profile_id") or "").strip().lower()
        for p in profiles:
            if isinstance(p, dict) and (str(p.get("id") or "").strip().lower() == active_id):
                return dict(p)
        return dict(profiles[0]) if profiles else {}

    def _active_priority_order(self) -> list[int]:
        return list(self._active_priority_profile().get("priority_order", []))

    def _active_priority_items(self) -> list[dict]:
        profile = self._active_priority_profile()
        items = profile.get("priority_items", [])
        if isinstance(items, list) and items:
            normalized: list[dict] = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                out = dict(item)
                if str(out.get("type", "") or "").strip().lower() == "slot":
                    out["activation_rule"] = normalize_activation_rule(
                        out.get("activation_rule")
                    )
                    out["ready_source"] = normalize_ready_source(
                        out.get("ready_source"), "slot"
                    )
                    out["buff_roi_id"] = str(
                        out.get("buff_roi_id", "") or ""
                    ).strip().lower()
                elif str(out.get("type", "") or "").strip().lower() == "manual":
                    out["ready_source"] = normalize_ready_source(
                        out.get("ready_source"), "manual"
                    )
                    out["buff_roi_id"] = str(
                        out.get("buff_roi_id", "") or ""
                    ).strip().lower()
                normalized.append(out)
            return normalized
        return [
            {"type": "slot", "slot_index": i, "activation_rule": "always"}
            for i in self._active_priority_order()
        ]

    def _active_manual_actions(self) -> list[dict]:
        profile = self._active_priority_profile()
        actions = profile.get("manual_actions", [])
        if not isinstance(actions, list):
            actions = []
            profile["manual_actions"] = actions
        return [a for a in actions if isinstance(a, dict)]

    @staticmethod
    def _slot_order_from_priority_items(items: list[dict]) -> list[int]:
        return [
            int(i["slot_index"])
            for i in list(items or [])
            if isinstance(i, dict)
            and str(i.get("type", "") or "").strip().lower() == "slot"
            and isinstance(i.get("slot_index"), int)
        ]

    def _set_priority_list_from_active_profile(self) -> None:
        pass  # Handled by cooldown_rotation status widget

    def set_active_priority_profile(
        self, profile_id: str, persist: bool = False
    ) -> None:
        cfg = self._cooldown_config()
        pid = (profile_id or "").strip().lower()
        if not pid:
            return
        profiles = cfg.get("priority_profiles") or []
        if not any(isinstance(p, dict) and (str(p.get("id") or "").strip().lower() == pid) for p in profiles):
            return
        cfg = dict(cfg)
        cfg["active_priority_profile_id"] = pid
        self._core.save_config("cooldown_rotation", cfg)
        profile_name = "Default"
        for p in profiles:
            if isinstance(p, dict) and (str(p.get("id") or "").strip().lower() == pid):
                profile_name = (str(p.get("name", "") or "").strip()) or "Default"
                break
        self._profile_status_label.setText(f"Automation: {profile_name}")
        self._update_bind_display()
        if persist:
            self.config_updated.emit(self._core.get_root_config())

    def _refresh_from_core(self) -> None:
        """Set UI from core: profile label, bind display, automation button, GCD; refresh module status widgets."""
        self._update_automation_button_text()
        profile_name = (
            str(self._active_priority_profile().get("name", "") or "").strip()
            or "Default"
        )
        self._profile_status_label.setText(f"Automation: {profile_name}")
        gcd = self._core.get_service("cooldown_rotation", "gcd_estimate")
        if gcd is not None and isinstance(gcd, (int, float)):
            self._gcd_label.setText(f"Est. GCD: {float(gcd):.2f}s")
        else:
            self._gcd_label.setText("Est. GCD: —")
        for _name, widget in self._module_manager.get_status_widgets():
            if hasattr(widget, "refresh_from_config"):
                widget.refresh_from_config()

    def refresh_from_config(self) -> None:
        """Called when config is updated from Settings dialog."""
        self._refresh_from_core()

    def _maybe_auto_save(self) -> None:
        pass  # Config persisted via Core/settings dialog

    def _prepopulate_slot_buttons(self) -> None:
        pass  # Handled by cooldown_rotation status widget

    def _update_automation_button_text(self) -> None:
        """Set toggle button to Enabled/Disabled (green/gray) and bind display to Toggle: [key]."""
        mod = self._module_manager.get("cooldown_rotation")
        enabled = getattr(mod, "enabled", True) if mod else False
        self._btn_automation_toggle.setProperty(
            "enabled", "true" if enabled else "false"
        )
        self._btn_automation_toggle.style().unpolish(self._btn_automation_toggle)
        self._btn_automation_toggle.style().polish(self._btn_automation_toggle)
        self._btn_automation_toggle.setText("Enabled" if enabled else "Disabled")
        self._update_bind_display()

    def _update_bind_display(self) -> None:
        profile = self._active_priority_profile()
        toggle_bind = str(profile.get("toggle_bind", "") or "").strip()
        single_fire_bind = str(profile.get("single_fire_bind", "") or "").strip()
        display_toggle = format_bind_for_display(toggle_bind) if toggle_bind else "—"
        display_single = (
            format_bind_for_display(single_fire_bind) if single_fire_bind else "—"
        )
        self._bind_display.setTextFormat(Qt.TextFormat.RichText)
        self._bind_display.setText(
            f"Toggle: <span style='color:{KEY_CYAN}'>{display_toggle}</span>"
            f" | Single: <span style='color:{KEY_CYAN}'>{display_single}</span>"
        )

    def _on_automation_toggle_clicked(self) -> None:
        mod = self._module_manager.get("cooldown_rotation")
        if mod:
            mod.toggle_rotation()
        self._update_automation_button_text()
        self.config_updated.emit(self._core.get_root_config())

    def toggle_automation(self) -> None:
        """Toggle automation on/off (e.g. from global hotkey)."""
        mod = self._module_manager.get("cooldown_rotation")
        if mod:
            mod.toggle_rotation()
        self._update_automation_button_text()
        self.config_updated.emit(self._core.get_root_config())

    def refresh_from_config(self) -> None:
        """Refresh UI from core config (e.g. after import in settings)."""
        self._refresh_from_core()

    def set_key_sender(self, key_sender: Optional["KeySender"]) -> None:
        pass  # Key sender from core

    def _on_priority_items_changed(self, items: list) -> None:
        pass  # Handled by cooldown_rotation status widget

    def _on_gcd_updated(self, gcd_seconds: float) -> None:
        self._gcd_label.setText(f"Est. GCD: {gcd_seconds:.2f}s")

    def record_last_action_sent(
        self, keybind: str, timestamp: float, display_name: str = "Unidentified"
    ) -> None:
        w = self._cooldown_status_widget()
        if w and hasattr(w, "record_last_action_sent"):
            w.record_last_action_sent(keybind, timestamp, display_name)

    def set_next_intention_blocked(
        self, keybind: str, display_name: str = "Unidentified"
    ) -> None:
        w = self._cooldown_status_widget()
        if w and hasattr(w, "set_next_intention_blocked"):
            w.set_next_intention_blocked(keybind, display_name)

    def set_queued_override(self, q: Optional[dict]) -> None:
        w = self._cooldown_status_widget()
        if w and hasattr(w, "set_queued_override"):
            w.set_queued_override(q)

    def set_queue_listener(self, listener: Optional[object]) -> None:
        pass  # Module owns queue listener

    def set_next_intention_casting_wait(
        self,
        slot_index: Optional[int],
        cast_ends_at: Optional[float],
    ) -> None:
        w = self._cooldown_status_widget()
        if w and hasattr(w, "set_next_intention_casting_wait"):
            w.set_next_intention_casting_wait(slot_index, cast_ends_at)

    def _on_priority_drop_remove(self, item_key: str) -> None:
        pass  # Handled by cooldown_rotation status widget

    # Padding (px) around the preview image inside the Live Preview panel
    PREVIEW_PADDING = 12

    def set_capture_running(self, running: bool) -> None:
        w = self._cooldown_status_widget()
        if w and hasattr(w, "set_capture_running"):
            w.set_capture_running(running)

    def _update_next_intention_time(self) -> None:
        pass  # Handled by cooldown_rotation status widget

    def update_preview(self, frame: np.ndarray) -> None:
        w = self._cooldown_status_widget()
        if w and hasattr(w, "update_preview"):
            w.update_preview(frame)

    def _apply_slot_button_style(
        self,
        btn: QPushButton,
        state: str,
        keybind: str,
        cooldown_remaining: Optional[float] = None,
        slot_index: int = -1,
    ) -> None:
        pass  # Handled by cooldown_rotation status widget

    def _next_priority_candidate(self, states: list[dict]) -> Optional[dict]:
        return None  # Handled by cooldown_rotation status widget

    def _next_casting_priority_slot(
        self, states: list[dict]
    ) -> tuple[Optional[int], Optional[float]]:
        return (None, None)  # Handled by cooldown_rotation status widget

    def _next_ready_priority_slot(self, states: list[dict]) -> Optional[int]:
        return None  # Handled by cooldown_rotation status widget

    def update_buff_states(self, states: dict) -> None:
        w = self._cooldown_status_widget()
        if w and hasattr(w, "update_buff_states"):
            w.update_buff_states(states)

    def _show_slot_menu(self, slot_index: int) -> None:
        pass  # Handled by cooldown_rotation status widget

    def _rename_slot(self, slot_index: int) -> None:
        pass  # Handled by cooldown_rotation status widget

    def _find_manual_action(self, action_id: str) -> Optional[dict]:
        return None  # Handled by cooldown_rotation status widget

    def _on_add_manual_action(self) -> None:
        pass  # Handled by cooldown_rotation status widget

    def _on_rename_manual_action(self, action_id: str) -> None:
        pass  # Handled by cooldown_rotation status widget

    def _on_rebind_manual_action(self, action_id: str) -> None:
        pass  # Handled by cooldown_rotation status widget

    def _on_remove_manual_action(self, action_id: str) -> None:
        pass  # Handled by cooldown_rotation status widget

    def _start_listening_for_key(self, slot_index: int) -> None:
        pass  # Handled by cooldown_rotation status widget

    @staticmethod
    def _qt_key_to_bind_token(event) -> str:
        key = int(event.key())
        if int(Qt.Key.Key_0) <= key <= int(Qt.Key.Key_9):
            return str(key - int(Qt.Key.Key_0))
        if int(Qt.Key.Key_A) <= key <= int(Qt.Key.Key_Z):
            return chr(ord("a") + (key - int(Qt.Key.Key_A)))
        if int(Qt.Key.Key_F1) <= key <= int(Qt.Key.Key_F35):
            return f"f{key - int(Qt.Key.Key_F1) + 1}"
        key_map = {
            int(Qt.Key.Key_Space): "space",
            int(Qt.Key.Key_Tab): "tab",
            int(Qt.Key.Key_Backtab): "tab",
            int(Qt.Key.Key_Return): "enter",
            int(Qt.Key.Key_Enter): "enter",
            int(Qt.Key.Key_Backspace): "backspace",
            int(Qt.Key.Key_Delete): "delete",
            int(Qt.Key.Key_Insert): "insert",
            int(Qt.Key.Key_Home): "home",
            int(Qt.Key.Key_End): "end",
            int(Qt.Key.Key_PageUp): "page up",
            int(Qt.Key.Key_PageDown): "page down",
            int(Qt.Key.Key_Left): "left",
            int(Qt.Key.Key_Right): "right",
            int(Qt.Key.Key_Up): "up",
            int(Qt.Key.Key_Down): "down",
            int(Qt.Key.Key_Minus): "-",
            int(Qt.Key.Key_Equal): "=",
            int(Qt.Key.Key_BracketLeft): "[",
            int(Qt.Key.Key_BracketRight): "]",
            int(Qt.Key.Key_Backslash): "\\",
            int(Qt.Key.Key_Semicolon): ";",
            int(Qt.Key.Key_Apostrophe): "'",
            int(Qt.Key.Key_Comma): ",",
            int(Qt.Key.Key_Period): ".",
            int(Qt.Key.Key_Slash): "/",
            int(Qt.Key.Key_QuoteLeft): "`",
        }
        token = key_map.get(key, "")
        if token:
            return token
        text = str(event.text() or "").strip().lower()
        return text if len(text) == 1 else ""

    def _cancel_listening(self) -> None:
        pass  # Handled by cooldown_rotation status widget

    def keyPressEvent(self, event) -> None:
        super().keyPressEvent(event)

    def update_slot_states(self, states: list[dict]) -> None:
        w = self._cooldown_status_widget()
        if w and hasattr(w, "update_slot_states"):
            w.update_slot_states(states)

    def set_before_save_callback(self, callback: Optional[Callable[[], None]]) -> None:
        """Set a callback run before writing config (e.g. to sync baselines from analyzer)."""
        self._before_save_callback = callback

    def mark_slots_recalibrated(self, slot_indices: set[int]) -> None:
        w = self._cooldown_status_widget()
        if w and hasattr(w, "mark_slots_recalibrated"):
            w.mark_slots_recalibrated(slot_indices)

    def mark_slot_recalibrated(self, slot_index: int) -> None:
        cfg = self._cooldown_config()
        overwritten = list(cfg.get("overwritten_baseline_slots") or [])
        if slot_index not in overwritten:
            overwritten.append(slot_index)
            cfg = dict(cfg)
            cfg["overwritten_baseline_slots"] = overwritten
            self._core.save_config("cooldown_rotation", cfg)

    def clear_overwritten_baseline_slots(self) -> None:
        cfg = self._cooldown_config()
        cfg = dict(cfg)
        cfg["overwritten_baseline_slots"] = []
        self._core.save_config("cooldown_rotation", cfg)

    def _save_config(self) -> None:
        if self._before_save_callback:
            self._before_save_callback()
        self.config_updated.emit(self._core.get_root_config())

    def show_status_message(self, text: str, timeout_ms: int = 0) -> None:
        """Show text in the status bar to the right of the Settings button. If timeout_ms > 0, clear after that many ms."""
        self._status_message_label.setText(text)
        if timeout_ms > 0:
            QTimer.singleShot(
                timeout_ms, lambda: self._status_message_label.setText("")
            )

    def _show_status_message(self, text: str, timeout_ms: int = 0) -> None:
        """Internal alias for show_status_message."""
        self.show_status_message(text, timeout_ms)

    def update_cast_bar_debug(self, debug: dict) -> None:
        """Update live cast-bar ROI motion/status debug in the status bar."""
        if not isinstance(debug, dict):
            return
        status = str(debug.get("status", "off") or "off")
        motion = float(debug.get("motion", 0.0) or 0.0)
        activity = float(debug.get("activity", 0.0) or 0.0)
        threshold = float(debug.get("threshold", 0.0) or 0.0)
        deactivate_threshold = float(debug.get("deactivate_threshold", 0.0) or 0.0)
        active = bool(debug.get("active", False))
        present = bool(debug.get("present", False))
        directional = bool(debug.get("directional", False))
        front = float(debug.get("front", 0.0) or 0.0)
        gate_active = bool(debug.get("gate_active", False))
        self._cast_bar_debug_label.setText(
            f"Cast ROI: {status} | m {motion:.1f} a {activity:.1f}/{threshold:.1f}->{deactivate_threshold:.1f} | "
            f"p {'Y' if present else 'N'} d {'Y' if directional else 'N'} f {front:.2f} | "
            f"{'ON' if active else 'OFF'} gate {'ON' if gate_active else 'OFF'}"
        )
        if status in ("off", "invalid-roi", "out-of-frame", "no-bar"):
            color = "#777"
        elif active:
            color = "#88ff88"
        elif status == "priming":
            color = "#eecc55"
        elif status == "not-directional":
            color = "#d8b377"
        elif gate_active:
            color = "#ffcc66"
        else:
            color = "#9aa0a6"
        self._cast_bar_debug_label.setStyleSheet(
            f"font-size: 10px; font-family: monospace; color: {color};"
        )

    def _on_settings_clicked(self) -> None:
        """No-op; main.py connects _btn_settings to settings_dialog.show_or_raise."""
        pass

