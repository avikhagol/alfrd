"""Manifest precedence (.alfrd/alfrd.yaml > alfrd.yaml > .alfrd.yaml) and writes to the active file."""

from __future__ import annotations

from pathlib import Path

import pytest

from alfrd import history
from alfrd.manifest import discover_manifest, load_manifest
from alfrd.manifest_paths import (
    MANIFEST_CANDIDATES,
    manifest_location,
    new_manifest_path,
    project_manifest,
    project_root,
)

ROOT_YAML = "version: 1\nname: rootcfg\n"
NESTED_YAML = "version: 1\nname: nestedcfg\n"


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture()
def both(tmp_path):
    """A project with a nested manifest and a (shadowed) root one."""
    root = tmp_path / "proj"
    _write(root / "alfrd.yaml", ROOT_YAML)
    _write(root / ".alfrd" / "alfrd.yaml", NESTED_YAML)
    return root


def test_precedence_order_is_fixed():
    assert MANIFEST_CANDIDATES == (".alfrd/alfrd.yaml", "alfrd.yaml", ".alfrd.yaml")


@pytest.mark.parametrize("present, active", [
    ((".alfrd.yaml",), ".alfrd.yaml"),
    (("alfrd.yaml",), "alfrd.yaml"),
    (("alfrd.yaml", ".alfrd.yaml"), "alfrd.yaml"),
    ((".alfrd/alfrd.yaml",), ".alfrd/alfrd.yaml"),
    ((".alfrd/alfrd.yaml", "alfrd.yaml", ".alfrd.yaml"), ".alfrd/alfrd.yaml"),
])
def test_active_manifest(tmp_path, present, active):
    for rel in present:
        _write(tmp_path / rel, ROOT_YAML)
    assert project_manifest(tmp_path) == tmp_path / active
    location = manifest_location(tmp_path)
    assert location["active"] == active and location["default_new"] == ".alfrd/alfrd.yaml"
    shadowed = {f["rel"] for f in location["files"] if f["shadowed"]}
    assert shadowed == set(present) - {active}


def test_no_manifest_creates_nested(tmp_path):
    assert project_manifest(tmp_path) is None
    assert manifest_location(tmp_path)["active"] is None
    assert new_manifest_path(tmp_path) == tmp_path / ".alfrd" / "alfrd.yaml"
    # a .alfrd/ folder holding only run state is not a manifest
    (tmp_path / ".alfrd" / "plans").mkdir(parents=True)
    assert project_manifest(tmp_path) is None


def test_project_root_of_nested_manifest(both):
    assert project_root(both / ".alfrd" / "alfrd.yaml") == both
    assert project_root(both / "alfrd.yaml") == both
    assert project_root(both / ".alfrd.yaml") == both


def test_discover_walks_up_and_prefers_nested(both):
    sub = both / "src" / "deep"
    sub.mkdir(parents=True)
    assert discover_manifest(sub) == (both / ".alfrd" / "alfrd.yaml").resolve()
    assert discover_manifest(both / ".alfrd") == (both / ".alfrd" / "alfrd.yaml").resolve()
    # naming the shadowed file still reads the active one: never read one file and write another
    assert discover_manifest(both / "alfrd.yaml") == (both / ".alfrd" / "alfrd.yaml").resolve()
    assert load_manifest(both).name == "nestedcfg"


def test_relative_paths_resolve_against_project_root(both):
    """Moving the manifest into .alfrd/ must not redirect project paths."""
    from alfrd.execution import load_execution

    _write(both / ".alfrd" / "alfrd.yaml", NESTED_YAML + "execution:\n  plan_csv: plans/my.plan.csv\n")
    cfg = load_execution(both)
    assert Path(cfg.plan_csv).resolve() == (both / "plans" / "my.plan.csv").resolve()


def test_studio_defs_reads_and_writes_active(both):
    from alfrd.studio_defs import manifest_file, save_manifest, studio_manifest

    assert manifest_file(both) == both / ".alfrd" / "alfrd.yaml"
    assert studio_manifest(both)["name"] == "nestedcfg"
    save_manifest(both, NESTED_YAML + "description: edited\n")
    assert "edited" in (both / ".alfrd" / "alfrd.yaml").read_text()
    assert (both / "alfrd.yaml").read_text() == ROOT_YAML, "the shadowed root file is untouched"
    assert (both / ".alfrd" / "alfrd.yaml.bak").read_text() == NESTED_YAML


def test_save_without_manifest_creates_nested(tmp_path):
    from alfrd.studio_defs import save_manifest

    path = save_manifest(tmp_path, ROOT_YAML)
    assert path == tmp_path / ".alfrd" / "alfrd.yaml" and path.read_text() == ROOT_YAML
    assert not (tmp_path / "alfrd.yaml").exists()


def test_root_only_project_keeps_writing_root(tmp_path):
    from alfrd.studio_defs import save_manifest

    _write(tmp_path / "alfrd.yaml", ROOT_YAML)
    save_manifest(tmp_path, ROOT_YAML + "description: x\n")
    assert "description: x" in (tmp_path / "alfrd.yaml").read_text()
    assert not (tmp_path / ".alfrd" / "alfrd.yaml").exists(), "a legacy project is never migrated silently"


def test_history_follows_active_file(both):
    loaded = (both / ".alfrd" / "alfrd.yaml").read_text()
    history.save(both, "alfrd.yaml", NESTED_YAML + "description: a\n", source="studio",
                 base_hash=history.text_hash(loaded))
    assert "description: a" in (both / ".alfrd" / "alfrd.yaml").read_text()
    assert (both / "alfrd.yaml").read_text() == ROOT_YAML
    # stale-hash guard compares against the active file, not the shadowed one
    with pytest.raises(history.Conflict):
        history.save(both, "alfrd.yaml", NESTED_YAML + "description: b\n", source="studio",
                     base_hash=history.text_hash(ROOT_YAML))
    first = history.versions(both, "alfrd.yaml")[-1]["version"]
    history.restore(both, "alfrd.yaml", first, force=True)
    assert (both / ".alfrd" / "alfrd.yaml").read_text() == NESTED_YAML
    assert (both / "alfrd.yaml").read_text() == ROOT_YAML


def test_watcher_maps_only_the_active_manifest(both):
    assert history.tracked_among(both, [".alfrd/alfrd.yaml"]) == ["alfrd.yaml"]
    assert history.tracked_among(both, ["alfrd.yaml", ".alfrd.yaml"]) == [], "shadowed files are not the manifest"


def test_quickstart_reads_active(both):
    from alfrd import quickstart

    text, path = quickstart._manifest_text(both)
    assert path == both / ".alfrd" / "alfrd.yaml" and text == NESTED_YAML


def test_gui_selection_resolves_active(both):
    pytest.importorskip("flask")
    from alfrd.gui.services import resolve_selected_manifest

    nested = (both / ".alfrd" / "alfrd.yaml").resolve()
    assert resolve_selected_manifest(str(both)) == nested
    assert resolve_selected_manifest(str(both / "alfrd.yaml")) == nested
    assert resolve_selected_manifest(str(nested)) == nested


def test_gui_selection_rejects_symlinked_nested(tmp_path):
    pytest.importorskip("flask")
    from alfrd.gui.services import resolve_selected_manifest

    real = _write(tmp_path / "elsewhere.yaml", ROOT_YAML)
    (tmp_path / "proj" / ".alfrd").mkdir(parents=True)
    (tmp_path / "proj" / ".alfrd" / "alfrd.yaml").symlink_to(real)
    with pytest.raises(ValueError, match="symbolic"):
        resolve_selected_manifest(str(tmp_path / "proj"))


def test_repository_root_is_parent_of_state_dir(tmp_path):
    from alfrd.repository import RepositoryService

    root = tmp_path / "repo"
    _write(root / ".alfrd" / "alfrd.yaml", NESTED_YAML)
    record = RepositoryService(tmp_path / "index.json").add(root)
    assert record.root == root.resolve()
    assert record.manifest_path == (root / ".alfrd" / "alfrd.yaml").resolve()


def test_discover_projects_finds_nested_only(tmp_path):
    from alfrd.manifest_default import discover_projects, local_manifest

    _write(tmp_path / "a" / ".alfrd" / "alfrd.yaml", NESTED_YAML)
    _write(tmp_path / "b" / "alfrd.yaml", ROOT_YAML)
    found, capped = discover_projects(tmp_path)
    assert [p.name for p in found] == ["a", "b"] and not capped
    assert local_manifest(tmp_path / "a") == tmp_path / "a" / ".alfrd" / "alfrd.yaml"


def test_create_project_writes_nested_manifest(tmp_path):
    from alfrd.project_creation import create_project
    from alfrd.runtime import RuntimeService, RuntimeStore

    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    service = RuntimeService(store)
    root = tmp_path / "newproj"
    project, _ = create_project(service, root, name="newproj", manifest=".alfrd/alfrd.yaml")
    assert (root / ".alfrd" / "alfrd.yaml").is_file()
    assert not (root / "alfrd.yaml").exists()
    assert Path(project.root_path) == root.resolve()


def test_create_project_refuses_existing_root_manifest(tmp_path):
    from alfrd.project_creation import create_project
    from alfrd.runtime import RuntimeService, RuntimeStore

    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    root = tmp_path / "legacy"
    _write(root / "alfrd.yaml", ROOT_YAML)
    with pytest.raises(FileExistsError, match="alfrd.yaml"):
        create_project(RuntimeService(store), root, name="legacy", manifest=".alfrd/alfrd.yaml")
    assert (root / "alfrd.yaml").read_text() == ROOT_YAML


def test_studio_save_endpoint_reports_active_path(both, tmp_path):
    pytest.importorskip("flask")
    from alfrd.gui import create_app
    from alfrd.gui.services import RuntimeCatalogReader
    from alfrd.manifest_default import register_project_folder
    from alfrd.runtime import RuntimeService, RuntimeStore

    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    service = RuntimeService(store)
    registered = register_project_folder(service, both)
    pid = getattr(registered, "identifier", None) or registered[0].identifier
    app = create_app({"TESTING": True, "SECRET_KEY": "k", "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'c.sqlite'}",
                      "RUNTIME_SERVICE": service, "CATALOG_READER": RuntimeCatalogReader(service),
                      "STUDIO_DEMO": False, "STUDIO_LIVE_INTERVAL": 0})
    client = app.test_client()
    h = {"X-CSRF-Token": client.get("/api/studio/session").get_json()["csrf_token"]}
    loaded = (both / ".alfrd" / "alfrd.yaml").read_text()
    response = client.post(f"/api/studio/projects/{pid}/manifest",
                           json={"text": NESTED_YAML + "description: s\n", "base_hash": history.text_hash(loaded)}, headers=h)
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert body["saved"] == ".alfrd/alfrd.yaml" and body["manifest_location"]["active"] == ".alfrd/alfrd.yaml"
    assert (both / "alfrd.yaml").read_text() == ROOT_YAML


def test_studio_new_project_defaults_to_nested(tmp_path):
    pytest.importorskip("flask")
    from alfrd.gui import create_app
    from alfrd.gui.services import RuntimeCatalogReader
    from alfrd.runtime import RuntimeService, RuntimeStore

    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    service = RuntimeService(store)
    app = create_app({"TESTING": True, "SECRET_KEY": "k", "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'c.sqlite'}",
                      "RUNTIME_SERVICE": service, "CATALOG_READER": RuntimeCatalogReader(service),
                      "STUDIO_DEMO": False, "STUDIO_LIVE_INTERVAL": 0})
    client = app.test_client()
    h = {"X-CSRF-Token": client.get("/api/studio/session").get_json()["csrf_token"]}
    made = client.post("/api/studio/projects/create", json={"path": str(tmp_path / "gui"), "name": "gui"}, headers=h)
    assert made.status_code == 201, made.get_json()
    assert (tmp_path / "gui" / ".alfrd" / "alfrd.yaml").is_file() and not (tmp_path / "gui" / "alfrd.yaml").exists()
    rooted = client.post("/api/studio/projects/create",
                         json={"path": str(tmp_path / "rooted"), "name": "rooted", "manifest": "alfrd.yaml"}, headers=h)
    assert rooted.status_code == 201 and (tmp_path / "rooted" / "alfrd.yaml").is_file()
    bad = client.post("/api/studio/projects/create",
                      json={"path": str(tmp_path / "bad"), "name": "bad", "manifest": "../x.yaml"}, headers=h)
    assert bad.status_code == 400 and not (tmp_path / "bad").exists()
