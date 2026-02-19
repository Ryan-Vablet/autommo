"""Migrate flat config to namespaced format. Safe to run on already-migrated config (no-op if 'core' exists)."""

from __future__ import annotations

import copy
import logging
from typing import Any

logger = logging.getLogger(__name__)


def migrate_config(old: dict[str, Any]) -> dict[str, Any]:
    """Convert flat config to namespaced { core, cooldown_rotation }. No-op if 'core' already present."""
    if "core" in old:
        return old
    slots = old.get("slots") or {}
    core: dict[str, Any] = {
        "monitor_index": old.get("monitor_index", 1),
        "bounding_box": copy.deepcopy(old.get("bounding_box") or {}),
        "slots": {
            "count": slots.get("count", 10),
            "gap_pixels": slots.get("gap_pixels", 2),
            "padding": slots.get("padding", 3),
        },
        "slot_display_names": copy.deepcopy(old.get("slot_display_names") or []),
        "overlay": copy.deepcopy(old.get("overlay") or {}),
        "display": copy.deepcopy(old.get("display") or {}),
        "profile_name": old.get("profile_name", ""),
        "modules_enabled": old.get("modules_enabled", ["cooldown_rotation"]),
    }
    cooldown_rotation: dict[str, Any] = {
        "detection": copy.deepcopy(old.get("detection") or {}),
        "slot_baselines": copy.deepcopy(old.get("slot_baselines") or []),
        "overwritten_baseline_slots": copy.deepcopy(old.get("overwritten_baseline_slots") or []),
        "buff_rois": copy.deepcopy(old.get("buff_rois") or []),
        "priority_order": copy.deepcopy(old.get("priority_order") or []),
        "automation_enabled": old.get("automation_enabled", False),
        "automation_toggle_bind": old.get("automation_toggle_bind", ""),
        "automation_hotkey_mode": old.get("automation_hotkey_mode", "toggle"),
        "priority_profiles": copy.deepcopy(old.get("priority_profiles") or []),
        "active_priority_profile_id": old.get("active_priority_profile_id", "default"),
        "min_press_interval_ms": old.get("min_press_interval_ms", 150),
        "gcd_ms": old.get("gcd_ms", 1500),
        "target_window_title": old.get("target_window_title", ""),
        "queue_whitelist": copy.deepcopy(old.get("queue_whitelist") or []),
        "queue_timeout_ms": old.get("queue_timeout_ms", 5000),
        "queue_fire_delay_ms": old.get("queue_fire_delay_ms", 100),
        "keybinds": copy.deepcopy(slots.get("keybinds") or []),
    }
    return {"core": core, "cooldown_rotation": cooldown_rotation}


def flatten_config(core: dict[str, Any], cooldown_rotation: dict[str, Any]) -> dict[str, Any]:
    """Build a single flat dict from namespaced slices for AppConfig.from_dict / SlotAnalyzer / KeySender."""
    slots = core.get("slots") or {}
    cr = cooldown_rotation
    det = cr.get("detection") or {}
    keybinds = cr.get("keybinds") or slots.get("keybinds") or []
    flat: dict[str, Any] = {
        "monitor_index": core.get("monitor_index", 1),
        "bounding_box": core.get("bounding_box") or {},
        "slots": {
            "count": slots.get("count", 10),
            "gap_pixels": slots.get("gap_pixels", 2),
            "padding": slots.get("padding", 3),
            "keybinds": keybinds,
        },
        "slot_display_names": core.get("slot_display_names") or [],
        "overlay": core.get("overlay") or {},
        "display": core.get("display") or {},
        "profile_name": core.get("profile_name", ""),
        "detection": det,
        "slot_baselines": cr.get("slot_baselines") or [],
        "overwritten_baseline_slots": cr.get("overwritten_baseline_slots") or [],
        "buff_rois": cr.get("buff_rois") or [],
        "priority_order": cr.get("priority_order") or [],
        "automation_enabled": cr.get("automation_enabled", False),
        "automation_toggle_bind": cr.get("automation_toggle_bind", ""),
        "automation_hotkey_mode": cr.get("automation_hotkey_mode", "toggle"),
        "priority_profiles": cr.get("priority_profiles") or [],
        "active_priority_profile_id": cr.get("active_priority_profile_id", "default"),
        "min_press_interval_ms": cr.get("min_press_interval_ms", 150),
        "gcd_ms": cr.get("gcd_ms", 1500),
        "target_window_title": cr.get("target_window_title", ""),
        "history_rows": (core.get("display") or {}).get("history_rows", 3),
        "queue_whitelist": cr.get("queue_whitelist") or [],
        "queue_timeout_ms": cr.get("queue_timeout_ms", 5000),
        "queue_fire_delay_ms": cr.get("queue_fire_delay_ms", 100),
    }
    return flat
