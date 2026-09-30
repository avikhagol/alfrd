"""Full-text search (alfrd.search): FTS5 index, live updates, fallback scan, Studio endpoints."""

from __future__ import annotations

import os

import pytest

from alfrd import search


@pytest.fixture()
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("ALFRD_SEARCH_DIR", str(tmp_path / "idx"))
    base = tmp_path / "proj"
    (base / "logs").mkdir(parents=True)
    (base / "alfrd.yaml").write_text("version: 1\nname: proj\n")
    (base / "logs" / "casa.log").write_text("".join(f"line {i} nothing here\n" for i in range(1, 200))
                                            + "fringefit J0742+103 SNR 12 wd_1/casa.log\n")
    (base / "logs" / "other.log").write_text("rpicard started\n")
    (base / "logs" / "image.png").write_bytes(b"\x89PNG\x00\x00J0742+103")
    files = {"logs/casa.log", "logs/other.log", "logs/image.png", "alfrd.yaml"}

    def fake(r):
        out = {}
        for rel in sorted(files):
            p = base / rel
            if p.is_file() and not search.BINARY.search(rel):
                st = p.stat()
                out[rel] = (st.st_size, st.st_mtime)
        return out

    monkeypatch.setattr(search, "candidates", fake)
    return base


pytestmark = pytest.mark.skipif(not search.fts5_available(), reason="sqlite3 without FTS5")


def test_target_names_are_single_tokens_and_lines_are_exact(root):
    idx = search.SearchIndex(root, "p")
    assert idx.sync()["state"] == "ready"
    [hit] = idx.search("J0742+103")
    assert hit["rel"] == "logs/casa.log" and hit["line"] == 200 and "J0742+103" in hit["snippet"]
    assert idx.search("j0742") and idx.search("fringe j07")  # prefix, case-insensitive, all words
    assert idx.search("wd_1/casa.log")[0]["line"] == 200
    assert idx.search("J0742+103 rpicard") == [], "all words must be in the same chunk"
    assert not idx.search("0742"), "J0742+103 is one token, not J / 0742 / 103"
    assert idx.search("image") == [] and idx.search("PNG") == [], "binaries are not indexed"
    stats = idx.stats()
    assert stats["files"] == 3 and stats["text_bytes"] > 0


def test_index_follows_append_rotate_and_delete(root):
    idx = search.SearchIndex(root, "p")
    idx.sync()
    log = root / "logs" / "other.log"
    with open(log, "a") as stream:
        stream.write("step avica_snr failed: no calibrator\npartial line without newline")
    idx.sync(only=["logs/other.log"])
    [hit] = idx.search("calibrator")
    assert hit["line"] == 2
    assert idx.search("without")[0]["line"] == 3, "an unfinished last line is searchable too"
    with open(log, "a") as stream:
        stream.write(" yet\nand more\n")
    idx.sync(only=["logs/other.log"])
    assert [h["line"] for h in idx.search("without")] == [3], "…and replaced once it is complete"
    assert "yet" in idx.search("without")[0]["snippet"] and idx.search("more")[0]["line"] == 4
    # Truncated and written again in place, longer than before: not an append.
    log.write_text("rewritten in place " * 20 + "\n")
    idx.sync(only=["logs/other.log"])
    assert idx.search("calibrator") == [] and idx.search("rewritten")[0]["line"] == 1
    log.write_text("rpicard started\nstep avica_snr failed: no calibrator\n")
    idx.sync(only=["logs/other.log"])
    # Rotation: a new file under the same name is indexed from the start.
    log.rename(root / "logs" / "other.log.1")
    log.write_text("fresh rotated log\n")
    idx.sync(only=["logs/other.log"])
    assert idx.search("calibrator") == [] and idx.search("rotated")[0]["line"] == 1
    # Truncation (same inode, smaller): from the start again.
    log.write_text("x\n")
    idx.sync(only=["logs/other.log"])
    assert idx.search("rotated") == []
    # Removal.
    log.unlink()
    idx.sync(removed=["logs/other.log"])
    assert idx.search("x") == []
    # A full sync drops what is no longer a candidate.
    (root / "logs" / "casa.log").unlink()
    idx.sync()
    assert idx.search("fringefit") == []


def test_live_event_updates_a_built_index(root):
    idx = search.ensure_built(root, "live-p")
    for thread in list(search._THREADS.values()):
        thread.join(10)
    assert idx.progress["state"] == "ready"
    with open(root / "logs" / "other.log", "a") as stream:
        stream.write("NEWTOKEN arrived\n")
    search.on_tree_event(root, "live-p", {"changed": [], "removed": [], "logs": {"logs/other.log": [1, 1]}})
    assert idx.search("NEWTOKEN")[0]["line"] == 2
    ctx = search.context(root, "logs/other.log", 2, around=1)
    assert ctx["first"] == 1 and ctx["lines"] == ["rpicard started", "NEWTOKEN arrived"]
    with pytest.raises(ValueError):
        search.context(root, "../etc/passwd", 1)


def test_fallback_scan(root):
    result = search.scan(root, "J0742+103 fringefit", limit=5)
    assert result["complete"] and [(h["rel"], h["line"]) for h in result["hits"]] == [("logs/casa.log", 200)]
    assert len(search.scan(root, "line", limit=3)["hits"]) == 3


def test_studio_search_endpoint_and_fallback(root, tmp_path, monkeypatch):
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
                      "RUNTIME_SERVICE": service, "CATALOG_READER": RuntimeCatalogReader(service), "STUDIO_DEMO": False})
    client = app.test_client()
    first = client.get("/api/studio/search", query_string={"projects": pid, "q": "J0742+103"}).get_json()
    assert first["hits"][0]["line"] == 200 and first["hits"][0]["entity"] == {"project": pid, "file": "logs/casa.log", "line": 200}
    for thread in list(search._THREADS.values()):
        thread.join(10)
    again = client.get("/api/studio/search", query_string={"projects": pid, "q": "fringefit"}).get_json()
    assert again["hits"][0]["mode"] == "index" and again["index"][pid]["state"] == "ready"
    assert client.get("/api/studio/search", query_string={"projects": pid, "q": ""}).status_code == 400
    ctx = client.get("/api/studio/search/context", query_string={"project": pid, "path": "logs/casa.log", "line": 200, "around": 2}).get_json()
    assert ctx["lines"][-1].startswith("fringefit")
    monkeypatch.setattr(search, "fts5_available", lambda: False)
    scan = client.get("/api/studio/search", query_string={"projects": pid, "q": "rpicard"}).get_json()
    assert scan["fts5"] is False and scan["hits"][0]["mode"] == "scan"
