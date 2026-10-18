"""Move a project's configuration to ``.alfrd/alfrd.yaml`` (never silently, never destructive).

:func:`plan` describes what would happen (no writes); :func:`apply` does it. A
root ``alfrd.yaml`` (or the legacy ``.alfrd.yaml``) is copied to
``.alfrd/alfrd.yaml`` and, unless ``keep="shadow"``, renamed to
``<name>.migrated`` so nobody edits a file that is no longer read. Nothing is
deleted. Relative paths inside the manifest keep resolving against the project
root, so the text is copied byte for byte.

When ``.alfrd/alfrd.yaml`` already exists and differs from the root file, the
plan is a ``conflict``: it carries a diff and :func:`apply` needs ``force=True``
(the nested file is kept as ``.alfrd/alfrd.yaml.bak``).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from alfrd.manifest_paths import MANIFEST_ALIAS_FILENAME, MANIFEST_FILENAME, NESTED_MANIFEST

KEEP_MODES = ("rename", "shadow")
SUFFIX = ".migrated"


class MigrateError(ValueError):
    """Nothing to migrate, or the manifest is not valid."""


class MigrateConflict(MigrateError):
    """``.alfrd/alfrd.yaml`` exists with different content; ``force`` is needed."""

    def __init__(self, plan: dict[str, Any]) -> None:
        self.plan = plan
        super().__init__(f"{NESTED_MANIFEST} already exists with different content")


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def _same(a: str, b: str) -> bool:
    return a.rstrip("\n") == b.rstrip("\n")


def _validate(text: str, rel: str) -> str | None:
    import yaml

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as error:
        return f"{rel} is not valid YAML: {(str(error).splitlines() or [''])[0]}"
    if not isinstance(data, dict):
        return f"{rel} must be a YAML mapping"
    if not str(data.get("name") or "").strip():
        return f"{rel} needs a `name`"
    return None


def _free_name(base: Path, source: str) -> str:
    name = source + SUFFIX
    if (base / name).exists():
        name = f"{source}.{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}{SUFFIX}"
    return name


def plan(root: str | Path, keep: str = "rename") -> dict[str, Any]:
    """What a migration would do; no writes.

    ``status``: ``no_manifest``, ``nothing`` (already nested, no root file), ``move``,
    ``cleanup`` (an identical shadowed root file: only the rename) or ``conflict``.
    """
    from alfrd.history import unified

    if keep not in KEEP_MODES:
        raise ValueError(f"keep must be one of {', '.join(KEEP_MODES)}")
    base = Path(root)
    source = next((rel for rel in (MANIFEST_FILENAME, MANIFEST_ALIAS_FILENAME) if (base / rel).is_file()), None)
    out: dict[str, Any] = {"status": "", "source": source, "target": NESTED_MANIFEST, "keep": keep,
                           "rename_to": None, "diff": "", "error": None, "valid": True, "will_write": False}
    nested_text = _read(base / NESTED_MANIFEST)
    if source is None:
        out["status"] = "nothing" if nested_text is not None else "no_manifest"
        return out
    text = _read(base / source) or ""
    out["error"] = _validate(text, source)
    out["valid"] = out["error"] is None
    if keep == "rename":
        out["rename_to"] = _free_name(base, source)
    if nested_text is None:
        out.update(status="move", will_write=True)
    elif _same(nested_text, text):
        out["status"] = "cleanup"
    else:
        out.update(status="conflict", will_write=True,
                   diff=unified(nested_text, text, f"{NESTED_MANIFEST} (active)", f"{source} (shadowed)"))
    return out


def apply(root: str | Path, keep: str = "rename", force: bool = False) -> dict[str, Any]:
    """Run the migration; returns the plan plus ``done`` and the resulting ``manifest_location``."""
    from alfrd import history
    from alfrd.manifest_paths import manifest_location

    base = Path(root).resolve()
    info = plan(base, keep)
    if info["status"] == "no_manifest":
        raise MigrateError("the project has no alfrd.yaml")
    if info["status"] == "nothing":
        raise MigrateError("no alfrd.yaml at the project root to move")
    if not info["valid"]:
        raise MigrateError(info["error"])
    if info["status"] == "conflict" and not force:
        raise MigrateConflict(info)
    source = base / info["source"]
    nested = base / NESTED_MANIFEST
    text = source.read_text(encoding="utf-8")
    if info["will_write"]:
        try:
            history.ensure_baseline(base, "alfrd.yaml")  # the version being replaced stays restorable
        except history.HistoryError:
            pass
        if nested.is_file():
            nested.with_name(nested.name + ".bak").write_bytes(nested.read_bytes())
        nested.parent.mkdir(parents=True, exist_ok=True)
        history.atomic_write(nested, text)
        try:
            history.record(base, "alfrd.yaml", text, source="migrate", message=f"moved {info['source']} to {NESTED_MANIFEST}")
        except history.HistoryError:
            pass
    if info["rename_to"]:
        os.replace(source, base / info["rename_to"])
    return {**info, "done": True, "manifest_location": manifest_location(base)}
