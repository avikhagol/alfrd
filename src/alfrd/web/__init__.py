"""Static, client-side ALFRD Studio.

This package only ships HTML/CSS/JS assets. They run entirely in the browser
and can be served three ways:

* ``alfrd studio`` - a dependency-free static server (``http.server``);
* ``alfrd serve`` - the Flask app mounts them at ``/studio/`` and the Studio
  then also reads live projects from the ALFRD runtime API;
* any static host such as GitHub Pages (see ``.github/workflows/pages.yml``).
"""

from __future__ import annotations

import shutil
from importlib import resources
from pathlib import Path

#: MIME types that must be exact for ES modules to load in every browser
#: (some Windows registries map ``.js`` to ``text/plain``).
MIME_TYPES = {
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".html": "text/html",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".webmanifest": "application/manifest+json",
    ".json": "application/json",
    ".yaml": "text/yaml",
    ".yml": "text/yaml",
}

#: Files that must exist in every build (checked by tests and ``alfrd studio``).
REQUIRED_ASSETS = (
    "index.html",
    "css/studio.css",
    "theme.css",
    "js/app.js",
    "js/components/overview.js",
    "js/components/canvas.js",
    "js/components/metadata.js",
    "js/components/results.js",
    "js/components/alfrd_config.js",
    "js/components/logs.js",
    "js/components/logview.js",
    "js/utils/yaml_parser.js",
    "js/utils/csv_parser.js",
    "js/utils/dom.js",
    "js/data/model.js",
    "js/data/importers.js",
    "js/data/demo.js",
    "js/data/server.js",
    "js/data/defs.js",
    "js/data/live.js",
    "js/data/presence.js",
    "assets/favicon.svg",
    "assets/favicon.ico",
    "assets/apple-touch-icon.png",
    "assets/site.webmanifest",
    "assets/templates/avica.yaml",
    "assets/defaults/alfrd.yaml",
)


def web_root() -> Path:
    """Return the on-disk directory that holds the Studio's static files."""
    root = resources.files(__name__)
    path = Path(str(root))
    if not (path / "index.html").is_file():  # pragma: no cover - zipped installs
        raise FileNotFoundError("ALFRD Studio assets are missing from this installation")
    return path


def missing_assets(root: Path | None = None) -> list[str]:
    base = root or web_root()
    return [name for name in REQUIRED_ASSETS if not (base / name).is_file()]


def export_site(destination: str | Path) -> Path:
    """Copy the Studio into ``destination`` (e.g. for GitHub Pages)."""
    target = Path(destination).expanduser().resolve()
    source = web_root()
    target.mkdir(parents=True, exist_ok=True)
    for item in source.rglob("*"):
        if item.is_dir() or item.suffix in {".py", ".pyc"} or "__pycache__" in item.parts:
            continue
        out = target / item.relative_to(source)
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, out)
    (target / ".nojekyll").write_text("", encoding="utf-8")
    return target


__all__ = ["MIME_TYPES", "REQUIRED_ASSETS", "export_site", "missing_assets", "web_root"]
