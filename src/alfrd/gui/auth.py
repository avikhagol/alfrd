"""Per-user access token for `alfrd serve`: the token file and the request check.

Every data route needs the token, either as the session cookie set by opening
``/studio/?token=…`` once, or as ``Authorization: Bearer <token>``. The token
and the session secret are written to ``<config dir>/server-<port>.json``
(mode 0600) so `alfrd url` can print the link again.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from collections import deque
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from flask import (
    current_app,
    redirect,
    render_template,
    request,
    session,
)
from flask.sessions import SecureCookieSessionInterface
from werkzeug.datastructures import WWWAuthenticate
from werkzeug.exceptions import TooManyRequests, Unauthorized

SESSION_KEY = "_alfrd_auth"

# Endpoints that answer without the token. Matched by endpoint name, not by
# path prefix, so a new route is protected unless it is added here on purpose
# (tests/test_access_tokens.py checks this set against the URL map).
PUBLIC_ENDPOINTS = frozenset({
    "system.health",
    "api.health",
    "system.version",
    "api.version",
    "system.login",
    "studio.studio_index",
    "studio.studio_asset",
    "static",
})

_THROTTLE_KEY = "alfrd_auth_throttle"


# --- token file --------------------------------------------------------------

def config_dir() -> Path:
    """Resolved per call (not the import-time ``ALFRD_CONFIG_DIR``) so ``XDG_CONFIG_HOME`` applies."""
    from platformdirs import user_config_dir

    return Path(user_config_dir("alfrd"))


def server_file(port: int) -> Path:
    return config_dir() / f"server-{int(port)}.json"


def new_token() -> str:
    return secrets.token_urlsafe(32)


def write_server_file(port: int, url: str, token: str | None, secret_key: str) -> Path:
    """Write ``{url, token, pid, secret_key}`` readable only by this user (0600)."""
    path = server_file(port)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    data = {"url": url, "token": token, "pid": os.getpid(), "secret_key": secret_key}
    # Write a fresh file: O_EXCL on a temporary name means no other mode ever
    # applies to the bytes, then an atomic replace.
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.unlink()
    except FileNotFoundError:
        pass
    fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            os.chmod(tmp, 0o600)
            json.dump(data, handle)
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    return path


def read_server_file(port: int) -> dict | None:
    try:
        data = json.loads(server_file(port).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def remove_server_file(port: int) -> bool:
    """Remove the file only if this process wrote it (another server may own the port now)."""
    data = read_server_file(port)
    if data is None or data.get("pid") != os.getpid():
        return False
    try:
        server_file(port).unlink()
    except OSError:
        return False
    return True


# --- request check -----------------------------------------------------------

def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _matches(supplied: object, expected: str) -> bool:
    return (
        isinstance(supplied, str)
        and supplied.isascii()
        and expected.isascii()
        and hmac.compare_digest(supplied, expected)
    )


def _expected_token() -> str:
    return current_app.config.get("ACCESS_TOKEN") or ""


def _bearer() -> str | None:
    header = request.headers.get("Authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer":
        return None
    return value.strip()


def _request_port() -> int:
    try:
        port = urlsplit(f"//{request.host}").port
    except ValueError:
        port = None
    return port or (443 if request.is_secure else 80)


def _wants_json() -> bool:
    if request.path.startswith("/api/"):
        return True
    best = request.accept_mimetypes.best_match(["text/html", "application/json"])
    return best != "text/html"


def _unauthorized(message: str):
    if not _wants_json():
        response = current_app.make_response(
            (render_template("auth/landing.htm", port=_request_port()), 401)
        )
        response.headers["WWW-Authenticate"] = 'Bearer realm="alfrd"'
        response.headers["Cache-Control"] = "no-store"
        return response
    raise Unauthorized(description=message,
                       www_authenticate=WWWAuthenticate("bearer", {"realm": "alfrd"}))


class _Throttle:
    """Failed token attempts per client address within a sliding window."""

    def __init__(self, max_addresses: int = 10_000) -> None:
        self.lock = threading.Lock()
        self.failures: dict[str, deque[float]] = {}
        self.max_addresses = max_addresses

    def _recent(self, address: str, now: float, window: float) -> deque[float]:
        times = self.failures.setdefault(address, deque())
        while times and times[0] <= now - window:
            times.popleft()
        return times

    def retry_after(self, address: str, limit: int, window: float) -> int:
        """Seconds until the next attempt is allowed (0 if not throttled)."""
        now = time.monotonic()
        with self.lock:
            times = self._recent(address, now, window)
            if len(times) < limit:
                if not times:
                    self.failures.pop(address, None)
                return 0
            return max(1, int(times[-limit] + window - now + 0.999))

    def fail(self, address: str, window: float) -> None:
        now = time.monotonic()
        with self.lock:
            times = self._recent(address, now, window)
            times.append(now)
            self.failures[address] = self.failures.pop(address)  # keep the dict ordered by last failure
            # oldest first: drop addresses whose last failure left the window, and the oldest beyond the cap
            for key in list(self.failures):
                if len(self.failures) <= self.max_addresses and self.failures[key][-1] > now - window:
                    break
                del self.failures[key]


def _throttle() -> _Throttle:
    return current_app.extensions.setdefault(_THROTTLE_KEY, _Throttle())


def _check_attempt(supplied: str, *also: str) -> bool:
    """Compare a supplied token (the access token or one of ``also``), with
    throttling; 429 while the address is over the limit."""
    throttle = _throttle()
    address = request.remote_addr or ""
    limit = int(current_app.config.get("ACCESS_THROTTLE_LIMIT", 10))
    window = float(current_app.config.get("ACCESS_THROTTLE_WINDOW", 60))
    wait = throttle.retry_after(address, limit, window)
    if wait:
        raise TooManyRequests(description="too many failed access-token attempts", retry_after=wait)
    if any(_matches(supplied, token) for token in (_expected_token(), *also) if token):
        return True
    throttle.fail(address, window)
    return False


def _status_api_token() -> str | None:
    from alfrd.gui.api_v1 import _token

    return _token()


def _session_ok() -> bool:
    stored = session.get(SESSION_KEY)
    return _matches(stored, _digest(_expected_token()))


def require_access():
    """``before_request``: the session cookie or a Bearer token, except for public endpoints.

    ``GET …?token=<t>`` trades the token for the session cookie and redirects
    to the same URL without it, so the token does not stay in the address bar
    or the history.
    """
    if not current_app.config.get("ACCESS_TOKEN_REQUIRED", True):
        return None
    # Unknown URL / wrong method: keep Flask's 404 and 405.
    if request.url_rule is None:
        return None

    if request.method == "GET" and "token" in request.args:
        if not _check_attempt(request.args.get("token", "")):
            return _unauthorized("invalid access token")
        session[SESSION_KEY] = _digest(_expected_token())
        session.permanent = False
        query = [(k, v) for k, v in request.args.items(multi=True) if k != "token"]
        # script_root keeps a reverse proxy's path prefix (SCRIPT_NAME).
        target = request.script_root + request.path + (f"?{urlencode(query)}" if query else "")
        response = redirect(target, code=302)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    if request.endpoint in PUBLIC_ENDPOINTS:
        return None

    bearer = _bearer()
    if bearer is not None:
        # The read-only status API keeps its own token (ALFRD_API_TOKEN), valid there only.
        api_token = _status_api_token() if request.blueprint == "api_v1" else None
        if _check_attempt(bearer, *([api_token] if api_token else [])):
            return None
        return _unauthorized("invalid access token")
    if _session_ok():
        return None
    return _unauthorized("access token required: open the link printed by `alfrd serve`")


def landing_page():
    """``GET /login``: the same page a browser gets without the token, with 200."""
    return render_template("auth/landing.htm", port=_request_port())


class AlfrdSessionInterface(SecureCookieSessionInterface):
    """Mark the session cookie ``Secure`` when the request came over https."""

    def get_cookie_secure(self, app) -> bool:
        return bool(app.config.get("SESSION_COOKIE_SECURE")) or request.is_secure


__all__ = [
    "PUBLIC_ENDPOINTS",
    "AlfrdSessionInterface",
    "config_dir",
    "landing_page",
    "new_token",
    "read_server_file",
    "remove_server_file",
    "require_access",
    "server_file",
    "write_server_file",
]
