"""Studio endpoints for the project chat (alfrd.chat).

Reads are GETs; every POST passes the app-wide loopback + CSRF gate
(``security.protect_mutation``). No request runs an agent itself: messages,
decisions and retries add plan rows and (re)start the session's detached runner.
"""

from __future__ import annotations

from flask import jsonify, request

from alfrd import chat
from alfrd import orchestration as proto
from alfrd.execution import ExecutionError

from .studio import _json_error, _poke, _project_root, studio_api

_BASE = "/studio/projects/<project_name>/chat"


def _fail(error: Exception):
    if isinstance(error, proto.RefError):
        return _json_error(error, error.status)
    if isinstance(error, LookupError):
        return _json_error(error, 404)
    if isinstance(error, PermissionError):
        return _json_error(error, 409)
    if isinstance(error, (ExecutionError, ValueError)):
        return _json_error(error, 400)
    raise error


@studio_api.get(_BASE + "/settings")
def chat_settings(project_name: str):
    """Effective chat settings (agents, orchestrator, bounds) from the active alfrd.yaml ``chat:`` block, plus
    ``block`` (that file's own ``chat:``), ``hash`` (of the file, for the save's conflict guard), ``presets``
    (addable agents) and ``roles`` (suggested role words)."""
    try:
        return jsonify(chat.settings_view(_project_root(project_name)))
    except Exception as error:  # noqa: BLE001
        return _fail(error)


@studio_api.post(_BASE + "/settings")
def chat_settings_save(project_name: str):
    """``{chat: <block>, base_hash?, force?}``: validate and replace the ``chat:`` block of the active alfrd.yaml
    through its history. 200 ``{settings, hash, version}``; 400 invalid; 409 ``{current_hash, current_text, diff}``."""
    from alfrd import history

    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict) or "chat" not in payload:
        return _json_error(ValueError("send {chat: <settings block>}"), 400)
    root = _project_root(project_name)
    try:
        saved = chat.save_settings(root, payload.get("chat"), base_hash=payload.get("base_hash") or None,
                                   force=bool(payload.get("force")))
        view = chat.settings_view(root)
    except history.Conflict as conflict:
        return jsonify(error={"code": 409, "message": f"{conflict} — reload the settings"}, current_hash=conflict.current_hash,
                       current_text=conflict.current, diff=conflict.diff), 409
    except Exception as error:  # noqa: BLE001
        return _fail(error)
    _poke(project_name)
    return jsonify(settings=view, hash=view["hash"], version=saved["version"])


@studio_api.get(_BASE + "/probe")
def chat_probe(project_name: str):
    """Which agent programs are installed, whether a local Ollama answers (and its models), whether HF_TOKEN is set."""
    try:
        return jsonify(chat.probe(_project_root(project_name)))
    except Exception as error:  # noqa: BLE001
        return _fail(error)


@studio_api.get(_BASE + "/sessions")
def chat_sessions(project_name: str):
    return jsonify(sessions=chat.list_sessions(_project_root(project_name)))


@studio_api.post(_BASE + "/sessions")
def chat_session_create(project_name: str):
    payload = request.get_json(silent=True) or {}
    try:
        data = chat.create_session(_project_root(project_name), payload.get("title"))
    except Exception as error:  # noqa: BLE001
        return _fail(error)
    _poke(project_name)
    return jsonify(session=data), 201


@studio_api.get(_BASE + "/sessions/<session_id>")
def chat_session_state(project_name: str, session_id: str):
    """Messages, requests, tasks with statuses, graph (nodes, edges, Mermaid), blockers, decisions and usage."""
    try:
        return jsonify(chat.session_state(_project_root(project_name), session_id,
                                          messages=request.args.get("messages", default=500, type=int)))
    except Exception as error:  # noqa: BLE001
        return _fail(error)


@studio_api.get(_BASE + "/sessions/<session_id>/activity")
def chat_activity(project_name: str, session_id: str):
    """Running units of the session (orchestrator calls too) with the last ``?lines=`` (≤ 400) lines of each log."""
    try:
        return jsonify(chat.activity(_project_root(project_name), session_id,
                                     lines=request.args.get("lines", default=60, type=int)))
    except Exception as error:  # noqa: BLE001
        return _fail(error)


@studio_api.post(_BASE + "/sessions/<session_id>/messages")
def chat_message(project_name: str, session_id: str):
    payload = request.get_json(silent=True) or {}
    try:
        out = chat.post_message(_project_root(project_name), session_id, str(payload.get("text") or ""))
    except Exception as error:  # noqa: BLE001
        return _fail(error)
    _poke(project_name)
    return jsonify(out), 201


@studio_api.post(_BASE + "/sessions/<session_id>/decisions/<decision_id>")
def chat_decision(project_name: str, session_id: str, decision_id: str):
    payload = request.get_json(silent=True) or {}
    try:
        decision = chat.decide(_project_root(project_name), session_id, decision_id,
                               choice=payload.get("choice"), answer=payload.get("answer"))
    except Exception as error:  # noqa: BLE001
        return _fail(error)
    _poke(project_name)
    return jsonify(decision=decision)


@studio_api.post(_BASE + "/sessions/<session_id>/tasks/<task_id>/<action>")
def chat_task_action(project_name: str, session_id: str, task_id: str, action: str):
    if action not in ("cancel", "retry"):
        return _json_error(ValueError(f"unknown action {action!r}"), 404)
    try:
        call = chat.cancel_task if action == "cancel" else chat.retry_task
        out = call(_project_root(project_name), session_id, task_id)
    except Exception as error:  # noqa: BLE001
        return _fail(error)
    _poke(project_name)
    return jsonify(out), 202


@studio_api.post(_BASE + "/sessions/<session_id>/requests/<request_id>/promote")
def chat_promote(project_name: str, session_id: str, request_id: str):
    """Promote a request's task graph to workflow steps of the active alfrd.yaml.

    ``{"preview": true}`` (default) answers the steps, the new text and its diff without writing;
    ``{"preview": false, "base_hash": …}`` saves through the alfrd.yaml history (conflict guard:
    409 with ``current_text`` and ``diff`` when the file changed since the preview; cycle check).
    """
    from alfrd import history
    from alfrd.execution import follow_workflow_edit, workflow_pattern

    payload = request.get_json(silent=True) or {}
    root = _project_root(project_name)
    try:
        if payload.get("preview", True):
            return jsonify(chat.promotion(root, session_id, request_id))
        before = workflow_pattern(root)
        out = chat.promote(root, session_id, request_id, base_hash=payload.get("base_hash"), force=bool(payload.get("force")))
    except history.Conflict as conflict:
        return jsonify(error={"code": 409, "message": f"{conflict} — preview again"}, current_text=conflict.current,
                       current_hash=conflict.current_hash, diff=conflict.diff), 409
    except Exception as error:  # noqa: BLE001
        return _fail(error)
    follow_workflow_edit(root, before)
    _poke(project_name)
    return jsonify(out)


@studio_api.get(_BASE + "/sessions/<session_id>/tasks/<task_id>/transcript")
def chat_transcript(project_name: str, session_id: str, task_id: str):
    """A task's attempts and the sizes of their archived prompt and reply (content is fetched per part)."""
    try:
        return jsonify(chat.transcript(_project_root(project_name), session_id, task_id))
    except Exception as error:  # noqa: BLE001
        return _fail(error)


@studio_api.get(_BASE + "/sessions/<session_id>/tasks/<task_id>/transcript/<unit_id>/<part>")
def chat_transcript_part(project_name: str, session_id: str, task_id: str, unit_id: str, part: str):
    """One page (``?offset=&length=``, at most 256 KiB) of an attempt's prompt or reply, on demand."""
    try:
        return jsonify(chat.transcript_part(_project_root(project_name), session_id, task_id, unit_id, part,
                                            offset=request.args.get("offset", default=0, type=int),
                                            length=request.args.get("length", default=None, type=int)))
    except Exception as error:  # noqa: BLE001
        return _fail(error)


@studio_api.get(_BASE + "/sessions/<session_id>/artifacts")
def chat_artifact(project_name: str, session_id: str):
    """Selective retrieval of ``?ref=<path>@<sha256>`` (``&offset=&length=``): confined, hash-checked, size-limited."""
    try:
        return jsonify(chat.read_artifact(_project_root(project_name), session_id, request.args.get("ref", ""),
                                          offset=request.args.get("offset", default=0, type=int),
                                          length=request.args.get("length", default=None, type=int)))
    except Exception as error:  # noqa: BLE001
        return _fail(error)


__all__: list[str] = []
