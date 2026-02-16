"""Slot analyzer — segments the action bar and detects cooldown states.

Phase 1: Brightness-based detection (compare average brightness vs baseline).
Phase 2: OCR for countdown numbers.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import cv2
import numpy as np

from src.models import (
    ActionBarState,
    AppConfig,
    SlotConfig,
    SlotSnapshot,
    SlotState,
)

logger = logging.getLogger(__name__)


class SlotAnalyzer:
    """Analyzes a captured action bar image to determine per-slot cooldown state."""

    def __init__(self, config: AppConfig):
        self._config = config
        self._slot_configs: list[SlotConfig] = []
        self._baselines: dict[int, float] = {}  # slot_index -> baseline brightness
        self._ocr_engine: Optional[object] = None  # Lazy-loaded OCREngine
        self._recompute_slot_layout()

    def _recompute_slot_layout(self) -> None:
        """Calculate pixel regions for each slot based on config.

        Divides the bounding box width evenly among slot_count slots,
        accounting for gap_pixels between them.
        """
        total_width = self._config.bounding_box.width
        total_height = self._config.bounding_box.height
        gap = self._config.slot_gap_pixels
        count = self._config.slot_count

        # Each slot width = (total_width - (count-1)*gap) / count
        slot_w = max(1, (total_width - (count - 1) * gap) // count)
        slot_h = total_height

        self._slot_configs = []
        for i in range(count):
            x = i * (slot_w + gap)
            self._slot_configs.append(
                SlotConfig(index=i, x_offset=x, y_offset=0, width=slot_w, height=slot_h)
            )
        logger.debug(f"Slot layout: {count} slots, each {slot_w}x{slot_h}px, gap={gap}px")

    def update_config(self, config: AppConfig) -> None:
        """Update config and recompute layout."""
        self._config = config
        self._recompute_slot_layout()

    def crop_slot(self, frame: np.ndarray, slot: SlotConfig) -> np.ndarray:
        """Extract a single slot's image from the action bar frame.

        Applies slot_padding as an inset on all four sides so the analyzed
        region excludes gap pixels and icon borders.
        """
        pad = self._config.slot_padding
        x1 = slot.x_offset + pad
        y1 = slot.y_offset + pad
        w = max(1, slot.width - 2 * pad)
        h = max(1, slot.height - 2 * pad)
        x2 = x1 + w
        y2 = y1 + h
        return frame[y1:y2, x1:x2]

    def compute_brightness(self, slot_image: np.ndarray) -> float:
        """Compute normalized average brightness (0.0 to 1.0) of a slot image."""
        gray = cv2.cvtColor(slot_image, cv2.COLOR_BGR2GRAY)
        return float(np.mean(gray) / 255.0)

    def calibrate_baselines(self, frame: np.ndarray) -> None:
        """Capture current frame as the 'ready' baseline for all slots.

        Call this when all abilities are off cooldown to establish
        what 'ready' looks like for brightness comparison.
        """
        for slot_cfg in self._slot_configs:
            slot_img = self.crop_slot(frame, slot_cfg)
            self._baselines[slot_cfg.index] = self.compute_brightness(slot_img)
        logger.info(f"Calibrated baselines for {len(self._baselines)} slots: {self._baselines}")

    def analyze_frame(self, frame: np.ndarray) -> ActionBarState:
        """Analyze a full action bar frame and return state for all slots.

        Args:
            frame: BGR numpy array of the captured action bar region.

        Returns:
            ActionBarState with a SlotSnapshot per slot.
        """
        now = time.time()
        snapshots: list[SlotSnapshot] = []

        for slot_cfg in self._slot_configs:
            slot_img = self.crop_slot(frame, slot_cfg)
            brightness = self.compute_brightness(slot_img)

            # Determine state via brightness comparison
            baseline = self._baselines.get(slot_cfg.index)
            if baseline is None:
                state = SlotState.UNKNOWN
            elif brightness < baseline * self._config.brightness_threshold:
                state = SlotState.ON_COOLDOWN
            else:
                state = SlotState.READY

            # TODO Phase 2: If on cooldown and OCR enabled, read countdown number
            cooldown_remaining = None

            snapshots.append(
                SlotSnapshot(
                    index=slot_cfg.index,
                    state=state,
                    brightness=brightness,
                    cooldown_remaining=cooldown_remaining,
                    timestamp=now,
                )
            )

        return ActionBarState(slots=snapshots, timestamp=now)
