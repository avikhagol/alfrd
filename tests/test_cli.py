from pathlib import Path

from typer.testing import CliRunner

from alfrd import get_project_dir
from alfrd.cli import alfrd_cli


runner = CliRunner()


def test_cli_help_smoke():
    result = runner.invoke(alfrd_cli, ["--help"])

    assert result.exit_code == 0, result.output
    assert "init" in result.output
    assert "run" in result.output
    assert "inspect" in result.output
    assert "serve" in result.output
    assert "gui" in result.output


def test_cli_command_help_smoke():
    for command in (
        "init",
        "ls",
        "lsp",
        "run",
        "add",
        "rm",
        "inspect",
        "nrun",
        "serve",
        "gui",
    ):
        result = runner.invoke(alfrd_cli, [command, "--help"])
        assert result.exit_code == 0, f"{command}: {result.output}"


def test_cli_init_uses_isolated_project_directory(tmp_path):
    result = runner.invoke(alfrd_cli, ["init", "smoke"])

    assert result.exit_code == 0, result.output
    project_dir = get_project_dir() / "smoke"
    assert project_dir.is_dir()
    assert project_dir.is_relative_to(tmp_path)


def test_cli_rm_rejects_paths_outside_project_directory(tmp_path):
    sentinel = tmp_path / "sentinel"
    sentinel.mkdir()

    result = runner.invoke(alfrd_cli, ["rm", str(sentinel)])

    assert result.exit_code != 0
    assert isinstance(result.exception, ValueError)
    assert sentinel.is_dir()


def test_cli_serve_runs_app_with_explicit_network_settings(monkeypatch):
    calls = []
    configs = []

    class FakeApp:
        def run(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr(
        "alfrd.gui.create_app", lambda config=None: configs.append(config) or FakeApp()
    )

    result = runner.invoke(
        alfrd_cli,
        ["serve", "--host", "127.0.0.2", "--port", "8765", "--debug"],
    )

    assert result.exit_code == 0, result.output
    assert calls == [{"host": "127.0.0.2", "port": 8765, "debug": True}]
    assert configs == [None]


def test_cli_gui_is_a_serve_alias(monkeypatch):
    calls = []

    class FakeApp:
        def run(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr("alfrd.gui.create_app", lambda config=None: FakeApp())

    result = runner.invoke(alfrd_cli, ["gui", "--port", "5050"])

    assert result.exit_code == 0, result.output
    assert calls == [{"host": "127.0.0.1", "port": 5050, "debug": False}]


def test_cli_serve_configures_runtime_database(monkeypatch, tmp_path):
    configs = []

    class FakeApp:
        def run(self, **kwargs):
            pass

    monkeypatch.setattr(
        "alfrd.gui.create_app", lambda config=None: configs.append(config) or FakeApp()
    )
    database = tmp_path / "runtime.sqlite"
    result = runner.invoke(alfrd_cli, ["serve", "--runtime-db", str(database)])

    assert result.exit_code == 0, result.output
    assert configs[0]["RUNTIME_DATABASE"] == str(database.resolve())
    assert configs[0]["RUNTIME_SERVICE"].store.schema_version == 1
    assert configs[0]["CATALOG_READER"].service is configs[0]["RUNTIME_SERVICE"]


def test_cli_manifest_validate_example():
    manifest = Path(__file__).parents[1] / "examples" / "avica_0.3" / "alfrd.yaml"
    result = runner.invoke(alfrd_cli, ["manifest", "validate", str(manifest)])
    assert result.exit_code == 0, result.output
    assert "valid project-manifest-v1" in result.output


def test_cli_import_avica_run(tmp_path):
    source = Path(__file__).parent / "fixtures" / "avica_run" / "reductions"
    manifest = Path(__file__).parents[1] / "examples" / "avica_0.3" / "alfrd.yaml"
    database = tmp_path / "runtime.sqlite"
    result = runner.invoke(
        alfrd_cli,
        [
            "import",
            "avica-run",
            str(source),
            "--project",
            "avica-cli",
            "--manifest",
            str(manifest),
            "--db",
            str(database),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Imported 1 dataset" in result.output
    assert "workflow=avica" in result.output


def test_cli_import_avica_run_reports_duplicate_project(tmp_path):
    source = Path(__file__).parent / "fixtures" / "avica_run" / "reductions"
    manifest = Path(__file__).parents[1] / "examples" / "avica_0.3" / "alfrd.yaml"
    database = tmp_path / "runtime.sqlite"
    args = [
        "import",
        "avica-run",
        str(source),
        "--project",
        "duplicate",
        "--manifest",
        str(manifest),
        "--db",
        str(database),
    ]

    assert runner.invoke(alfrd_cli, args).exit_code == 0
    duplicate = runner.invoke(alfrd_cli, args)
    assert duplicate.exit_code == 1
    assert "AVICA import failed: project 'duplicate' already exists" in duplicate.output


def test_runtime_help_smoke():
    result = runner.invoke(alfrd_cli, ["runtime", "--help"])
    assert result.exit_code == 0, result.output
    for command in ("start", "resume", "retry", "retry-step", "cancel", "logs", "status"):
        assert command in result.output


def _runtime_db(tmp_path):
    return str(tmp_path / "runtime.sqlite")


def test_runtime_start_spawns_worker_and_reports_status(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "alfrd.cli.subprocess.Popen", lambda *args, **kwargs: calls.append((args, kwargs))
    )
    db = _runtime_db(tmp_path)

    from alfrd.runtime import RuntimeService, RuntimeStore

    store = RuntimeStore(db)
    store.initialize()
    service = RuntimeService(store)
    project = service.create_project("demo", tmp_path / "demo")
    workflow = service.create_workflow(project.id, "wf", [{"key": "one", "command": ["true"]}])
    dataset = service.create_dataset(project.id, "a")

    result = runner.invoke(
        alfrd_cli, ["runtime", "start", workflow.id, dataset.id, "--db", db]
    )

    assert result.exit_code == 0, result.output
    assert "status=pending" in result.output
    assert len(calls) == 1


def test_runtime_start_reports_validation_errors_without_spawning(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "alfrd.cli.subprocess.Popen", lambda *args, **kwargs: calls.append((args, kwargs))
    )
    db = _runtime_db(tmp_path)

    result = runner.invoke(
        alfrd_cli, ["runtime", "start", "missing-workflow", "missing-dataset", "--db", db]
    )

    assert result.exit_code != 0
    assert "parameter validation failed" in result.output
    assert calls == []


def test_runtime_cancel_requires_confirmation(tmp_path):
    db = _runtime_db(tmp_path)
    from alfrd.runtime import RuntimeService, RuntimeStore

    store = RuntimeStore(db)
    store.initialize()
    service = RuntimeService(store)
    project = service.create_project("demo", tmp_path / "demo")
    workflow = service.create_workflow(project.id, "wf", [{"key": "one", "command": ["true"]}])
    dataset = service.create_dataset(project.id, "a")
    run = service.create_run(workflow.id, dataset.id)

    declined = runner.invoke(
        alfrd_cli, ["runtime", "cancel", run.id, "--db", db], input="n\n"
    )
    assert declined.exit_code != 0
    assert service.get_run(run.id).status == "pending"

    confirmed = runner.invoke(alfrd_cli, ["runtime", "cancel", run.id, "--db", db, "--yes"])
    assert confirmed.exit_code == 0, confirmed.output
    assert service.get_run(run.id).status == "cancelled"
