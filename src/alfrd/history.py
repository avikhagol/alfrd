"""Version history of a project's config files (alfrd.yaml by default).

Store (next to the project, like plans)::

    <root>/.alfrd/history/<file>/<stamp>-<hash>.<ext>
    <root>/.alfrd/history/<file>/index.jsonl      one JSON line per version

A version is recorded when the Studio or the CLI saves the file, and when the
live watcher sees it change on disk (source ``external``). A save with the same
content as the latest version records nothing. The newest ``keep`` versions are
kept (default 200). Which files are tracked, and how many versions, is set in
alfrd.yaml::

    history:
      files: [alfrd.yaml, avica.inp, "input_template*/*.inp"]   # default [alfrd.yaml]
      keep: 200
      git_commit: false        # true: also `git commit` Studio saves (only in a git repo)

Plan CSVs are never tracked. Restore writes an old version back as a new version.
"""

from __future__ import annotations

import difflib
import getpass
import hashlib
import json
import os
import re
import socket
import subprocess
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Mapping

from alfrd.runtime.plan_csv import atomic_write, locked

HISTORY_DIR = Path(".alfrd") / "history"
DEFAULT_FILES = ("alfrd.yaml",)
DEFAULT_KEEP = 200
SOURCES = ("studio", "cli", "external", "restore")
_VERSION = re.compile(r"^\d{8}T\d{6}\d{6}-[0-9a-f]{12}$")


class HistoryError(ValueError):
    """Not a tracked file, unknown version, …"""


class Conflict(Exception):
    """The file changed on disk since the caller loaded it."""

    def __init__(self, rel: str, current: str, proposed: str | None = None) -> None:
        self.rel = rel
        self.current = current
        self.current_hash = text_hash(current)
        self.diff = unified(current, proposed, f"{rel} (on disk)", f"{rel} (yours)") if proposed is not None else ""
        super().__init__(f"{rel} changed on disk since you loaded it")


def text_hash(text: str) -> str:
    """SHA-256 (hex) of the text as UTF-8: what the Studio sends back as ``base_hash``."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return None


def settings(root: str | Path) -> dict[str, Any]:
    from alfrd.manifest_default import manifest_data

    try:
        data, _path, _default = manifest_data(root)
    except Exception:  # noqa: BLE001 - a broken alfrd.yaml still gets its history
        data = {}
    block = data.get("history") if isinstance(data.get("history"), Mapping) else {}
    files = block.get("files") or list(DEFAULT_FILES)
    if isinstance(files, str):
        files = [files]
    try:
        keep = max(1, int(block.get("keep") or DEFAULT_KEEP))
    except (TypeError, ValueError):
        keep = DEFAULT_KEEP
    patterns = [str(f) for f in files if str(f).strip()]
    if "alfrd.yaml" not in patterns:
        patterns.insert(0, "alfrd.yaml")
    return {"files": patterns, "keep": keep, "git_commit": bool(block.get("git_commit"))}


def _excluded(rel: str) -> bool:
    return rel.startswith(".alfrd/") or rel.endswith((".plan.csv",)) or "/." in f"/{rel}"


def tracked(root: str | Path) -> list[str]:
    """Tracked files that exist (patterns expanded), relative to the root."""
    base = Path(root)
    out: list[str] = []
    for pattern in settings(base)["files"]:
        if any(ch in pattern for ch in "*?["):
            for path in sorted(base.glob(pattern)):
                rel = path.relative_to(base).as_posix()
                if path.is_file() and not _excluded(rel):
                    out.append(rel)
        elif (base / pattern).is_file() or pattern == "alfrd.yaml":
            out.append(pattern)
    return list(dict.fromkeys(out))


def _normal(rel: str) -> str | None:
    """A relative path inside the project (``./`` dropped), or None for anything else."""
    text = str(rel or "").strip()
    while text.startswith("./"):
        text = text[2:]
    if not text or text.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", text):
        return None
    parts = Path(text).parts
    if ".." in parts or "." in parts:
        return None
    return Path(*parts).as_posix()


def is_tracked(root: str | Path, rel: str) -> bool:
    rel = _normal(rel)
    if rel is None or _excluded(rel):
        return False
    return any(fnmatch(rel, p) if any(ch in p for ch in "*?[") else rel == p for p in settings(root)["files"])


def tracked_among(root: str | Path, rels) -> list[str]:
    """Which of ``rels`` (tree paths) are tracked; ``.alfrd.yaml`` is reported as ``alfrd.yaml``."""
    patterns = settings(root)["files"]
    out = []
    for rel in rels:
        rel = "alfrd.yaml" if rel == ".alfrd.yaml" else str(rel)
        if _excluded(rel) or ".." in Path(rel).parts:
            continue
        if any(fnmatch(rel, p) if any(ch in p for ch in "*?[") else rel == p for p in patterns):
            out.append(rel)
    return out


def capture_external(root: str | Path, rels) -> list[dict[str, Any]]:
    """Record tracked files among ``rels`` that differ from their latest version (source ``external``)."""
    out = []
    for rel in tracked_among(root, rels):
        try:
            entry = record(root, rel, source="external")
        except (HistoryError, OSError):
            continue
        if entry:
            out.append(entry)
    return out


def _check(root: Path, rel: str) -> str:
    clean = _normal(rel)
    if clean is None or not is_tracked(root, clean):
        raise HistoryError(f"{rel or '(none)'} is not a tracked file (alfrd.yaml history.files)")
    base = Path(root).resolve()
    target = _path(base, clean).resolve()
    if target != base and base not in target.parents:
        raise HistoryError(f"{rel} is outside the project")
    return clean


def _path(root: Path, rel: str) -> Path:
    """The file on disk; ``alfrd.yaml`` stands for the project's manifest (``.alfrd.yaml`` too)."""
    if rel == "alfrd.yaml":
        from alfrd.studio_defs import manifest_file

        return manifest_file(root) or root / rel
    return root / rel


def _dir(root: Path, rel: str) -> Path:
    return root / HISTORY_DIR / rel.replace("/", "__")


def _lock(root: Path) -> Path:
    return root / ".alfrd" / "locks" / "history.lock"


def _who() -> str:
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001
        user = os.environ.get("USER") or "unknown"
    return f"{user}@{socket.gethostname()}"


def _index(folder: Path) -> list[dict[str, Any]]:
    try:
        lines = (folder / "index.jsonl").read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    out = []
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict) and item.get("version"):
            out.append(item)
    return out


def versions(root: str | Path, rel: str) -> list[dict[str, Any]]:
    """Versions of a tracked file, newest first."""
    base = Path(root).resolve()
    rel = _check(base, rel)
    return list(reversed(_index(_dir(base, rel))))


def record(root: str | Path, rel: str, text: str | None = None, *, source: str, message: str | None = None,
           who: str | None = None) -> dict[str, Any] | None:
    """Store ``text`` (default: the file on disk) as a new version; None when it equals the latest one."""
    base = Path(root).resolve()
    rel = _check(base, rel)
    if source not in SOURCES:
        raise HistoryError(f"source must be one of {', '.join(SOURCES)}")
    if text is None:
        text = read_text(_path(base, rel))
        if text is None:
            return None
    digest = text_hash(text)[:12]
    folder = _dir(base, rel)
    with locked(_lock(base)):
        index = _index(folder)
        if index and index[-1].get("hash") == digest:
            return None
        now = datetime.now(timezone.utc)
        version = f"{now.strftime('%Y%m%dT%H%M%S%f')}-{digest}"
        suffix = Path(rel).suffix or ".txt"
        folder.mkdir(parents=True, exist_ok=True)
        atomic_write(folder / f"{version}{suffix}", text)
        entry = {"version": version, "at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "who": who or _who(), "source": source,
                 "hash": digest, "size": len(text.encode("utf-8")), "file": f"{version}{suffix}"}
        if message:
            entry["message"] = str(message)[:500]
        index.append(entry)
        keep = settings(base)["keep"]
        dropped = index[:-keep] if len(index) > keep else []
        index = index[-keep:]
        atomic_write(folder / "index.jsonl", "".join(json.dumps(i) + "\n" for i in index))
        for item in dropped:
            try:
                (folder / item["file"]).unlink()
            except (OSError, KeyError):
                pass
    return entry


def ensure_baseline(root: str | Path, rel: str) -> dict[str, Any] | None:
    """Record the file as it is on disk when it isn't the latest version (before overwriting it)."""
    try:
        return record(root, rel, source="external", message="as found on disk")
    except HistoryError:
        return None


def read_version(root: str | Path, rel: str, version: str) -> str:
    base = Path(root).resolve()
    rel = _check(base, rel)
    if version == "current":
        text = read_text(_path(base, rel))
        if text is None:
            raise HistoryError(f"{rel} does not exist")
        return text
    if not _VERSION.match(str(version)):
        raise HistoryError(f"bad version id {version!r}")
    folder = _dir(base, rel)
    item = next((i for i in _index(folder) if i["version"] == version), None)
    if item is None:
        raise HistoryError(f"{rel} has no version {version}")
    text = read_text(folder / item["file"])
    if text is None:
        raise HistoryError(f"version {version} of {rel} is missing on disk")
    return text


def unified(a: str, b: str | None, name_a: str, name_b: str) -> str:
    return "".join(difflib.unified_diff(a.splitlines(keepends=True), (b or "").splitlines(keepends=True),
                                        fromfile=name_a, tofile=name_b, n=3))


def diff(root: str | Path, rel: str, a: str, b: str = "current") -> str:
    """Unified diff from version ``a`` to ``b`` (either may be ``current``)."""
    return unified(read_version(root, rel, a), read_version(root, rel, b), f"{rel}@{a}", f"{rel}@{b}")


def check_base(root: str | Path, rel: str, *, base_hash: str | None = None, base_text: str | None = None,
               proposed: str | None = None) -> None:
    """Raise :class:`Conflict` when the file on disk is not what the caller loaded.

    Nothing to compare (no base given) passes; so does a file that is already
    exactly ``proposed``. A missing file only conflicts with a non-empty base.
    """
    if base_hash is None and base_text is None:
        return
    base = Path(root).resolve()
    current = read_text(_path(base, rel))
    if current is None:
        return
    expected = base_hash or text_hash(base_text or "")
    # A save adds the final newline, so a base loaded (or kept) without it still matches.
    seen = {text_hash(current), text_hash(current[:-1]) if current.endswith("\n") else ""}
    if expected not in seen and current != proposed and current != (proposed or "") + "\n":
        raise Conflict(rel, current, proposed)


def save(root: str | Path, rel: str, text: str, *, source: str, message: str | None = None,
         base_hash: str | None = None, base_text: str | None = None, force: bool = False) -> dict[str, Any] | None:
    """Write a tracked file with the conflict guard; old content recorded first, the new one after."""
    base = Path(root).resolve()
    rel = _check(base, rel)
    if not force:
        check_base(base, rel, base_hash=base_hash, base_text=base_text, proposed=text)
    ensure_baseline(base, rel)
    final = text if text.endswith("\n") else text + "\n"
    if read_text(_path(base, rel)) == final:
        return None  # nothing to write (and alfrd.yaml.bak keeps the previous content)
    if rel == "alfrd.yaml":  # validate before anything is recorded
        import yaml

        data = yaml.safe_load(final)
        if not isinstance(data, dict) or not str(data.get("name") or "").strip():
            raise ValueError("alfrd.yaml must be a YAML mapping with a `name`")
    # Recorded before the write, so the live watcher can't claim the new content as "external".
    entry = record(base, rel, final, source=source, message=message)
    if rel == "alfrd.yaml":
        from alfrd.studio_defs import save_manifest

        save_manifest(base, text)
    else:
        atomic_write(_path(base, rel), final)
    if settings(base)["git_commit"] and source in ("studio", "restore"):
        git_commit(base, rel, message or f"alfrd: {source} save of {rel}")
    return entry


def restore(root: str | Path, rel: str, version: str, *, base_hash: str | None = None, force: bool = False) -> dict[str, Any] | None:
    """Save an old version back as the newest one (source ``restore``)."""
    text = read_version(root, rel, version)
    return save(root, rel, text, source="restore", message=f"restored {version}", base_hash=base_hash, force=force)


# ---------------------------------------------------------------------------
# Git (read-only unless history.git_commit)


def _git(root: Path, *args: str, timeout: float = 5.0) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None


def git_log(root: str | Path, rel: str, limit: int = 50) -> list[dict[str, str]] | None:
    """``git log --follow`` of the file; None when the project isn't in a git repo."""
    base = Path(root).resolve()
    inside = _git(base, "rev-parse", "--is-inside-work-tree")
    if inside is None or inside.returncode != 0 or inside.stdout.strip() != "true":
        return None
    out = _git(base, "log", "--follow", f"-n{int(limit)}", "--format=%H%x1f%an%x1f%aI%x1f%s", "--", rel)
    if out is None or out.returncode != 0:
        return []
    items = []
    for line in out.stdout.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 4:
            items.append({"commit": parts[0], "author": parts[1], "at": parts[2], "subject": parts[3]})
    return items


def git_commit(root: Path, rel: str, message: str) -> bool:
    if git_log(root, rel) is None:
        return False
    added = _git(root, "add", "--", rel)
    if added is None or added.returncode != 0:
        return False
    done = _git(root, "commit", "-m", message, "--", rel)
    return bool(done and done.returncode == 0)


__all__ = [
    "Conflict", "HistoryError", "check_base", "diff", "ensure_baseline", "git_log", "is_tracked", "read_version",
    "record", "restore", "save", "settings", "text_hash", "tracked", "versions",
]
