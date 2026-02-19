"""Holds namespaced config dict; get_config/save_config with optional persist to JSON."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from src.core.config_migration import migrate_config

logger = logging.getLogger(__name__)


class ConfigManager:
    """Holds root config dict. get_config(key) returns that slice; save_config(key, data) merges and optionally saves."""

    def __init__(self, config_path: Path, initial: dict[str, Any] | None = None) -> None:
        self._path = Path(config_path)
        if initial is not None:
            self._root = copy_nested(initial)
        else:
            # Do not overwrite existing config: load from file if present, else create defaults.
            if self._path.exists():
                try:
                    with open(self._path) as f:
                        data = json.load(f)
                except Exception as e:
                    logger.exception("Failed to load config from %s: %s", self._path, e)
                    data = {}
                if not data or "core" not in data:
                    data = migrate_config(data)
                    logger.info("Config migrated to namespaced format")
                self._root = data
                # #region agent log
                try:
                    import time
                    _logpath = Path(__file__).resolve().parent.parent / "debug-6d0385.log"
                    with open(_logpath, "a") as _f:
                        _f.write(json.dumps({"sessionId": "6d0385", "hypothesisId": "H1", "location": "config_manager.py:__init__", "message": "ConfigManager loaded from file (no overwrite)", "data": {"path": str(self._path), "root_keys": list(self._root.keys()) if isinstance(self._root, dict) else []}, "timestamp": int(time.time() * 1000)}) + "\n")
                except Exception:
                    pass
                # #endregion
            else:
                self._root = migrate_config({})
                self._save_file()

    def get_config(self, module_key: str) -> dict[str, Any]:
        """Return a copy of the config section for the given key. Missing key returns {}."""
        data = self._root.get(module_key)
        if data is None:
            return {}
        return dict(copy_nested(data))

    def save_config(self, module_key: str, data: dict[str, Any]) -> None:
        """Merge data into root[module_key] and persist to file."""
        self._root[module_key] = copy_nested(data)
        self._save_file()

    def get_root(self) -> dict[str, Any]:
        """Return the full root config (for migration check, etc.). Caller should not mutate."""
        return self._root

    def set_root(self, root: dict[str, Any]) -> None:
        """Replace root config (e.g. after loading from file)."""
        self._root = copy_nested(root)

    def set_root_and_save(self, root: dict[str, Any]) -> None:
        """Replace root config and persist to file (e.g. after import)."""
        self._root = copy_nested(root)
        self._save_file()

    def load_from_file(self) -> dict[str, Any]:
        """Load JSON from path; if flat format, migrate and save. Set _root and return it."""
        if self._path.exists():
            try:
                with open(self._path) as f:
                    data = json.load(f)
            except Exception as e:
                logger.exception("Failed to load config from %s: %s", self._path, e)
                data = {}
        else:
            data = {}
        # #region agent log
        try:
            import time
            _logpath = Path(__file__).resolve().parent.parent / "debug-6d0385.log"
            with open(_logpath, "a") as _f:
                _f.write(json.dumps({"sessionId": "6d0385", "hypothesisId": "H1", "location": "config_manager.py:load_from_file", "message": "After read", "data": {"path": str(self._path), "file_existed": self._path.exists(), "root_keys": list(data.keys()) if isinstance(data, dict) else [], "has_core": "core" in data if isinstance(data, dict) else False}, "timestamp": int(time.time() * 1000)}) + "\n")
        except Exception:
            pass
        # #endregion
        if not data or "core" not in data:
            data = migrate_config(data)
            logger.info("Config migrated to namespaced format")
            self._root = data
            self._save_file()
        else:
            self._root = data
        return self._root

    def _save_file(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "w") as f:
                json.dump(self._root, f, indent=2)
        except Exception as e:
            logger.exception("Failed to save config to %s: %s", self._path, e)


def copy_nested(obj: Any) -> Any:
    """Deep copy dict/list; other types returned as-is."""
    if isinstance(obj, dict):
        return {k: copy_nested(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [copy_nested(v) for v in obj]
    return obj
