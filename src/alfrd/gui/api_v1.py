"""HTTP v1: the read-only plan status contract (alfrd.api.status) served by ``alfrd serve``.

Separate from the Studio's UI-shaped ``/api/studio/…`` endpoints. Everything
here is a GET and changes nothing. Loopback only, unless a bearer token is
configured (``ALFRD_API_TOKEN`` in the environment or ``API_TOKEN`` in the app
config): then other clients must send ``Authorization: Bearer <token>``.

    GET /api/v1/projects
    GET /api/v1/projects/<p>/plans
    GET /api/v1/projects/<p>/plans/<id|latest>?detail=summary|rows|full&since=&wait=&limit=&offset=
    GET /api/v1/projects/<p>/plans/<id|latest>/events?since=&timeout=     (Server-Sent Events)
    GET /api/v1/projects/<p>/plans/<id|latest>/log?target=&step=&unit=&lines=
    GET /api/v1/schema/plan_status

``<p>`` is the project identifier or a unique project name.
"""

from __future__ import annotations

import hmac
import json
import os
import re
from importlib import resources
from pathlib import Path

from flask import Blueprint, Response, abort, current_app, jsonify, request

from alfrd.api import status as api

api_v1 = Blueprint("api_v1", __name__, url_prefix="/api/v1")
MAX_WAIT = 60.0


def _token() -> str | None:
    return current_app.config.get("API_TOKEN") or os.environ.get("ALFRD_API_TOKEN") or None


@api_v1.before_request
def _gate():
    from alfrd.gui.security import _is_loopback

    if _is_loopback(request.remote_addr):
        return None
    token = _token()
    if not token:
        return _error(403, "the status API is loopback-only; set ALFRD_API_TOKEN to serve it to other hosts")
    supplied = request.headers.get("Authorization", "")
    scheme, _, value = supplied.partition(" ")
    if scheme.lower() != "bearer" or not value or not hmac.compare_digest(value.strip().encode(), token.encode()):
        response = _error(401, "a bearer token is required")
        response.headers["WWW-Authenticate"] = 'Bearer realm="alfrd"'
        return response
    return None


def _error(code: int, message: str):
    response = jsonify(error={"code": code, "message": message})
    response.status_code = code
    return response


def _project(selector: str) -> tuple[Path, dict[str, str]]:
    from alfrd.runtime import RuntimeNotFound

    service = current_app.config.get("RUNTIME_SERVICE")
    if service is None:
        abort(404, description="the status API needs a runtime-backed `alfrd serve`")
    try:
        project = service.get_project_by_selector(selector)
    except RuntimeNotFound as error:
        abort(404, description=str(error))
    scope = current_app.config.get("STUDIO_PROJECTS")
    if isinstance(scope, list) and project.identifier not in scope:
        abort(404, description=f"project {selector!r} is not served here")
    root = Path(project.root_path)
    if not root.is_dir():
        abort(404, description=f"project root {root} is not a directory")
    return root, {"name": project.name, "identifier": project.identifier}


def _plan_id(value: str) -> str | None:
    return None if value == "latest" else value


def _float(name: str, default: float, top: float) -> float:
    raw = request.args.get(name)
    if raw in (None, ""):
        return default
    try:
        return max(0.0, min(float(raw), top))
    except ValueError:
        abort(400, description=f"{name} must be a number of seconds")


@api_v1.get("/projects")
def projects():
    service = current_app.config.get("RUNTIME_SERVICE")
    if service is None:
        return jsonify(projects=[])
    scope = current_app.config.get("STUDIO_PROJECTS")
    items = [{"name": p.name, "identifier": p.identifier, "root": p.root_path} for p in service.list_projects()
             if not isinstance(scope, list) or p.identifier in scope]
    return jsonify(schema="alfrd.project_list/1", projects=items)


@api_v1.get("/projects/<selector>/plans")
def plans(selector: str):
    root, info = _project(selector)
    try:
        return jsonify(api.list_plans(root, project=info))
    except api.StatusNotFound as error:
        return _error(404, str(error))


@api_v1.get("/projects/<selector>/plans/<plan_id>")
def plan(selector: str, plan_id: str):
    root, info = _project(selector)
    detail = request.args.get("detail", "summary")
    since = request.args.get("since") or None
    wait = _float("wait", 0.0, MAX_WAIT)
    limit = request.args.get("limit", type=int)
    offset = request.args.get("offset", default=0, type=int)
    try:
        if wait > 0:
            api.wait(root, _plan_id(plan_id), until="any-change", timeout=wait, since=since, project=info, poll=0.5)
        doc = api.plan_status(root, _plan_id(plan_id), detail=detail, since=since, limit=limit, offset=offset, project=info)
    except api.StatusNotFound as error:
        return _error(404, str(error))
    except ValueError as error:
        return _error(400, str(error))
    response = jsonify(doc)
    response.headers["Cache-Control"] = "no-store"
    return response


@api_v1.get("/projects/<selector>/plans/<plan_id>/events")
def plan_events(selector: str, plan_id: str):
    """SSE: one ``event: started|finished|plan`` per plan event; ``id:`` is the resume cursor (Last-Event-ID).

    ``?since=<integer seq>`` instead returns the structured events (events.jsonl) as JSON.
    """
    root, info = _project(selector)
    seq = request.args.get("since") or ""
    if re.fullmatch(r"[0-9]+", seq):
        try:
            doc = api.structured_events(root, _plan_id(plan_id), since=int(seq), limit=request.args.get("limit", type=int))
        except api.StatusNotFound as error:
            return _error(404, str(error))
        except ValueError as error:
            return _error(400, str(error))
        response = jsonify(doc)
        response.headers["Cache-Control"] = "no-store"
        return response
    since = request.args.get("since") or request.headers.get("Last-Event-ID") or None
    timeout = _float("timeout", 3600.0, 24 * 3600.0)
    try:
        api.parse_cursor(since)
        plan_real = api.plan_status(root, _plan_id(plan_id), project=info)["plan"]["id"]
    except api.StatusNotFound as error:
        return _error(404, str(error))
    except ValueError as error:
        return _error(400, str(error))
    poll = float(current_app.config.get("API_EVENTS_POLL", 1.0))

    def stream():
        yield "retry: 3000\n\n"
        for item in api.follow(root, plan_real, since=since, keep=True, timeout=timeout, project=info, poll=poll):
            yield f"id: {item['cursor']}\nevent: {item['type']}\ndata: {json.dumps(item, default=str)}\n\n"
        yield "event: end\ndata: {}\n\n"

    return Response(stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@api_v1.get("/projects/<selector>/plans/<plan_id>/log")
def plan_log(selector: str, plan_id: str):
    root, info = _project(selector)
    try:
        doc = api.log_tail(root, _plan_id(plan_id), target=request.args.get("target") or None,
                           step=request.args.get("step") or None, unit=request.args.get("unit") or None,
                           lines=request.args.get("lines", default=50, type=int), project=info)
    except api.StatusNotFound as error:
        return _error(404, str(error))
    return jsonify(doc)


@api_v1.get("/schema/plan_status")
def schema():
    text = resources.files("alfrd.schemas").joinpath("plan_status.v1.json").read_text(encoding="utf-8")
    return current_app.response_class(text, mimetype="application/schema+json")


__all__ = ["api_v1"]
