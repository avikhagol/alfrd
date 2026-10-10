"""The built-in notifiers: ``desktop``, ``webhook``, ``command`` (notifier plan A4) and ``telegram``.

Each is a :data:`alfrd.notify.Sender`: ``send(route, message, timeout)`` gets the route
(options such as ``url``/``secret``/``argv`` sit next to ``via``/``on``) and one
``alfrd.notification/1`` message. It raises to be retried, raises :class:`~alfrd.notify.Skip`
when retrying can't help, and never waits longer than ``timeout``. Standard library only.
"""
from __future__ import annotations

import hashlib
import hmac
import html
import ipaddress
import json
import os
import shutil
import subprocess
import sys
import time
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


def _ended(message: Mapping[str, Any]) -> bool:
    from alfrd.notify import ENDED

    return any(k in ENDED for k in message.get("kinds") or ())


def _studio_url(route: Mapping[str, Any], message: Mapping[str, Any]) -> str:
    """The Studio link; "" without ``studio_url`` and for an ended plan (nothing left to open from a phone)."""
    studio, rel = str(route.get("studio_url") or "").rstrip("/"), str(message.get("link") or "")
    if not studio or not rel or _ended(message):
        return ""
    return studio + (rel if rel.startswith("/") else "/" + rel)


def telegram_text(route: Mapping[str, Any], message: Mapping[str, Any]) -> str:
    """Plain text (the fallback when Telegram rejects the HTML): title, body, Studio link; ≤ 4096 units."""
    title = " ".join(str(message.get("title") or "ALFRD").split()) or "ALFRD"
    body = str(message.get("body") or "")
    link = _studio_url(route, message)
    tail = f"\n\nOpen in Studio\n{link}" if link else ""
    if _utf16_len(tail) >= TELEGRAM_LIMIT - 3:
        raise Skip("telegram Studio link exceeds the message limit")
    head = _clip(title, min(256, TELEGRAM_LIMIT - _utf16_len(tail) - 3))
    room = TELEGRAM_LIMIT - _utf16_len(head) - _utf16_len(tail) - 2
    return head + (f"\n\n{_clip(body, room)}" if body else "") + tail


# Telegram HTML accepts only b i u s code pre a blockquote; every value goes through _esc.
RULE = "━" * 14
_ICONS = {"done": "✅", "running": "🔄", "failed": "❌", "blocked": "⛔", "cancelled": "🚫",
          "interrupted": "⚠️", "todo": "⏳", "paused": "⏸️", "pending": "📝", "skip": "⏭️"}
_KIND_ICONS = {"plan.started": "🚀", "plan.finished": "🏁", "plan.failed": "❌", "plan.cancelled": "🚫",
               "plan.interrupted": "⚠️", "turn.started": "▶️", "turn.finished": "✅", "turn.failed": "❌",
               "turn.retrying": "🔁", "turn.fallback_model": "🔀", "turn.idle": "💤", "review.pending": "📝",
               "review.approved": "👍", "review.rejected": "👎", "handoff.published": "📨",
               "limit.reached": "⛔", "plugin.hook_failed": "⚠️"}


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _elapsed(seconds: Any) -> str:
    """``42s``, ``12m 03s``, ``1h 05m``; ``time unavailable`` when unknown."""
    try:
        seconds = int(max(0.0, float(seconds)))
    except (TypeError, ValueError):
        return "time unavailable"
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60:02d}s"
    return f"{seconds // 3600}h {seconds % 3600 // 60:02d}m"


def _headline_icon(message: Mapping[str, Any]) -> str:
    kinds = [str(k) for k in message.get("kinds") or ()]
    if any(k.endswith(".failed") for k in kinds):
        return "❌"
    if "plan.finished" in kinds:
        steps = [s for t in (message.get("summary") or {}).get("targets") or () for s in t.get("steps") or ()]
        return "⚠️" if any(s.get("status") in ("failed", "blocked") for s in steps) else "🏁"
    return next((_KIND_ICONS[k] for k in kinds if k in _KIND_ICONS), "🔔")


def _event_line(event: Mapping[str, Any]) -> str:
    from alfrd.notify import _phrase

    kind = str(event.get("kind") or "")
    parts = [f"{_KIND_ICONS.get(kind, '•')} {_esc(_phrase(kind, 1))}"]
    if event.get("unit"):
        parts.append(f"<code>{_esc(event['unit'])}</code>")
    if event.get("turn"):
        parts.append(f"turn {_esc(event['turn'])}/{_esc(event.get('turns') or '?')}")
    if event.get("agent"):
        parts.append(f"🤖 {_esc(event['agent'])}")
    return " · ".join(parts)


def _summary_lines(summary: Mapping[str, Any]) -> list[str]:
    """The avica run summary: a totals line, then per target ``icon step · status · duration``."""
    targets = summary.get("targets") or []
    if not targets:
        return []
    steps = [s for t in targets for s in t.get("steps") or ()]
    counts: dict[str, int] = {}
    for step in steps:
        counts[str(step.get("status"))] = counts.get(str(step.get("status")), 0) + 1
    durations = [float(s["duration_s"]) for s in steps if isinstance(s.get("duration_s"), (int, float))]
    total = sum(durations) if durations else None
    tally = " · ".join(f"{_ICONS.get(k, '•')} {n} {_esc(k)}" for k, n in counts.items())
    lines = ["", "📊 <b>Run summary</b>", f"⏱️ {_elapsed(total)} total step time · {tally}"]
    for target in targets:
        lines += ["", f"🎯 <b>{_esc(target.get('target') or '?')}</b>"]
        for step in target.get("steps") or ():
            status = str(step.get("status") or "?")
            lines.append(f"{_ICONS.get(status, '•')} <code>{_esc(step.get('step'))}</code> · {_esc(status)}"
                         f" · {_elapsed(step.get('duration_s'))}")
    if summary.get("more"):
        lines.append(f"<i>…and {int(summary['more'])} more target(s)</i>")
    return lines


def telegram_html(route: Mapping[str, Any], message: Mapping[str, Any]) -> str:
    """The styled message: headline, project/target/plan, one line per event, the avica run summary, the link.

    Lines that don't fit 4096 UTF-16 units are dropped from the end (each line is self-contained HTML).
    """
    title = " ".join(str(message.get("title") or "ALFRD").split()) or "ALFRD"
    head = [f"{_headline_icon(message)} <b>{_esc(_clip(title, 200))}</b>"]
    where = []
    if message.get("project_name"):
        where.append(f"📁 <b>{_esc(message['project_name'])}</b>")
    if message.get("target"):
        where.append(f"🎯 {_esc(message['target'])}")
    if where:
        head.append(" · ".join(where))
    if message.get("plan"):
        head.append(f"🧾 <code>{_esc(message['plan'])}</code>")
    head.append(RULE)
    events = message.get("events") or []
    body = [_event_line(e) for e in events] or [_esc(line) for line in str(message.get("body") or "").splitlines()]
    if message.get("skipped") and events:
        body.append(f"<i>⏭️ {int(message['skipped'])} older event(s) were not sent</i>")
    if message.get("dropped"):
        body.append(f"<i>🗑️ {int(message['dropped'])} event(s) were dropped (queue full)</i>")
    body += _summary_lines(message.get("summary") or {})
    link = _studio_url(route, message)
    tail = f'\n\n🔗 <a href="{_esc(link)}">Open in Studio</a>' if link and len(link) < 2000 else ""
    text = "\n".join(head)
    notice = "<i>✂️ Summary shortened to fit Telegram.</i>" if message.get("summary") else "<i>✂️ Message shortened to fit Telegram.</i>"
    room = TELEGRAM_LIMIT - _utf16_len(text) - _utf16_len(tail) - _utf16_len(notice) - 2
    kept: list[str] = []
    for index, line in enumerate(body):
        if _utf16_len(line) + 1 > room:
            kept.append(notice)
            break
        kept.append(line)
        room -= _utf16_len(line) + 1
    return text + ("\n" + "\n".join(kept) if kept else "") + tail


def telegram_settings() -> dict[str, Any]:
    """The Telegram plugin's saved settings (``{}`` when there are none)."""
    try:
        from alfrd.extensions import settings

        return settings.values("telegram")
    except Exception:  # noqa: BLE001 - a broken settings file is the same as none
        return {}


def _send_html(token: str, chat_id: Any, text: str, plain: str, timeout: float) -> None:
    """``sendMessage`` with HTML; when Telegram rejects the markup (HTTP 400) send ``plain`` instead."""
    params = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "link_preview_options": {"is_disabled": True}}
    try:
        telegram_call(token, "sendMessage", params, timeout)
        return
    except TelegramError as exc:
        if exc.status != 400:
            raise
    params.pop("parse_mode")
    telegram_call(token, "sendMessage", {**params, "text": _clip(plain, TELEGRAM_LIMIT)}, timeout)


# -- telegram digest (hold_minutes) -------------------------------------------
OUTBOX_KEEP = 24 * 3600.0  # a held notification older than this is dropped (e.g. the token stopped working)
OUTBOX_MAX = 500
DIGEST_GAP = "\n\n┄┄┄┄┄┄┄┄\n\n"


def hold_minutes(value: Any) -> float:
    """A ``hold_minutes`` setting as minutes ≥ 0 (empty, bad or negative → 0: send right away)."""
    if value in (None, "") or isinstance(value, bool):
        return 0.0
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def outbox_file(chat_id: Any) -> Path:
    """The chat's held notifications (JSON lines ``{at, html, plain}``)."""
    from alfrd.extensions import plugins_dir

    name = hashlib.sha256(str(chat_id).strip().encode()).hexdigest()[:12]
    return plugins_dir() / "telegram" / f"outbox-{name}.jsonl"


class _Locked:
    """An exclusive ``fcntl`` lock on ``<outbox>.lock`` while the outbox is read and rewritten (none on Windows)."""

    def __init__(self, path: Path) -> None:
        self.path = path.with_name(path.name + ".lock")

    def __enter__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = open(self.path, "a")  # closed in __exit__
        try:
            import fcntl

            fcntl.flock(self.handle, fcntl.LOCK_EX)
        except ImportError:
            pass

    def __exit__(self, *exc: object) -> None:
        self.handle.close()


def _read_outbox(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict) and isinstance(item.get("html"), str):
            out.append(item)
    return out


def _write_outbox(path: Path, items: list[dict[str, Any]]) -> None:
    if not items:
        path.unlink(missing_ok=True)
        return
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items), encoding="utf-8")
    os.replace(tmp, path)


def telegram_hold(chat_id: Any, text: str, plain: str, now: float | None = None) -> None:
    """Add one notification to the chat's digest outbox."""
    path = outbox_file(chat_id)
    with _Locked(path):
        items = _read_outbox(path)[-(OUTBOX_MAX - 1):]
        items.append({"at": time.time() if now is None else now, "html": text, "plain": plain})
        _write_outbox(path, items)


def _pack(texts: list[str], header: str) -> list[str]:
    """``texts`` joined into as few ≤ 4096-unit messages as possible; ``header`` leads the first."""
    out, current, empty = [], header, True
    for text in texts:
        joined = current + ("" if empty else DIGEST_GAP) + text
        if not empty and _utf16_len(joined) > TELEGRAM_LIMIT:
            out.append(current)
            current = text
        else:
            current = _clip(joined, TELEGRAM_LIMIT) if empty else joined
        empty = False
    if not empty:
        out.append(current)
    return out


def telegram_flush(token: str, chat_id: Any, hold_seconds: float, timeout: float = 10.0, *,
                   force: bool = False, now: float | None = None) -> int:
    """Send the chat's held notifications once the oldest is ``hold_seconds`` old; returns messages sent.

    One digest message, split only when it is longer than a Telegram message. On a send error
    the outbox is kept for the next flush (the runner's next notification, or the bot service).
    """
    path = outbox_file(chat_id)
    if not path.exists():
        return 0
    now = time.time() if now is None else now
    with _Locked(path):
        items = [i for i in _read_outbox(path) if now - float(i.get("at") or now) <= OUTBOX_KEEP]
        if not items or (not force and now - float(items[0].get("at") or now) < hold_seconds):
            _write_outbox(path, items)
            return 0
        count = len(items)
        header = f"🗂️ <b>ALFRD digest</b> · {count} notification{'' if count == 1 else 's'}\n{RULE}\n\n"
        parts = _pack([i["html"] for i in items], header)
        plains = _pack([str(i.get("plain") or "") for i in items], f"ALFRD digest · {count}\n\n")
        sent = 0
        try:
            for index, part in enumerate(parts):
                _send_html(token, chat_id, part, plains[min(index, len(plains) - 1)], timeout)
                sent += 1
        finally:
            # Parts don't map back to items one to one: keep everything unless the whole digest went out.
            _write_outbox(path, [] if sent == len(parts) else items)
        return sent


def telegram(route: Mapping[str, Any], message: Mapping[str, Any], timeout: float) -> None:
    """``sendMessage`` to ``chat_id`` with bot ``token``; 400/401/403/404 are skipped, 429/5xx retried.

    A route without ``token`` / ``chat_id`` uses the values saved in Settings → Plugins → Telegram;
    so do ``hold_minutes`` (collect notifications into a digest) and ``mute_when_active`` (send
    nothing while the Studio is in use, see :mod:`alfrd.presence`).
    """
    from alfrd import presence

    saved = telegram_settings()
    token = saved.get("token") if route.get("token") is None else route.get("token")
    chat_id = saved.get("chat_id") if route.get("chat_id") is None else route.get("chat_id")
    if not isinstance(token, str) or not token.strip():
        raise Skip("telegram needs token (in notify.json or Settings → Plugins → Telegram)")
    if isinstance(chat_id, bool) or not isinstance(chat_id, (str, int)) or not str(chat_id).strip():
        raise Skip("telegram needs chat_id (in notify.json or Settings → Plugins → Telegram)")
    muted = route.get("mute_when_active", saved.get("mute_when_active")) is True and presence.active()
    if muted:
        raise Skip("muted while you use ALFRD Studio (mute_when_active)")
    text, plain = telegram_html(route, message), telegram_text(route, message)
    hold = hold_minutes(route.get("hold_minutes", saved.get("hold_minutes")))
    if hold > 0:
        telegram_hold(chat_id, text, plain)
        try:
            telegram_flush(token.strip(), chat_id, hold * 60, timeout)
        except TelegramError:
            pass  # held: the next flush retries
        return
    try:
        _send_html(token.strip(), chat_id, text, plain, timeout)
    except TelegramError as exc:
        if exc.status in _TELEGRAM_SKIP:
            raise Skip(str(exc)) from None
        raise


SENDERS.update(desktop=desktop, webhook=webhook, command=command, telegram=telegram)

__all__ = ["TelegramError", "command", "desktop", "desktop_env", "is_loopback", "signature", "telegram",
           "hold_minutes", "outbox_file", "telegram_call", "telegram_flush", "telegram_hold", "telegram_html",
           "telegram_settings", "telegram_text", "webhook"]
