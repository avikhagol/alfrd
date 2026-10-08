"""Studio plugin routes: the list, enable/disable, the theme, and plugin browser files.

Every route sits behind the access token (``studio_api`` / ``studio`` blueprints);
the mutations also pass ``require_local_csrf`` (loopback + same origin + CSRF).
Python loads once per process (``extensions.load``): disabling hides a plugin's
browser files at once, everything else that needs an import says
``restart_required``.
"""
from __future__ import annotations

from pathlib import Path

from flask import abort, jsonify, request, send_from_directory
from werkzeug.exceptions import NotFound

from alfrd import extensions
from alfrd.extensions import themes
from alfrd.gui.security import require_local_csrf
from alfrd.gui.studio import _json_error, studio, studio_api

#: Plugin browser files are ES modules and styles; ``nosniff`` needs the exact type.
_MIME = {".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css", ".json": "application/json",
         ".svg": "image/svg+xml", ".png": "image/png", ".woff2": "font/woff2", ".map": "application/json"}


def _records() -> list[extensions.Record]:
    """Loaded records (with manifests) plus anything discovered since (e.g. disabled at startup)."""
    out = {r.id: r for r in extensions.loaded() if r.source == "entry_point"}
    for rec in extensions.discover():
        if rec.source == "entry_point" and rec.id not in out:
            out[rec.id] = rec
    return sorted(out.values(), key=lambda r: r.id)


def _record(plugin_id: str) -> extensions.Record | None:
    return next((r for r in _records() if r.id == plugin_id), None)


def _has_python(rec: extensions.Record) -> bool:
    plugin = rec.plugin
    return bool(plugin and (plugin.panels or plugin.converters or plugin.cli is not None))


def _entry(rec: extensions.Record, disabled: set[str]) -> dict:
    plugin = rec.plugin
    active = extensions.active(rec)
    web = None
    folder = extensions.web_dir(rec) if active else None
    if folder is not None:
        base = f"/studio/plugins/{rec.id}/"
        web = {"js": base + "index.js" if (folder / "index.js").is_file() else None,
               "css": base + "index.css" if (folder / "index.css").is_file() else None}
    theme_css = None
    if active and themes.plugin_css(rec) is not None:
        theme_css = f"/studio/plugins/{rec.id}/theme.css"
    return {
        "id": rec.id, "title": rec.title or rec.id, "version": rec.version, "description": plugin.description if plugin else "",
        "kinds": rec.kinds, "enabled": rec.id not in disabled, "active": active, "status": rec.status,
        "error": rec.error, "traceback_tail": rec.traceback_tail, "missing_bin": rec.missing_bin, "source": rec.dist or rec.source,
        "web": web, "theme_css": theme_css, "has_python": _has_python(rec),
        "converters": [{"src": list(c.src), "to": c.to} for c in plugin.converters] if plugin and active else [],
    }


def _themes() -> list[dict]:
    out = [{"id": r.id, "title": r.title or r.id, "source": r.source}
           for r in extensions.discover() if r.source in ("builtin", "theme_dir")]
    for rec in extensions.loaded():
        if extensions.active(rec) and themes.plugin_css(rec) is not None and all(t["id"] != rec.id for t in out):
            out.append({"id": rec.id, "title": rec.title or rec.id, "source": "plugin"})
    return out


@studio_api.get("/studio/plugins")
def plugins_list():
    """Installed plugins, their state and browser files, and the selectable themes."""
    state = extensions.read_state()
    disabled = set(state["disabled"])
    return jsonify(plugins=[_entry(r, disabled) for r in _records()], theme=state["theme"], themes=_themes(),
                   safe_mode=extensions.safe_mode())


@studio_api.post("/studio/plugins/<plugin_id>/<action>")
def plugins_toggle(plugin_id: str, action: str):
    """``enable`` / ``disable``. The reply says when only a restart finishes the change."""
    require_local_csrf()
    if action not in ("enable", "disable"):
        abort(404)
    rec = _record(plugin_id)
    if rec is None:
        return _json_error(LookupError(f"no plugin {plugin_id!r}"), 404)
    enable = action == "enable"
    extensions.set_enabled(plugin_id, enable)
    # Enabling needs an import this process has not done; disabling Python parts needs it undone.
    restart = (enable and rec.status != "ok") or (not enable and _has_python(rec))
    disabled = set(extensions.read_state()["disabled"])
    return jsonify(plugin=_entry(rec, disabled), restart_required=bool(restart and not extensions.safe_mode()))


@studio_api.post("/studio/theme")
def theme_set():
    """``{"id": theme}``: one of the listed themes; the Studio swaps the stylesheet itself."""
    require_local_csrf()
    theme_id = (request.get_json(silent=True) or {}).get("id")
    known = [t["id"] for t in _themes()]
    if theme_id not in known:
        return _json_error(ValueError(f"unknown theme {theme_id!r}"), 400, themes=known)
    extensions.set_theme(theme_id)
    return jsonify(theme=theme_id)


@studio.get("/plugins/<plugin_id>/<path:rel>")
def plugin_file(plugin_id: str, rel: str):
    """A file from an active plugin's declared ``web`` folder (``theme.css``: its declared theme)."""
    rec = next((r for r in extensions.loaded() if r.id == plugin_id), None)
    if rec is None or not extensions.active(rec):
        abort(404)
    if rel == "theme.css" and themes.plugin_css(rec) is not None:
        path = themes.plugin_css(rec)
    else:
        folder = extensions.web_dir(rec)
        if folder is None or Path(rel).is_absolute():
            abort(404)
        try:
            path = (folder / rel).resolve()
        except (OSError, RuntimeError):
            abort(404)
        if not path.is_relative_to(folder) or not path.is_file():  # traversal and symlinks out of the folder
            abort(404)
    try:
        response = send_from_directory(path.parent, path.name, max_age=0)
    except NotFound:
        abort(404)
    mime = _MIME.get(path.suffix.lower())
    if mime:
        response.mimetype = mime
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-cache"
    return response


#: The only types a converted file is served as.
_CONVERT_MIME = {"pdf": "application/pdf", "png": "image/png"}


@studio_api.get("/studio/projects/<project_name>/convert")
def plugin_convert(project_name: str):
    """``?path=rel&to=pdf|png``: a project file converted by a plugin (cached under ``.alfrd/cache/convert``)."""
    from flask import send_file

    from alfrd.extensions import convert as conversion
    from alfrd.gui.studio import _project_root

    rel = request.args.get("path", "")
    to = (request.args.get("to") or "pdf").strip().lower()
    try:
        path = conversion.convert(_project_root(project_name), rel, to)
    except conversion.ConvertError as error:
        return _json_error(error, error.status, detail_tail=error.detail_tail)
    response = send_file(path, mimetype=_CONVERT_MIME[to.lstrip(".")], conditional=True, max_age=0)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-cache"
    return response


__all__ = ["plugin_convert", "plugin_file", "plugins_list", "plugins_toggle", "theme_set"]
