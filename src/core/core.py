"""Central service provider passed to every module. Config, capture, key_sender, modules, extensions, hooks."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from src.core.base_module import BaseModule

logger = logging.getLogger(__name__)


class Core:
    """Passed to every module's setup(). Provides shared infrastructure. Modules interact only through Core."""

    def __init__(
        self,
        config_manager: Any,
        screen_capture: Any,
        key_sender: Any,
    ) -> None:
        self._config = config_manager
        self._capture = screen_capture
        self._key_sender = key_sender
        self._modules: dict[str, "BaseModule"] = {}
        self._extensions: dict[str, list[Callable[..., Any]]] = {}
        self._hooks: dict[str, list[Callable[..., None]]] = {}

    def get_config(self, module_key: str) -> dict:
        """Get a module's namespaced config section."""
        return self._config.get_config(module_key)

    def get_root_config(self) -> dict:
        """Return the full root config dict (for listeners that need to refresh). Do not mutate."""
        return self._config.get_root()

    def set_root_config(self, root: dict) -> None:
        """Replace entire config (e.g. after import) and persist."""
        self._config.set_root_and_save(root)

    def save_config(self, module_key: str, data: dict) -> None:
        """Save a module's config section. Merges into root and persists."""
        self._config.save_config(module_key, data)

    def get_capture(self) -> Any:
        """Access the shared screen capture system."""
        return self._capture

    def get_key_sender(self) -> Any:
        """Access the shared key sender."""
        return self._key_sender

    def get_module(self, key: str) -> "BaseModule | None":
        """Get a reference to another loaded module. Returns None if not loaded."""
        return self._modules.get(key)

    def is_module_loaded(self, key: str) -> bool:
        """Check if a module is available."""
        return key in self._modules

    def register_module(self, key: str, module: "BaseModule") -> None:
        """Called by ModuleManager when a module is loaded. Not for use by modules."""
        self._modules[key] = module

    def get_service(self, module_key: str, service_name: str) -> Any:
        """Read a service value from another module. Returns None if module not loaded or service missing."""
        mod = self._modules.get(module_key)
        if mod is None:
            return None
        try:
            return mod.get_service_value(service_name)
        except Exception as e:
            logger.debug("get_service %s.%s failed: %s", module_key, service_name, e)
            return None

    def register_extension(self, point: str, widget_factory: Callable[..., Any]) -> None:
        """Register a widget factory for an extension point. Point is namespaced: '{module_key}.{point_name}'."""
        self._extensions.setdefault(point, []).append(widget_factory)

    def get_extensions(self, point: str) -> list[Callable[..., Any]]:
        """Return the list of widget factories registered for this extension point."""
        return list(self._extensions.get(point, []))

    def subscribe(self, hook: str, callback: Callable[..., None]) -> None:
        """Subscribe to a hook. Hook names are namespaced: '{module_key}.{hook_name}'."""
        self._hooks.setdefault(hook, []).append(callback)

    def emit(self, hook: str, **kwargs: Any) -> None:
        """Emit a hook. Callbacks are invoked; exceptions in one callback are logged and do not stop others."""
        for cb in self._hooks.get(hook, []):
            try:
                cb(**kwargs)
            except Exception as e:
                logger.exception("Hook %s subscriber failed: %s", hook, e)
