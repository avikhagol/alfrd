"""Per-user server credentials, request authentication and in-app pairing.

Data routes require a signed session or a Bearer token. Opening an access link
or submitting the login form establishes the session. Durable token/signing-key
generations live in owner-only ``credentials-<port>.json`` records, separately
from the removable ``server-<port>.json`` discovery file used by ``alfrd url``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import stat
import tempfile
import threading
import time
from collections import deque
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

from flask import (
    current_app,
    redirect,
    render_template,
    request,
    session,
    url_for,
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


# Durable credentials are separate from the removable live discovery file.
def _safe_stat(info, *, directory=False):
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if (not kind(info.st_mode) or (hasattr(os, "getuid") and info.st_uid != os.getuid())
            or (os.name != "nt" and stat.S_IMODE(info.st_mode) & 0o077)
            or (not directory and info.st_nlink != 1)):
        raise ValueError("Unsafe ALFRD credential path: require owner-only directory/file")


def _credential_directory():
    path = config_dir().absolute()
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("Symlinks are not allowed in ALFRD credential paths")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    _safe_stat(path.lstat(), directory=True)
    return path


@contextmanager
def _credential_lock(path):
    # Keep the lock inode: unlinking it would allow a third writer to bypass a waiter.
    if path.exists():
        _safe_stat(path.lstat())
    fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0), 0o600)
    try:
        _safe_stat(os.fstat(fd))
        if path.is_symlink() or path.stat().st_ino != os.fstat(fd).st_ino:
            raise ValueError("Unsafe credential lock")
        if os.name == "nt":  # pragma: no cover - Windows
            import msvcrt
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"0")
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def durable_credentials(port, policy, *, token=None, secret_key=None, rotate=False):
    """Load/create one policy-scoped generation; changing policy revokes the old one.

    Fixed operator credentials take precedence. Never recover from an unsafe or
    malformed file by silently overwriting it. All readers/writers use the lock.
    """
    directory = _credential_directory()
    path = directory / f"credentials-{int(port)}.json"
    fingerprint = hashlib.sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest()
    with _credential_lock(directory / f"credentials-{int(port)}.lock"):
        data = None
        if path.is_symlink():
            raise ValueError("Unsafe credential symlink")
        if path.exists():
            _safe_stat(path.lstat())
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
            with os.fdopen(fd, "r", encoding="utf-8") as handle:
                _safe_stat(os.fstat(handle.fileno()))
                data = json.load(handle)
            if (not isinstance(data, dict) or data.get("version") != 1
                    or not all(isinstance(data.get(k), str) and data[k] for k in ("policy", "token", "secret_key"))):
                raise ValueError("Invalid ALFRD credential record")
        if (data and data["policy"] != fingerprint
                and ((token and token == data["token"]) or (secret_key and secret_key == data["secret_key"]))):
            raise ValueError("Access policy changed: supply new fixed credentials or unset them to generate a new pair")
        reuse = (data and not rotate and data["policy"] == fingerprint
                 and (token is None or token == data["token"])
                 and (secret_key is None or secret_key == data["secret_key"]))
        if reuse:
            return data["token"], data["secret_key"]
        data = {"version": 1, "policy": fingerprint, "token": token or new_token(),
                "secret_key": secret_key or secrets.token_hex(32)}
        fd, temporary = tempfile.mkstemp(prefix=".credentials-", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return data["token"], data["secret_key"]


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
            (_landing(), 401)
        )
        response.headers["WWW-Authenticate"] = 'Bearer realm="alfrd"'
        response.headers["Cache-Control"] = "no-store"
        return _private(response)
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
        _authenticate(False)
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


def _authenticate(remember):
    from alfrd.gui.security import csrf_token

    session.clear()
    session[SESSION_KEY] = _digest(_expected_token())
    session.permanent = remember
    csrf_token()


def _landing(error=None):
    nonce = session.get("_alfrd_login_nonce")
    if not nonce:
        nonce = session["_alfrd_login_nonce"] = secrets.token_urlsafe(32)
    return render_template("auth/landing.htm", port=_request_port(), nonce=nonce, error=error)


def _landing_response(error=None, status=200):
    return _private(current_app.make_response((_landing(error), status)))


def _pasted_token(value):
    value = value.strip()
    if len(value) > 4096:
        return ""
    if "://" not in value:
        return value
    from alfrd.gui.security import _same_origin

    try:
        parsed = urlsplit(value)
        query = parse_qs(parsed.query)
        if (not _same_origin(value) or parsed.username or parsed.password or parsed.fragment
                or parsed.path != url_for("studio.studio_index") or len(query.get("token", [])) != 1):
            return ""
        return query["token"][0]
    except ValueError:
        return ""


def _check_login_attempt(value):
    # The paste endpoint is usable on LAN; bound distributed guessing as well.
    throttle = current_app.extensions.setdefault("alfrd_login_global_throttle", _Throttle())
    window = float(current_app.config.get("ACCESS_THROTTLE_WINDOW", 60))
    wait = throttle.retry_after("global", 100, window)
    if wait:
        raise TooManyRequests(description="too many failed attempts", retry_after=wait)
    if _check_attempt(value):
        return True
    throttle.fail("global", window)
    return False


def landing_page():
    """Pair in this browser context without putting pasted credentials in a URL."""
    error = None
    status = 200
    if request.method == "POST":
        from alfrd.gui.security import _same_origin

        # Require a bounded form body before parsing it, including on Flask
        # releases that do not support per-request max_content_length setters.
        if request.content_length is None or request.content_length > 8192:
            return _landing_response("Access form is too large. Reload the access page and paste only the access link or token.", 413)
        # no-referrer makes Firefox send Origin: null on this HTML form POST.
        # An opaque/missing origin is not proof of same-origin: the signed
        # session nonce AND access token below remain mandatory. Fetch metadata
        # rejects known foreign submissions, including opaque-origin forms.
        origin = request.headers.get("Origin")
        source = origin if origin and origin != "null" else request.headers.get("Referer")
        fetch_site = request.headers.get("Sec-Fetch-Site")
        if (source and not _same_origin(source)) or fetch_site in {"cross-site", "same-site"}:
            return _landing_response("Login from another site was blocked. Reload the access page in this app and paste a fresh link from alfrd url.", 403)
        if not _matches(request.form.get("login_nonce", ""), session.get("_alfrd_login_nonce", "")) or not session.get("_alfrd_login_nonce"):
            return _landing_response("This access form has expired or is invalid. Reload the access page and paste the link again.", 403)
        try:
            valid = _check_login_attempt(_pasted_token(request.form.get("credential", "")))
        except TooManyRequests as exc:
            response = _landing_response("Too many attempts. Wait a minute and try again.", 429)
            response.headers["Retry-After"] = str(exc.retry_after)
            return _private(response)
        if valid:
            _authenticate(request.form.get("remember") == "on")
            return _private(redirect(url_for("studio.studio_index"), code=303))
        error, status = "Access link or token not accepted. Run alfrd url for this server and try again.", 401
    return _landing_response(error, status)


def _private(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


class AlfrdSessionInterface(SecureCookieSessionInterface):
    """Mark the session cookie ``Secure`` when the request came over https."""

    def get_cookie_secure(self, app) -> bool:
        return bool(app.config.get("SESSION_COOKIE_SECURE")) or request.is_secure


__all__ = [
    "PUBLIC_ENDPOINTS",
    "AlfrdSessionInterface",
    "config_dir",
    "durable_credentials",
    "landing_page",
    "new_token",
    "read_server_file",
    "remove_server_file",
    "require_access",
    "server_file",
    "write_server_file",
]
