"""Screen capture using mss.

Captures a specific region of a specific monitor at high frame rates.
Only captures the action bar bounding box — never the full screen.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import mss
import numpy as np

from src.models import BoundingBox

logger = logging.getLogger(__name__)


class ScreenCapture:
    """Captures a screen region using mss."""

    def __init__(self, monitor_index: int = 1):
        self._sct: Optional[mss.mss] = None
        self._monitor_index = monitor_index

    def start(self) -> None:
        """Initialize the mss capture context."""
        self._sct = mss.mss()
        monitors = self._sct.monitors
        logger.info(f"Available monitors: {len(monitors) - 1} (indices 1..{len(monitors) - 1})")
        if self._monitor_index >= len(monitors):
            logger.warning(
                f"Monitor {self._monitor_index} not found, falling back to monitor 1"
            )
            self._monitor_index = 1

    def stop(self) -> None:
        """Release capture resources."""
        if self._sct:
            self._sct.close()
            self._sct = None

    @property
    def monitor_info(self) -> dict:
        """Get info about the selected monitor."""
        if not self._sct:
            raise RuntimeError("Capture not started. Call start() first.")
        return self._sct.monitors[self._monitor_index]

    def grab_region(self, bbox: BoundingBox) -> np.ndarray:
        """Capture a region and return as a numpy BGR array.

        Args:
            bbox: The bounding box relative to the selected monitor.

        Returns:
            numpy array of shape (height, width, 3) in BGR format.
        """
        if not self._sct:
            raise RuntimeError("Capture not started. Call start() first.")

        monitor = self._sct.monitors[self._monitor_index]
        region = bbox.as_mss_region(
            monitor_offset_x=monitor["left"],
            monitor_offset_y=monitor["top"],
        )

        # mss returns BGRA, convert to BGR for OpenCV compatibility
        raw = self._sct.grab(region)
        frame = np.array(raw, dtype=np.uint8)
        return frame[:, :, :3]  # Drop alpha channel

    def list_monitors(self) -> list[dict]:
        """List all available monitors with their geometry."""
        if not self._sct:
            raise RuntimeError("Capture not started. Call start() first.")
        # Skip index 0 which is the "all monitors" virtual screen
        return self._sct.monitors[1:]
