"""Cooldown Rotation module: slot analysis, priority, queue, key sending. Runs on_frame in capture thread; emits signals for GUI updates."""

from __future__ import annotations

import logging
from abc import ABCMeta
from typing import Any, Optional

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal

from src.core.base_module import BaseModule
from src.core.config_migration import flatten_config
from src.models import AppConfig
from src.analysis import SlotAnalyzer
from src.automation.queue_listener import QueueListener

logger = logging.getLogger(__name__)


class _ModuleMeta(type(QObject), ABCMeta):
    """Combined metaclass so CooldownRotationModule can inherit QObject and BaseModule (ABC)."""
    pass


class CooldownRotationModule(QObject, BaseModule, metaclass=_ModuleMeta):
    """Monitors action bar cooldowns and sends keys based on priority. Owns SlotAnalyzer and QueueListener."""

    name = "Cooldown Rotation"
    key = "cooldown_rotation"
    version = "1.0.0"
    description = "Monitors action bar cooldowns and sends keys based on priority"
    requires: list[str] = []
    optional: list[str] = []
    provides_services = ["slot_states", "priority_order", "gcd_estimate", "is_active"]
    extension_points = ["settings.detection", "settings.automation"]
    hooks = ["slot_states_updated", "key_sent", "rotation_toggled"]

    # Emit from capture thread; connect with Qt.ConnectionType.QueuedConnection for GUI updates
    slot_states_updated_signal = pyqtSignal(list)
    key_action_signal = pyqtSignal(dict)
    buff_state_updated_signal = pyqtSignal(object)
    cast_bar_debug_signal = pyqtSignal(object)

    def __init__(self) -> None:
        QObject.__init__(self)
        BaseModule.__init__(self)
        self._analyzer: Optional[SlotAnalyzer] = None
        self._queue_listener: Optional[QueueListener] = None
        self._status_widget: Optional[Any] = None
        self._action_origin: tuple[int, int] = (0, 0)
        self._slot_states: list[dict] = []
        self._last_priority_order: list[int] = []
        self._gcd_estimate_sec: float = 1.5
        self._is_active: bool = False

    def _get_app_config(self) -> AppConfig:
        if self.core is None:
            return AppConfig()
        core_cfg = self.core.get_config("core")
        cr_cfg = self.core.get_config(self.key)
        flat = flatten_config(core_cfg, cr_cfg)
        return AppConfig.from_dict(flat)

    def setup(self, core: Any) -> None:
        super().setup(core)
        app_config = self._get_app_config()
        self._analyzer = SlotAnalyzer(app_config)
        # Baselines are loaded by main after module load (avoids circular import)
        self._queue_listener = QueueListener(get_config=self._get_app_config, parent=self)
        self._queue_listener.start()
        self._is_active = app_config.automation_enabled

    def set_action_origin(self, ax: int, ay: int) -> None:
        """Set action bar origin in the captured frame. Call from main before process_frame."""
        self._action_origin = (ax, ay)

    def get_queue_listener(self) -> Optional[QueueListener]:
        return self._queue_listener

    def get_analyzer(self) -> Optional[SlotAnalyzer]:
        return self._analyzer

    def on_frame(self, frame: np.ndarray) -> None:
        if self.core is None or self._analyzer is None:
            return
        app_config = self._get_app_config()
        ax, ay = self._action_origin
        state = self._analyzer.analyze_frame(frame, action_origin=(ax, ay))
        keybinds = app_config.keybinds
        slot_dicts = [
            {
                "index": s.index,
                "state": s.state.value,
                "keybind": keybinds[s.index] if s.index < len(keybinds) else None,
                "cooldown_remaining": s.cooldown_remaining,
                "cast_progress": s.cast_progress,
                "cast_ends_at": s.cast_ends_at,
                "last_cast_start_at": s.last_cast_start_at,
                "last_cast_success_at": s.last_cast_success_at,
                "glow_candidate": bool(getattr(s, "glow_candidate", False)),
                "glow_fraction": float(getattr(s, "glow_fraction", 0.0) or 0.0),
                "glow_ready": bool(getattr(s, "glow_ready", False)),
                "yellow_glow_candidate": bool(getattr(s, "yellow_glow_candidate", False)),
                "yellow_glow_fraction": float(getattr(s, "yellow_glow_fraction", 0.0) or 0.0),
                "yellow_glow_ready": bool(getattr(s, "yellow_glow_ready", False)),
                "red_glow_candidate": bool(getattr(s, "red_glow_candidate", False)),
                "red_glow_fraction": float(getattr(s, "red_glow_fraction", 0.0) or 0.0),
                "red_glow_ready": bool(getattr(s, "red_glow_ready", False)),
                "brightness": s.brightness,
            }
            for s in state.slots
        ]
        self._slot_states = slot_dicts
        self.slot_states_updated_signal.emit(slot_dicts)
        self.core.emit(f"{self.key}.slot_states_updated", states=slot_dicts)
        buff_states = self._analyzer.buff_states()
        self.buff_state_updated_signal.emit(buff_states)
        self.cast_bar_debug_signal.emit(self._analyzer.cast_bar_debug())
        key_sender = self.core.get_key_sender()
        queued = self._queue_listener.get_queue() if self._queue_listener else None
        on_queued_sent = self._queue_listener.clear_queue if self._queue_listener else None
        if key_sender is not None:
            result = key_sender.evaluate_and_send(
                state,
                app_config.active_priority_items(),
                app_config.keybinds,
                app_config.active_manual_actions(),
                app_config.automation_enabled,
                buff_states=buff_states,
                queued_override=queued,
                on_queued_sent=on_queued_sent,
            )
            if result is not None:
                self.key_action_signal.emit(result)
                self.core.emit(f"{self.key}.key_sent", **result)
        self._last_priority_order = list(app_config.active_priority_order())
        self._gcd_estimate_sec = (app_config.gcd_ms or 1500) / 1000.0
        self._is_active = app_config.automation_enabled

    def update_analyzer_config(self) -> None:
        """Call when config changed so analyzer and key_sender use new values."""
        if self._analyzer is None or self.core is None:
            return
        app_config = self._get_app_config()
        self._analyzer.update_config(app_config)
        key_sender = self.core.get_key_sender()
        if key_sender is not None and hasattr(key_sender, "update_config"):
            key_sender.update_config(app_config)

    def get_service_value(self, service_name: str) -> Any:
        if service_name == "slot_states":
            return self._slot_states
        if service_name == "priority_order":
            return self._last_priority_order
        if service_name == "gcd_estimate":
            return self._gcd_estimate_sec
        if service_name == "is_active":
            return self._is_active
        return None

    def get_settings_widget(self) -> Optional[Any]:
        """Return Detection + Automation settings widget. Implemented in settings_widget.py."""
        try:
            from modules.cooldown_rotation.settings_widget import CooldownRotationSettingsWidget
            if self.core is None:
                return None
            return CooldownRotationSettingsWidget(self.core)
        except ImportError:
            return None

    def get_status_widget(self) -> Optional[Any]:
        """Return status widget (preview, slots, last action, priority). Same instance every time so main window updates the visible widget."""
        try:
            from modules.cooldown_rotation.status_widget import CooldownRotationStatusWidget
            if self.core is None:
                return None
            if self._status_widget is None:
                self._status_widget = CooldownRotationStatusWidget(self)
            return self._status_widget
        except ImportError:
            return None

    def on_enable(self) -> None:
        if self.core is None:
            return
        cr = self.core.get_config(self.key)
        cr["automation_enabled"] = True
        self.core.save_config(self.key, cr)
        self._is_active = True
        self.core.emit(f"{self.key}.rotation_toggled", enabled=True)

    def on_disable(self) -> None:
        if self.core is None:
            return
        cr = self.core.get_config(self.key)
        cr["automation_enabled"] = False
        self.core.save_config(self.key, cr)
        self._is_active = False
        self.core.emit(f"{self.key}.rotation_toggled", enabled=False)

    def toggle_rotation(self) -> None:
        self._is_active = not self._is_active
        if self.core is None:
            return
        cr = self.core.get_config(self.key)
        cr["automation_enabled"] = self._is_active
        self.core.save_config(self.key, cr)
        self.core.emit(f"{self.key}.rotation_toggled", enabled=self._is_active)

    def teardown(self) -> None:
        if self._queue_listener is not None:
            self._queue_listener.stop()
            self._queue_listener = None
        self._analyzer = None
