"""The built-in notifiers: ``desktop``, ``webhook``, ``command`` (notifier plan A4) and ``telegram``.

Each is a :data:`alfrd.notify.Sender`: ``send(route, message, timeout)`` gets the route
(options such as ``url``/``secret``/``argv`` sit next to ``via``/``on``) and one
``alfrd.notification/1`` message. It raises to be retried, raises :class:`~alfrd.notify.Skip`
when retrying can't help, and never waits longer than ``timeout``. Standard library only.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from alfrd.notify import SENDERS, Skip

URGENT = ("review.pending",)  # plus every *.failed kind


def _payload(message: Mapping[str, Any]) -> bytes:
    return json.dumps(message, ensure_ascii=False, default=str).encode("utf-8")


def _failure(proc: subprocess.CompletedProcess) -> str:
    err = (proc.stderr or b"").decode("utf-8", "replace").strip()
    return f"exit {proc.returncode}" + (f": {err[:200]}" if err else "")


# -- desktop ------------------------------------------------------------------
def _user_bus() -> Path:
    return Path(f"/run/user/{os.getuid()}/bus")


def desktop_env(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment for ``notify-send``; raises :class:`Skip` when there is no desktop session.

    Runners are detached and may lack ``DBUS_SESSION_BUS_ADDRESS``; the systemd user bus
    (``/run/user/<uid>/bus``) is used then.
    """
    env = dict(os.environ if environ is None else environ)
    if env.get("DBUS_SESSION_BUS_ADDRESS"):
        return env
    bus = _user_bus()
    if bus.exists():
        env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={bus}"
        return env
    if env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"):
        return env  # notify-send can still find the bus through the display
    raise Skip("no desktop session (DBUS_SESSION_BUS_ADDRESS, DISPLAY and WAYLAND_DISPLAY are unset, "
               f"no {bus})")


def _urgent(message: Mapping[str, Any]) -> bool:
    return any(str(k).endswith(".failed") or k in URGENT for k in message.get("kinds") or ())


def desktop(route: Mapping[str, Any], message: Mapping[str, Any], timeout: float) -> None:
    """``notify-send`` on Linux, ``osascript`` on macOS."""
    title = str(message.get("title") or "ALFRD")
    body = str(message.get("body") or "")
    if sys.platform == "darwin":
        exe = shutil.which("osascript")
        if not exe:
            raise Skip("osascript not found")
        # text goes in as arguments, never into the script source
        argv = [exe, "-e", "on run argv", "-e", "display notification (item 2 of argv) with title (item 1 of argv)",
                "-e", "end run", title, body]
        env = None
    elif os.name == "posix":
        env = desktop_env()
        exe = shutil.which("notify-send")
        if not exe:
            raise Skip("notify-send not found (install libnotify-bin or libnotify)")
        argv = [exe, "--app-name=ALFRD", *(["--urgency=critical"] if _urgent(message) else []), "--", title, body]
    else:
        raise Skip(f"desktop notifications are not supported on {sys.platform}")
    proc = subprocess.run(argv, env=env, capture_output=True, timeout=timeout, check=False)
    if proc.returncode:
        raise RuntimeError(_failure(proc))


# -- webhook ------------------------------------------------------------------
def is_loopback(host: str | None) -> bool:
    host = (host or "").strip("[]").lower()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is an error: the payload must not follow it to another host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def signature(secret: str, body: bytes) -> str:
    """The ``X-Alfrd-Signature`` value: ``sha256=<hex HMAC of the body>``."""
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def webhook(route: Mapping[str, Any], message: Mapping[str, Any], timeout: float) -> None:
    """POST the message as JSON; https unless the host is loopback; signed when ``secret`` is set."""
    from alfrd import __version__

    url = str(route.get("url") or "")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise Skip("webhook url must be https://host/…")
    if parts.scheme == "http" and not is_loopback(parts.hostname):
        raise Skip(f"webhook to {parts.hostname} needs https (http is allowed for loopback hosts only)")
    body = _payload(message)
    headers = {str(k): str(v) for k, v in (route.get("headers") or {}).items()}
    headers.update({"Content-Type": "application/json", "User-Agent": f"alfrd/{__version__}"})
    if route.get("secret"):
        headers["X-Alfrd-Signature"] = signature(str(route["secret"]), body)
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    opener = urllib.request.build_opener(_NoRedirect)
    with opener.open(request, timeout=timeout) as response:  # non-2xx raises HTTPError → retried
        response.read(65536)


# -- command ------------------------------------------------------------------
def command(route: Mapping[str, Any], message: Mapping[str, Any], timeout: float) -> None:
    """Run ``argv`` (no shell) with the message JSON on stdin; non-zero exit is an error."""
    argv = route.get("argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) and a for a in argv):
        raise Skip("command needs argv: a nonempty list of strings")
    try:
        proc = subprocess.run(argv, input=_payload(message), capture_output=True, timeout=timeout,
                              shell=False, check=False)
    except FileNotFoundError as exc:
        raise Skip(f"command not found: {argv[0]}") from exc
    if proc.returncode:
        raise RuntimeError(_failure(proc))


# -- telegram -----------------------------------------------------------------
TELEGRAM_API = "https://api.telegram.org"
TELEGRAM_LIMIT = 4096  # sendMessage text limit (counted here in UTF-16 units, which is stricter)
_TELEGRAM_SKIP = (400, 401, 403, 404)  # bad chat, bad token, bot blocked: retrying can't help


class TelegramError(RuntimeError):
    """A Telegram Bot API call failed; ``status`` is the HTTP code (0 for a network error)."""

    def __init__(self, text: str, status: int = 0) -> None:
        super().__init__(text)
        self.status = status


def _scrub(text: str, token: str) -> str:
    return text.replace(token, "<token>") if token else text


def telegram_call(token: str, method: str, params: Mapping[str, Any], timeout: float) -> Any:
    """POST ``params`` as JSON to Bot API ``method``; returns ``result``.

    Raises :class:`TelegramError`, whose text never contains the token. Shared with the
    ``alfrd-telegram`` bot plugin.
    """
    url = f"{TELEGRAM_API}/bot{token}/{method}"
    body = json.dumps(params, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=timeout) as response:
            reply = json.loads(response.read(1 << 20) or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            detail = _scrub(str(json.loads(exc.read(65536) or b"{}").get("description") or ""), token)
        except (ValueError, AttributeError, OSError):
            detail = ""
        text = f"telegram {method}: HTTP {exc.code}" + (f": {detail[:200]}" if detail else "")
        raise TelegramError(_scrub(text, token), exc.code) from None
    except (OSError, ValueError) as exc:  # URLError, timeouts, a non-JSON answer
        reason = getattr(exc, "reason", exc)
        raise TelegramError(_scrub(f"telegram {method}: {type(exc).__name__}: {reason}", token)) from None
    if not isinstance(reply, dict) or not reply.get("ok"):
        detail = _scrub(str(reply.get("description") or ""), token) if isinstance(reply, dict) else ""
        raise TelegramError(_scrub(f"telegram {method}: not ok: {detail[:200]}", token))
    return reply.get("result")


def _utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _clip(text: str, limit: int) -> str:
    """``text`` cut to ``limit`` UTF-16 units, ending with ``…`` when cut."""
    if _utf16_len(text) <= limit:
        return text
    out, used = [], 1  # room for the ellipsis
    for char in text:
        used += _utf16_len(char)
        if used > limit:
            break
        out.append(char)
    return "".join(out).rstrip() + "…"


def telegram_text(route: Mapping[str, Any], message: Mapping[str, Any]) -> str:
    """Plain text: title line, body, then the Studio link when ``studio_url`` is set; ≤ 4096 units."""
    title = " ".join(str(message.get("title") or "ALFRD").split()) or "ALFRD"
    body = str(message.get("body") or "")
    link = ""
    studio, rel = str(route.get("studio_url") or "").rstrip("/"), str(message.get("link") or "")
    if studio and rel:
        link = studio + (rel if rel.startswith("/") else "/" + rel)
    tail = f"\n\nOpen in Studio\n{link}" if link else ""
    if _utf16_len(tail) >= TELEGRAM_LIMIT - 3:
        raise Skip("telegram Studio link exceeds the message limit")
    head = _clip(title, min(256, TELEGRAM_LIMIT - _utf16_len(tail) - 3))
    room = TELEGRAM_LIMIT - _utf16_len(head) - _utf16_len(tail) - 2
    return head + (f"\n\n{_clip(body, room)}" if body else "") + tail


def telegram_settings() -> dict[str, Any]:
    """The Telegram plugin's saved ``token`` / ``chat_id`` (``{}`` when there are none)."""
    try:
        from alfrd.extensions import settings

        return settings.values("telegram")
    except Exception:  # noqa: BLE001 - a broken settings file is the same as none
        return {}


def telegram(route: Mapping[str, Any], message: Mapping[str, Any], timeout: float) -> None:
    """``sendMessage`` to ``chat_id`` with bot ``token``; 400/401/403/404 are skipped, 429/5xx retried.

    A route without ``token`` / ``chat_id`` uses the values saved in Settings → Plugins → Telegram.
    """
    token, chat_id = route.get("token"), route.get("chat_id")
    if token is None or chat_id is None:
        saved = telegram_settings()
        token = saved.get("token") if token is None else token
        chat_id = saved.get("chat_id") if chat_id is None else chat_id
    if not isinstance(token, str) or not token.strip():
        raise Skip("telegram needs token (in notify.json or Settings → Plugins → Telegram)")
    if isinstance(chat_id, bool) or not isinstance(chat_id, (str, int)) or not str(chat_id).strip():
        raise Skip("telegram needs chat_id (in notify.json or Settings → Plugins → Telegram)")
    params = {"chat_id": chat_id, "text": telegram_text(route, message),
              "link_preview_options": {"is_disabled": True}}
    try:
        telegram_call(token.strip(), "sendMessage", params, timeout)
    except TelegramError as exc:
        if exc.status in _TELEGRAM_SKIP:
            raise Skip(str(exc)) from None
        raise


SENDERS.update(desktop=desktop, webhook=webhook, command=command, telegram=telegram)

__all__ = ["TelegramError", "command", "desktop", "desktop_env", "is_loopback", "signature", "telegram",
           "telegram_call", "telegram_settings", "telegram_text", "webhook"]
