"""Studio superuser page (``/superuser``, ``#/superuser``): settings, statistics and an ops panel.

Every endpoint here is for a browser on the server machine only, except ``GET /studio/ui-flags``
(read by every Studio, read-only away from the server): reads pass the same loopback +
same-origin + CSRF check as the server folder browser (``require_local_csrf``), writes the usual
mutation gate. The settings live in one file per user (alfrd.superuser) and apply to every
`alfrd serve` of this user on the next request.
"""

from __future__ import annotations

import os
import platform
import sys
import time
from pathlib import Path

from flask import current_app, jsonify, redirect, request

from alfrd import __version__, superuser, ui_flags

from .security import require_local_csrf
from .studio import _json_error, _poke, _project_root, studio_api

_STARTED = time.time()


def _local_only():
    """Reads too: the page shows other servers, processes and paths."""
    require_local_csrf()


def _flags() -> dict:
    """What this server's command line forces (the file cannot loosen these)."""
    cfg = current_app.config
    return {"experimental_features": bool(cfg.get("STUDIO_EXPERIMENTAL")), "no_gui_install": not cfg.get("PLUGINS_GUI_INSTALL", True),
            "no_plugin_actions": not cfg.get("PLUGINS_ACTIONS", True), "live_interval": cfg.get("STUDIO_LIVE_INTERVAL", 2.0),
            "demo": bool(cfg.get("STUDIO_DEMO")), "projects_scope": cfg.get("STUDIO_PROJECTS")}


def _effective(data: dict) -> dict:
    """The values this server uses after combining the file and its flags."""
    from alfrd.gui.security import experimental_enabled
    from alfrd.gui.studio import live_interval
    from alfrd.gui.studio_plugins import gui_install, plugin_actions

    return {"experimental": experimental_enabled(), "live_interval": live_interval(),
            "plugins": gui_install(), "plugin_actions": plugin_actions(),
            **{name: superuser.allowed(name, data) for name in superuser.LOCKS if name not in ("plugins", "plugin_actions")},
            "read_only": data["lockdown"]["read_only"]}


def _view() -> dict:
    data = superuser.load()
    return {"revision": ui_flags.revision(superuser.flat(data)), "settings": data, "defaults": superuser.DEFAULTS, "labels": superuser.LABELS, "file": str(superuser.path()),
            "exists": superuser.path().is_file(), "error": superuser.error(), "flags": _flags(), "effective": _effective(data),
            "choices": {"sections": list(superuser.SECTIONS), "templates": list(superuser.TEMPLATES), "locks": list(superuser.LOCKS)}}


@studio_api.get("/studio/superuser")
def superuser_get():
    _local_only()
    return jsonify(_view())


@studio_api.post("/studio/superuser")
def superuser_save():
    """``{settings: {...}}`` (whole or partial, nested or ``"section.key"``). 200 the new view; 400 names the bad key."""
    payload = request.get_json(silent=True) or {}
    changes = payload.get("settings")
    if not isinstance(changes, dict):
        return _json_error(ValueError("send {settings: {...}}"), 400)
    if "expected_revision" in payload:  # the Machine section of the settings form: refuse a stale form
        try:
            ui_flags.check_revision(ui_flags.revision(superuser.flat()), payload["expected_revision"])
        except ui_flags.UIFlagError as error:
            return _json_error(error, getattr(error, "status_code", 400))
    try:
        # replace: the whole set (the page offers it when the file is broken); else only the keys sent
        superuser.save(changes) if payload.get("replace") else superuser.merge(changes)
    except superuser.SuperuserError as error:
        return _json_error(error, 400)
    except OSError as error:
        return _json_error(OSError(f"cannot write {superuser.path()}: {error}"), 500)
    _poke_all()
    return jsonify(_view())


@studio_api.post("/studio/superuser/reset")
def superuser_reset():
    try:
        superuser.reset()
    except OSError as error:
        return _json_error(OSError(f"cannot remove {superuser.path()}: {error}"), 500)
    _poke_all()
    return jsonify(_view())


def _poke_all() -> None:
    """Wake the open Studios' live streams; each re-reads the session on its next live event or focus."""
    service = current_app.config.get("RUNTIME_SERVICE")
    for project in (service.list_projects() if service is not None else []):
        try:
            _poke(project.identifier)
        except Exception:  # noqa: BLE001 - a missing project folder must not fail the save
            pass


@studio_api.get("/studio/ui-flags")
def ui_flags_get():
    """``{revision, flags}``: the Studio UI flag registry (alfrd.ui_flags) with the machine values of the superuser file.

    Not local-only, unlike the rest of this module: any Studio browser may read it. Where changes are refused (a remote
    request, or a browser-only Studio with no server), every flag comes back ``locked`` with the reason. Browser flags
    carry their defaults here; the browser's own values (alfrd.data.prefs in the page) apply on top.
    """
    return jsonify(_flags_payload(request.args.get("project")))


def _project_flag_values(view: dict) -> dict:
    """The project flags live in the project's alfrd.yaml ``chat:`` block (``chat.settings_view``)."""
    values = {"chat.final_summary": view["final_summary"], "chat.saved_skills": bool(view["skill_selection"]["enabled"])}
    values["chat.watch.enabled"] = view["watch"]["enabled"]
    for name, agent in view["agents"].items():
        if f"chat.agents.{name}.lean" in ui_flags.BY_KEY and agent.get("lean") is not None:
            values[f"chat.agents.{name}.lean"] = bool(agent["lean"])
    return values


def _flags_payload(project: str | None = None) -> dict:
    """``{revision, revisions: {machine, project?}, flags}``. ``revision`` is the machine one; a project's values (and
    revision) are read only for a request that may change settings, so a remote reader sees the defaults."""
    from alfrd import chat
    from alfrd.gui.security import mutations_enabled, runtime_enabled

    values = superuser.flat()
    # the same content hash for the same settings: the page can tell that a change happened
    revisions = {"machine": ui_flags.revision(values)}
    project_values = {}
    if project and mutations_enabled():
        try:
            view = chat.settings_view(_project_root(project))
            project_values, revisions["project"] = _project_flag_values(view), int(view["hash"][:12], 16)
        except Exception:  # noqa: BLE001 - an unreadable project shows the defaults
            pass
    flags = ui_flags.resolve(project=project_values, machine=values)
    if not mutations_enabled():
        reason = ("Browser-only mode: this Studio has no server to save to." if not runtime_enabled()
                  else "Changes are allowed only from a browser on the server machine.")
        for flag in flags:
            flag["locked"] = True
            flag["reason"] = f"{reason} {flag['reason']}" if flag["reason"] else reason
    return {"revision": revisions["machine"], "revisions": revisions, "flags": flags}


@studio_api.post("/studio/ui-flags")
def ui_flags_save():
    """``{scope: 'browser'|'project', changes: {key: value}, expected_revision, current_revision?, project?}``: the Browser
    and Project sections of the settings form (Machine saves go to ``POST /studio/superuser`` with ``expected_revision``).

    Local requests only (the app-wide mutation gate: remote and browser-only requests get 403), refused while read-only.
    409 when ``expected_revision`` is not the current one. Browser values live in the page's own storage, so the page sends
    the revision it holds now as ``current_revision`` and gets the validated values back to store.
    """
    from alfrd import chat, history
    from alfrd.execution import ExecutionError
    from alfrd.gui.security import lockdown_refusal

    payload = request.get_json(silent=True) or {}
    scope = payload.get("scope")
    try:
        if scope not in ("browser", "project"):
            raise ui_flags.UIFlagError("scope must be 'browser' or 'project' (machine settings save through /studio/superuser)")
        changes = ui_flags.validate_changes(scope, payload.get("changes"))
        if scope == "browser":
            current = payload.get("current_revision")
            ui_flags.check_revision(current if isinstance(current, int) and not isinstance(current, bool) else -1, payload.get("expected_revision"))
            return jsonify(scope=scope, values=changes)
        refusal = lockdown_refusal(["file_edits"])
        if refusal:
            return jsonify(error={"code": 403, "message": refusal, "reason": "superuser", "lock": ["file_edits"]}), 403
        root = _project_root(str(payload.get("project") or ""))
        view = chat.settings_view(root)
        ui_flags.check_revision(int(view["hash"][:12], 16), payload.get("expected_revision"))
        block = {**view["block"]}
        for key, value in changes.items():
            if key == "chat.final_summary":
                block["final_summary"] = value
            elif key == "chat.watch.enabled":
                block["watch"] = {**(block.get("watch") or {}), "enabled": value}
            elif key == "chat.saved_skills":
                block["skill_selection"] = {**(block.get("skill_selection") or {}), "enabled": value}
            elif not (key.startswith("chat.agents.") and key.endswith(".lean")):
                raise ui_flags.UIFlagError(f"{key} is not stored per project yet")
            else:
                name = key.split(".")[2]
                if name not in view["agents"]:
                    raise ui_flags.UIFlagError(f"{key}: agent {name!r} is not configured in this project")
                block["agents"] = {**(block.get("agents") or {}), name: {**((block.get("agents") or {}).get(name) or {}), "lean": value}}
        chat.save_settings(root, block, base_hash=view["hash"])
    except ui_flags.RevisionConflict as error:
        return _json_error(error, 409)
    except history.Conflict as error:
        return _json_error(error, 409)
    except (ui_flags.UIFlagError, ExecutionError, ValueError) as error:
        return _json_error(error, 400)
    except LookupError as error:
        return _json_error(error, 404)
    _poke(payload["project"])
    return jsonify(scope=scope, **_flags_payload(payload["project"]))


# --- statistics and ops -----------------------------------------------------------------------


def _servers() -> list[dict]:
    """Every `alfrd serve` of this user that left a server file (the URL only; tokens stay private)."""
    from alfrd.gui.auth import config_dir
    from alfrd.runtime.scheduler import pid_alive

    out = []
    for file in sorted(config_dir().glob("server-*.json")):
        try:
            import json

            data = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        port = file.stem.removeprefix("server-")
        pid = data.get("pid")
        try:
            alive = bool(pid) and pid_alive(int(pid))
        except (TypeError, ValueError, OverflowError, OSError):
            alive = False
        out.append({"port": int(port) if port.isdigit() else port, "url": data.get("url"), "pid": pid,
                    "alive": alive, "this": str(pid) == str(os.getpid())})
    return out


def _runs() -> tuple[list[dict], dict]:
    """Active plans (workflow runs and chat sessions) of every project, with their running units."""
    from alfrd.runtime import scheduler

    service = current_app.config.get("RUNTIME_SERVICE")
    projects = service.list_projects() if service is not None else []
    stats = {"projects": len(projects), "plans": 0, "chat_sessions": 0, "active_plans": 0, "runners_alive": 0, "running_units": 0}
    runs = []
    for project in projects:
        root = Path(project.root_path)
        try:
            records = scheduler.list_plans(root, include_chat=True)
        except OSError:
            continue
        stats["plans"] += sum(not p.get("chat") for p in records)
        stats["chat_sessions"] += sum(bool(p.get("chat")) for p in records)
        for plan in records:
            if not plan.get("id"):
                continue
            folder = scheduler.PlanDir(root, plan["id"])
            try:
                alive = folder.runner_alive()
                active = plan.get("status") in scheduler.ACTIVE + ("interrupted",)
                if not (alive or active):
                    continue
                units = [u for u in folder.units() if u.get("status") == "running"]
            except OSError:  # an unreadable plan folder must not hide the others
                continue
            stats["active_plans"] += 1
            stats["runners_alive"] += int(alive)
            stats["running_units"] += len(units)
            title = scheduler.chat_title(root, plan) if plan.get("chat") else plan.get("workflow")
            runs.append({"project": project.identifier, "project_name": project.name, "root": str(root), "plan": plan["id"],
                         "kind": "chat" if plan.get("chat") else "workflow", "title": title, "status": plan.get("status"),
                         "runner_alive": alive, "runner_pid": (plan.get("runner") or {}).get("pid"),
                         "started": plan.get("started_at") or plan.get("created"),
                         "units": [{"id": u.get("id"), "steps": u.get("steps"), "row": u.get("row"), "target": u.get("target"),
                                    "pid": u.get("pid"), "started": u.get("started")} for u in units]})
    return runs, stats


def _system() -> dict:
    out = {"version": __version__, "python": sys.version.split()[0], "platform": platform.platform(), "host": platform.node(),
           "pid": os.getpid(), "uptime_s": round(time.time() - _STARTED), "runtime_db": current_app.config.get("RUNTIME_DATABASE")}
    db = out["runtime_db"]
    if db and Path(db).is_file():
        out["runtime_db_bytes"] = Path(db).stat().st_size
    try:
        import psutil

        mem = psutil.virtual_memory()
        out.update(cpu=psutil.cpu_percent(interval=None), cores=psutil.cpu_count() or 1, mem_used=mem.total - mem.available,
                   mem_total=mem.total, load=list(os.getloadavg()) if hasattr(os, "getloadavg") else None)
    except Exception:  # noqa: BLE001 - psutil is optional for this page
        pass
    return out


@studio_api.get("/studio/superuser/ops")
def superuser_ops():
    _local_only()
    runs, stats = _runs()
    servers = _servers()
    stats["servers_alive"] = sum(s["alive"] for s in servers)
    return jsonify(stats=stats, runs=runs, servers=servers, system=_system())


@studio_api.post("/studio/superuser/ops")
def superuser_ops_action():
    """``{project, plan, action: pause|resume|cancel|stop-unit, unit?}``: control any project's run from here."""
    from alfrd.execution import ExecutionError
    from alfrd.gui.studio import _project_root
    from alfrd.runtime import scheduler

    payload = request.get_json(silent=True) or {}
    project, plan_id, action = payload.get("project"), str(payload.get("plan") or ""), payload.get("action")
    if action not in ("pause", "resume", "cancel", "stop-unit") or not project or not plan_id.replace("-", "").replace("_", "").isalnum():
        return _json_error(ValueError("send {project, plan, action: pause|resume|cancel|stop-unit, unit?}"), 400)
    if action == "resume":
        from alfrd.gui.security import lockdown_refusal

        refusal = lockdown_refusal(["runs"])
        if refusal:
            return jsonify(error={"code": 403, "message": refusal, "reason": "superuser", "lock": ["runs"]}), 403
    try:
        root = _project_root(project)
        if action == "stop-unit":
            unit = str(payload.get("unit") or "")
            if not unit:
                return _json_error(ValueError("stop-unit needs unit"), 400)
            out = scheduler.stop_unit(root, plan_id, unit)
        else:
            out = scheduler.control(root, plan_id, action)
    except (ExecutionError, LookupError, ValueError) as error:
        return _json_error(error, 404)
    _poke(project)
    return jsonify(ok=True, result=out)


# --- the page's own URL ------------------------------------------------------------------------


def register_page(app) -> None:
    """``GET /superuser`` → the Studio at ``#/superuser`` (access token and session rules as for /studio/)."""

    @app.get("/superuser")
    def superuser_page():
        query = request.query_string.decode()
        return redirect(f"/studio/{'?' + query if query else ''}#/superuser", code=302)
