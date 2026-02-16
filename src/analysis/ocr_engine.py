"""OCR engine for reading keybind labels and cooldown countdown numbers.

Wraps EasyOCR with preprocessing optimized for small game text.
Lazy-loads the model on first use to avoid slow startup.
"""
from __future__ import annotations

import logging
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)


class OCREngine:
    """OCR for game UI text — keybinds and cooldown numbers."""

    def __init__(self, allowlist: str = "0123456789."):
        self._reader = None  # Lazy-loaded easyocr.Reader
        self._allowlist = allowlist

    def _ensure_loaded(self) -> None:
        """Lazy-load EasyOCR model on first use."""
        if self._reader is None:
            logger.info("Loading EasyOCR model (first run may download ~100MB)...")
            import easyocr
            self._reader = easyocr.Reader(["en"], gpu=False)
            logger.info("EasyOCR model loaded.")

    def preprocess(self, image: np.ndarray, scale_factor: int = 4) -> np.ndarray:
        """Preprocess a small image crop for OCR.

        - Convert to grayscale
        - Upscale (small game text is often < 12px, OCR needs more)
        - Threshold to clean up
        """
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        # Upscale with cubic interpolation
        h, w = gray.shape
        upscaled = cv2.resize(gray, (w * scale_factor, h * scale_factor), interpolation=cv2.INTER_CUBIC)
        # Binary threshold — white text on dark background
        _, thresh = cv2.threshold(upscaled, 180, 255, cv2.THRESH_BINARY)
        return thresh

    def read_cooldown_number(self, slot_image: np.ndarray) -> Optional[float]:
        """Read a cooldown countdown number from the center of a slot image.

        Args:
            slot_image: BGR image of a single slot.

        Returns:
            The cooldown number as a float, or None if nothing readable.
        """
        self._ensure_loaded()

        # Crop center region (countdown numbers appear in the middle)
        h, w = slot_image.shape[:2]
        margin_x, margin_y = w // 4, h // 4
        center_crop = slot_image[margin_y : h - margin_y, margin_x : w - margin_x]

        processed = self.preprocess(center_crop)
        # TODO: Call self._reader.readtext() with allowlist, parse result
        # results = self._reader.readtext(processed, allowlist=self._allowlist)
        # ... parse and return
        return None

    def read_keybind_label(self, slot_image: np.ndarray) -> Optional[str]:
        """Read the keybind label from the top-right corner of a slot.

        Args:
            slot_image: BGR image of a single slot.

        Returns:
            The keybind string (e.g. '1', 'F', 'G'), or None.
        """
        self._ensure_loaded()

        # Crop top-right quadrant where keybind labels typically appear
        h, w = slot_image.shape[:2]
        top_right = slot_image[0 : h // 3, w // 2 :]

        processed = self.preprocess(top_right)
        # TODO: Call self._reader.readtext() with broader allowlist, parse result
        # results = self._reader.readtext(processed, allowlist='0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ')
        # ... parse and return
        return None
