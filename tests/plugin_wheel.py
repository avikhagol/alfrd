"""Tiny offline wheels, built without a build backend or network access."""
from __future__ import annotations
import base64
import hashlib
from pathlib import Path
import zipfile


def build_wheel(folder: Path, *, name="alfrd_testplug", version="0.1", plugin_id="testplug",
                requires=(), broken=False, source=None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    info = f"{name}-{version}.dist-info"
    manifest = source or ("raise RuntimeError('test plugin broken')\n" if broken else
        f'from alfrd.extensions import Plugin, PanelSpec\n'
        f'plugin = Plugin(id={plugin_id!r}, version={version!r}, viewers=[{plugin_id!r}], web="web",\n'
        f' panels=[PanelSpec({(plugin_id + "_panel")!r}, evaluate=lambda root, panel, values, spec: {{"test": True}})])\n')
    files = {f"{name}/__init__.py": manifest,
        f"{name}/web/index.js": 'export function activate(api) { api.registerViewer({id:"testplug", match:[".test"], render(file,el) {el.textContent=file.text;}}); }\n',
        f"{info}/METADATA": f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n" + ''.join(f'Requires-Dist: {req}\n' for req in requires),
        f"{info}/WHEEL": "Wheel-Version: 1.0\nGenerator: alfrd-tests\nRoot-Is-Purelib: true\nTag: py3-none-any\n"}
    if plugin_id is not None:
        files[f"{info}/entry_points.txt"] = f"[alfrd.plugins]\n{plugin_id} = {name}:plugin\n"
    rows = []
    for path, data in files.items():
        data = data.encode()
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode()
        rows.append(f"{path},sha256={digest},{len(data)}\n")
    files[f"{info}/RECORD"] = ''.join(rows) + f"{info}/RECORD,,\n"
    wheel = folder / f"{name}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as z:
        for path, data in files.items():
            z.writestr(path, data)
    return wheel
