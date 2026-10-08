"""Serve the static ALFRD Studio from the Flask app and expose the two
small JSON endpoints it needs in server mode.

The Studio is a client-side application (``alfrd.web``). When it is loaded
from ``alfrd serve`` it detects ``/api/studio/session`` and switches to server
mode: projects and matrices come from the existing read-only API, and the
only mutations it performs (connect a project path, retry a step) go through
the same loopback + CSRF gate as the rest of the dashboard.
"""

from __future__ import annotations

import re
import subprocess
import threading
from pathlib import Path

from flask import Blueprint, abort, current_app, jsonify, request, send_file, send_from_directory

from alfrd import __version__
from alfrd.web import MIME_TYPES, web_root

studio = Blueprint("studio", __name__, url_prefix="/studio")
studio_api = Blueprint("studio_api", __name__, url_prefix="/api")


def _send(filename: str):
    root = web_root()
    # send_from_directory rejects traversal (``..``) and absolute paths.
    response = send_from_directory(root, filename, max_age=0)
    mime = MIME_TYPES.get(Path(filename).suffix.lower())
    if mime:
        response.mimetype = mime
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@studio.get("/")
def studio_index():
    return _send("index.html")


@studio.get("/<path:filename>")
def studio_asset(filename: str):
    if filename == "theme.css":
        from alfrd.extensions.themes import css_path, current_theme
        from werkzeug.exceptions import NotFound

        path = css_path(current_theme(), plugins=False)  # plugin themes: /studio/plugins/<id>/theme.css
        try:
            response = send_from_directory(path.parent, path.name, mimetype="text/css", max_age=0)
        except (NotFound, OSError):
            # A theme may disappear between resolution and send (e.g. uninstall).
            response = current_app.response_class(":root { color-scheme: dark; }\n", mimetype="text/css")
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response
    if filename.endswith((".py", ".pyc")) or "__pycache__" in filename:
        abort(404)
    return _send(filename)


@studio_api.get("/studio/session")
def studio_session():
    """Tell the Studio it is served by ALFRD and hand it a CSRF token."""
    from alfrd.gui.security import csrf_token, mutations_enabled, runtime_enabled

    return jsonify(
        app="alfrd",
        version=__version__,
        runtime_enabled=runtime_enabled(),
        mutations_enabled=mutations_enabled(),
        csrf_token=csrf_token(),
        demo=bool(current_app.config.get("STUDIO_DEMO")),
        default_project=current_app.config.get("STUDIO_DEFAULT_PROJECT"),
        projects=current_app.config.get("STUDIO_PROJECTS"),
        can_quit=bool(current_app.config.get("STUDIO_SHUTDOWN")) and mutations_enabled(),
        # Settings → Plugins "Restart now": `alfrd serve` re-executes itself (not under --debug).
        can_restart=bool(current_app.config.get("STUDIO_RESTART")) and mutations_enabled(),
        # Import → Connect → Browse… lists server folders (loopback + CSRF, like mutations).
        can_browse=mutations_enabled(),
        # Folders without alfrd.yaml can be connected with the default manifest.
        default_manifest=_default_manifest_available(),
        live={
            "enabled": float(current_app.config.get("STUDIO_LIVE_INTERVAL", 2.0) or 0) > 0
            and current_app.config.get("RUNTIME_SERVICE") is not None,
            "interval": float(current_app.config.get("STUDIO_LIVE_INTERVAL", 2.0) or 0),
            "idle": float(current_app.config.get("STUDIO_LIVE_IDLE", 5.0)),
        },
    )


@studio_api.post("/projects/connect")
def connect_project_api():
    """JSON twin of ``/dashboard/connect`` used by the Studio's Import dialog."""
    from alfrd.gui.routes import connect_manifest_path

    payload = request.get_json(silent=True) or {}
    path = str(payload.get("path") or "").strip()
    if not path:
        return jsonify(error={"code": 400, "message": "path is required"}), 400
    project, errors, status = connect_manifest_path(path)
    if errors:
        return jsonify(error={"code": status, "message": "; ".join(errors)}), status
    return jsonify(project), 201


@studio_api.post("/studio/projects/create")
def create_project_api():
    from alfrd.project_creation import create_project
    from alfrd.agent_loop import DEFAULT_ITERATIONS

    service = current_app.config.get("RUNTIME_SERVICE")
    if service is None:
        return _json_error(ValueError("project creation needs a runtime-backed server"), 400)
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict) or not isinstance(payload.get("path"), str) or not payload["path"].strip():
        return _json_error(ValueError("path is required"), 400)
    try:
        project, _ = create_project(service, payload["path"], name=payload.get("name"),
                                    template=payload.get("template", "basic"), task=payload.get("task", ""),
                                    iterations=payload.get("iterations", DEFAULT_ITERATIONS),
                                    sequence=payload.get("sequence"))
    except FileExistsError as error:
        return _json_error(error, 409)
    except (ValueError, OSError, TypeError) as error:
        return _json_error(error, 400)
    scope = current_app.config.get("STUDIO_PROJECTS")
    if isinstance(scope, list) and project.identifier not in scope:
        scope.append(project.identifier)
    current_app.config["STUDIO_DEFAULT_PROJECT"] = project.identifier
    return jsonify(name=project.name, identifier=project.identifier, root=project.root_path), 201


@studio_api.get("/studio/project-templates")
def project_templates():
    import yaml

    return jsonify(templates=[{"name": path.stem, "description": (yaml.safe_load(path.read_text()) or {}).get("description", "")}
                              for path in sorted((web_root() / "assets" / "templates").glob("*.yaml"))])


# ---------------------------------------------------------------------------
# AVICA tree (read-only; confined to the connected project's root directory)


def _project_root(project_name: str) -> Path:
    from alfrd.runtime import RuntimeNotFound

    service = current_app.config.get("RUNTIME_SERVICE")
    if service is None:
        abort(404, description="AVICA views need a runtime-backed `alfrd serve`")
    try:
        project = service.get_project_by_selector(project_name)
    except RuntimeNotFound:
        abort(404, description=f"Project {project_name!r} not found")
    root = Path(project.root_path)
    if not root.is_dir():
        abort(404, description=f"Project root {root} is not a directory")
    return root


def _sync_project_name(project_name: str, root: Path) -> str | None:
    """Copy alfrd.yaml's ``name``/``description`` to the runtime row (identifier unchanged); the synced name."""
    import yaml

    from alfrd.gui.routes import _SAFE_PROJECT_NAME
    from alfrd.manifest_default import local_manifest
    from alfrd.runtime import RuntimeNotFound

    service = current_app.config.get("RUNTIME_SERVICE")
    path = local_manifest(root)
    if service is None or path is None:
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        name, description = data.get("name"), data.get("description")
        if not isinstance(name, str) or _SAFE_PROJECT_NAME.fullmatch(name) is None:
            return None
        return service.sync_project_manifest(project_name, name, description if isinstance(description, str) else None).name
    except (OSError, UnicodeError, yaml.YAMLError, AttributeError, RuntimeNotFound, ValueError):
        return None


def _json_error(error: Exception, status: int, **extra):
    """``{"error": {"code", "message", **extra}}``; e.g. ``reason="permission"`` tells
    a filesystem 403 apart from the session/CSRF 403s raised by ``require_local_csrf``."""
    return jsonify(error={"code": status, "message": str(error), **extra}), status


@studio_api.get("/studio/avica/<project_name>/layout")
def avica_layout(project_name: str):
    from alfrd.avica_layout import scan_layout

    return jsonify(scan_layout(_project_root(project_name)))


@studio_api.get("/studio/avica/<project_name>/fits-files")
def avica_fits_files(project_name: str):
    """Suggest basenames from configured FITS storage, never a caller-supplied path."""
    import os
    import stat
    from alfrd.avica_layout import resolve_config, resolve_dir
    from alfrd.gui.security import require_local_csrf

    if "dir" in request.args:
        return _json_error(ValueError("dir is not supported; use folder_for_fits"), 400)
    root = _project_root(project_name).resolve()
    try:
        value = resolve_config(root).get("folder_for_fits")
        if value is None:
            return jsonify(files=[], truncated=False, note="folder_for_fits is unset")
        if "$" in str(value):
            return jsonify(files=[], truncated=False, note="folder_for_fits contains an unresolved variable")
        folder = (resolve_dir(root, value) or root).resolve()
        if not folder.is_relative_to(root):
            require_local_csrf()
        if not stat.S_ISDIR(folder.stat().st_mode):
            raise FileNotFoundError(f"FITS folder {folder} not found")
        files = set()
        def fail(error):
            raise error
        for directory, dirs, names in os.walk(folder, followlinks=False, onerror=fail):
            depth = len(Path(directory).relative_to(folder).parts)
            dirs[:] = sorted(d for d in dirs if not d.startswith(".")) if depth < 3 else []
            for name in sorted(names):
                lower = name.lower()
                if not name.startswith(".") and (lower.endswith("fits") or ".idi" in lower):
                    files.add(name)
                    if len(files) > 2000:
                        return jsonify(files=sorted(files)[:2000], truncated=True)
        return jsonify(files=sorted(files), truncated=False)
    except PermissionError as error:
        return _json_error(error, 403)
    except FileNotFoundError as error:
        return _json_error(error, 404)


@studio_api.post("/studio/avica/<project_name>/summary")
def avica_summary(project_name: str):
    """Run ``avica pipe config --summary`` in the project root (loopback + CSRF only)."""
    from alfrd.avica_layout import resolve_config

    try:
        config = resolve_config(_project_root(project_name), run_summary=True)
    except FileNotFoundError as error:
        return _json_error(error, 424)
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
        return _json_error(error, 502)
    return jsonify(config.to_dict())


@studio_api.get("/studio/avica/<project_name>/workdir")
def avica_workdir(project_name: str):
    from alfrd.avica_layout import read_workdir

    code = request.args.get("code", "")
    target = request.args.get("target") or None
    try:
        return jsonify(read_workdir(_project_root(project_name), code, target))
    except ValueError as error:
        return _json_error(error, 400)
    except FileNotFoundError as error:
        return _json_error(error, 404)


@studio_api.get("/studio/avica/<project_name>/logs/<name>")
def avica_log(project_name: str, name: str):
    from alfrd.avica_layout import read_log

    try:
        text = read_log(_project_root(project_name), name)
    except ValueError as error:
        return _json_error(error, 400)
    except FileNotFoundError as error:
        return _json_error(error, 404)
    return current_app.response_class(text, mimetype="text/plain")


# ---------------------------------------------------------------------------
# Project tree driven by alfrd.yaml (any template)


@studio_api.get("/studio/projects/<project_name>/scan")
def project_scan(project_name: str):
    """The files the Studio reads for this project (same JSON as `alfrd avica scan --bundle`).

    Logs are listed with sizes only; the Studio fetches one when it is opened.
    ``?only=rel1&only=rel2`` returns (and reads) just those files: the live
    refresh after a ``tree`` event.
    """
    from alfrd.avica_layout import collect_studio_files

    only = request.args.getlist("only") or None
    live_state = None
    if only is None:
        hub = live_hub()
        if hub is not None:
            live_state = hub.touch(project_name)  # the version this scan is at least as new as
    root = _project_root(project_name)
    try:
        data = collect_studio_files(root, log_tail=0, only=only)
    except FileNotFoundError as error:
        return _json_error(error, 404)
    if only is None:
        data["project_name"] = _sync_project_name(project_name, root)
    if live_state:
        data["live"] = {"epoch": live_state["epoch"], "version": live_state["version"]}
    return jsonify(data)


_ALLOWED: dict[tuple[str, str], tuple[float, Path]] = {}
_ALLOWED_TTL = 30.0


def _allowed_cached(root: Path, rel: str) -> Path:
    """``allowed_file`` for repeated tail polls (it expands alfrd.yaml's log patterns)."""
    import time

    from alfrd.studio_defs import allowed_file

    key = (str(root), rel)
    hit = _ALLOWED.get(key)
    now = time.monotonic()
    if hit and now - hit[0] < _ALLOWED_TTL and hit[1].is_file():
        return hit[1]
    path = allowed_file(root, rel)
    if len(_ALLOWED) > 2000:
        _ALLOWED.clear()
    _ALLOWED[key] = (now, path)
    return path


#: Types ``/file?raw=1`` serves as bytes (the image and PDF viewers); never HTML or SVG.
RAW_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
             ".webp": "image/webp", ".bmp": "image/bmp", ".pdf": "application/pdf"}


def _project_file_raw(project_name: str, rel: str):
    mimetype = RAW_TYPES.get(Path(rel).suffix.lower())
    if mimetype is None:
        return _json_error(ValueError("raw=1 serves images and PDF only"), 415)
    root = _project_root(project_name)
    try:
        path = _allowed_cached(root, rel)
    except ValueError as error:
        return _json_error(error, 400)
    except PermissionError as error:
        # Images and PDFs a views `text`/`image` panel declares open in the viewers too.
        from alfrd.layout_generic import declared_source

        if not declared_source(root, rel):
            return _json_error(error, 403)
        path = (root.resolve() / rel).resolve()
        if not path.is_relative_to(root.resolve()):
            return _json_error(error, 403)
    except FileNotFoundError as error:
        return _json_error(error, 404)
    response = send_file(path, mimetype=mimetype, conditional=True, max_age=0)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-cache"
    return response


@studio_api.get("/studio/projects/<project_name>/file")
def project_file(project_name: str):
    """One log declared in alfrd.yaml (step logs or log artifacts).

    Without ``offset``: the last 400 kB. With ``offset=N`` (and the ``id`` the
    previous reply gave): only what was appended since, for live tails. The
    reply headers say where to continue: ``X-Offset``, ``X-File-Id``,
    ``X-File-Size``, and ``X-Reset: 1`` when the text replaces the old one
    (rotation, truncation, or too much appended).
    """
    from alfrd.studio_defs import read_range

    rel = request.args.get("path", "")
    if request.args.get("raw") == "1":
        return _project_file_raw(project_name, rel)
    raw = request.args.get("offset")
    try:
        offset = int(raw) if raw not in (None, "") else None
    except ValueError:
        return _json_error(ValueError("offset must be an integer"), 400)
    try:
        root = _project_root(project_name)
        try:
            path = _allowed_cached(root, rel)
        except PermissionError:
            # A views `text`/`image` panel's source (e.g. notes.md): the panel already shows it.
            from alfrd.layout_generic import declared_source

            if not declared_source(root, rel):
                raise
            path = (root.resolve() / rel).resolve()
            path.relative_to(root.resolve())  # ValueError when a symlink leads outside
        chunk = read_range(path, offset, file=request.args.get("id") or None)
    except ValueError as error:
        return _json_error(error, 400)
    except PermissionError as error:
        return _json_error(error, 403)
    except FileNotFoundError as error:
        return _json_error(error, 404)
    response = current_app.response_class(chunk["text"], mimetype="text/plain")
    response.headers["X-Offset"] = str(chunk["offset"])
    response.headers["X-File-Id"] = chunk["id"]
    response.headers["X-File-Size"] = str(chunk["size"])
    response.headers["X-File-Mtime"] = str(chunk["mtime"])
    response.headers["X-Reset"] = "1" if chunk["reset"] else "0"
    response.headers["Cache-Control"] = "no-store"
    return response


# ---------------------------------------------------------------------------
# Live updates (see alfrd.studio_live): one event stream per Studio tab, a
# JSON poll as fallback. Both are read-only.


def live_hub():
    """The server's LiveHub (created on first use); None when live updates are off."""
    hub = current_app.extensions.get("alfrd_live")
    if hub is not None:
        return hub or None
    interval = float(current_app.config.get("STUDIO_LIVE_INTERVAL", 2.0) or 0)
    service = current_app.config.get("RUNTIME_SERVICE")
    if interval <= 0 or service is None:
        current_app.extensions["alfrd_live"] = False
        return None
    from alfrd.runtime import RuntimeNotFound
    from alfrd.studio_live import LiveHub

    def root_of(name: str) -> Path | None:
        try:
            root = Path(service.get_project_by_selector(name).root_path)
        except RuntimeNotFound:
            return None
        from alfrd.manifest_default import has_manifest

        return root if root.is_dir() and has_manifest(root) else None  # local alfrd.yaml or the default

    database = current_app.config.get("RUNTIME_DATABASE")
    paths = [database, f"{database}-wal"] if database and database != ":memory:" else []
    hub = LiveHub(root_of, runtime_paths=paths, interval=interval,
                  idle=float(current_app.config.get("STUDIO_LIVE_IDLE", 5.0)))
    current_app.extensions["alfrd_live"] = hub
    return hub


def _live_projects() -> list[str]:
    names = [n for n in request.args.get("projects", "").split(",") if n]
    scope = current_app.config.get("STUDIO_PROJECTS")
    if isinstance(scope, list):
        names = [n for n in names if n in scope]
    return names[:20]


def _poke(project_name: str) -> None:
    hub = current_app.extensions.get("alfrd_live")
    if hub:
        hub.poke(project_name)


@studio_api.get("/studio/events")
def live_events():
    """Server-Sent Events: ``hello`` (watcher state), ``tree``, ``runtime``, ``reset``; a ping every 15 s."""
    import json
    import queue

    from flask import Response

    hub = live_hub()
    if hub is None:
        return _json_error(RuntimeError("live updates are off (alfrd serve --live-interval 0)"), 404)
    projects = _live_projects()
    heartbeat = float(current_app.config.get("STUDIO_LIVE_HEARTBEAT", 15.0))

    def stream():
        sub, hello = hub.subscribe(projects)
        try:
            yield "retry: 3000\n"
            yield f"event: hello\ndata: {json.dumps({'state': hello, 'interval': hub.interval, 'idle': hub.idle})}\n\n"
            while True:
                try:
                    event = sub.queue.get(timeout=heartbeat)
                except queue.Empty:
                    yield ": ping\n\n"
                    continue
                yield f"event: {event['type']}\ndata: {json.dumps(event, default=str)}\n\n"
                if sub.dropped:
                    sub.dropped = False
                    yield "event: reset\ndata: {}\n\n"
        finally:
            hub.unsubscribe(sub)

    return Response(stream(), mimetype="text/event-stream", headers={
        "Cache-Control": "no-store", "X-Accel-Buffering": "no",
    })


@studio_api.get("/studio/changes")
def live_changes():
    """Poll fallback: ``?projects=a,b&since={"a": "epoch:version", "@runtime": …}`` → state + events."""
    import json

    hub = live_hub()
    if hub is None:
        return _json_error(RuntimeError("live updates are off"), 404)
    try:
        since = json.loads(request.args.get("since") or "{}")
        if not isinstance(since, dict):
            raise ValueError
    except ValueError:
        return _json_error(ValueError("since must be a JSON object"), 400)
    return jsonify({**hub.changes(since, _live_projects()), "interval": hub.interval, "idle": hub.idle})


@studio_api.post("/studio/projects/<project_name>/manifest")
def project_manifest_save(project_name: str):
    """Save alfrd.yaml from Project settings (loopback + CSRF; old file kept as .bak).

    Conflict guard: with ``base_hash`` (SHA-256 of the text the Studio loaded)
    or ``base_text``, a file changed on disk since then is not overwritten: 409
    with ``current_text`` and a ``diff``. ``force: true`` overwrites anyway.
    Every save is a version in the history (alfrd.history).
    """
    from alfrd import history
    from alfrd.studio_defs import manifest_file

    payload = request.get_json(silent=True) or {}
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        return _json_error(ValueError("text is required"), 400)
    root = _project_root(project_name)
    path = manifest_file(root) or root / "alfrd.yaml"
    try:
        entry = history.save(root, "alfrd.yaml", text, source="studio", message=payload.get("message"),
                             base_hash=payload.get("base_hash"), base_text=payload.get("base_text"),
                             force=bool(payload.get("force")))
    except history.Conflict as conflict:
        return jsonify(error={"code": 409, "message": str(conflict)}, current_text=conflict.current,
                       current_hash=conflict.current_hash, diff=conflict.diff), 409
    except Exception as error:  # yaml errors, missing name, OS errors
        return _json_error(error, 400)
    _poke(project_name)
    return jsonify(saved=path.name, backup=f"{path.name}.bak", version=entry, project_name=_sync_project_name(project_name, root), hash=history.text_hash(text if text.endswith("\n") else text + "\n"))


@studio_api.get("/studio/projects/<project_name>/quickstart")
def project_quickstart(project_name: str):
    """Setup forms declared under ``quickstart:`` (template or alfrd.yaml), with current values."""
    from alfrd import quickstart

    try:
        return jsonify(forms=quickstart.forms(_project_root(project_name)))
    except (ValueError, OSError) as error:
        return _json_error(error, 400)


@studio_api.post("/studio/projects/<project_name>/quickstart/<form_id>")
def project_quickstart_apply(project_name: str, form_id: str):
    """Write a setup form's changed values (loopback + CSRF). Body ``{"values": {field: value}}``."""
    from alfrd import quickstart

    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", form_id):
        return _json_error(ValueError("bad form id"), 400)
    payload = request.get_json(silent=True) or {}
    try:
        result = quickstart.apply(_project_root(project_name), form_id, payload.get("values") or {})
    except quickstart.QuickstartError as error:
        return jsonify(error={"code": 400, "message": str(error)}, field_errors=error.errors), 400
    except (ValueError, OSError) as error:
        return _json_error(error, 400)
    _poke(project_name)
    return jsonify(result)


@studio_api.post("/studio/avica/<project_name>/config")
def avica_config_update(project_name: str):
    """Write ``<step>.<param> = value`` lines to avica.inp (loopback + CSRF).

    The cached ``avica pipe config --summary`` rows are patched so the Studio
    shows the new values; run `alfrd avica summary` to re-resolve them.
    """
    import json

    from alfrd.avica_layout import SUMMARY_FILENAMES, manifest_avica, resolve_config
    from alfrd.studio_defs import update_key_values

    root = _project_root(project_name)
    payload = request.get_json(silent=True) or {}
    changes = payload.get("changes")
    if not isinstance(changes, dict) or not changes:
        return _json_error(ValueError("changes must be a non-empty mapping"), 400)
    block = manifest_avica(root)
    config_name = str(block.get("config") or "avica.inp")
    if "/" in config_name or config_name.startswith("."):
        return _json_error(ValueError("invalid avica.config"), 400)
    from alfrd import history

    tracked = history.is_tracked(root, config_name)
    if tracked:
        history.ensure_baseline(root, config_name)
    try:
        written = update_key_values(root / config_name, changes)
    except OSError as error:
        return _json_error(error, 500)
    if tracked:
        history.record(root, config_name, source="studio", message="parameters: " + ", ".join(sorted(written))[:200])
    cache = root / str(block.get("config_summary_cache") or SUMMARY_FILENAMES[0])
    if cache.is_file() and cache.suffix == ".json":
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            for key, value in written.items():
                step, _, param = key.rpartition(".")
                for row in data.get("rows", []):
                    if row.get("parameter") == param and (row.get("step") == step or (not step and (row.get("step") in ("", "other") or str(row.get("source", "")).endswith("/core")))):
                        row["value"] = value
                        row["source"] = f"{config_name}/studio"
            cache.write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")
        except (OSError, ValueError):
            pass
    _poke(project_name)
    return jsonify(written=written, file=config_name, config=resolve_config(root).to_dict())


ACTIVE_PLAN_STATUSES = ("running", "paused", "interrupted")
REMOVAL_ACTIVE_REASON = "Stop this project's active runs before removing it."
REMOVAL_READONLY_REASON = "Project removal is available only in a writable local Studio session."


def _project_active_jobs(row, active_run_ids=()) -> list[dict]:
    """Plans (agent loop / plan runner) and runtime runs of one project that are still live."""
    from alfrd.runtime import scheduler

    jobs: list[dict] = []
    if row.root_path:
        root = Path(row.root_path).expanduser()
        if root.is_dir():
            for plan in scheduler.list_plans(root):
                pid = str(plan.get("id") or "")
                status = plan.get("status")
                try:
                    alive = bool(pid) and scheduler.PlanDir(root.resolve(), pid).runner_alive()
                except Exception:  # noqa: BLE001 - a broken plan folder must not hide the others
                    alive = False
                if status in ACTIVE_PLAN_STATUSES or alive:
                    jobs.append({"kind": "plan", "id": pid, "status": status, "runner_alive": alive})
    jobs.extend({"kind": "run", "id": run_id, "status": "running", "runner_alive": None} for run_id in active_run_ids)
    return jobs


def _removal_preview(service, project_name: str, *, scope: bool = True, measure: bool = False) -> dict:
    """Counts, active jobs and permission for removing a project.

    ``scope=False`` skips the folder inspection (Forget and Delete only need the
    permission checks); ``measure=True`` also walks the folder for its size.
    """
    from alfrd.gui.security import mutations_enabled

    row = service.get_project_by_selector(project_name)
    counts = service.project_removal_counts(row.identifier or project_name)
    active = _project_active_jobs(row, counts.pop("active_run_ids", []))
    writable = mutations_enabled()
    reason = None if writable else REMOVAL_READONLY_REASON
    if writable and active:
        reason = REMOVAL_ACTIVE_REASON
    return {
        "identifier": row.identifier, "name": row.name, "root_path": row.root_path,
        "counts": counts, "active_jobs": active, "active_job_count": len(active),
        "can_remove": reason is None, "reason": reason, "mutations_enabled": writable,
        "delete_scope": _delete_scope(service, row, measure=measure) if scope else None, "delete_reason": None,
    }


DELETE_FILE_CAP = 200_000
ALFRD_FILES = ("alfrd.yaml", "alfrd.yaml.bak", ".alfrd.yaml", ".alfrd.yaml.bak", "alfrd.plan.csv", "alfrd.plan.csv.bak",
               "alfrd.targets.csv", "alfrd.targets.csv.bak", "alfrd.notes.jsonl",
               ".alfrd_project.yaml", ".alfrd_workflow.yaml", "alfrd.db")
#: Project state inside ``.alfrd/`` (removed one by one when ``.alfrd`` also holds global state).
ALFRD_STATE_DIRS = ("plans", "history", "locks", "tmp", "cache")  # cache: plugin conversions


def _alfrd_files(root: Path, database: Path | None) -> list[Path]:
    """ALFRD's own files in a project folder: manifest, plan/targets/notes, ``.alfrd/`` and task bookkeeping.

    Task files, handoffs, worktrees, results and data are never in this list.
    """
    found: set[Path] = set()

    def add(path: Path) -> None:
        if (path.exists() or path.is_symlink()) and path.parent.resolve().is_relative_to(root):
            found.add(path)

    for name in ALFRD_FILES:
        add(root / name)
    try:  # configured names (execution.plan_csv, targets.csv), when they live in the folder
        from alfrd.execution import load_execution
        from alfrd import targets_csv as tc

        for path in (load_execution(root).plan_csv, tc.load_spec(root).csv):
            path = Path(path)
            if path.resolve().is_relative_to(root) and path.resolve() != root:
                add(path)
                add(path.with_name(path.name + ".bak"))
    except Exception:  # noqa: BLE001 - a broken manifest still has the default names above
        pass
    state = root / ".alfrd"
    if state.is_dir() and not state.is_symlink():
        shared = root == Path.home().resolve() or (database is not None and database.is_relative_to(state))
        if shared:
            for name in ALFRD_STATE_DIRS:
                add(state / name)
        else:
            add(state)
    for pattern in (".alfrd-agent-loop*.lock", "*/.alfrd-task.json", "*/.alfrd-agent-loop*.lock", "runs/*/.alfrd"):
        for path in root.glob(pattern):
            if not path.parent.is_symlink():
                add(path)
    return sorted(found)


def _measure_folder(root: Path) -> dict:
    """File count and total size of a folder (symlinks not followed), capped at ``DELETE_FILE_CAP`` files."""
    import os

    out = {"files": 0, "bytes": 0, "truncated": False}
    for folder, _dirs, files in os.walk(root, followlinks=False):
        for name in files:
            out["files"] += 1
            try:
                out["bytes"] += (Path(folder) / name).lstat().st_size
            except OSError:
                pass
        if out["files"] >= DELETE_FILE_CAP:
            out["truncated"] = True
            break
    return out


def _delete_scope(service, row, *, measure: bool = False) -> dict:
    """What Delete would remove, in both modes.

    Default: ALFRD's files (``alfrd_files``) and the database entry; the folder and its
    other files stay. ``all_files``: the whole folder, refused for a symlinked folder, a
    top-level folder, the home folder or one containing it, the folder holding the
    runtime database, and a folder containing another registered project.

    The folder's size (``files``/``bytes``/``truncated``) needs a full walk, so when the
    whole folder may be deleted it is filled in only with ``measure=True``; without it
    those are None and ``measured`` is False.
    """
    out = {"path": row.root_path, "exists": False, "alfrd_files": [], "files": 0, "bytes": 0, "truncated": False,
           "measured": True, "git_repository": False, "allowed": True, "all_files_allowed": False,
           "all_files_reason": None}
    if not row.root_path:
        out["all_files_reason"] = "This project has no folder on record."
        return out
    raw = Path(row.root_path).expanduser()
    root = raw.resolve()
    out["path"] = str(root)
    database = getattr(getattr(service, "store", None), "database", None)
    database = Path(database).expanduser().resolve() if database and str(database) != ":memory:" else None
    if not root.exists():
        out["all_files_allowed"] = True  # already gone: deleting only forgets it
        return out
    if not root.is_dir():
        out["all_files_reason"] = "The project path is not a folder."
        return out
    out["exists"] = True
    out["alfrd_files"] = [str(p.relative_to(root)) + ("/" if p.is_dir() and not p.is_symlink() else "") for p in _alfrd_files(root, database)]
    home = Path.home().resolve()
    others = [Path(p.root_path).expanduser().resolve() for p in service.list_projects()
              if p.root_path and p.identifier != row.identifier]
    reason = None
    if raw.is_symlink():
        reason = "The project folder is a symbolic link; delete its target yourself."
    elif len(root.parts) <= 2:
        reason = f"{root} is a top-level folder; ALFRD will not delete it."
    elif root == home or home.is_relative_to(root):
        reason = "The project folder is (or contains) your home folder."
    elif database and database.is_relative_to(root):
        reason = "The project folder contains the runtime database."
    elif any(o != root and o.is_relative_to(root) for o in others):
        inner = next(o for o in others if o != root and o.is_relative_to(root))
        reason = f"The project folder contains another project ({inner}); remove that one first."
    out["all_files_reason"] = reason
    out["all_files_allowed"] = reason is None
    if reason is None:
        out["git_repository"] = (root / ".git").exists()
        if measure:
            out.update(_measure_folder(root))
        else:
            out.update(files=None, bytes=None, truncated=None, measured=False)
    return out


def _forget(service, project_name: str, row) -> dict:
    """Drop a project from the runtime database and this server's scope (files untouched)."""
    counts = service.forget_project(row.identifier or project_name)
    forgotten = current_app.extensions.setdefault("alfrd_forgotten", [])
    root = str(Path(row.root_path).expanduser().resolve()) if row.root_path else None
    if root and not any(f["root"] == root for f in forgotten):
        forgotten.append({"root": root, "name": row.name, "identifier": row.identifier})
    # The scope may hold the identifier or the (legacy) name: drop every alias.
    aliases = {project_name, row.identifier, row.name} - {None, ""}
    scope = current_app.config.get("STUDIO_PROJECTS")
    if isinstance(scope, list):
        scope[:] = [key for key in scope if key not in aliases]
    if current_app.config.get("STUDIO_DEFAULT_PROJECT") in aliases:
        current_app.config["STUDIO_DEFAULT_PROJECT"] = None
    return counts


@studio_api.get("/studio/projects/<project_name>/removal-preview")
def project_removal_preview(project_name: str):
    """What Forget would remove and whether removal is allowed now (read-only)."""
    from alfrd.runtime import RuntimeNotFound

    service = current_app.config.get("RUNTIME_SERVICE")
    if service is None:
        return _json_error(RuntimeError("no runtime database"), 404)
    measure = request.args.get("size", "").lower() in ("1", "true", "yes")
    try:
        return jsonify(_removal_preview(service, project_name, measure=measure))
    except RuntimeNotFound as error:
        return _json_error(error, 404)


@studio_api.get("/studio/projects/<project_name>/removal-size")
def project_removal_size(project_name: str):
    """Size of the project folder for "Delete all files and folders" (read-only; walks the folder).

    Only measured when deleting the whole folder is allowed; ``measured`` is False otherwise.
    """
    from alfrd.runtime import RuntimeNotFound

    service = current_app.config.get("RUNTIME_SERVICE")
    if service is None:
        return _json_error(RuntimeError("no runtime database"), 404)
    try:
        row = service.get_project_by_selector(project_name)
    except RuntimeNotFound as error:
        return _json_error(error, 404)
    scope = _delete_scope(service, row, measure=True)
    return jsonify({k: scope[k] for k in ("path", "exists", "files", "bytes", "truncated", "measured",
                                          "git_repository", "all_files_allowed")})


@studio_api.post("/studio/projects/<project_name>/delete")
def project_delete(project_name: str):
    """Delete a project (loopback + CSRF): ALFRD's files and the database entry.

    Body ``{"confirm": "<project name>", "all_files": false}``: the exact project name,
    typed by the person. ``all_files: true`` deletes the whole project folder instead
    (refused for folders ``_delete_scope`` marks unsafe). Refused while runs are active.
    """
    import shutil

    from alfrd.runtime import RuntimeNotFound

    service = current_app.config.get("RUNTIME_SERVICE")
    if service is None:
        return _json_error(RuntimeError("no runtime database"), 404)
    try:
        preview = _removal_preview(service, project_name, scope=False)
        row = service.get_project_by_selector(project_name)
    except RuntimeNotFound as error:
        return _json_error(error, 404)
    if not preview["mutations_enabled"]:
        return _json_error(PermissionError(REMOVAL_READONLY_REASON), 403)
    if preview["active_jobs"]:
        return _json_error(RuntimeError(REMOVAL_ACTIVE_REASON), 409)
    payload = request.get_json(silent=True) or {}
    if payload.get("confirm") != row.name:
        return _json_error(ValueError("Type the project name exactly to confirm deletion."), 400)
    all_files = payload.get("all_files") is True
    scope = _delete_scope(service, row, measure=all_files)  # the only walk: for the reported size
    if all_files and not scope["all_files_allowed"]:
        return _json_error(PermissionError(scope["all_files_reason"]), 409)
    root = Path(scope["path"]) if scope["path"] else None
    errors: list[str] = []
    removed: list[str] = []
    if root is not None and root.exists():
        onerror = lambda _f, path, exc: errors.append(f"{path}: {exc[1]}")  # noqa: E731
        if all_files:
            from alfrd import workspaces

            repository = workspaces.repository(root.parent) if root.parent.exists() else None
            shutil.rmtree(root, onerror=onerror)
            removed = ["."]
            if repository and repository.exists():
                subprocess.run(["git", "-C", str(repository), "worktree", "prune"], capture_output=True, timeout=60, check=False)
        else:
            for rel in scope["alfrd_files"]:
                path = root / rel.rstrip("/")
                try:
                    if path.is_dir() and not path.is_symlink():
                        shutil.rmtree(path, onerror=onerror)
                    else:
                        path.unlink()
                    removed.append(rel)
                except OSError as exc:
                    errors.append(f"{path}: {exc}")
            # Runtime run folders (runs/<id>) that held only ALFRD state are now empty.
            runs = root / "runs"
            if runs.is_dir() and not runs.is_symlink():
                for folder in [*runs.iterdir(), runs]:
                    if folder.is_dir() and not folder.is_symlink():
                        try:
                            folder.rmdir()  # only when empty
                        except OSError:
                            pass
    if errors:
        return _json_error(OSError(f"Could not delete everything ({len(errors)} errors), first: {errors[0]}"), 500)
    counts = _forget(service, project_name, row)
    # A deleted project cannot be rediscovered.
    forgotten = current_app.extensions.get("alfrd_forgotten", [])
    forgotten[:] = [f for f in forgotten if f["root"] != scope["path"]]
    return jsonify(deleted=project_name, identifier=row.identifier, name=row.name, path=scope["path"],
                   all_files=all_files, removed=removed, files=scope["files"] if all_files else len(removed),
                   bytes=scope["bytes"] if all_files else None, **counts)


@studio_api.post("/studio/projects/<project_name>/forget")
def project_forget(project_name: str):
    """Remove a project from the runtime database (loopback + CSRF). Files are not touched."""
    from alfrd.runtime import RuntimeNotFound

    service = current_app.config.get("RUNTIME_SERVICE")
    if service is None:
        return _json_error(RuntimeError("no runtime database"), 404)
    try:
        preview = _removal_preview(service, project_name, scope=False)
        if not preview["mutations_enabled"]:
            return _json_error(PermissionError(REMOVAL_READONLY_REASON), 403)
        if preview["active_jobs"]:
            return _json_error(RuntimeError(REMOVAL_ACTIVE_REASON), 409)
        row = service.get_project_by_selector(project_name)
        counts = _forget(service, project_name, row)
    except RuntimeNotFound as error:
        return _json_error(error, 404)
    return jsonify(forgotten=project_name, identifier=row.identifier, name=row.name, **counts)


PROJECT_VISIBILITY = ("hidden", "shown", "opened")


@studio_api.post("/studio/projects/<project_name>/visibility")
def project_visibility(project_name: str):
    """Show, hide or open a remembered project in this server's Studio (loopback + CSRF).

    ``{"state": "hidden" | "shown" | "opened"}``. Only the server's scope
    (``STUDIO_PROJECTS``) and opened project (``STUDIO_DEFAULT_PROJECT``) change:
    the runtime database and the project folder are not touched, so a hidden
    project is shown again without Import → Connect.
    """
    from alfrd.runtime import RuntimeNotFound

    service = current_app.config.get("RUNTIME_SERVICE")
    if service is None:
        return _json_error(RuntimeError("no runtime database"), 404)
    state = str((request.get_json(silent=True) or {}).get("state") or "").strip().lower()
    if state not in PROJECT_VISIBILITY:
        return _json_error(ValueError(f"state must be one of {', '.join(PROJECT_VISIBILITY)}"), 400)
    try:
        row = service.get_project_by_selector(project_name)
    except RuntimeNotFound as error:
        return _json_error(error, 404)
    key = row.identifier or row.name
    scope = current_app.config.get("STUDIO_PROJECTS")
    if scope is None and state == "hidden":
        # --all-projects shows everything: hiding one turns that into an explicit list.
        scope = [p.identifier or p.name for p in service.list_projects()]
        current_app.config["STUDIO_PROJECTS"] = scope
    if isinstance(scope, list):
        if state == "hidden":
            while key in scope:
                scope.remove(key)
        elif key not in scope:
            scope.append(key)
    default = current_app.config.get("STUDIO_DEFAULT_PROJECT")
    if state == "opened":
        current_app.config["STUDIO_DEFAULT_PROJECT"] = key
    elif default == key:
        current_app.config["STUDIO_DEFAULT_PROJECT"] = None
    return jsonify(project=key, state=state, projects=current_app.config.get("STUDIO_PROJECTS"),
                   default_project=current_app.config.get("STUDIO_DEFAULT_PROJECT"))


def _rediscover_candidates() -> list[dict]:
    """Folders Rediscover may register again: the `alfrd serve` folder (or the
    sub-projects it discovered) and projects forgotten since start."""
    from alfrd.manifest_default import local_manifest

    service = current_app.config.get("RUNTIME_SERVICE")
    known = {str(Path(p.root_path).expanduser().resolve()) for p in service.list_projects() if p.root_path} if service else set()
    out: list[dict] = []
    start = current_app.config.get("STUDIO_START_FOLDER")
    items = ([{"root": str(Path(start).resolve()), "name": Path(start).name, "identifier": None, "start": True}] if start else [])
    items += [{"root": str(Path(f).resolve()), "name": Path(f).name, "identifier": None, "start": False, "discovered": True}
              for f in current_app.config.get("STUDIO_DISCOVERED_FOLDERS") or []]
    items += [{**f, "start": False} for f in current_app.extensions.get("alfrd_forgotten", [])]
    for item in items:
        if item["root"] in known or any(o["root"] == item["root"] for o in out):
            continue
        root = Path(item["root"])
        out.append({**item, "exists": root.is_dir(), "default_manifest": root.is_dir() and local_manifest(root) is None})
    return out


@studio_api.get("/studio/projects/rediscover")
def project_rediscover_list():
    """Forgotten projects that Rediscover can restore (read-only)."""
    return jsonify(candidates=_rediscover_candidates())


@studio_api.post("/studio/projects/rediscover")
def project_rediscover():
    """Register the `alfrd serve` folder and forgotten projects again (loopback + CSRF).

    ``{"root": "/path"}`` restores one of the candidates; without it all of them.
    Only folders the server already knew are accepted (use Import → Connect for others).
    """
    from alfrd.manifest import ManifestError
    from alfrd.manifest_default import register_project_folder

    service = current_app.config.get("RUNTIME_SERVICE")
    if service is None:
        return _json_error(RuntimeError("no runtime database"), 404)
    wanted = (request.get_json(silent=True) or {}).get("root")
    candidates = _rediscover_candidates()
    if wanted:
        wanted = str(Path(str(wanted)).expanduser().resolve())
        candidates = [c for c in candidates if c["root"] == wanted]
        if not candidates:
            return _json_error(LookupError(f"{wanted} is not a forgotten project of this server"), 404)
    restored, failed = [], []
    scope = current_app.config.get("STUDIO_PROJECTS")
    for item in candidates:
        try:
            project, used_default = register_project_folder(service, item["root"])
        except (ManifestError, OSError) as error:
            failed.append({"root": item["root"], "error": str(error)})
            continue
        if isinstance(scope, list) and project.identifier not in scope:
            scope.append(project.identifier)
        if (item["start"] or item.get("discovered")) and not current_app.config.get("STUDIO_DEFAULT_PROJECT"):
            current_app.config["STUDIO_DEFAULT_PROJECT"] = project.identifier
        current_app.extensions["alfrd_forgotten"] = [f for f in current_app.extensions.get("alfrd_forgotten", []) if f["root"] != item["root"]]
        restored.append({"root": item["root"], "name": project.name, "identifier": project.identifier, "default_manifest": used_default})
    return jsonify(restored=restored, failed=failed, projects=scope,
                   default_project=current_app.config.get("STUDIO_DEFAULT_PROJECT"))


# ---------------------------------------------------------------------------
# Server folder browser (Import → Connect → Browse…). Listing folders is
# sensitive, so it needs the same loopback + CSRF checks as a mutation.

FS_LIST_LIMIT = 500


def _fs_default_path() -> Path:
    start = current_app.config.get("STUDIO_START_FOLDER") or current_app.config.get("STUDIO_BROWSE_ROOT")
    if not start:
        discovered = current_app.config.get("STUDIO_DISCOVERED_FOLDERS") or []
        start = str(Path(discovered[0]).parent) if discovered else None
    return Path(start) if start and Path(start).is_dir() else Path.home()


def _manifest_name(folder: Path, manifest: Path) -> str | None:
    import yaml

    from alfrd.avica_layout import load_yaml_cached

    try:
        data = load_yaml_cached(manifest)
    except (OSError, yaml.YAMLError, ValueError):
        return None
    name = data.get("name") if isinstance(data, dict) else None
    return str(name) if name not in (None, "") else None


@studio_api.get("/studio/fs/list")
def fs_list():
    """Sub-folders of ``path`` on the server (``?path=&hidden=1``), marking ALFRD projects.

    ``{path, parent, home, start, writable, entries: [{name, path, is_project, manifest_name?, is_ms}], truncated}``.
    Files are never listed; ``*.ms`` folders are listed but not meant to be opened.
    ``writable`` is a hint for New folder (``os.access``); mkdir may still fail.
    """
    import os

    from alfrd.gui.security import require_local_csrf
    from alfrd.manifest_default import local_manifest

    require_local_csrf()
    raw = str(request.args.get("path") or "").strip()
    hidden = request.args.get("hidden") in {"1", "true", "yes"}
    try:
        folder = (Path(raw).expanduser() if raw else _fs_default_path()).resolve()
    except (OSError, RuntimeError, ValueError) as error:
        return _json_error(ValueError(f"Bad path {raw!r}: {error}"), 400)
    if not folder.exists():
        return _json_error(FileNotFoundError(f"{folder} does not exist"), 404)
    if not folder.is_dir():
        return _json_error(NotADirectoryError(f"{folder} is not a folder"), 400)
    entries: list[dict] = []
    truncated = False
    try:
        with os.scandir(folder) as it:
            names = []
            for entry in it:
                if not hidden and entry.name.startswith("."):
                    continue
                try:
                    if not entry.is_dir():
                        continue
                except OSError:
                    continue
                names.append(entry.name)
    except PermissionError:
        return _json_error(PermissionError(f"Permission denied: {folder}"), 403)
    except OSError as error:
        return _json_error(OSError(f"Cannot list {folder}: {error.strerror or error}"), 400)
    names.sort(key=lambda n: (n.lower(), n))
    if len(names) > FS_LIST_LIMIT:
        names, truncated = names[:FS_LIST_LIMIT], True
    for name in names:
        child = folder / name
        is_ms = name.lower().endswith(".ms")
        item = {"name": name, "path": str(child), "is_project": False, "is_ms": is_ms}
        if not is_ms:
            try:
                manifest = local_manifest(child)
            except OSError:
                manifest = None
            if manifest is not None:
                item["is_project"] = True
                item["manifest_name"] = _manifest_name(child, manifest)
        entries.append(item)
    parent = str(folder.parent) if folder.parent != folder else None
    here = local_manifest(folder) if folder.is_dir() else None
    return jsonify(
        path=str(folder), parent=parent, home=str(Path.home()), start=str(_fs_default_path().resolve()),
        is_project=here is not None, manifest_name=_manifest_name(folder, here) if here else None,
        writable=os.access(folder, os.W_OK), entries=entries, truncated=truncated,
    )


def _fs_child_name(name) -> str:
    """One new folder name: a single path component, kept exactly as typed."""
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name is required")
    if name in {".", ".."} or any(c in name for c in ("/", "\\", "\0")):
        raise ValueError(f"{name!r} is not a single folder name")
    if Path(name).name != name or len(Path(name).parts) != 1:
        raise ValueError(f"{name!r} is not a single folder name")
    return name


@studio_api.post("/studio/fs/mkdir")
def fs_mkdir():
    """Create one folder ``{parent, name}`` on the server (Browse → New folder; loopback + CSRF).

    201 ``{path, name, parent}``. 400 bad payload or name, 404 parent missing or
    not a folder, 409 a file or folder of that name exists, 403 filesystem
    permission (``error.reason == "permission"``); session/CSRF/mutation-policy
    403s come from ``require_local_csrf`` and carry no ``reason``.
    Missing ancestors are never created.
    """
    import errno

    from alfrd.gui.security import require_local_csrf

    require_local_csrf()
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _json_error(ValueError("expected a JSON object {parent, name}"), 400)
    raw = payload.get("parent")
    if not isinstance(raw, str) or not raw.strip():
        return _json_error(ValueError("parent is required"), 400)
    try:
        name = _fs_child_name(payload.get("name"))
        folder = Path(raw).expanduser().resolve()
    except ValueError as error:
        return _json_error(error, 400)
    except (OSError, RuntimeError) as error:
        return _json_error(ValueError(f"Bad path {raw!r}: {error}"), 400)
    if not folder.is_dir():
        return _json_error(FileNotFoundError(f"{folder} does not exist or is not a folder"), 404)
    target = folder / name
    try:
        target.mkdir()
    except FileExistsError:
        kind = "folder" if target.is_dir() else "file"
        return _json_error(FileExistsError(f"A {kind} named {name!r} already exists in {folder}"), 409)
    except PermissionError:
        return _json_error(PermissionError(f"Permission denied: cannot create a folder in {folder}"), 403, reason="permission")
    except (FileNotFoundError, NotADirectoryError):
        return _json_error(FileNotFoundError(f"{folder} does not exist or is not a folder"), 404)
    except OSError as error:
        if error.errno == errno.EROFS:  # read-only file system: same answer as a permission error
            return _json_error(PermissionError(f"Read-only file system: cannot create a folder in {folder}"), 403, reason="permission")
        return _json_error(OSError(f"Cannot create {target}: {error.strerror or error}"), 400)
    return jsonify(path=str(target), name=name, parent=str(folder)), 201


@studio_api.post("/studio/quit")
def studio_quit():
    """Stop `alfrd serve` (loopback + CSRF). The response is sent before the server stops."""
    import threading

    shutdown = current_app.config.get("STUDIO_SHUTDOWN")
    if not shutdown:
        return _json_error(RuntimeError("this server was not started by `alfrd serve`; stop it where it runs"), 501)
    threading.Timer(0.3, shutdown).start()
    return jsonify(stopping=True)


# Restart waits for mutations in flight (POST/PUT/PATCH/DELETE; reads are retried by the Studio).
_INFLIGHT = {"count": 0}
_INFLIGHT_LOCK = threading.Lock()


@studio_api.before_app_request
def _count_mutation():
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.endpoint != "studio_api.studio_restart":
        with _INFLIGHT_LOCK:
            _INFLIGHT["count"] += 1
        request.environ["alfrd.counted"] = True


@studio_api.teardown_app_request
def _uncount_mutation(_error=None):
    if request.environ.pop("alfrd.counted", False):
        with _INFLIGHT_LOCK:
            _INFLIGHT["count"] -= 1


def _restart_when_idle(restart, wait: float = 10.0) -> None:
    import time

    deadline = time.monotonic() + wait
    while _INFLIGHT["count"] > 0 and time.monotonic() < deadline:
        time.sleep(0.05)
    time.sleep(0.3)  # let the 202 reach the browser
    restart()


@studio_api.post("/studio/restart")
def studio_restart():
    """Restart `alfrd serve` (loopback + CSRF) with the same token and session secret.

    The reply (202) is sent first; the server stops once mutations in flight have
    finished (at most 10 s) and re-executes itself, so the Studio reconnects as
    the same logged-in user and loads plugins anew.
    """
    from alfrd.extensions import jobs
    from alfrd.gui.security import require_local_csrf

    require_local_csrf()
    restart = current_app.config.get("STUDIO_RESTART")
    if not restart:
        return _json_error(RuntimeError("this server cannot restart itself (started with --debug or not by "
                                        "`alfrd serve`); restart it where it runs"), 409)
    if jobs.runner.running():
        return _json_error(RuntimeError("a plugin job is running; restart when it has finished"), 409,
                           job=jobs.runner.running())
    threading.Thread(target=_restart_when_idle, args=(restart,), daemon=True, name="studio-restart").start()
    return jsonify(restarting=True), 202


def _default_manifest_available() -> bool:
    from alfrd.manifest_default import default_manifest_path

    return default_manifest_path() is not None


def studio_available() -> bool:
    try:
        return (web_root() / "index.html").is_file()
    except FileNotFoundError:  # pragma: no cover
        current_app.logger.warning("Studio assets missing; falling back to /dashboard/")
        return False


__all__ = ["studio", "studio_api", "studio_available"]
