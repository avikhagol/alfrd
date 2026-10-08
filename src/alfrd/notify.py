"""Notification delivery inside the runner (plan events → routes → notifiers).

The runner starts one :class:`Dispatcher` per plan. :meth:`Runner.event` feeds it every
structured event; a daemon thread batches events (20 s per plan), matches them against
the routes (project ``alfrd.yaml notify.routes`` first, then the user's
``user_config_dir("alfrd")/notify.json``) and calls the notifier for each route with a
timeout and retries. Nothing here raises into the runner: failures go to runner.log.

``.alfrd/plans/<id>/notify.cursor`` holds the last delivered ``seq``. A re-adopting
runner resumes after it, so nothing is sent twice; events older than one hour are
replaced by one summary message.
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urlencode

from alfrd.events import EVENTS_FILE, EventLog, kind_matches

CURSOR_FILE = "notify.cursor"
USER_FILE = "notify.json"
KNOWN_VIA = frozenset({"desktop", "webhook", "command"})
USER_ONLY_VIA = frozenset({"webhook", "command"})  # may run code or carry secrets: never from alfrd.yaml
BATCH_WINDOW = 20.0
MAX_AGE = 3600.0
TIMEOUT = 10.0
RETRIES = 3
BACKOFF = (1.0, 2.0, 4.0)
QUEUE_SIZE = 1000

# ``send(route, message, timeout)``; raise to retry. The built-in notifiers register here (N4).
Sender = Callable[[Mapping[str, Any], Mapping[str, Any], float], None]
SENDERS: dict[str, Sender] = {}

_PHRASES = {
    "plan.started": ("plan started", "plans started"), "plan.finished": ("plan finished", "plans finished"),
    "plan.failed": ("plan failed", "plans failed"), "plan.cancelled": ("plan cancelled", "plans cancelled"),
    "plan.interrupted": ("plan interrupted", "plans interrupted"),
    "turn.started": ("turn started", "turns started"), "turn.finished": ("turn finished", "turns finished"),
    "turn.failed": ("turn failed", "turns failed"), "turn.retrying": ("turn retrying", "turns retrying"),
    "turn.fallback_model": ("turn fell back to another model", "turns fell back to another model"),
    "turn.idle": ("turn idle", "turns idle"),
    "review.pending": ("review pending", "reviews pending"), "review.approved": ("review approved", "reviews approved"),
    "review.rejected": ("review rejected", "reviews rejected"),
    "handoff.published": ("handoff published", "handoffs published"),
    "limit.reached": ("limit reached", "limits reached"),
}


class RouteError(ValueError):
    """A ``notify.routes`` entry that doesn't fit the schema."""


# -- routes -------------------------------------------------------------------
def validate_routes(routes: Any, *, source: str = "notify.routes", user: bool = False) -> list[dict[str, Any]]:
    """Check the schema of a route list; returns normalised copies (``on`` always a list).

    An unknown ``via`` is allowed here (it may come from a plugin later) and reported by
    :func:`route_warnings`; ``webhook`` and ``command`` are refused outside the user file.
    """
    if routes is None:
        return []
    if not isinstance(routes, list):
        raise RouteError(f"{source} must be a list of routes")
    out = []
    for i, route in enumerate(routes):
        where = f"{source}[{i}]"
        if not isinstance(route, Mapping):
            raise RouteError(f"{where} must be a mapping with via and on")
        via = route.get("via")
        if not isinstance(via, str) or not via.strip():
            raise RouteError(f"{where}.via must be a nonempty string")
        on = route.get("on")
        on = [on] if isinstance(on, str) else on
        if not isinstance(on, list) or not on or not all(isinstance(k, str) and k.strip() for k in on):
            raise RouteError(f"{where}.on must be an event kind, a prefix like plan.*, or a list of them")
        if via.strip() in USER_ONLY_VIA and not user:
            raise RouteError(f"{where}: via {via.strip()} is only allowed in the user's {USER_FILE}")
        out.append({**route, "via": via.strip(), "on": [k.strip() for k in on]})
    return out


def route_warnings(routes: Iterable[Mapping[str, Any]]) -> list[str]:
    """Warnings for runner.log and Diagnostics: unknown notifiers and patterns that match no kind."""
    from alfrd.events import EVENT_KINDS

    warnings = []
    for route in routes:
        if route["via"] not in KNOWN_VIA:
            warnings.append(f"notify route via {route['via']!r} is unknown; its events are not delivered")
        for pattern in route["on"]:
            if not any(kind_matches(pattern, k) for k in EVENT_KINDS):
                warnings.append(f"notify route via {route['via']}: {pattern!r} matches no event kind")
    return warnings


def user_config_file() -> Path:
    from platformdirs import user_config_dir

    return Path(user_config_dir("alfrd")) / USER_FILE  # resolved per call (XDG_CONFIG_HOME may change)


def user_routes(path: Path | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    """Routes from the user's ``notify.json`` (``{"routes": [...]}``); a broken file is a warning, not an error."""
    path = path or user_config_file()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [], []
    except (OSError, ValueError) as exc:
        return [], [f"cannot read {path}: {exc}"]
    try:
        if not isinstance(data, Mapping):
            raise RouteError(f"{path} must hold a JSON object with routes")
        return validate_routes(data.get("routes"), source=f"{path}: routes", user=True), []
    except RouteError as exc:
        return [], [str(exc)]


def load_routes(project_routes: Any, user_file: Path | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    """Project routes first, then user routes; plus the warnings for both."""
    warnings: list[str] = []
    try:
        routes = validate_routes(project_routes)
    except RouteError as exc:
        routes, warnings = [], [str(exc)]
    more, problems = user_routes(user_file)
    routes = routes + more
    return routes, warnings + problems + route_warnings(routes)


def route_matches(route: Mapping[str, Any], event: Mapping[str, Any]) -> bool:
    kind = str(event.get("kind") or "")
    if kind == "turn.failed" and event.get("unit") is None and (event.get("data") or {}).get("stop_plan"):
        return False  # the stop line repeats the unit's own turn.failed
    return any(kind_matches(p, kind) for p in route["on"])


# -- messages -----------------------------------------------------------------
def studio_link(project: Any, plan: Any, unit: Any = None) -> str:
    """The Studio deep link (decision D5)."""
    query = {"project": project or "", "plan": plan or ""}
    if unit:
        query["unit"] = unit
    return "/studio/#/workflow?" + urlencode(query)


def _status(event: Mapping[str, Any]) -> str:
    data = event.get("data") or {}
    return str(data.get("status") or str(event.get("kind") or "").split(".", 1)[-1])


def _brief(event: Mapping[str, Any]) -> dict[str, Any]:
    """The fields a notification may carry. Never ``text`` or ``data`` (handoffs stay out of Part A)."""
    return {
        "seq": event.get("seq"), "at": event.get("at"), "kind": event.get("kind"),
        "unit": event.get("unit"), "turn": event.get("turn"), "turns": event.get("turns"),
        "agent": event.get("agent"), "status": _status(event),
        "link": studio_link(event.get("project"), event.get("plan"), event.get("unit")),
    }


def _phrase(kind: str, count: int) -> str:
    one, many = _PHRASES.get(kind, (kind, kind))
    return one if count == 1 else f"{count} {many}"


def build_message(events: list[Mapping[str, Any]], *, skipped: int = 0, dropped: int = 0) -> dict[str, Any]:
    """One notification for a batch of a plan's events (``"3 turns failed in t-x"``)."""
    first = events[0]
    target = next((e.get("target") for e in events if e.get("target")), None)
    counts: dict[str, int] = {}
    for event in events:
        counts[str(event.get("kind"))] = counts.get(str(event.get("kind")), 0) + 1
    title = ", ".join(_phrase(k, n) for k, n in counts.items())
    if target:
        title += f" in {target}"
    lines = []
    for event in events:
        part = " ".join(str(x) for x in (
            _phrase(str(event.get("kind")), 1),
            event.get("unit") or "",
            f"turn {event['turn']}/{event.get('turns') or '?'}" if event.get("turn") else "",
            f"({event['agent']})" if event.get("agent") else "",
        ) if x)
        lines.append(part)
    if skipped:
        lines.append(f"{skipped} older event(s) were not sent")
    if dropped:
        lines.append(f"{dropped} event(s) were dropped (queue full)")
    units = {e.get("unit") for e in events}
    unit = units.pop() if len(units) == 1 else None
    return {
        "schema": "alfrd.notification/1", "project": first.get("project"), "plan": first.get("plan"),
        "target": target, "title": title, "body": "\n".join(lines), "kinds": list(counts),
        "count": len(events), "skipped": skipped, "dropped": dropped,
        "link": studio_link(first.get("project"), first.get("plan"), unit), "events": [_brief(e) for e in events],
    }


def stale_summary(events: list[Mapping[str, Any]]) -> dict[str, Any]:
    """The one message that replaces events older than :data:`MAX_AGE`."""
    message = build_message(events)
    message.update(title=f"{len(events)} earlier event(s) not sent"
                   + (f" in {message['target']}" if message["target"] else ""),
                   body=message["title"], skipped=len(events), events=[])
    return message


def _age(event: Mapping[str, Any], now: float) -> float:
    try:
        return now - datetime.fromisoformat(str(event.get("at"))).timestamp()
    except ValueError:
        return 0.0


# -- cursor -------------------------------------------------------------------
def read_cursor(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def write_cursor(path: Path, seq: int) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(f"{seq}\n", encoding="utf-8")
    os.replace(tmp, path)


# -- dispatcher ---------------------------------------------------------------
class Dispatcher:
    """Batches a plan's events and delivers them on a daemon thread; never raises into the caller."""

    def __init__(self, plan_dir: str | Path, routes: list[dict[str, Any]], *, log: Callable[[str], None] = print,
                 senders: Mapping[str, Sender] | None = None, batch_window: float = BATCH_WINDOW,
                 max_age: float = MAX_AGE, timeout: float = TIMEOUT, retries: int = RETRIES,
                 backoff: Iterable[float] = BACKOFF, queue_size: int = QUEUE_SIZE) -> None:
        self.plan_dir = Path(plan_dir)
        self.routes = routes
        self.log = log
        self.senders = SENDERS if senders is None else senders
        self.batch_window = batch_window
        self.max_age = max_age
        self.timeout = timeout
        self.retries = retries
        self.backoff = tuple(backoff) or (0.0,)
        self.queue: deque[dict[str, Any]] = deque()
        self.queue_size = queue_size
        self.dropped = 0
        self.cond = threading.Condition()
        self.stopping = False
        self.thread: threading.Thread | None = None
        self.cursor_file = self.plan_dir / CURSOR_FILE
        self.cursor = 0
        self.queued = 0  # highest seq accepted, so the backlog and live events never overlap
        self.warned: set[str] = set()
        self.stale: list[dict[str, Any]] = []  # backlog too old to send one by one (delivered as one summary)

    # -- feeding --
    def start(self) -> "Dispatcher":
        """Resume after the cursor (a first run starts at the end of events.jsonl) and start the thread."""
        log = EventLog(self.plan_dir / EVENTS_FILE)
        cursor = read_cursor(self.cursor_file)
        if cursor is None:
            cursor = log.last_seq()
            self._save_cursor(cursor)
        self.cursor = self.queued = cursor
        backlog, now = [], time.time()
        while True:
            chunk = log.read_since(self.queued)
            if not chunk:
                break
            backlog += chunk
            self.queued = chunk[-1]["seq"]
        self.stale = [e for e in backlog if _age(e, now) > self.max_age]
        if self.stale:
            self.log(f"notify: {len(self.stale)} event(s) older than {self.max_age:.0f} s are summarised, not sent")
        for event in backlog:
            if _age(event, now) <= self.max_age:
                self._push(event)
        self.thread = threading.Thread(target=self._loop, name=f"alfrd-notify-{self.plan_dir.name}", daemon=True)
        self.thread.start()
        return self

    def submit(self, event: Mapping[str, Any]) -> None:
        try:
            seq = event.get("seq")
            if isinstance(seq, int):
                if seq <= self.queued:
                    return
                self.queued = seq
            self._push(dict(event))
        except Exception as exc:  # noqa: BLE001 - notifications are best effort
            self.log(f"notify: cannot queue event: {exc}")

    def _push(self, event: dict[str, Any]) -> None:
        with self.cond:
            if len(self.queue) >= self.queue_size:
                self.queue.popleft()
                self.dropped += 1
            self.queue.append(event)
            self.cond.notify()

    def stop(self, deadline: float = 15.0) -> None:
        """Flush what is queued (no batching wait) and give the thread up to ``deadline`` seconds."""
        with self.cond:
            self.stopping = True
            self.cond.notify()
        if self.thread is not None:
            self.thread.join(max(0.0, deadline))
            if self.thread.is_alive():
                self.log("notify: delivery still running at stop; undelivered events are retried by the next runner")

    # -- delivery thread --
    def _loop(self) -> None:
        if self.stale:
            try:
                self._deliver_batch(self.stale, summary=True)
            except Exception as exc:  # noqa: BLE001
                self.log(f"notify: delivery failed: {exc}")
            self.stale = []
        while True:
            with self.cond:
                while not self.queue and not self.stopping:
                    self.cond.wait()
                if not self.queue and self.stopping:
                    return
                end = time.monotonic() + self.batch_window
                while not self.stopping:
                    left = end - time.monotonic()
                    if left <= 0:
                        break
                    self.cond.wait(left)
                batch, dropped = list(self.queue), self.dropped
                self.queue.clear()
                self.dropped = 0
            try:
                self._deliver_batch(batch, dropped=dropped)
            except Exception as exc:  # noqa: BLE001
                self.log(f"notify: delivery failed: {exc}")

    def _deliver_batch(self, events: list[dict[str, Any]], *, summary: bool = False, dropped: int = 0) -> None:
        if dropped:
            self.log(f"notify: queue full, {dropped} event(s) dropped")
        for route in self.routes:
            matched = [e for e in events if route_matches(route, e)]
            if not matched:
                continue
            message = stale_summary(matched) if summary else build_message(matched, dropped=dropped)
            self._send(route, message)
        seqs = [e["seq"] for e in events if isinstance(e.get("seq"), int)]
        if seqs and max(seqs) > self.cursor:
            self._save_cursor(max(seqs))

    def _send(self, route: Mapping[str, Any], message: Mapping[str, Any]) -> bool:
        via = route["via"]
        sender = self.senders.get(via)
        if sender is None:
            if via not in self.warned:
                self.warned.add(via)
                self.log(f"notify: no notifier {via!r}; its events are not delivered")
            return False
        for attempt in range(self.retries + 1):
            error = _call_with_timeout(sender, (route, message, self.timeout), self.timeout)
            if error is None:
                return True
            if attempt < self.retries:
                self.log(f"notify: {via} failed ({error}); retry {attempt + 1}/{self.retries}")
                time.sleep(self.backoff[min(attempt, len(self.backoff) - 1)])
        self.log(f"notify: {via} gave up after {self.retries + 1} attempts: {message.get('title')}")
        return False

    def _save_cursor(self, seq: int) -> None:
        self.cursor = seq
        try:
            write_cursor(self.cursor_file, seq)
        except OSError as exc:
            self.log(f"notify: cannot write {self.cursor_file.name}: {exc}")


def _call_with_timeout(func: Callable[..., Any], args: tuple, timeout: float) -> str | None:
    """Run ``func(*args)`` on a helper thread; returns an error text, or None on success.

    A sender that hangs past ``timeout`` is abandoned (daemon thread) so it can't hold up delivery.
    """
    result: dict[str, Any] = {}

    def run() -> None:
        try:
            func(*args)
            result["ok"] = True
        except Exception as exc:  # noqa: BLE001
            result["error"] = f"{type(exc).__name__}: {exc}"

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        return f"timed out after {timeout:g} s"
    return None if result.get("ok") else result.get("error", "unknown error")


def start_for(plan_dir: Path, project_routes: Any, *, log: Callable[[str], None]) -> Dispatcher | None:
    """The runner's dispatcher, or None when no route is configured. Never raises."""
    try:
        routes, warnings = load_routes(project_routes)
        for warning in warnings:
            log(f"warning: {warning}")
        if not routes:
            return None
        return Dispatcher(plan_dir, routes, log=log, batch_window=BATCH_WINDOW, max_age=MAX_AGE, timeout=TIMEOUT,
                          retries=RETRIES, backoff=BACKOFF).start()
    except Exception as exc:  # noqa: BLE001
        log(f"warning: notifications disabled: {exc}")
        return None


__all__ = [
    "Dispatcher", "KNOWN_VIA", "RouteError", "SENDERS", "build_message", "load_routes", "route_matches",
    "route_warnings", "start_for", "studio_link", "user_routes", "validate_routes",
]
