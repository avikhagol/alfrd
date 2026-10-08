"""Theme resolution, reload behavior, access policy, palette and packaging."""
from __future__ import annotations

import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

from alfrd import extensions
from alfrd.extensions import themes
from alfrd.web import export_site, web_root
from test_studio import studio_app


def test_default_and_reload_without_restart(studio_app):
    app, _ = studio_app
    client = app.test_client()
    dark = client.get("/studio/theme.css")
    assert dark.status_code == 200
    assert dark.data == b":root { color-scheme: dark; }\n"
    assert dark.mimetype == "text/css"
    assert dark.headers["Cache-Control"] == "no-store"
    assert dark.headers["X-Content-Type-Options"] == "nosniff"
    extensions.set_theme("daylight-orbit")
    light = client.get("/studio/theme.css")
    assert light.status_code == 200
    assert b"color-scheme: light" in light.data and b"--bg: #F6F7FB" in light.data
    # Theme endpoint is public even when API authentication is required.
    app.config["ACCESS_TOKEN"] = "test-token"
    app.config["ACCESS_TOKEN_REQUIRED"] = True
    assert client.get("/studio/theme.css").status_code == 200


def test_dropin_and_builtin_priority(studio_app):
    app, _ = studio_app
    folder = extensions.themes_dir() / "paper"
    folder.mkdir(parents=True)
    css = ":root { --bg: #fff; color-scheme: light; }\n"
    (folder / "theme.css").write_text(css)
    extensions.set_theme("paper")
    assert app.test_client().get("/studio/theme.css").data.decode() == css
    shadow = extensions.themes_dir() / "obsidian-orbit"
    shadow.mkdir()
    (shadow / "theme.css").write_text("SHOULD NOT LOAD")
    assert themes.css_path("obsidian-orbit").parent.parent != extensions.themes_dir()


@pytest.mark.parametrize("theme_id", ["missing", "../escape", "", "paper\n", "/tmp/paper"])
def test_invalid_or_missing_theme_falls_back(theme_id):
    assert themes.css_path(theme_id).parent.name == "obsidian-orbit"


def test_symlink_escape_is_rejected(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "theme.css").write_text("secret")
    extensions.themes_dir().mkdir(parents=True)
    (extensions.themes_dir() / "escaped").symlink_to(outside, target_is_directory=True)
    assert themes.css_path("escaped").parent.name == "obsidian-orbit"


def test_packaged_theme_and_confinement(monkeypatch, tmp_path):
    package = tmp_path / "themeplug"
    package.mkdir()
    css = package / "theme.css"
    css.write_text(":root { color-scheme: light; }")
    module = ModuleType("themeplug")
    module.__file__ = str(package / "__init__.py")
    monkeypatch.setitem(sys.modules, "themeplug", module)
    plugin = extensions.Plugin(id="themeplug", version="1", theme="theme.css")
    record = extensions.Record(id="themeplug", source="entry_point", status="ok", plugin=plugin,
                               entry_point="themeplug:plugin")
    monkeypatch.setattr(extensions, "loaded", lambda: [record])
    assert themes.css_path("themeplug") == css
    record.status = "disabled"
    assert themes.css_path("themeplug").parent.name == "obsidian-orbit"
    record.status = "ok"
    monkeypatch.setenv("ALFRD_NO_PLUGINS", "1")
    assert themes.css_path("themeplug").parent.name == "obsidian-orbit"
    monkeypatch.delenv("ALFRD_NO_PLUGINS")
    outside = tmp_path / "outside.css"
    outside.write_text("secret")
    for path in ("../outside.css", str(outside)):
        record.plugin = extensions.Plugin(id="themeplug", version="1", theme=path)
        assert themes.css_path("themeplug").parent.name == "obsidian-orbit"


def test_plugin_theme_id_with_underscore_is_selectable(monkeypatch, tmp_path):
    (tmp_path / "theme.css").write_text(":root { color-scheme: light; }")
    module = ModuleType("my_theme")
    module.__file__ = str(tmp_path / "__init__.py")
    monkeypatch.setitem(sys.modules, "my_theme", module)
    record = extensions.Record(id="my_theme", source="entry_point", status="ok", entry_point="my_theme:plugin",
                               plugin=extensions.Plugin(id="my_theme", version="1", theme="theme.css"))
    monkeypatch.setattr(extensions, "loaded", lambda: [record])
    extensions.set_theme("my_theme")
    assert themes.current_theme() == "my_theme"
    assert themes.css_path() == tmp_path / "theme.css"


def test_missing_at_send_time_never_breaks_page(studio_app, monkeypatch, tmp_path):
    app, _ = studio_app
    monkeypatch.setattr(themes, "css_path", lambda _: tmp_path / "gone.css")
    response = app.test_client().get("/studio/theme.css")
    assert response.status_code == 200
    assert b"color-scheme: dark" in response.data
    assert response.headers["Cache-Control"] == "no-store"


def test_theme_link_order_and_static_export(tmp_path):
    html = (web_root() / "index.html").read_text()
    assert html.index('href="css/studio.css"') < html.index('href="theme.css"')
    assert '<meta name="color-scheme"' not in html
    landing = (web_root().parent / "gui/templates/auth/landing.htm").read_text()
    assert landing.index('href="/studio/css/studio.css"') < landing.index('href="/studio/theme.css"')
    exported = export_site(tmp_path / "exported")
    assert (exported / "theme.css").read_text() == ":root { color-scheme: dark; }\n"


def _tokens(text):
    return dict(re.findall(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", text))


def test_every_colour_is_a_token_and_light_overrides_all():
    text = (web_root() / "css/studio.css").read_text()
    outside_root = re.sub(r":root\s*\{[^}]*\}", "", text)
    outside_root = re.sub(r"/\*.*?\*/", "", outside_root, flags=re.S)
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|(?:rgba?|hsla?)\(", outside_root)
    light = _tokens((web_root() / "css/themes/daylight-orbit/theme.css").read_text())
    base = _tokens(text)
    colors = {k for k, v in base.items() if re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", v)}
    assert colors <= light.keys()
    assert light["--shadow"] != "none"
    assert light["--on-accent"] == "#FFFFFF"
    for selector in (".btn.primary", ".pill.on", ".log-jump", "select option:checked"):
        rules = re.findall(re.escape(selector) + r" \{([^}]+)\}", text)
        assert any("color: var(--on-accent)" in rule for rule in rules)


def _luminance(hex_color):
    values = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    rgb = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in values]
    return sum(v * w for v, w in zip(rgb, (.2126, .7152, .0722)))


def test_light_text_and_accent_labels_meet_wcag_aa():
    tokens = _tokens((web_root() / "css/themes/daylight-orbit/theme.css").read_text())
    pairs = [(name, surface) for name in ("--text", "--text-2", "--text-3", "--muted")
             for surface in ("--surface", "--surface-2", "--bg")]
    pairs += [("--on-accent", name) for name in ("--accent", "--accent-control", "--accent-600", "--accent-700")]
    pairs.append(("--on-ok", "--ok-control"))
    for fg, bg in pairs:
        a, b = sorted((_luminance(tokens[fg]), _luminance(tokens[bg])))
        assert (b + .05) / (a + .05) >= 4.5, (fg, bg)
