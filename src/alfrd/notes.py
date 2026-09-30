"""Annotations: shared notes anchored to targets, cells, result rows and log lines.

Stored in ``alfrd.notes.jsonl`` next to alfrd.yaml, so they travel with the
data (and with scan bundles). The file is append-only: one JSON event per
line, ``op`` = ``create`` | ``edit`` | ``resolve`` | ``reopen`` | ``delete``;
the state of a note is its events replayed in order::

    {"id": "n_8f3a", "op": "create", "anchor": {"target": "J0742+103", "project_code": "BV019",
     "workdir": "wd_1", "step": "rpicard"}, "text": "EF flagged 03:10–03:40, RFI",
     "tags": ["flagged", "rfi"], "author": "avi@khagol", "at": "2026-09-29T10:12:00Z"}

Anchors are entity paths without the project (see alfrd.entities). A note
whose anchor no longer exists (target gone, file removed) is kept and listed
as orphaned. Appends take the same lock + write pattern as plan CSV edits.
"""

from __future__ import annotations

import getpass
import json
import os
import re
import secrets
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from alfrd.entities import make as make_entity
from alfrd.runtime.plan_csv import locked

NOTES_FILE = "alfrd.notes.jsonl"
OPS = ("create", "edit", "resolve", "reopen", "delete")
MAX_TEXT = 20_000
_ID = re.compile(r"^n_[0-9a-f]{4,16}$")
_TAG = re.compile(r"^[\w.+-]{1,40}$", re.UNICODE)


class NoteError(ValueError):
    """Bad anchor, unknown note, …"""


def path_for(root: str | Path) -> Path:
    return Path(root) / NOTES_FILE


def _lock(root: Path) -> Path:
    return root / ".alfrd" / "locks" / "notes.lock"


def author() -> str:
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001
        user = os.environ.get("USER") or "unknown"
    return f"{user}@{socket.gethostname()}"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def clean_anchor(anchor: Mapping[str, Any] | None, levels=None) -> dict[str, Any]:
    """An entity path without ``project``; at least one level. Raises NoteError."""
    raw = {k: v for k, v in dict(anchor or {}).items() if k != "project"}
    try:
        out = make_entity(raw, project="-", levels=levels)
    except ValueError as exc:
        raise NoteError(str(exc)) from exc
    out.pop("project", None)
    if not out:
        raise NoteError("a note needs an anchor (target, step, file, …)")
    return out


def clean_tags(tags: Iterable[Any] | str | None) -> list[str]:
    if isinstance(tags, str):
        tags = re.split(r"[,\s]+", tags)
    out = []
    for tag in tags or []:
        tag = str(tag).strip().lstrip("#").lower()
        if tag and _TAG.match(tag) and tag not in out:
            out.append(tag)
    return out[:20]


def events(root: str | Path) -> list[dict[str, Any]]:
    """Every event in the file (bad lines skipped)."""
    try:
        lines = path_for(root).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    out = []
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict) and item.get("op") in OPS and isinstance(item.get("id"), str):
            out.append(item)
    return out


def replay(items: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Notes by id from events in file order (the last event of a note wins)."""
    notes: dict[str, dict[str, Any]] = {}
    for ev in items:
        nid, op = ev["id"], ev["op"]
        if op == "create":
            notes[nid] = {"id": nid, "anchor": dict(ev.get("anchor") or {}), "text": str(ev.get("text") or ""),
                          "tags": list(ev.get("tags") or []), "author": ev.get("author"), "created": ev.get("at"),
                          "updated": ev.get("at"), "status": "open", "history": 1}
            continue
        note = notes.get(nid)
        if note is None:
            continue
        note["updated"] = ev.get("at")
        note["history"] += 1
        if op == "edit":
            if "text" in ev:
                note["text"] = str(ev.get("text") or "")
            if "tags" in ev:
                note["tags"] = list(ev.get("tags") or [])
            if "anchor" in ev:
                note["anchor"] = dict(ev.get("anchor") or {})
        elif op == "resolve":
            note["status"] = "resolved"
            note["resolved_by"] = ev.get("author")
        elif op == "reopen":
            note["status"] = "open"
        elif op == "delete":
            notes.pop(nid)
    return notes


def _append(root: Path, event: dict[str, Any]) -> None:
    path = path_for(root)
    line = json.dumps(event, ensure_ascii=False) + "\n"
    with locked(_lock(root)):
        with open(path, "a", encoding="utf-8") as stream:
            stream.write(line)
            stream.flush()
            os.fsync(stream.fileno())


def create(root: str | Path, anchor: Mapping[str, Any], text: str, tags: Iterable[str] | str | None = None,
           *, who: str | None = None, levels=None) -> dict[str, Any]:
    base = Path(root)
    body = str(text or "").strip()
    if not body:
        raise NoteError("a note needs text")
    if len(body) > MAX_TEXT:
        raise NoteError(f"a note is at most {MAX_TEXT} characters")
    event = {"id": f"n_{secrets.token_hex(4)}", "op": "create", "anchor": clean_anchor(anchor, levels), "text": body,
             "tags": clean_tags(tags), "author": who or author(), "at": _now()}
    _append(base, event)
    return replay([event])[event["id"]]


def change(root: str | Path, note_id: str, op: str, *, text: str | None = None, tags: Iterable[str] | str | None = None,
           anchor: Mapping[str, Any] | None = None, who: str | None = None, levels=None) -> dict[str, Any] | None:
    """edit / resolve / reopen / delete; returns the note after it (None when deleted)."""
    base = Path(root)
    if op not in OPS or op == "create":
        raise NoteError(f"op must be one of {', '.join(OPS[1:])}")
    if not _ID.match(str(note_id)):
        raise NoteError(f"bad note id {note_id!r}")
    if note_id not in replay(events(base)):
        raise NoteError(f"no note {note_id}")
    event: dict[str, Any] = {"id": note_id, "op": op, "author": who or author(), "at": _now()}
    if op == "edit":
        if text is not None:
            body = str(text).strip()
            if not body:
                raise NoteError("a note needs text")
            event["text"] = body[:MAX_TEXT]
        if tags is not None:
            event["tags"] = clean_tags(tags)
        if anchor is not None:
            event["anchor"] = clean_anchor(anchor, levels)
    _append(base, event)
    return replay(events(base)).get(note_id)


def orphaned(note: Mapping[str, Any], root: str | Path, targets: set[str] | None = None) -> bool:
    """Its target is not a target of the project any more, or its file is gone."""
    anchor = note.get("anchor") or {}
    rel = anchor.get("file")
    if rel and (".." in Path(rel).parts or not (Path(root) / rel).exists()):
        return True
    return bool(targets is not None and anchor.get("target") and anchor["target"] not in targets)


def known_targets(root: str | Path) -> set[str] | None:
    """Targets the project knows (result CSVs, work dirs, targets and plan CSVs); None when it can't tell."""
    base = Path(root)
    found: set[str] = set()
    try:
        from alfrd.avica_layout import scan_layout

        layout = scan_layout(base)
        found.update(r.get("target") for r in layout.get("result_csvs") or [] if r.get("target"))
        for code in layout.get("project_codes") or []:
            found.update(code.get("targets") or [])
    except Exception:  # noqa: BLE001 - not an AVICA tree
        pass
    try:
        from alfrd import targets_csv as tc

        spec = tc.load_spec(base)
        if spec.csv.is_file():
            found.update(r["target"] for r in tc.read(spec)["rows"])
    except Exception:  # noqa: BLE001
        pass
    try:
        from alfrd.execution import load_execution
        from alfrd.runtime.scheduler import table_for

        cfg = load_execution(base)
        if cfg.plan_csv.is_file():
            found.update(r.target for r in table_for(cfg, cfg.plan_csv).rows)
    except Exception:  # noqa: BLE001
        pass
    return found or None


def listing(root: str | Path) -> dict[str, Any]:
    base = Path(root)
    notes = list(replay(events(base)).values())
    targets = known_targets(base)
    for note in notes:
        note["orphaned"] = orphaned(note, base, targets)
    notes.sort(key=lambda n: str(n.get("created") or ""), reverse=True)
    tags = sorted({t for n in notes for t in n.get("tags") or []})
    return {"file": NOTES_FILE, "notes": notes, "tags": tags,
            "counts": {"open": sum(n["status"] == "open" for n in notes), "resolved": sum(n["status"] == "resolved" for n in notes),
                       "orphaned": sum(bool(n["orphaned"]) for n in notes)}}


__all__ = ["NOTES_FILE", "NoteError", "change", "clean_anchor", "clean_tags", "create", "events", "listing", "orphaned",
           "replay"]
