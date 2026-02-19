"""Cooldown Rotation settings: Detection + Automation tabs. Reads/writes core.get_config('cooldown_rotation')."""

from __future__ import annotations

import copy
import logging
from typing import Any, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

# Reuse dialog styling helpers
from src.ui.settings_dialog import _row_label, _section_frame

logger = logging.getLogger(__name__)

MODULE_KEY = "cooldown_rotation"


class CooldownRotationSettingsWidget(QWidget):
    """Detection + Automation settings. Uses core.get_config/save_config for cooldown_rotation namespace."""

    def __init__(self, core: Any, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._core = core
        self._build_ui()
        self._connect_signals()
        self._populate()

    def _build_ui(self) -> None:
        tabs = QTabWidget()
        tabs.addTab(self._build_detection_tab(), "Detection")
        tabs.addTab(self._build_automation_tab(), "Automation")
        layout = QVBoxLayout(self)
        layout.addWidget(tabs)

    def _build_detection_tab(self) -> QWidget:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSpacing(10)
        layout.addWidget(_section_frame("Detection", self._build_detection_section()))
        for factory in self._core.get_extensions(f"{MODULE_KEY}.settings.detection"):
            try:
                w = factory() if callable(factory) else factory
                if w is not None:
                    layout.addWidget(w)
            except Exception as e:
                logger.exception("Extension point detection failed: %s", e)
        layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(content)
        return scroll

    def _build_detection_section(self) -> QWidget:
        w = QWidget()
        fl = QFormLayout(w)
        self._spin_polling_fps = QSpinBox()
        self._spin_polling_fps.setRange(5, 120)
        self._spin_polling_fps.setMaximumWidth(64)
        fl.addRow(_row_label("Polling FPS:"), self._spin_polling_fps)
        self._spin_cooldown_min_ms = QSpinBox()
        self._spin_cooldown_min_ms.setRange(0, 5000)
        self._spin_cooldown_min_ms.setSuffix(" ms")
        self._spin_cooldown_min_ms.setMaximumWidth(92)
        fl.addRow(_row_label("Cooldown min:"), self._spin_cooldown_min_ms)
        self._combo_detection_region = QComboBox()
        self._combo_detection_region.addItem("Top-Left Quadrant", "top_left")
        self._combo_detection_region.addItem("Full Slot", "full")
        fl.addRow(_row_label("Region:"), self._combo_detection_region)
        self._spin_brightness_drop = QSpinBox()
        self._spin_brightness_drop.setRange(0, 255)
        self._spin_brightness_drop.setMaximumWidth(48)
        fl.addRow(_row_label("Darken:"), self._spin_brightness_drop)
        self._slider_pixel_fraction = QSlider(Qt.Orientation.Horizontal)
        self._slider_pixel_fraction.setRange(10, 90)
        self._pixel_fraction_label = QLabel("0.30")
        self._pixel_fraction_label.setMinimumWidth(32)
        row = QHBoxLayout()
        row.addWidget(self._slider_pixel_fraction)
        row.addWidget(self._pixel_fraction_label)
        fl.addRow(_row_label("Trigger:"), row)
        self._slider_change_pixel_fraction = QSlider(Qt.Orientation.Horizontal)
        self._slider_change_pixel_fraction.setRange(10, 90)
        self._change_pixel_fraction_label = QLabel("0.30")
        row2 = QHBoxLayout()
        row2.addWidget(self._slider_change_pixel_fraction)
        row2.addWidget(self._change_pixel_fraction_label)
        fl.addRow(_row_label("Change:"), row2)
        self._edit_cooldown_change_ignore = QLineEdit()
        self._edit_cooldown_change_ignore.setPlaceholderText("e.g. 5")
        fl.addRow(_row_label("Change ignore:"), self._edit_cooldown_change_ignore)
        self._check_glow_enabled = QCheckBox("Enable glow ready override")
        fl.addRow("", self._check_glow_enabled)
        glow_row = QHBoxLayout()
        self._spin_glow_ring = QSpinBox()
        self._spin_glow_ring.setRange(1, 12)
        self._spin_glow_ring.setMaximumWidth(64)
        self._spin_glow_value_delta = QSpinBox()
        self._spin_glow_value_delta.setRange(5, 120)
        self._spin_glow_value_delta.setMaximumWidth(64)
        self._spin_glow_saturation = QSpinBox()
        self._spin_glow_saturation.setRange(0, 255)
        self._spin_glow_saturation.setMaximumWidth(64)
        self._spin_glow_confirm = QSpinBox()
        self._spin_glow_confirm.setRange(1, 8)
        self._spin_glow_confirm.setMaximumWidth(56)
        glow_row.addWidget(self._spin_glow_ring)
        glow_row.addWidget(self._spin_glow_value_delta)
        glow_row.addWidget(self._spin_glow_saturation)
        glow_row.addWidget(self._spin_glow_confirm)
        glow_row.addStretch()
        fl.addRow(_row_label("Glow:"), glow_row)
        self._slider_glow_ring_fraction = QSlider(Qt.Orientation.Horizontal)
        self._slider_glow_ring_fraction.setRange(5, 60)
        self._glow_ring_fraction_label = QLabel("0.18")
        row3 = QHBoxLayout()
        row3.addWidget(self._slider_glow_ring_fraction)
        row3.addWidget(self._glow_ring_fraction_label)
        fl.addRow(_row_label("Yellow frac:"), row3)
        self._slider_glow_red_fraction = QSlider(Qt.Orientation.Horizontal)
        self._slider_glow_red_fraction.setRange(5, 60)
        self._glow_red_fraction_label = QLabel("0.18")
        row4 = QHBoxLayout()
        row4.addWidget(self._slider_glow_red_fraction)
        row4.addWidget(self._glow_red_fraction_label)
        fl.addRow(_row_label("Red frac:"), row4)
        self._check_cast_detection = QCheckBox("Enable cast/channel detection")
        fl.addRow("", self._check_cast_detection)
        self._spin_cast_confirm = QSpinBox()
        self._spin_cast_confirm.setRange(1, 10)
        self._spin_cast_min_ms = QSpinBox()
        self._spin_cast_min_ms.setRange(50, 3000)
        self._spin_cast_min_ms.setSuffix(" ms")
        self._spin_cast_max_ms = QSpinBox()
        self._spin_cast_max_ms.setRange(100, 8000)
        self._spin_cast_max_ms.setSuffix(" ms")
        cast_row = QHBoxLayout()
        cast_row.addWidget(self._spin_cast_confirm)
        cast_row.addWidget(self._spin_cast_min_ms)
        cast_row.addWidget(self._spin_cast_max_ms)
        cast_row.addStretch()
        fl.addRow(_row_label("Cast timing:"), cast_row)
        self._check_cast_bar = QCheckBox("Cast bar ROI")
        fl.addRow("", self._check_cast_bar)
        cast_bar_row = QHBoxLayout()
        self._spin_cast_bar_left = QSpinBox()
        self._spin_cast_bar_top = QSpinBox()
        self._spin_cast_bar_width = QSpinBox()
        self._spin_cast_bar_height = QSpinBox()
        for s in (self._spin_cast_bar_left, self._spin_cast_bar_top, self._spin_cast_bar_width, self._spin_cast_bar_height):
            s.setMaximumWidth(70)
        cast_bar_row.addWidget(self._spin_cast_bar_left)
        cast_bar_row.addWidget(self._spin_cast_bar_top)
        cast_bar_row.addWidget(self._spin_cast_bar_width)
        cast_bar_row.addWidget(self._spin_cast_bar_height)
        cast_bar_row.addStretch()
        fl.addRow(_row_label("Cast bar:"), cast_bar_row)
        return w

    def _build_automation_tab(self) -> QWidget:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSpacing(10)
        layout.addWidget(_section_frame("Controls", self._build_automation_controls()))
        layout.addWidget(_section_frame("Timing", self._build_automation_timing()))
        layout.addWidget(_section_frame("Spell Queue", self._build_spell_queue()))
        for factory in self._core.get_extensions(f"{MODULE_KEY}.settings.automation"):
            try:
                w = factory() if callable(factory) else factory
                if w is not None:
                    layout.addWidget(w)
            except Exception as e:
                logger.exception("Extension point automation failed: %s", e)
        layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(content)
        return scroll

    def _build_automation_controls(self) -> QWidget:
        w = QWidget()
        fl = QFormLayout(w)
        self._edit_toggle_bind = QLineEdit()
        self._edit_toggle_bind.setPlaceholderText("e.g. f24")
        fl.addRow(_row_label("Toggle bind:"), self._edit_toggle_bind)
        self._edit_single_bind = QLineEdit()
        self._edit_single_bind.setPlaceholderText("optional")
        fl.addRow(_row_label("Single bind:"), self._edit_single_bind)
        return w

    def _build_automation_timing(self) -> QWidget:
        w = QWidget()
        fl = QFormLayout(w)
        self._spin_min_delay = QSpinBox()
        self._spin_min_delay.setRange(50, 2000)
        self._spin_min_delay.setMaximumWidth(80)
        fl.addRow(_row_label("Delay (ms):"), self._spin_min_delay)
        self._spin_gcd_ms = QSpinBox()
        self._spin_gcd_ms.setRange(500, 3000)
        self._spin_gcd_ms.setSuffix(" ms")
        self._spin_gcd_ms.setMaximumWidth(80)
        fl.addRow(_row_label("GCD (ms):"), self._spin_gcd_ms)
        self._spin_queue_window = QSpinBox()
        self._spin_queue_window.setRange(0, 500)
        self._spin_queue_window.setMaximumWidth(56)
        fl.addRow(_row_label("Queue (ms):"), self._spin_queue_window)
        self._check_allow_cast = QCheckBox("Allow sends while casting/channeling")
        fl.addRow("", self._check_allow_cast)
        self._edit_window_title = QLineEdit()
        self._edit_window_title.setPlaceholderText("Target window title (empty = any)")
        fl.addRow(_row_label("Window:"), self._edit_window_title)
        return w

    def _build_spell_queue(self) -> QWidget:
        w = QWidget()
        fl = QFormLayout(w)
        self._edit_queue_keys = QLineEdit()
        self._edit_queue_keys.setPlaceholderText("e.g. R, T, V")
        fl.addRow(_row_label("Queue keys:"), self._edit_queue_keys)
        self._spin_queue_timeout = QSpinBox()
        self._spin_queue_timeout.setRange(1000, 30000)
        self._spin_queue_timeout.setSuffix(" ms")
        self._spin_queue_timeout.setMaximumWidth(80)
        fl.addRow(_row_label("Queue timeout:"), self._spin_queue_timeout)
        self._spin_queue_fire_delay = QSpinBox()
        self._spin_queue_fire_delay.setRange(0, 300)
        self._spin_queue_fire_delay.setSuffix(" ms")
        self._spin_queue_fire_delay.setMaximumWidth(80)
        fl.addRow(_row_label("Fire delay:"), self._spin_queue_fire_delay)
        return w

    def _connect_signals(self) -> None:
        for spin in (
            self._spin_polling_fps,
            self._spin_cooldown_min_ms,
            self._spin_brightness_drop,
            self._spin_glow_ring,
            self._spin_glow_value_delta,
            self._spin_glow_saturation,
            self._spin_glow_confirm,
            self._spin_cast_confirm,
            self._spin_cast_min_ms,
            self._spin_cast_max_ms,
            self._spin_cast_bar_left,
            self._spin_cast_bar_top,
            self._spin_cast_bar_width,
            self._spin_cast_bar_height,
            self._spin_min_delay,
            self._spin_gcd_ms,
            self._spin_queue_window,
            self._spin_queue_timeout,
            self._spin_queue_fire_delay,
        ):
            spin.valueChanged.connect(self._save)
        for slider in (self._slider_pixel_fraction, self._slider_change_pixel_fraction, self._slider_glow_ring_fraction, self._slider_glow_red_fraction):
            slider.valueChanged.connect(self._save)
        self._combo_detection_region.currentIndexChanged.connect(self._save)
        self._edit_cooldown_change_ignore.editingFinished.connect(self._save)
        self._edit_toggle_bind.textChanged.connect(self._save)
        self._edit_single_bind.textChanged.connect(self._save)
        self._edit_window_title.textChanged.connect(self._save)
        self._edit_queue_keys.textChanged.connect(self._save)
        for cb in (
            self._check_glow_enabled,
            self._check_cast_detection,
            self._check_cast_bar,
            self._check_allow_cast,
        ):
            cb.toggled.connect(self._save)

    def _get_cr(self) -> dict:
        return copy.deepcopy(self._core.get_config(MODULE_KEY))

    def _populate(self) -> None:
        cr = self._get_cr()
        det = cr.get("detection") or {}
        self._spin_polling_fps.setValue(int(det.get("polling_fps", 20)))
        self._spin_cooldown_min_ms.setValue(int(det.get("cooldown_min_duration_ms", 2000)))
        region = (det.get("detection_region") or "top_left").strip().lower()
        if region not in ("full", "top_left"):
            region = "top_left"
        idx = self._combo_detection_region.findData(region)
        self._combo_detection_region.setCurrentIndex(max(0, idx))
        self._spin_brightness_drop.setValue(int(det.get("brightness_drop_threshold", 40)))
        frac = float(det.get("cooldown_pixel_fraction", 0.30))
        self._slider_pixel_fraction.setValue(int(frac * 100))
        self._pixel_fraction_label.setText(f"{frac:.2f}")
        change_frac = float(det.get("cooldown_change_pixel_fraction", frac))
        self._slider_change_pixel_fraction.setValue(int(change_frac * 100))
        self._change_pixel_fraction_label.setText(f"{change_frac:.2f}")
        ignore = det.get("cooldown_change_ignore_by_slot") or []
        self._edit_cooldown_change_ignore.setText(", ".join(str(x) for x in ignore))
        self._check_glow_enabled.setChecked(bool(det.get("glow_enabled", True)))
        self._spin_glow_ring.setValue(int(det.get("glow_ring_thickness_px", 4)))
        self._spin_glow_value_delta.setValue(int(det.get("glow_value_delta", 35)))
        self._spin_glow_saturation.setValue(int(det.get("glow_saturation_min", 80)))
        self._spin_glow_confirm.setValue(int(det.get("glow_confirm_frames", 2)))
        y_frac = float(det.get("glow_ring_fraction", 0.18))
        self._slider_glow_ring_fraction.setValue(int(y_frac * 100))
        self._glow_ring_fraction_label.setText(f"{y_frac:.2f}")
        r_frac = float(det.get("glow_red_ring_fraction", y_frac))
        self._slider_glow_red_fraction.setValue(int(r_frac * 100))
        self._glow_red_fraction_label.setText(f"{r_frac:.2f}")
        self._check_cast_detection.setChecked(bool(det.get("cast_detection_enabled", True)))
        self._spin_cast_confirm.setValue(int(det.get("cast_confirm_frames", 2)))
        self._spin_cast_min_ms.setValue(int(det.get("cast_min_duration_ms", 150)))
        self._spin_cast_max_ms.setValue(int(det.get("cast_max_duration_ms", 3000)))
        cbr = det.get("cast_bar_region") or {}
        self._check_cast_bar.setChecked(bool(cbr.get("enabled", False)))
        self._spin_cast_bar_left.setValue(int(cbr.get("left", 0)))
        self._spin_cast_bar_top.setValue(int(cbr.get("top", 0)))
        self._spin_cast_bar_width.setValue(int(cbr.get("width", 0)))
        self._spin_cast_bar_height.setValue(int(cbr.get("height", 0)))
        self._spin_min_delay.setValue(int(cr.get("min_press_interval_ms", 150)))
        self._spin_gcd_ms.setValue(int(cr.get("gcd_ms", 1500)))
        self._spin_queue_window.setValue(int(det.get("queue_window_ms", 120)))
        self._check_allow_cast.setChecked(bool(cr.get("allow_cast_while_casting", False)))
        self._edit_window_title.setText(str(cr.get("target_window_title", "") or ""))
        self._edit_queue_keys.setText(", ".join(cr.get("queue_whitelist") or []))
        self._spin_queue_timeout.setValue(int(cr.get("queue_timeout_ms", 5000)))
        self._spin_queue_fire_delay.setValue(int(cr.get("queue_fire_delay_ms", 100)))
        profiles = cr.get("priority_profiles") or []
        active_id = (cr.get("active_priority_profile_id") or "default").strip().lower()
        prof = {}
        for p in profiles:
            if (str(p.get("id") or "").strip().lower() == active_id):
                prof = p
                break
        if not prof and profiles:
            prof = profiles[0]
        self._edit_toggle_bind.setText(str(prof.get("toggle_bind", "") or ""))
        self._edit_single_bind.setText(str(prof.get("single_fire_bind", "") or ""))

    def _save(self) -> None:
        cr = self._get_cr()
        det = dict(cr.get("detection") or {})  # preserve keys we don't have widgets for
        det["polling_fps"] = self._spin_polling_fps.value()
        det["cooldown_min_duration_ms"] = self._spin_cooldown_min_ms.value()
        det["detection_region"] = (self._combo_detection_region.currentData() or "top_left")
        det["brightness_drop_threshold"] = self._spin_brightness_drop.value()
        det["cooldown_pixel_fraction"] = self._slider_pixel_fraction.value() / 100.0
        det["cooldown_change_pixel_fraction"] = self._slider_change_pixel_fraction.value() / 100.0
        raw_ignore = (self._edit_cooldown_change_ignore.text() or "").strip()
        det["cooldown_change_ignore_by_slot"] = [int(x.strip()) for x in raw_ignore.replace(",", " ").split() if x.strip().isdigit()]
        self._pixel_fraction_label.setText(f"{det['cooldown_pixel_fraction']:.2f}")
        self._change_pixel_fraction_label.setText(f"{det['cooldown_change_pixel_fraction']:.2f}")
        det["glow_enabled"] = self._check_glow_enabled.isChecked()
        det["glow_ring_thickness_px"] = self._spin_glow_ring.value()
        det["glow_value_delta"] = self._spin_glow_value_delta.value()
        det["glow_saturation_min"] = self._spin_glow_saturation.value()
        det["glow_confirm_frames"] = self._spin_glow_confirm.value()
        det["glow_ring_fraction"] = self._slider_glow_ring_fraction.value() / 100.0
        det["glow_red_ring_fraction"] = self._slider_glow_red_fraction.value() / 100.0
        self._glow_ring_fraction_label.setText(f"{det['glow_ring_fraction']:.2f}")
        self._glow_red_fraction_label.setText(f"{det['glow_red_ring_fraction']:.2f}")
        det["cast_detection_enabled"] = self._check_cast_detection.isChecked()
        det["cast_confirm_frames"] = self._spin_cast_confirm.value()
        det["cast_min_duration_ms"] = self._spin_cast_min_ms.value()
        det["cast_max_duration_ms"] = self._spin_cast_max_ms.value()
        det["cast_bar_region"] = {
            "enabled": self._check_cast_bar.isChecked(),
            "left": self._spin_cast_bar_left.value(),
            "top": self._spin_cast_bar_top.value(),
            "width": self._spin_cast_bar_width.value(),
            "height": self._spin_cast_bar_height.value(),
        }
        cr["detection"] = det
        cr["min_press_interval_ms"] = self._spin_min_delay.value()
        cr["gcd_ms"] = self._spin_gcd_ms.value()
        det["queue_window_ms"] = self._spin_queue_window.value()
        cr["allow_cast_while_casting"] = self._check_allow_cast.isChecked()
        cr["target_window_title"] = (self._edit_window_title.text() or "").strip()
        raw_queue = (self._edit_queue_keys.text() or "").strip()
        cr["queue_whitelist"] = [k.strip().lower() for k in raw_queue.replace(",", " ").split() if k.strip()]
        cr["queue_timeout_ms"] = self._spin_queue_timeout.value()
        cr["queue_fire_delay_ms"] = self._spin_queue_fire_delay.value()
        from src.automation.binds import normalize_bind
        profiles = list(cr.get("priority_profiles") or [])
        if not profiles:
            profiles = [{"id": "default", "name": "Default", "toggle_bind": "", "single_fire_bind": "", "priority_order": [], "priority_items": [], "manual_actions": []}]
        active_id = (cr.get("active_priority_profile_id") or "default").strip().lower()
        for p in profiles:
            if (str(p.get("id") or "").strip().lower() == active_id):
                p["toggle_bind"] = normalize_bind((self._edit_toggle_bind.text() or "").strip())
                p["single_fire_bind"] = normalize_bind((self._edit_single_bind.text() or "").strip())
                break
        else:
            profiles[0]["toggle_bind"] = normalize_bind((self._edit_toggle_bind.text() or "").strip())
            profiles[0]["single_fire_bind"] = normalize_bind((self._edit_single_bind.text() or "").strip())
        cr["priority_profiles"] = profiles
        self._core.save_config(MODULE_KEY, cr)
