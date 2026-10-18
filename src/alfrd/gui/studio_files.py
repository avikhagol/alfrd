"""Studio endpoints for a project's Measurement Sets and its confined file explorer (see alfrd.project_files).

Read-only. ``GET …/measurement-sets`` lists the ``.ms`` folders that alfrd.yaml's ``artifacts:``
declare; ``GET …/files?path=rel`` lists one folder below the project root, for projects
without such a declaration (or to look elsewhere). Nothing outside the project root is listed.
"""
from __future__ import annotations

from flask import jsonify, request

from alfrd import project_files

from . import studio as _studio
from .studio import _json_error, studio_api


@studio_api.get("/studio/projects/<project_name>/measurement-sets")
def project_measurement_sets(project_name: str):
    """``{configured, artifacts, entries, notes, truncated, root}`` from the active manifest's artifacts."""
    root = _studio._project_root(project_name)
    try:
        return jsonify(project_files.measurement_sets(root))
    except OSError as error:
        return _json_error(error, 500)


@studio_api.get("/studio/projects/<project_name>/files")
def project_files_list(project_name: str):
    """One folder of the project (``?path=`` relative; empty = the root). 400 for ``..``/absolute paths,
    403 for a link out of the project, 404 for a missing folder."""
    root = _studio._project_root(project_name)
    try:
        return jsonify(project_files.list_dir(root, request.args.get("path", "")))
    except project_files.ExplorerError as error:
        return _json_error(error, error.status)


__all__ = ["project_files_list", "project_measurement_sets"]
