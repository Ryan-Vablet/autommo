"""Cooldown Reader — Main entry point.

Wires together: screen capture → slot analysis → UI + overlay.
"""

from __future__ import annotations

import json
from typing import Optional
import logging
import sys
from pathlib import Path

from PyQt6.QtCore import QRect, Qt, QThread, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QIcon, QImage
from PyQt6.QtWidgets import QApplication, QMessageBox

from src.automation.binds import normalize_bind
from src.automation.global_hotkey import GlobalToggleListener
from src.automation.key_sender import KeySender
from src.capture import ScreenCapture
from src.core import Core, ModuleManager
from src.core.config_manager import ConfigManager
from src.core.config_migration import flatten_config, migrate_config
from src.models import AppConfig, BoundingBox
from src.overlay import CalibrationOverlay
from src.ui import MainWindow
from src.ui.settings_dialog import SettingsDialog

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


def _capture_plan_from_core(core, monitor_width: int, monitor_height: int, module_key: Optional[str] = None) -> tuple[BoundingBox, tuple[int, int]]:
    """Return (capture_bbox, action_origin) from core config (expanded for cast/buff ROIs). TODO: Phase 2 — generic module config."""
    core_cfg = core.get_config("core")
    cr_cfg = core.get_config(module_key) if module_key else {}
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
        core_cfg = self._core.get_config("core")
        monitor_index = int(core_cfg.get("monitor_index", 1))
        self._start_capture(monitor_index)
        polling_fps = 20
        try:
            first_key = self._module_manager._load_order[0] if self._module_manager._load_order else None
            cr_cfg = self._core.get_config(first_key) or {} if first_key else {}
            polling_fps = max(1, min(240, int(cr_cfg.get("polling_fps", 20))))
        except Exception:
            pass
        interval = 1.0 / max(1, polling_fps)
        logger.info("Module capture worker started at %s FPS", polling_fps)
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
                    first_key = self._module_manager._load_order[0] if self._module_manager._load_order else None
                    capture_bbox, action_origin = _capture_plan_from_core(
                        self._core,
                        int(monitor["width"]),
                        int(monitor["height"]),
                        first_key,
                    )
                    frame = self._capture.grab_region(capture_bbox)
                    ax, ay = action_origin
                    for key in self._module_manager._load_order:
                        mod = self._module_manager.modules.get(key)
                        if mod and hasattr(mod, "set_action_origin"):
                            mod.set_action_origin(ax, ay)
                    # Emit QImage so QueuedConnection can marshal it to the main thread (np.ndarray cannot)
                    h, w, ch = frame.shape
                    rgb = frame[:, :, ::-1].copy()
                    qimg = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888).copy()
                    self.frame_captured.emit(qimg)
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
    core_cfg = root.get("core") or {}
    module_keys_in_config = [k for k in root if k != "core"]
    first_module_key = module_keys_in_config[0] if module_keys_in_config else None
    cr_cfg = root.get(first_module_key) or {} if first_module_key else {}
    flat = flatten_config(core_cfg, cr_cfg)
    initial_app_config = AppConfig.from_dict(flat)
    initial_app_config.automation_enabled = False

    capture = ScreenCapture(monitor_index=int(core_cfg.get("monitor_index", 1)))
    key_sender = KeySender(initial_app_config)
    core = Core(config_manager, capture, key_sender)
    module_manager = ModuleManager(core)
    module_manager.discover(PROJECT_ROOT / "modules")
    modules_enabled = core_cfg.get("modules_enabled") or list(module_manager._discovered.keys())
    module_manager.load(modules_enabled)

    # --- Main window and settings dialog ---
    window = MainWindow(core, module_manager)

    def before_save_callback() -> None:
        for key in module_manager._load_order:
            mod = module_manager.modules.get(key)
            if mod and hasattr(mod, "sync_baselines_to_config"):
                mod.sync_baselines_to_config()

    window.set_before_save_callback(before_save_callback)
    settings_dialog = SettingsDialog(core, module_manager, before_save_callback=before_save_callback, parent=window)

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
        first_key = module_manager._load_order[0] if module_manager._load_order else None
        cr = core.get_config(first_key) if first_key else {}
        return AppConfig.from_dict(flatten_config(core.get_config("core"), cr))

    def on_config_updated(_root) -> None:
        cfg = _app_config_from_root()
        key_sender.update_config(cfg)
        for key in module_manager._load_order:
            mod = module_manager.modules.get(key)
            if mod and hasattr(mod, "update_analyzer_config"):
                mod.update_analyzer_config()
        c_cfg = (_root or {}).get("core") or {}
        first_key = module_manager._load_order[0] if module_manager._load_order else None
        cr_cfg_new = (_root or {}).get(first_key) or {} if first_key else {}
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
    for key in module_manager._load_order:
        mod = module_manager.modules.get(key)
        if mod is None:
            continue
        if hasattr(mod, "slot_states_updated_signal"):
            mod.slot_states_updated_signal.connect(window.update_slot_states, Qt.ConnectionType.QueuedConnection)
            mod.slot_states_updated_signal.connect(overlay.update_slot_states, Qt.ConnectionType.QueuedConnection)
        if hasattr(mod, "buff_state_updated_signal"):
            mod.buff_state_updated_signal.connect(window.update_buff_states, Qt.ConnectionType.QueuedConnection)
            mod.buff_state_updated_signal.connect(overlay.update_buff_states, Qt.ConnectionType.QueuedConnection)
        if hasattr(mod, "cast_bar_debug_signal"):
            mod.cast_bar_debug_signal.connect(window.update_cast_bar_debug, Qt.ConnectionType.QueuedConnection)
        if hasattr(mod, "key_action_signal"):
            mod.key_action_signal.connect(
                lambda result, m=mod: _on_key_action(result, window, core, m),
                Qt.ConnectionType.QueuedConnection,
            )
        if hasattr(mod, "get_queue_listener"):
            ql = mod.get_queue_listener()
            if ql is not None and hasattr(ql, "queue_updated"):
                ql.queue_updated.connect(window.set_queued_override, Qt.ConnectionType.QueuedConnection)

    worker.frame_captured.connect(window.update_preview, Qt.ConnectionType.QueuedConnection)

    def _on_key_action(result: dict, win, c, module=None) -> None:
        display_name = str(result.get("display_name", "") or "").strip() or "Unidentified"
        if result.get("action") == "sent":
            win.record_last_action_sent(result["keybind"], result.get("timestamp", 0.0), display_name)
        elif result.get("action") == "blocked" and result.get("reason") == "window":
            win.set_next_intention_blocked(result["keybind"], display_name)
        elif result.get("action") == "blocked" and result.get("reason") == "casting":
            win.set_next_intention_casting_wait(slot_index=result.get("slot_index"), cast_ends_at=result.get("cast_ends_at"))

    # TODO: Phase 2 — eliminate AppConfig and flatten_config two-namespace assumption
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
        for key in module_manager._load_order:
            mod = module_manager.modules.get(key)
            if mod and hasattr(mod, "get_hotkey_binds"):
                for b in mod.get_hotkey_binds():
                    if b.get("bind"):
                        binds.append(b["bind"])
        return binds

    def on_hotkey_triggered(triggered_bind: str):
        bind = normalize_bind(triggered_bind or "")
        if not bind:
            return
        for key in module_manager._load_order:
            mod = module_manager.modules.get(key)
            if not mod or not hasattr(mod, "get_hotkey_binds"):
                continue
            for b in mod.get_hotkey_binds():
                if b.get("bind") == bind:
                    mod.handle_hotkey(b)
                    window.refresh_from_config()
                    name = b.get("profile_name", "Profile")
                    action = b.get("action", "")
                    if action == "single_fire":
                        window.show_status_message(f"Single-fire armed ({name})", 1200)
                    elif action == "toggle":
                        window.show_status_message(f"Toggled: {name}", 1200)
                    return

    hotkey_listener = GlobalToggleListener(get_binds=all_profile_binds)
    hotkey_listener.triggered.connect(on_hotkey_triggered)
    hotkey_listener.start()

    def _module_calibrate(mm, method_name, btn, win):
        """Call a calibration method on the first module that has it."""
        for key in mm._load_order:
            mod = mm.modules.get(key)
            if not mod or not hasattr(mod, method_name):
                continue
            if method_name == "calibrate_all_baselines" and hasattr(mod, "has_baselines") and mod.has_baselines():
                reply = QMessageBox.question(
                    win, "Recalibrate all slots?",
                    "You already have baselines set. Recalibrate all slots? This will replace existing baselines.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
            ok, msg = getattr(mod, method_name)()
            btn.setText(msg if ok else f"Failed: {msg}")
            btn.setStyleSheet("" if ok else "color: red;")
            QTimer.singleShot(2000, lambda: (btn.setText("Calibrate All Baselines"), btn.setStyleSheet("")))
            if ok and hasattr(win, "clear_overwritten_baseline_slots"):
                win.clear_overwritten_baseline_slots()
            return

    def _module_calibrate_buff(mm, roi_id, win, settings_dlg, on_cfg_updated):
        """Call calibrate_buff_roi_present on the first module that has it."""
        for key in mm._load_order:
            mod = mm.modules.get(key)
            if not mod or not hasattr(mod, "calibrate_buff_roi_present"):
                continue
            ok, msg = mod.calibrate_buff_roi_present(roi_id)
            if ok:
                settings_dlg.sync_from_config()
                on_cfg_updated(core.get_root_config())
            win.show_status_message(msg, 2000)
            return

    def _module_calibrate_slot(mm, slot_index, win):
        """Call calibrate_single_slot on the first module that has it."""
        for key in mm._load_order:
            mod = mm.modules.get(key)
            if not mod or not hasattr(mod, "calibrate_single_slot"):
                continue
            ok, msg = mod.calibrate_single_slot(slot_index)
            if ok and hasattr(win, "mark_slot_recalibrated"):
                win.mark_slot_recalibrated(slot_index)
            win.show_status_message(msg, 2000)
            return

    settings_dialog.calibrate_requested.connect(
        lambda: _module_calibrate(module_manager, "calibrate_all_baselines", settings_dialog._btn_calibrate, window)
    )
    settings_dialog.calibrate_buff_present_requested.connect(
        lambda roi_id: _module_calibrate_buff(module_manager, roi_id, window, settings_dialog, on_config_updated)
    )
    window.calibrate_slot_requested.connect(
        lambda idx: _module_calibrate_slot(module_manager, idx, window)
    )
    window._btn_settings.clicked.connect(settings_dialog.show_or_raise)

    exit_code = app.exec()
    hotkey_listener.stop()
    if is_running[0]:
        worker.stop()
    module_manager.shutdown()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
