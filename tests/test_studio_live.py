"""Live updates: offset log reads, tree snapshots, watchers, event stream and poll fallback."""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from pathlib import Path

import pytest

from alfrd.studio_defs import read_range
from alfrd.studio_live import FileWatcher, LiveHub, TreeWatcher, diff_snapshots, scan_snapshot, tree_snapshot

TREE = Path(__file__).parent / "fixtures" / "avica_tree"
LOG = "reductions/RDV41/wd/wd_S/avica_avg_casa_log-20260908_180141.log"
META = "reductions/RDV41/wd/avica.meta/refants_X_0742+103.avica"


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    shutil.copytree(TREE, root)
    return root


def _bump(path: Path, text: str) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(text)
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))


# -- read_range -------------------------------------------------------------


def test_read_range_appends_and_resets(tmp_path):
    log = tmp_path / "a.log"
    log.write_bytes(b"one\ntwo\n")
    first = read_range(log)
    assert first["text"] == "one\ntwo\n" and first["reset"] and first["offset"] == 8
    assert read_range(log, first["offset"], file=first["id"])["text"] == ""
    with log.open("ab") as f:
        f.write(b"three\n")
    more = read_range(log, first["offset"], file=first["id"])
    assert more == {**more, "text": "three\n", "reset": False, "offset": 14}
    # Truncated: start over.
    log.write_bytes(b"x\n")
    again = read_range(log, more["offset"], file=more["id"])
    assert again["reset"] and again["text"] == "x\n"
    # Replaced by a new file (rotation): new id → reset even if it is longer.
    log.rename(tmp_path / "a.log.1")
    log.write_bytes(b"rotated line one\nrotated two\n")
    rotated = read_range(log, 2, file=again["id"])
    assert rotated["reset"] and rotated["text"].startswith("rotated")


def test_read_range_keeps_split_utf8_for_next_read(tmp_path):
    log = tmp_path / "u.log"
    data = "αβ".encode()
    log.write_bytes(data[:3])  # α plus the first byte of β
    part = read_range(log)
    assert part["text"] == "α" and part["offset"] == 2
    log.write_bytes(data)
    rest = read_range(log, part["offset"], file=part["id"])
    assert rest["text"] == "β" and not rest["reset"]


def test_read_range_large_append_resets_to_tail(tmp_path):
    log = tmp_path / "big.log"
    log.write_bytes(b"a\n")
    first = read_range(log, tail=100)
    log.write_bytes(b"a\n" + b"".join(b"line %d\n" % i for i in range(200)))
    got = read_range(log, first["offset"], tail=100, file=first["id"])
    assert got["reset"] and len(got["text"].encode()) <= 100 and got["text"].startswith("line ")


# -- snapshots / watchers ---------------------------------------------------


def test_snapshot_is_stat_only_and_classifies(tree):
    snap = tree_snapshot(tree)
    assert snap["alfrd.yaml"][0] == "content"
    assert snap[META][0] == "content"
    assert snap[LOG][0] == "log"
    assert any(kind == "marker" for kind, *_ in snap.values())


def test_full_scan_can_seed_the_same_snapshot(tree):
    from alfrd.avica_layout import collect_studio_files

    assert scan_snapshot(collect_studio_files(tree, log_tail=0)) == tree_snapshot(tree)


def test_seed_captures_history_and_keeps_changes_during_read(tree):
    from alfrd.avica_layout import collect_studio_files
    from alfrd.history import versions

    w = TreeWatcher("p", tree, lambda e: None)
    scan = collect_studio_files(tree, log_tail=0)
    # Metadata in the scan precedes the edit: the first poll must detect it.
    _bump(tree / META, "\n")
    assert w.seed(scan)
    assert w.baseline_ts == scan["generated_ts"]
    assert versions(tree, "alfrd.yaml")[0]["source"] == "external"
    event = w.check()
    assert META in event["changed"] and event["version"] == 1
    previous = (w._snapshot, w.baseline_ts, w.version, list(w.events))
    assert not w.seed(scan)
    assert (w._snapshot, w.baseline_ts, w.version, list(w.events)) == previous


def test_hub_initial_scan_discovers_once_and_retains_start_cursor(tree, monkeypatch):
    from alfrd import avica_layout
    from alfrd.studio_live import Watcher

    # Control polling explicitly; no timing-dependent operation count.
    monkeypatch.setattr(Watcher, "start", lambda self: None)
    monkeypatch.setattr(Watcher, "wait_ready", lambda self, timeout=10: True)
    hub = LiveHub(lambda key: tree, interval=2)
    calls = []
    collect = avica_layout.collect_studio_files

    def counting(*args, **kwargs):
        calls.append(kwargs)
        return collect(*args, **kwargs)

    monkeypatch.setattr(avica_layout, "collect_studio_files", counting)
    data = hub.scan("p", lambda: counting(tree, log_tail=0))
    assert len(calls) == 1
    w = hub._watchers["p"]
    assert w._ready.is_set() and data["live"] == {"epoch": w.epoch, "version": 0}

    def changing_scan():
        data = counting(tree, log_tail=0)
        _bump(tree / META, "\n")
        assert w.check()["version"] == 1
        return data

    updated = hub.scan("p", changing_scan)
    assert updated["live"]["version"] == 0  # never claim an event newer than the scan
    got, reset = w.since(updated["live"]["epoch"], 0)
    assert not reset and got[0]["changed"] == [META]
    hub.stop_all()


def test_hub_failed_initial_scan_still_recovers(tree):
    hub = LiveHub(lambda key: tree, interval=0.2)
    try:
        def fail():
            raise FileNotFoundError("scan failed")

        with pytest.raises(FileNotFoundError):
            hub.scan("p", fail)
        w = hub._watchers["p"]
        assert w.wait_ready(5) and w.running
        assert w._snapshot == tree_snapshot(tree)
    finally:
        hub.stop_all()


def test_seed_keeps_companion_events_after_the_scan_start(tree, monkeypatch):
    from alfrd.avica_layout import collect_studio_files
    from alfrd.runtime import scheduler
    from alfrd.studio_live import Watcher

    monkeypatch.setattr(Watcher, "start", lambda self: None)
    monkeypatch.setattr(scheduler, "list_plans", lambda _: [{"id": "run", "status": "running"}])
    events_file = tree / ".alfrd/plans/run/events.jsonl"
    events_file.parent.mkdir(parents=True)
    events_file.write_text("")

    def ready(watcher, timeout=10):
        if watcher._snapshot is None:
            watcher.check()
        return True

    monkeypatch.setattr(Watcher, "wait_ready", ready)
    hub = LiveHub(lambda key: tree)

    def collect():
        data = collect_studio_files(tree, log_tail=0)
        events_file.write_text(json.dumps({"seq": 1, "kind": "review.pending", "plan": "run"}) + "\n")
        assert hub._event_watchers["p"].check()["version"] == 1
        _bump(tree / META, "\n")
        return data

    try:
        data = hub.scan("p", collect)
        w = hub._watchers["p"]
        assert data["live"]["version"] == 0 and w.version == 1
        assert w.check()["version"] == 2
        response = hub.changes({"p": f"{w.epoch}:0"}, ["p"])
        assert response["reset"] == []
        assert [event["type"] for event in response["events"]] == ["alfrd", "tree"]
        assert [event["version"] for event in response["events"]] == [1, 2]
    finally:
        hub.stop_all()


def test_initial_scan_blocks_a_concurrent_baseline(tree, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from alfrd.avica_layout import collect_studio_files
    from alfrd.studio_live import Watcher

    monkeypatch.setattr(Watcher, "start", lambda self: None)
    monkeypatch.setattr(Watcher, "wait_ready", lambda self, timeout=10: True)
    hub = LiveHub(lambda key: tree)
    entered, release, probing = threading.Event(), threading.Event(), threading.Event()

    def delayed_scan():
        data = collect_studio_files(tree, log_tail=0)
        assert hub.reap(time.time() + 3600) == []  # an in-flight scan owns its watcher
        entered.set()
        assert release.wait(5)
        return data

    with ThreadPoolExecutor(max_workers=2) as pool:
        response = pool.submit(hub.scan, "p", delayed_scan)
        try:
            assert entered.wait(5)
            w = hub._watchers["p"]
            _bump(tree / META, "\n")

            def check():
                probing.set()
                return w.check()

            poll = pool.submit(check)
            assert probing.wait(5)
            assert w._snapshot is None  # the newer poll cannot replace scan metadata
        finally:
            release.set()
        data = response.result(timeout=5)
        event = poll.result(timeout=5)
    assert data["live"]["version"] == 0 and event["version"] == 1
    assert META in event["changed"]
    assert hub._scanning == {} and hub.reap(time.time()) == []
    assert hub.reap(time.time() + 3600) == ["p"]
    hub.stop_all()


def test_seeded_watcher_waits_then_polls_and_can_be_poked(tree, monkeypatch):
    from alfrd.avica_layout import collect_studio_files

    w = TreeWatcher("p", tree, lambda e: None, interval=0.2)
    w.seed(collect_studio_files(tree, log_tail=0))
    waiting, release, checked = threading.Event(), threading.Event(), threading.Event()

    def wait(delay):
        waiting.set()
        assert release.wait(5)

    def check():
        checked.set()
        w.stop()

    monkeypatch.setattr(w._wake, "wait", wait)
    monkeypatch.setattr(w, "check", check)
    w.start()
    try:
        assert waiting.wait(5) and not checked.is_set()
        w.poke()
        assert w._wake.is_set()
    finally:
        release.set()
        w._thread.join(5)
        w.stop()
    assert checked.is_set()


def test_diff_separates_log_growth_from_content_changes(tree):
    before = tree_snapshot(tree)
    _bump(tree / LOG, "more\n")
    _bump(tree / META, "\n")
    (tree / "reductions/RDV41/wd/avica.meta/new_X_0742+103.avica").write_text("{}")
    diff = diff_snapshots(before, tree_snapshot(tree))
    assert list(diff["logs"]) == [LOG]
    assert META in diff["changed"] and "reductions/RDV41/wd/avica.meta/new_X_0742+103.avica" in diff["changed"]
    (tree / META).unlink()
    assert META in diff_snapshots(before, tree_snapshot(tree))["removed"]


def test_watcher_versions_events_and_adapts(tree):
    got = []
    w = TreeWatcher("p", tree, got.append, interval=2, idle=15)
    assert w.check() is None and w.baseline_ts > 0  # first pass = baseline
    assert w.check() is None  # nothing changed
    _bump(tree / LOG, "x\n")
    event = w.check()
    assert event["version"] == 1 and event["logs"] and got == [event]
    assert w.next_delay() == 2  # busy
    w.last_change -= 3600
    assert w.next_delay() == 15  # idle
    w.pass_seconds = 2.0
    assert w.next_delay() == 40  # a slow tree slows itself down
    events, reset = w.since(w.epoch, 0)
    assert events == [event] and not reset
    assert w.since("other-epoch", 0)[1] is True


def test_watcher_survives_errors(tree, monkeypatch):
    monkeypatch.setenv("ALFRD_DEFAULT_MANIFEST", str(tree / "no-default.yaml"))  # no fallback manifest
    w = TreeWatcher("p", tree, lambda e: None)
    w.check()
    (tree / "alfrd.yaml").rename(tree / "alfrd.yaml.off")
    assert w.check() is None and w.error
    (tree / "alfrd.yaml.off").rename(tree / "alfrd.yaml")
    w.check()
    assert w.error is None


def test_file_watcher_sees_runtime_db(tmp_path):
    db = tmp_path / "runtime.sqlite"
    db.write_bytes(b"1")
    got = []
    w = FileWatcher("runtime", [db, f"{db}-wal"], got.append)
    w.check()
    _bump(db, "2")
    assert w.check()["type"] == "runtime"


def test_hub_runs_watchers_only_while_used(tree):
    hub = LiveHub(lambda name: tree if name == "p" else None, interval=0.2, idle=0.2, lease=0.5)
    sub, hello = hub.subscribe(["p", "missing"])
    assert set(hello) == {"p"} and hello["p"]["version"] == 0
    _bump(tree / META, "\n")
    event = sub.queue.get(timeout=5)
    assert event["type"] == "tree" and META in event["changed"]
    hub.unsubscribe(sub)
    assert hub.reap(time.time()) == []  # lease: a quick reconnect keeps the watcher
    assert hub.reap(time.time() + 5) == ["p"]
    # Poll fallback
    first = hub.changes({}, ["p"])
    state = first["state"]["p"]
    _bump(tree / LOG, "y\n")
    deadline = time.time() + 5
    while time.time() < deadline:
        polled = hub.changes({"p": f"{state['epoch']}:{state['version']}"}, ["p"])
        if polled["events"]:
            break
        time.sleep(0.1)
    assert polled["events"][0]["logs"] and polled["reset"] == []
    assert hub.changes({"p": "stale:3"}, ["p"])["reset"] == ["p"]
    hub.stop_all()


# -- Flask endpoints ----------------------------------------------------------


@pytest.fixture
def served(tree, tmp_path):
    pytest.importorskip("flask")
    from alfrd.cli import _connect_startup_project
    from alfrd.gui import create_app
    from alfrd.gui.services import RuntimeCatalogReader
    from alfrd.runtime import RuntimeService, RuntimeStore

    db = tmp_path / "runtime.sqlite"
    store = RuntimeStore(db)
    store.initialize()
    service = RuntimeService(store)
    name = _connect_startup_project(service, str(tree))
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "k",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'catalog.sqlite'}",
        "RUNTIME_SERVICE": service,
        "RUNTIME_DATABASE": str(db),
        "CATALOG_READER": RuntimeCatalogReader(service),
        "STUDIO_DEFAULT_PROJECT": name,
        "STUDIO_PROJECTS": [name],
        "STUDIO_LIVE_INTERVAL": 0.2,
        "STUDIO_LIVE_IDLE": 0.2,
        "STUDIO_LIVE_HEARTBEAT": 0.3,
    })
    yield app, app.test_client(), name, tree
    hub = app.extensions.get("alfrd_live")
    if hub:
        hub.stop_all()


def test_session_announces_live(served):
    _, client, _, _ = served
    live = client.get("/api/studio/session").get_json()["live"]
    assert live == {"enabled": True, "interval": 0.2, "idle": 0.2}


def test_file_offset_endpoint(served):
    _, client, name, tree = served
    url = f"/api/studio/projects/{name}/file"
    first = client.get(url, query_string={"path": LOG})
    assert first.headers["X-Reset"] == "1"
    offset, ident = first.headers["X-Offset"], first.headers["X-File-Id"]
    assert client.get(url, query_string={"path": LOG, "offset": offset, "id": ident}).data == b""
    _bump(tree / LOG, "appended\n")
    more = client.get(url, query_string={"path": LOG, "offset": offset, "id": ident})
    assert more.data == b"appended\n" and more.headers["X-Reset"] == "0"
    assert client.get(url, query_string={"path": LOG, "offset": "x"}).status_code == 400
    assert client.get(url, query_string={"path": "avica.inp", "offset": 0}).status_code == 403


def test_scan_only_returns_just_those_files(served):
    _, client, name, _ = served
    scan = client.get(f"/api/studio/projects/{name}/scan", query_string=[("only", META), ("only", LOG)]).get_json()
    rels = {f["rel"]: f for f in scan["files"]}
    assert set(rels) == {META, LOG} and "text" in rels[META] and "text" not in rels[LOG]
    assert scan["generated_ts"] > 0 and "live" not in scan  # partial scans don't claim a version
    full = client.get(f"/api/studio/projects/{name}/scan").get_json()
    assert full["live"]["epoch"] and full["live"]["version"] == 0


def test_full_scan_endpoint_reuses_discovery_for_initial_baseline(served, monkeypatch):
    from alfrd import avica_layout

    app, client, name, root = served
    app.config["STUDIO_LIVE_INTERVAL"] = 30
    app.config["STUDIO_LIVE_IDLE"] = 30
    collect = avica_layout.collect_studio_files
    calls = []

    def counting(scan_root, *args, **kwargs):
        if Path(scan_root) == root:
            calls.append(kwargs)
        return collect(scan_root, *args, **kwargs)

    monkeypatch.setattr(avica_layout, "collect_studio_files", counting)
    # Another watcher may still be finishing a pass after its fixture called stop().
    # Reproduce that process-global monkeypatch traffic without relying on timing.
    unrelated = root.parent / "unrelated"
    unrelated.mkdir()
    (unrelated / "alfrd.yaml").write_text("name: unrelated\n", encoding="utf-8")
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(avica_layout.collect_studio_files, unrelated, read=False, log_tail=0).result(timeout=5)
    data = client.get(f"/api/studio/projects/{name}/scan").get_json()
    assert calls == [{"log_tail": 0, "only": None}]
    watcher = app.extensions["alfrd_live"]._watchers[name]
    assert watcher.running and watcher._snapshot == scan_snapshot(data)
    assert watcher.baseline_ts == data["generated_ts"]


def test_event_stream_hello_tree_and_ping(served):
    _, client, name, tree = served
    res = client.get("/api/studio/events", query_string={"projects": f"{name},not-in-scope"}, buffered=False)
    assert res.mimetype == "text/event-stream"
    chunks = iter(res.response)
    text = ""
    while "event: hello" not in text or "\n\n" not in text.split("event: hello", 1)[1]:
        text += next(chunks).decode()
    hello = json.loads(text.split("event: hello\ndata: ", 1)[1].split("\n\n", 1)[0])
    assert set(hello["state"]) == {name, "@runtime"}
    _bump(tree / META, "\n")
    seen = ""
    deadline = time.time() + 5
    while "event: tree" not in seen and time.time() < deadline:
        seen += next(chunks).decode()
    assert "event: tree" in seen and META in seen
    while ": ping" not in seen and time.time() < deadline:
        seen += next(chunks).decode()
    assert ": ping" in seen
    res.close()


def test_changes_poll_and_live_off(served, tmp_path):
    app, client, name, _ = served
    res = client.get("/api/studio/changes", query_string={"projects": name}).get_json()
    assert res["state"][name]["epoch"] and res["events"] == []
    assert client.get("/api/studio/changes", query_string={"since": "[1]"}).status_code == 400
    app.config["STUDIO_LIVE_INTERVAL"] = 0
    app.extensions.pop("alfrd_live").stop_all()
    assert client.get("/api/studio/events").status_code == 404
    assert client.get("/api/studio/session").get_json()["live"]["enabled"] is False
