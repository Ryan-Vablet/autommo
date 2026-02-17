"""Global hotkey listener for automation toggle (works when app does not have focus)."""
from __future__ import annotations

import logging
from typing import Callable, Optional

from PyQt6.QtCore import QObject, pyqtSignal, QThread

logger = logging.getLogger(__name__)


def format_bind_for_display(bind: str) -> str:
    """Convert stored bind string to display label (e.g. 'f5' -> 'F5', 'x1' -> 'Mouse 4')."""
    if not bind or not bind.strip():
        return "Set"
    b = bind.strip().lower()
    if b == "x1":
        return "Mouse 4"
    if b == "x2":
        return "Mouse 5"
    if b in ("left", "right", "middle"):
        return {"left": "LMB", "right": "RMB", "middle": "MMB"}.get(b, b)
    if len(b) <= 2 and b.startswith("f"):
        return b.upper()
    return b.upper() if len(b) <= 2 else b.capitalize()


def normalize_bind(bind: str) -> str:
    """Normalize bind string for comparison (lowercase, stripped)."""
    return bind.strip().lower() if bind else ""


class _ListenerThread(QThread):
    """Thread that runs pynput listeners and emits when the configured key/button is pressed."""

    triggered = pyqtSignal()

    def __init__(self, get_bind: Callable[[], str], parent: Optional[QObject] = None):
        super().__init__(parent)
        self._get_bind = get_bind
        self._running = True
        self._k_listener = None
        self._m_listener = None

    def run(self) -> None:
        try:
            from pynput import keyboard, mouse
        except ImportError:
            logger.warning("pynput not installed; global automation toggle hotkey disabled")
            return

        def on_key(key) -> bool:
            if not self._running:
                return False
            try:
                name = key.name if hasattr(key, "name") else (key.char or "")
                if isinstance(name, str) and name:
                    b = name.lower()
                else:
                    b = str(key).lower().replace("key.", "")
                if normalize_bind(b) == normalize_bind(self._get_bind()):
                    self.triggered.emit()
            except Exception:
                pass
            return self._running

        def on_click(x: int, y: int, button, pressed: bool) -> bool:
            if not self._running or not pressed:
                return self._running
            try:
                name = getattr(button, "name", str(button)).lower()
                if normalize_bind(name) == normalize_bind(self._get_bind()):
                    self.triggered.emit()
            except Exception:
                pass
            return self._running

        while self._running and not normalize_bind(self._get_bind()):
            self.msleep(500)
        if not self._running:
            return

        k_listener = keyboard.Listener(on_release=on_key)
        m_listener = mouse.Listener(on_click=on_click)
        self._k_listener = k_listener
        self._m_listener = m_listener
        k_listener.start()
        m_listener.start()
        while self._running:
            self.msleep(200)
        try:
            k_listener.stop()
            m_listener.stop()
        except Exception:
            pass

    def stop(self) -> None:
        self._running = False


class CaptureOneKeyThread(QThread):
    """Captures the next key or mouse button press globally and emits it as a bind string."""

    captured = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._done = False

    def run(self) -> None:
        try:
            from pynput import keyboard, mouse
        except ImportError:
            self.cancelled.emit()
            return

        result: Optional[str] = None

        def on_key(key) -> bool:
            nonlocal result
            if self._done:
                return False
            try:
                if hasattr(key, "name") and key.name:
                    result = key.name.lower()
                elif hasattr(key, "char") and key.char:
                    result = key.char.lower()
                else:
                    result = str(key).lower().replace("key.", "")
                if result:
                    self._done = True
                    self.captured.emit(result)
                return False
            except Exception:
                pass
            return True

        def on_click(x: int, y: int, button, pressed: bool) -> bool:
            nonlocal result
            if self._done or not pressed:
                return not self._done
            try:
                result = getattr(button, "name", str(button)).lower()
                if result == "left":
                    return True
                if result:
                    self._done = True
                    self.captured.emit(result)
                return False
            except Exception:
                pass
            return True

        k_listener = keyboard.Listener(on_release=on_key)
        m_listener = mouse.Listener(on_click=on_click)
        k_listener.start()
        m_listener.start()
        while not self._done and (k_listener.running or m_listener.running):
            self.msleep(50)
        k_listener.stop()
        m_listener.stop()

    def cancel(self) -> None:
        self._done = True


class GlobalToggleListener(QObject):
    """Starts a background thread that emits when the automation toggle key is pressed."""

    triggered = pyqtSignal()

    def __init__(self, get_bind: Callable[[], str], parent: Optional[QObject] = None):
        super().__init__(parent)
        self._get_bind = get_bind
        self._thread: Optional[_ListenerThread] = None

    def start(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            return
        self._thread = _ListenerThread(self._get_bind, self)
        self._thread.triggered.connect(self.triggered.emit)
        self._thread.start()

    def stop(self) -> None:
        if self._thread is not None:
            self._thread.stop()
            self._thread.wait(2000)
            self._thread = None
