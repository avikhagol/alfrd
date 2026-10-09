"""Studio plugin routes: the list, enable/disable, the theme, plugin browser files,
each plugin's settings and services (``/studio/plugins/<id>/config``), and its project
checks and actions (``/studio/projects/<project>/plugins/<id>/…``).

Every route sits behind the access token (``studio_api`` / ``studio`` blueprints);
the mutations also pass ``require_local_csrf`` (loopback + same origin + CSRF).
Python loads once per process (``extensions.load``): disabling hides a plugin's
browser files at once, everything else that needs an import says
``restart_required``.
"""
from __future__ import annotations

from pathlib import Path

from flask import abort, current_app, jsonify, request, send_from_directory
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
        # Settings → Plugins shows "Configure" when a loaded plugin has settings or services.
        "configurable": bool(plugin and active and (plugin.settings or plugin.services)),
    }


def _themes() -> list[dict]:
    out = [{"id": r.id, "title": r.title or r.id, "source": r.source}
           for r in extensions.discover() if r.source in ("builtin", "theme_dir")]
    for rec in extensions.loaded():
        if extensions.active(rec) and themes.plugin_css(rec) is not None and all(t["id"] != rec.id for t in out):
            out.append({"id": rec.id, "title": rec.title or rec.id, "source": "plugin"})
    return out


def gui_install(state: dict | None = None) -> bool:
    """Studio installs are on unless ``alfrd serve --no-gui-install`` or ``"gui_install": false``."""
    state = state or extensions.read_state()
    return bool(current_app.config.get("PLUGINS_GUI_INSTALL", True) and state["gui_install"])


@studio_api.get("/studio/plugins")
def plugins_list():
    """Installed plugins, their state and browser files, and the selectable themes."""
    from alfrd.extensions import jobs

    state = extensions.read_state()
    from alfrd.extensions import installer
    inventory = installer.installed()
    disabled = set(state["disabled"])
    # ``job``: the running (or last) plugin job, so a reloaded Studio can show its progress.
    job = jobs.runner.get(jobs.runner.last) if jobs.runner.last else None
    return jsonify(plugins=[{**_entry(r, disabled), "managed": r.id in inventory} for r in _records()], theme=state["theme"], themes=_themes(),
                   safe_mode=extensions.safe_mode(), gui_install=gui_install(state),
                   plugin_actions=plugin_actions(state), job=job)


@studio_api.get("/studio/plugins/catalog")
def plugins_catalog():
    """The Browse tab: the catalog (``?refresh=1`` refetches) merged with what is installed.
    ``user`` is who plugins run as (the install dialog's trust note)."""
    import getpass

    from alfrd.extensions import catalog, installer

    state = extensions.read_state()
    got = catalog.get(refresh=request.args.get("refresh") == "1")
    try:
        inventory = installer.installed()
    except installer.InstallError as exc:
        inventory, got["error"] = {}, got["error"] or str(exc)
    return jsonify(catalog_url=got["url"], fetched_at=catalog.fetched_iso(got["fetched_at"]), stale=got["stale"],
                   error=got["error"], name=(got["catalog"] or {}).get("name"),
                   plugins=catalog.merged(got["catalog"], inventory), gui_install=gui_install(state),
                   user=getpass.getuser())


def _job_event(hub):
    """``notify`` for a plugin job: a ``plugin_job`` live event every Studio tab receives."""
    if hub is None:
        return None
    return lambda job: hub.broadcast("plugin_job", job=job)


def _gate(command: str):
    """CSRF, then the install policy: None when a job may start, else the 403 reply."""
    require_local_csrf()
    if not gui_install():
        return _json_error(PermissionError(f"Studio installs are turned off; use: {command}"), 403,
                           reason="gui_install", command=command)
    return None


def _start_job(action: str, payload: dict):
    """One job at a time (409 with the running job), else the started job (202)."""
    from alfrd.extensions import jobs
    from alfrd.gui.studio import live_hub

    try:
        job = jobs.runner.start(action, payload, _job_event(live_hub()))
    except jobs.JobBusy as exc:
        return _json_error(exc, 409, job=jobs.runner.running())
    return jsonify(job=job), 202


@studio_api.post("/studio/plugins/install")
def plugins_install():
    """``{"id", "version"?}``: a catalog version, hash-pinned (newest without ``version``).
    ``{"source", "confirm_id"}``: an advanced source; the job aborts unless it provides ``confirm_id``."""
    from alfrd.extensions import catalog, jobs

    body = request.get_json(silent=True) or {}
    advanced = body.get("source") is not None
    source = str(body.get("source") or "").strip()
    plugin_id = str(body.get("id") or "")
    denied = _gate(f"alfrd plugin install {source or '<wheel URL>'}")
    if denied:
        return denied
    if advanced:
        confirm = str(body.get("confirm_id") or "")
        if not source or source.startswith("-"):
            return _json_error(ValueError("specify a package name, URL or local wheel"), 400)
        if not jobs.ID_RE.match(confirm):
            return _json_error(ValueError("type the plugin id this source provides to confirm"), 400)
        return _start_job("install", {"source": source, "confirm_id": confirm})
    try:
        _, version = catalog.find(catalog.get()["catalog"], plugin_id, body.get("version"))
    except catalog.CatalogError as exc:
        return _json_error(exc, 404)
    return _start_job("install-pinned", {"id": plugin_id, "version": version})


@studio_api.get("/studio/plugins/jobs/<job_id>")
def plugins_job(job_id: str):
    """A job's log since ``?offset=`` (``X-Offset`` says where to continue) and its state (``X-Job-Status``)."""
    from alfrd.extensions import jobs

    job = jobs.runner.get(job_id) if jobs.JOB_ID_RE.match(job_id) else None
    if job is None:
        return _json_error(LookupError(f"no plugin job {job_id!r}"), 404)
    try:
        offset = int(request.args.get("offset") or 0)
        text, end = jobs.read_log(job_id, offset)
    except ValueError:
        return _json_error(ValueError("offset must be an integer"), 400)
    except FileNotFoundError as exc:
        return _json_error(exc, 404)
    response = current_app.response_class(text, mimetype="text/plain")
    response.headers["X-Offset"] = str(end)
    response.headers["X-Job-Status"] = job["status"]
    response.headers["X-Restart-Required"] = "1" if job["restart_required"] else "0"
    response.headers["Cache-Control"] = "no-store"
    return response


@studio_api.post("/studio/plugins/<plugin_id>/<action>")
def plugins_toggle(plugin_id: str, action: str):
    """``enable`` / ``disable``. The reply says when only a restart finishes the change.
    ``update`` / ``remove``: a background job (202) for a plugin alfrd installed."""
    if action in ("update", "remove"):
        from alfrd.extensions import installer

        denied = _gate(f"alfrd plugin {action} {plugin_id}")
        if denied:
            return denied
        try:
            known = plugin_id in installer.installed()
        except installer.InstallError as exc:
            return _json_error(exc, 500)
        if not known:
            return _json_error(LookupError(f"{plugin_id!r} was not installed by alfrd"), 404)
        return _start_job(action, {"id": plugin_id})
    require_local_csrf()
    if action not in ("enable", "disable"):
        abort(404)
    rec = _record(plugin_id)
    if rec is None:
        return _json_error(LookupError(f"no plugin {plugin_id!r}"), 404)
    enable = action == "enable"
    extensions.set_enabled(plugin_id, enable)
    if not enable and rec.plugin is not None:
        from alfrd.extensions import services

        for service in rec.plugin.services:  # a disabled plugin runs nothing
            services.supervisor.stop(plugin_id, service.id)
    # Enabling needs an import this process has not done; disabling Python parts needs it undone.
    restart = (enable and rec.status != "ok") or (not enable and _has_python(rec))
    disabled = set(extensions.read_state()["disabled"])
    return jsonify(plugin=_entry(rec, disabled), restart_required=bool(restart and not extensions.safe_mode()))


# -- settings and services ------------------------------------------------------


def _configurable(plugin_id: str):
    """The loaded, active plugin, or the 404 reply."""
    rec = next((r for r in extensions.loaded() if r.id == plugin_id), None)
    if rec is None or rec.plugin is None or not extensions.active(rec):
        return None, _json_error(LookupError(f"plugin {plugin_id!r} is not loaded"), 404)
    return rec.plugin, None


def _config(plugin) -> dict:
    from alfrd.extensions import services, settings

    return {"id": plugin.id, "title": plugin.title or plugin.id, "settings": settings.public(plugin),
            "missing": settings.missing(plugin), "can_check": plugin.check is not None,
            "services": [services.supervisor.status(plugin.id, s) for s in plugin.services]}


def _audit(action: str, plugin_id: str, error: str | None = None) -> None:
    from alfrd.extensions import jobs

    try:
        jobs.audit(action, plugin_id, result="failed" if error else "ok", error=error)
    except OSError:
        pass


@studio_api.get("/studio/plugins/<plugin_id>/config")
def plugin_config(plugin_id: str):
    """The settings form (secrets only say ``set``) and the services with their state."""
    plugin, denied = _configurable(plugin_id)
    return denied or jsonify(_config(plugin))


@studio_api.post("/studio/plugins/<plugin_id>/config")
def plugin_config_save(plugin_id: str):
    """``{"values": {key: value}, "clear": [key]}``; an empty secret keeps the saved one.
    Running services of the plugin restart so they read the new values."""
    from alfrd.extensions import services, settings

    require_local_csrf()
    plugin, denied = _configurable(plugin_id)
    if denied:
        return denied
    body = request.get_json(silent=True) or {}
    changes, clear = body.get("values") or {}, body.get("clear") or []
    if not isinstance(changes, dict) or not isinstance(clear, list):
        return _json_error(ValueError("expected {values: {...}, clear: [...]}"), 400)
    try:
        settings.save(plugin, changes, [str(k) for k in clear])
    except ValueError as exc:
        return _json_error(exc, 400)
    _audit("settings", plugin_id)
    restarted = services.supervisor.running(plugin_id)
    for service in plugin.services:
        if service.id in restarted:
            services.supervisor.restart(plugin_id, service)
    return jsonify({**_config(plugin), "restarted": restarted})


@studio_api.post("/studio/plugins/<plugin_id>/check")
def plugin_check(plugin_id: str):
    """Run the plugin's ``check(values)`` with the saved settings: ``{ok, message}``."""
    from alfrd.extensions import settings

    require_local_csrf()
    plugin, denied = _configurable(plugin_id)
    if denied:
        return denied
    if plugin.check is None:
        return _json_error(LookupError(f"plugin {plugin_id!r} has no check"), 404)
    lacking = settings.missing(plugin)
    if lacking:
        return jsonify(ok=False, message=f"Fill in: {', '.join(lacking)}.")
    try:
        message = plugin.check(settings.values(plugin_id))
    except ValueError as exc:
        return jsonify(ok=False, message=str(exc))
    except Exception as exc:  # noqa: BLE001 - its text may hold a secret: only the type
        return jsonify(ok=False, message=f"The check failed ({type(exc).__name__}).")
    return jsonify(ok=True, message=str(message or "OK"))


_SERVICE_ACTIONS = ("start", "stop", "restart", "autostart-on", "autostart-off")


@studio_api.post("/studio/projects/<project_name>/plugins/<plugin_id>/check")
def plugin_project_check(project_name: str, plugin_id: str):
    """Read-only project check, gated before resolving the project or calling plugin code."""
    from alfrd.extensions import settings
    from alfrd.gui.studio import _project_root

    require_local_csrf()
    plugin, denied = _configurable(plugin_id)
    if denied:
        return denied
    if plugin.check_project is None:
        return _json_error(LookupError(f"plugin {plugin_id!r} has no project check"), 404)
    root = _project_root(project_name)
    try:
        level, text = plugin.check_project(root, settings.values(plugin_id))
        if level not in ("ok", "warn", "fail") or not isinstance(text, str):
            raise TypeError("invalid project check result")
    except ValueError as exc:  # callback contract: ValueError messages are safe to show
        level, text = "fail", str(exc)
    except Exception as exc:  # noqa: BLE001 - never expose secrets in arbitrary library errors
        level, text = "fail", f"The check failed ({type(exc).__name__})."
    return jsonify(level=level, text=" ".join(text.split())[:120])


#: Project actions: the request body (the payload) and the reply are capped.
MAX_ACTION_PAYLOAD = 256 * 1024
MAX_ACTION_RESULT = 2 * 1024 * 1024


def plugin_actions(state: dict | None = None) -> bool:
    """Mutating project actions are on unless ``alfrd serve --no-plugin-actions`` or ``"plugin_actions": false``."""
    state = state or extensions.read_state()
    return bool(current_app.config.get("PLUGINS_ACTIONS", True) and state["plugin_actions"])


def _run_action(action: extensions.ProjectAction, root: Path, values: dict, payload: dict) -> tuple[bool, object]:
    """``(finished, (data, error))``: ``action.run`` in a worker thread, waited for at most its timeout.

    A timed-out worker can't be stopped; it finishes (or fails) on its own and its result is dropped.
    """
    import threading

    box: dict = {}

    def work() -> None:
        try:
            box["data"] = action.run(root, values, payload)
        except BaseException as exc:  # noqa: BLE001 - reported to the caller below
            box["error"] = exc

    worker = threading.Thread(target=work, name=f"plugin-action-{action.id}", daemon=True)
    worker.start()
    worker.join(float(action.timeout))
    if worker.is_alive():
        return False, (None, None)
    return True, (box.get("data"), box.get("error"))


@studio_api.post("/studio/projects/<project_name>/plugins/<plugin_id>/actions/<action_id>")
def plugin_project_action(project_name: str, plugin_id: str, action_id: str):
    """Run a plugin's project action with the JSON body as payload: ``{ok, data}`` or ``{ok: false, error}``.

    Gated (CSRF/loopback, an active plugin, a known action, the ``plugin_actions`` policy for mutating ones)
    before the project is resolved or any plugin code runs. Mutating calls are audited, without payload values.
    """
    import json

    from alfrd.extensions import settings
    from alfrd.gui.studio import _project_root

    require_local_csrf()
    plugin, denied = _configurable(plugin_id)
    if denied:
        return denied
    action = next((a for a in plugin.project_actions if a.id == action_id), None)
    if action is None:
        return _json_error(LookupError(f"plugin {plugin_id!r} has no project action {action_id!r}"), 404)

    def audit(result: str, error: str | None = None) -> None:
        if action.mutating:
            from alfrd.extensions import jobs

            try:
                jobs.audit("project_action", plugin_id, result=result, error=error, action_id=action_id,
                           project=project_name)
            except OSError:
                pass

    if action.mutating and not plugin_actions():
        audit("refused", "plugin actions are turned off")
        return _json_error(PermissionError("Plugin actions that change files are turned off on this server."), 403,
                           reason="plugin_actions")
    raw = request.get_data(cache=False)
    if len(raw) > MAX_ACTION_PAYLOAD:
        return _json_error(ValueError(f"The request is larger than {MAX_ACTION_PAYLOAD // 1024} KiB."), 413)
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except ValueError:
        payload = None
    if not isinstance(payload, dict):
        return _json_error(ValueError("expected a JSON object"), 400)
    root = _project_root(project_name)
    finished, (data, error) = _run_action(action, root, settings.values(plugin_id), payload)
    if not finished:
        audit("timeout", f"timed out after {action.timeout:g} s")
        return _json_error(TimeoutError(f"The action took longer than {action.timeout:g} s."), 504)
    if error is None and not isinstance(data, dict):
        error = TypeError("project action result must be a dict")
    body = None
    if error is None:
        try:
            body = json.dumps({"ok": True, "data": data}, allow_nan=False)
        except (TypeError, ValueError) as exc:  # not JSON-safe: a plugin bug, never its text
            error = TypeError(type(exc).__name__)
    if error is None and len(body) > MAX_ACTION_RESULT:
        message = "The action result is too large."
    elif isinstance(error, ValueError):  # the contract: ValueError messages are safe to show
        message = " ".join(str(error).split())[:500] or "The action failed."
    elif error is not None:  # never expose secrets in arbitrary library errors
        message = f"The action failed ({type(error).__name__})."
    else:
        audit("ok")
        return current_app.response_class(body, mimetype="application/json")
    audit("failed", message)
    return jsonify(ok=False, error=message)


@studio_api.post("/studio/plugins/<plugin_id>/services/<service_id>/<action>")
def plugin_service(plugin_id: str, service_id: str, action: str):
    """Start, stop or restart a plugin service, or turn its start-with-the-server on or off."""
    from alfrd.extensions import services, settings

    require_local_csrf()
    if action not in _SERVICE_ACTIONS:
        abort(404)
    plugin, denied = _configurable(plugin_id)
    if denied:
        return denied
    service = next((s for s in plugin.services if s.id == service_id), None)
    if service is None:
        return _json_error(LookupError(f"plugin {plugin_id!r} has no service {service_id!r}"), 404)
    if action in ("start", "restart"):
        lacking = settings.missing(plugin)
        if lacking:
            return _json_error(ValueError(f"Fill in and save: {', '.join(lacking)}."), 400)
        getattr(services.supervisor, action)(plugin_id, service)
    elif action == "stop":
        services.supervisor.stop(plugin_id, service_id)
    else:
        extensions.set_autostart(services.key(plugin_id, service_id), action == "autostart-on")
    _audit(f"service-{action}", plugin_id)
    return jsonify(service=services.supervisor.status(plugin_id, service))


@studio_api.get("/studio/plugins/<plugin_id>/services/<service_id>/log")
def plugin_service_log(plugin_id: str, service_id: str):
    """The last lines of a service's log (``?lines=``, at most 200)."""
    from alfrd.extensions import services

    plugin, denied = _configurable(plugin_id)
    if denied:
        return denied
    if all(s.id != service_id for s in plugin.services):
        return _json_error(LookupError(f"plugin {plugin_id!r} has no service {service_id!r}"), 404)
    try:
        lines = max(1, min(200, int(request.args.get("lines") or 40)))
    except ValueError:
        return _json_error(ValueError("lines must be an integer"), 400)
    response = current_app.response_class(services.tail(plugin_id, service_id, lines), mimetype="text/plain")
    response.headers["Cache-Control"] = "no-store"
    return response


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


__all__ = ["plugin_check", "plugin_config", "plugin_config_save", "plugin_convert", "plugin_file", "plugin_project_action", "plugin_project_check",
           "plugin_service",
           "plugin_service_log", "plugins_install", "plugins_job", "plugins_list", "plugins_toggle", "theme_set"]
