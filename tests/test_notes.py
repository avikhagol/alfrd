"""Annotations (alfrd.notes): event replay, concurrent appends, orphans, live update, endpoints."""

from __future__ import annotations

import json
import multiprocessing as mp
import time

import pytest

from alfrd import notes


@pytest.fixture()
def root(tmp_path):
    base = tmp_path / "proj"
    base.mkdir()
    (base / "alfrd.yaml").write_text("version: 1\nname: proj\n")
    return base


def test_create_edit_resolve_delete_replay(root):
    a = notes.create(root, {"target": "J0742+103", "project_code": "BV019", "step": "rpicard"}, "EF flagged, RFI", "#Flagged, rfi")
    assert a["status"] == "open" and a["tags"] == ["flagged", "rfi"] and "@" in a["author"]
    b = notes.create(root, {"target": "J0741+100"}, "check calibrator")
    notes.change(root, a["id"], "edit", text="EF flagged 03:10–03:40, RFI")
    notes.change(root, a["id"], "resolve")
    notes.change(root, b["id"], "delete")
    state = notes.replay(notes.events(root))
    assert list(state) == [a["id"]]
    assert state[a["id"]]["text"] == "EF flagged 03:10–03:40, RFI" and state[a["id"]]["status"] == "resolved"
    notes.change(root, a["id"], "reopen")
    assert notes.replay(notes.events(root))[a["id"]]["status"] == "open"
    lines = (root / "alfrd.notes.jsonl").read_text().splitlines()
    assert [json.loads(l)["op"] for l in lines] == ["create", "create", "edit", "resolve", "delete", "reopen"], "append-only"
    with pytest.raises(notes.NoteError):
        notes.change(root, b["id"], "edit", text="x")  # deleted
    with pytest.raises(notes.NoteError):
        notes.create(root, {"galaxy": "M87"}, "x")
    with pytest.raises(notes.NoteError):
        notes.create(root, {}, "x")
    with pytest.raises(notes.NoteError):
        notes.create(root, {"target": "A"}, "  ")
    # A broken line (a crash mid-write) is skipped, the rest still replays.
    with open(root / "alfrd.notes.jsonl", "a") as stream:
        stream.write('{"id": "n_1234", "op": "cre\n')
    assert list(notes.replay(notes.events(root))) == [a["id"]]


def _writer(path, n):
    for i in range(n):
        notes.create(path, {"target": f"T{i}"}, f"note {i}")


def test_concurrent_appends_keep_every_line(root):
    procs = [mp.get_context("fork").Process(target=_writer, args=(root, 40)) for _ in range(4)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(30)
    evs = notes.events(root)
    assert len(evs) == 160 and len({e["id"] for e in evs}) == 160


def test_orphans(root):
    (root / "logs").mkdir()
    (root / "logs" / "casa.log").write_text("x\n")
    (root / "alfrd.targets.csv").write_text("TARGET_NAME,FILENAMES,PROJECT_CODE\nJ0742+103,a.idifits,BV019\n")
    notes.create(root, {"target": "J0742+103"}, "ok")
    notes.create(root, {"target": "GONE"}, "orphan target")
    notes.create(root, {"file": "logs/casa.log", "line": 1}, "on a line")
    notes.create(root, {"file": "logs/removed.log", "line": 3}, "file gone")
    data = notes.listing(root)
    assert {n["text"]: n["orphaned"] for n in data["notes"]} == {"ok": False, "orphan target": True, "on a line": False, "file gone": True}
    assert data["counts"] == {"open": 4, "resolved": 0, "orphaned": 2}


def test_notes_file_is_in_the_scan_and_live_events(root):
    from alfrd.avica_layout import collect_studio_files
    from alfrd.studio_live import TreeWatcher

    watcher = TreeWatcher("p", root, lambda event: None, interval=0.2)
    watcher.check()
    notes.create(root, {"target": "A"}, "hello")
    event = watcher.check()
    assert event and "alfrd.notes.jsonl" in event["changed"]
    item = next(f for f in collect_studio_files(root)["files"] if f["rel"] == "alfrd.notes.jsonl")
    assert item["hint"] == "notes" and '"hello"' in item["text"]


def test_studio_notes_endpoints(root, tmp_path):
    pytest.importorskip("flask")
    from alfrd.gui import create_app
    from alfrd.gui.services import RuntimeCatalogReader
    from alfrd.manifest_default import register_project_folder
    from alfrd.runtime import RuntimeService, RuntimeStore

    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    service = RuntimeService(store)
    registered = register_project_folder(service, root)
    pid = getattr(registered, "identifier", None) or registered[0].identifier
    app = create_app({"TESTING": True, "SECRET_KEY": "k", "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'c.sqlite'}",
                      "RUNTIME_SERVICE": service, "CATALOG_READER": RuntimeCatalogReader(service), "STUDIO_DEMO": False,
                      "STUDIO_LIVE_INTERVAL": 0})
    client = app.test_client()
    h = {"X-CSRF-Token": client.get("/api/studio/session").get_json()["csrf_token"]}
    base = f"/api/studio/projects/{pid}/notes"
    assert client.post(base, json={"anchor": {"target": "A"}, "text": "x"}).status_code == 403, "writes need CSRF"
    made = client.post(base, json={"anchor": {"target": "A", "step": "rpicard"}, "text": "RFI", "tags": ["rfi"]}, headers=h)
    assert made.status_code == 201
    nid = made.get_json()["note"]["id"]
    assert client.post(base, json={"anchor": {"nope": 1}, "text": "x"}, headers=h).status_code == 400
    assert client.post(f"{base}/{nid}", json={"op": "resolve"}, headers=h).get_json()["note"]["status"] == "resolved"
    assert client.post(f"{base}/n_ffffffff", json={"op": "resolve"}, headers=h).status_code == 404
    data = client.get(base).get_json()
    assert data["notes"][0]["id"] == nid and data["tags"] == ["rfi"] and data["counts"]["resolved"] == 1
