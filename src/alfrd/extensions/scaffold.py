"""Local, editable starting points for plugin packages and drop-in themes."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

KINDS = ("viewer", "converter", "theme", "panel")


def create(plugin_id: str, *, kind: str = "viewer", directory: Path = Path(".")) -> Path:
    """Create a scaffold without replacing an existing folder or executing code."""
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,40}", plugin_id):
        raise ValueError("plugin id must start with a lowercase letter and contain lowercase letters, digits, - or _ (max 41 characters)")
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}; choose {', '.join(KINDS)}")
    if kind == "theme" and "_" in plugin_id:
        raise ValueError("theme ids use lowercase letters, digits and hyphens")
    target = Path(directory) / (plugin_id if kind == "theme" else f"alfrd-{plugin_id}")
    if target.exists():
        raise ValueError(f"already exists: {target}")
    files = _files(plugin_id, kind)
    target.mkdir(parents=True, exist_ok=False)
    try:
        for relative, content in files.items():
            path = target / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
    except BaseException:
        shutil.rmtree(target)
        raise
    return target


def _files(plugin_id: str, kind: str) -> dict[str, str]:
    if kind == "theme":
        from alfrd.web import web_root

        palette = web_root() / "css" / "themes" / "obsidian-orbit" / "theme.css"
        css = palette.read_text(encoding="utf-8")
        root = re.search(r":root\s*\{(.*?)\}", (web_root() / "css" / "studio.css").read_text(encoding="utf-8"), re.S)
        structural = {"r", "r-sm", "r-lg", "sans", "mono", "rail", "top", "foot"}
        tokens = [(name, value) for name, value in re.findall(r"--([\w-]+)\s*:\s*([^;]+);", root.group(1))
                  if name not in structural and not name.startswith("s-")]
        css += "\n/* Edit the colour tokens below; preserve contrast for text and controls. */\n:root {\n"
        css += "".join(f"  --{name}: {value};\n" for name, value in tokens) + "}\n"
        return {
            "theme.css": css,
            "theme.json": json.dumps({"title": plugin_id.replace("-", " ").title(), "color_scheme": "dark"}, indent=2) + "\n",
            "README.md": f"# {plugin_id}\n\nEdit theme.css, then copy this folder into the directory printed by `alfrd plugin theme`.\nSelect it with `alfrd plugin theme {plugin_id}`.\n",
        }
    module = "alfrd_" + plugin_id.replace("-", "_")
    manifest = f'plugin = Plugin(id={plugin_id!r}, version="0.1.0", title={plugin_id!r}'
    imports = "from alfrd.extensions import Plugin"
    helper = ""
    web = ""
    if kind == "viewer":
        manifest += f', viewers=({plugin_id!r},), web="web")\n'
        web = f'''export function activate(api) {{
  api.registerViewer({{
    id: {json.dumps(plugin_id)}, match: [".example"],
    render(file, el) {{
      const pre = document.createElement("pre");
      pre.textContent = file.text || "";
      el.replaceChildren(pre);
    }},
  }});
}}
'''
    elif kind == "panel":
        panel = plugin_id.replace("-", "_") + "_panel"
        imports += ", PanelSpec"
        manifest += f', panels=(PanelSpec({panel!r}, client=True),), web="web")\n'
        web = f'''export function activate(api) {{
  api.registerPanel({json.dumps(panel)}, (instance, context, t) => "<div>Example panel</div>");
}}
'''
    else:
        imports += ", Converter"
        helper = '\n\ndef convert(src, dest, *, timeout=30):\n    """Replace this example with your conversion; write dest or raise."""\n    dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")\n\n'
        manifest += ', converters=(Converter(src=(".example",), to="txt", run=convert),))\n'
    files = {
        "pyproject.toml": f'''[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.build_meta"

[project]
name = "alfrd-{plugin_id}"
version = "0.1.0"
requires-python = ">=3.10"
# alfrd itself is provided by the host (`--target` would install a second copy);
# the manifest's `alfrd_api` declares compatibility.
dependencies = []

[project.entry-points."alfrd.plugins"]
"{plugin_id}" = "{module}:plugin"

[tool.setuptools.packages.find]
include = ["{module}*"]

[tool.setuptools.package-data]
"{module}" = ["web/*"]
''',
        f"{module}/__init__.py": imports + "\n" + helper + "\n" + manifest,
        "tests/test_plugin.py": f'''from {module} import plugin
from alfrd.extensions import api_ok


def test_manifest():
    assert plugin.id == {plugin_id!r}
    assert api_ok(plugin.alfrd_api)
    assert {kind!r} in plugin.kinds
''',
        "README.md": f"# alfrd-{plugin_id}\n\nEdit the manifest and implementation, then build with `python -m pip wheel --no-deps .`.\nInstall the wheel with `alfrd plugin install <wheel>` and restart `alfrd serve`.\nRun tests with `python -m pytest tests`.\n" + (
            "\nThe browser activate(api) hook is the planned Phase 3 contract; Phase 2 discovers the contribution but does not load browser code.\n" if web else ""
        ),
    }
    if web:
        files[f"{module}/web/index.js"] = web
    return files
