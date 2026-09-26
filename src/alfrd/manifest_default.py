"""The default ``alfrd.yaml`` used for a folder that has none of its own.

ALFRD ships ``alfrd/web/assets/defaults/alfrd.yaml`` (an AVICA project
manifest). ``alfrd serve`` and the Studio read it for a project folder without
a local ``alfrd.yaml`` / ``.alfrd.yaml``; a local file always replaces it
completely (no merge). ``ALFRD_DEFAULT_MANIFEST=/path/file.yaml`` selects a
different default.

When the default is used its ``name`` becomes the folder name, so each folder
is its own ALFRD project. The Studio receives the text as ``alfrd.yaml``
(marked ``default: true`` in the scan), and saving Project settings writes a
local ``alfrd.yaml`` that supersedes it from then on.
"""

from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path
from typing import Any

LOCAL_NAMES = ("alfrd.yaml", ".alfrd.yaml")
ENV_VAR = "ALFRD_DEFAULT_MANIFEST"
#: Files that mark a folder as an AVICA project folder (for ``alfrd serve`` in the cwd).
PROJECT_MARKERS = ("avica.inp", "vasco.inp", "avica.summary.json", "avica.logs", "reductions")

_NAME_LINE = re.compile(r"^name:[^\n]*$", re.M)


def local_manifest(root: str | Path) -> Path | None:
    """The folder's own ``alfrd.yaml`` (or ``.alfrd.yaml``), if any."""
    for name in LOCAL_NAMES:
        path = Path(root) / name
        if path.is_file():
            return path
    return None


def default_manifest_path() -> Path | None:
    """``$ALFRD_DEFAULT_MANIFEST`` or the packaged default (None when neither exists)."""
    env = os.environ.get(ENV_VAR)
    if env:
        path = Path(env).expanduser()
        return path if path.is_file() else None
    try:
        from alfrd.web import web_root

        path = web_root() / "assets" / "defaults" / "alfrd.yaml"
    except FileNotFoundError:  # pragma: no cover - zipped installs
        return None
    return path if path.is_file() else None


def _project_name(root: str | Path | None) -> str | None:
    if root is None:
        return None
    return Path(root).expanduser().resolve().name or None


def default_manifest_text(root: str | Path | None = None) -> str | None:
    """Default manifest text, ``name:`` set to the folder name of ``root``."""
    path = default_manifest_path()
    if path is None:
        return None
    text = path.read_text(encoding="utf-8")
    name = _project_name(root)
    if name:
        line = f"name: {json.dumps(name)}"
        text, n = _NAME_LINE.subn(line, text, count=1)
        if not n:
            text = f"{line}\n{text}"
    return text


def default_manifest_data(root: str | Path | None = None) -> dict[str, Any]:
    """Parsed default manifest (``name`` = folder name of ``root``); {} when unavailable."""
    import yaml

    from alfrd.avica_layout import load_yaml_cached

    path = default_manifest_path()
    if path is None:
        return {}
    try:
        data = load_yaml_cached(path) or {}
    except (OSError, yaml.YAMLError):
        return {}
    if not isinstance(data, dict):
        return {}
    data = copy.deepcopy(data)
    name = _project_name(root)
    if name:
        data["name"] = name
    return data


def manifest_data(root: str | Path) -> tuple[dict[str, Any], Path | None, bool]:
    """``(data, path, is_default)``: the local manifest, else the default one."""
    import yaml

    from alfrd.avica_layout import load_yaml_cached

    path = local_manifest(root)
    if path is not None:
        try:
            data = load_yaml_cached(path) or {}
        except (OSError, yaml.YAMLError):
            data = {}
        return (data if isinstance(data, dict) else {}), path, False
    data = default_manifest_data(root)
    return data, (default_manifest_path() if data else None), bool(data)


def has_manifest(root: str | Path) -> bool:
    """True when the folder has a local manifest or a default one is available."""
    return local_manifest(root) is not None or default_manifest_path() is not None


def register_project_folder(service, folder: str | Path, *, allow_default: bool = True):
    """Register ``folder`` in the runtime database (local alfrd.yaml, else the default).

    Returns ``(project, used_default)``; an already registered folder is returned
    as is. Raises :class:`alfrd.manifest.ManifestError` when there is no manifest
    to use (or it is invalid).
    """
    from alfrd.manifest import ManifestError, load_manifest, parse_manifest
    from alfrd.runtime import RuntimeNotFound
    from alfrd.runtime.identity import project_identifier

    base = Path(folder).expanduser().resolve()
    if not base.is_dir():
        raise ManifestError(f"{base} is not a folder")
    path = local_manifest(base)
    used_default = path is None
    if path is not None:
        manifest = load_manifest(path)
    else:
        default = default_manifest_path() if allow_default else None
        if default is None:
            raise ManifestError(f"No alfrd.yaml in {base}")
        manifest = parse_manifest(default_manifest_data(base), source=default)
    try:
        return service.get_project_by_identifier(project_identifier(base, manifest.name)), used_default
    except RuntimeNotFound:
        pass
    project, _ = service.register_manifest(manifest, root_path=base, create_root=False)
    return project, used_default


#: Folder names never searched for sub-projects (big data trees and scratch space).
SKIP_DIRS = ("raw", "calibration_tables")
SKIP_PREFIXES = ("tmp_", ".")
SKIP_SUFFIXES = (".ms",)


def skip_dir(name: str) -> bool:
    """True for folders that are never searched (``*.ms``, ``raw``, ``tmp_*``, dot-folders, ...)."""
    low = name.lower()
    return name in SKIP_DIRS or name.startswith(SKIP_PREFIXES) or low.endswith(SKIP_SUFFIXES)


def _has_local_manifest(path: str) -> bool:
    return any(os.path.isfile(os.path.join(path, name)) for name in LOCAL_NAMES)


def discover_projects(root: str | Path, depth: int = 2, max_dirs: int = 2000) -> tuple[list[Path], bool]:
    """Sub-folders of ``root`` with their own ``alfrd.yaml`` / ``.alfrd.yaml``.

    Returns ``(folders, capped)`` sorted by relative path. Only local manifests
    count (never the packaged default). ``root`` itself is not checked. A
    project's own sub-folders belong to it and are not searched; ``*.ms``,
    ``raw``, ``tmp_*``, ``calibration_tables`` and dot-folders are skipped, as
    are symlinked and unreadable folders. At most ``max_dirs`` folders are
    listed; ``capped`` is True when that limit stopped the walk.
    """
    base = Path(root).expanduser().resolve()
    found: list[Path] = []
    queue: list[tuple[str, int]] = [(str(base), 0)]
    listed = 0
    capped = False
    while queue:
        path, level = queue.pop(0)
        if level >= max(0, int(depth)):
            continue
        if listed >= max_dirs:
            capped = True
            break
        listed += 1
        try:
            with os.scandir(path) as entries:
                children = sorted(
                    (e.name, e.path) for e in entries
                    if not skip_dir(e.name) and e.is_dir(follow_symlinks=False)
                )
        except OSError:  # unreadable or vanished: skip quietly
            continue
        for _name, child in children:
            try:
                is_project = _has_local_manifest(child)
            except OSError:
                continue
            if is_project:
                found.append(Path(child))
            else:
                queue.append((child, level + 1))
    found.sort(key=lambda p: p.relative_to(base).as_posix().lower())
    return found, capped


def looks_like_project(root: str | Path) -> bool:
    """A folder worth opening with the default manifest (AVICA config, logs or reductions)."""
    base = Path(root)
    return any((base / name).exists() for name in PROJECT_MARKERS)


__all__ = [
    "ENV_VAR",
    "LOCAL_NAMES",
    "default_manifest_data",
    "default_manifest_path",
    "default_manifest_text",
    "discover_projects",
    "has_manifest",
    "local_manifest",
    "looks_like_project",
    "manifest_data",
    "register_project_folder",
    "skip_dir",
]
