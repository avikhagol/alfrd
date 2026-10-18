"""Measurement Sets declared in alfrd.yaml, and a confined file explorer for a project.

Discovery (:func:`measurement_sets`) reads the active manifest's ``artifacts:`` (merged
with its template): every ``kind: directory`` entry whose ``path_pattern`` or
``fallback_patterns`` end in ``.ms`` (the packaged default has ``measurement_sets``:
``{workdir}/**/*.ms``). No new configuration field. ``**`` matches any number of folders.

The explorer (:func:`list_dir`) lists one folder below the project root and follows
the :mod:`alfrd.artifact_collections` walk rules: the resolved path must stay inside
the root (``realpath`` + ``commonpath``), ``..`` and absolute paths are refused,
symlinks are shown but never followed when they point outside, and ``.ms`` /
``.flagversions`` folders are leaves (never entered). Hidden names are not listed.
"""
from __future__ import annotations

import os
import re
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

from alfrd.artifact_collections import LEAF_SUFFIXES, _segment_regex

MAX_ENTRIES = 2000
MAX_MS = 500
MAX_VISITS = 20000
MAX_DEPTH = 8


class ExplorerError(ValueError):
    """A path the explorer refuses (``..``, absolute, outside the project, not a folder)."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _inside(base: str, path: str) -> bool:
    try:
        return os.path.commonpath([base, path]) == base
    except ValueError:  # different drives
        return False


def _is_ms(name: str) -> bool:
    return name.lower().endswith(".ms")


def _leaf(name: str) -> bool:
    return name.lower().endswith(LEAF_SUFFIXES)


def _clean(rel: str | None) -> str:
    text = str(rel or "").replace("\\", "/").strip()
    if "\x00" in text:
        raise ExplorerError("invalid path")
    path = PurePosixPath(text)
    if text.startswith("/") or path.is_absolute() or re.match(r"^[A-Za-z]:", text):
        raise ExplorerError("path must be relative to the project folder")
    if ".." in path.parts:
        raise ExplorerError("'..' is not allowed in a path")
    return "/".join(p for p in path.parts if p not in ("", "."))


def _entry(base: str, entry: os.DirEntry[str], rel: str) -> dict[str, Any] | None:
    try:
        link = entry.is_symlink()
        if link:
            target = os.path.realpath(entry.path)
            outside = not _inside(base, target)
            exists = os.path.exists(target)
            is_dir = exists and os.path.isdir(target)
        else:
            outside, exists = False, True
            is_dir = entry.is_dir(follow_symlinks=False)
        size = None if is_dir or not exists or outside else entry.stat(follow_symlinks=not link).st_size
    except OSError:
        return None
    kind = "ms" if is_dir and _is_ms(entry.name) else "dir" if is_dir else "file" if exists else "broken"
    out: dict[str, Any] = {"name": entry.name, "path": rel, "kind": kind}
    if size is not None:
        out["size"] = size
    if link:
        out["link"] = True
        out["outside"] = outside
    # Only folders inside the project that are not leaves (.ms, .flagversions) can be opened.
    out["browsable"] = kind == "dir" and not outside and not _leaf(entry.name)
    if outside:
        out["kind"] = "link"
    return out


def list_dir(root: str | Path, rel: str | None = "") -> dict[str, Any]:
    """One folder of the project: ``{root, path, parent, entries: [{name, path, kind, size?, link?, outside?, browsable}], truncated}``.

    ``kind`` is ``dir``, ``ms`` (a ``.ms`` folder: a leaf), ``file``, ``link`` (a symlink that leaves the
    project; never followed) or ``broken``. Raises :class:`ExplorerError` (``status`` 400/403/404).
    """
    base = os.path.realpath(Path(root))
    clean = _clean(rel)
    folder = os.path.join(base, *clean.split("/")) if clean else base
    real = os.path.realpath(folder)
    if not _inside(base, real):
        raise ExplorerError(f"{clean} is a link that leaves the project folder", 403)
    if not os.path.exists(real):
        raise ExplorerError(f"{clean or 'the project folder'} does not exist", 404)
    if not os.path.isdir(real):
        raise ExplorerError(f"{clean} is not a folder", 400)
    if clean and any(_leaf(part) for part in clean.split("/")):
        raise ExplorerError(f"{clean} is a Measurement Set or flag-version folder; it is opened, not browsed", 400)
    entries: list[dict[str, Any]] = []
    truncated = False
    try:
        listing = sorted(os.scandir(real), key=lambda e: e.name.casefold())
    except PermissionError:
        raise ExplorerError(f"{clean or 'the project folder'} cannot be read", 403) from None
    except OSError as exc:
        raise ExplorerError(f"{clean or 'the project folder'} cannot be read ({exc.strerror or exc})", 400) from None
    for entry in listing:
        if entry.name.startswith("."):
            continue
        if len(entries) >= MAX_ENTRIES:
            truncated = True
            break
        item = _entry(base, entry, f"{clean}/{entry.name}" if clean else entry.name)
        if item is not None:
            entries.append(item)
    order = {"ms": 0, "dir": 1, "link": 2, "file": 3, "broken": 4}
    entries.sort(key=lambda e: (order.get(e["kind"], 9) > 1, e["name"].casefold()))
    parent = None if not clean else ("/".join(clean.split("/")[:-1]))
    return {"root": base, "path": clean, "parent": parent, "entries": entries, "truncated": truncated}


def _ms_patterns(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    out = []
    for item in manifest.get("artifacts") or []:
        if not isinstance(item, Mapping) or item.get("kind") != "directory":
            continue
        raw = [item.get("path_pattern"), *(item.get("fallback_patterns") or [])] if isinstance(item.get("fallback_patterns"), list) \
            else [item.get("path_pattern")]
        patterns = [str(p).rstrip("/") for p in raw if isinstance(p, str) and _is_ms(str(p).rstrip("/"))]
        if patterns:
            out.append({"name": str(item.get("name") or ""), "description": str(item.get("description") or ""), "patterns": patterns})
    return out


def _below(base: str, start: str, rest: list[str]) -> Iterable[str]:
    """Folders below ``start`` (any depth: the ``**``) whose last segments match ``rest``; no symlinks, no leaves."""
    regexes = [_segment_regex(seg, {}) for seg in rest]
    stack, visits = [(start, [], 0)], 0
    while stack:
        folder, parts, depth = stack.pop()
        try:
            listing = sorted(os.scandir(folder), key=lambda e: e.name)
        except OSError:
            continue
        nested = []
        for entry in listing:
            visits += 1
            if visits > MAX_VISITS:
                return
            try:
                if not entry.is_dir(follow_symlinks=False) or entry.name.startswith("."):
                    continue
            except OSError:
                continue
            chain = [*parts, entry.name]
            if len(chain) >= len(regexes) and all(r.fullmatch(n) for r, n in zip(regexes, chain[-len(regexes):])):
                yield entry.path
            if not _leaf(entry.name) and depth + 1 < MAX_DEPTH:
                nested.append((entry.path, chain, depth + 1))
        stack.extend(reversed(nested))


def measurement_sets(root: str | Path) -> dict[str, Any]:
    """``.ms`` folders the active alfrd.yaml declares: ``{configured, artifacts, entries: [{path, name, artifact}], notes, truncated}``.

    ``configured`` is false when no ``kind: directory`` artifact pattern ends in ``.ms``: the Studio then
    offers the explorer instead.
    """
    from alfrd.studio_defs import ScanCache, _expansions, normalize_pattern, studio_context

    base = os.path.realpath(Path(root))
    ctx = studio_context(Path(base))
    artifacts = _ms_patterns(ctx["manifest"])
    found: dict[str, dict[str, Any]] = {}
    notes: list[str] = []
    cache = ScanCache()
    truncated = False
    for artifact in artifacts:
        for pattern in artifact["patterns"]:
            if ("{workdir}" in pattern or "{meta_dir}" in pattern) and not ctx["workdirs"]:
                notes.append(f"{pattern}: no work folders found for {{workdir}}")
                continue
            text = normalize_pattern(pattern)
            segs = text.split("/")
            if "**" in segs:
                cut = segs.index("**")
                prefix, rest = "/".join(segs[:cut]), [s for s in segs[cut + 1:] if s and s != "**"]
                starts = [os.path.join(base, rel) for rel, _g, _w in _expansions(Path(base), prefix, ctx, {}, kind="directory", cache=cache)] \
                    if prefix else [base]
                hits: Iterable[str] = (p for start in starts for p in _below(base, start, rest or ["*.ms"]))
            else:
                hits = (os.path.join(base, rel) for rel, _g, _w in _expansions(Path(base), text, ctx, {}, kind="directory", cache=cache))
            for path in hits:
                real = os.path.realpath(path)
                if not _inside(base, real) or not os.path.isdir(real):
                    continue  # a symlink out of the project is never offered
                rel = os.path.relpath(path, base).replace(os.sep, "/")
                if rel in found:
                    continue
                if len(found) >= MAX_MS:
                    truncated = True
                    break
                found[rel] = {"path": rel, "name": os.path.basename(path), "artifact": artifact["name"]}
    entries = sorted(found.values(), key=lambda e: (e["path"].casefold(), e["path"]))
    return {"root": base, "configured": bool(artifacts), "artifacts": artifacts, "entries": entries, "notes": notes,
            "truncated": truncated}


__all__ = ["ExplorerError", "MAX_ENTRIES", "MAX_MS", "list_dir", "measurement_sets"]
