"""Resolve the selected theme without changing the Studio's URL rules.

Built-ins win over drop-ins and plugin themes. All file paths are confined
within their theme/package directory; invalid or unavailable themes fall back
to Obsidian Orbit. State is read on every request, so switching needs a reload
and does not require restarting the server.
"""
from __future__ import annotations

from pathlib import Path

from alfrd import extensions
from alfrd.web import web_root


def current_theme() -> str:
    return extensions.read_state()["theme"]


def _confined(base: Path, relative: str) -> Path | None:
    try:
        if Path(relative).is_absolute():
            return None
        base = base.resolve()
        path = (base / relative).resolve()
        if path.is_relative_to(base) and path.is_file() and path.suffix == ".css":
            return path
    except (OSError, ValueError, RuntimeError):
        pass
    return None


def plugin_css(record: extensions.Record) -> Path | None:
    """A loaded plugin's confined ``theme`` CSS file, or ``None``."""
    base = extensions.module_dir(record)
    theme = record.plugin.theme if record.plugin is not None else None
    return _confined(base, theme) if base is not None and theme else None


def css_path(theme_id: str | None = None, *, plugins: bool = True) -> Path:
    """The existing, confined CSS file for ``theme_id`` or the default theme.

    ``plugins=False`` (the public ``/studio/theme.css``) resolves built-in and drop-in
    themes only; the Studio adds a plugin theme through the protected plugin route.
    """
    builtin = web_root() / "css" / "themes"
    fallback = builtin / extensions.DEFAULT_THEME / "theme.css"
    name = current_theme() if theme_id is None else theme_id
    if not isinstance(name, str) or not extensions.THEME_ID.fullmatch(name):
        return fallback
    for base in (builtin, extensions.themes_dir()):
        path = _confined(base, f"{name}/theme.css")
        if path is not None:
            return path
    if not plugins or extensions.safe_mode():
        return fallback
    for record in extensions.loaded():
        if record.id == name and record.status == "ok":
            path = plugin_css(record)
            if path is not None:
                return path
    return fallback


__all__ = ["css_path", "current_theme", "plugin_css"]
