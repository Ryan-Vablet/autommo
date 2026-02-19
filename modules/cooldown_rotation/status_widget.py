"""Cooldown Rotation status: preview, slot states, last action, next intention, priority panel. Updated via module signals (QueuedConnection)."""

from __future__ import annotations

import time
from typing import Any, Optional

import numpy as np
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QCursor, QFontMetrics, QImage, QPixmap
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

# Import shared UI building blocks (used after main_window is loaded)
from src.ui.priority_panel import MIME_PRIORITY_ITEM, PriorityPanel, SlotButton

SECTION_BG = "#252535"
SECTION_BG_DARK = "#1e1e2e"
SECTION_BORDER = "#3a3a4a"
KEY_CYAN = "#66eeff"
KEY_YELLOW = "#eecc55"
KEY_BLUE = "#7db5ff"
PREVIEW_PADDING = 12


class CooldownRotationStatusWidget(QWidget):
    """Preview, slot states, last action, next intention, priority list. Connect module signals with Qt.QueuedConnection."""

    calibrate_slot_requested = pyqtSignal(int)  # slot_index; connect to main window so main.py can run calibration

    def __init__(self, module: Any, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._module = module
        self._slot_buttons: list[SlotButton] = []
        self._queued_override: Optional[dict] = None
        self._last_action_sent_time: Optional[float] = None
        self._last_fired_by_keybind: dict[str, float] = {}
        self._build_ui()
        self._next_intention_timer = QTimer(self)
        self._next_intention_timer.setInterval(100)
        self._next_intention_timer.timeout.connect(self._update_next_intention_time)

    def _config(self) -> Any:
        return self._module._get_app_config()

    def _build_ui(self) -> None:
        from src.ui.main_window import (
            LastActionHistoryWidget,
            _ActionEntryRow,
            _SlotStatesRow,
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        # Preview
        preview_frame = QFrame(self)
        preview_frame.setObjectName("sectionFrame")
        preview_frame.setStyleSheet(
            f"background: {SECTION_BG}; border: 1px solid {SECTION_BORDER}; border-radius: 4px; padding: 8px;"
        )
        preview_inner = QVBoxLayout(preview_frame)
        preview_inner.setContentsMargins(8, 8, 8, 8)
        title_preview = QLabel("LIVE PREVIEW")
        title_preview.setObjectName("sectionTitle")
        title_preview.setFixedHeight(28)
        preview_inner.addWidget(title_preview)
        self._preview_label = QLabel("No capture running")
        self._preview_label.setObjectName("previewLabel")
        self._preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_label.setMinimumSize(300, 42)
        self._preview_label.setStyleSheet(
            "background: #111; border-radius: 3px; color: #666; font-size: 11px;"
        )
        self._preview_label.setScaledContents(False)
        self._preview_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        preview_inner.addWidget(self._preview_label)
        preview_frame.setMinimumHeight(96)
        preview_frame.setMinimumWidth(320)
        layout.addWidget(preview_frame)
        # Slot states row
        self._slot_states_row = _SlotStatesRow(self)
        self._slot_states_row.setFixedHeight(34)
        layout.addWidget(self._slot_states_row)
        # Last action
        last_action_frame = QFrame()
        last_action_frame.setObjectName("sectionFrameDark")
        last_action_frame.setStyleSheet(
            f"background: {SECTION_BG_DARK}; border: 1px solid {SECTION_BORDER}; border-radius: 4px; padding: 8px;"
        )
        last_inner = QVBoxLayout(last_action_frame)
        last_inner.setContentsMargins(8, 8, 8, 8)
        last_inner.addWidget(QLabel("LAST ACTION"))
        history_rows = getattr(self._config(), "history_rows", 3)
        self._last_action_history = LastActionHistoryWidget(
            max_rows=history_rows, parent=last_action_frame, show_title=False
        )
        self._last_action_history.setMinimumHeight(80)
        last_inner.addWidget(self._last_action_history)
        last_action_frame.setMinimumHeight(28 + 80 + 24)
        layout.addWidget(last_action_frame)
        # Next intention
        next_frame = QFrame()
        next_frame.setObjectName("sectionFrameDark")
        next_frame.setStyleSheet(
            f"background: {SECTION_BG_DARK}; border: 1px solid {SECTION_BORDER}; border-radius: 4px; padding: 8px;"
        )
        next_inner = QVBoxLayout(next_frame)
        next_inner.setContentsMargins(8, 8, 8, 8)
        next_inner.addWidget(QLabel("NEXT INTENTION"))
        self._next_intention_row = _ActionEntryRow(
            "—", "no action", "", "", key_color="#555", parent=next_frame
        )
        next_inner.addWidget(self._next_intention_row)
        next_frame.setMinimumHeight(28 + 52 + 24)
        layout.addWidget(next_frame)
        # Priority panel (needs a parent with config; we pass self and it will use _config())
        self._priority_panel = PriorityPanel(self)
        self._priority_panel.setFixedWidth(210)
        layout.addWidget(self._priority_panel)

    def set_capture_running(self, running: bool) -> None:
        if running:
            self._last_action_sent_time = time.time()
            self._next_intention_timer.start()
            self._update_next_intention_time()
        else:
            self._next_intention_timer.stop()
            self._last_action_sent_time = None
            self._next_intention_row.set_time("")

    def set_queued_override(self, q: Optional[dict]) -> None:
        self._queued_override = q

    def update_preview(self, qimg: QImage) -> None:
        if qimg.isNull():
            return
        w, h = qimg.width(), qimg.height()
        pixmap = QPixmap.fromImage(qimg)
        # When label not yet laid out, width/height can be 0 and we'd scale to 1x1 (invisible)
        max_w = self._preview_label.width() - 2 * PREVIEW_PADDING
        max_h = self._preview_label.height() - 2 * PREVIEW_PADDING
        if max_w < 50 or max_h < 20:
            max_w = max(50, min(w, 500))
            max_h = max(20, min(h, 80))
        else:
            max_w = max(1, max_w)
            max_h = max(1, max_h)
        scaled = pixmap.scaled(
            max_w, max_h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._preview_label.setText("")
        self._preview_label.setPixmap(scaled)
        self._preview_label.update()

    def update_slot_states(self, states: list[dict]) -> None:
        if not states:
            return
        config = self._config()
        while len(config.keybinds) < len(states):
            config.keybinds.append("")
        if len(self._slot_buttons) != len(states):
            for b in self._slot_buttons:
                b.deleteLater()
            self._slot_buttons.clear()
            for i in range(len(states)):
                btn = SlotButton(i, self._slot_states_row)
                btn.setObjectName("slotButton")
                btn.setStyleSheet(
                    "border: 1px solid #444; padding: 4px; font-family: monospace; font-size: 10px; font-weight: bold;"
                )
                btn.context_menu_requested.connect(self._show_slot_context_menu)
                self._slot_buttons.append(btn)
            self._slot_states_row.set_buttons(self._slot_buttons)
        for btn, s in zip(self._slot_buttons, states):
            keybind = s.get("keybind")
            if keybind is None and s["index"] < len(config.keybinds):
                keybind = config.keybinds[s["index"]] or None
            keybind = keybind or "?"
            state = s.get("state", "unknown")
            cd = s.get("cooldown_remaining")
            self._apply_slot_button_style(btn, state, keybind, cd)
        self._priority_panel.priority_list.set_keybinds(config.keybinds)
        try:
            profile = config.get_active_priority_profile()
            self._priority_panel.priority_list.set_manual_actions(
                profile.get("manual_actions", []) if isinstance(profile, dict) else []
            )
        except Exception:
            self._priority_panel.priority_list.set_manual_actions([])
        self._priority_panel.priority_list.update_states(states)
        if self._queued_override:
            keybind = (self._queued_override.get("key") or "?").strip() or "?"
            names = getattr(config, "slot_display_names", [])
            slot_name = "Unidentified"
            if self._queued_override.get("source") == "tracked":
                si = self._queued_override.get("slot_index")
                if si is not None and si < len(names) and (names[si] or "").strip():
                    slot_name = (names[si] or "").strip()
            self._next_intention_row.set_content(keybind, slot_name, "queued", KEY_CYAN)
            return
        next_slot = self._next_ready_priority_slot(states)
        if next_slot is not None:
            keybind = config.keybinds[next_slot] if next_slot < len(config.keybinds) else "?"
            keybind = keybind or "?"
            names = getattr(config, "slot_display_names", [])
            slot_name = names[next_slot].strip() if next_slot < len(names) and (names[next_slot] or "").strip() else "Unidentified"
            self._next_intention_row.set_content(keybind, slot_name, "ready", KEY_CYAN)
        else:
            self._next_intention_row.set_content("—", "no action", "", "#555")

    def update_buff_states(self, states: dict) -> None:
        if not isinstance(states, dict):
            self._priority_panel.priority_list.set_buff_states({})
            return
        self._priority_panel.priority_list.set_buff_states(
            {str(k): dict(v) for k, v in states.items() if isinstance(v, dict)}
        )

    def mark_slots_recalibrated(self, slot_indices: set) -> None:
        pass  # Optional: track for bold slot labels

    def _apply_slot_button_style(
        self, btn: QPushButton, state: str, keybind: str, cooldown_remaining: Optional[float] = None
    ) -> None:
        display_key = keybind or "?"
        text = f"[{display_key}]"
        if cooldown_remaining is not None:
            text += f"\n{cooldown_remaining:.1f}s"
        btn.setText(text)
        color = {
            "ready": "#2d5a2d",
            "on_cooldown": "#5a2d2d",
            "casting": "#2a3f66",
            "channeling": "#5a4a1f",
            "locked": "#3f3f3f",
            "gcd": "#5a5a2d",
            "unknown": "#333333",
        }.get(state, "#333333")
        btn.setStyleSheet(
            f"border: 1px solid #444; padding: 4px; font-family: monospace; font-size: 10px; font-weight: bold;"
            f" background: {color}; color: #eee;"
        )

    def _show_slot_context_menu(self, slot_index: int) -> None:
        """Show right-click menu for a slot button (Calibrate This Slot, etc.)."""
        menu = QMenu(self)
        calibrate_action = menu.addAction("Calibrate This Slot")
        chosen = menu.exec(QCursor.pos())
        if chosen == calibrate_action:
            self.calibrate_slot_requested.emit(slot_index)

    def _next_ready_priority_slot(self, states: list[dict]) -> Optional[int]:
        config = self._config()
        try:
            profile = config.get_active_priority_profile()
        except Exception:
            profile = {}
        if not isinstance(profile, dict):
            profile = {}
        items = profile.get("priority_items", []) or []
        order = profile.get("priority_order", []) if isinstance(profile, dict) else []
        by_idx = {s["index"]: s for s in states}
        for idx in order:
            if isinstance(idx, int) and by_idx.get(idx, {}).get("state") == "ready":
                return idx
        for item in items:
            if isinstance(item, dict) and str(item.get("type") or "").strip().lower() == "slot":
                idx = item.get("slot_index")
                if isinstance(idx, int) and by_idx.get(idx, {}).get("state") == "ready":
                    return idx
        return None

    def _update_next_intention_time(self) -> None:
        if self._last_action_sent_time is not None:
            self._next_intention_row.set_time(
                f"{time.time() - self._last_action_sent_time:.1f}s"
            )

    def record_last_action_sent(
        self, keybind: str, timestamp: float, display_name: str = "Unidentified"
    ) -> None:
        elapsed = (
            (timestamp - self._last_action_sent_time)
            if self._last_action_sent_time is not None
            else 0.0
        )
        self._last_action_history.add_entry(
            keybind, display_name or "Unidentified", elapsed
        )
        self._last_action_sent_time = timestamp
        self._last_fired_by_keybind[keybind] = timestamp
        self._priority_panel.priority_list.set_last_fired_timestamps(self._last_fired_by_keybind)
        self._priority_panel.record_send_timestamp(timestamp)

    def set_next_intention_blocked(
        self, keybind: str, display_name: str = "Unidentified"
    ) -> None:
        self._next_intention_row.set_content(
            keybind, display_name or "Unidentified", "ready (window)", KEY_YELLOW
        )

    def set_next_intention_casting_wait(
        self,
        slot_index: Optional[int],
        cast_ends_at: Optional[float],
    ) -> None:
        config = self._config()
        name = "cast/channel"
        if slot_index is not None:
            names = getattr(config, "slot_display_names", [])
            if slot_index < len(names) and (names[slot_index] or "").strip():
                name = (names[slot_index] or "").strip()
            else:
                name = f"slot {slot_index + 1}"
        if cast_ends_at:
            remaining = max(0.0, cast_ends_at - time.time())
            status = f"waiting: casting ({remaining:.1f}s)"
        else:
            status = "waiting: channeling"
        self._next_intention_row.set_content("…", name, status, KEY_BLUE)

    def refresh_from_config(self) -> None:
        """Called when config changed; refresh priority panel and slot bindings."""
        config = self._config()
        try:
            profile = config.get_active_priority_profile()
        except Exception:
            profile = {}
        profile_name = str(profile.get("name", "") or "Default").strip() if isinstance(profile, dict) else "Default"
        self._priority_panel.set_priority_list_name(profile_name)
        self._priority_panel.priority_list.set_keybinds(config.keybinds)
        self._priority_panel.priority_list.set_display_names(
            getattr(config, "slot_display_names", [])
        )
        self._priority_panel.priority_list.set_buff_rois(
            getattr(config, "buff_rois", []) or []
        )
        manual = profile.get("manual_actions", []) if isinstance(profile, dict) else []
        self._priority_panel.priority_list.set_manual_actions(manual)
        items = profile.get("priority_items", []) if isinstance(profile, dict) else []
        self._priority_panel.priority_list.set_items(items)
        self._last_action_history.set_max_rows(getattr(config, "history_rows", 3))
        # Prepopulate slot state buttons from config so they appear before first frame
        slot_count = getattr(config, "slot_count", 0) or 0
        if slot_count > 0:
            keybinds = getattr(config, "keybinds", []) or []
            placeholder = [
                {
                    "index": i,
                    "state": "unknown",
                    "keybind": keybinds[i] if i < len(keybinds) else "?",
                    "cooldown_remaining": None,
                }
                for i in range(slot_count)
            ]
            self.update_slot_states(placeholder)

    def _on_manual_item_action(self, action_id: str, action: str) -> None:
        """Stub for priority list context menu; persist via core in Phase 1."""
        pass

    def _on_slot_item_activation_rule_changed(self, item_key: str, rule: str) -> None:
        """Stub for priority list context menu."""
        pass

    def _on_item_ready_source_changed(self, item_key: str, source: str, buff_id: str) -> None:
        """Stub for priority list context menu."""
        pass
