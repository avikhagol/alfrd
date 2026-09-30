"""Studio endpoints for annotations (alfrd.notes; ``alfrd.notes.jsonl`` next to alfrd.yaml).

GET lists them (with orphan detection); POSTs append events behind the
app-wide loopback + CSRF gate. The file is part of the project tree, so other
Studios see new notes through the usual live events.
"""

from __future__ import annotations

from flask import jsonify, request

from alfrd import notes

from .studio import _json_error, _poke, _project_root, studio_api


@studio_api.get("/studio/projects/<project_name>/notes")
def notes_list(project_name: str):
    return jsonify(notes.listing(_project_root(project_name)))


@studio_api.post("/studio/projects/<project_name>/notes")
def notes_create(project_name: str):
    payload = request.get_json(silent=True) or {}
    try:
        note = notes.create(_project_root(project_name), payload.get("anchor") or {}, payload.get("text") or "",
                            payload.get("tags"))
    except notes.NoteError as error:
        return _json_error(error, 400)
    _poke(project_name)
    return jsonify(note=note), 201


@studio_api.post("/studio/projects/<project_name>/notes/<note_id>")
def notes_change(project_name: str, note_id: str):
    payload = request.get_json(silent=True) or {}
    try:
        note = notes.change(_project_root(project_name), note_id, str(payload.get("op") or ""),
                            text=payload.get("text"), tags=payload.get("tags"), anchor=payload.get("anchor"))
    except notes.NoteError as error:
        return _json_error(error, 404 if str(error).startswith("no note") else 400)
    _poke(project_name)
    return jsonify(note=note)


__all__: list[str] = []
