"""Authenticated, observation-only harness endpoint."""
from flask import abort, current_app, jsonify, redirect, request

from alfrd import harness

from .studio import _project_root, studio_api
from .studio_chat import _fail


@studio_api.get("/studio/projects/<project_name>/chat/sessions/<session_id>/harness/state")
def harness_state(project_name, session_id):
    root = _project_root(project_name)
    service = current_app.config["RUNTIME_SERVICE"]
    project = service.get_project_by_selector(project_name)
    scope = current_app.config.get("STUDIO_PROJECTS")
    if scope is not None and project.identifier not in scope and project.name not in scope:
        abort(403, description="Project is not served by this server")
    try:
        options = {k: request.args[k] for k in ("scope", "request", "at") if k in request.args}
        for key in ("before_seq", "limit"):
            if key in request.args:
                try:
                    options[key] = int(request.args[key])
                except ValueError:
                    raise ValueError(f"{key} must be a positive integer") from None
        response = jsonify(harness.state(root, session_id, project=project.identifier, **options))
        response.headers["Cache-Control"] = "no-store"
        return response
    except (ValueError, LookupError, OSError) as error:
        return _fail(error)


def harness_redirect():
    return redirect("/studio/#/harness")
