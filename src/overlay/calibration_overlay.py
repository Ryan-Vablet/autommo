"""Calibration overlay — a transparent, always-on-top window that draws a
colored rectangle showing the current capture bounding box.

The overlay is click-through (input passes to windows beneath it).
Position and size are controlled from the main UI, not by dragging.
"""
from __future__ import annotations

import logging
from typing import Optional

from PyQt6.QtCore import Qt, QRect
from PyQt6.QtGui import QPainter, QColor, QPen
from PyQt6.QtWidgets import QWidget

from src.models import BoundingBox

logger = logging.getLogger(__name__)


class CalibrationOverlay(QWidget):
    """Transparent overlay window that shows the capture bounding box."""

    def __init__(self, monitor_geometry: QRect, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._bbox = BoundingBox()
        self._border_color = QColor("#00FF00")
        self._border_width = 2
        self._monitor_geometry = monitor_geometry

        self._setup_window()

    def _setup_window(self) -> None:
        """Configure the window to be transparent, frameless, always-on-top, click-through."""
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool  # Hides from taskbar
            | Qt.WindowType.WindowTransparentForInput  # Click-through
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        # Cover the entire monitor
        self.setGeometry(self._monitor_geometry)

    def update_bounding_box(self, bbox: BoundingBox) -> None:
        """Update the displayed bounding box and repaint."""
        self._bbox = bbox
        self.update()  # Triggers paintEvent

    def update_border_color(self, color: str) -> None:
        """Update the overlay border color."""
        self._border_color = QColor(color)
        self.update()

    def paintEvent(self, event) -> None:
        """Draw the bounding box rectangle."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        pen = QPen(self._border_color, self._border_width)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        painter.drawRect(
            self._bbox.left,
            self._bbox.top,
            self._bbox.width,
            self._bbox.height,
        )
        painter.end()
