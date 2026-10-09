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
import re
import tempfile
import time
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
def locked(lock_path: str | Path, *, timeout: float | None = None) -> Iterator[None]:
    path = Path(lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+", encoding="utf-8") as handle:
        if fcntl is not None:
            if timeout is None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            else:
                deadline = time.monotonic() + timeout
                while True:
                    try:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise TimeoutError("plan CSV lock timed out") from None
                        time.sleep(min(0.05, remaining))
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def update(path: str | Path, lock_path: str | Path, steps: Sequence[str],
           cells: Mapping[tuple[str, str], str] | None = None,
           fills: Mapping[tuple[str, str], str] | None = None,
           *, only_if: Mapping[tuple[str, str], set[str]] | None = None,
           lock_timeout: float | None = None, **columns: str) -> PlanTable:
    """Set ``cells[(row_key, step)]`` and fill empty ``fills[(row_key, column)]``.

    ``only_if`` limits a cell change to cells whose current (normalized) value
    is in the given set, so a user's edit made meanwhile is never overwritten.
    Columns named in ``fills`` that the file lacks are not added.
    ``lock_timeout`` optionally bounds lock acquisition (seconds); existing
    callers keep the blocking lock. A timeout leaves the file untouched.
    """
    with locked(lock_path, timeout=lock_timeout):
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


class DuplicateRowError(ValueError):
    """Raised by ``add_row`` when (target, code) is already a row in the plan CSV."""


def check_target(target: str) -> str:
    """A usable target name (stripped), or ``ValueError``.

    ``#…`` would be read back as a comment row and ``@`` would clash with
    ``row_key`` (``target@code``).
    """
    name = str(target or "").strip()
    if not name:
        raise ValueError("target is required")
    if name.startswith("#"):
        raise ValueError(f"target {name!r} starts with '#', which marks a comment row")
    if "@" in name:
        raise ValueError(f"target {name!r} contains '@', which separates target and project code")
    return name


def join_files(value: str | Sequence[str] | None) -> str:
    """FITS file names as one ``a,b,c`` cell: accepts a string or a list, split on commas, newlines, spaces."""
    items = list(value) if isinstance(value, (list, tuple)) else [value]
    names = [n for item in items for n in re.split(r"[,\s]+", str(item or "")) if n]
    return ",".join(names)


def file_names(value: str | Sequence[str] | None, base: str | Path | None = None) -> set[str]:
    """File names of a row as a set for conflict checks.

    Split like :func:`join_files`, ``./a`` and ``a`` made equal, and resolved to
    an absolute path when the file exists below ``base`` (or as given).
    """
    out: set[str] = set()
    for name in join_files(value).split(","):
        if not name:
            continue
        norm = os.path.normpath(name)
        path = Path(norm).expanduser()
        candidate = path if path.is_absolute() else (Path(base) / path if base else path)
        try:
            out.add(str(candidate.resolve()) if candidate.exists() else norm)
        except OSError:
            out.add(norm)
    return out


def conflict_keys(row: PlanRow, names: Sequence[str], table: PlanTable, base: str | Path | None = None) -> dict[str, set[str]]:
    """The values a row holds while it runs, per ``serialize_on`` name.

    ``target`` = the key column, ``files`` = the file column (a set of names),
    any other name = that plan CSV column (case-insensitive). Empty values hold nothing.
    """
    out: dict[str, set[str]] = {}
    for name in names:
        if name == "target":
            values = {row.target} if row.target else set()
        elif name == "files" or name.lower() == table.files_column.lower():
            values = file_names(row.files, base)
        else:
            column = _find(table.header, name) or name
            value = str(row.values.get(column, "")).strip()
            values = {value} if value else set()
        out[name] = values
    return out


def _with_columns(header: Sequence[str], columns: Sequence[str], steps: Sequence[str]) -> list[str]:
    """``header`` plus any of ``columns`` it lacks, inserted before the first step column."""
    out = list(header)
    missing = [c for c in columns if _find(out, c) is None]
    at = next((i for i, h in enumerate(out) if h in steps), len(out))
    out[at:at] = missing
    return out


def add_row(path: str | Path, lock_path: str | Path, steps: Sequence[str], *, target: str, files: str = "",
            code: str = "", workdir: str = "", selected: Iterable[str] | None = None, create: bool = False,
            **columns: str) -> PlanTable:
    """Append one row (target, files, code, workdir) to a plan CSV.

    Takes the same lock and atomic-replace path as ``update``, so this is safe
    to call while a plan is running, paused or finished; a live runner re-reads
    the CSV before each unit and picks the row up (same as a hand edit).
    ``selected`` marks which step columns start as ``todo`` (default: every
    step column already in the file); the rest are left empty. A value given
    for a column the header lacks (project code, files, work dir) adds that
    column before the first step column; existing rows get ``""``.

    Raises ``ValueError`` for a bad target name, ``DuplicateRowError`` when
    the row's key (as it will be parsed) is already taken, and
    ``FileNotFoundError`` when the file is missing, unless ``create`` is true:
    then it is created with ``steps`` as its step columns.
    """
    target = check_target(target)
    code = str(code or "").strip()
    source = Path(path)
    with locked(lock_path):
        if source.exists():
            text = source.read_text(encoding="utf-8-sig")
        elif create:
            text = ""
        else:
            raise FileNotFoundError(f"plan CSV {source} does not exist")
        table = parse(text, source, steps, **columns)
        values = {table.files_column: str(files or ""), table.code_column: code,
                  table.workdir_column: str(workdir or "").strip()}
        if table.header:
            header = _with_columns(table.header, [c for c, v in values.items() if v], steps)
        else:
            header = [table.key_column, table.files_column, table.code_column, table.workdir_column, *steps]
        usable = [s for s in steps if s in header]
        chosen = set(usable) if selected is None else set(selected) & set(usable)
        new_row = {h: "" for h in header}
        new_row.update({(_find(header, c) or c): v for c, v in values.items()})
        new_row[_find(header, table.key_column) or header[0]] = target
        new_row.update({s: TODO if s in chosen else "" for s in usable})
        raw_rows = list(csv.DictReader(io.StringIO(text))) if text else []
        new_text = dump(header, [*raw_rows, new_row])
        written = parse(new_text, source, steps, **columns)
        key = row_key(target, code)
        if not written.rows or written.rows[-1].key != key or sum(r.key == key for r in written.rows) > 1:
            raise DuplicateRowError(f"{key} is already a row in {source.name}")
        atomic_write(source, new_text)
        return written


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
    "DuplicateRowError", "PlanRow", "PlanTable", "add_row", "atomic_write", "check_target", "conflict_keys", "create",
    "dump", "file_names", "join_files", "locked", "normalize", "parse", "read", "row_key", "update",
]
