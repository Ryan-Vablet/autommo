"""Key sender — sends keypresses based on slot states and priority rules.

FUTURE SCOPE: This module is a stub. The interface is designed so that
a rule engine can be plugged in later.

Planned features:
- Priority-ordered list of keybinds to press when ready
- Minimum delay between keypresses (to avoid spam)
- GCD awareness (don't press during GCD)
- Conditional rules (e.g., "only press 5 if slot 3 is on cooldown")
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from src.models import ActionBarState

logger = logging.getLogger(__name__)


@dataclass
class PriorityRule:
    """A single priority rule: press this keybind when the slot is ready."""
    keybind: str
    slot_index: int
    priority: int  # Lower = higher priority
    enabled: bool = True
    # Future: conditions like "only if slot X is on cooldown"
    conditions: list = field(default_factory=list)


class KeySender:
    """Sends keypresses based on action bar state and priority rules."""

    def __init__(self):
        self._rules: list[PriorityRule] = []
        self._enabled = False
        self._min_delay_ms = 100  # Minimum ms between key sends
        self._last_send_time = 0.0

    def set_rules(self, rules: list[PriorityRule]) -> None:
        """Set the priority rules, sorted by priority."""
        self._rules = sorted(rules, key=lambda r: r.priority)

    def enable(self) -> None:
        self._enabled = True
        logger.info("KeySender enabled")

    def disable(self) -> None:
        self._enabled = False
        logger.info("KeySender disabled")

    def evaluate(self, state: ActionBarState) -> Optional[str]:
        """Given current action bar state, determine which key (if any) to press.

        Returns the keybind string to press, or None.
        Does NOT actually send the key — caller is responsible for that.
        """
        if not self._enabled:
            return None

        for rule in self._rules:
            if not rule.enabled:
                continue
            slot = next((s for s in state.slots if s.index == rule.slot_index), None)
            if slot and slot.is_ready:
                # TODO: Check conditions
                return rule.keybind

        return None

    def send_key(self, keybind: str) -> None:
        """Actually send a keypress. Stub — will use pynput."""
        # TODO: Implement with pynput
        # from pynput.keyboard import Controller, Key
        # keyboard = Controller()
        # keyboard.press(keybind)
        # keyboard.release(keybind)
        logger.debug(f"Would send key: {keybind}")
