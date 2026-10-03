"""alfrd.yaml-driven Studio definitions: template merge, step logs, MS paths, writes."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from alfrd.studio_defs import (
    allowed_file,
    collect_log_files,
    collect_ms_paths,
    expand_pattern,
    save_manifest,
    studio_manifest,
    update_key_values,
)

TREE = Path(__file__).parent / "fixtures" / "avica_tree"


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    shutil.copytree(TREE, root)
    wd = root / "reductions" / "RDV41" / "wd"
    (wd / "wd_X_0742+103" / "VLBI_X.ms" / "ANTENNA").mkdir(parents=True)
    (wd / "wd_X_0742+103" / "VLBI_X.ms" / "ANTENNA" / "avica_avg_casa_log-1.log").write_text("inside an MS: never listed")
    (wd / "VLBI.ms").mkdir()
    diag = wd / "wd_X_0742+103" / "diagnostics_2026-09-08_15-26-08"
    diag.mkdir()
    (diag / "casa.log_2026-09-08-15_26_00").write_text("rpicard\n")
    (root / "casa-20260922-092704.log").write_text("casa\n")
    return root


def test_template_defaults_and_alfrd_yaml_overrides():
    m = studio_manifest(TREE)
    assert m["template"] == "avica"
    assert m["step_order"][0] == "preprocess_fitsidi" and len(m["steps"]) == 9
    assert m["steps"]["fits_to_ms"]["label"] == "Convert FITS to CASA Measurement Set"  # template
    assert m["steps"]["avica_avg"]["logs"] == ["{workdir}/wd_{band}/avica_avg_*log*"]  # alfrd.yaml
    assert m["project_settings"]["field_aliases"]["vasco_avg"] == "avica_avg"
    assert m["overview"]["ms_path"][0] == "{workdir}/wd_{band}_{target}/VLBI_{band}.ms"
    assert m["results"]["calibration_step"] == "rpicard"  # template
    assert m["results"]["calibrated_sources"] == r"^calibrated\s+(.+)$"


def test_step_logs_and_log_artifacts_are_listed_not_walked(tree):
    files = {f["rel"]: f for f in collect_log_files(tree)}
    avg = files["reductions/RDV41/wd/wd_S/avica_avg_casa_log-20260908_180141.log"]
    assert avg["steps"] == ["avica_avg"] and avg["band"] == "S" and avg["workdir"] == "RDV41"
    rp = files["reductions/RDV41/wd/wd_X_0742+103/diagnostics_2026-09-08_15-26-08/casa.log_2026-09-08-15_26_00"]
    assert rp["target"] == "0742+103" and "step:rpicard" in rp["groups"]
    assert "artifact:casa_root_logs" in files["casa-20260922-092704.log"]["groups"]
    crash = files["avica.logs/avica_crash_rpicard.json"]
    assert crash["steps"] == ["rpicard"] and "artifact:crash_snapshots" in crash["groups"]
    assert not any(".ms/" in rel for rel in files)


def test_overview_ms_paths(tree):
    paths = collect_ms_paths(tree)
    own = [p for p in paths if p["target"] == "0742+103"]
    assert own[0]["rel"] == "reductions/RDV41/wd/wd_X_0742+103/VLBI_X.ms" and own[0]["band"] == "X"
    assert any(p["rel"] == "reductions/RDV41/wd/VLBI.ms" and p["workdir"] == "RDV41" for p in paths)


def test_expand_pattern_captures_and_rejects_parent_dirs(tree):
    hits = expand_pattern(tree, "{target_dir}/{project_code}/wd/wd_{band}_{target}", {"target_dir": "reductions"}, kind="directory")
    assert ("reductions/RDV41/wd/wd_X_0742+103", {"project_code": "RDV41", "band": "X", "target": "0742+103"}) in hits
    assert expand_pattern(tree, "../*") == []
    assert expand_pattern(tree, "{workdir}/x.log") == []  # no context → nothing


def test_allowed_file_only_serves_declared_logs(tree):
    assert allowed_file(tree, "reductions/RDV41/wd/wd_S/avica_avg_casa_log-20260908_180141.log").is_file()
    with pytest.raises(PermissionError):
        allowed_file(tree, "avica.inp")
    with pytest.raises(ValueError):
        allowed_file(tree, "../etc/passwd")
    with pytest.raises(FileNotFoundError):
        allowed_file(tree, "avica.logs/missing.log")


def test_update_key_values_keeps_comments_and_order(tmp_path):
    inp = tmp_path / "avica.inp"
    inp.write_text("# AVICA\ntarget_dir = reductions/  # where\nrpicard.mpi_cores = 3\n")
    written = update_key_values(inp, {"rpicard.mpi_cores": 7, "avica_avg.drop_dead_pol": "off", "bad key": 1})
    assert written == {"rpicard.mpi_cores": 7, "avica_avg.drop_dead_pol": "off"}
    assert inp.read_text() == "# AVICA\ntarget_dir = reductions/  # where\nrpicard.mpi_cores = 7\navica_avg.drop_dead_pol = off\n"
    assert (tmp_path / "avica.inp.bak").read_text().endswith("rpicard.mpi_cores = 3\n")


def test_save_manifest_validates_and_keeps_backup(tree):
    with pytest.raises(ValueError):
        save_manifest(tree, "- not a mapping\n")
    with pytest.raises(ValueError):
        save_manifest(tree, "description: no name\n")
    before = (tree / "alfrd.yaml").read_text()
    save_manifest(tree, "name: avica-t-0.3\ntemplate: avica\n")
    assert (tree / "alfrd.yaml.bak").read_text() == before
    assert studio_manifest(tree)["template"] == "avica"


@pytest.fixture
def served(tree, tmp_path):
    pytest.importorskip("flask")
    from alfrd.cli import _connect_startup_project
    from alfrd.gui import create_app
    from alfrd.gui.services import RuntimeCatalogReader
    from alfrd.runtime import RuntimeService, RuntimeStore

    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    service = RuntimeService(store)
    name = _connect_startup_project(service, str(tree))
    assert name == service.get_project_by_name("avica-t-0.3").identifier
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "k",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'catalog.sqlite'}",
        "RUNTIME_SERVICE": service,
        "CATALOG_READER": RuntimeCatalogReader(service),
        "STUDIO_DEFAULT_PROJECT": name,
        "STUDIO_DEMO": False,
    })
    client = app.test_client()
    token = client.get("/api/studio/session").get_json()["csrf_token"]
    return client, token, tree


def test_session_reports_default_project_and_no_demo(served):
    client, _, _ = served
    session = client.get("/api/studio/session").get_json()
    assert session["default_project"].endswith(".proj.avica-t-0.3") and session["demo"] is False


def test_scan_and_file_endpoints(served):
    client, _, _ = served
    scan = client.get("/api/studio/projects/avica-t-0.3/scan").get_json()
    logs = [f for f in scan["files"] if f.get("log")]
    assert any(f["rel"].endswith("wd_S/avica_avg_casa_log-20260908_180141.log") and "text" not in f for f in logs)
    assert any(p["target"] == "0742+103" for p in scan["ms_paths"])
    ok = client.get("/api/studio/projects/avica-t-0.3/file", query_string={"path": "casa-20260922-092704.log"})
    assert ok.status_code == 200 and ok.data == b"casa\n"
    assert client.get("/api/studio/projects/avica-t-0.3/file", query_string={"path": "avica.inp"}).status_code == 403
    assert client.get("/api/studio/projects/avica-t-0.3/file", query_string={"path": "../x"}).status_code == 400


def test_manifest_and_config_writes_need_csrf(served):
    client, token, tree = served
    text = (tree / "alfrd.yaml").read_text().replace("name: avica-t-0.3", "name: avica-t-0.3\n# edited")
    assert client.post("/api/studio/projects/avica-t-0.3/manifest", json={"text": text}).status_code == 403
    saved = client.post("/api/studio/projects/avica-t-0.3/manifest", json={"text": text}, headers={"X-CSRF-Token": token})
    assert saved.status_code == 200 and "# edited" in (tree / "alfrd.yaml").read_text()
    bad = client.post("/api/studio/projects/avica-t-0.3/manifest", json={"text": "a: [1"}, headers={"X-CSRF-Token": token})
    assert bad.status_code == 400

    (tree / "avica.summary.json").write_text(json.dumps({"rows": [{"step": "rpicard", "parameter": "mpi_cores", "source": "default/step", "value": 3}]}))
    res = client.post("/api/studio/avica/avica-t-0.3/config", json={"changes": {"rpicard.mpi_cores": 9}}, headers={"X-CSRF-Token": token})
    assert res.status_code == 200, res.get_json()
    assert "rpicard.mpi_cores = 9" in (tree / "avica.inp").read_text()
    row = json.loads((tree / "avica.summary.json").read_text())["rows"][0]
    assert row["value"] == 9 and row["source"] == "avica.inp/studio"


def test_serve_scopes_studio_to_startup_project_and_projects_forget(tree, tmp_path):
    pytest.importorskip("flask")
    from typer.testing import CliRunner

    from alfrd.cli import _connect_startup_project, _runtime_service, alfrd_cli

    db = str(tmp_path / "rt.sqlite")
    service = _runtime_service(db)
    other = tmp_path / "other"
    other.mkdir()
    (other / "alfrd.yaml").write_text("name: other-proj\n")
    assert _connect_startup_project(service, str(other)) == service.get_project_by_name("other-proj").identifier
    assert _connect_startup_project(service, str(tree)) == service.get_project_by_name("avica-t-0.3").identifier

    runner = CliRunner()
    listed = runner.invoke(alfrd_cli, ["projects", "list", "--db", db])
    assert listed.exit_code == 0 and "other-proj" in listed.output and "avica-t-0.3" in listed.output
    gone = runner.invoke(alfrd_cli, ["projects", "forget", "other-proj", "--db", db, "--yes"])
    assert gone.exit_code == 0, gone.output
    assert [p.name for p in _runtime_service(db).list_projects()] == ["avica-t-0.3"]
    assert (other / "alfrd.yaml").is_file()  # files untouched
    assert runner.invoke(alfrd_cli, ["projects", "forget", "nope", "--db", db, "--yes"]).exit_code != 0


def test_forget_and_quit_endpoints(served, tmp_path):
    import threading

    client, token, _ = served
    app = client.application
    service = app.config["RUNTIME_SERVICE"]
    other = tmp_path / "other2"
    other.mkdir()
    (other / "alfrd.yaml").write_text("name: other2\n")
    service.register_manifest(other / "alfrd.yaml", root_path=other, create_root=False)

    assert client.post("/api/studio/projects/other2/forget").status_code == 403  # CSRF
    res = client.post("/api/studio/projects/other2/forget", headers={"X-CSRF-Token": token})
    assert res.status_code == 200 and res.get_json()["forgotten"] == "other2"
    assert (other / "alfrd.yaml").is_file()
    assert client.post("/api/studio/projects/other2/forget", headers={"X-CSRF-Token": token}).status_code == 404

    assert client.get("/api/studio/session").get_json()["can_quit"] is False
    assert client.post("/api/studio/quit", headers={"X-CSRF-Token": token}).status_code == 501
    stopped = threading.Event()
    app.config["STUDIO_SHUTDOWN"] = stopped.set
    assert client.get("/api/studio/session").get_json()["can_quit"] is True
    assert client.post("/api/studio/quit").status_code == 403
    assert client.post("/api/studio/quit", headers={"X-CSRF-Token": token}).get_json() == {"stopping": True}
    assert stopped.wait(2)


def test_default_manifest_when_folder_has_none(tree, tmp_path, monkeypatch):
    """No local alfrd.yaml: the packaged default is used (name = folder name); a local file replaces it."""
    pytest.importorskip("flask")
    from alfrd.avica_layout import collect_studio_files, manifest_avica
    from alfrd.cli import _connect_startup_project
    from alfrd.manifest_default import default_manifest_path, default_manifest_text, manifest_data
    from alfrd.runtime import RuntimeService, RuntimeStore

    monkeypatch.delenv("ALFRD_DEFAULT_MANIFEST", raising=False)
    assert default_manifest_path() is not None
    (tree / "alfrd.yaml").unlink()
    data, path, is_default = manifest_data(tree)
    assert is_default and data["name"] == "proj" and path == default_manifest_path()
    assert "name: \"proj\"" in default_manifest_text(tree)
    assert studio_manifest(tree)["steps"]["rpicard"]["label"] == "VLBI calibration (rPicard)"
    assert manifest_avica(tree)["target_dir"] == "reductions/"
    scan = collect_studio_files(tree, log_tail=0)
    first = next(f for f in scan["files"] if f["rel"] == "alfrd.yaml")
    assert scan["default_manifest"] and first["default"] and "name: \"proj\"" in first["text"]
    assert any(f["rel"].endswith("_result.csv") for f in scan["files"])

    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    service = RuntimeService(store)
    assert _connect_startup_project(service, str(tree)) == service.get_project_by_name("proj").identifier

    # A local alfrd.yaml supersedes the default completely.
    (tree / "alfrd.yaml").write_text("version: 1\nname: mine\n")
    data, path, is_default = manifest_data(tree)
    assert not is_default and data["name"] == "mine" and manifest_avica(tree) == {}
    assert collect_studio_files(tree, log_tail=0)["default_manifest"] is False


def test_default_manifest_env_override_and_cwd_heuristic(tmp_path, monkeypatch):
    from alfrd.cli import _connect_startup_project
    from alfrd.manifest_default import default_manifest_path, looks_like_project
    from alfrd.runtime import RuntimeService, RuntimeStore

    custom = tmp_path / "mydefault.yaml"
    custom.write_text("version: 1\nname: x\n")
    monkeypatch.setenv("ALFRD_DEFAULT_MANIFEST", str(custom))
    assert default_manifest_path() == custom
    other = tmp_path / "other"
    other.mkdir()
    (other / "notes.txt").write_text("not an AVICA folder\n")
    assert not looks_like_project(other)
    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    service = RuntimeService(store)
    monkeypatch.chdir(other)
    assert _connect_startup_project(service, None) is None  # a random cwd is not registered
    assert _connect_startup_project(service, str(other)) is not None  # --project always uses the default
    (other / "avica.inp").write_text("target_dir = reductions/\n")
    assert looks_like_project(other)


def test_empty_folder_starts_an_avica_project_from_scratch(tmp_path, monkeypatch):
    """`alfrd serve` in a completely empty folder opens it with the default (AVICA) alfrd.yaml."""
    from alfrd.avica_layout import scan_layout
    from alfrd.cli import _connect_startup_project
    from alfrd.manifest_default import is_empty_folder, looks_like_project, manifest_data
    from alfrd.runtime import RuntimeService, RuntimeStore

    monkeypatch.delenv("ALFRD_DEFAULT_MANIFEST", raising=False)
    empty = tmp_path / "new_avica"
    empty.mkdir()
    (empty / ".alfrd").mkdir()  # dot-entries do not count
    assert is_empty_folder(empty) and looks_like_project(empty)
    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    service = RuntimeService(store)
    monkeypatch.chdir(empty)
    identifier = _connect_startup_project(service, None)
    assert identifier is not None
    assert service.get_project_by_identifier(identifier).name == "new_avica"
    data, _path, is_default = manifest_data(empty)
    assert is_default and data["template"] == "avica" and data["name"] == "new_avica"
    layout = scan_layout(empty)  # nothing on disk yet: no reductions/, no result CSVs
    assert layout["result_csvs"] == [] and layout["project_codes"] == []
    assert not (empty / "alfrd.yaml").exists()  # nothing is written until Project settings → Save


def test_rediscover_restores_forgotten_and_serve_folder(tree, tmp_path):
    """Studio settings → Rediscover: connect forgotten projects again without restarting alfrd serve."""
    pytest.importorskip("flask")
    from alfrd.cli import _connect_startup_project
    from alfrd.gui import create_app
    from alfrd.gui.services import RuntimeCatalogReader
    from alfrd.runtime import RuntimeService, RuntimeStore

    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    service = RuntimeService(store)
    name = _connect_startup_project(service, str(tree))
    other = tmp_path / "other3"
    other.mkdir()
    (other / "avica.inp").write_text("target_dir = reductions/\n")  # no alfrd.yaml: default manifest
    app = create_app({
        "TESTING": True, "SECRET_KEY": "k",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'catalog.sqlite'}",
        "RUNTIME_SERVICE": service, "CATALOG_READER": RuntimeCatalogReader(service),
        "STUDIO_DEFAULT_PROJECT": name, "STUDIO_PROJECTS": [name], "STUDIO_START_FOLDER": str(tree),
        "STUDIO_DEMO": False,
    })
    client = app.test_client()
    token = client.get("/api/studio/session").get_json()["csrf_token"]
    h = {"X-CSRF-Token": token}
    assert client.get("/api/studio/projects/rediscover").get_json()["candidates"] == []
    from alfrd.manifest_default import register_project_folder

    other_id = register_project_folder(service, other)[0].identifier
    assert client.post(f"/api/studio/projects/{name}/forget", headers=h).status_code == 200
    assert client.post(f"/api/studio/projects/{other_id}/forget", headers=h).status_code == 200
    assert client.get("/api/studio/session").get_json()["default_project"] is None

    cands = client.get("/api/studio/projects/rediscover").get_json()["candidates"]
    assert {c["root"] for c in cands} == {str(tree.resolve()), str(other.resolve())}
    assert next(c for c in cands if c["root"] == str(tree.resolve()))["start"] is True
    assert next(c for c in cands if c["root"] == str(other.resolve()))["default_manifest"] is True

    assert client.post("/api/studio/projects/rediscover", json={"root": "/etc"}, headers=h).status_code == 404
    assert client.post("/api/studio/projects/rediscover", json={}).status_code == 403  # CSRF
    one = client.post("/api/studio/projects/rediscover", json={"root": str(other)}, headers=h).get_json()
    assert [r["name"] for r in one["restored"]] == ["other3"] and one["restored"][0]["default_manifest"]
    rest = client.post("/api/studio/projects/rediscover", json={}, headers=h).get_json()
    assert [r["identifier"] for r in rest["restored"]] == [name] and rest["default_project"] == name
    session = client.get("/api/studio/session").get_json()
    assert session["default_project"] == name and name in session["projects"]
    assert client.get(f"/api/studio/projects/{name}/scan").status_code == 200
    assert client.get("/api/studio/projects/rediscover").get_json()["candidates"] == []
