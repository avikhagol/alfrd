"""Studio endpoints for the project's target list (``alfrd.targets.csv``, see alfrd.targets_csv).

``GET`` reads it; ``preview``, ``save`` and ``remove`` are POSTs behind the app-wide
loopback + CSRF gate (``security.protect_mutation``). A save pokes the live
hub so open Studio tabs reload the Overview.
"""

from __future__ import annotations

from flask import jsonify, request

from alfrd import targets_csv as tc
from alfrd.execution import ExecutionError

from .studio import _json_error, _poke, _project_root, studio_api

_MAX_TEXT = 8 * 1024 * 1024


def _spec(project_name: str) -> tc.TargetsSpec:
    return tc.load_spec(_project_root(project_name))


def _payload() -> tuple[str, str, dict]:
    payload = request.get_json(silent=True) or {}
    text = payload.get("text")
    if not isinstance(text, str):
        raise ValueError("text must be the CSV file's content")
    if len(text) > _MAX_TEXT:
        raise ValueError("targets CSV is larger than 8 MB")
    mode = str(payload.get("mode") or "merge")
    mapping = payload.get("columns") if isinstance(payload.get("columns"), dict) else None
    return text, mode, mapping or {}


@studio_api.get("/studio/projects/<project_name>/targets")
def targets_get(project_name: str):
    """The targets file (``rows``: target, files, code, extra) and where it lives."""
    try:
        spec = _spec(project_name)
        table = tc.read(spec)
    except (ExecutionError, ValueError) as error:
        return _json_error(error, 400)
    except OSError as error:
        return _json_error(error, 500)
    return jsonify(spec=spec.to_dict(), rows=table["rows"], problems=table["problems"])


@studio_api.post("/studio/projects/<project_name>/targets/preview")
def targets_preview(project_name: str):
    """What importing ``text`` would change (added / updated / unchanged / removed), without writing."""
    try:
        text, mode, mapping = _payload()
        spec = _spec(project_name)
        result = tc.preview(spec, text, mode, mapping)
    except (ExecutionError, ValueError) as error:
        return _json_error(error, 400)
    except OSError as error:
        return _json_error(error, 500)
    return jsonify(spec=spec.to_dict(), **result)


@studio_api.post("/studio/projects/<project_name>/targets")
def targets_save(project_name: str):
    """Import ``text`` (merge or replace) into the targets file; rows with problems reject the whole import."""
    try:
        text, mode, mapping = _payload()
        spec = _spec(project_name)
        result = tc.save(spec, text, mode, mapping)
    except (ExecutionError, ValueError) as error:
        return _json_error(error, 400)
    except OSError as error:
        return _json_error(error, 500)
    _poke(project_name)
    return jsonify(spec=spec.to_dict(), rows=result["rows"],
                   **{k: result[k] for k in ("added", "updated", "unchanged", "removed")})


@studio_api.post("/studio/projects/<project_name>/targets/remove")
def targets_remove(project_name: str):
    """Remove ``targets`` (names, or ``target@code`` for one row) from the targets file."""
    payload = request.get_json(silent=True) or {}
    names = payload.get("targets")
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        return _json_error(ValueError("targets must be a list of target names"), 400)
    try:
        spec = _spec(project_name)
        result = tc.remove(spec, names)
    except (ExecutionError, ValueError) as error:
        return _json_error(error, 400)
    except OSError as error:
        return _json_error(error, 500)
    if result["removed"]:
        _poke(project_name)
    return jsonify(spec=spec.to_dict(), rows=result["rows"], removed=result["removed"], missing=result["missing"])
