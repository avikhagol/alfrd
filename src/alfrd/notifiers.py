"""The built-in notifiers: ``desktop``, ``webhook`` and ``command`` (notifier plan A4).

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
import urllib.request
from pathlib import Path
from typing import Any, Mapping
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

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
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


SENDERS.update(desktop=desktop, webhook=webhook, command=command)

__all__ = ["command", "desktop", "desktop_env", "is_loopback", "signature", "webhook"]
