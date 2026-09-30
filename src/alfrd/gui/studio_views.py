"""Studio endpoint for template-driven views (alfrd.layout_generic): read-only GETs.

    GET /api/studio/projects/<p>/view?entity=<entity query>&view=metadata
    GET /api/studio/projects/<p>/view/file?entity=…&panel=N&path=REL     an image an image panel lists
"""

from __future__ import annotations

from flask import current_app, jsonify, request, send_file

from alfrd import layout_generic
from alfrd.entities import from_query, levels_from

from .studio import _json_error, _project_root, studio_api


def _entity(project_name: str, root) -> dict:
    spec = layout_generic.spec_for(root)
    levels = levels_from({"hierarchy": spec["hierarchy"]})
    raw = request.args.get("entity") or ""
    if raw and not any(part.split("=", 1)[0] == "project" for part in raw.lstrip("?#").split("&")):
        raw = f"project={project_name}&{raw}"  # the URL already names the project
    entity = from_query(raw, levels=levels) if raw else {"project": project_name}
    service = current_app.config.get("RUNTIME_SERVICE")
    entity["project"] = service.get_project_by_selector(project_name).identifier
    return entity


@studio_api.get("/studio/projects/<project_name>/view")
def project_view(project_name: str):
    root = _project_root(project_name)
    try:
        entity = _entity(project_name, root)
        return jsonify(layout_generic.view(root, entity, name=request.args.get("view") or "metadata"))
    except ValueError as error:
        return _json_error(error, 400)


@studio_api.get("/studio/projects/<project_name>/view/file")
def project_view_file(project_name: str):
    root = _project_root(project_name)
    try:
        entity = _entity(project_name, root)
        path = layout_generic.panel_file(root, entity, request.args.get("panel", default=-1, type=int), request.args.get("path") or "")
    except ValueError as error:
        return _json_error(error, 400)
    except PermissionError as error:
        return _json_error(error, 403)
    response = send_file(path, max_age=0)
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


__all__: list[str] = []
