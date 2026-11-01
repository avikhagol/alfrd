"""Installed-app OAuth. Refresh tokens live only in a private, atomic local file.

No browser or Google request occurs on import. Credential refresh uses the same
synchronous, deadline-bounded transport as the service-account client.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sys
import tempfile
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

from platformdirs import user_config_dir

from .client import SyncError, _bounded, remaining

DRIVE_FILE = "https://www.googleapis.com/auth/drive.file"
OAUTH_SCOPES = ("openid", "https://www.googleapis.com/auth/userinfo.email", DRIVE_FILE)
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
NOT_SIGNED_IN = ("Not signed in to Google. Open a project's Google Sheet dialog and choose "
                 "Sign in with Google, or run: alfrd gsheet auth login")
REAUTH = ("Google sign-in expired or was revoked. Sign in again in the Google Sheet dialog "
          "or run: alfrd gsheet auth login")
HEADLESS = ("Sign in with Google needs a web browser on this computer. On a server, set "
            "Sign-in method to service_account and paste a service-account key (see README 'Advanced: service account').")
ENV = {"client_id": "ALFRD_GSHEET_OAUTH_CLIENT_ID", "client_secret": "ALFRD_GSHEET_OAUTH_CLIENT_SECRET",
       "picker_api_key": "ALFRD_GSHEET_PICKER_API_KEY", "project_number": "ALFRD_GSHEET_GCP_PROJECT_NUMBER"}
_store_lock = threading.RLock()
_cache = OrderedDict()


CLIENT_FILE_ENV = "ALFRD_GSHEET_OAUTH_CLIENT_FILE"


class _OAuthConfig(dict):
    def __repr__(self):
        return "<OAuthConfig (values redacted)>"

    __str__ = __repr__


def _client_defaults(path, source):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        raise SyncError(f"Cannot read {source}; check the OAuth client file path and permissions.") from None
    except (ValueError, UnicodeError):
        raise SyncError(f"Invalid JSON in {source}; check the OAuth client file.") from None
    if not isinstance(data, dict):
        raise SyncError(f"Invalid {source}; expected an OAuth client JSON object.")
    if "web" in data:
        raise SyncError(f"Invalid {source}; a Desktop app client is required (not a Web client).")
    if "installed" in data:
        data = data["installed"]
        if not isinstance(data, dict):
            raise SyncError(f"Invalid {source}; expected a Desktop app client JSON object.")
    return {key: data.get(key, "") for key in ENV}


def config():
    path = Path(__file__).with_name("oauth_client.json")
    defaults = _client_defaults(path, str(path))
    sources = {key: str(path) for key in ENV}
    if filename := os.environ.get(CLIENT_FILE_ENV):
        source = f"{CLIENT_FILE_ENV} ({filename})"
        for key, value in _client_defaults(Path(filename), source).items():
            if value != "":
                defaults[key], sources[key] = value, source
    out = _OAuthConfig()
    for key, env in ENV.items():
        value = os.environ.get(env)
        out[key] = value or defaults[key]
        if value:
            sources[key] = env
    patterns = {"client_id": r"[A-Za-z0-9_-]+\.apps\.googleusercontent\.com",
                "client_secret": r"[^\s]{1,256}", "picker_api_key": r"[A-Za-z0-9_-]{30,64}",
                "project_number": r"[0-9]{6,20}"}
    for key, value in out.items():
        if not isinstance(value, str) or (value and not re.fullmatch(patterns[key], value)):
            raise SyncError(f"Invalid {ENV[key]} in {sources[key]}; check the OAuth configuration.")
    return out


def require_config(*, picker=False):
    cfg = config()
    keys = ENV if picker else ("client_id", "client_secret")
    if any(not cfg[k] for k in keys):
        raise SyncError("Google sign-in is not available in this build. Configure the OAuth client."
                        if not picker else "Choosing existing files isn't set up in this build (missing Picker API key or project number).")
    return cfg


def token_path():
    return Path(user_config_dir("alfrd")) / "gsheet" / "oauth-token.json"


def load_token():
    try:
        data = json.loads(token_path().read_text())
        if (not isinstance(data, dict) or data.get("version") != 1
                or not isinstance(data.get("client_id"), str)
                or not isinstance(data.get("refresh_token"), str) or not data["refresh_token"]
                or data.get("status") not in ("ok", "needs_reauth")
                or not isinstance(data.get("scopes"), list) or DRIVE_FILE not in data["scopes"]):
            return {}
        return data
    except (OSError, ValueError, UnicodeError):
        return {}


def save_token(data):
    with _store_lock:
        path = token_path()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.parent.chmod(0o700)
        fd, name = tempfile.mkstemp(prefix=".oauth-", dir=path.parent)
        try:
            with os.fdopen(fd, "w") as stream:
                os.fchmod(stream.fileno(), 0o600)
                json.dump(data, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, path)
        finally:
            Path(name).unlink(missing_ok=True)


def account():
    cfg, data = config(), load_token()
    status = data.get("status", "signed_out")
    reason = data.get("status_reason", "")
    if data and data["client_id"] != cfg["client_id"]:
        status, reason = "needs_reauth", "client_changed"
    available = bool(cfg["client_id"] and cfg["client_secret"])
    return {"mode": "oauth", "available": available, "signed_in": status == "ok" and available,
            "status": status, "status_reason": reason, "email": data.get("email", ""),
            "picker": available and bool(cfg["picker_api_key"] and cfg["project_number"]),
            "create": available, "remote_hint": remote_hint()}


def mark_needs_reauth(expected=None):
    with _store_lock:
        data = load_token()
        # An old in-flight refresh must not invalidate a newer sign-in.
        if data and (expected is None or data["refresh_token"] == expected):
            data.update(status="needs_reauth", status_reason="invalid_grant")
            save_token(data)
        _cache.clear()


def pkce_challenge(verifier):
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode().rstrip("=")


def authorization_url(cfg, redirect_uri, state, verifier):
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
        "response_type": "code", "client_id": cfg["client_id"], "redirect_uri": redirect_uri,
        "scope": " ".join(OAUTH_SCOPES), "code_challenge": pkce_challenge(verifier),
        "code_challenge_method": "S256", "state": state, "access_type": "offline", "prompt": "consent"})


def _post(url, data, *, timeout=15, session_factory=None):
    if session_factory is None:
        from requests import Session
        session_factory = Session
    try:
        with session_factory() as session:
            session.trust_env = False
            response = session.post(url, data=data, timeout=timeout, allow_redirects=False)
            try:
                if not 200 <= response.status_code < 300:
                    raise SyncError("Google sign-in request failed. Try again or check your organisation's policy.")
                return response.json() if url == TOKEN_URL else {}
            finally:
                response.close()
    except SyncError:
        raise
    except Exception:
        raise SyncError("Google sign-in network request failed. Try again.") from None


def revoke(token, *, session_factory=None):
    if not token:
        return False
    try:
        _bounded(lambda: _post(REVOKE_URL, {"token": token}, timeout=10,
                               session_factory=session_factory), time.monotonic() + 10)
        return True
    except SyncError:
        return False


def sign_out(*, session_factory=None):
    # Cancel sessions before deleting: a late callback cannot restore the grant.
    from . import loopback
    loopback.cancel_all()
    with _store_lock:
        data = load_token()
        try:
            return revoke(data.get("refresh_token"), session_factory=session_factory)
        finally:
            token_path().unlink(missing_ok=True)
            _cache.clear()


class ScopeMissing(SyncError):
    pass


def exchange(code, verifier, redirect_uri, cfg, *, session_factory=None):
    """Return a validated token record; only a still-active session may persist it."""
    if not isinstance(code, str) or not code or not re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", verifier):
        raise SyncError("Invalid Google sign-in callback.")
    data = _bounded(lambda: _post(TOKEN_URL, {"code": code, "client_id": cfg["client_id"],
                  "client_secret": cfg["client_secret"], "code_verifier": verifier,
                  "redirect_uri": redirect_uri, "grant_type": "authorization_code"},
                  session_factory=session_factory), time.monotonic() + 15)
    if not isinstance(data, dict):
        raise SyncError("Google returned an invalid sign-in response.")
    scopes = data.get("scope", "").split() if isinstance(data.get("scope"), str) else []
    if DRIVE_FILE not in scopes:
        revoke(data.get("refresh_token") or data.get("access_token"), session_factory=session_factory)
        raise ScopeMissing("Google Drive file access was not granted. Sign in again and allow file access.")
    if not isinstance(data.get("refresh_token"), str) or not data["refresh_token"]:
        raise SyncError("Google did not return offline access. Sign in again.")
    claims = {}
    if data.get("id_token"):
        try:
            payload = data["id_token"].split(".")[1]
            claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
            if (claims.get("aud") != cfg["client_id"] or claims.get("iss") not in
                    ("accounts.google.com", "https://accounts.google.com") or claims["exp"] <= time.time()):
                raise ValueError()
        except (ValueError, TypeError, KeyError, IndexError):
            raise SyncError("Google returned invalid identity claims. Sign in again.") from None
    email = claims.get("email", "") if claims.get("email_verified") is True else ""
    if not isinstance(email, str) or len(email) > 320 or any(ord(c) < 32 for c in email):
        email = ""
    return {"version": 1, "client_id": cfg["client_id"], "refresh_token": data["refresh_token"],
            "scopes": scopes, "email": email, "sub": claims.get("sub", ""),
            "signed_in_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "status": "ok", "status_reason": ""}


@dataclass
class CredentialSource:
    """Marker selecting installed-app credentials, with no secret in its repr."""


def credentials(source, deadline):
    cfg = require_config()
    with _store_lock:
        data = load_token()
        if not data:
            raise SyncError(NOT_SIGNED_IN, code="auth_required")
        if data["status"] != "ok" or data["client_id"] != cfg["client_id"]:
            raise SyncError(REAUTH, code="auth_required")
        key = (str(token_path()), cfg["client_id"], cfg["client_secret"],
               hashlib.sha256(data["refresh_token"].encode()).hexdigest())
        if key not in _cache:
            from google.oauth2.credentials import Credentials
            from google.auth.exceptions import RefreshError

            class UserCredentials(Credentials):
                def refresh(self, request):
                    try:
                        super().refresh(request)
                    except RefreshError as exc:
                        if "invalid_grant" in str(exc):
                            mark_needs_reauth(self.refresh_token)
                            raise SyncError(REAUTH, code="auth_required") from None
                        raise SyncError("Google sign-in refresh failed. Try again later.") from None
            creds = UserCredentials(token=None, refresh_token=data["refresh_token"], token_uri=TOKEN_URL,
                                    client_id=cfg["client_id"], client_secret=cfg["client_secret"], scopes=OAUTH_SCOPES)
            _cache[key] = creds, threading.Lock()
            if len(_cache) > 8:
                _cache.popitem(last=False)
        _cache.move_to_end(key)
        return _cache[key]


def access_token(*, deadline):
    def refresh():
        creds, lock = credentials(CredentialSource(), deadline)
        if not lock.acquire(timeout=remaining(deadline)):
            raise SyncError("Google Sheet request deadline exceeded.")
        try:
            if not creds.valid:
                from google.auth.transport.requests import Request
                from requests import Session
                with Session() as session:
                    session.trust_env = False
                    request = Request(session=session)
                    creds.refresh(lambda *a, **kw: request(*a, **{**kw, "timeout": min(15, remaining(deadline))}))
            return creds.token
        finally:
            lock.release()
    return _bounded(refresh, deadline)


def secret_values():
    """Only for server-side output redaction; never serialize this result."""
    cfg = config()
    with _store_lock:
        return [cfg["client_secret"], cfg["picker_api_key"], load_token().get("refresh_token", ""),
                *(creds.token for creds, _ in _cache.values() if creds.token)]


def remote_hint():
    return bool(os.environ.get("SSH_CONNECTION"))


def browser_available():
    return not remote_hint() and (not sys.platform.startswith("linux") or
                                bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")))
