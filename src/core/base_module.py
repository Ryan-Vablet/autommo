"""Base class for all modules. Lifecycle: setup → ready → (on_frame / get_*_widget) → teardown."""

from __future__ import annotations

from abc import ABC
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from PyQt6.QtWidgets import QWidget
    from src.core.core import Core


class BaseModule(ABC):
    """Base class all modules inherit from. Subclass must define class-level identity and capability attrs."""

    name: str = ""
    key: str = ""
    version: str = "1.0.0"
    description: str = ""
    requires: list[str] = []
    optional: list[str] = []
    provides_services: list[str] = []
    extension_points: list[str] = []
    hooks: list[str] = []

    def __init__(self) -> None:
        self.core: Optional[Core] = None
        self.enabled: bool = True

    def setup(self, core: Core) -> None:
        """Called once after modules are loaded and dependency-checked. Store core; do not access other modules' services yet."""
        self.core = core

    def ready(self) -> None:
        """Called once after ALL modules have completed setup(). Safe to access other modules' services."""
        pass

    def get_settings_widget(self) -> Optional["QWidget"]:
        """Return a QWidget for the settings dialog tab, or None."""
        return None

    def get_status_widget(self) -> Optional["QWidget"]:
        """Return a QWidget for the main window status area, or None."""
        return None

    def get_service_value(self, service_name: str) -> Any:
        """Return current value for the named service. Called by Core when another module requests it."""
        return None

    def on_enable(self) -> None:
        """Module-specific activation logic."""
        pass

    def on_disable(self) -> None:
        """Module-specific deactivation logic."""
        pass

    def on_config_changed(self, key: str, value: Any) -> None:
        """Called when any of this module's config values change."""
        pass

    def on_frame(self, frame: Any) -> None:
        """Called each capture cycle with the raw frame. Only implement if the module needs per-frame processing."""
        pass

    def teardown(self) -> None:
        """Cleanup on app shutdown."""
        pass
