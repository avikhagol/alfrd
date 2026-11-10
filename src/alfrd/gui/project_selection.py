"""Persist the Studio's shown projects alongside its runtime database."""

import json
import os
import tempfile
from pathlib import Path


def _path(app):
    service = app.config.get("RUNTIME_SERVICE")
    database = getattr(getattr(service, "store", None), "database", None)
    if database is None or str(database) == ":memory:":
        return None
    database = Path(database).expanduser().resolve()
    return database.with_name(f"{database.name}.studio-projects.json")


def restore(app):
    path = _path(app)
    if path is None:
        return
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(saved, dict) or saved.get("version") != 1 or "shown" not in saved:
            return
        shown = saved.get("shown")
        if shown is not None and (not isinstance(shown, list) or not all(isinstance(p, str) for p in shown)):
            return
        known = {p.identifier or p.name for p in app.config["RUNTIME_SERVICE"].list_projects()}
        scope = None if shown is None else list(dict.fromkeys(p for p in shown if p in known))
        # A stale or empty selection must never strand the project picker.
        app.config["STUDIO_PROJECTS"] = scope or None
        if app.config.get("STUDIO_DEFAULT_PROJECT") not in (known if scope is None else scope):
            app.config["STUDIO_DEFAULT_PROJECT"] = None
    except (OSError, ValueError):
        pass  # Missing, damaged or unavailable state: retain the startup defaults.


def save(app):
    path = _path(app)
    if path is None:
        return
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = stream.name
            json.dump({"version": 1, "shown": app.config.get("STUDIO_PROJECTS")}, stream)
        os.replace(temporary, path)
    except OSError as error:
        app.logger.warning("Could not save Studio project selection: %s", error)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
