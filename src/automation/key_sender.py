"""Key sender — sends keypresses based on slot states and priority order."""
from __future__ import annotations

import logging
import sys
import time
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from src.models import AppConfig

from src.models import ActionBarState, SlotState

logger = logging.getLogger(__name__)


def _is_target_window_active_win(target_title: str) -> bool:
    """Windows: True if foreground window title contains target_title (case-insensitive), or if target_title is empty."""
    if not (target_title or "").strip():
        return True
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False
        length = user32.GetWindowTextLengthW(hwnd) + 1
        buf = ctypes.create_unicode_buffer(length)
        user32.GetWindowTextW(hwnd, buf, length)
        foreground = buf.value or ""
        return target_title.strip().lower() in foreground.lower()
    except Exception as e:
        logger.debug("Foreground window check failed: %s", e)
        return False


def is_target_window_active(target_window_title: str) -> bool:
    """True if we may send keys (target window focused or no target set)."""
    if sys.platform != "win32":
        return True
    return _is_target_window_active_win(target_window_title or "")


class KeySender:
    """Sends keypresses for the first READY slot in priority order, with min delay and optional window check."""

    def __init__(self, config: "AppConfig"):
        self._config = config
        self._last_send_time = 0.0

    def update_config(self, config: "AppConfig") -> None:
        self._config = config

    def is_target_window_active(self) -> bool:
        """True if foreground window matches target_window_title, or target is empty."""
        return is_target_window_active(getattr(self._config, "target_window_title", "") or "")

    def evaluate_and_send(
        self,
        state: ActionBarState,
        priority_order: list[int],
        keybinds: list[str],
        automation_enabled: bool,
        queued_override: Optional[dict] = None,
        on_queued_sent: Optional[Callable[[], None]] = None,
    ) -> Optional[dict]:
        """
        If automation enabled, optionally handle queued override first (whitelist or tracked slot);
        then find first READY slot in priority_order and send its keybind. Returns None if nothing
        sent/blocked; otherwise a dict for the UI (may include "queued": True).
        """
        if not automation_enabled:
            return None

        min_interval_sec = (getattr(self._config, "min_press_interval_ms", 150) or 150) / 1000.0
        now = time.time()
        min_interval_ok = (now - self._last_send_time) >= min_interval_sec
        window_ok = self.is_target_window_active()

        if queued_override:
            source = queued_override.get("source")
            key = (queued_override.get("key") or "").strip()
            if source == "whitelist" and key:
                if min_interval_ok and window_ok:
                    try:
                        import keyboard
                        keyboard.send(key)
                    except Exception as e:
                        logger.warning("keyboard.send(queued %r) failed: %s", key, e)
                        return None
                    self._last_send_time = now
                    if on_queued_sent:
                        on_queued_sent()
                    logger.info("Sent queued key: %s", key)
                    return {"keybind": key, "action": "sent", "timestamp": now, "queued": True}
                return None
            if source == "tracked":
                slot_index = queued_override.get("slot_index")
                if slot_index is not None and key:
                    slots_by_index = {s.index: s for s in state.slots}
                    slot = slots_by_index.get(slot_index)
                    if slot and slot.state == SlotState.READY and min_interval_ok and window_ok:
                        try:
                            import keyboard
                            keyboard.send(key)
                        except Exception as e:
                            logger.warning("keyboard.send(queued %r) failed: %s", key, e)
                            return None
                        self._last_send_time = now
                        if on_queued_sent:
                            on_queued_sent()
                        logger.info("Sent queued key: %s (slot %s)", key, slot_index)
                        return {"keybind": key, "action": "sent", "timestamp": now, "slot_index": slot_index, "queued": True}
                return None

        if not min_interval_ok:
            return None

        slots_by_index = {s.index: s for s in state.slots}
        for slot_index in priority_order:
            slot = slots_by_index.get(slot_index)
            if not slot or slot.state != SlotState.READY:
                continue
            keybind = keybinds[slot_index] if slot_index < len(keybinds) else None
            if not (keybind or "").strip():
                continue
            keybind = keybind.strip()

            if not self.is_target_window_active():
                return {"keybind": keybind, "action": "blocked", "reason": "window", "slot_index": slot_index}

            try:
                import keyboard
                keyboard.send(keybind)
            except Exception as e:
                logger.warning("keyboard.send(%r) failed: %s", keybind, e)
                return None

            self._last_send_time = now
            logger.info("Sent key: %s", keybind)
            return {"keybind": keybind, "action": "sent", "timestamp": now, "slot_index": slot_index}

        return None
