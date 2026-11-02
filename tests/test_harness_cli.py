import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from alfrd import cli, harness_cli


def test_help_and_new_server(monkeypatch):
    runner = CliRunner()
    help = runner.invoke(cli.alfrd_cli, ["harness", "--help"])
    assert help.exit_code == 0
    for option in ("--project", "--chat", "--no-open"):
        assert option in help.output
    calls = []
    monkeypatch.setattr(harness_cli, "listening", lambda *a: False)
    monkeypatch.setattr(cli, "_serve_web", lambda *a, **kw: calls.append((a, kw)))
    result = runner.invoke(cli.alfrd_cli, ["harness", "--project", "test", "--chat", "abc", "--no-open"])
    assert result.exit_code == 0, result.output
    assert calls[0][1]["monitor_only"] and calls[0][1]["harness_mode"] and calls[0][1]["no_browser"]
    assert calls[0][1]["harness_chat"] == "abc"


def test_attach_and_old_server(monkeypatch):
    monkeypatch.setattr(harness_cli, "listening", lambda *a: True)
    monkeypatch.setattr(cli, "_serve_web", lambda *a, **k: pytest.fail("second server"))
    monkeypatch.setattr(cli.webbrowser, "open", lambda *a: pytest.fail("browser opened"))
    monkeypatch.setattr("alfrd.gui.auth.read_server_file", lambda _: {"token": "local"})
    def get(base, path, token):
        assert token == "local"
        if path.endswith("/session"):
            return {"app": "alfrd", "harness_state_version": 1, "default_project": "p", "projects": ["p"]}
        if path == "/api/projects":
            return {"projects": [{"id": 123, "identifier": "p", "name": "test"}]}
        return {"sessions": [{"id": "chat-id"}]}
    monkeypatch.setattr(harness_cli, "get_json", get)
    runner = CliRunner()
    result = runner.invoke(cli.alfrd_cli, ["harness", "--project", "test", "--no-open"])
    assert result.exit_code == 0, result.output
    assert "http://127.0.0.1:5000/studio/?token=local#/harness/chat-id?p=p" in result.output
    monkeypatch.setattr(harness_cli, "get_json", lambda *a: {"app": "alfrd"})
    result = runner.invoke(cli.alfrd_cli, ["harness", "--no-open"])
    assert result.exit_code != 0 and "upgrade/restart" in result.output


def test_monitor_launch_omits_recovery(monkeypatch, tmp_path):
    configs = []
    class App:
        def __init__(self):
            self.config = {}
    monkeypatch.setattr("alfrd.gui.create_app", lambda cfg: configs.append(cfg) or App())
    monkeypatch.setattr("alfrd.gui.auth.write_server_file", lambda *a: None)
    monkeypatch.setattr("alfrd.gui.auth.remove_server_file", lambda *a: None)
    monkeypatch.setattr(cli, "_start_plugin_services", lambda *a: None)
    monkeypatch.setattr(cli, "_serve_production", lambda *a: None)
    for name in ("_reconcile_plans_later", "_chat_watch_later", "_chat_schedule_later"):
        monkeypatch.setattr(cli, name, lambda *a, **k: pytest.fail("monitor started recovery"))
    monkeypatch.setattr(cli, "_connect_startup_project", lambda *a: None)
    cli._serve_web("127.0.0.1", 8769, False, runtime_db=str(tmp_path / "runtime.db"), no_browser=True,
                   discover=False, harness_mode=True, monitor_only=True)
    assert configs[0]["STUDIO_HARNESS_MODE"] is True


@pytest.mark.parametrize("url", ["file:///tmp", "http://user:pass@localhost", "http://localhost/?token=secret", "http://localhost/#secret"])
def test_attach_url_rejects_credential_urls(url):
    with pytest.raises(ValueError):
        harness_cli.base_url(url)


def test_remote_attach_never_reads_local_credentials(monkeypatch):
    monkeypatch.setattr("alfrd.gui.auth.read_server_file", lambda *a: pytest.fail("local credentials"))
    def get(base, path, token):
        assert token is None
        return {"app": "alfrd", "harness_state_version": 1}
    monkeypatch.setattr(harness_cli, "get_json", get)
    assert harness_cli.attach_url("https://example.org", local_credentials=True) == "https://example.org/studio/#/harness"


def test_chat_override_truth_table():
    """Execute the actual application check; never write browser preferences."""
    source = (Path(__file__).parents[1] / "src/alfrd/web/js/app.js").read_text()
    check = source.split("function chatOn() {", 1)[1].split("\n}", 1)[0]
    js = "const check = new Function('state','location','experimentalOn','shown','readPrefs'," + json.dumps(check) + ");\n"
    js += """
for (const harness of [false, true]) for (const experimental of [false, true])
for (const visible of [false, true]) for (const toggle of [false, true]) {
  const prefs = { 'ui.experimental_chat': toggle };
  const result = check({view: harness ? 'harness' : 'overview'}, {hash: ''}, () => experimental, () => visible, () => prefs);
  if (result !== (harness || (experimental && visible && toggle))) throw Error('truth table');
  if (prefs['ui.experimental_chat'] !== toggle) throw Error('saved override');
}
"""
    subprocess.run(["node", "--input-type=module", "-e", js], check=True, capture_output=True)
