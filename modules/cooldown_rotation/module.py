"""Cooldown Rotation module: slot analysis, priority, queue, key sending. Runs on_frame in capture thread; emits signals for GUI updates."""

from __future__ import annotations

import base64
import logging
from abc import ABCMeta
from typing import Any, Optional

import cv2
import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal

from src.automation.binds import normalize_bind
from src.capture import ScreenCapture
from src.core.base_module import BaseModule
from src.core.config_migration import flatten_config
from src.models import AppConfig, BoundingBox
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

    @staticmethod
    def _encode_baselines(baselines: dict[int, np.ndarray]) -> list[dict]:
        """Encode baselines for JSON: list of {shape: [h, w], data: base64} in slot order."""
        return [
            {"shape": list(ary.shape), "data": base64.b64encode(ary.tobytes()).decode()}
            for i in sorted(baselines.keys())
            for ary in [baselines[i]]
        ]

    @staticmethod
    def _decode_baselines(data: list[dict]) -> dict[int, np.ndarray]:
        """Decode baselines from config (list of {shape, data})."""
        result: dict[int, np.ndarray] = {}
        for i, d in enumerate(data):
            shape = d.get("shape")
            b64 = d.get("data")
            if shape and b64:
                arr = np.frombuffer(base64.b64decode(b64), dtype=np.uint8)
                result[i] = arr.reshape(shape).copy()
        return result

    @staticmethod
    def _encode_gray_template(gray: np.ndarray) -> dict:
        """Encode a grayscale template for JSON."""
        return {
            "shape": [int(gray.shape[0]), int(gray.shape[1])],
            "data": base64.b64encode(gray.astype(np.uint8).tobytes()).decode(),
        }

    def _sync_baselines_to_config(self) -> None:
        """Encode current analyzer baselines into config."""
        if self.core is None or self._analyzer is None:
            return
        cr = dict(self.core.get_config(self.key))
        cr["slot_baselines"] = self._encode_baselines(self._analyzer.get_baselines())
        self.core.save_config(self.key, cr)

    def sync_baselines_to_config(self) -> None:
        """Public hook for main/window before-save: persist baselines to config."""
        self._sync_baselines_to_config()

    def has_baselines(self) -> bool:
        """Return True if the analyzer has any saved baselines (for confirm-before-recalibrate)."""
        return bool(self._analyzer and self._analyzer.get_baselines())

    def ready(self) -> None:
        """Load saved baselines from config after setup."""
        if self._analyzer is None or self.core is None:
            return
        cr_cfg = self.core.get_config(self.key)
        saved = cr_cfg.get("slot_baselines")
        if saved:
            try:
                decoded = self._decode_baselines(saved)
                if decoded:
                    self._analyzer.set_baselines(decoded)
            except Exception as e:
                logger.warning("Could not load saved baselines: %s", e)

    def calibrate_all_baselines(self) -> tuple[bool, str]:
        """Calibrate all slot baselines from a fresh screen capture. Returns (success, message)."""
        if self.core is None or self._analyzer is None:
            return False, "Module not ready"
        core_cfg = self.core.get_config("core")
        try:
            cap = ScreenCapture(monitor_index=int(core_cfg.get("monitor_index", 1)))
            cap.start()
            bb = core_cfg.get("bounding_box") or {}
            frame = cap.grab_region(BoundingBox(
                top=bb.get("top", 0), left=bb.get("left", 0),
                width=bb.get("width", 0), height=bb.get("height", 0),
            ))
            cap.stop()
            self._analyzer.calibrate_baselines(frame)
            logger.info("Baselines calibrated from current frame")
            self._sync_baselines_to_config()
            return True, "Calibrated ✓"
        except Exception as e:
            logger.error("Calibration failed: %s", e)
            return False, str(e)

    def calibrate_single_slot(self, slot_index: int) -> tuple[bool, str]:
        """Calibrate one slot baseline. Returns (success, message)."""
        if self.core is None or self._analyzer is None:
            return False, "Module not ready"
        core_cfg = self.core.get_config("core")
        try:
            cap = ScreenCapture(monitor_index=int(core_cfg.get("monitor_index", 1)))
            cap.start()
            bb = core_cfg.get("bounding_box") or {}
            frame = cap.grab_region(BoundingBox(
                top=bb.get("top", 0), left=bb.get("left", 0),
                width=bb.get("width", 0), height=bb.get("height", 0),
            ))
            cap.stop()
            self._analyzer.calibrate_single_slot(frame, slot_index)
            self._sync_baselines_to_config()
            return True, f"Slot {slot_index + 1} calibrated ✓"
        except Exception as e:
            logger.error("Per-slot calibration failed: %s", e)
            return False, str(e)

    def calibrate_buff_roi_present(self, roi_id: str) -> tuple[bool, str]:
        """Calibrate a buff ROI present template. Returns (success, message)."""
        rid = str(roi_id or "").strip().lower()
        if not rid:
            return False, "No ROI id"
        if self.core is None:
            return False, "Module not ready"
        cr = self.core.get_config(self.key)
        rois = [dict(r) for r in (cr.get("buff_rois") or []) if isinstance(r, dict)]
        roi = next((r for r in rois if str(r.get("id", "") or "").strip().lower() == rid), None)
        if roi is None:
            return False, f"Buff ROI not found: {rid}"
        core_cfg = self.core.get_config("core")
        bb = core_cfg.get("bounding_box") or {}
        action_left, action_top = int(bb.get("left", 0)), int(bb.get("top", 0))
        roi_left = int(roi.get("left", 0))
        roi_top = int(roi.get("top", 0))
        roi_width = int(roi.get("width", 0))
        roi_height = int(roi.get("height", 0))
        if roi_width <= 1 or roi_height <= 1:
            return False, "Buff ROI size must be > 1x1"
        try:
            cap = ScreenCapture(monitor_index=int(core_cfg.get("monitor_index", 1)))
            cap.start()
            monitor = cap.monitor_info
            mw, mh = int(monitor["width"]), int(monitor["height"])
            left = min(action_left, action_left + roi_left)
            top = min(action_top, action_top + roi_top)
            right = max(action_left + int(bb.get("width", 0)), action_left + roi_left + roi_width)
            bottom = max(action_top + int(bb.get("height", 0)), action_top + roi_top + roi_height)
            left = max(0, min(left, mw - 1))
            top = max(0, min(top, mh - 1))
            right = max(left + 1, min(right, mw))
            bottom = max(top + 1, min(bottom, mh))
            bbox = BoundingBox(top=top, left=left, width=right - left, height=bottom - top)
            frame = cap.grab_region(bbox)
            cap.stop()
            action_origin = (action_left - left, action_top - top)
            x1 = action_origin[0] + roi_left
            y1 = action_origin[1] + roi_top
            x2, y2 = x1 + roi_width, y1 + roi_height
            if x1 < 0 or y1 < 0 or x2 > frame.shape[1] or y2 > frame.shape[0]:
                return False, "Buff ROI is out of capture frame"
            crop = frame[y1:y2, x1:x2]
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            calibration = dict(roi.get("calibration") or {})
            calibration["present_template"] = self._encode_gray_template(gray)
            roi["calibration"] = calibration
            cr = dict(self.core.get_config(self.key))
            cr["buff_rois"] = rois
            self.core.save_config(self.key, cr)
            return True, f"Buff '{roi.get('name', rid)}' present calibrated"
        except Exception as e:
            logger.error("Buff calibration failed: %s", e, exc_info=True)
            return False, str(e)

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
                result = dict(result)
                result.setdefault("module_key", self.key)
                result.setdefault("display_name", "Unidentified")
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

    def get_hotkey_binds(self) -> list[dict]:
        """Return list of hotkey definitions this module wants registered.
        Each dict: {"bind": "f24", "action": "toggle"|"single_fire", "profile_id": "...", "profile_name": "..."}
        """
        if self.core is None:
            return []
        cr = self.core.get_config(self.key)
        result: list[dict] = []
        for p in (cr.get("priority_profiles") or []):
            if not isinstance(p, dict):
                continue
            pid = str(p.get("id", "") or "").strip()
            pname = str(p.get("name", "") or "").strip() or "Profile"
            toggle = normalize_bind(str(p.get("toggle_bind", "") or ""))
            single = normalize_bind(str(p.get("single_fire_bind", "") or ""))
            if toggle:
                result.append({"bind": toggle, "action": "toggle", "profile_id": pid, "profile_name": pname})
            if single:
                result.append({"bind": single, "action": "single_fire", "profile_id": pid, "profile_name": pname})
        return result

    def handle_hotkey(self, bind_info: dict) -> None:
        """Handle a triggered hotkey. bind_info is one of the dicts from get_hotkey_binds()."""
        if self.core is None:
            return
        action = bind_info.get("action")
        profile_id = str(bind_info.get("profile_id", "") or "").strip()
        cr = self.core.get_config(self.key)
        active_id = (str(cr.get("active_priority_profile_id") or "").strip()).lower()
        if profile_id.lower() != active_id:
            cr = dict(cr)
            cr["active_priority_profile_id"] = profile_id
            self.core.save_config(self.key, cr)
        if action == "single_fire":
            key_sender = self.core.get_key_sender()
            if key_sender and hasattr(key_sender, "request_single_fire"):
                key_sender.request_single_fire()
        elif action == "toggle":
            self.toggle_rotation()

    def set_active_priority_profile(self, profile_id: str) -> None:
        """Set the active priority profile (e.g. when user switches in UI)."""
        if self.core is None:
            return
        pid = (profile_id or "").strip().lower()
        if not pid:
            return
        cr = self.core.get_config(self.key)
        profiles = cr.get("priority_profiles") or []
        if not any(isinstance(p, dict) and (str(p.get("id") or "").strip().lower() == pid) for p in profiles):
            return
        cr = dict(cr)
        cr["active_priority_profile_id"] = pid
        self.core.save_config(self.key, cr)

    def get_active_profile_display(self) -> dict:
        """Return dict with toggle_bind, single_fire_bind, profile_name for the active profile (for UI bind display)."""
        out = {"toggle_bind": "", "single_fire_bind": "", "profile_name": "Default"}
        if self.core is None:
            return out
        cr = self.core.get_config(self.key)
        active_id = (str(cr.get("active_priority_profile_id") or "").strip()).lower()
        for p in (cr.get("priority_profiles") or []):
            if not isinstance(p, dict):
                continue
            if (str(p.get("id") or "").strip().lower() == active_id):
                out["profile_name"] = (str(p.get("name", "") or "").strip()) or "Default"
                out["toggle_bind"] = str(p.get("toggle_bind", "") or "").strip()
                out["single_fire_bind"] = str(p.get("single_fire_bind", "") or "").strip()
                break
        return out

    def mark_slot_recalibrated(self, slot_index: int) -> None:
        """Record that a slot was recalibrated (for overwritten_baseline_slots)."""
        if self.core is None:
            return
        cr = self.core.get_config(self.key)
        overwritten = list(cr.get("overwritten_baseline_slots") or [])
        if slot_index not in overwritten:
            overwritten.append(slot_index)
            cr = dict(cr)
            cr["overwritten_baseline_slots"] = overwritten
            self.core.save_config(self.key, cr)

    def clear_overwritten_baseline_slots(self) -> None:
        """Clear the overwritten_baseline_slots list (e.g. after full recalibrate)."""
        if self.core is None:
            return
        cr = dict(self.core.get_config(self.key))
        cr["overwritten_baseline_slots"] = []
        self.core.save_config(self.key, cr)

    def teardown(self) -> None:
        if self._queue_listener is not None:
            self._queue_listener.stop()
            self._queue_listener = None
        self._analyzer = None
