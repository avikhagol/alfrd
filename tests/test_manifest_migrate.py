"""Moving the configuration to .alfrd/alfrd.yaml: plan, conflict, force, history, CLI, Studio API."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from alfrd import history, manifest_migrate as migrate
from alfrd.manifest_paths import manifest_location, project_manifest

ROOT_YAML = "version: 1\nname: rootcfg\n# keep me\n"
NESTED_YAML = "version: 1\nname: nestedcfg\n"


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture()
def legacy(tmp_path):
    root = tmp_path / "proj"
    _write(root / "alfrd.yaml", ROOT_YAML)
    return root


def test_plan_move_writes_nothing(legacy):
    info = migrate.plan(legacy)
    assert info["status"] == "move" and info["source"] == "alfrd.yaml" and info["rename_to"] == "alfrd.yaml.migrated"
    assert not (legacy / ".alfrd").exists() and (legacy / "alfrd.yaml").read_text() == ROOT_YAML


def test_apply_copies_byte_for_byte_and_renames_old(legacy):
    result = migrate.apply(legacy)
    assert result["done"] and result["manifest_location"]["active"] == ".alfrd/alfrd.yaml"
    assert (legacy / ".alfrd" / "alfrd.yaml").read_text() == ROOT_YAML  # comments survive
    assert not (legacy / "alfrd.yaml").exists() and (legacy / "alfrd.yaml.migrated").read_text() == ROOT_YAML
    assert project_manifest(legacy) == legacy / ".alfrd" / "alfrd.yaml"


def test_keep_shadow_leaves_the_old_file(legacy):
    migrate.apply(legacy, keep="shadow")
    assert (legacy / "alfrd.yaml").read_text() == ROOT_YAML
    location = manifest_location(legacy)
    assert location["active"] == ".alfrd/alfrd.yaml" and [f["rel"] for f in location["files"] if f["shadowed"]] == ["alfrd.yaml"]


def test_legacy_alias_is_moved(tmp_path):
    root = tmp_path / "p"
    _write(root / ".alfrd.yaml", ROOT_YAML)
    migrate.apply(root)
    assert (root / ".alfrd" / "alfrd.yaml").read_text() == ROOT_YAML and (root / ".alfrd.yaml.migrated").is_file()


def test_invalid_manifest_is_refused_and_untouched(tmp_path):
    root = tmp_path / "p"
    _write(root / "alfrd.yaml", "version: 1\n")  # no name
    assert migrate.plan(root)["valid"] is False
    with pytest.raises(migrate.MigrateError, match="name"):
        migrate.apply(root)
    assert (root / "alfrd.yaml").is_file() and not (root / ".alfrd").exists()


def test_nothing_to_do(tmp_path):
    root = tmp_path / "p"
    assert migrate.plan(root)["status"] == "no_manifest"
    _write(root / ".alfrd" / "alfrd.yaml", NESTED_YAML)
    assert migrate.plan(root)["status"] == "nothing"
    with pytest.raises(migrate.MigrateError):
        migrate.apply(root)


def test_conflict_needs_force_and_keeps_a_backup(legacy):
    _write(legacy / ".alfrd" / "alfrd.yaml", NESTED_YAML)
    info = migrate.plan(legacy)
    assert info["status"] == "conflict" and "-name: nestedcfg" in info["diff"] and "+name: rootcfg" in info["diff"]
    with pytest.raises(migrate.MigrateConflict) as caught:
        migrate.apply(legacy)
    assert caught.value.plan["status"] == "conflict"
    assert (legacy / ".alfrd" / "alfrd.yaml").read_text() == NESTED_YAML and (legacy / "alfrd.yaml").read_text() == ROOT_YAML
    migrate.apply(legacy, force=True)
    assert (legacy / ".alfrd" / "alfrd.yaml").read_text() == ROOT_YAML
    assert (legacy / ".alfrd" / "alfrd.yaml.bak").read_text() == NESTED_YAML  # nothing is lost
    assert "migrate" in {e["source"] for e in history.versions(legacy, "alfrd.yaml")}


def test_identical_shadowed_file_is_only_renamed(legacy):
    _write(legacy / ".alfrd" / "alfrd.yaml", ROOT_YAML)
    assert migrate.plan(legacy)["status"] == "cleanup"
    migrate.apply(legacy)
    assert not (legacy / "alfrd.yaml").exists() and (legacy / "alfrd.yaml.migrated").is_file()
    assert (legacy / ".alfrd" / "alfrd.yaml").read_text() == ROOT_YAML


def test_rename_never_overwrites_an_earlier_migrated_file(legacy):
    _write(legacy / "alfrd.yaml.migrated", "older\n")
    migrate.apply(legacy)
    assert (legacy / "alfrd.yaml.migrated").read_text() == "older\n"
    assert len(list(legacy.glob("alfrd.yaml.*.migrated"))) == 1


def test_history_baseline_is_kept_before_the_move(legacy):
    migrate.apply(legacy)
    texts = [history.read_version(legacy, "alfrd.yaml", e["version"]) for e in history.versions(legacy, "alfrd.yaml")]
    assert ROOT_YAML in texts


def test_cli_dry_run_then_apply(legacy):
    from alfrd.cli import alfrd_cli

    runner = CliRunner()
    dry = runner.invoke(alfrd_cli, ["manifest", "migrate", str(legacy), "--dry-run"])
    assert dry.exit_code == 0 and "move" in dry.output and (legacy / "alfrd.yaml").exists() and not (legacy / ".alfrd").exists()
    done = runner.invoke(alfrd_cli, ["manifest", "migrate", str(legacy)])
    assert done.exit_code == 0, done.output
    assert (legacy / ".alfrd" / "alfrd.yaml").is_file()
    again = runner.invoke(alfrd_cli, ["manifest", "migrate", str(legacy)])
    assert again.exit_code == 0 and "nothing" in again.output


def test_cli_conflict_exits_nonzero(legacy):
    from alfrd.cli import alfrd_cli

    _write(legacy / ".alfrd" / "alfrd.yaml", NESTED_YAML)
    result = CliRunner().invoke(alfrd_cli, ["manifest", "migrate", str(legacy)])
    assert result.exit_code == 1 and "--force" in result.output
    assert (legacy / ".alfrd" / "alfrd.yaml").read_text() == NESTED_YAML


def test_cli_projects_create_config_location(tmp_path):
    from alfrd.cli import alfrd_cli

    runner = CliRunner()
    db = str(tmp_path / "r.sqlite")
    nested = runner.invoke(alfrd_cli, ["projects", "create", str(tmp_path / "a"), "--db", db])
    assert nested.exit_code == 0, nested.output
    assert (tmp_path / "a" / ".alfrd" / "alfrd.yaml").is_file() and not (tmp_path / "a" / "alfrd.yaml").exists()
    rooted = runner.invoke(alfrd_cli, ["projects", "create", str(tmp_path / "b"), "--config-location", "root", "--db", db])
    assert rooted.exit_code == 0, rooted.output
    assert (tmp_path / "b" / "alfrd.yaml").is_file() and not (tmp_path / "b" / ".alfrd" / "alfrd.yaml").exists()
    bad = runner.invoke(alfrd_cli, ["projects", "create", str(tmp_path / "c"), "--config-location", "x", "--db", db])
    assert bad.exit_code != 0


def test_studio_api_location_preview_and_migrate(legacy, tmp_path):
    pytest.importorskip("flask")
    from alfrd.gui import create_app
    from alfrd.gui.services import RuntimeCatalogReader
    from alfrd.manifest_default import register_project_folder
    from alfrd.runtime import RuntimeService, RuntimeStore

    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    service = RuntimeService(store)
    registered = register_project_folder(service, legacy)
    pid = getattr(registered, "identifier", None) or registered[0].identifier
    app = create_app({"TESTING": True, "SECRET_KEY": "k", "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'c.sqlite'}",
                      "RUNTIME_SERVICE": service, "CATALOG_READER": RuntimeCatalogReader(service),
                      "STUDIO_DEMO": False, "STUDIO_LIVE_INTERVAL": 0})
    client = app.test_client()
    h = {"X-CSRF-Token": client.get("/api/studio/session").get_json()["csrf_token"]}
    base = f"/api/studio/projects/{pid}/manifest"
    where = client.get(f"{base}/location").get_json()
    assert where["manifest_location"]["active"] == "alfrd.yaml" and where["migration"]["status"] == "move"
    preview = client.post(f"{base}/migrate", json={"preview": True}, headers=h)
    assert preview.status_code == 200 and preview.get_json()["status"] == "move" and not (legacy / ".alfrd" / "alfrd.yaml").exists()
    assert client.post(f"{base}/migrate", json={}).status_code == 403  # no CSRF token
    done = client.post(f"{base}/migrate", json={}, headers=h)
    assert done.status_code == 200, done.get_json()
    assert done.get_json()["manifest_location"]["active"] == ".alfrd/alfrd.yaml"
    again = client.post(f"{base}/migrate", json={}, headers=h)
    assert again.status_code == 400
