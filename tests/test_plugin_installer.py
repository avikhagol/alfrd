from __future__ import annotations
import importlib.metadata as metadata
import json
from pathlib import Path
import subprocess
import sys

import pytest
from typer.testing import CliRunner

from alfrd import extensions as ext, layout_generic as lg
from alfrd.extensions import installer as ins
from plugin_wheel import build_wheel


@pytest.fixture
def offline(tmp_path, monkeypatch):
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    return ("--no-index", "--find-links", str(tmp_path / "wheels"))


@pytest.fixture(autouse=True)
def modules():
    yield
    for name in list(sys.modules):
        if name.startswith(("alfrd_testplug", "alfrd_secondplug", "alfrd_brokenplug")):
            sys.modules.pop(name)


def files():
    return sorted(str(p.relative_to(ext.site_dir())) for p in ext.site_dir().rglob("*") if p.is_file())


def test_build_command_uv_and_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(ins.shutil, "which", lambda _: "/bin/uv")
    cmd = ins.build_command("alfrd-testplug", site=tmp_path, constraints=tmp_path / "c", upgrade=True, extra=("--offline",))
    assert cmd == ["/bin/uv", "pip", "install", "--target", str(tmp_path), "--python", sys.executable,
                   "--constraint", str(tmp_path / "c"), "--upgrade", "--offline", "alfrd-testplug"]
    monkeypatch.setattr(ins.shutil, "which", lambda _: None)
    assert ins.build_command("x", site=tmp_path, constraints=tmp_path / "c")[:4] == [sys.executable, "-m", "pip", "install"]


def test_constraints_exclude_plugin_site(tmp_path, monkeypatch):
    class Dist:
        def __init__(self, name, path):
            self.metadata, self.version, self.path = {"Name": name}, "1.2", path
        def locate_file(self, name):
            return self.path / name
    monkeypatch.setattr(ins.metadata, "distributions", lambda: [Dist("alfrd", tmp_path), Dist("plug", ext.site_dir())])
    path = ins.write_constraints(tmp_path / "c")
    assert path.read_text() == "alfrd==1.2\n"


def test_offline_install_disable_remove_end_to_end(tmp_path, offline):
    from alfrd.cli import alfrd_cli
    wheel = build_wheel(tmp_path / "wheels")
    result = ins.install(str(wheel), extra=offline)
    assert result[0]["id"] == "testplug" and result[0]["sha256"]
    assert ins.installed()["testplug"]["dists"] == ["alfrd-testplug"]
    records = ext.load(force=True)
    assert next(r for r in records if r.id == "testplug").status == "ok"
    assert "testplug_panel" in lg.PANELS
    runner = CliRunner()
    out = runner.invoke(alfrd_cli, ["plugin", "list"])
    assert out.exit_code == 0 and "testplug" in out.output and "ok" in out.output
    out = runner.invoke(alfrd_cli, ["plugin", "disable", "testplug"])
    assert out.exit_code == 0 and "restart" in out.output
    records = ext.load(force=True)
    assert next(r for r in records if r.id == "testplug").status == "disabled"
    assert "testplug_panel" not in lg.PANELS
    out = runner.invoke(alfrd_cli, ["plugin", "remove", "testplug", "--yes"])
    assert out.exit_code == 0, out.output
    assert ins.installed() == {} and files() == []


def test_install_cli_yes_uses_real_installer(tmp_path, offline, monkeypatch):
    from alfrd.cli import alfrd_cli
    wheel = build_wheel(tmp_path / "wheels")
    original = ins.install
    monkeypatch.setattr(ins, "install", lambda spec, **kw: original(spec, extra=offline, **kw))
    out = CliRunner().invoke(alfrd_cli, ["plugin", "install", str(wheel), "--yes"])
    assert out.exit_code == 0, out.output
    assert "testplug" in ins.installed()


def test_broken_installed_plugin_isolated(tmp_path, offline):
    from alfrd.cli import alfrd_cli
    from alfrd.gui import create_app
    wheel = build_wheel(tmp_path / "wheels", name="alfrd_brokenplug", plugin_id="broken", broken=True)
    ins.install(str(wheel), extra=offline)
    out = CliRunner().invoke(alfrd_cli, ["plugin", "list"])
    assert out.exit_code == 0 and "broken" in out.output and "error" in out.output
    app = create_app({"TESTING": True, "SECRET_KEY": "k", "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'db.sqlite'}"})
    assert next(r for r in app.extensions["alfrd_plugins"] if r.id == "broken").status == "error"


def test_plugin_installed_as_dependency_keeps_its_own_source(tmp_path, offline):
    first = build_wheel(tmp_path / "wheels")
    ins.install(str(first), extra=offline)
    build_wheel(tmp_path / "wheels", version="0.2")
    second = build_wheel(tmp_path / "wheels", name="alfrd_secondplug", plugin_id="secondplug",
                         requires=("alfrd_testplug>=0.2",))
    ins.install(str(second), extra=offline)
    inventory = ins.installed()
    assert inventory["testplug"]["source"] == str(first.resolve()) and inventory["testplug"]["version"] == "0.2"
    assert inventory["secondplug"]["source"] == str(second.resolve())
    assert "alfrd-testplug" not in inventory["secondplug"]["dists"]


def test_busy_lock_is_clean_error():
    with ins.install_lock():
        with pytest.raises(ins.InstallerBusy, match="another install or remove"):
            ins.install("foo")


def test_failure_leaves_inventory_and_site_unchanged(tmp_path, offline, monkeypatch):
    wheel = build_wheel(tmp_path / "wheels")
    ins.install(str(wheel), extra=offline)
    before = files(), ins.installed()
    monkeypatch.setattr(ins.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, 1, "", "resolver conflict"))
    with pytest.raises(ins.InstallError, match="resolver conflict"):
        ins.install("bad")
    assert (files(), ins.installed()) == before
    assert not list(ext.plugins_dir().glob(".install-*"))


def test_missing_entrypoint_rolls_back(tmp_path, offline):
    wheel = build_wheel(tmp_path / "wheels", plugin_id=None, source="x=1\n")
    with pytest.raises(ins.InstallError, match="no alfrd.plugins"):
        ins.install(str(wheel), extra=offline)
    assert ins.installed() == {} and files() == []


def test_remove_refuses_record_traversal(tmp_path, offline):
    wheel = build_wheel(tmp_path / "wheels")
    ins.install(str(wheel), extra=offline)
    dist = next(ext.site_dir().glob("*.dist-info"))
    (dist / "RECORD").write_text((dist / "RECORD").read_text() + "../precious,,\n")
    precious = ext.plugins_dir() / "precious"
    precious.write_text("keep")
    before = files(), ins.installed()
    with pytest.raises(ins.InstallError, match="outside plugin site"):
        ins.remove("testplug")
    assert precious.read_text() == "keep" and (files(), ins.installed()) == before


def test_shared_dependency_preserved_until_last_plugin(tmp_path, offline):
    folder = tmp_path / "wheels"
    build_wheel(folder, name="alfrd_dep", plugin_id=None, source="x=1\n")
    first = build_wheel(folder, requires=["alfrd-dep==0.1"])
    second = build_wheel(folder, name="alfrd_secondplug", plugin_id="second", requires=["alfrd-dep==0.1"])
    ins.install(str(first), extra=offline)
    ins.install(str(second), extra=offline)
    assert "alfrd-dep" in ins.installed()["second"]["dists"]
    ins.remove("testplug")
    assert (ext.site_dir() / "alfrd_dep" / "__init__.py").exists()
    assert not (ext.site_dir() / "alfrd_testplug").exists()
    ins.remove("second")
    assert files() == []


def test_update_reinstalls_from_recorded_source(tmp_path, offline):
    folder = tmp_path / "wheels"
    wheel = build_wheel(folder)
    ins.install(str(wheel), extra=offline)
    # Same source path, updated metadata (simulates a mutable local wheel).
    replacement = build_wheel(folder, version="0.2")
    wheel.write_bytes(replacement.read_bytes())
    # Wheel filename must match metadata; use a mocked source record for the new file.
    inventory = ins.installed()
    inventory["testplug"]["source"] = str(replacement)
    ins._write_inventory(inventory)
    assert ins.update("testplug", extra=offline)[0]["version"] == "0.2"
    ins.remove("testplug")
    assert files() == []


def test_host_dependency_conflict_fails_without_mutation(tmp_path, offline):
    version = metadata.version("typer")
    wheel = build_wheel(tmp_path / "wheels", requires=[f"typer!={version}"])
    with pytest.raises(ins.InstallError):
        ins.install(str(wheel), extra=offline)
    assert files() == [] and ins.installed() == {}


def test_update_cleans_dependencies_no_longer_required(tmp_path, offline):
    folder = tmp_path / "wheels"
    build_wheel(folder, name="alfrd_dep", plugin_id=None, source="x=1\n")
    first = build_wheel(folder, requires=["alfrd-dep==0.1"])
    ins.install(str(first), extra=offline)
    new = build_wheel(folder, version="0.2")
    inventory = ins.installed()
    inventory["testplug"]["source"] = str(new)
    ins._write_inventory(inventory)
    ins.update("testplug", extra=offline)
    assert not (ext.site_dir() / "alfrd_dep").exists()
    ins.remove("testplug")
    assert files() == []


def test_inventory_write_failure_rolls_back_site(tmp_path, offline, monkeypatch):
    first = build_wheel(tmp_path / "wheels")
    ins.install(str(first), extra=offline)
    before = files(), ins.installed()
    new = build_wheel(tmp_path / "wheels", name="alfrd_secondplug", plugin_id="second")
    def fail(data):
        raise OSError("disk full")
    monkeypatch.setattr(ins, "_write_inventory", fail)
    with pytest.raises(ins.InstallError, match="disk full"):
        ins.install(str(new), extra=offline)
    assert (files(), ins.installed()) == before


def test_plugin_id_collision_does_not_replace_inventory(tmp_path, offline):
    first = build_wheel(tmp_path / "wheels")
    ins.install(str(first), extra=offline)
    before = files(), ins.installed()
    second = build_wheel(tmp_path / "wheels", name="alfrd_secondplug", plugin_id="testplug")
    with pytest.raises(ins.InstallError, match="already installed"):
        ins.install(str(second), extra=offline)
    assert (files(), ins.installed()) == before


def test_local_source_is_saved_as_absolute_path(tmp_path, offline, monkeypatch):
    wheel = build_wheel(tmp_path / "wheels")
    monkeypatch.chdir(wheel.parent)
    ins.install(wheel.name, extra=offline)
    assert ins.installed()["testplug"]["source"] == str(wheel.resolve())
    monkeypatch.chdir(tmp_path)
    ins.update("testplug", extra=offline)
    ins.remove("testplug")
    assert files() == []
