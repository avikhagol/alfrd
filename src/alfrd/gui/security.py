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


__all__ = ["csrf_token", "mutations_enabled", "protect_mutation", "runtime_enabled"]
