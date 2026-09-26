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
        project = service.get_project_by_name(project_name)
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
    """
    from alfrd.avica_layout import collect_studio_files

    try:
        return jsonify(collect_studio_files(_project_root(project_name), log_tail=0))
    except FileNotFoundError as error:
        return _json_error(error, 404)


@studio_api.get("/studio/projects/<project_name>/file")
def project_file(project_name: str):
    """Tail of one log declared in alfrd.yaml (step logs or log artifacts)."""
    from alfrd.studio_defs import allowed_file, read_tail

    rel = request.args.get("path", "")
    try:
        path = allowed_file(_project_root(project_name), rel)
    except ValueError as error:
        return _json_error(error, 400)
    except PermissionError as error:
        return _json_error(error, 403)
    except FileNotFoundError as error:
        return _json_error(error, 404)
    return current_app.response_class(read_tail(path), mimetype="text/plain")


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
