"""`alfrd serve` in a parent folder: sub-project discovery and the server folder browser."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from alfrd.cli import alfrd_cli
from alfrd.manifest_default import discover_projects, skip_dir

runner = CliRunner()


def _project(folder: Path, name: str, hidden: bool = False) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / (".alfrd.yaml" if hidden else "alfrd.yaml")).write_text(f"name: {name}\n", encoding="utf-8")
    return folder


@pytest.fixture
def parent(tmp_path: Path) -> Path:
    """data_reductions/{pipe_comparison, alma, deep/nested/too_deep, skipped folders}."""
    root = tmp_path / "data_reductions"
    _project(root / "pipe_comparison", "pipe-comparison")
    _project(root / "alma", "alma-cycle9", hidden=True)
    _project(root / "group" / "vlba", "vlba-run")  # depth 2
    _project(root / "deep" / "nested" / "too_deep", "too-deep")  # depth 3
    _project(root / "alma" / "sub" / "inner", "inner")  # inside a project: belongs to it
    _project(root / "obs.ms" / "x", "in-ms")
    _project(root / "raw" / "x", "in-raw")
    _project(root / "tmp_work" / "x", "in-tmp")
    _project(root / "calibration_tables" / "x", "in-cal")
    _project(root / ".cache" / "x", "in-dot")
    (root / "empty").mkdir()
    (root / "notes.txt").write_text("not a folder")
    return root


def test_discover_projects_depth_and_skip_rules(parent: Path):
    found, capped = discover_projects(parent)
    assert not capped
    assert [p.relative_to(parent).as_posix() for p in found] == ["alma", "group/vlba", "pipe_comparison"]
    deeper, _ = discover_projects(parent, depth=3)
    assert "deep/nested/too_deep" in [p.relative_to(parent).as_posix() for p in deeper]
    assert "alma/sub/inner" not in [p.relative_to(parent).as_posix() for p in deeper]
    shallow, _ = discover_projects(parent, depth=1)
    assert [p.name for p in shallow] == ["alma", "pipe_comparison"]
    assert discover_projects(parent, depth=0) == ([], False)
    assert all(skip_dir(n) for n in ("x.ms", "X.MS", "raw", "tmp_1", "calibration_tables", ".git"))
    assert not skip_dir("reductions")


def test_discover_projects_cap_and_unreadable(parent: Path, tmp_path: Path):
    wide = tmp_path / "wide"
    for i in range(30):
        (wide / f"d{i:02d}" / "x").mkdir(parents=True)
    _project(wide / "zz" / "p", "late")
    found, capped = discover_projects(wide, max_dirs=5)
    assert capped and found == []
    locked = parent / "locked"
    _project(locked / "p", "hidden-away")
    real = os.scandir

    def scandir(path="."):
        if Path(path) == locked:
            raise PermissionError(13, "Permission denied", str(path))
        return real(path)

    import alfrd.manifest_default as md

    md_os = md.os
    try:
        md_os.scandir = scandir
        found, _ = discover_projects(parent)
    finally:
        md_os.scandir = real
    assert "locked/p" not in [p.relative_to(parent).as_posix() for p in found]
    assert "alma" in [p.name for p in found]


def _serve(monkeypatch, tmp_path: Path, cwd: Path, *args: str):
    configs = []

    class FakeApp:
        def run(self, **kwargs):
            pass

    monkeypatch.setattr("alfrd.gui.create_app", lambda config=None: configs.append(config) or FakeApp())
    monkeypatch.setattr("alfrd.cli.webbrowser.open", lambda url: True)
    monkeypatch.chdir(cwd)
    result = runner.invoke(alfrd_cli, ["serve", "--no-browser", "--runtime-db", str(tmp_path / "rt.sqlite"), *args])
    assert result.exit_code == 0, result.output
    return configs[0], result.output


def test_serve_in_parent_folder_registers_sub_projects(monkeypatch, tmp_path: Path, parent: Path):
    pytest.importorskip("flask")
    config, output = _serve(monkeypatch, tmp_path, parent)
    service = config["RUNTIME_SERVICE"]
    names = {p.identifier: p.name for p in service.list_projects()}
    assert sorted(names.values()) == ["alma-cycle9", "pipe-comparison", "vlba-run"]
    assert set(config["STUDIO_PROJECTS"]) == set(names)
    # First alphabetically (by folder): alma.
    assert names[config["STUDIO_DEFAULT_PROJECT"]] == "alma-cycle9"
    assert config["STUDIO_START_FOLDER"] is None
    assert config["STUDIO_DISCOVERED_FOLDERS"] == [str(parent / "alma"), str(parent / "group" / "vlba"), str(parent / "pipe_comparison")]
    assert f"Project pipe-comparison: {parent / 'pipe_comparison'}" in output
    assert "too-deep" not in output and "inner" not in output

    # Second start: same projects (already registered), plus --all-projects shows everything.
    other = _project(tmp_path / "elsewhere", "elsewhere")
    config2, _ = _serve(monkeypatch, tmp_path, other)
    assert config2["STUDIO_PROJECTS"] == [config2["STUDIO_DEFAULT_PROJECT"]]
    config3, output3 = _serve(monkeypatch, tmp_path, parent, "--all-projects")
    assert config3["STUDIO_PROJECTS"] is None
    assert len(config3["RUNTIME_SERVICE"].list_projects()) == 4


def test_serve_discovery_options(monkeypatch, tmp_path: Path, parent: Path):
    pytest.importorskip("flask")
    config, output = _serve(monkeypatch, tmp_path, parent, "--no-discover")
    assert config["STUDIO_PROJECTS"] is None and config["STUDIO_DEFAULT_PROJECT"] is None
    assert config["RUNTIME_SERVICE"].list_projects() == []
    config, _ = _serve(monkeypatch, tmp_path, parent, "--discover-depth", "1")
    assert sorted(p.name for p in config["RUNTIME_SERVICE"].list_projects()) == ["alma-cycle9", "pipe-comparison"]


def test_serve_in_a_project_does_not_split_it(monkeypatch, tmp_path: Path, parent: Path):
    pytest.importorskip("flask")
    config, output = _serve(monkeypatch, tmp_path, parent / "alma")
    service = config["RUNTIME_SERVICE"]
    assert [p.name for p in service.list_projects()] == ["alma-cycle9"]
    assert config["STUDIO_START_FOLDER"] == str((parent / "alma").resolve())
    assert config["STUDIO_DISCOVERED_FOLDERS"] == []


# ---------------------------------------------------------------------------
# GET /api/studio/fs/list


@pytest.fixture
def app_client(tmp_path: Path, parent: Path):
    pytest.importorskip("flask")
    from alfrd.gui import create_app
    from alfrd.gui.services import RuntimeCatalogReader
    from alfrd.runtime import RuntimeService, RuntimeStore

    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    service = RuntimeService(store)
    app = create_app({
        "TESTING": True, "SECRET_KEY": "k",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'catalog.sqlite'}",
        "RUNTIME_SERVICE": service, "CATALOG_READER": RuntimeCatalogReader(service),
        "STUDIO_DEFAULT_PROJECT": None, "STUDIO_DEMO": False,
        "STUDIO_DISCOVERED_FOLDERS": [str(parent / "alma"), str(parent / "pipe_comparison")],
    })
    client = app.test_client()
    token = client.get("/api/studio/session").get_json()["csrf_token"]
    return app, client, {"X-CSRF-Token": token}


def test_fs_list_lists_folders_and_projects(app_client, parent: Path):
    _, client, h = app_client
    assert client.get("/api/studio/session").get_json()["can_browse"] is True
    body = client.get("/api/studio/fs/list", headers=h).get_json()  # default: parent of discovered projects
    assert body["path"] == str(parent.resolve())
    assert body["parent"] == str(parent.resolve().parent)
    names = [e["name"] for e in body["entries"]]
    assert names == sorted(names, key=str.lower) and "notes.txt" not in names and ".cache" not in names
    alma = next(e for e in body["entries"] if e["name"] == "alma")
    assert alma["is_project"] and alma["manifest_name"] == "alma-cycle9" and not alma["is_ms"]
    ms = next(e for e in body["entries"] if e["name"] == "obs.ms")
    assert ms["is_ms"] and not ms["is_project"]
    assert not next(e for e in body["entries"] if e["name"] == "empty")["is_project"]
    assert body["truncated"] is False and body["is_project"] is False
    hidden = client.get("/api/studio/fs/list", query_string={"path": str(parent), "hidden": "1"}, headers=h).get_json()
    assert ".cache" in [e["name"] for e in hidden["entries"]]
    inner = client.get("/api/studio/fs/list", query_string={"path": str(parent / "alma")}, headers=h).get_json()
    assert inner["is_project"] and inner["manifest_name"] == "alma-cycle9"


def test_fs_list_errors_and_security(app_client, parent: Path, tmp_path: Path, monkeypatch):
    app, client, h = app_client
    assert client.get("/api/studio/fs/list").status_code == 403  # no CSRF token
    assert client.get("/api/studio/fs/list", headers={"X-CSRF-Token": "wrong"}).status_code == 403
    far = client.get("/api/studio/fs/list", headers=h, environ_base={"REMOTE_ADDR": "10.0.0.5"})
    assert far.status_code == 403
    missing = client.get("/api/studio/fs/list", query_string={"path": str(tmp_path / "nope")}, headers=h)
    assert missing.status_code == 404 and "does not exist" in missing.get_json()["error"]["message"]
    notdir = client.get("/api/studio/fs/list", query_string={"path": str(parent / "notes.txt")}, headers=h)
    assert notdir.status_code == 400
    locked = tmp_path / "locked"
    locked.mkdir()
    real = os.scandir

    def scandir(path="."):
        if Path(path) == locked:
            raise PermissionError(13, "Permission denied", str(path))
        return real(path)

    monkeypatch.setattr(os, "scandir", scandir)
    denied = client.get("/api/studio/fs/list", query_string={"path": str(locked)}, headers=h)
    monkeypatch.setattr(os, "scandir", real)
    assert denied.status_code == 403 and "Permission denied" in denied.get_json()["error"]["message"]
    app.config["RUNTIME_MUTATIONS_ENABLED"] = False
    assert client.get("/api/studio/session").get_json()["can_browse"] is False
    assert client.get("/api/studio/fs/list", headers=h).status_code == 403


def test_fs_list_truncates(app_client, tmp_path: Path, monkeypatch):
    _, client, h = app_client
    import alfrd.gui.studio as studio_mod

    many = tmp_path / "many"
    for i in range(12):
        (many / f"d{i:02d}").mkdir(parents=True)
    monkeypatch.setattr(studio_mod, "FS_LIST_LIMIT", 5)
    body = client.get("/api/studio/fs/list", query_string={"path": str(many)}, headers=h).get_json()
    assert body["truncated"] is True and [e["name"] for e in body["entries"]] == ["d00", "d01", "d02", "d03", "d04"]


def test_discovered_folders_are_rediscover_candidates(app_client, parent: Path):
    app, client, h = app_client
    from alfrd.manifest_default import register_project_folder

    service = app.config["RUNTIME_SERVICE"]
    alma = register_project_folder(service, parent / "alma")[0]
    cands = client.get("/api/studio/projects/rediscover").get_json()["candidates"]
    assert [c["root"] for c in cands] == [str((parent / "pipe_comparison").resolve())]
    assert cands[0]["discovered"] is True
    assert client.post(f"/api/studio/projects/{alma.identifier}/forget", headers=h).status_code == 200
    res = client.post("/api/studio/projects/rediscover", json={}, headers=h).get_json()
    assert sorted(r["name"] for r in res["restored"]) == ["alma-cycle9", "pipe-comparison"]
    assert res["default_project"] is not None


def test_connect_folder_without_alfrd_yaml_uses_default(app_client, tmp_path: Path):
    """Import → Connect / Browse: a folder without alfrd.yaml connects with the default manifest."""
    app, client, h = app_client
    assert client.get("/api/studio/session").get_json()["default_manifest"] is True
    bare = tmp_path / "0742+103_reduction"
    (bare / "avica.logs").mkdir(parents=True)
    res = client.post("/api/projects/connect", json={"path": str(bare)}, headers=h)
    assert res.status_code == 201, res.get_json()
    body = res.get_json()
    assert body["default_manifest"] is True and body["name"] == "0742+103_reduction"
    assert body["root_path"] == str(bare.resolve())
    scan = client.get(f"/api/studio/projects/{body['identifier']}/scan")
    assert scan.status_code == 200
    # Connecting again returns the same project; a local alfrd.yaml is reported as not default.
    again = client.post("/api/projects/connect", json={"path": str(bare)}, headers=h).get_json()
    assert again["identifier"] == body["identifier"]
    local = _project(tmp_path / "local", "local-one")
    assert client.post("/api/projects/connect", json={"path": str(local)}, headers=h).get_json()["default_manifest"] is False
    missing = client.post("/api/projects/connect", json={"path": str(tmp_path / "nope")}, headers=h)
    assert missing.status_code == 400


def test_connected_project_joins_server_scope(app_client, tmp_path: Path):
    """Connect from the Studio: the project stays in STUDIO_PROJECTS (visible after a reload)."""
    app, client, h = app_client
    app.config["STUDIO_PROJECTS"] = []
    bare = tmp_path / "bare"
    bare.mkdir()
    ident = client.post("/api/projects/connect", json={"path": str(bare)}, headers=h).get_json()["identifier"]
    assert client.get("/api/studio/session").get_json()["projects"] == [ident]
    client.post("/api/projects/connect", json={"path": str(bare)}, headers=h)
    assert client.get("/api/studio/session").get_json()["projects"] == [ident]


def test_project_visibility_hidden_shown_opened(app_client, parent: Path):
    """Studio settings → Known projects: switch opened / shown / hidden without re-connecting."""
    app, client, h = app_client
    from alfrd.manifest_default import register_project_folder

    service = app.config["RUNTIME_SERVICE"]
    alma = register_project_folder(service, parent / "alma")[0].identifier
    pipe = register_project_folder(service, parent / "pipe_comparison")[0].identifier
    app.config["STUDIO_PROJECTS"] = [pipe]
    app.config["STUDIO_DEFAULT_PROJECT"] = pipe
    url = lambda key: f"/api/studio/projects/{key}/visibility"  # noqa: E731
    session = lambda: client.get("/api/studio/session").get_json()  # noqa: E731

    res = client.post(url(alma), json={"state": "shown"}, headers=h)
    assert res.status_code == 200 and res.get_json()["projects"] == [pipe, alma]
    assert session()["default_project"] == pipe
    client.post(url(alma), json={"state": "shown"}, headers=h)  # idempotent
    assert session()["projects"] == [pipe, alma]

    body = client.post(url(alma), json={"state": "opened"}, headers=h).get_json()
    assert body["default_project"] == alma and session()["projects"] == [pipe, alma]

    body = client.post(url(alma), json={"state": "shown"}, headers=h).get_json()
    assert body["default_project"] is None and body["projects"] == [pipe, alma]

    client.post(url(pipe), json={"state": "opened"}, headers=h)
    body = client.post(url(pipe), json={"state": "hidden"}, headers=h).get_json()
    assert body["projects"] == [alma] and body["default_project"] is None
    # Still remembered: runtime database untouched, no rediscover candidate.
    assert {p.identifier for p in service.list_projects()} == {alma, pipe}

    # --all-projects (scope None): hiding one turns the scope into an explicit list.
    app.config["STUDIO_PROJECTS"] = None
    assert client.post(url(pipe), json={"state": "shown"}, headers=h).get_json()["projects"] is None
    assert client.post(url(alma), json={"state": "hidden"}, headers=h).get_json()["projects"] == [pipe]

    assert client.post(url(alma), json={"state": "bogus"}, headers=h).status_code == 400
    assert client.post(url("nope"), json={"state": "shown"}, headers=h).status_code == 404
    assert client.post(url(alma), json={"state": "shown"}).status_code == 403  # no CSRF token
