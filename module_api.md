# Module API — Cooldown Reader

Human-readable overview and a concise spec for AI-assisted development and onboarding.

---

## Part 1: Human-readable overview

### What the module system is

The app is built around a **module system**: optional features (e.g. cooldown rotation) live in the `modules/` directory. Each module is a Python package that provides:

- **Logic** — e.g. slot analysis, key sending, priority rules.
- **UI** — a *status widget* (main window: preview, slots, last action, priority) and optionally a *settings widget* (tabs in the Settings dialog).

The **Core** holds config and shared services (capture, key sender). The **ModuleManager** discovers modules under `modules/`, loads them in dependency order, and calls `setup()` then `ready()`. The main window and Settings dialog ask the manager for widgets and wire signals (e.g. frame capture → preview, slot states → UI).

### Important rule for widget creation

**Status and settings widgets must be cached.**  
`get_status_widget()` and `get_settings_widget()` are called multiple times (e.g. when the main window builds the layout and again when delegating updates). If the module creates a **new** widget on every call, the UI will add one instance to the layout but send updates to a different instance that is never visible. **Always return the same widget instance** (create once, store on the module, return that reference).

### Where things live

- **`src/core/`** — Core, ModuleManager, BaseModule, config.
- **`src/main.py`** — Entry point; creates Core, ModuleManager, MainWindow, SettingsDialog, capture worker; wires signals (e.g. `worker.frame_captured` → `window.update_preview`).
- **`modules/<module_name>/`** — One folder per module (e.g. `cooldown_rotation/`), with `__init__.py`, `module.py`, `status_widget.py`, and optionally `settings_widget.py`.

---

## Part 2: For AI / team (module contract and patterns)

Use this section when prompting an AI or onboarding a dev to add or change modules.

### Module discovery and lifecycle

- **Discovery:** ModuleManager scans `modules/` for directories that contain `__init__.py` and a class that subclasses `BaseModule` (and is not `BaseModule` itself). The class's `key` (or directory name) is the module id (e.g. `cooldown_rotation`).
- **Load order:** Modules are loaded in topological order by `requires` / `optional` (see BaseModule).
- **Lifecycle:** For each module, in order: `__init__` → `setup(core)` → `ready()`. On shutdown: `teardown()` in reverse order.
- **Per-frame:** When capture is running, the worker grabs a frame, emits it for preview (QImage), then calls `module_manager.process_frame(frame)`. Each loaded module's `on_frame(frame)` is called if the module is `enabled`.

### BaseModule contract (reference)

- **Class attributes (identity):** `name`, `key`, `version`, `description`, `requires`, `optional`, `provides_services`, `extension_points`, `hooks`.
- **Instance:** `self.core` is set in `setup(core)`. Do not use before `setup`.
- **Methods to implement or use:**
  - `setup(core)` — Store core; create analyzers, listeners, etc. Do not depend on other modules' services yet.
  - `ready()` — Optional. After all modules' `setup()` has run; safe to use other modules' services.
  - `get_settings_widget()` — Return a single QWidget for the Settings dialog tab, or None. **Must return the same instance every time** (cache on the module).
  - `get_status_widget()` — Return a single QWidget for the main window status area (preview, slots, etc.), or None. **Must return the same instance every time** (cache on the module).
  - `on_frame(frame)` — Optional. Called each capture cycle with the raw frame (numpy array) if the module is enabled.
  - `get_service_value(service_name)` — Optional. Return a value for another module or the app (e.g. `slot_states`, `gcd_estimate`).
  - `teardown()` — Optional. Cleanup on app shutdown.

### Widget creation rule (critical)

- **Cache widget instances.**  
  Example for status widget:
  - In `__init__`: `self._status_widget = None`.
  - In `get_status_widget()`: if `self._status_widget is None`, create it and assign to `self._status_widget`; return `self._status_widget`.  
  Same pattern for `get_settings_widget()` if the same widget is requested multiple times.  
  If the UI adds widget A to the layout but later calls `update_*` on a new widget B, the visible UI will never change.

### Main window and signals (how the main app wires modules)

- **Status widgets:** Main window calls `module_manager.get_status_widgets()` and adds each returned widget to its layout (inside a scroll area). It delegates updates (e.g. `update_preview`, `update_slot_states`) to the *first* status widget when there is only one module; that widget must be the same instance that was added to the layout (hence caching).
- **Preview:** Capture worker runs in a thread; it converts each frame to a **QImage** (not numpy) and emits `frame_captured.emit(qimg)`. Connection uses `QueuedConnection`. The main window's `update_preview(qimg)` forwards to the cached status widget's `update_preview(qimg)`.
- **Slot states / other signals:** Main app connects module signals (e.g. `slot_states_updated_signal`) to the main window with `QueuedConnection`; the main window then delegates to the appropriate status widget (same cached instance).

### Config

- Config is namespaced: `core`, `cooldown_rotation`, etc. Modules get their slice via `core.get_config(module_key)` and save via `core.save_config(module_key, data)`. ConfigManager loads from file on startup (without overwriting existing file in `ConfigManager.__init__`).

### Adding a new module

1. Create `modules/<key>/` with `__init__.py` that exposes the module class.
2. Implement a class that subclasses `BaseModule` (and, if using signals, the appropriate Qt base e.g. QObject). Set `key`, `name`, and optionally `requires`/`optional`.
3. In `setup(core)`, create analyzers/services and store references. Optionally create and cache `_status_widget` and `_settings_widget` here or on first access.
4. Implement `get_status_widget()` and/or `get_settings_widget()`; **return the same cached instance every time**.
5. If the module needs per-frame work, implement `on_frame(frame)`.
6. Register the module in config's `modules_enabled` (e.g. in `core` or app config) so ModuleManager loads it.
7. In `main.py`, wire any module-specific signals to the main window or overlay (following the same pattern as cooldown_rotation).
