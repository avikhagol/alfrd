"""Studio endpoints for the version history of alfrd.yaml (and other tracked files; alfrd.history).

Reads are GETs; Restore is a POST behind the app-wide loopback + CSRF gate.
``?file=`` must be a tracked file (alfrd.yaml ``history.files``), so nothing
else in the project can be read through these routes.
"""

from __future__ import annotations

from flask import jsonify, request

from alfrd import history

from .studio import _json_error, _poke, _project_root, studio_api


def _file() -> str:
    return request.args.get("file") or (request.get_json(silent=True) or {}).get("file") or "alfrd.yaml"


@studio_api.get("/studio/projects/<project_name>/history")
def history_list(project_name: str):
    root = _project_root(project_name)
    rel = _file()
    try:
        items = history.versions(root, rel)
    except history.HistoryError as error:
        return _json_error(error, 400)
    current = history.read_text(history._path(root.resolve(), rel))
    return jsonify(file=rel, tracked=history.tracked(root), versions=items, keep=history.settings(root)["keep"],
                   current_hash=history.text_hash(current) if current is not None else None,
                   git=history.git_log(root, rel))


@studio_api.get("/studio/projects/<project_name>/history/diff")
def history_diff(project_name: str):
    root = _project_root(project_name)
    a, b = request.args.get("a", ""), request.args.get("b", "current")
    try:
        return jsonify(file=_file(), a=a, b=b, diff=history.diff(root, _file(), a, b))
    except history.HistoryError as error:
        return _json_error(error, 404 if "no version" in str(error) else 400)


@studio_api.get("/studio/projects/<project_name>/history/<version>")
def history_version(project_name: str, version: str):
    root = _project_root(project_name)
    try:
        return jsonify(file=_file(), version=version, text=history.read_version(root, _file(), version))
    except history.HistoryError as error:
        return _json_error(error, 404 if "no version" in str(error) else 400)


@studio_api.post("/studio/projects/<project_name>/history/<version>/restore")
def history_restore(project_name: str, version: str):
    """Save an old version back as the newest one (the conflict guard applies with ``base_hash``)."""
    root = _project_root(project_name)
    payload = request.get_json(silent=True) or {}
    try:
        entry = history.restore(root, _file(), version, base_hash=payload.get("base_hash"), force=bool(payload.get("force")))
    except history.Conflict as conflict:
        return jsonify(error={"code": 409, "message": str(conflict)}, current_text=conflict.current,
                       current_hash=conflict.current_hash, diff=conflict.diff), 409
    except history.HistoryError as error:
        return _json_error(error, 404 if "no version" in str(error) else 400)
    except Exception as error:  # an old alfrd.yaml that no longer validates
        return _json_error(error, 400)
    _poke(project_name)
    text = history.read_version(root, _file(), "current")
    return jsonify(file=_file(), restored=version, version=entry, text=text, hash=history.text_hash(text))


__all__: list[str] = []
