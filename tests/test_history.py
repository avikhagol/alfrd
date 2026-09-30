"""Version history of alfrd.yaml (alfrd.history) and its Studio endpoints."""

from __future__ import annotations

import time

import pytest

from alfrd import history

YAML = "version: 1\nname: proj\n"


@pytest.fixture()
def root(tmp_path):
    base = tmp_path / "proj"
    base.mkdir()
    (base / "alfrd.yaml").write_text(YAML)
    return base


def test_save_records_versions_and_dedupes(root):
    entry = history.save(root, "alfrd.yaml", YAML + "description: a\n", source="studio")
    assert entry["source"] == "studio" and "@" in entry["who"]
    items = history.versions(root, "alfrd.yaml")
    assert [i["source"] for i in items] == ["studio", "external"], "the file as found is kept before the first save"
    assert history.save(root, "alfrd.yaml", YAML + "description: a\n", source="studio") is None
    assert len(history.versions(root, "alfrd.yaml")) == 2
    assert history.read_version(root, "alfrd.yaml", items[1]["version"]) == YAML
    assert (root / "alfrd.yaml.bak").read_text() == YAML
    diff = history.diff(root, "alfrd.yaml", items[1]["version"])
    assert "+description: a" in diff


def test_conflict_guard_refuses_a_stale_save(root):
    loaded = (root / "alfrd.yaml").read_text()
    (root / "alfrd.yaml").write_text(YAML + "description: edited elsewhere\n")  # someone else saves
    with pytest.raises(history.Conflict) as info:
        history.save(root, "alfrd.yaml", YAML + "description: mine\n", source="studio", base_hash=history.text_hash(loaded))
    assert "+description: mine" in info.value.diff and "-description: edited elsewhere" in info.value.diff
    assert "edited elsewhere" in (root / "alfrd.yaml").read_text()
    # Same with the base text, and force overwrites.
    with pytest.raises(history.Conflict):
        history.save(root, "alfrd.yaml", YAML, source="studio", base_text=loaded + "x")
    history.save(root, "alfrd.yaml", YAML + "description: mine\n", source="studio", base_text=loaded, force=True)
    assert "mine" in (root / "alfrd.yaml").read_text()
    # Saving what is already on disk is never a conflict.
    history.save(root, "alfrd.yaml", (root / "alfrd.yaml").read_text(), source="studio", base_hash="0" * 64)


def test_restore_is_a_new_version_and_retention(root):
    history.save(root, "alfrd.yaml", YAML + "description: b\n", source="studio")
    first = history.versions(root, "alfrd.yaml")[-1]
    entry = history.restore(root, "alfrd.yaml", first["version"])
    assert entry["source"] == "restore" and (root / "alfrd.yaml").read_text() == YAML
    assert len(history.versions(root, "alfrd.yaml")) == 3
    (root / "alfrd.yaml").write_text(YAML + "history: {keep: 3}\n")
    for i in range(6):
        history.save(root, "alfrd.yaml", YAML + f"history: {{keep: 3}}\ndescription: v{i}\n", source="cli")
    items = history.versions(root, "alfrd.yaml")
    assert len(items) == 3 and "v5" in history.read_version(root, "alfrd.yaml", items[0]["version"])
    folder = root / ".alfrd" / "history" / "alfrd.yaml"
    assert len(list(folder.glob("*.yaml"))) == 3


def test_tracked_files_and_refusals(root):
    (root / "avica.inp").write_text("a = 1\n")
    (root / "alfrd.plan.csv").write_text("x\n")
    assert history.tracked(root) == ["alfrd.yaml"]
    with pytest.raises(history.HistoryError):
        history.versions(root, "avica.inp")
    (root / "alfrd.yaml").write_text(YAML + "history:\n  files: [avica.inp, '*.csv', ../x]\n")
    assert history.tracked(root) == ["alfrd.yaml", "avica.inp"], "plan CSVs are never tracked"
    for bad in ("../x", "alfrd.plan.csv", ".alfrd/history/x", "reductions/x.log"):
        with pytest.raises(history.HistoryError):
            history.versions(root, bad)
    with pytest.raises(history.HistoryError):
        history.read_version(root, "alfrd.yaml", "../../etc/passwd")


def test_external_edit_is_captured_by_the_live_watcher(root):
    from alfrd.studio_live import TreeWatcher

    watcher = TreeWatcher("p", root, lambda event: None, interval=0.2)
    watcher.check()  # baseline: the file as found
    assert [i["source"] for i in history.versions(root, "alfrd.yaml")] == ["external"]
    time.sleep(0.02)
    (root / "alfrd.yaml").write_text(YAML + "description: vim\n")
    event = watcher.check()
    assert event and "alfrd.yaml" in event["changed"]
    items = history.versions(root, "alfrd.yaml")
    assert [i["source"] for i in items] == ["external", "external"]
    assert "vim" in history.read_version(root, "alfrd.yaml", items[0]["version"])


# ---------------------------------------------------------------------------
# Studio endpoints


@pytest.fixture()
def studio(root, tmp_path):
    pytest.importorskip("flask")
    from alfrd.gui import create_app
    from alfrd.gui.services import RuntimeCatalogReader
    from alfrd.manifest_default import register_project_folder
    from alfrd.runtime import RuntimeService, RuntimeStore

    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    service = RuntimeService(store)
    registered = register_project_folder(service, root)
    identifier = getattr(registered, "identifier", None) or registered[0].identifier
    app = create_app({"TESTING": True, "SECRET_KEY": "k", "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'c.sqlite'}",
                      "RUNTIME_SERVICE": service, "CATALOG_READER": RuntimeCatalogReader(service), "STUDIO_DEMO": False,
                      "STUDIO_LIVE_INTERVAL": 0})
    client = app.test_client()
    token = client.get("/api/studio/session").get_json()["csrf_token"]
    return client, {"X-CSRF-Token": token}, identifier


def test_studio_save_409_history_and_restore(studio, root):
    client, h, pid = studio
    base = f"/api/studio/projects/{pid}"
    loaded = (root / "alfrd.yaml").read_text()
    ok = client.post(f"{base}/manifest", json={"text": YAML + "description: 1\n", "base_hash": history.text_hash(loaded)}, headers=h)
    assert ok.status_code == 200 and ok.get_json()["version"]["source"] == "studio"
    stale = client.post(f"{base}/manifest", json={"text": YAML + "description: 2\n", "base_hash": history.text_hash(loaded)}, headers=h)
    assert stale.status_code == 409
    body = stale.get_json()
    assert "description: 1" in body["current_text"] and "+description: 2" in body["diff"]
    listing = client.get(f"{base}/history").get_json()
    assert listing["file"] == "alfrd.yaml" and [v["source"] for v in listing["versions"]] == ["studio", "external"]
    assert listing["current_hash"] == history.text_hash((root / "alfrd.yaml").read_text())
    old = listing["versions"][-1]["version"]
    assert client.get(f"{base}/history/{old}").get_json()["text"] == YAML
    assert "-description: 1" in client.get(f"{base}/history/diff", query_string={"a": "current", "b": old}).get_json()["diff"]
    assert client.get(f"{base}/history/20260101T000000000000-000000000000").status_code == 404
    assert client.get(f"{base}/history", query_string={"file": "../etc/passwd"}).status_code == 400
    assert client.post(f"{base}/history/{old}/restore", json={}).status_code == 403, "restore needs the CSRF token"
    restored = client.post(f"{base}/history/{old}/restore", json={}, headers=h)
    assert restored.status_code == 200 and (root / "alfrd.yaml").read_text() == YAML
    assert client.get(f"{base}/history").get_json()["versions"][0]["source"] == "restore"


def test_paths_outside_the_project_are_refused(root, tmp_path):
    (root.parent / "alfrd.yaml").write_text("version: 1\nname: outside\n")
    for bad in ("../alfrd.yaml", "./../alfrd.yaml", "/etc/passwd", "a/../../alfrd.yaml", ".//../alfrd.yaml"):
        with pytest.raises(history.HistoryError):
            history.read_version(root, bad, "current")
    assert history.read_version(root, "./alfrd.yaml", "current") == YAML
