"""Plan CSV: one row per target, one column per workflow step.

Cells written by people: ``todo`` (run it), empty / ``skip`` / ``-`` (don't).
Cells written by ALFRD: ``running done failed blocked cancelled interrupted``.
``queued`` is read as ``todo``.

Other columns (the key column, FITS file names, project code, work dir, notes)
are kept as they are. ALFRD only rewrites the cells it changes: every update is
read → modify → atomic replace under a lock, so edits made in between (a cell
switched to ``skip``, a new row) are kept.
"""

from __future__ import annotations

import contextlib
import csv
import io
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Mapping, Sequence

try:  # POSIX
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

TODO = "todo"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
BLOCKED = "blocked"
CANCELLED = "cancelled"
INTERRUPTED = "interrupted"
SKIP_VALUES = {"", "skip", "-", "no", "n"}
TODO_VALUES = {"todo", "queued", "yes", "y", "x", "run"}
DONE_VALUES = {"done", "ok", "succeeded"}
STOP_VALUES = {FAILED, BLOCKED, CANCELLED, INTERRUPTED}
STATES = (TODO, RUNNING, DONE, FAILED, BLOCKED, CANCELLED, INTERRUPTED, "skip")


def normalize(value: str | None) -> str:
    v = str(value or "").strip().lower()
    if v in SKIP_VALUES:
        return "skip"
    if v in TODO_VALUES:
        return TODO
    if v in DONE_VALUES:
        return DONE
    return v


@dataclass
class PlanRow:
    values: dict[str, str]
    index: int
    key: str
    target: str
    code: str = ""
    workdir: str = ""
    files: str = ""

    def cell(self, step: str) -> str:
        return normalize(self.values.get(step))


@dataclass
class PlanTable:
    path: Path
    header: list[str]
    rows: list[PlanRow]
    steps: list[str]
    key_column: str
    code_column: str
    workdir_column: str
    files_column: str
    unknown_steps: list[str] = field(default_factory=list)

    def row(self, key: str) -> PlanRow | None:
        return next((r for r in self.rows if r.key == key), None)

    def to_dict(self) -> dict:
        return {
            "path": str(self.path),
            "header": self.header,
            "steps": self.steps,
            "key_column": self.key_column,
            "rows": [
                {"key": r.key, "target": r.target, "code": r.code, "workdir": r.workdir, "files": r.files,
                 "cells": {s: r.cell(s) for s in self.steps}, "values": r.values}
                for r in self.rows
            ],
        }


def row_key(target: str, code: str = "") -> str:
    """Row identity: the target, plus the project code when the row names one."""
    return f"{target}@{code}" if code else target


def _find(header: Sequence[str], name: str) -> str | None:
    lowered = {h.lower(): h for h in header}
    return lowered.get(name.lower())


def parse(text: str, path: Path, steps: Sequence[str], *, key_column: str, code_column: str,
          workdir_column: str, files_column: str) -> PlanTable:
    reader = csv.DictReader(io.StringIO(text))
    header = list(reader.fieldnames or [])
    key = _find(header, key_column) or (header[0] if header else key_column)
    code = _find(header, code_column) or code_column
    workdir = _find(header, workdir_column) or workdir_column
    files = _find(header, files_column) or files_column
    step_cols = [s for s in steps if s in header]
    known = {key, code, workdir, files, *steps}
    rows = []
    seen: dict[str, int] = {}
    for index, raw in enumerate(reader):
        values = {h: (raw.get(h) or "") for h in header}
        target = values.get(key, "").strip()
        if not target or target.startswith("#"):
            continue
        c = values.get(code, "").strip()
        k = row_key(target, c)
        if k in seen:
            seen[k] += 1
            k = f"{k}#{seen[k]}"
        else:
            seen[k] = 1
        rows.append(PlanRow(values=values, index=index, key=k, target=target, code=c,
                            workdir=values.get(workdir, "").strip(), files=values.get(files, "").strip()))
    # Columns that look like step cells (todo/done/...) but are not workflow steps.
    unknown = [h for h in header if h not in known
               and any(normalize(r.values.get(h)) in {TODO, DONE, RUNNING, FAILED} for r in rows)]
    return PlanTable(path=path, header=header, rows=rows, steps=step_cols, key_column=key,
                     code_column=code, workdir_column=workdir, files_column=files, unknown_steps=unknown)


def read(path: str | Path, steps: Sequence[str], **columns: str) -> PlanTable:
    source = Path(path)
    text = source.read_text(encoding="utf-8-sig") if source.exists() else ""
    return parse(text, source, steps, **columns)


def dump(header: Sequence[str], rows: Iterable[Mapping[str, str]]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(header), extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({h: row.get(h, "") for h in header})
    return buffer.getvalue()


def atomic_write(path: str | Path, text: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
        with contextlib.suppress(OSError):
            os.chmod(tmp, (target.stat().st_mode & 0o777) if target.exists() else 0o644)
        os.replace(tmp, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


@contextlib.contextmanager
def locked(lock_path: str | Path) -> Iterator[None]:
    path = Path(lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+", encoding="utf-8") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def update(path: str | Path, lock_path: str | Path, steps: Sequence[str],
           cells: Mapping[tuple[str, str], str] | None = None,
           fills: Mapping[tuple[str, str], str] | None = None,
           *, only_if: Mapping[tuple[str, str], set[str]] | None = None, **columns: str) -> PlanTable:
    """Set ``cells[(row_key, step)]`` and fill empty ``fills[(row_key, column)]``.

    ``only_if`` limits a cell change to cells whose current (normalized) value
    is in the given set, so a user's edit made meanwhile is never overwritten.
    Columns named in ``fills`` that the file lacks are not added.
    """
    with locked(lock_path):
        table = read(path, steps, **columns)
        changed = False
        by_key = {r.key: r for r in table.rows}
        for (key, step), value in (cells or {}).items():
            row = by_key.get(key)
            if row is None or step not in table.header:
                continue
            if only_if and (key, step) in only_if and row.cell(step) not in only_if[(key, step)]:
                continue
            if row.values.get(step) != value:
                row.values[step] = value
                changed = True
        for (key, column), value in (fills or {}).items():
            row = by_key.get(key)
            if row is None or not value or column not in table.header or row.values.get(column, "").strip():
                continue
            row.values[column] = value
            changed = True
        if changed:
            text = Path(path).read_text(encoding="utf-8-sig")
            # Keep rows the parser dropped (comments, blanks) by rewriting from the raw file.
            raw_rows = list(csv.DictReader(io.StringIO(text)))
            index_map = {r.index: r for r in table.rows}
            out = [index_map[i].values if i in index_map else raw for i, raw in enumerate(raw_rows)]
            atomic_write(path, dump(table.header, out))
            table = read(path, steps, **columns)
        return table


def create(path: str | Path, rows: Sequence[Mapping[str, str]], steps: Sequence[str], selected: Iterable[str],
           *, key_column: str, files_column: str, code_column: str, workdir_column: str,
           extra_columns: Sequence[str] = ()) -> Path:
    """Write a new plan CSV: ``selected`` steps are ``todo``, the rest empty."""
    chosen = set(selected)
    header = [key_column, files_column, code_column, workdir_column, *extra_columns, *steps]
    out = []
    for item in rows:
        values = {key_column: item.get("target", ""), files_column: item.get("files", ""),
                  code_column: item.get("code", ""), workdir_column: item.get("workdir", "")}
        values.update({c: item.get(c, "") for c in extra_columns})
        values.update({s: (item.get("cells") or {}).get(s, TODO if s in chosen else "") for s in steps})
        out.append(values)
    atomic_write(path, dump(header, out))
    return Path(path)


__all__ = [
    "BLOCKED", "CANCELLED", "DONE", "FAILED", "INTERRUPTED", "RUNNING", "STATES", "STOP_VALUES", "TODO",
    "PlanRow", "PlanTable", "atomic_write", "create", "dump", "locked", "normalize", "parse", "read",
    "row_key", "update",
]
