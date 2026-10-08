from pathlib import Path

import pytest
from typer.testing import CliRunner

from alfrd.cli import alfrd_cli


runner = CliRunner()


def test_cli_help_smoke():
    result = runner.invoke(alfrd_cli, ["--help"])

    assert result.exit_code == 0, result.output
    assert "runtime" in result.output
    assert "serve" in result.output
    assert "gui" in result.output
    assert "studio" in result.output


def test_cli_command_help_smoke():
    for command in (
        "serve",
        "gui",
        "studio",
        "url",
        "projects",
        "runtime",
        "manifest",
        "import",
        "plan",
        "plugin",
    ):
        result = runner.invoke(alfrd_cli, [command, "--help"])
        assert result.exit_code == 0, f"{command}: {result.output}"


def test_cli_serve_runs_app_with_explicit_network_settings(monkeypatch):
    calls = []
    configs = []

    class FakeApp:
        def run(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr(
        "alfrd.gui.create_app", lambda config=None: configs.append(config) or FakeApp()
    )
    monkeypatch.setattr("alfrd.cli.webbrowser.open", lambda url: True)

    result = runner.invoke(
        alfrd_cli,
        ["serve", "--host", "127.0.0.2", "--port", "8765", "--debug"],
    )

    assert result.exit_code == 0, result.output
    assert calls == [{"host": "127.0.0.2", "port": 8765, "debug": True}]
    assert configs[0]["RUNTIME_SERVICE"] is not None
    assert "ALFRD Studio: http://127.0.0.2:8765/studio/" in result.output
    assert "/dashboard/" not in result.output


def test_cli_gui_is_a_serve_alias(monkeypatch):
    calls = []

    class FakeApp:
        def run(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr("alfrd.gui.create_app", lambda config=None: FakeApp())
    monkeypatch.setattr("alfrd.cli.webbrowser.open", lambda url: True)
    monkeypatch.setattr("alfrd.cli._serve_production", lambda app, host, port: calls.append({"host": host, "port": port, "server": "waitress"}))

    result = runner.invoke(alfrd_cli, ["gui", "--port", "5050"])

    assert result.exit_code == 0, result.output
    assert calls == [{"host": "127.0.0.1", "port": 5050, "server": "waitress"}]  # not Flask's development server


def test_cli_serve_configures_runtime_database(monkeypatch, tmp_path):
    configs = []

    class FakeApp:
        def run(self, **kwargs):
            pass

    monkeypatch.setattr(
        "alfrd.gui.create_app", lambda config=None: configs.append(config) or FakeApp()
    )
    monkeypatch.setattr("alfrd.cli.webbrowser.open", lambda url: True)
    database = tmp_path / "runtime.sqlite"
    result = runner.invoke(alfrd_cli, ["serve", "--runtime-db", str(database)])

    assert result.exit_code == 0, result.output
    assert configs[0]["RUNTIME_DATABASE"] == str(database.resolve())
    assert configs[0]["RUNTIME_SERVICE"].store.schema_version == 3
    assert configs[0]["CATALOG_READER"].service is configs[0]["RUNTIME_SERVICE"]


def test_cli_no_browser_skips_browser_and_wildcard_prints_loopback(monkeypatch):
    calls = []

    class FakeApp:
        def run(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr("alfrd.gui.create_app", lambda config=None: FakeApp())
    monkeypatch.setattr("alfrd.cli.webbrowser.open", lambda url: (_ for _ in ()).throw(AssertionError(url)))
    monkeypatch.setattr("alfrd.cli._serve_production", lambda app, host, port: calls.append({"host": host, "port": port}))
    result = runner.invoke(alfrd_cli, ["serve", "--host", "0.0.0.0", "--no-browser"])
    assert result.exit_code == 0, result.output
    assert "http://127.0.0.1:5000/studio/" in result.output
    assert "clear text" in result.output
    assert calls == [{"host": "0.0.0.0", "port": 5000}]


def test_cli_manifest_validate_example():
    manifest = Path(__file__).parents[1] / "examples" / "avica_0.3" / "alfrd.yaml"
    result = runner.invoke(alfrd_cli, ["manifest", "validate", str(manifest)])
    assert result.exit_code == 0, result.output
    assert "valid project-manifest-v1" in result.output


def test_browser_opens_only_after_http_readiness(monkeypatch):
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from alfrd.cli import _open_studio_when_ready

    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(("http", self.path))
            self.send_response(200)
            self.end_headers()

        def log_message(self, format, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr("alfrd.cli.webbrowser.open", lambda url: calls.append(("browser", url)) or True)
    base = f"http://127.0.0.1:{server.server_port}"
    url = f"{base}/studio/?token=browser-token"
    try:
        _open_studio_when_ready(url, threading.Event(), f"{base}/api/health")
        assert calls == [("http", "/api/health"), ("browser", url)]
        assert "?token=" in calls[1][1]
        stopped = threading.Event()
        stopped.set()
        _open_studio_when_ready(url, stopped)
        assert len(calls) == 2
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("command", ["serve", "gui"])
@pytest.mark.parametrize("source", ["option", "env", "generated"])
def test_cli_server_access_link_and_file(monkeypatch, command, source):
    from alfrd.gui import auth
    from urllib.parse import parse_qs, urlsplit

    configs, opened = [], []
    stored = []
    port = 8765
    monkeypatch.setattr("alfrd.gui.create_app", lambda config: configs.append(config) or object())
    monkeypatch.setattr("alfrd.cli._reconcile_plans_later", lambda *args, **kwargs: None)
    monkeypatch.setattr("alfrd.cli._open_studio_when_ready", lambda url, stopped, probe: opened.append((url, probe)))
    monkeypatch.setattr("alfrd.cli._serve_production", lambda *args: stored.append(auth.read_server_file(port)))
    args = [command, "--port", str(port)]
    if source == "option":
        monkeypatch.setenv("ALFRD_TOKEN", "env-token")
        args += ["--token", "pinned+token&value"]
    elif source == "env":
        monkeypatch.setenv("ALFRD_TOKEN", "env-token")
    result = runner.invoke(alfrd_cli, args)
    assert result.exit_code == 0, result.output
    token = configs[0]["ACCESS_TOKEN"]
    if source == "generated":
        assert token
    else:
        assert token == ("pinned+token&value" if source == "option" else "env-token")
    assert configs[0]["ACCESS_TOKEN_REQUIRED"] is True
    assert parse_qs(urlsplit(opened[0][0]).query) == {"token": [token]}
    assert opened[0][1] == f"http://127.0.0.1:{port}/api/health"
    assert stored[0]["token"] == token
    assert stored[0]["secret_key"] == configs[0]["SECRET_KEY"]
    assert auth.read_server_file(port) is None


@pytest.mark.parametrize("command", ["serve", "gui"])
@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_cli_no_token_disables_auth_on_loopback(monkeypatch, command, host):
    from alfrd.gui import auth, create_app

    configs, stored = [], []
    monkeypatch.setattr("alfrd.gui.create_app", lambda config: configs.append(config) or object())
    monkeypatch.setattr("alfrd.cli._reconcile_plans_later", lambda *args, **kwargs: None)
    monkeypatch.setattr("alfrd.cli._serve_production", lambda *args: stored.append(auth.read_server_file(8765)))
    result = runner.invoke(alfrd_cli, [command, "--host", host, "--port", "8765", "--no-token", "--no-browser"])
    assert result.exit_code == 0, result.output
    assert configs[0]["ACCESS_TOKEN_REQUIRED"] is False
    assert create_app(configs[0]).test_client().get("/api/studio/session", headers={"Authorization": ""}).status_code == 200
    assert stored[0]["token"] is None
    assert "Warning:" in result.output
    assert "?token=" not in result.output


@pytest.mark.parametrize("command", ["serve", "gui"])
@pytest.mark.parametrize("flag", [[], ["--no-gui-install"]])
def test_cli_no_gui_install_reaches_app_config(monkeypatch, command, flag):
    configs = []
    monkeypatch.setattr("alfrd.gui.create_app", lambda config: configs.append(config) or object())
    monkeypatch.setattr("alfrd.cli._reconcile_plans_later", lambda *args, **kwargs: None)
    monkeypatch.setattr("alfrd.cli._serve_production", lambda *args: None)
    result = runner.invoke(alfrd_cli, [command, "--port", "8765", "--no-browser", *flag])
    assert result.exit_code == 0, result.output
    assert configs[0]["PLUGINS_GUI_INSTALL"] is (not flag)


@pytest.mark.parametrize("command", ["serve", "gui"])
@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.0.2.1", "example.com"])
def test_cli_no_token_rejects_non_loopback(monkeypatch, command, host):
    monkeypatch.setattr("alfrd.cli._runtime_service", lambda *args: pytest.fail("must reject before creating a database"))
    result = runner.invoke(alfrd_cli, [command, "--host", host, "--no-token", "--no-browser"])
    assert result.exit_code != 0
    assert "--no-token is only available on a loopback interface" in result.output


@pytest.mark.parametrize("token", ["pinned+token&value", None])
def test_cli_url_reads_access_link(token):
    from alfrd.gui import auth
    from urllib.parse import parse_qs, urlsplit

    auth.write_server_file(8765, "http://127.0.0.1:8765/studio/", token, "secret")
    result = runner.invoke(alfrd_cli, ["url", "--port", "8765"])
    assert result.exit_code == 0, result.output
    assert parse_qs(urlsplit(result.output.strip()).query) == ({"token": [token]} if token else {})
    assert result.output.startswith("http://127.0.0.1:8765/studio/")


@pytest.mark.parametrize("contents", [None, "invalid json", "{}"])
def test_cli_url_missing_or_invalid_file(contents):
    from alfrd.gui import auth

    if contents is not None:
        path = auth.server_file(5000)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)
    result = runner.invoke(alfrd_cli, ["url"])
    assert result.exit_code == 1
    assert "No alfrd serve found on port 5000" in result.output


def test_cli_formats_ipv6_and_rejects_remote_debug(monkeypatch):
    class FakeApp:
        def run(self, **kwargs):
            pass

    monkeypatch.setattr("alfrd.gui.create_app", lambda config=None: FakeApp())
    result = runner.invoke(alfrd_cli, ["serve", "--host", "::1", "--no-browser"])
    assert result.exit_code == 0, result.output
    assert "http://[::1]:5000/studio/" in result.output
    result = runner.invoke(alfrd_cli, ["serve", "--host", "0.0.0.0", "--debug", "--no-browser"])
    assert result.exit_code != 0
    assert "loopback" in result.output


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
