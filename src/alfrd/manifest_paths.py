"""Where a project's ``alfrd.yaml`` lives: one resolver for every reader and writer.

A project folder can hold its manifest in three places. Precedence is fixed,
highest first (:data:`MANIFEST_CANDIDATES`):

1. ``.alfrd/alfrd.yaml`` -- the canonical location (the Studio creates new
   manifests here);
2. ``alfrd.yaml`` in the project root;
3. ``.alfrd.yaml`` in the project root (legacy alias).

The highest existing file is the *active* manifest: it is the one read, and
the one every mutation writes. Lower files are *shadowed*: kept on disk,
never read, never written, never moved or deleted.

The project root of a nested manifest is the parent of ``.alfrd/``, so
relative paths inside it (``plan.csv``, ``targets.csv``, ``avica.target_dir``,
...) resolve exactly as they do for a root-level ``alfrd.yaml``.

Kept import-free (stdlib only) so hot paths can use it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

MANIFEST_FILENAME = "alfrd.yaml"
MANIFEST_ALIAS_FILENAME = ".alfrd.yaml"
#: The project-local folder ALFRD owns (configuration, run state, logs, locks).
STATE_DIRNAME = ".alfrd"
NESTED_MANIFEST = f"{STATE_DIRNAME}/{MANIFEST_FILENAME}"
#: Manifest locations relative to a project root, highest precedence first.
MANIFEST_CANDIDATES = (NESTED_MANIFEST, MANIFEST_FILENAME, MANIFEST_ALIAS_FILENAME)
#: Where a manifest created by the Studio goes when the project has none yet.
DEFAULT_NEW_MANIFEST = NESTED_MANIFEST


def project_manifest(root: str | Path) -> Path | None:
    """The active manifest of the project folder ``root`` (no parent walk), or None."""
    base = Path(root)
    for rel in MANIFEST_CANDIDATES:
        path = base / rel
        if path.is_file():
            return path
    return None


def has_project_manifest(root: str | Path) -> bool:
    return project_manifest(root) is not None


def project_root(manifest_path: str | Path) -> Path:
    """The project folder a manifest belongs to (``<root>/.alfrd/alfrd.yaml`` -> ``<root>``)."""
    path = Path(manifest_path)
    if path.parent.name == STATE_DIRNAME and path.name == MANIFEST_FILENAME:
        return path.parent.parent
    return path.parent


def is_manifest_name(path: str | Path) -> bool:
    """True for a file that can be a manifest at one of the supported locations."""
    path = Path(path)
    return path.name == MANIFEST_ALIAS_FILENAME or path.name == MANIFEST_FILENAME


def manifest_rel(root: str | Path) -> str | None:
    """The active manifest relative to ``root`` (``.alfrd/alfrd.yaml``, ...), or None."""
    path = project_manifest(root)
    return path.relative_to(Path(root)).as_posix() if path is not None else None


def new_manifest_path(root: str | Path) -> Path:
    """The active manifest, or where a new one is created (``.alfrd/alfrd.yaml``)."""
    return project_manifest(root) or Path(root) / DEFAULT_NEW_MANIFEST


def manifest_location(root: str | Path) -> dict[str, Any]:
    """Which manifest files exist in ``root`` and which one is active.

    ``{"active", "default_new", "files": [{"rel", "exists", "active", "shadowed"}]}``;
    ``shadowed`` marks an existing file ignored because a higher one exists.
    """
    active = manifest_rel(root)
    files = []
    for rel in MANIFEST_CANDIDATES:
        exists = (Path(root) / rel).is_file()
        files.append({"rel": rel, "exists": exists, "active": rel == active,
                      "shadowed": exists and active is not None and rel != active})
    return {"active": active, "default_new": DEFAULT_NEW_MANIFEST, "files": files}


__all__ = [
    "DEFAULT_NEW_MANIFEST",
    "MANIFEST_ALIAS_FILENAME",
    "MANIFEST_CANDIDATES",
    "MANIFEST_FILENAME",
    "NESTED_MANIFEST",
    "STATE_DIRNAME",
    "has_project_manifest",
    "is_manifest_name",
    "manifest_location",
    "manifest_rel",
    "new_manifest_path",
    "project_manifest",
    "project_root",
]
