"""Live updates for the Studio: cheap change detection on a project tree.

A :class:`ProjectWatcher` polls the files the Studio reads for one ALFRD
project (the same selection as :func:`alfrd.avica_layout.collect_studio_files`,
stat only: no file is opened) and turns differences into small events:

* ``changed`` / ``removed``: files whose content the Studio shows (alfrd.yaml,
  avica.meta, result CSVs, templates …) or log files that appeared/vanished.
  The Studio re-reads only those (``/scan?only=``).
* ``logs``: declared logs that grew (``{rel: [size, mtime]}``). The Studio
  updates sizes in place and nudges any open log tail.

Polling (not inotify) is deliberate: AVICA often writes from cluster nodes
onto NFS/sshfs mounts, where inotify sees nothing, and a recursive inotify
watch over measurement sets costs far more than stat-ing the few hundred
declared files. The interval adapts: ``interval`` while things change,
``idle`` after two quiet minutes, and never less than 20x the time a pass took,
so a huge tree slows itself down. Watchers only run while somebody listens
(an open event stream, or a poll within the last ``lease`` seconds).

:class:`LiveHub` owns the watchers of one server plus a watcher for the
runtime database (``runtime`` events), and fans events out to subscribers.
Nothing here imports Flask.
"""

from __future__ import annotations

import os
import queue
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

Snapshot = dict[str, tuple]

HOT_WINDOW = 120.0  # seconds after the last change (or the start) during which the fast interval is used


def tree_snapshot(root: str | Path) -> Snapshot:
    """``{rel: (kind, size, mtime_ns)}`` for every file the Studio reads; stat only.

    ``kind`` is ``"content"`` (the Studio shows the text), ``"log"`` (listed,
    read when opened) or ``"marker"`` (a band folder).
    """
    from alfrd.avica_layout import collect_studio_files

    data = collect_studio_files(root, log_tail=0, read=False)
    out: Snapshot = {}
    for f in data["files"]:
        if f.get("marker"):
            out[f["rel"]] = ("marker", 0, 0)
        else:
            kind = "content" if f.get("content") else "log" if f.get("log") else "other"
            out[f["rel"]] = (kind, f.get("size", 0), int(float(f.get("mtime") or 0) * 1e6))
    return out


def diff_snapshots(old: Snapshot, new: Snapshot) -> dict[str, Any]:
    """Split the difference into what needs a re-read and what is log growth."""
    changed: list[str] = []
    logs: dict[str, list[float]] = {}
    for rel, value in new.items():
        before = old.get(rel)
        if before is None:
            changed.append(rel)
        elif before != value:
            if value[0] == "log" and before[0] == "log":
                logs[rel] = [value[1], value[2] / 1e6]
            else:
                changed.append(rel)
    removed = [rel for rel in old if rel not in new]
    return {"changed": sorted(changed), "removed": sorted(removed), "logs": logs}


def file_signature(paths: Iterable[str | Path]) -> tuple:
    sig = []
    for p in paths:
        try:
            st = os.stat(p)
            sig.append((st.st_size, st.st_mtime_ns))
        except OSError:
            sig.append(None)
    return tuple(sig)


class Watcher:
    """Polls ``probe()`` in a thread and publishes differences as events."""

    kind = "tree"

    def __init__(self, key: str, publish: Callable[[dict[str, Any]], None], interval: float = 2.0,
                 idle: float = 5.0, max_interval: float = 60.0, history: int = 200) -> None:
        self.key = key
        self.publish = publish
        self.interval = max(0.2, float(interval))
        self.idle = max(self.interval, float(idle))
        self.max_interval = max(self.idle, float(max_interval))
        self.epoch = f"{int(time.time() * 1000):x}"
        self.version = 0
        self.baseline_ts = 0.0
        self.last_change = time.time()  # start "busy": somebody just opened the Studio
        self.last_pass = 0.0
        self.pass_seconds = 0.0
        self.error: str | None = None
        self.events: deque[dict[str, Any]] = deque(maxlen=history)
        self._snapshot: Any = None
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None

    # -- subclass API -------------------------------------------------------
    def probe(self) -> Any:  # pragma: no cover - abstract
        raise NotImplementedError

    def compare(self, old: Any, new: Any) -> dict[str, Any] | None:
        return None if old == new else {}

    # -- lifecycle ----------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"alfrd-live-{self.key}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and not self._stop.is_set())

    def wait_ready(self, timeout: float = 10.0) -> bool:
        return self._ready.wait(timeout)

    def poke(self) -> None:
        """Check now (e.g. the Studio just wrote a file)."""
        self._wake.set()

    def next_delay(self, now: float | None = None) -> float:
        now = time.time() if now is None else now
        base = self.interval if now - self.last_change < HOT_WINDOW else self.idle
        return min(self.max_interval, max(base, 20.0 * self.pass_seconds))

    def state(self) -> dict[str, Any]:
        return {"epoch": self.epoch, "version": self.version, "baseline_ts": self.baseline_ts,
                "interval": round(self.next_delay(), 2), "pass_ms": round(self.pass_seconds * 1000, 1),
                "error": self.error}

    def since(self, epoch: str | None, version: int) -> tuple[list[dict[str, Any]], bool]:
        """Events after ``version``; ``reset`` when they are no longer all available."""
        with self._lock:
            if epoch != self.epoch:
                return [], True
            events = [e for e in self.events if e["version"] > version]
            complete = version >= self.version or (events and events[0]["version"] == version + 1)
            return events, not complete

    # -- loop ---------------------------------------------------------------
    def check(self) -> dict[str, Any] | None:
        """One pass: probe, compare with the previous pass, publish a change."""
        t0 = time.perf_counter()
        try:
            snap = self.probe()
            self.error = None
        except Exception as error:  # a half-written alfrd.yaml, a vanished folder …
            self.error = f"{type(error).__name__}: {error}"
            self.pass_seconds = time.perf_counter() - t0
            return None
        self.pass_seconds = time.perf_counter() - t0
        self.last_pass = time.time()
        if self._snapshot is None:
            self._snapshot = snap
            self.baseline_ts = self.last_pass - self.pass_seconds
            self._ready.set()
            return None
        diff = self.compare(self._snapshot, snap)
        self._snapshot = snap
        if diff is None:
            return None
        with self._lock:
            self.version += 1
            self.last_change = self.last_pass
            event = {"type": self.kind, "key": self.key, "epoch": self.epoch, "version": self.version, **diff}
            self.events.append(event)
            # Companion event watchers share a cursor; publish under the cursor lock
            # so subscribers always receive versions in the same order as history.
            self.publish(event)
        return event

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.check()
            except Exception as error:  # never let the thread die silently
                self.error = f"{type(error).__name__}: {error}"
            self._ready.set()
            self._wake.wait(self.next_delay())
            self._wake.clear()


class TreeWatcher(Watcher):
    kind = "tree"

    def __init__(self, key: str, root: str | Path, publish, **kw) -> None:
        super().__init__(key, publish, **kw)
        self.root = Path(root)

    def probe(self) -> Snapshot:
        snap = tree_snapshot(self.root)
        if self._snapshot is None:  # first pass: edits made while nobody watched become versions
            self._history([rel for rel, value in snap.items() if value[0] == "content"])
        return snap

    def _history(self, rels) -> None:
        """Tracked config files (alfrd.yaml …) that changed get a version (alfrd.history, source external)."""
        try:
            from alfrd.history import capture_external

            capture_external(self.root, rels)
        except Exception:  # noqa: BLE001 - history must never break live updates
            pass

    def compare(self, old: Snapshot, new: Snapshot) -> dict[str, Any] | None:
        diff = diff_snapshots(old, new)
        if not (diff["changed"] or diff["removed"] or diff["logs"]):
            return None
        if diff["changed"]:
            self._history(diff["changed"])
        try:  # keep a built full-text index current (alfrd.search)
            from alfrd.search import on_tree_event

            on_tree_event(self.root, self.key, diff)
        except Exception:  # noqa: BLE001
            pass
        return {"project": self.key, **diff}


class AlfrdWatcher(Watcher):
    """Tail plan events by byte offset; initial contents are a baseline, never replayed."""

    kind = "alfrd"

    def __init__(self, key, root, publish, **kw):
        super().__init__(key, publish, **kw)
        self.root = Path(root)
        self._tails = {}
        self._ref = None

    def _read(self, path, offset):
        import json
        records = []
        with path.open("rb") as stream:
            stream.seek(offset)
            while True:
                start = stream.tell()
                line = stream.readline()
                if not line or not line.endswith(b"\n"):
                    return records, start
                try:
                    record = json.loads(line)
                    if isinstance(record, dict):
                        records.append(record)
                except (ValueError, UnicodeDecodeError):
                    pass

    def probe(self):
        from alfrd import notify
        from alfrd.runtime import scheduler
        if self._ref is None:
            self._ref = notify.project_ref(self.root)
        plans = scheduler.list_plans(self.root)
        active = {p["id"] for p in plans if p.get("status") in scheduler.ACTIVE}
        out = []
        for plan in plans:
            ident = plan["id"]
            if ident not in active and ident not in self._tails:
                continue
            path = self.root / ".alfrd" / "plans" / ident / "events.jsonl"
            state = self._tails.get(ident)
            try:
                stat = path.stat()
                inode = (stat.st_dev, stat.st_ino)
                if state is None and self._snapshot is None:
                    # Only complete lines form the initial baseline.
                    records, offset = self._read(path, 0)
                    seq = max((r.get("seq", 0) for r in records if type(r.get("seq")) is int), default=0)
                    self._tails[ident] = (inode, offset, seq)
                    continue
                old_inode, offset, seq = state or (inode, 0, 0)
                records = []
                if old_inode != inode:
                    rotated = path.with_name(path.name + ".1")
                    try:
                        st = rotated.stat()
                        if (st.st_dev, st.st_ino) == old_inode:
                            records, _ = self._read(rotated, offset)
                    except OSError:
                        pass
                    offset = 0
                elif stat.st_size < offset:
                    offset = 0
                more, offset = self._read(path, offset)
                for record in records + more:
                    number = record.get("seq")
                    if type(number) is not int or number <= seq:
                        continue
                    seq = number
                    brief = notify._brief(record, self._ref["identifier"])
                    brief.update(plan=ident, target=record.get("target"), project=self.key)
                    out.append(brief)
                self._tails[ident] = (inode, offset, seq)
            except OSError:
                continue
        # Keep finished tails: a runner may flush plan.finished after changing its status.
        existing = {p["id"] for p in plans}
        self._tails = {k: v for k, v in self._tails.items() if k in existing}
        return out

    def compare(self, old, new):
        return {"events": new} if new else None


class FileWatcher(Watcher):
    """Size/mtime of a few files, e.g. the runtime SQLite database and its WAL."""

    kind = "runtime"

    def __init__(self, key: str, paths: Iterable[str | Path], publish, **kw) -> None:
        super().__init__(key, publish, **kw)
        self.paths = [str(p) for p in paths]

    def probe(self) -> tuple:
        return file_signature(self.paths)


class Subscriber:
    def __init__(self, keys: set[str]) -> None:
        self.keys = keys
        self.queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1000)
        self.dropped = False

    def put(self, event: dict[str, Any]) -> None:
        try:
            self.queue.put_nowait(event)
        except queue.Full:  # a stalled client: tell it to reload instead of queueing forever
            self.dropped = True


class LiveHub:
    """Watchers shared by every Studio tab of one ``alfrd serve``.

    ``roots`` maps a project name to its folder (called on demand, so projects
    connected later work). A watcher runs while it has subscribers, or for
    ``lease`` seconds after the last poll of :meth:`changes`.
    """

    RUNTIME = "@runtime"  # not a valid project name, so it cannot collide

    def __init__(self, roots: Callable[[str], Path | None], runtime_paths: Iterable[str | Path] = (),
                 interval: float = 2.0, idle: float = 5.0, lease: float = 60.0) -> None:
        self.roots = roots
        self.runtime_paths = [p for p in runtime_paths if p]
        self.interval = interval
        self.idle = idle
        self.lease = lease
        self.enabled = interval > 0
        self._watchers: dict[str, Watcher] = {}
        self._event_watchers: dict[str, AlfrdWatcher] = {}
        self._subs: set[Subscriber] = set()
        self._leases: dict[str, float] = {}
        self._lock = threading.RLock()
        self._reaper: threading.Thread | None = None

    # -- watchers -----------------------------------------------------------
    def _publish(self, event: dict[str, Any]) -> None:
        with self._lock:
            subs = list(self._subs)
        for sub in subs:
            if event["key"] in sub.keys:
                sub.put(event)

    def watcher(self, key: str) -> Watcher | None:
        with self._lock:
            w = self._watchers.get(key)
            if w is not None and w.running:
                return w
            if key == self.RUNTIME:
                if not self.runtime_paths:
                    return None
                w = FileWatcher(key, self.runtime_paths, self._publish, interval=max(self.interval, 1.0), idle=self.idle)
            else:
                root = self.roots(key)
                if root is None:
                    return None
                w = TreeWatcher(key, root, self._publish, interval=self.interval, idle=self.idle)
            self._watchers[key] = w
            w.start()
            if key != self.RUNTIME:
                # Share the tree cursor/history so SSE and poll fallback have one ordered stream.
                def forward(event, owner=w):
                    with owner._lock:
                        owner.version += 1
                        event.update(epoch=owner.epoch, version=owner.version)
                        owner.events.append(event)
                        self._publish(event)
                events = AlfrdWatcher(key, root, forward, interval=self.interval, idle=self.idle)
                self._event_watchers[key] = events
                events.start()
            self._ensure_reaper()
            return w

    def _in_use(self, key: str, now: float) -> bool:
        if any(key in s.keys for s in self._subs):
            return True
        return now - self._leases.get(key, 0.0) < self.lease

    def reap(self, now: float | None = None) -> list[str]:
        """Stop watchers nobody listens to. Returns their keys."""
        now = time.time() if now is None else now
        stopped = []
        with self._lock:
            for key, w in list(self._watchers.items()):
                if not self._in_use(key, now):
                    w.stop()
                    companion = self._event_watchers.pop(key, None)
                    if companion:
                        companion.stop()
                    del self._watchers[key]
                    stopped.append(key)
        return stopped

    def _ensure_reaper(self) -> None:
        if self._reaper and self._reaper.is_alive():
            return

        def loop() -> None:
            while True:
                time.sleep(min(10.0, max(1.0, self.lease / 3)))
                self.reap()
                with self._lock:
                    if not self._watchers:
                        self._reaper = None
                        return

        self._reaper = threading.Thread(target=loop, name="alfrd-live-reaper", daemon=True)
        self._reaper.start()

    def stop_all(self) -> None:
        with self._lock:
            for w in self._watchers.values():
                w.stop()
            self._watchers.clear()
            for w in self._event_watchers.values():
                w.stop()
            self._event_watchers.clear()

    # -- subscribers --------------------------------------------------------
    def subscribe(self, projects: Iterable[str]) -> tuple[Subscriber, dict[str, Any]]:
        keys = {p for p in projects if p}
        keys.add(self.RUNTIME)
        sub = Subscriber(keys)
        with self._lock:
            self._subs.add(sub)
        return sub, self.hello(keys)

    def unsubscribe(self, sub: Subscriber) -> None:
        with self._lock:
            self._subs.discard(sub)
            now = time.time()
            for key in sub.keys:  # a reconnect within the lease keeps the watcher (and its epoch)
                self._leases[key] = max(self._leases.get(key, 0.0), now)

    def hello(self, keys: Iterable[str], wait: float = 10.0) -> dict[str, Any]:
        """Current state of each watcher (started if needed)."""
        out: dict[str, Any] = {}
        for key in keys:
            w = self.watcher(key)
            if w is None:
                continue
            w.wait_ready(wait)
            companion = self._event_watchers.get(key)
            if companion is not None:
                companion.wait_ready(wait)
            out[key] = w.state()
        return out

    def changes(self, since: Mapping[str, str], keys: Iterable[str]) -> dict[str, Any]:
        """Poll fallback: events after ``since[key] = "epoch:version"`` for each key."""
        now = time.time()
        keys = set(keys) | {self.RUNTIME}
        with self._lock:
            for key in keys:
                self._leases[key] = now
        state = self.hello(keys)
        events: list[dict[str, Any]] = []
        resets: list[str] = []
        for key, st in state.items():
            epoch, _, version = str(since.get(key) or "").partition(":")
            if not epoch:
                continue
            w = self._watchers.get(key)
            if w is None:
                continue
            try:
                got, reset = w.since(epoch, int(version or 0))
            except ValueError:
                got, reset = [], True
            events.extend(got)
            if reset:
                resets.append(key)
        return {"state": state, "events": events, "reset": resets}

    def touch(self, key: str) -> dict[str, Any] | None:
        """Start (or keep) the watcher for ``key``, wait for its baseline, return its state.

        Called before a full scan is served: the scan carries this state, so
        the Studio knows which version it shows and re-reads on connect only
        if the watcher has moved on since.
        """
        with self._lock:
            self._leases[key] = time.time()
        w = self.watcher(key)
        if w is None:
            return None
        w.wait_ready(10.0)
        return w.state()

    def poke(self, key: str) -> None:
        w = self._watchers.get(key)
        if w is not None:
            w.poke()
        events = self._event_watchers.get(key)
        if events is not None:
            events.poke()


__all__ = ["LiveHub", "TreeWatcher", "AlfrdWatcher", "FileWatcher", "Watcher", "diff_snapshots", "tree_snapshot"]
