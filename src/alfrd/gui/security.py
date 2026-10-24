"""Local-only mutation and CSRF protection for the optional GUI."""

from __future__ import annotations

import hmac
import ipaddress
import secrets
from urllib.parse import urlsplit

from flask import abort, current_app, has_request_context, request, session

_CSRF_SESSION_KEY = "_alfrd_csrf_token"


def runtime_enabled() -> bool:
    return current_app.config.get("RUNTIME_SERVICE") is not None


# Studio features shown only under `alfrd serve --experimental-features`: the Chat mode
# preference (Chat section, chat dock, chat runs in Workflow) and these project templates.
EXPERIMENTAL_TEMPLATES = frozenset({"agent-loop"})


def experimental_enabled() -> bool:
    """``alfrd serve --experimental-features`` or the superuser file's ``features.experimental``."""
    from alfrd import superuser

    return bool(current_app.config.get("STUDIO_EXPERIMENTAL", False)) or superuser.load()["features"]["experimental"]


# Superuser lockdown: which mutating endpoint belongs to which ``lockdown`` switch (alfrd.superuser).
# Stopping running work (pause, cancel, stop a unit, cancel a chat task) is never locked.
LOCKED_ENDPOINTS = {
    "plugins": {"studio_api.plugins_install", "studio_api.plugins_toggle", "studio_api.plugin_config_save",
                "studio_api.plugin_project_types", "studio_api.plugin_service"},
    "plugin_actions": {"studio_api.plugin_project_action"},
    "project_create": {"studio_api.create_project_api", "studio_api.connect_project_api", "studio_api.project_rediscover",
                       "studio_api.fs_mkdir"},
    "project_delete": {"studio_api.project_delete", "studio_api.project_forget"},
    "runs": {"studio_api.plan_start", "studio_api.plan_add_row", "studio_api.plan_turn_override", "studio_api.plan_review",
             "studio_api.plan_response", "studio_api.plan_reject", "studio_api.plans_reconcile", "control.start_run",
             "control.resume_run", "control.retry_run", "control.retry_step"},
    "chat": {"studio_api.chat_session_create", "studio_api.chat_session_rename", "studio_api.chat_session_delete",
             "studio_api.chat_message", "studio_api.chat_decision", "studio_api.chat_promote", "studio_api.mailbox_messages",
             "studio_api.mailbox_action", "studio_api.chat_settings_save"},
    "file_edits": {"studio_api.project_manifest_save", "studio_api.project_manifest_migrate", "studio_api.history_restore",
                   "studio_api.edit_handoff", "studio_api.project_task", "studio_api.task_create", "studio_api.task_rename",
                   "studio_api.targets_save", "studio_api.targets_add", "studio_api.targets_remove", "studio_api.notes_create",
                   "studio_api.notes_change", "studio_api.project_quickstart_apply", "studio_api.avica_config_update",
                   "studio_api.chat_settings_save", "studio_api.plan_cells", "api.datasets_import_api",
                   "studio_api.chat_promote"},
}
# Allowed even in read-only mode: reads sent as POST, stopping work, the Studio's own housekeeping.
ALWAYS_ALLOWED = {
    "studio_api.presence_api", "studio_api.theme_set", "studio_api.studio_quit", "studio_api.studio_restart",
    "studio_api.plugin_check", "studio_api.plugin_project_check", "studio_api.plugin_project_applicability",
    "studio_api.targets_preview", "studio_api.plan_preview", "studio_api.avica_summary", "studio_api.notification_test",
    "studio_api.plan_unit_stop", "control.cancel_run", "studio_api.project_visibility",
    "studio_api.superuser_save", "studio_api.superuser_reset", "studio_api.superuser_ops_action",
}
# Endpoints whose action argument decides: these actions only stop or pause work.
STOP_ACTIONS = {"studio_api.plan_action": {"pause", "cancel"}, "studio_api.chat_task_action": {"cancel"}}
STOP_CATEGORY = {"studio_api.plan_action": "runs", "studio_api.chat_task_action": "chat"}


def lockdown_categories() -> list[str]:
    """The lockdown switches this request needs (``["read_only"]`` for any other change); [] when free."""
    endpoint = request.endpoint or ""
    if endpoint in ALWAYS_ALLOWED:
        return []
    if endpoint in STOP_ACTIONS:
        action = (request.view_args or {}).get("action")
        return [] if action in STOP_ACTIONS[endpoint] else [STOP_CATEGORY[endpoint]]
    found = [category for category, endpoints in LOCKED_ENDPOINTS.items() if endpoint in endpoints]
    return found or ["read_only"]


def lockdown_refusal(categories: list[str], data=None) -> str | None:
    """Why the superuser settings refuse these categories, or None."""
    from alfrd import superuser

    if not categories:
        return None
    lock = (data or superuser.load())["lockdown"]
    if lock["read_only"]:
        return "this Studio is read-only (superuser setting lockdown.read_only)"
    for category in categories:
        if category != "read_only" and not lock.get(category, True):
            return f"turned off by the superuser ({superuser.LABELS.get('lockdown.' + category, category)}: lockdown.{category})"
    return None


def enforce_lockdown():
    """``before_request`` after the CSRF gate: refuse what the superuser settings lock (403, reason superuser)."""
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"} or request.url_rule is None:
        return None
    from flask import jsonify

    categories = lockdown_categories()
    message = lockdown_refusal(categories)
    if message is None:
        return None
    return jsonify(error={"code": 403, "message": message, "reason": "superuser", "lock": categories}), 403


def mutations_enabled() -> bool:
    configured = runtime_enabled() and bool(
        current_app.config.get("RUNTIME_MUTATIONS_ENABLED", True)
    )
    if not configured or not has_request_context():
        return configured
    try:
        host = urlsplit(f"//{request.host}").hostname
    except ValueError:
        return False
    return _is_loopback(host) and _is_loopback(request.remote_addr)


def csrf_token() -> str:
    token = session.get(_CSRF_SESSION_KEY)
    if not isinstance(token, str) or not token:
        token = secrets.token_urlsafe(32)
        session[_CSRF_SESSION_KEY] = token
    return token


def _is_loopback(value: str | None) -> bool:
    if not value:
        return False
    if value.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def _default_port(scheme: str) -> int | None:
    return {"http": 80, "https": 443}.get(scheme.lower())


def _same_origin(source: str) -> bool:
    try:
        supplied = urlsplit(source)
        expected = urlsplit(request.host_url)
        supplied_port = supplied.port or _default_port(supplied.scheme)
        expected_port = expected.port or _default_port(expected.scheme)
    except ValueError:
        return False
    return (
        supplied.scheme.lower() == expected.scheme.lower()
        and (supplied.hostname or "").lower() == (expected.hostname or "").lower()
        and supplied_port == expected_port
    )


def protect_mutation() -> None:
    """Reject unsafe requests unless runtime writes are local and intentional."""

    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return
    # Preserve Flask's normal 404/405 routing semantics for URLs that did not
    # resolve to an unsafe-method handler.
    if request.url_rule is None:
        return
    require_local_csrf()


def require_local_csrf() -> None:
    """Abort unless the request is local (loopback), same-origin and carries the CSRF token.

    Used by every mutation and by sensitive reads such as the server folder
    browser (``GET /api/studio/fs/list``).
    """
    if not mutations_enabled():
        abort(403, description="runtime mutations are disabled")

    host = urlsplit(f"//{request.host}").hostname
    if not _is_loopback(host) or not _is_loopback(request.remote_addr):
        abort(403, description="runtime mutations are available only from loopback")

    source = request.headers.get("Origin") or request.headers.get("Referer")
    if source and not _same_origin(source):
        abort(403, description="cross-origin mutation rejected")

    supplied = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
    expected = session.get(_CSRF_SESSION_KEY, "")
    if not (
        isinstance(supplied, str)
        and isinstance(expected, str)
        and expected
        and supplied.isascii()
        and expected.isascii()
        and hmac.compare_digest(supplied, expected)
    ):
        abort(403, description="invalid or missing CSRF token")


__all__ = ["csrf_token", "mutations_enabled", "protect_mutation", "require_local_csrf", "runtime_enabled"]
