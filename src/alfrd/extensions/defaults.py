"""Packaged inventory of the bundled examples, without importing optional plugins.

Keep this metadata with the examples when adding or renaming a bundled plugin.
It lives in the Python package so wheel installs can list defaults too, even when
the repository's examples directory and a remote catalog are unavailable.
"""
from __future__ import annotations

from alfrd import extensions


_BUNDLED = (
    ("gsheet", "alfrd-gsheet", "Google Sheet", "Two-way sync around launched steps, writing only changed cells."),
    ("markdown", "alfrd-markdown", "Markdown", "Render Markdown files in the Studio file viewer."),
    ("ps2pdf", "alfrd-ps2pdf", "PostScript to PDF", "Convert PostScript and EPS files to PDF using Ghostscript."),
    ("telegram", "alfrd-telegram", "Telegram", "Plan status, logs, agent-loop handoffs and confirmed pause/resume in one allowed chat."),
)


def discover() -> list[dict]:
    """Available bundled plugins and installed built-ins; no imports or network IO.

    ``installed`` describes presence, while ``enabled`` describes activation.
    Studio overlays installed entry-point records by id to retain their real state.
    Built-ins currently consist of the themes registered by extensions discovery.
    """
    out = [{"id": pid, "package": package, "title": title, "description": description,
            "source": "bundled", "installed": False, "enabled": False, "active": False,
            "status": "available", "example": f"examples/plugins/{package}"}
           for pid, package, title, description in _BUNDLED]
    selected = extensions.read_state()["theme"]
    for rec in extensions.discover():
        if rec.source != "builtin":
            continue
        out.append({"id": rec.id, "title": rec.title or rec.id,
                    "description": rec.plugin.description if rec.plugin else f"Built-in {rec.title or rec.id} theme for Studio.",
                    "source": "builtin", "installed": True, "enabled": rec.id == selected,
                    "active": rec.id == selected, "status": "enabled" if rec.id == selected else "installed"})
    return out
