"""Discovers, loads, and manages modules. Topological sort by dependencies; setup then ready; process_frame in order."""

from __future__ import annotations

import importlib
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from src.core.base_module import BaseModule
from src.core.core import Core

if TYPE_CHECKING:
    from PyQt6.QtWidgets import QWidget

logger = logging.getLogger(__name__)


class ModuleManager:
    """Discovers, loads, validates, and manages modules. Frames are processed in dependency order."""

    def __init__(self, core: Core) -> None:
        self.core = core
        self.modules: dict[str, BaseModule] = {}
        self._load_order: list[str] = []
        self._discovered: dict[str, type[BaseModule]] = {}

    def discover(self, modules_dir: str) -> list[str]:
        """Scan modules_dir for subdirs with __init__.py; find BaseModule subclass in each. Return list of module keys."""
        path = Path(modules_dir).resolve()
        if not path.is_dir():
            logger.warning("Modules dir not found: %s", modules_dir)
            return []
        parent = path.parent
        package_prefix = path.name
        if str(parent) not in sys.path:
            sys.path.insert(0, str(parent))
        found: list[str] = []
        for entry in sorted(path.iterdir()):
            if not entry.is_dir():
                continue
            init_py = entry / "__init__.py"
            if not init_py.is_file():
                continue
            pkg_name = f"{package_prefix}.{entry.name}"
            try:
                mod = importlib.import_module(pkg_name)
                cls = None
                for name in dir(mod):
                    obj = getattr(mod, name)
                    if (
                        isinstance(obj, type)
                        and issubclass(obj, BaseModule)
                        and obj is not BaseModule
                    ):
                        cls = obj
                        break
                if cls is not None:
                    key = getattr(cls, "key", None) or entry.name
                    self._discovered[key] = cls
                    found.append(key)
                    logger.info("Discovered module: %s (%s)", key, getattr(cls, "name", key))
                else:
                    logger.warning("No BaseModule subclass in %s", init_py)
            except Exception as e:
                logger.exception("Failed to load module %s: %s", entry.name, e)
        return found

    def load(self, module_keys: Optional[list[str]] = None) -> None:
        """Load specified modules (or all discovered). Validate requires, topological sort, setup() then ready()."""
        to_load = list(module_keys) if module_keys is not None else list(self._discovered)
        # Validate requires
        available = set(self._discovered)
        for key in to_load:
            if key not in available:
                logger.error("Module %s not discovered, skipping load", key)
                to_load = [k for k in to_load if k != key]
                continue
            cls = self._discovered[key]
            for req in getattr(cls, "requires", []):
                if req not in available:
                    logger.error("Module %s requires %s which is not available, skipping", key, req)
                    to_load = [k for k in to_load if k != key]
                    break
        # Topological sort by requires + optional (only optional that are in to_load)
        order = self._topological_sort(to_load)
        if order is None:
            logger.error("Cycle or error in module dependencies, aborting load")
            return
        # Instantiate and lifecycle
        for key in order:
            cls = self._discovered[key]
            try:
                instance = cls()
                self.modules[key] = instance
                self.core.register_module(key, instance)
                self._load_order.append(key)
            except Exception as e:
                logger.exception("Failed to instantiate module %s: %s", key, e)
        for key in self._load_order:
            module = self.modules[key]
            try:
                module.setup(self.core)
            except Exception as e:
                logger.exception("Module %s setup failed: %s", key, e)
        for key in self._load_order:
            module = self.modules[key]
            try:
                module.ready()
            except Exception as e:
                logger.exception("Module %s ready() failed: %s", key, e)

    def _topological_sort(self, keys: list[str]) -> Optional[list[str]]:
        """Topological sort by requires + optional (if present in keys). Returns None if cycle."""
        # graph[key] = list of keys it depends on (must be processed before key)
        graph: dict[str, list[str]] = {}
        for key in keys:
            cls = self._discovered.get(key)
            if cls is None:
                continue
            deps = list(getattr(cls, "requires", []))
            for opt in getattr(cls, "optional", []):
                if opt in keys:
                    deps.append(opt)
            graph[key] = [d for d in deps if d in keys]
        # Kahn: in_degree[k] = number of deps of k (that are in keys)
        in_degree = {k: len(graph.get(k, [])) for k in keys}
        queue = [k for k in keys if in_degree[k] == 0]
        result = []
        while queue:
            n = queue.pop(0)
            result.append(n)
            # Any node that had n as dependency now has one less
            for k in keys:
                if n in graph.get(k, []):
                    in_degree[k] -= 1
                    if in_degree[k] == 0:
                        queue.append(k)
        if len(result) != len(keys):
            return None
        return result

    def get(self, key: str) -> Optional[BaseModule]:
        """Get a loaded module by key."""
        return self.modules.get(key)

    def process_frame(self, frame: Any) -> None:
        """Call on_frame() on each enabled module in dependency order."""
        for key in self._load_order:
            module = self.modules.get(key)
            if module is not None and module.enabled:
                try:
                    module.on_frame(frame)
                except Exception as e:
                    logger.exception("Module %s on_frame failed: %s", key, e)

    def get_settings_widgets(self) -> list[tuple[str, "QWidget"]]:
        """Return (tab_name, widget) for all modules that provide settings."""
        out: list[tuple[str, "QWidget"]] = []
        for key in self._load_order:
            module = self.modules.get(key)
            if module is None:
                continue
            w = module.get_settings_widget()
            if w is not None:
                out.append((module.name, w))
        return out

    def get_status_widgets(self) -> list[tuple[str, "QWidget"]]:
        """Return (module_name, widget) for main window display."""
        out: list[tuple[str, "QWidget"]] = []
        for key in self._load_order:
            module = self.modules.get(key)
            if module is None:
                continue
            w = module.get_status_widget()
            if w is not None:
                out.append((module.name, w))
        return out

    def shutdown(self) -> None:
        """Call teardown() on each module in reverse load order."""
        for key in reversed(self._load_order):
            module = self.modules.get(key)
            if module is not None:
                try:
                    module.teardown()
                except Exception as e:
                    logger.exception("Module %s teardown failed: %s", key, e)
