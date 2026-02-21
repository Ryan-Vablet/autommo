from __future__ import annotations

__all__ = ["KeySender"]


def __getattr__(name: str):
    if name == "KeySender":
        from .key_sender import KeySender

        return KeySender
    raise AttributeError(name)
