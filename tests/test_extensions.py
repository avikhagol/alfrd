"""Plugins Phase 2 core: manifest, discovery, loading, isolation, state (alfrd.extensions)."""

from __future__ import annotations

import importlib.metadata
import json
import sys
import textwrap
from pathlib import Path

import pytest

import alfrd.layout_generic as lg
from alfrd import extensions as ext
from alfrd.extensions import Converter, PanelSpec, Plugin, api_ok


@pytest.fixture
def fake_plugins(tmp_path, monkeypatch):
    """``add(name, source)`` writes module ``alfrd_fake_<name>`` and an entry point ``name`` for it."""
    mods = tmp_path / "fake_mods"
    mods.mkdir()
    monkeypatch.syspath_prepend(str(mods))
    eps: list = []
    monkeypatch.setattr(importlib.metadata, "entry_points",
                        lambda group=None, **kw: [e for e in eps if group in (None, e.group)])

    def add(name: str, source: str, attr: str = "plugin") -> None:
        module = f"alfrd_fake_{name.replace('-', '_')}"
        (mods / f"{module}.py").write_text(textwrap.dedent(source))
        sys.modules.pop(module, None)
        eps.append(importlib.metadata.EntryPoint(name, f"{module}:{attr}", ext.GROUP))

    yield add
    for name in list(sys.modules):
        if name.startswith("alfrd_fake_"):
            sys.modules.pop(name)


GOOD = """
from alfrd.extensions import Plugin, PanelSpec
plugin = Plugin(id="good", version="1.2", title="Good one",
                panels=[PanelSpec("good_panel", evaluate=lambda root, panel, values, spec: {"hello": 1})])
"""


def _rec(records, plugin_id):
    return next(r for r in records if r.id == plugin_id)


def test_api_specifier_parser():
    assert api_ok(">=1,<2", 1) and api_ok("==1", "1.0") and api_ok(">0.9", 1) and api_ok("!=2", 1)
    assert not api_ok(">=2", 1) and not api_ok("<1", 1) and not api_ok(">=1,<2", 2)
    for bad in ("", "~=1", ">=x", "1"):
        with pytest.raises(ValueError):
            api_ok(bad, 1)


def test_manifest_kinds_and_tuples():
    p = Plugin(id="x", version="1", viewers=["md"], panels=[PanelSpec("p", client=True)],
               converters=[Converter(src=[".ps"], to=".pdf", run=print)], theme="theme.css")
    assert p.kinds == ["viewer", "panel", "converter", "theme"]
    assert p.viewers == ("md",) and p.converters[0].src == (".ps",)
    assert Plugin(id="y", version="1").kinds == []


def test_load_applies_an_ok_plugin_once(fake_plugins):
    fake_plugins("good", GOOD)
    records = ext.load()
    rec = _rec(records, "good")
    assert rec.status == "ok" and rec.title == "Good one" and rec.kinds == ["panel"] and rec.version == "1.2"
    assert "good_panel" in lg.PANEL_TYPES
    assert ext.load() is records  # once per process
    assert rec.to_dict()["panels"] == ["good_panel"] and "plugin" not in rec.to_dict()
    ext.reset()
    assert "good_panel" not in lg.PANELS


@pytest.mark.parametrize("source, status, error", [
    ("raise RuntimeError('boom at import')\n", "error", "RuntimeError: boom at import"),
    ("import sys\nsys.exit(3)\n", "error", "SystemExit: 3"),
    ("plugin = object()\n", "error", "not alfrd.extensions.Plugin"),
    ("from alfrd.extensions import Plugin\nplugin = Plugin(id='other', version='1')\n", "error", "does not match Plugin.id"),
    ("from alfrd.extensions import Plugin\nplugin = Plugin(id='bad', version='1', alfrd_api='>=2')\n",
     "incompatible API: needs >=2 (alfrd provides 1)", ""),
    ("from alfrd.extensions import Plugin\nplugin = Plugin(id='bad', version='1', alfrd_api='~=1')\n", "error", "ValueError"),
])
def test_broken_plugins_are_recorded_not_raised(fake_plugins, source, status, error):
    fake_plugins("bad", source)
    fake_plugins("good", GOOD)
    records = ext.load()
    bad = _rec(records, "bad")
    assert bad.status == status and error in bad.error
    if bad.status == "error":
        assert bad.traceback_tail.strip()
    assert _rec(records, "good").status == "ok"  # the others still load


def test_broken_plugin_never_stops_create_app(fake_plugins, tmp_path):
    pytest.importorskip("flask")
    from alfrd.gui import create_app

    fake_plugins("bad", "raise ImportError('missing dependency foo')\n")
    app = create_app({"TESTING": True, "SECRET_KEY": "k", "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'c.sqlite'}"})
    bad = _rec(app.extensions["alfrd_plugins"], "bad")
    assert bad.status == "error" and "missing dependency foo" in bad.error
    assert "ImportError" in bad.traceback_tail
    assert app.test_client().get("/studio/").status_code == 200


def test_missing_binary_is_listed_not_applied(fake_plugins):
    fake_plugins("needs", """
        from alfrd.extensions import Plugin, PanelSpec
        plugin = Plugin(id="needs", version="1", requires_bin=["alfrd-no-such-binary-xyz"],
                        panels=[PanelSpec("needs_panel", client=True)])
    """)
    rec = _rec(ext.load(), "needs")
    assert rec.status == "missing binary: alfrd-no-such-binary-xyz" and rec.missing_bin == ["alfrd-no-such-binary-xyz"]
    assert "needs_panel" not in lg.PANELS


def test_panel_clash_rolls_back_the_plugin(fake_plugins):
    fake_plugins("clash", """
        from alfrd.extensions import Plugin, PanelSpec
        plugin = Plugin(id="clash", version="1", panels=[PanelSpec("clash_a", client=True), PanelSpec("text", client=True)])
    """)
    rec = _rec(ext.load(), "clash")
    assert rec.status == "error" and "text" in rec.error
    assert "clash_a" not in lg.PANELS


def test_disabled_plugins_are_not_imported(fake_plugins):
    fake_plugins("good", GOOD + "\nimport builtins\nbuiltins.ALFRD_FAKE_IMPORTED = True\n")
    ext.set_enabled("good", False)
    assert json.loads(ext.state_file().read_text())["disabled"] == ["good"]
    rec = _rec(ext.load(), "good")
    assert rec.status == "disabled" and not rec.enabled and "good_panel" not in lg.PANELS
    import builtins
    assert not hasattr(builtins, "ALFRD_FAKE_IMPORTED")
    ext.set_enabled("good", True)
    assert _rec(ext.load(force=True), "good").status == "ok"  # a restart applies it


def test_safe_mode_skips_every_plugin(fake_plugins, monkeypatch):
    fake_plugins("good", GOOD)
    monkeypatch.setenv("ALFRD_NO_PLUGINS", "1")
    rec = _rec(ext.load(), "good")
    assert rec.status == "skipped (safe mode)" and "good_panel" not in lg.PANELS


DEFAULT_STATE = {"disabled": [], "theme": "obsidian-orbit", "catalog_url": None, "gui_install": True,
                 "autostart": []}


def test_state_reads_tolerantly_and_writes_atomically():
    assert ext.read_state() == DEFAULT_STATE
    ext.state_file().parent.mkdir(parents=True, exist_ok=True)
    ext.state_file().write_text("{not json")
    assert ext.read_state()["theme"] == "obsidian-orbit"
    ext.state_file().write_text(json.dumps({"disabled": "x", "theme": "../evil", "catalog_url": "http://x/i.json",
                                            "gui_install": "no"}))
    assert ext.read_state() == DEFAULT_STATE
    ext.set_theme("paper-light")
    assert ext.read_state()["theme"] == "paper-light"
    assert [p.name for p in ext.state_file().parent.iterdir() if p.name.startswith(".plugins.")] == []
    with pytest.raises(ValueError):
        ext.set_theme("Bad Theme")


def test_state_keeps_catalog_settings_across_toggles():
    ext.state_file().parent.mkdir(parents=True, exist_ok=True)
    ext.state_file().write_text(json.dumps({"catalog_url": "file:///srv/index.json", "gui_install": False,
                                            "note": "kept"}))
    assert ext.read_state()["catalog_url"] == "file:///srv/index.json"
    assert ext.read_state()["gui_install"] is False
    ext.set_enabled("good", False)
    ext.set_theme("paper-light")
    raw = json.loads(ext.state_file().read_text())
    assert raw == {"catalog_url": "file:///srv/index.json", "gui_install": False, "note": "kept",
                   "disabled": ["good"], "theme": "paper-light"}
    assert ext.catalog_url_ok("https://example.org/index.json")
    assert not any(map(ext.catalog_url_ok, ("http://example.org/i.json", "ftp://x", "", None, 3)))


def test_drop_in_themes_are_discovered():
    for name, css in (("night-sky", ":root{}"), ("Bad_Name", ":root{}"), ("no-css", None)):
        folder = ext.themes_dir() / name
        folder.mkdir(parents=True)
        if css is not None:
            (folder / "theme.css").write_text(css)
    (ext.themes_dir() / "night-sky" / "theme.json").write_text(json.dumps({"title": "Night sky"}))
    themes = {r.id: r for r in ext.discover() if r.kinds == ["theme"]}
    assert "night-sky" in themes and themes["night-sky"].title == "Night sky" and themes["night-sky"].source == "theme_dir"
    assert "Bad_Name" not in themes and "no-css" not in themes


def test_add_site_appends_after_site_packages():
    ext.add_site()
    assert str(ext.site_dir()) not in sys.path  # no folder yet
    ext.site_dir().mkdir(parents=True)
    ext.add_site()
    ext.add_site()
    assert sys.path.count(str(ext.site_dir())) == 1 and sys.path[-1] == str(ext.site_dir())
    site_packages = [i for i, p in enumerate(sys.path) if p.endswith("site-packages")]
    assert all(i < sys.path.index(str(ext.site_dir())) for i in site_packages)


def test_plugin_cli_is_mounted_and_core_names_win(fake_plugins):
    typer = pytest.importorskip("typer")
    from typer.testing import CliRunner

    from alfrd.cli import mount_plugin_commands

    cli_src = """
        import typer
        from alfrd.extensions import Plugin
        cli = typer.Typer()
        @cli.command()
        def hello(name: str = "x"):
            print(f"hello {name}")
        @cli.command()
        def other():
            pass
        plugin = Plugin(id="{id}", version="1", cli=cli)
    """
    fake_plugins("greet", cli_src.replace("{id}", "greet"))
    fake_plugins("serve", cli_src.replace("{id}", "serve"))
    app = typer.Typer()

    @app.command()
    def serve():
        print("core serve")

    records = mount_plugin_commands(app)
    assert _rec(records, "greet").status == "ok"
    assert _rec(records, "serve").status == "error" and "taken" in _rec(records, "serve").error
    result = CliRunner().invoke(app, ["greet", "hello", "--name", "M31"])
    assert result.exit_code == 0 and "hello M31" in result.output
    assert "core serve" in CliRunner().invoke(app, ["serve"]).output


def test_serve_has_a_safe_mode_option():
    from typer.testing import CliRunner

    from alfrd.cli import alfrd_cli

    assert "--safe-mode" in CliRunner().invoke(alfrd_cli, ["serve", "--help"]).output
