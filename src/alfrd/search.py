"""Full-text search over a project's text files (logs, CSVs, alfrd.yaml, notes).

**A (default): an SQLite FTS5 index** per project at
``$ALFRD_HOME/search/<identifier>.sqlite`` (local, never next to the data, so
never SQLite on NFS). Files are indexed in chunks of lines with their start
line and byte offset, so a hit opens the log viewer at the line. The index is
kept current from the live watcher's diffs: a changed file is re-indexed, a
removed one dropped, a log that grew is indexed from the stored offset (same
file id and no truncation; otherwise from the start, like ``read_range``).

Tokenizer ``unicode61`` with ``tokenchars '+-_.'`` keeps target names
(``J0742+103``), ``wd_1`` and ``casa.log`` as single tokens; every query word
is a prefix query.

**B (fallback, when this Python's sqlite3 has no FTS5): a bounded scan** of the
same files, stopping after ``limit`` hits or a time budget.

Scope: the files the Studio reads (``collect_studio_files``: declared logs and
content files) plus ``alfrd.notes.jsonl``; measurement sets, FITS and other
binaries are never read.
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

SCHEMA_VERSION = 2
CHUNK_LINES = 40
CHUNK_BYTES = 8192
MAX_FILE_BYTES = 256 * 1024 * 1024
BINARY = re.compile(r"\.(ms|fits|idifits|uvf|png|jpe?g|gif|pdf|ps|eps|tar|gz|bz2|xz|zip|npy|npz|pkl|so|o|pyc|sqlite|db|table|f0|dat)$", re.I)
EXTRA_FILES = ("alfrd.notes.jsonl",)
TOKENIZE = "unicode61 tokenchars '+-_.'"


CONTENTLESS = sqlite3.sqlite_version_info >= (3, 43, 0)


def fts5_available() -> bool:
    try:
        con = sqlite3.connect(":memory:")
        con.execute(f"CREATE VIRTUAL TABLE t USING fts5(x, tokenize=\"{TOKENIZE}\")")
        con.close()
        return True
    except sqlite3.Error:
        return False


def index_dir() -> Path:
    from alfrd import get_alfrd_dir

    return Path(os.environ.get("ALFRD_SEARCH_DIR") or get_alfrd_dir() / "search")


def _safe(identifier: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._+-]+", "_", identifier)[-80:]
    return f"{name}-{hashlib.sha1(identifier.encode()).hexdigest()[:8]}"


def candidates(root: str | Path) -> dict[str, tuple[int, float]]:
    """``{rel: (size, mtime)}`` of the text files to index."""
    from alfrd.avica_layout import collect_studio_files

    base = Path(root)
    out: dict[str, tuple[int, float]] = {}
    try:
        data = collect_studio_files(base, log_tail=0, read=False)
        files = data["files"]
    except FileNotFoundError:
        files = []
    for item in files:
        rel = item["rel"]
        if item.get("marker") or BINARY.search(rel):
            continue
        out[rel] = (int(item.get("size") or 0), float(item.get("mtime") or 0))
    for rel in EXTRA_FILES:
        try:
            st = (base / rel).stat()
            out[rel] = (st.st_size, st.st_mtime)
        except OSError:
            pass
    return out


def _file_id(st: os.stat_result) -> str:
    return f"{st.st_dev:x}-{st.st_ino:x}"


_TOKEN = re.compile(r"[\w+\-.]+", re.UNICODE)


def terms(text: str) -> list[str]:
    """Query words split the way the tokenizer splits text (``wd_1/casa.log`` → two terms)."""
    return [t for t in _TOKEN.findall(str(text or "")) if t.strip(".-")][:12]


def _query(text: str) -> str:
    """User words → an FTS5 query: all terms required, a prefix query from 3 characters on."""
    return " AND ".join(f'"{t}"*' if len(t) >= 3 else f'"{t}"' for t in terms(text))


def _line_of(chunk: str, words: Sequence[str]) -> int:
    """0-based line in ``chunk`` of the first line containing every word (else any word)."""
    lines = chunk.split("\n")
    low = [w.lower() for w in words]
    for i, line in enumerate(lines):
        if all(w in line.lower() for w in low):
            return i
    for i, line in enumerate(lines):
        if any(w in line.lower() for w in low):
            return i
    return 0


def _snippet(line: str, words: Sequence[str], width: int = 160) -> str:
    low = line.lower()
    at = min([low.find(w.lower()) for w in words if w and low.find(w.lower()) >= 0] or [0])
    start = max(0, at - width // 3)
    text = line[start:start + width].strip()
    return ("…" if start else "") + text + ("…" if start + width < len(line) else "")


class SearchIndex:
    """The FTS5 index of one project (thread-safe; one connection per call)."""

    def __init__(self, root: str | Path, identifier: str, path: str | Path | None = None) -> None:
        self.root = Path(root).resolve()
        self.identifier = identifier
        self.path = Path(path) if path else index_dir() / f"{_safe(identifier)}.sqlite"
        self.lock = threading.RLock()
        self.progress: dict[str, Any] = {"state": "idle", "files_done": 0, "files_total": 0, "bytes": 0}

    # -- storage -------------------------------------------------------------
    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(self.path), timeout=30)
        try:
            row = con.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
        except sqlite3.OperationalError:
            row = None
        if row is not None and row[0] != str(SCHEMA_VERSION):  # an older index: rebuild it
            con.executescript("DROP TABLE IF EXISTS chunks; DROP TABLE IF EXISTS chunk_info; DROP TABLE IF EXISTS files; DELETE FROM meta;")
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        # Contentless (the text stays in the files; snippets are read from them) when SQLite can
        # delete from such a table (3.43+); otherwise the chunks keep their text (about 2.5x larger).
        store = "content='', contentless_delete=1," if CONTENTLESS else ""
        con.executescript(f"""
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS files (rel TEXT PRIMARY KEY, file_id TEXT, size INTEGER, mtime REAL,
                                              offset INTEGER, lines INTEGER, tail TEXT, partial INTEGER);
            CREATE TABLE IF NOT EXISTS chunk_info (id INTEGER PRIMARY KEY, rel TEXT, line INTEGER, off INTEGER, len INTEGER);
            CREATE INDEX IF NOT EXISTS chunk_rel ON chunk_info (rel);
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(text, {store} detail=column, tokenize="{TOKENIZE}");
        """)
        row = con.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
        if row is None:
            con.execute("INSERT OR REPLACE INTO meta VALUES ('schema', ?)", (str(SCHEMA_VERSION),))
            con.execute("INSERT OR REPLACE INTO meta VALUES ('root', ?)", (str(self.root),))
            con.commit()
        return con

    def stats(self) -> dict[str, Any]:
        with self.lock, self.connect() as con:
            files, text = con.execute("SELECT COUNT(*), COALESCE(SUM(offset), 0) FROM files").fetchone()
        size = sum(p.stat().st_size for p in self.path.parent.glob(self.path.name + "*") if p.is_file())
        return {"files": files, "text_bytes": text, "index_bytes": size, **self.progress}

    # -- indexing ------------------------------------------------------------
    def _drop(self, con: sqlite3.Connection, rel: str) -> None:
        con.execute("DELETE FROM chunks WHERE rowid IN (SELECT id FROM chunk_info WHERE rel = ?)", (rel,))
        con.execute("DELETE FROM chunk_info WHERE rel = ?", (rel,))
        con.execute("DELETE FROM files WHERE rel = ?", (rel,))

    def _index_file(self, con: sqlite3.Connection, rel: str, known: tuple | None) -> int:
        """Index new text of one file; returns bytes read.

        A log that only grew is indexed from the stored offset, provided the
        bytes just before it are still the same (a file truncated and written
        again in place is indexed from the start). The first ``MAX_FILE_BYTES``
        of a file are indexed, so line numbers stay exact. An unfinished last
        line is indexed as its own chunk and replaced once the line is complete.
        """
        path = self.root / rel
        try:
            st = path.stat()
        except OSError:
            self._drop(con, rel)
            return 0
        fid = _file_id(st)
        start, line = 0, 0
        if known is not None:
            k_id, k_size, k_mtime, k_off, k_lines, k_tail, k_partial = known
            if k_id == fid and st.st_size == k_size and abs(st.st_mtime - k_mtime) < 1e-6:
                return 0  # unchanged
            if k_id == fid and st.st_size >= k_off and _grew_only(rel) and _tail_hash(path, k_off) == k_tail:
                start, line = k_off, k_lines  # a log that grew: only the new part
                if k_partial:
                    con.execute("DELETE FROM chunks WHERE rowid = ?", (k_partial,))
                    con.execute("DELETE FROM chunk_info WHERE id = ?", (k_partial,))
            else:
                self._drop(con, rel)
        if start >= MAX_FILE_BYTES:
            con.execute("UPDATE files SET size = ?, mtime = ? WHERE rel = ?", (st.st_size, st.st_mtime, rel))
            return 0  # indexed up to the cap already
        rows: list[tuple[str, int, int, int]] = []
        partial: tuple[str, int, int, int] | None = None
        with open(path, "rb") as stream:
            stream.seek(start)
            offset = start
            buf: list[str] = []
            buf_line, buf_off, buf_bytes = line, offset, 0
            for raw in stream:
                if b"\x00" in raw[:512]:
                    rows, buf, partial = [], [], None
                    break  # binary after all
                if not raw.endswith(b"\n"):
                    text = raw.decode("utf-8", errors="replace").rstrip("\r")
                    if text.strip():
                        partial = (text, line + 1, offset, len(raw))
                    break
                if not buf:
                    buf_line, buf_off = line, offset
                buf.append(raw.decode("utf-8", errors="replace").rstrip("\r\n"))
                buf_bytes += len(raw)
                offset += len(raw)
                line += 1
                if len(buf) >= CHUNK_LINES or buf_bytes >= CHUNK_BYTES:
                    rows.append(("\n".join(buf), buf_line + 1, buf_off, offset - buf_off))
                    buf, buf_bytes = [], 0
                if offset >= MAX_FILE_BYTES:
                    break
            if buf:
                rows.append(("\n".join(buf), buf_line + 1, buf_off, offset - buf_off))
        for text, first, off, length in rows:
            cur = con.execute("INSERT INTO chunk_info (rel, line, off, len) VALUES (?, ?, ?, ?)", (rel, first, off, length))
            con.execute("INSERT INTO chunks (rowid, text) VALUES (?, ?)", (cur.lastrowid, text))
        partial_id = None
        if partial is not None and offset < MAX_FILE_BYTES:
            text, first, off, length = partial
            cur = con.execute("INSERT INTO chunk_info (rel, line, off, len) VALUES (?, ?, ?, ?)", (rel, first, off, length))
            con.execute("INSERT INTO chunks (rowid, text) VALUES (?, ?)", (cur.lastrowid, text))
            partial_id = cur.lastrowid
        con.execute("INSERT OR REPLACE INTO files VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (rel, fid, st.st_size, st.st_mtime, offset, line, _tail_hash(path, offset), partial_id))
        return offset - start + (partial[3] if partial else 0)

    def sync(self, only: Iterable[str] | None = None, removed: Iterable[str] = (), *, budget: float | None = None,
             nice: bool = False) -> dict[str, Any]:
        """Bring the index up to date (``only``: just these files, e.g. a live diff)."""
        with self.lock:
            con = self.connect()
            try:
                for rel in removed:
                    self._drop(con, rel)
                known = {r[0]: r[1:] for r in con.execute("SELECT rel, file_id, size, mtime, offset, lines, tail, partial FROM files")}
                if only is None:
                    wanted = candidates(self.root)
                    for rel in set(known) - set(wanted):
                        self._drop(con, rel)
                    todo = sorted(wanted, key=lambda r: wanted[r][0])  # small files first
                else:
                    allowed = candidates(self.root)
                    todo = [r for r in only if r in allowed]
                    for rel in set(only) - set(allowed):
                        if rel in known:
                            self._drop(con, rel)
                self.progress = {"state": "indexing", "files_done": 0, "files_total": len(todo), "bytes": 0}
                t0 = time.monotonic()
                for i, rel in enumerate(todo):
                    self.progress["bytes"] += self._index_file(con, rel, known.get(rel))
                    self.progress["files_done"] = i + 1
                    if (i + 1) % 20 == 0:
                        con.commit()
                        if nice:
                            time.sleep(0.01)
                    if budget is not None and time.monotonic() - t0 > budget:
                        con.commit()
                        self.progress["state"] = "partial"
                        return dict(self.progress)
                con.commit()
                self.progress["state"] = "ready"
                con.execute("INSERT OR REPLACE INTO meta VALUES ('synced', ?)", (str(time.time()),))
                con.commit()
                return dict(self.progress)
            finally:
                con.close()

    # -- querying ------------------------------------------------------------
    def search(self, text: str, limit: int = 50) -> list[dict[str, Any]]:
        query = _query(text)
        if not query:
            return []
        words = terms(text)
        con = self.connect()
        try:
            rows = con.execute(
                "SELECT i.rel, i.line, i.off, i.len, f.size, f.file_id, bm25(chunks) AS rank FROM chunks"
                " JOIN chunk_info i ON i.id = chunks.rowid LEFT JOIN files f ON f.rel = i.rel"
                " WHERE chunks MATCH ? ORDER BY rank LIMIT ?", (query, int(limit))).fetchall()
        except sqlite3.OperationalError:
            return []
        finally:
            con.close()
        out = []
        for rel, line, off, length, _size, fid, rank in rows:
            chunk = self._read_chunk(rel, off, length, fid)
            if chunk is None:
                continue  # the file changed since it was indexed; the next sync fixes the index
            at = _line_of(chunk, words)
            out.append({"rel": rel, "line": int(line) + at, "snippet": _snippet(chunk.split("\n")[at], words),
                        "score": round(-float(rank), 3)})
        return out

    def _read_chunk(self, rel: str, off: int, length: int, fid: str | None) -> str | None:
        try:
            path = self.root / rel
            st = path.stat()
            if fid and _file_id(st) != fid or st.st_size < off + length:
                return None
            with open(path, "rb") as stream:
                stream.seek(off)
                return stream.read(length).decode("utf-8", errors="replace").replace("\r\n", "\n").rstrip("\n")
        except OSError:
            return None


def _tail_hash(path: Path, offset: int, size: int = 256) -> str | None:
    """SHA-1 of the ``size`` bytes before ``offset``: tells an append from a rewrite."""
    try:
        with open(path, "rb") as stream:
            stream.seek(max(0, offset - size))
            return hashlib.sha1(stream.read(min(size, offset))).hexdigest()
    except OSError:
        return None


def _grew_only(rel: str) -> bool:
    """Logs are appended to; other files (CSV, YAML, JSON) are rewritten as a whole."""
    name = rel.rsplit("/", 1)[-1].lower()
    return "log" in name or name.endswith((".out", ".jsonl", ".txt")) or "mpi_and_err" in name


def scan(root: str | Path, text: str, *, limit: int = 50, budget: float = 5.0) -> dict[str, Any]:
    """Fallback B: read the files until ``limit`` hits or ``budget`` seconds."""
    base = Path(root).resolve()
    words = terms(text)
    low = [w.lower() for w in words]
    hits: list[dict[str, Any]] = []
    t0 = time.monotonic()
    done = True
    if not low:
        return {"hits": [], "complete": True}
    for rel in sorted(candidates(base)):
        if time.monotonic() - t0 > budget:
            done = False
            break
        try:
            with open(base / rel, "rb") as stream:
                for n, raw in enumerate(stream, 1):
                    line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                    lower = line.lower()
                    if all(w in lower for w in low):
                        hits.append({"rel": rel, "line": n, "snippet": _snippet(line, words), "score": 0})
                        if len(hits) >= limit:
                            return {"hits": hits, "complete": False}
        except OSError:
            continue
    return {"hits": hits, "complete": done}


def context(root: str | Path, rel: str, line: int, around: int = 40) -> dict[str, Any]:
    """Lines ``line ± around`` of a searchable file (ValueError / FileNotFoundError otherwise)."""
    base = Path(root).resolve()
    if rel not in candidates(base):
        raise ValueError(f"{rel} is not a searchable file of this project")
    line = max(1, int(line))
    first = max(1, line - around)
    out: list[str] = []
    with open(base / rel, "rb") as stream:
        for n, raw in enumerate(stream, 1):
            if n >= first:
                out.append(raw.decode("utf-8", errors="replace").rstrip("\r\n"))
            if n >= line + around:
                break
    return {"rel": rel, "line": line, "first": first, "lines": out}


# ---------------------------------------------------------------------------
# One index per project, built in the background (alfrd serve)

_INDEXES: dict[str, SearchIndex] = {}
_THREADS: dict[str, threading.Thread] = {}
_REG = threading.Lock()


def index_for(root: str | Path, identifier: str) -> SearchIndex:
    with _REG:
        idx = _INDEXES.get(identifier)
        if idx is None or idx.root != Path(root).resolve():
            idx = _INDEXES[identifier] = SearchIndex(root, identifier)
        return idx


def ensure_built(root: str | Path, identifier: str) -> SearchIndex:
    """Start (or resume) the first build in a low-priority background thread."""
    idx = index_for(root, identifier)
    with _REG:
        thread = _THREADS.get(identifier)
        if thread is not None and thread.is_alive():
            return idx
        if idx.progress.get("state") == "ready":
            return idx

        def work() -> None:
            try:
                idx.sync(nice=True)  # short pauses between batches keep the server responsive
            except Exception as error:  # noqa: BLE001
                idx.progress = {**idx.progress, "state": "error", "error": str(error)}

        thread = threading.Thread(target=work, name=f"alfrd-search-{identifier[-20:]}", daemon=True)
        _THREADS[identifier] = thread
        thread.start()
    return idx


def on_tree_event(root: str | Path, identifier: str, event: Mapping[str, Any]) -> None:
    """Live watcher diff → index update (only when the index was built already)."""
    idx = _INDEXES.get(identifier)
    if idx is None or idx.progress.get("state") not in ("ready", "partial"):
        return
    changed = list(event.get("changed") or []) + list((event.get("logs") or {}).keys())
    try:
        idx.sync(only=changed, removed=event.get("removed") or [])
    except Exception:  # noqa: BLE001 - search must never break live updates
        pass


__all__ = ["SearchIndex", "candidates", "context", "ensure_built", "fts5_available", "index_for", "on_tree_event", "scan"]
