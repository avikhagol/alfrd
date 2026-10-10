"""Is the user at the Studio right now? A timestamp file the Studio touches while it is used.

The Studio (``alfrd serve``) posts ``/api/studio/presence`` at most once a minute while the
tab is visible and the user clicks, types or scrolls. Notifiers that support "mute while
active" (the ``telegram`` notifier) check :func:`active` before sending.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

#: Seconds after the last Studio interaction during which the user counts as active.
WINDOW = 300.0


def presence_file() -> Path:
    from alfrd import get_alfrd_dir

    return get_alfrd_dir() / "studio-presence"


def touch(now: float | None = None) -> None:
    """Record a Studio interaction (best effort)."""
    path = presence_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        if now is not None:
            os.utime(path, (now, now))
    except OSError:
        pass


def active(window: float = WINDOW, now: float | None = None) -> bool:
    """True when the Studio was used in the last ``window`` seconds."""
    try:
        seen = presence_file().stat().st_mtime
    except OSError:
        return False
    return (time.time() if now is None else now) - seen <= window


__all__ = ["WINDOW", "active", "presence_file", "touch"]
