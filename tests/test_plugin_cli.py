"""Plugin management UX, failure messages and editable package scaffolds."""

import importlib.util
import json
import tomllib
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from alfrd import extensions
from alfrd.cli import alfrd_cli
from alfrd.extensions import Plugin, Record

runner = CliRunner()


@pytest.fixture
def fake_plugins(monkeypatch):
    good = SimpleNamespace(name="sample", value="sample:plugin", dist=None,
                           load=lambda: Plugin(id="sample", version="1.2.3", viewers=("sample",), web="web"))
    def broken():
        raise RuntimeError("import exploded")
    bad = SimpleNamespace(name="broken", value="broken:plugin", dist=None, load=broken)
    monkeypatch.setattr(extensions, "_entry_points", lambda: [good, bad])
    return good, bad


def test_list_and_info_include_load_errors_and_manifest(fake_plugins):
    result = runner.invoke(alfrd_cli, ["plugin", "list"])
    assert result.exit_code == 0, result.output
    assert "sample" in result.output and "1.2.3" in result.output
    assert "broken" in result.output and "error" in result.output
    assert "SOURCE PACKAGE" in result.output
    result = runner.invoke(alfrd_cli, ["plugin", "info", "sample"])
    data = json.loads(result.output)
    assert data["viewers"] == ["sample"] and data["web"] == "web"
    assert data["alfrd_api"] == ">=1,<2"
    result = runner.invoke(alfrd_cli, ["plugin", "info", "broken"])
    data = json.loads(result.output)
    assert "import exploded" in data["error"] and "RuntimeError" in data["traceback_tail"]


def test_enable_disable_persists_and_does_not_import(fake_plugins, monkeypatch):
    monkeypatch.setattr(fake_plugins[0], "load", lambda: pytest.fail("enable/disable must not import"))
    result = runner.invoke(alfrd_cli, ["plugin", "disable", "sample"])
    assert result.exit_code == 0 and "restart `alfrd serve` to apply" in result.output
    assert extensions.read_state()["disabled"] == ["sample"]
    result = runner.invoke(alfrd_cli, ["plugin", "enable", "sample"])
    assert result.exit_code == 0 and not extensions.read_state()["disabled"]


def test_unknown_plugin_has_clean_error(fake_plugins):
    for action in ("info", "enable", "disable"):
        result = runner.invoke(alfrd_cli, ["plugin", action, "absent"])
        assert result.exit_code == 1 and "unknown plugin" in result.output
        assert "Traceback" not in result.output


def test_info_keeps_core_command_clash_visible(monkeypatch):
    import typer

    plugin = Plugin(id="plugin", version="1", cli=typer.Typer())
    ep = SimpleNamespace(name="plugin", value="example:plugin", dist=None, load=lambda: plugin)
    monkeypatch.setattr(extensions, "_entry_points", lambda: [ep])
    result = runner.invoke(alfrd_cli, ["plugin", "info", "plugin"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["status"] == "error" and "taken by alfrd" in data["error"]


def test_plugin_command_mount_is_idempotent_and_not_a_core_clash(monkeypatch):
    import typer
    from alfrd.cli import _core_command_names, mount_plugin_commands

    app = typer.Typer()
    commands = typer.Typer()
    plugin = Plugin(id="example", version="1", cli=commands)
    rec = Record("example", "entry_point", status="ok", plugin=plugin)
    monkeypatch.setattr(extensions, "load", lambda **kwargs: [rec])
    mount_plugin_commands(app)
    mount_plugin_commands(app)
    assert rec.status == "ok"
    assert len(app.registered_groups) == 1
    assert "example" not in _core_command_names(app)


def test_install_confirmation_and_yes(monkeypatch):
    from alfrd.extensions import installer
    calls = []
    monkeypatch.setattr(installer, "install", lambda spec: calls.append(spec) or [{"id": "sample", "version": "1"}])
    result = runner.invoke(alfrd_cli, ["plugin", "install", "package.whl"], input="n\n")
    assert result.exit_code == 1 and not calls
    assert "access to your files and projects" in result.output
    result = runner.invoke(alfrd_cli, ["plugin", "install", "package.whl"], input="y\n")
    assert result.exit_code == 0 and calls == ["package.whl"]
    result = runner.invoke(alfrd_cli, ["plugin", "install", "package.whl", "--yes"])
    assert result.exit_code == 0 and len(calls) == 2
    assert "access to your files and projects" in result.output


def test_remove_confirmation_and_update(monkeypatch):
    from alfrd.extensions import installer
    calls = []
    monkeypatch.setattr(installer, "remove", lambda name: calls.append(("remove", name)))
    monkeypatch.setattr(installer, "update", lambda name: calls.append(("update", name)) or [{"id": "sample", "version": "2"}])
    result = runner.invoke(alfrd_cli, ["plugin", "remove", "sample"], input="n\n")
    assert result.exit_code == 1 and not calls
    result = runner.invoke(alfrd_cli, ["plugin", "remove", "sample", "--yes"])
    assert result.exit_code == 0 and calls == [("remove", "sample")]
    assert runner.invoke(alfrd_cli, ["plugin", "update", "sample"]).exit_code == 0
    assert runner.invoke(alfrd_cli, ["plugin", "update"]).exit_code == 0
    assert calls[-2:] == [("update", "sample"), ("update", None)]


@pytest.mark.parametrize("command", [["install", "bad", "--yes"], ["remove", "bad", "--yes"], ["update", "bad"]])
def test_installer_errors_exit_one_without_traceback(monkeypatch, command):
    from alfrd.extensions import installer
    def fail(*args, **kwargs):
        raise installer.InstallerBusy("another install or remove is running")
    monkeypatch.setattr(installer, command[0], fail)
    result = runner.invoke(alfrd_cli, ["plugin", *command])
    assert result.exit_code == 1 and "another install or remove is running" in result.output
    assert "Traceback" not in result.output


def test_theme_lists_and_selects_available_theme(monkeypatch):
    records = [Record("obsidian-orbit", "builtin", kinds=["theme"], status="ok"),
               Record("daylight-orbit", "builtin", kinds=["theme"], title="Daylight Orbit", status="ok"),
               Record("disabled-theme", "entry_point", kinds=["theme"], enabled=False, status="disabled")]
    monkeypatch.setattr(extensions, "load", lambda force=False: records)
    result = runner.invoke(alfrd_cli, ["plugin", "theme"])
    assert result.exit_code == 0 and "* obsidian-orbit" in result.output
    assert "disabled-theme" not in result.output
    result = runner.invoke(alfrd_cli, ["plugin", "theme", "daylight-orbit"])
    assert result.exit_code == 0 and extensions.read_state()["theme"] == "daylight-orbit"
    assert "reload Studio to apply" in result.output and "restart" not in result.output
    result = runner.invoke(alfrd_cli, ["plugin", "theme", "absent"])
    assert result.exit_code == 1 and "available: obsidian-orbit, daylight-orbit" in result.output


@pytest.mark.parametrize("argv, mounted", [(["plugin", "list"], False), (["--help"], True)])
def test_main_does_not_load_plugins_for_plugin_commands(monkeypatch, argv, mounted):
    import sys
    from alfrd import cli
    calls = []
    monkeypatch.setattr(sys, "argv", ["alfrd", *argv])
    monkeypatch.setattr(cli, "mount_plugin_commands", lambda: calls.append(1))
    monkeypatch.setattr(cli, "alfrd_cli", lambda: None)
    cli.main()
    assert bool(calls) is mounted


@pytest.mark.parametrize("kind", ["viewer", "panel", "converter"])
def test_new_package_is_importable_and_has_entry_point(tmp_path, kind):
    result = runner.invoke(alfrd_cli, ["plugin", "new", "my-example", "--kind", kind, "--dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    root = tmp_path / "alfrd-my-example"
    data = tomllib.loads((root / "pyproject.toml").read_text())
    assert data["project"]["entry-points"]["alfrd.plugins"] == {"my-example": "alfrd_my_example:plugin"}
    assert data["project"]["dependencies"] == []  # host-provided alfrd is not reinstalled into --target
    spec = importlib.util.spec_from_file_location("scaffold_example", root / "alfrd_my_example" / "__init__.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.plugin.id == "my-example" and kind in module.plugin.kinds
    if kind != "converter":
        js = (root / "alfrd_my_example" / "web" / "index.js").read_text()
        assert "export function activate(api)" in js
        assert ("api.registerViewer" if kind == "viewer" else "api.registerPanel") in js
    assert (root / "tests" / "test_plugin.py").is_file()
    result = runner.invoke(alfrd_cli, ["plugin", "new", "my-example", "--dir", str(tmp_path)])
    assert result.exit_code == 1 and "already exists" in result.output


def test_new_theme_is_dropin_and_rejects_bad_ids(tmp_path):
    result = runner.invoke(alfrd_cli, ["plugin", "new", "paper", "--kind", "theme", "--dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    root = tmp_path / "paper"
    assert ":root" in (root / "theme.css").read_text()
    assert "--on-accent:" in (root / "theme.css").read_text()
    assert "--bg:" in (root / "theme.css").read_text()
    assert json.loads((root / "theme.json").read_text())["title"] == "Paper"
    for name in ("../outside", "Uppercase", "a/b"):
        result = runner.invoke(alfrd_cli, ["plugin", "new", name, "--dir", str(tmp_path)])
        assert result.exit_code == 1 and "Traceback" not in result.output
    result = runner.invoke(alfrd_cli, ["plugin", "new", "paper2", "--kind", "unsupported", "--dir", str(tmp_path)])
    assert result.exit_code == 1 and "unknown kind" in result.output
