"""Collections: folders of viewable files declared as ``kind: collection`` artifacts.

The avica template ships rPicard's diagnostics::

    - name: rpicard_diagnostics
      kind: collection
      path_pattern: "{workdir}/wd_{band}_{target}/diagnostics_*"
      run_order: newest      # the newest run first (by the timestamp in the name, else mtime)
      depth: 3               # how far below a run folder files are listed
      include: ["*/*.png", "*.pdf", ...]   # relative to the run folder; * stays in one folder
      exclude: ["FLAGTABLES/*"]
      pinned:  ["*/SUMMARY_*.pdf", "fringes_overview.csv.*"]

Each folder matching ``path_pattern`` is one *run*. Nothing below a run is read
until it is asked for: ``list_runs`` only lists the run folders; ``list_files``
walks one run (bounded by ``depth`` and ``MAX_FILES``); ``resolve_file`` checks
that a requested path is a listed file of a declared run before it is served.
Import-free apart from the layout reader.
"""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

#: Files shown inline (by suffix). Anything else is offered as a download.
INLINE_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".list": "text/plain",
    ".log": "text/plain",
    ".inp": "text/plain",
    ".out": "text/plain",
    ".obs": "text/plain",
}
#: Never inline (scriptable in a browser): served as attachments only.
NEVER_INLINE = frozenset({".svg", ".html", ".htm", ".xhtml", ".js", ".xml"})
MAX_FILES = 5000
MAX_RUNS = 200
_STAMP = re.compile(r"(\d{4})-?(\d{2})-?(\d{2})[_T-]?(\d{2})[-_:]?(\d{2})[-_:]?(\d{2})")
_PLACEHOLDER = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


class CollectionError(ValueError):
    """Unknown collection or run, or a path that is not part of it."""


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if str(v).strip()]
    return [str(value)] if str(value).strip() else []


def collections(root: str | Path) -> list[dict[str, Any]]:
    """The ``kind: collection`` artifacts of alfrd.yaml (template first, alfrd.yaml overrides by name)."""
    from alfrd.studio_defs import studio_manifest

    manifest = studio_manifest(Path(root))
    out = []
    for item in manifest.get("artifacts") or []:
        if isinstance(item, Mapping) and item.get("kind") == "collection" and item.get("name") and item.get("path_pattern"):
            out.append(dict(item))
    return out


def _spec(root: Path, name: str) -> dict[str, Any]:
    spec = next((c for c in collections(root) if c["name"] == name), None)
    if spec is None:
        raise CollectionError(f"no collection named {name!r} in alfrd.yaml")
    return spec


def _kind(name: str) -> str:
    low = name.lower()
    suffix = PurePosixPath(low).suffix
    if suffix in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
        return "image"
    if suffix == ".pdf":
        return "pdf"
    if suffix in (".ps", ".eps"):
        return "postscript"
    if ".csv" in low or suffix == ".tsv":
        return "table"
    if suffix in INLINE_TYPES:
        return "text"
    if "." not in name or re.search(r"\.(log|out)_", low):
        return "text"
    return "file"


def _stamp(name: str) -> str:
    """``YYYY-MM-DD HH:MM:SS`` found in a run folder name, or ''."""
    m = _STAMP.search(name)
    return f"{m[1]}-{m[2]}-{m[3]} {m[4]}:{m[5]}:{m[6]}" if m else ""


def _workdirs(root: Path) -> list[dict[str, Any]]:
    from alfrd.avica_layout import layout_patterns, resolve_config, resolve_dir, scan_project_codes

    cfg = resolve_config(root)
    target_dir = resolve_dir(root, cfg.get("target_dir")) or root / "reductions"
    patterns = layout_patterns(root)
    return [{"id": c.id, "code": c.code, "wd": c.wd} for c in scan_project_codes(root, target_dir, patterns)]


def _expand(base: Path, pattern: str, fixed: Mapping[str, str]) -> list[tuple[Path, dict[str, str]]]:
    """Folders below ``base`` matching ``pattern`` (segments with {name} or globs), with the captured values."""
    results: list[tuple[Path, dict[str, str]]] = [(base, {})]
    for segment in [s for s in pattern.split("/") if s and s != "."]:
        names = _PLACEHOLDER.findall(segment)
        literal = not names and not any(ch in segment for ch in "*?[")
        nxt: list[tuple[Path, dict[str, str]]] = []
        for folder, values in results:
            if literal:
                child = folder / segment
                if child.is_dir():
                    nxt.append((child, values))
                continue
            regex = _segment_regex(segment, {**fixed, **values})
            try:
                entries = sorted(os.scandir(folder), key=lambda e: e.name)
            except OSError:
                continue
            for entry in entries:
                if not entry.is_dir(follow_symlinks=False):
                    continue
                m = regex.fullmatch(entry.name)
                if m:
                    nxt.append((Path(entry.path), {**values, **{k: v for k, v in m.groupdict().items() if v is not None}}))
        results = nxt
        if not results:
            break
    return results


def _segment_regex(segment: str, fixed: Mapping[str, str]) -> re.Pattern[str]:
    out = []
    pos = 0
    seen: set[str] = set()
    for m in _PLACEHOLDER.finditer(segment):
        out.append(_glob_part(segment[pos:m.start()]))
        name = m.group(1)
        if name in fixed:
            out.append(re.escape(str(fixed[name])))
        elif name in seen:
            out.append(f"(?P={name})")
        else:
            seen.add(name)
            out.append(f"(?P<{name}>.+?)")
        pos = m.end()
    out.append(_glob_part(segment[pos:]))
    return re.compile("".join(out))


def _glob_part(text: str) -> str:
    return fnmatch.translate(text)[4:-3] if text else ""  # strip (?s: … )\Z


def list_runs(root: str | Path, name: str, *, code: str | None = None, target: str | None = None,
              band: str | None = None) -> dict[str, Any]:
    """Runs of one collection: ``{collection, runs: [{id, rel, code, workdir, band, target, stamp, mtime}]}``.

    ``{workdir}`` is each AVICA work dir; other placeholders are matched and
    reported (``band``, ``target``). ``code`` / ``target`` / ``band`` filter.
    Newest first unless ``run_order: oldest``.
    """
    base = Path(root).resolve()
    spec = _spec(base, name)
    pattern = str(spec["path_pattern"])
    fixed = {k: v for k, v in (("target", target), ("band", band)) if v}
    runs: list[dict[str, Any]] = []
    starts = _workdirs(base) if "{workdir}" in pattern else [{"id": "", "code": "", "wd": base}]
    rest = pattern.split("{workdir}", 1)[-1].lstrip("/") if "{workdir}" in pattern else pattern
    for wd in starts:
        if code and wd["code"] != code and wd["id"] != code:
            continue
        for folder, values in _expand(wd["wd"], rest, fixed):
            values = {**fixed, **values}
            try:
                mtime = folder.stat().st_mtime
            except OSError:
                continue
            rel = os.path.relpath(folder, base).replace(os.sep, "/")
            runs.append({"id": rel, "rel": rel, "name": folder.name, "code": wd["code"], "workdir": wd["id"],
                         "band": values.get("band", ""), "target": values.get("target", ""),
                         "stamp": _stamp(folder.name), "mtime": mtime})
    newest = str(spec.get("run_order") or "newest") != "oldest"
    runs.sort(key=lambda r: (r["stamp"] or "", r["mtime"]), reverse=newest)
    return {"collection": public_spec(spec), "runs": runs[:MAX_RUNS], "truncated": len(runs) > MAX_RUNS}


def public_spec(spec: Mapping[str, Any]) -> dict[str, Any]:
    return {k: spec.get(k) for k in ("name", "description", "step", "viewer", "path_pattern", "pinned", "run_order") if spec.get(k) is not None}


def _run_folder(base: Path, spec: Mapping[str, Any], run: str) -> Path:
    """The run folder for ``run`` (its path relative to the root), checked against ``path_pattern``."""
    rel = PurePosixPath(str(run or ""))
    if not run or rel.is_absolute() or ".." in rel.parts:
        raise CollectionError("run must be a path relative to the project root")
    folder = (base / rel).resolve()
    if base not in folder.parents or not folder.is_dir():
        raise CollectionError(f"run {run!r} not found")
    pattern = str(spec["path_pattern"])
    rest = pattern.split("{workdir}", 1)[-1].lstrip("/") if "{workdir}" in pattern else pattern
    regex = re.compile("/".join(_segment_regex(s, {}).pattern for s in rest.split("/") if s and s != "."))
    tail_len = len([s for s in rest.split("/") if s and s != "."])
    tail = "/".join(rel.parts[-tail_len:]) if tail_len else ""
    if not tail_len or len(rel.parts) < tail_len or not regex.fullmatch(tail):
        raise CollectionError(f"{run!r} is not a run of collection {spec['name']!r}")
    if "{workdir}" in pattern:
        head = (base / PurePosixPath(*rel.parts[:-tail_len])).resolve()
        if head not in {w["wd"].resolve() for w in _workdirs(base)}:
            raise CollectionError(f"{run!r} is not below an AVICA work dir")
    return folder


def _match(rel: str, patterns: Iterable[str]) -> bool:
    """fnmatch where ``*`` does not cross ``/``."""
    for p in patterns:
        if re.fullmatch("/".join(_glob_part(s) for s in p.split("/")), rel):
            return True
    return False


def _walk(folder: Path, depth: int) -> Iterable[tuple[str, os.DirEntry[str]]]:
    stack = [(folder, "", 0)]
    while stack:
        current, prefix, level = stack.pop()
        try:
            entries = sorted(os.scandir(current), key=lambda e: e.name)
        except OSError:
            continue
        dirs = []
        for entry in entries:
            rel = f"{prefix}{entry.name}"
            if entry.is_dir(follow_symlinks=False):
                if level + 1 < depth and not entry.name.endswith((".ms", ".flagversions")):
                    dirs.append((Path(entry.path), f"{rel}/", level + 1))
            elif entry.is_file(follow_symlinks=False):
                yield rel, entry
        stack.extend(reversed(dirs))


def list_files(root: str | Path, name: str, run: str, *, folder: str | None = None) -> dict[str, Any]:
    """Files of one run: ``{run, groups: [{folder, count, bytes}], files: [{path, kind, size, mtime, pinned}]}``.

    ``folder`` limits ``files`` to one sub folder ('' = the run folder itself);
    ``groups`` always covers the whole run.
    """
    base = Path(root).resolve()
    spec = _spec(base, name)
    run_dir = _run_folder(base, spec, run)
    include = _as_list(spec.get("include")) or ["*", "*/*"]
    exclude = _as_list(spec.get("exclude"))
    pinned = _as_list(spec.get("pinned"))
    depth = max(1, int(spec.get("depth") or 2))
    groups: dict[str, dict[str, Any]] = {}
    files: list[dict[str, Any]] = []
    total = 0
    for rel, entry in _walk(run_dir, depth):
        if not _match(rel, include) or _match(rel, exclude):
            continue
        total += 1
        if total > MAX_FILES:
            break
        top = rel.split("/", 1)[0] if "/" in rel else ""
        try:
            stat = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        group = groups.setdefault(top, {"folder": top, "count": 0, "bytes": 0, "kinds": {}})
        group["count"] += 1
        group["bytes"] += stat.st_size
        kind = _kind(entry.name)
        group["kinds"][kind] = group["kinds"].get(kind, 0) + 1
        is_pinned = _match(rel, pinned)
        if folder is None or top == folder or is_pinned:
            files.append({"path": rel, "name": entry.name, "folder": top, "kind": kind, "size": stat.st_size,
                          "mtime": stat.st_mtime, "pinned": is_pinned})
    ordered = sorted(groups.values(), key=lambda g: (g["folder"] != "", g["folder"].lower()))
    return {"run": run, "collection": public_spec(spec), "groups": ordered, "files": files, "truncated": total > MAX_FILES}


def resolve_file(root: str | Path, name: str, run: str, path: str) -> tuple[Path, str, bool]:
    """``(file, media type, inline)`` for a file of a run; ``CollectionError`` when it is not one."""
    base = Path(root).resolve()
    spec = _spec(base, name)
    run_dir = _run_folder(base, spec, run)
    rel = PurePosixPath(str(path or ""))
    if not path or rel.is_absolute() or ".." in rel.parts:
        raise CollectionError("path must be relative to the run folder")
    if len(rel.parts) > max(1, int(spec.get("depth") or 2)):
        raise CollectionError(f"{path!r} is deeper than the collection's depth")
    posix = rel.as_posix()
    include = _as_list(spec.get("include")) or ["*", "*/*"]
    if not _match(posix, include) or _match(posix, _as_list(spec.get("exclude"))):
        raise CollectionError(f"{path!r} is not part of collection {name!r}")
    target = (run_dir / rel).resolve()
    if run_dir not in target.parents or not target.is_file():
        raise FileNotFoundError(f"{path} not found")
    suffix = rel.suffix.lower()
    if suffix in NEVER_INLINE:
        return target, "application/octet-stream", False
    kind = _kind(rel.name)
    media = INLINE_TYPES.get(suffix) or ("text/plain" if kind in ("text", "table") else "application/octet-stream")
    return target, media, media != "application/octet-stream"


__all__ = ["CollectionError", "INLINE_TYPES", "MAX_FILES", "collections", "list_files", "list_runs", "public_spec", "resolve_file"]
