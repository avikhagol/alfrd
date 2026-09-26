"""Live updates: offset log reads, tree snapshots, watchers, event stream and poll fallback."""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import pytest

from alfrd.studio_defs import read_range
from alfrd.studio_live import FileWatcher, LiveHub, TreeWatcher, diff_snapshots, tree_snapshot

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
