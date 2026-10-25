"""The chat's ``R/overview`` projection (goal, criteria, files) and the hash-checked raw artifact bytes (C22).

Everything is derived from the session's own records, so a legacy session (no criteria, no files) answers
empty lists. A criterion is ``unverified`` unless its task recorded evidence for it.
"""
from __future__ import annotations

import hashlib
import mimetypes
import os
from pathlib import Path
from typing import Any, Mapping

from alfrd import orchestration as proto

MAX_RAW_BYTES = 20 * 1024 * 1024
_VERDICTS = ("verified", "failed")


def _evidence(rec: Mapping[str, Any], index: int, text: str) -> str:
    """``verified``/``failed`` only when the task's result carries evidence naming this criterion."""
    for item in (rec.get("result") or {}).get("evidence") or []:
        if not isinstance(item, Mapping) or item.get("verdict") not in _VERDICTS:
            continue
        if item.get("criterion") in (index, text):
            return item["verdict"]
    return "unverified"


def _criteria(tasks: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for rec in tasks:
        for index, text in enumerate((rec.get("task") or {}).get("acceptance_criteria") or []):
            out.append({"id": f"{rec['row']}.c{index + 1}", "text": str(text), "task_rows": [rec["row"]],
                        "status": _evidence(rec, index, str(text))})
    return out


def _current_hash(root: Path, rel: str) -> str | None:
    try:
        path = proto.confined(root, rel)
        if path.is_file() and path.stat().st_size <= proto.MAX_HASH_BYTES:
            return proto.file_sha256(path)
    except (proto.RefError, OSError):
        pass
    return None


def _files(root: Path, tasks: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    found: dict[tuple[str, str | None], dict[str, Any]] = {}

    def add(rel: str, sha: str | None, row: str, expected: bool = False) -> None:
        entry = found.setdefault((rel, sha), {"path": rel, "ref": proto.format_ref(rel, sha) if sha else None,
                                              "producer_rows": [], "_expected": expected, "_sha": sha})
        if row not in entry["producer_rows"]:
            entry["producer_rows"].append(row)

    for rec in tasks:
        recorded = set()
        for ref in (rec.get("result") or {}).get("artifacts") or []:
            try:
                rel, sha = proto.parse_ref(ref)
            except proto.RefError:
                continue
            recorded.add(rel)
            add(rel, sha, rec["row"])
        for rel in (rec.get("task") or {}).get("expected_outputs") or []:
            rel = str(rel)
            if rel not in recorded:
                add(rel, None, rec["row"], expected=True)
    files = []
    for entry in found.values():
        sha, rel = entry.pop("_sha"), entry["path"]
        entry.pop("_expected")
        actual = _current_hash(root, rel)
        if actual is None:
            entry["state"] = "expected" if sha is None and not _done(tasks, entry["producer_rows"]) else "missing"
        else:
            entry["state"] = "recorded" if sha is None or actual == sha else "changed"
            if sha is None:  # expected but now on disk: report its present version
                entry["ref"] = proto.format_ref(rel, actual)
            with_stat = proto.confined(root, rel).stat()
            entry["bytes"] = with_stat.st_size
            entry["current_ref"] = proto.format_ref(rel, actual)
        media = mimetypes.guess_type(rel)[0]
        if media:
            entry["media_type"] = media
        files.append(entry)
    return files


def _done(tasks: list[Mapping[str, Any]], rows: list[str]) -> bool:
    return any(rec["row"] in rows and rec.get("status") == "done" for rec in tasks)


def overview(store) -> dict[str, Any]:
    requests = store.requests()
    first = requests[0] if requests else None
    goal = {"text": str(first.get("text") or ""), "source": f"request:{first['id']}"} if first else {"text": "", "source": None}
    tasks = [rec for rec in store.tasks() if rec.get("kind") == "task"]
    return {"rev": store.rev, "goal": goal, "criteria": _criteria(tasks), "files": _files(store.root, tasks)}


def raw_artifact(root: str | Path, ref: str) -> tuple[bytes, str, str]:
    """``(bytes, media_type, path)`` of ``path@sha256``. :class:`RefError`: 404 missing, 403/400 outside the
    root or a symlink, 413 over 20 MB, 409 when the content no longer has the referenced hash."""
    rel, sha = proto.parse_ref(ref)
    base = Path(root).resolve()
    proto.confined(base, rel)
    current = base
    for part in Path(rel).parts:  # every component, not just the last: a linked directory is refused too
        current = current / part
        if current.is_symlink():
            raise proto.RefError(f"{rel}: symbolic links are not served", 403)
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(current, flags)
    except FileNotFoundError:
        raise proto.RefError(f"{rel}: no such file", 404) from None
    except OSError as exc:
        raise proto.RefError(f"{rel}: cannot be read ({exc.strerror})", 403) from None
    with os.fdopen(fd, "rb") as stream:
        if not os.path.isfile(current) or os.fstat(stream.fileno()).st_mode & 0o170000 != 0o100000:
            raise proto.RefError(f"{rel}: not a regular file", 404)
        data = stream.read(MAX_RAW_BYTES + 1)
    if len(data) > MAX_RAW_BYTES:
        raise proto.RefError(f"{rel}: larger than {MAX_RAW_BYTES} bytes", 413)
    actual = hashlib.sha256(data).hexdigest()
    if actual != sha:
        raise proto.RefError(f"{rel}: content changed since it was referenced (sha256 {actual[:12]}…, ref {sha[:12]}…)", 409)
    return data, mimetypes.guess_type(rel)[0] or "application/octet-stream", rel
