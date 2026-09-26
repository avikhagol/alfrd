"""Serve the static ALFRD Studio from the Flask app and expose the two
small JSON endpoints it needs in server mode.

The Studio is a client-side application (``alfrd.web``). When it is loaded
from ``alfrd serve`` it detects ``/api/studio/session`` and switches to server
mode: projects and matrices come from the existing read-only API, and the
only mutations it performs (connect a project path, retry a step) go through
the same loopback + CSRF gate as the rest of the dashboard.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from flask import Blueprint, abort, current_app, jsonify, request, send_from_directory

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


def _json_error(error: Exception, status: int):
    return jsonify(error={"code": status, "message": str(error)}), status


@studio_api.get("/studio/avica/<project_name>/layout")
def avica_layout(project_name: str):
    from alfrd.avica_layout import scan_layout

    return jsonify(scan_layout(_project_root(project_name)))


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
    try:
        data = collect_studio_files(_project_root(project_name), log_tail=0, only=only)
    except FileNotFoundError as error:
        return _json_error(error, 404)
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
    raw = request.args.get("offset")
    try:
        offset = int(raw) if raw not in (None, "") else None
    except ValueError:
        return _json_error(ValueError("offset must be an integer"), 400)
    try:
        path = _allowed_cached(_project_root(project_name), rel)
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
        return root if (root / "alfrd.yaml").is_file() or (root / ".alfrd.yaml").is_file() else None

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
        "Cache-Control": "no-store", "X-Accel-Buffering": "no", "Connection": "keep-alive",
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
    """Save alfrd.yaml from Project settings (loopback + CSRF; old file kept as .bak)."""
    from alfrd.studio_defs import save_manifest

    payload = request.get_json(silent=True) or {}
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        return _json_error(ValueError("text is required"), 400)
    try:
        path = save_manifest(_project_root(project_name), text)
    except Exception as error:  # yaml errors, missing name, OS errors
        return _json_error(error, 400)
    _poke(project_name)
    return jsonify(saved=path.name, backup=f"{path.name}.bak")


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
    try:
        written = update_key_values(root / config_name, changes)
    except OSError as error:
        return _json_error(error, 500)
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


@studio_api.post("/studio/projects/<project_name>/forget")
def project_forget(project_name: str):
    """Remove a project from the runtime database (loopback + CSRF). Files are not touched."""
    from alfrd.runtime import RuntimeNotFound

    service = current_app.config.get("RUNTIME_SERVICE")
    if service is None:
        return _json_error(RuntimeError("no runtime database"), 404)
    try:
        counts = service.forget_project(project_name)
    except RuntimeNotFound as error:
        return _json_error(error, 404)
    scope = current_app.config.get("STUDIO_PROJECTS")
    if isinstance(scope, list) and project_name in scope:
        scope.remove(project_name)
    if current_app.config.get("STUDIO_DEFAULT_PROJECT") == project_name:
        current_app.config["STUDIO_DEFAULT_PROJECT"] = None
    return jsonify(forgotten=project_name, **counts)


@studio_api.post("/studio/quit")
def studio_quit():
    """Stop `alfrd serve` (loopback + CSRF). The response is sent before the server stops."""
    import threading

    shutdown = current_app.config.get("STUDIO_SHUTDOWN")
    if not shutdown:
        return _json_error(RuntimeError("this server was not started by `alfrd serve`; stop it where it runs"), 501)
    threading.Timer(0.3, shutdown).start()
    return jsonify(stopping=True)


def studio_available() -> bool:
    try:
        return (web_root() / "index.html").is_file()
    except FileNotFoundError:  # pragma: no cover
        current_app.logger.warning("Studio assets missing; falling back to /dashboard/")
        return False


__all__ = ["studio", "studio_api", "studio_available"]
