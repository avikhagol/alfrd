"""The project's target list: ``alfrd.targets.csv`` (target, FITS file names, project code).

Import-free (YAML + csv only). Declared in alfrd.yaml::

    targets:
      csv: alfrd.targets.csv        # relative to alfrd.yaml
      columns:                      # header names accepted on import (case-insensitive)
        key:   [TARGET_NAME, source, source_name, target, name]
        files: [FILENAMES, fitsfilenames, fits, fitsidi, files]
        code:  [PROJECT_CODE, project_code, code]

The file is the inventory the Overview and the Run dialog share. It is kept
across plans; ``execution.plan_csv`` stays the state of one run. Written column
names are the plan CSV's (``execution.key_column`` / ``files_column`` /
``code_column``), so the two files read the same. File names in a cell are
written ``a,b`` (the plan CSV and the commands use the same form).
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from alfrd.runtime import plan_csv as pc

#: Mirrors the ``targets:`` block of templates/avica.yaml and defaults/alfrd.yaml.
DEFAULT_TARGETS: dict[str, Any] = {
    "csv": "alfrd.targets.csv",
    "columns": {
        "key": ["TARGET_NAME", "source", "source_name", "target", "name"],
        "files": ["FILENAMES", "fitsfilenames", "fits", "fitsidi", "files"],
        "code": ["PROJECT_CODE", "project_code", "code"],
    },
}
MODES = ("merge", "replace")


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if str(v).strip()]
    return [str(value)] if str(value).strip() else []


@dataclass
class TargetsSpec:
    root: Path
    csv: Path
    key_column: str
    files_column: str
    code_column: str
    aliases: dict[str, list[str]] = field(default_factory=dict)

    @property
    def lock(self) -> Path:
        return self.root / ".alfrd" / "locks" / f"{self.csv.name}.lock"

    @property
    def rel(self) -> str:
        try:
            return self.csv.relative_to(self.root).as_posix()
        except ValueError:
            return str(self.csv)

    def to_dict(self) -> dict[str, Any]:
        return {"csv": self.rel, "exists": self.csv.is_file(), "key_column": self.key_column,
                "files_column": self.files_column, "code_column": self.code_column, "columns": self.aliases}


def load_spec(root: str | Path) -> TargetsSpec:
    """The ``targets:`` block (template, then alfrd.yaml), columns named like the plan CSV."""
    from alfrd.execution import load_execution
    from alfrd.studio_defs import studio_manifest

    base = Path(root).expanduser().resolve()
    block = studio_manifest(base).get("targets")
    block = block if isinstance(block, Mapping) else {}
    cfg = load_execution(base)
    names = {"key": cfg.key_column, "files": cfg.files_column, "code": cfg.code_column}
    raw_columns = block.get("columns")
    declared: Mapping[str, Any] = raw_columns if isinstance(raw_columns, Mapping) else {}
    aliases = {}
    for role, written in names.items():
        accepted = _as_list(declared.get(role)) or DEFAULT_TARGETS["columns"][role]
        aliases[role] = list(dict.fromkeys([written, *accepted]))
    path = Path(str(block.get("csv") or DEFAULT_TARGETS["csv"])).expanduser()
    return TargetsSpec(root=base, csv=path if path.is_absolute() else base / path, key_column=names["key"],
                       files_column=names["files"], code_column=names["code"], aliases=aliases)


def _column(header: Sequence[str], accepted: Sequence[str]) -> str | None:
    lowered = {h.strip().lower(): h for h in header if h}
    return next((lowered[a.lower()] for a in accepted if a.lower() in lowered), None)


def detect_columns(header: Sequence[str], spec: TargetsSpec, mapping: Mapping[str, str] | None = None) -> dict[str, str | None]:
    """Which header column holds key / files / code: an explicit ``mapping`` first, then the aliases."""
    out: dict[str, str | None] = {}
    for role in ("key", "files", "code"):
        chosen = (mapping or {}).get(role)
        out[role] = chosen if chosen in header else _column(header, spec.aliases[role])
    return out


def parse(text: str, spec: TargetsSpec, mapping: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Rows of an imported CSV/TSV: ``{header, columns, rows: [{target, files, code, extra}], problems}``.

    Problems carry the file's line number (the header is line 1): empty or unusable target
    names (``#…``, ``@``), a (target, code) seen twice, no key column.
    """
    body = text.lstrip("\ufeff")
    first = body.splitlines()[0] if body.strip() else ""
    delimiter = "\t" if first.count("\t") > first.count(",") else ","
    reader = csv.DictReader(io.StringIO(body), delimiter=delimiter)
    header = [h for h in (reader.fieldnames or []) if h is not None]
    cols = detect_columns(header, spec, mapping)
    problems: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    if not cols["key"]:
        problems.append({"line": 0, "message": f"no target column (looked for {', '.join(spec.aliases['key'])})"})
        return {"header": header, "columns": cols, "rows": rows, "problems": problems}
    seen: dict[str, int] = {}
    used = {c for c in cols.values() if c}
    for raw in reader:
        n = reader.line_num
        name = str(raw.get(cols["key"]) or "").strip()
        if not name and not any(str(v or "").strip() for v in raw.values()):
            continue  # blank line
        try:
            name = pc.check_target(name)
        except ValueError as error:
            problems.append({"line": n, "message": str(error)})
            continue
        code = str(raw.get(cols["code"]) or "").strip() if cols["code"] else ""
        key = pc.row_key(name, code)
        if key in seen:
            problems.append({"line": n, "message": f"{key} repeats line {seen[key]}"})
            continue
        seen[key] = n
        files = pc.join_files(str(raw.get(cols["files"]) or "").replace(";", ",")) if cols["files"] else ""
        extra = {k: str(v or "") for k, v in raw.items() if k and k not in used}
        rows.append({"target": name, "files": files, "code": code, "extra": extra})
    return {"header": header, "columns": cols, "rows": rows, "problems": problems}


def read(spec: TargetsSpec) -> dict[str, Any]:
    """The current targets file (empty table when it does not exist yet)."""
    if not spec.csv.is_file():
        return {"header": [], "columns": {}, "rows": [], "problems": []}
    return parse(spec.csv.read_text(encoding="utf-8-sig"), spec)


def merge(existing: Sequence[Mapping[str, Any]], incoming: Sequence[Mapping[str, Any]], mode: str = "merge") -> dict[str, Any]:
    """New table plus what changed: ``{rows, added, updated, unchanged}`` (lists of row keys).

    ``merge`` keeps existing rows and updates the files (and extra columns)
    of rows with the same (target, code); an empty incoming files cell never erases a known one. ``replace``
    is the incoming table as it is.
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of {', '.join(MODES)}")
    old = {pc.row_key(r["target"], r.get("code", "")): dict(r) for r in existing}
    added: list[str] = []
    updated: list[str] = []
    unchanged: list[str] = []
    if mode == "replace":
        out = [dict(r) for r in incoming]
        for r in out:
            key = pc.row_key(r["target"], r.get("code", ""))
            prev = old.get(key)
            (added if prev is None else unchanged if prev.get("files", "") == r.get("files", "") else updated).append(key)
        return {"rows": out, "added": added, "updated": updated, "unchanged": unchanged,
                "removed": [k for k in old if k not in {pc.row_key(r["target"], r.get("code", "")) for r in out}]}
    out = [dict(r) for r in existing]
    index = {pc.row_key(r["target"], r.get("code", "")): i for i, r in enumerate(out)}
    for r in incoming:
        key = pc.row_key(r["target"], r.get("code", ""))
        if key not in index:
            index[key] = len(out)
            out.append(dict(r))
            added.append(key)
            continue
        cur = out[index[key]]
        files = r.get("files") or cur.get("files", "")
        extra = {**(cur.get("extra") or {}), **{k: v for k, v in (r.get("extra") or {}).items() if v}}
        if files != cur.get("files", "") or extra != (cur.get("extra") or {}):
            out[index[key]] = {**cur, "files": files, "extra": extra}
            updated.append(key)
        else:
            unchanged.append(key)
    return {"rows": out, "added": added, "updated": updated, "unchanged": unchanged, "removed": []}


def dump(spec: TargetsSpec, rows: Iterable[Mapping[str, Any]]) -> str:
    rows = list(rows)
    extras = list(dict.fromkeys(k for r in rows for k in (r.get("extra") or {})
                                if k.lower() not in {spec.key_column.lower(), spec.files_column.lower(), spec.code_column.lower()}))
    header = [spec.key_column, spec.files_column, spec.code_column, *extras]
    return pc.dump(header, [{spec.key_column: r["target"], spec.files_column: r.get("files", ""),
                             spec.code_column: r.get("code", ""), **(r.get("extra") or {})} for r in rows])


def save(spec: TargetsSpec, text: str, mode: str = "merge", mapping: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Import ``text`` into the targets file (under its lock, atomic replace).

    Raises ``ValueError`` when the text has problems (nothing is written then).
    """
    incoming = parse(text, spec, mapping)
    if incoming["problems"]:
        raise ValueError("; ".join(f"line {p['line']}: {p['message']}" if p["line"] else p["message"] for p in incoming["problems"][:5]))
    with pc.locked(spec.lock):
        result = merge(read(spec)["rows"], incoming["rows"], mode)
        pc.atomic_write(spec.csv, dump(spec, result["rows"]))
    return result


def preview(spec: TargetsSpec, text: str, mode: str = "merge", mapping: Mapping[str, str] | None = None) -> dict[str, Any]:
    """What ``save`` would do, without writing."""
    incoming = parse(text, spec, mapping)
    result = merge(read(spec)["rows"], incoming["rows"], mode)
    return {**incoming, **{k: result[k] for k in ("added", "updated", "unchanged", "removed")}, "total": len(result["rows"])}


def add(spec: TargetsSpec, target: str, files: str = "", code: str = "") -> dict[str, Any]:
    """Add (or update the files of) one row, e.g. from the plan's Add target dialog."""
    row = {"target": pc.check_target(target), "files": pc.join_files(files), "code": str(code or "").strip(), "extra": {}}
    with pc.locked(spec.lock):
        result = merge(read(spec)["rows"], [row], "merge")
        pc.atomic_write(spec.csv, dump(spec, result["rows"]))
    return result


def drop(rows: Sequence[Mapping[str, Any]], names: Iterable[str]) -> dict[str, Any]:
    """Rows without ``names``: a bare target drops every row of it (all codes), ``target@code`` just that row.

    → ``{rows, removed: [row keys], missing: [names not in the table]}``.
    """
    wanted = [str(n).strip() for n in names if str(n).strip()]
    hit: set[str] = set()
    out, removed = [], []
    for r in rows:
        key = pc.row_key(r["target"], r.get("code", ""))
        match = next((n for n in wanted if n == key or ("@" not in n and n == r["target"])), None)
        if match is None:
            out.append(dict(r))
        else:
            hit.add(match)
            removed.append(key)
    return {"rows": out, "removed": removed, "missing": [n for n in wanted if n not in hit]}


def remove(spec: TargetsSpec, names: Iterable[str]) -> dict[str, Any]:
    """Remove targets (or ``target@code`` rows) from the targets file (under its lock, atomic replace)."""
    names = list(names)
    if not names:
        raise ValueError("no targets to remove")
    with pc.locked(spec.lock):
        result = drop(read(spec)["rows"], names)
        if result["removed"]:
            pc.atomic_write(spec.csv, dump(spec, result["rows"]))
    return result


__all__ = ["DEFAULT_TARGETS", "TargetsSpec", "add", "detect_columns", "drop", "dump", "load_spec", "merge", "parse",
           "preview", "read", "remove", "save"]
