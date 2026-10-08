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
GRACE = 0.5  # extra wait for a sender that enforces its own timeout
STOP_MARGIN = 0.25  # a stopping dispatcher gives up this long before its deadline, so the cursor is saved in time

# ``send(route, message, timeout)``; raise to retry, raise :class:`Skip` when retrying can't help.
# The built-in notifiers (:mod:`alfrd.notifiers`) register here when this module is imported.
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


class Skip(Exception):
    """Raised by a sender that can't deliver here (no desktop session, http to a remote host…); never retried."""


# -- routes -------------------------------------------------------------------
def yaml_on_key(route: Any) -> Any:
    """``route`` with a ``True`` key renamed to ``on``: YAML 1.1 reads a bare ``on:`` key as true."""
    if isinstance(route, Mapping) and "on" not in route and True in route:
        return {("on" if k is True else k): v for k, v in route.items()}
    return route


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
        route = yaml_on_key(route)
        on = route.get("on")
        on = [on] if isinstance(on, str) else on
        if not isinstance(on, list) or not on or not all(isinstance(k, str) and k.strip() for k in on):
            raise RouteError(f"{where}.on must be an event kind, a prefix like plan.*, or a list of them")
        if via.strip() in USER_ONLY_VIA and not user:
            raise RouteError(f"{where}: via {via.strip()} is only allowed in the user's {USER_FILE}")
        _check_options(via.strip(), route, where)
        out.append({**route, "via": via.strip(), "on": [k.strip() for k in on]})
    return out


def _check_options(via: str, route: Mapping[str, Any], where: str) -> None:
    """The options of the built-in notifiers (other keys are left to the notifier)."""
    if via == "webhook":
        if not isinstance(route.get("url"), str) or not route["url"].strip():
            raise RouteError(f"{where}.url must be a nonempty string (https://…)")
        if "secret" in route and not isinstance(route["secret"], str):
            raise RouteError(f"{where}.secret must be a string")
        headers = route.get("headers")
        if headers is not None and not (isinstance(headers, Mapping)
                                        and all(isinstance(k, str) and isinstance(v, str) for k, v in headers.items())):
            raise RouteError(f"{where}.headers must map header names to strings")
    elif via == "command":
        argv = route.get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(a, str) and a for a in argv):
            raise RouteError(f"{where}.argv must be a nonempty list of strings (no shell)")


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
def project_ref(root: Any) -> dict[str, Any]:
    """``{"root", "name", "identifier"}`` of a project; the identifier is the Studio's project key (R1).

    Falls back to the root path when the manifest can't be read, so a link is always built.
    """
    try:
        from alfrd.api.status import project_info

        return project_info(root)
    except Exception:  # noqa: BLE001
        return {"root": str(root or ""), "name": Path(str(root or "")).name or None, "identifier": str(root or "")}


def studio_link(project: Any, plan: Any, unit: Any = None) -> str:
    """The Studio deep link (decision D5)."""
    query = {"project": project or "", "plan": plan or ""}
    if unit:
        query["unit"] = unit
    return "/studio/#/workflow?" + urlencode(query)


def _status(event: Mapping[str, Any]) -> str:
    data = event.get("data") or {}
    return str(data.get("status") or str(event.get("kind") or "").split(".", 1)[-1])


def _brief(event: Mapping[str, Any], key: Any = None) -> dict[str, Any]:
    """The fields a notification may carry. Never ``text`` or ``data`` (handoffs stay out of Part A)."""
    return {
        "seq": event.get("seq"), "at": event.get("at"), "kind": event.get("kind"),
        "unit": event.get("unit"), "turn": event.get("turn"), "turns": event.get("turns"),
        "agent": event.get("agent"), "status": _status(event),
        "link": studio_link(key or event.get("project"), event.get("plan"), event.get("unit")),
    }


def _phrase(kind: str, count: int) -> str:
    one, many = _PHRASES.get(kind, (kind, kind))
    return one if count == 1 else f"{count} {many}"


def build_message(events: list[Mapping[str, Any]], *, skipped: int = 0, dropped: int = 0,
                  project: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """One notification for a batch of a plan's events (``"3 turns failed in t-x"``).

    ``project`` is :func:`project_ref` of the plan's root; links use its identifier.
    """
    first = events[0]
    key = (project or {}).get("identifier")
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
        "schema": "alfrd.notification/1", "project": first.get("project"),
        "project_name": (project or {}).get("name"), "plan": first.get("plan"), "target": target, "title": title, "body": "\n".join(lines), "kinds": list(counts),
        "count": len(events), "skipped": skipped, "dropped": dropped,
        "link": studio_link(key or first.get("project"), first.get("plan"), unit),
        "events": [_brief(e, key) for e in events],
    }


def stale_summary(events: list[Mapping[str, Any]], project: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The one message that replaces events older than :data:`MAX_AGE`."""
    message = build_message(events, project=project)
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
                 backoff: Iterable[float] = BACKOFF, queue_size: int = QUEUE_SIZE,
                 project: Mapping[str, Any] | None = None) -> None:
        self.plan_dir = Path(plan_dir)
        self.project = dict(project) if project is not None else project_ref(_root_of(self.plan_dir))
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
        self.deadline = float("inf")  # monotonic time stop() waits until; retries are cut to fit it
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
            self.deadline = time.monotonic() + max(0.0, deadline)
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
        workers = []
        for index, route in enumerate(self.routes):
            matched = [e for e in events if route_matches(route, e)]
            if not matched:
                continue
            message = (stale_summary(matched, self.project) if summary
                       else build_message(matched, dropped=dropped, project=self.project))
            # one thread per route: a slow webhook must not hold up the desktop notice (R2)
            worker = threading.Thread(target=self._send, args=(route, message, index), daemon=True,
                                      name=f"alfrd-notify-{route['via']}")
            worker.start()
            workers.append(worker)
        for worker in workers:
            worker.join()  # bounded: every attempt has a timeout and stop() cuts the retries
        seqs = [e["seq"] for e in events if isinstance(e.get("seq"), int)]
        if seqs and max(seqs) > self.cursor:
            self._save_cursor(max(seqs))

    def _send(self, route: Mapping[str, Any], message: Mapping[str, Any], index: int = 0) -> bool:
        via = route["via"]
        sender = self.senders.get(via)
        if sender is None:
            if via not in self.warned:
                self.warned.add(via)
                self.log(f"notify: no notifier {via!r}; its events are not delivered")
            return False
        attempts = 0
        cutoff = lambda: self.deadline - STOP_MARGIN  # noqa: E731 - moves when stop() is called
        for attempt in range(self.retries + 1):
            left = cutoff() - time.monotonic()
            if left <= 0:
                break
            attempts += 1
            timeout = min(self.timeout, left)
            error, skipped = _call_with_timeout(sender, (route, message, timeout), timeout, cutoff)
            if error is None:
                return True
            if skipped:
                if f"skip:{index}" not in self.warned:
                    self.warned.add(f"skip:{index}")
                    self.log(f"warning: notify {via}: {error}; skipped")
                return False
            if attempt < self.retries:
                pause = self.backoff[min(attempt, len(self.backoff) - 1)]
                if time.monotonic() + pause >= cutoff():
                    break
                self.log(f"notify: {via} failed ({error}); retry {attempt + 1}/{self.retries}")
                time.sleep(pause)
        self.log(f"notify: {via} gave up after {attempts} attempt{'' if attempts == 1 else 's'}: {message.get('title')}")
        return False

    def _save_cursor(self, seq: int) -> None:
        self.cursor = seq
        try:
            write_cursor(self.cursor_file, seq)
        except OSError as exc:
            self.log(f"notify: cannot write {self.cursor_file.name}: {exc}")


def _root_of(plan_dir: Path) -> Path:
    """``<root>/.alfrd/plans/<id>`` → ``<root>``."""
    return plan_dir.parents[2] if len(plan_dir.parents) > 2 else plan_dir


def _call_with_timeout(func: Callable[..., Any], args: tuple, timeout: float,
                       cutoff: Callable[[], float] = lambda: float("inf")) -> tuple[str | None, bool]:
    """Run ``func(*args)`` on a helper thread; returns ``(error text or None, skipped)``.

    A sender that hangs past ``timeout`` (or past the monotonic ``cutoff()``, which a stopping
    dispatcher moves closer) is abandoned (daemon thread) so it can't hold up delivery.
    The sender enforces ``timeout`` itself, so it gets a short grace to report its own error.
    """
    result: dict[str, Any] = {}

    def run() -> None:
        try:
            func(*args)
            result["ok"] = True
        except Skip as exc:
            result["error"], result["skip"] = str(exc) or "skipped", True
        except Exception as exc:  # noqa: BLE001
            result["error"] = f"{type(exc).__name__}: {exc}"

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    end = time.monotonic() + timeout + GRACE
    while worker.is_alive():
        left = min(end, cutoff()) - time.monotonic()
        if left <= 0:
            break
        worker.join(min(left, 0.1))
    if worker.is_alive():
        return f"timed out after {timeout:g} s", False
    if result.get("ok"):
        return None, False
    return result.get("error", "unknown error"), bool(result.get("skip"))


def start_for(plan_dir: Path, project_routes: Any, *, log: Callable[[str], None],
              root: Path | None = None) -> Dispatcher | None:
    """The runner's dispatcher, or None when no route is configured. Never raises."""
    try:
        routes, warnings = load_routes(project_routes)
        for warning in warnings:
            log(f"warning: {warning}")
        if not routes:
            return None
        return Dispatcher(plan_dir, routes, log=log, batch_window=BATCH_WINDOW, max_age=MAX_AGE, timeout=TIMEOUT,
                          retries=RETRIES, backoff=BACKOFF,
                          project=project_ref(root if root is not None else _root_of(Path(plan_dir)))).start()
    except Exception as exc:  # noqa: BLE001
        log(f"warning: notifications disabled: {exc}")
        return None


__all__ = [
    "Dispatcher", "KNOWN_VIA", "RouteError", "SENDERS", "Skip", "build_message", "load_routes", "project_ref",
    "route_matches", "route_warnings", "start_for", "studio_link", "user_routes", "validate_routes",
]

from alfrd import notifiers as _notifiers  # noqa: E402,F401 - registers desktop, webhook, command in SENDERS
