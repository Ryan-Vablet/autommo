"""Cooldown Reader — Main entry point.

Wires together: screen capture → slot analysis → UI + overlay.
"""

from __future__ import annotations

import base64
import json
import logging
import sys
from pathlib import Path

import cv2

from PyQt6.QtCore import QRect, Qt, QThread, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QIcon, QImage
from PyQt6.QtWidgets import QApplication, QMessageBox

from src.automation.binds import normalize_bind
from src.automation.global_hotkey import GlobalToggleListener
from src.automation.key_sender import KeySender
from src.automation.queue_listener import QueueListener
from src.capture import ScreenCapture
from src.analysis import SlotAnalyzer
from src.core import Core, ModuleManager
from src.core.config_manager import ConfigManager
from src.core.config_migration import flatten_config, migrate_config
from src.models import AppConfig, BoundingBox
from src.overlay import CalibrationOverlay
from src.ui import MainWindow
from src.ui.settings_dialog import SettingsDialog

import numpy as np


def encode_baselines(baselines: dict[int, np.ndarray]) -> list[dict]:
    """Encode baselines for JSON: list of {shape: [h, w], data: base64} in slot order."""
    return [
        {"shape": list(ary.shape), "data": base64.b64encode(ary.tobytes()).decode()}
        for i in sorted(baselines.keys())
        for ary in [baselines[i]]
    ]


def decode_baselines(data: list[dict]) -> dict[int, np.ndarray]:
    """Decode baselines from config (list of {shape, data})."""
    result = {}
    for i, d in enumerate(data):
        shape = d.get("shape")
        b64 = d.get("data")
        if shape and b64:
            arr = np.frombuffer(base64.b64decode(b64), dtype=np.uint8)
            result[i] = arr.reshape(shape).copy()
    return result


def encode_gray_template(gray: np.ndarray) -> dict:
    return {
        "shape": [int(gray.shape[0]), int(gray.shape[1])],
        "data": base64.b64encode(gray.astype(np.uint8).tobytes()).decode(),
    }


logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

_SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = _SCRIPT_DIR.parent
# When frozen (e.g. PyInstaller), bundle root is sys._MEIPASS; include cocktus.ico via --add-data
_BASE_PATH = Path(getattr(sys, "_MEIPASS", PROJECT_ROOT))
CONFIG_PATH = PROJECT_ROOT / "config" / "default_config.json"
ICON_PATH = _BASE_PATH / "cocktus.ico"


class CaptureWorker(QThread):
    """Worker thread that captures frames and analyzes them at the configured FPS."""

    frame_captured = pyqtSignal(np.ndarray)  # Raw frame for preview
    state_updated = pyqtSignal(list)  # List of slot state dicts
    buff_state_updated = pyqtSignal(object)  # Dict of buff ROI states
    cast_bar_debug = pyqtSignal(object)  # Live cast-bar ROI motion/status info
    key_action = pyqtSignal(
        object
    )  # Dict when a key was sent or blocked (action, keybind, etc.)

    def __init__(self, analyzer: SlotAnalyzer, config: AppConfig, key_sender=None):
        super().__init__()
        self._analyzer = analyzer
        self._config = config
        self._key_sender = key_sender
        self._queue_listener = None
        self._running = False
        self._capture: ScreenCapture | None = None
        self._active_monitor_index: int | None = None

    def set_queue_listener(self, listener) -> None:
        """Set the spell queue listener so the worker can pass queued override and clear on send."""
        self._queue_listener = listener

    def _start_capture(self, monitor_index: int) -> None:
        self._capture = ScreenCapture(monitor_index=monitor_index)
        self._capture.start()
        self._active_monitor_index = monitor_index

    def _restart_capture(self, monitor_index: int) -> None:
        if self._capture is not None:
            self._capture.stop()
        self._start_capture(monitor_index)
        logger.info(f"Capture worker switched to monitor {monitor_index}")

    def _capture_plan(self, monitor_width: int, monitor_height: int) -> tuple[BoundingBox, tuple[int, int]]:
        """Return capture bbox (expanded for cast ROI and buff ROIs) and action origin inside it."""
        action_bbox = self._config.bounding_box
        left = int(action_bbox.left)
        top = int(action_bbox.top)
        right = left + int(action_bbox.width)
        bottom = top + int(action_bbox.height)

        cast_region = getattr(self._config, "cast_bar_region", {}) or {}
        if bool(cast_region.get("enabled", False)):
            cast_w = int(cast_region.get("width", 0))
            cast_h = int(cast_region.get("height", 0))
            if cast_w > 1 and cast_h > 1:
                cast_left = left + int(cast_region.get("left", 0))
                cast_top = top + int(cast_region.get("top", 0))
                cast_right = cast_left + cast_w
                cast_bottom = cast_top + cast_h
                left = min(left, cast_left)
                top = min(top, cast_top)
                right = max(right, cast_right)
                bottom = max(bottom, cast_bottom)

        # Buff ROIs are relative to action bar and may sit outside action bbox.
        for raw in list(getattr(self._config, "buff_rois", []) or []):
            if not isinstance(raw, dict):
                continue
            if not bool(raw.get("enabled", True)):
                continue
            roi_w = int(raw.get("width", 0))
            roi_h = int(raw.get("height", 0))
            if roi_w <= 1 or roi_h <= 1:
                continue
            roi_left = int(action_bbox.left) + int(raw.get("left", 0))
            roi_top = int(action_bbox.top) + int(raw.get("top", 0))
            roi_right = roi_left + roi_w
            roi_bottom = roi_top + roi_h
            left = min(left, roi_left)
            top = min(top, roi_top)
            right = max(right, roi_right)
            bottom = max(bottom, roi_bottom)

        # Clamp to selected monitor bounds (coords are monitor-relative).
        left = max(0, min(left, monitor_width - 1))
        top = max(0, min(top, monitor_height - 1))
        right = max(left + 1, min(right, monitor_width))
        bottom = max(top + 1, min(bottom, monitor_height))

        capture_bbox = BoundingBox(
            top=top,
            left=left,
            width=max(1, right - left),
            height=max(1, bottom - top),
        )
        action_origin = (action_bbox.left - capture_bbox.left, action_bbox.top - capture_bbox.top)
        return capture_bbox, action_origin

    def run(self) -> None:
        self._running = True
        self._start_capture(self._config.monitor_index)
        try:
            interval = 1.0 / max(1, self._config.polling_fps)
            logger.info(f"Capture worker started at {self._config.polling_fps} FPS")

            while self._running:
                try:
                    if self._active_monitor_index != self._config.monitor_index:
                        self._restart_capture(self._config.monitor_index)
                    monitor = self._capture.monitor_info
                    capture_bbox, action_origin = self._capture_plan(
                        monitor_width=int(monitor["width"]),
                        monitor_height=int(monitor["height"]),
                    )
                    frame = self._capture.grab_region(capture_bbox)
                    ax, ay = action_origin
                    aw = int(self._config.bounding_box.width)
                    ah = int(self._config.bounding_box.height)
                    action_frame = frame[ay:ay + ah, ax:ax + aw]
                    if action_frame.size == 0:
                        action_frame = frame
                    self.frame_captured.emit(action_frame)

                    state = self._analyzer.analyze_frame(frame, action_origin=action_origin)
                    slot_dicts = [
                        {
                            "index": s.index,
                            "state": s.state.value,
                            "keybind": (
                                self._config.keybinds[s.index]
                                if s.index < len(self._config.keybinds)
                                else None
                            ),
                            "cooldown_remaining": s.cooldown_remaining,
                            "cast_progress": s.cast_progress,
                            "cast_ends_at": s.cast_ends_at,
                            "last_cast_start_at": s.last_cast_start_at,
                            "last_cast_success_at": s.last_cast_success_at,
                            "glow_candidate": bool(getattr(s, "glow_candidate", False)),
                            "glow_fraction": float(getattr(s, "glow_fraction", 0.0) or 0.0),
                            "glow_ready": bool(getattr(s, "glow_ready", False)),
                            "yellow_glow_candidate": bool(getattr(s, "yellow_glow_candidate", False)),
                            "yellow_glow_fraction": float(
                                getattr(s, "yellow_glow_fraction", 0.0) or 0.0
                            ),
                            "yellow_glow_ready": bool(getattr(s, "yellow_glow_ready", False)),
                            "red_glow_candidate": bool(getattr(s, "red_glow_candidate", False)),
                            "red_glow_fraction": float(getattr(s, "red_glow_fraction", 0.0) or 0.0),
                            "red_glow_ready": bool(getattr(s, "red_glow_ready", False)),
                            "brightness": s.brightness,
                        }
                        for s in state.slots
                    ]
                    # Snapshot queue at start of tick so priority never replaces it this tick.
                    queued = self._queue_listener.get_queue() if self._queue_listener else None
                    self.state_updated.emit(slot_dicts)
                    buff_states = self._analyzer.buff_states()
                    self.buff_state_updated.emit(buff_states)
                    self.cast_bar_debug.emit(self._analyzer.cast_bar_debug())
                    if self._key_sender is not None:
                        on_queued_sent = (
                            self._queue_listener.clear_queue if self._queue_listener else None
                        )
                        result = self._key_sender.evaluate_and_send(
                            state,
                            self._config.active_priority_items(),
                            self._config.keybinds,
                            self._config.active_manual_actions(),
                            getattr(self._config, "automation_enabled", False),
                            buff_states=buff_states,
                            queued_override=queued,
                            on_queued_sent=on_queued_sent,
                        )
                        if result is not None:
                            self.key_action.emit(result)

                except Exception as e:
                    logger.error(f"Capture error: {e}", exc_info=True)

                self.msleep(int(interval * 1000))
        finally:
            if self._capture is not None:
                self._capture.stop()

    def stop(self) -> None:
        self._running = False
        self.wait()

    def update_config(self, config: AppConfig) -> None:
        self._config = config
        self._analyzer.update_config(config)
        if self._key_sender is not None:
            self._key_sender.update_config(config)


def _capture_plan_from_core(core, monitor_width: int, monitor_height: int) -> tuple[BoundingBox, tuple[int, int]]:
    """Return (capture_bbox, action_origin) from core config (expanded for cast/buff ROIs)."""
    core_cfg = core.get_config("core")
    cr_cfg = core.get_config("cooldown_rotation")
    flat = flatten_config(core_cfg, cr_cfg)
    cfg = AppConfig.from_dict(flat)
    action_bbox = cfg.bounding_box
    left = int(action_bbox.left)
    top = int(action_bbox.top)
    right = left + int(action_bbox.width)
    bottom = top + int(action_bbox.height)
    cast_region = getattr(cfg, "cast_bar_region", {}) or {}
    if bool(cast_region.get("enabled", False)):
        cast_w = int(cast_region.get("width", 0))
        cast_h = int(cast_region.get("height", 0))
        if cast_w > 1 and cast_h > 1:
            cast_left = left + int(cast_region.get("left", 0))
            cast_top = top + int(cast_region.get("top", 0))
            left = min(left, cast_left)
            top = min(top, cast_top)
            right = max(right, cast_left + cast_w)
            bottom = max(bottom, cast_top + cast_h)
    for raw in list(getattr(cfg, "buff_rois", []) or []):
        if not isinstance(raw, dict) or not bool(raw.get("enabled", True)):
            continue
        roi_w, roi_h = int(raw.get("width", 0)), int(raw.get("height", 0))
        if roi_w <= 1 or roi_h <= 1:
            continue
        roi_left = int(action_bbox.left) + int(raw.get("left", 0))
        roi_top = int(action_bbox.top) + int(raw.get("top", 0))
        left = min(left, roi_left)
        top = min(top, roi_top)
        right = max(right, roi_left + roi_w)
        bottom = max(bottom, roi_top + roi_h)
    left = max(0, min(left, monitor_width - 1))
    top = max(0, min(top, monitor_height - 1))
    right = max(left + 1, min(right, monitor_width))
    bottom = max(top + 1, min(bottom, monitor_height))
    capture_bbox = BoundingBox(top=top, left=left, width=max(1, right - left), height=max(1, bottom - top))
    action_origin = (int(action_bbox.left) - capture_bbox.left, int(action_bbox.top) - capture_bbox.top)
    return capture_bbox, action_origin


class ModuleCaptureWorker(QThread):
    """Capture loop: grab frame from core capture, emit preview, set_action_origin, process_frame."""

    frame_captured = pyqtSignal(QImage)  # QImage crosses threads with QueuedConnection; np.ndarray does not

    def __init__(self, core, module_manager):
        super().__init__()
        self._core = core
        self._module_manager = module_manager
        self._running = False
        self._capture = None
        self._active_monitor_index = None

    def _start_capture(self, monitor_index: int) -> None:
        self._capture = ScreenCapture(monitor_index=monitor_index)
        self._capture.start()
        self._active_monitor_index = monitor_index

    def run(self) -> None:
        self._running = True
        # #region agent log
        try:
            import time
            with open(Path(__file__).resolve().parent.parent / "debug-6d0385.log", "a") as _f:
                _f.write(json.dumps({"sessionId": "6d0385", "hypothesisId": "H3", "location": "main.py:ModuleCaptureWorker.run", "message": "Worker run() entered", "data": {"running": self._running}, "timestamp": int(time.time() * 1000)}) + "\n")
        except Exception:
            pass
        # #endregion
        core_cfg = self._core.get_config("core")
        monitor_index = int(core_cfg.get("monitor_index", 1))
        self._start_capture(monitor_index)
        polling_fps = 20
        try:
            cr_cfg = self._core.get_config("cooldown_rotation")
            polling_fps = max(1, min(240, int(cr_cfg.get("polling_fps", 20))))
        except Exception:
            pass
        interval = 1.0 / max(1, polling_fps)
        logger.info("Module capture worker started at %s FPS", polling_fps)
        cooldown_module = self._module_manager.get("cooldown_rotation")
        _first_frame_logged = [False]
        try:
            while self._running:
                try:
                    core_cfg = self._core.get_config("core")
                    mid = int(core_cfg.get("monitor_index", 1))
                    if self._active_monitor_index != mid:
                        if self._capture is not None:
                            self._capture.stop()
                        self._start_capture(mid)
                    monitor = self._capture.monitor_info
                    capture_bbox, action_origin = _capture_plan_from_core(
                        self._core,
                        int(monitor["width"]),
                        int(monitor["height"]),
                    )
                    frame = self._capture.grab_region(capture_bbox)
                    ax, ay = action_origin
                    if cooldown_module is not None and hasattr(cooldown_module, "set_action_origin"):
                        cooldown_module.set_action_origin(ax, ay)
                    # Emit QImage so QueuedConnection can marshal it to the main thread (np.ndarray cannot)
                    h, w, ch = frame.shape
                    rgb = frame[:, :, ::-1].copy()
                    qimg = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888).copy()
                    self.frame_captured.emit(qimg)
                    if not _first_frame_logged[0]:
                        _first_frame_logged[0] = True
                        # #region agent log
                        try:
                            import time
                            with open(Path(__file__).resolve().parent.parent / "debug-6d0385.log", "a") as _f:
                                _f.write(json.dumps({"sessionId": "6d0385", "hypothesisId": "H3", "location": "main.py:ModuleCaptureWorker.run", "message": "First frame_captured emitted (QImage)", "data": {"w": w, "h": h}, "timestamp": int(time.time() * 1000)}) + "\n")
                        except Exception:
                            pass
                        # #endregion
                    self._module_manager.process_frame(frame)
                except Exception as e:
                    logger.error("Module capture error: %s", e, exc_info=True)
                self.msleep(int(interval * 1000))
        finally:
            if self._capture is not None:
                self._capture.stop()

    def stop(self) -> None:
        self._running = False
        self.wait()


def load_config() -> AppConfig:
    """Load config from JSON, falling back to defaults."""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            data = json.load(f)
        logger.info(f"Loaded config from {CONFIG_PATH}")
        return AppConfig.from_dict(data)
    logger.warning(f"Config not found at {CONFIG_PATH}, using defaults")
    return AppConfig()


def monitor_rect_for_index(monitor_index: int, monitors: list[dict]) -> QRect:
    """Resolve a monitor index (1-based) to a QRect, with safe fallback."""
    if monitors:
        idx = min(max(1, monitor_index), len(monitors)) - 1
        m = monitors[idx]
        return QRect(m["left"], m["top"], m["width"], m["height"])
    return QRect(0, 0, 1920, 1080)


def main() -> None:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    if ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(ICON_PATH)))

    # --- Config: load (with migration), Core, ModuleManager ---
    config_manager = ConfigManager(CONFIG_PATH)
    config_manager.load_from_file()
    root = config_manager.get_root()
    # #region agent log
    try:
        import time
        with open(PROJECT_ROOT / "debug-6d0385.log", "a") as _f:
            _f.write(json.dumps({"sessionId": "6d0385", "hypothesisId": "H1", "location": "main.py:main", "message": "After load_from_file", "data": {"root_keys": list(root.keys()), "core_keys": list((root.get("core") or {}).keys()), "has_cr": "cooldown_rotation" in root}, "timestamp": int(time.time() * 1000)}) + "\n")
    except Exception:
        pass
    # #endregion
    core_cfg = root.get("core") or {}
    cr_cfg = root.get("cooldown_rotation") or {}
    flat = flatten_config(core_cfg, cr_cfg)
    initial_app_config = AppConfig.from_dict(flat)
    initial_app_config.automation_enabled = False

    capture = ScreenCapture(monitor_index=int(core_cfg.get("monitor_index", 1)))
    key_sender = KeySender(initial_app_config)
    core = Core(config_manager, capture, key_sender)
    module_manager = ModuleManager(core)
    module_manager.discover(PROJECT_ROOT / "modules")
    modules_enabled = core_cfg.get("modules_enabled") or ["cooldown_rotation"]
    module_manager.load(modules_enabled)

    cooldown_module = module_manager.get("cooldown_rotation")
    if cooldown_module is not None and hasattr(cooldown_module, "get_analyzer"):
        analyzer = cooldown_module.get_analyzer()
        if analyzer is not None and cr_cfg.get("slot_baselines"):
            try:
                decoded = decode_baselines(cr_cfg["slot_baselines"])
                if decoded:
                    analyzer.set_baselines(decoded)
            except Exception as e:
                logger.warning("Could not load saved baselines: %s", e)

    # --- Main window and settings dialog ---
    window = MainWindow(core, module_manager)

    def sync_baselines_to_config() -> None:
        if cooldown_module is None:
            return
        ana = cooldown_module.get_analyzer()
        if ana is None:
            return
        cr = core.get_config("cooldown_rotation")
        cr = dict(cr)
        cr["slot_baselines"] = encode_baselines(ana.get_baselines())
        core.save_config("cooldown_rotation", cr)

    window.set_before_save_callback(sync_baselines_to_config)
    settings_dialog = SettingsDialog(core, module_manager, before_save_callback=sync_baselines_to_config, parent=window)

    capture.start()
    monitors = capture.list_monitors()
    settings_dialog.populate_monitors(monitors)
    if core_cfg.get("display", {}).get("always_on_top", False):
        window.setWindowFlags(window.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
    window.show()
    capture.stop()

    # --- Overlay ---
    monitor_rect = monitor_rect_for_index(int(core_cfg.get("monitor_index", 1)), monitors)
    overlay = CalibrationOverlay(monitor_geometry=monitor_rect)
    bb = core_cfg.get("bounding_box") or {}
    overlay.update_bounding_box(BoundingBox(top=bb.get("top", 0), left=bb.get("left", 0), width=bb.get("width", 0), height=bb.get("height", 0)))
    overlay.update_cast_bar_region(cr_cfg.get("cast_bar_region") or {})
    overlay.update_buff_rois(cr_cfg.get("buff_rois") or [])
    overlay.update_show_active_screen_outline((core_cfg.get("overlay") or {}).get("show_active_screen_outline", False))
    if (core_cfg.get("overlay") or {}).get("enabled", False):
        overlay.show()

    # --- Worker: module capture loop ---
    worker = ModuleCaptureWorker(core, module_manager)

    def _app_config_from_root() -> AppConfig:
        return AppConfig.from_dict(flatten_config(core.get_config("core"), core.get_config("cooldown_rotation")))

    def on_config_updated(_root) -> None:
        cfg = _app_config_from_root()
        key_sender.update_config(cfg)
        if cooldown_module is not None and hasattr(cooldown_module, "update_analyzer_config"):
            cooldown_module.update_analyzer_config()
        c_cfg = (_root or {}).get("core") or {}
        cr_cfg_new = (_root or {}).get("cooldown_rotation") or {}
        overlay.update_cast_bar_region(cr_cfg_new.get("cast_bar_region") or {})
        overlay.update_buff_rois(cr_cfg_new.get("buff_rois") or [])
        overlay.update_bounding_box(cfg.bounding_box)
        slots = c_cfg.get("slots") or {}
        overlay.update_slot_layout(
            int(slots.get("count", 12)),
            int(slots.get("gap_pixels", 0)),
            int(slots.get("padding", 0)),
        )
        overlay.update_monitor_geometry(monitor_rect_for_index(cfg.monitor_index, monitors))
        overlay.update_show_active_screen_outline((c_cfg.get("overlay") or {}).get("show_active_screen_outline", False))
        if (c_cfg.get("overlay") or {}).get("enabled", True):
            overlay.show()
        else:
            overlay.hide()
        window.refresh_from_config()
        flags = window.windowFlags()
        if (c_cfg.get("display") or {}).get("always_on_top", False):
            window.setWindowFlags(flags | Qt.WindowType.WindowStaysOnTopHint)
        else:
            window.setWindowFlags(flags & ~Qt.WindowType.WindowStaysOnTopHint)
        window.show()

    def apply_overlay_visibility(visible: bool) -> None:
        overlay.show() if visible else overlay.hide()

    def apply_monitor(monitor_index: int) -> None:
        overlay.update_monitor_geometry(monitor_rect_for_index(monitor_index, monitors))

    settings_dialog.bounding_box_changed.connect(overlay.update_bounding_box)
    settings_dialog.slot_layout_changed.connect(overlay.update_slot_layout)
    settings_dialog.overlay_visibility_changed.connect(apply_overlay_visibility)
    settings_dialog.monitor_changed.connect(apply_monitor)
    settings_dialog.config_updated.connect(on_config_updated)
    window.config_updated.connect(on_config_updated)

    # Module signals -> window and overlay (QueuedConnection for thread safety)
    if cooldown_module is not None:
        cooldown_module.slot_states_updated_signal.connect(window.update_slot_states, Qt.ConnectionType.QueuedConnection)
        cooldown_module.slot_states_updated_signal.connect(overlay.update_slot_states, Qt.ConnectionType.QueuedConnection)
        cooldown_module.buff_state_updated_signal.connect(window.update_buff_states, Qt.ConnectionType.QueuedConnection)
        cooldown_module.buff_state_updated_signal.connect(overlay.update_buff_states, Qt.ConnectionType.QueuedConnection)
        cooldown_module.cast_bar_debug_signal.connect(window.update_cast_bar_debug, Qt.ConnectionType.QueuedConnection)
        cooldown_module.key_action_signal.connect(
            lambda result: _on_key_action(result, window, core),
            Qt.ConnectionType.QueuedConnection,
        )
        ql = cooldown_module.get_queue_listener()
        if ql is not None and hasattr(ql, "queue_updated"):
            ql.queue_updated.connect(window.set_queued_override, Qt.ConnectionType.QueuedConnection)

    worker.frame_captured.connect(window.update_preview, Qt.ConnectionType.QueuedConnection)

    def _on_key_action(result: dict, win, c) -> None:
        cfg = _app_config_from_root()
        names = getattr(cfg, "slot_display_names", []) or []
        slot_index = result.get("slot_index")
        item_type = str(result.get("item_type", "") or "").strip().lower()
        display_name = str(result.get("display_name", "") or "").strip() or "Unidentified"
        if item_type == "slot" and slot_index is not None and slot_index < len(names) and (names[slot_index] or "").strip():
            display_name = (names[slot_index] or "").strip()
        if result.get("action") == "sent":
            win.record_last_action_sent(result["keybind"], result.get("timestamp", 0.0), display_name)
        elif result.get("action") == "blocked" and result.get("reason") == "window":
            win.set_next_intention_blocked(result["keybind"], display_name)
        elif result.get("action") == "blocked" and result.get("reason") == "casting":
            win.set_next_intention_casting_wait(slot_index=result.get("slot_index"), cast_ends_at=result.get("cast_ends_at"))

    slots = core_cfg.get("slots") or {}
    overlay.update_slot_layout(int(slots.get("count", 12)), int(slots.get("gap_pixels", 0)), int(slots.get("padding", 0)))

    is_running = [False]

    def toggle_capture():
        if is_running[0]:
            worker.stop()
            window._btn_start.setText("▶ Start Capture")
            window.set_capture_running(False)
            overlay.set_capture_active(False)
            is_running[0] = False
        else:
            worker.start()
            window._btn_start.setText("⏹ Stop Capture")
            window.set_capture_running(True)
            overlay.set_capture_active(True)
            is_running[0] = True

    window._btn_start.clicked.connect(toggle_capture)

    def on_start_capture_requested():
        if not is_running[0]:
            worker.start()
            window._btn_start.setText("⏹ Stop Capture")
            window.set_capture_running(True)
            overlay.set_capture_active(True)
            is_running[0] = True

    window.start_capture_requested.connect(on_start_capture_requested)

    def all_profile_binds() -> list[str]:
        binds = []
        cfg = core.get_config("cooldown_rotation")
        for p in (cfg.get("priority_profiles") or []):
            toggle_bind = normalize_bind(str(p.get("toggle_bind", "") or ""))
            single_fire_bind = normalize_bind(str(p.get("single_fire_bind", "") or ""))
            if toggle_bind:
                binds.append(toggle_bind)
            if single_fire_bind:
                binds.append(single_fire_bind)
        return binds

    def on_hotkey_triggered(triggered_bind: str):
        bind = normalize_bind(triggered_bind or "")
        if not bind:
            return
        profiles = (core.get_config("cooldown_rotation") or {}).get("priority_profiles") or []
        matched_profile = None
        matched_action = None
        for p in profiles:
            if bind == normalize_bind(str(p.get("toggle_bind", "") or "")):
                matched_profile = p
                matched_action = "toggle"
                break
            if bind == normalize_bind(str(p.get("single_fire_bind", "") or "")):
                matched_profile = p
                matched_action = "single_fire"
                break
        if not matched_profile or not matched_action:
            return
        profile_id = str(matched_profile.get("id", "") or "").strip().lower()
        profile_name = str(matched_profile.get("name", "") or "").strip() or "Profile"
        cfg = core.get_config("cooldown_rotation")
        active_id = (cfg.get("active_priority_profile_id") or "").strip().lower()
        if profile_id != active_id:
            cfg = dict(cfg)
            cfg["active_priority_profile_id"] = profile_id
            core.save_config("cooldown_rotation", cfg)
            window.set_active_priority_profile(profile_id, persist=True)
            window.show_status_message(f"Profile: {profile_name}", 1200)
        if matched_action == "single_fire":
            key_sender.request_single_fire()
            window.show_status_message(f"Single-fire armed ({profile_name})", 1200)
            return
        window.toggle_automation()

    hotkey_listener = GlobalToggleListener(get_binds=all_profile_binds)
    hotkey_listener.triggered.connect(on_hotkey_triggered)
    hotkey_listener.start()

    def revert_calibrate_button(btn):
        btn.setText("Calibrate All Baselines")
        btn.setStyleSheet("")

    def calibrate_baselines(button_to_update):
        btn = button_to_update
        if cooldown_module is None:
            return
        ana = cooldown_module.get_analyzer()
        if ana is None:
            return
        baselines = ana.get_baselines()
        if baselines:
            reply = QMessageBox.question(
                window, "Recalibrate all slots?",
                "You already have baselines set. Recalibrate all slots? This will replace existing baselines.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        try:
            cap = ScreenCapture(monitor_index=int(core_cfg.get("monitor_index", 1)))
            cap.start()
            bbox = core_cfg.get("bounding_box") or {}
            frame = cap.grab_region(BoundingBox(top=bbox.get("top", 0), left=bbox.get("left", 0), width=bbox.get("width", 0), height=bbox.get("height", 0)))
            cap.stop()
            ana.calibrate_baselines(frame)
            logger.info("Baselines calibrated from current frame")
            sync_baselines_to_config()
            window.clear_overwritten_baseline_slots()
            btn.setText("Calibrated ✓")
            btn.setStyleSheet("")
            QTimer.singleShot(2000, lambda: revert_calibrate_button(btn))
        except Exception as e:
            logger.error("Calibration failed: %s", e)
            btn.setText("Calibration Failed")
            btn.setStyleSheet("color: red;")
            QTimer.singleShot(2000, lambda: revert_calibrate_button(btn))

    settings_dialog.calibrate_requested.connect(lambda: calibrate_baselines(settings_dialog._btn_calibrate))

    def calibrate_buff_roi_present(roi_id: str) -> None:
        rid = str(roi_id or "").strip().lower()
        if not rid:
            return
        cr = core.get_config("cooldown_rotation")
        rois = [dict(r) for r in (cr.get("buff_rois") or []) if isinstance(r, dict)]
        roi = next((r for r in rois if str(r.get("id", "") or "").strip().lower() == rid), None)
        if roi is None:
            window.show_status_message(f"Buff ROI not found: {rid}", 2000)
            return
        c_cfg = core.get_config("core")
        bb = c_cfg.get("bounding_box") or {}
        action_left, action_top = int(bb.get("left", 0)), int(bb.get("top", 0))
        roi_left = int(roi.get("left", 0))
        roi_top = int(roi.get("top", 0))
        roi_width = int(roi.get("width", 0))
        roi_height = int(roi.get("height", 0))
        if roi_width <= 1 or roi_height <= 1:
            window.show_status_message("Buff ROI size must be > 1x1", 2000)
            return
        try:
            cap = ScreenCapture(monitor_index=int(c_cfg.get("monitor_index", 1)))
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
                window.show_status_message("Buff ROI is out of capture frame", 2000)
                return
            crop = frame[y1:y2, x1:x2]
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            calibration = dict(roi.get("calibration") or {})
            calibration["present_template"] = encode_gray_template(gray)
            roi["calibration"] = calibration
            cr = dict(core.get_config("cooldown_rotation"))
            cr["buff_rois"] = rois
            core.save_config("cooldown_rotation", cr)
            settings_dialog.sync_from_config()
            on_config_updated(core.get_root_config())
            window.show_status_message(f"Buff '{roi.get('name', rid)}' present calibrated", 2000)
        except Exception as e:
            logger.error("Buff calibration failed: %s", e, exc_info=True)
            window.show_status_message(f"Buff calibration failed: {e}", 2000)

    settings_dialog.calibrate_buff_present_requested.connect(calibrate_buff_roi_present)

    def calibrate_single_slot(slot_index: int) -> None:
        if cooldown_module is None:
            return
        ana = cooldown_module.get_analyzer()
        if ana is None:
            return
        try:
            cap = ScreenCapture(monitor_index=int(core_cfg.get("monitor_index", 1)))
            cap.start()
            bb = core_cfg.get("bounding_box") or {}
            frame = cap.grab_region(BoundingBox(top=bb.get("top", 0), left=bb.get("left", 0), width=bb.get("width", 0), height=bb.get("height", 0)))
            cap.stop()
            ana.calibrate_single_slot(frame, slot_index)
            window.mark_slot_recalibrated(slot_index)
            window.show_status_message(f"Slot {slot_index + 1} calibrated ✓", 2000)
        except Exception as e:
            logger.error("Per-slot calibration failed: %s", e)
            window.show_status_message(f"Calibration failed: {e}", 2000)

    window.calibrate_slot_requested.connect(calibrate_single_slot)
    window._btn_settings.clicked.connect(settings_dialog.show_or_raise)

    exit_code = app.exec()
    hotkey_listener.stop()
    if is_running[0]:
        worker.stop()
    module_manager.shutdown()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
