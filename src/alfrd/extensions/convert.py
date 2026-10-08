"""Plugin converters: run one in a worker process and cache its output in the project.

The Studio asks for ``<project>/<rel>`` as ``pdf`` or ``png``; :func:`convert` finds an
active plugin converter for the file's extension, runs it in a fresh Python process
(``python -m alfrd.extensions.convert <plugin_id> <to> <src> <dest> <timeout>``, its own
session so a timeout kills every process it started) and keeps the result in
``<root>/.alfrd/cache/convert/<sha256>.<to>``. The key covers the source bytes and the
plugin id and version, so an edited file or an upgraded plugin converts again.
"""

from __future__ import annotations

import hashlib
import os
import signal
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

from alfrd import extensions
from alfrd.extensions import Converter, Record

MAX_SOURCE = 200 * 1024**2
MAX_OUTPUT = 500 * 1024**2
TIMEOUT = 120.0
#: Extra seconds the parent waits beyond ``timeout`` before killing the worker's process group.
GRACE = 5.0
TARGETS = {"pdf", "png"}
_CHUNK = 1024**2
_TAIL = 2000


class ConvertError(Exception):
    """A conversion failure with the HTTP ``status`` the Studio answers."""

    def __init__(self, status: int, message: str, detail_tail: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.detail_tail = detail_tail


def _ext(name: str) -> str:
    text = str(name or "").strip().lower()
    return text if text.startswith(".") else "." + text


def _to(name: str) -> str:
    return str(name or "").strip().lower().lstrip(".")


def _matches(conv: Converter, ext: str, to: str) -> bool:
    return _to(conv.to) == _to(to) and _ext(ext) in {_ext(s) for s in conv.src}


def find_converter(ext: str, to: str) -> tuple[Record, Converter] | None:
    """The first active plugin's converter from ``ext`` (``.ps``, any case) to ``to``."""
    for rec in extensions.loaded():
        if rec.plugin is None or not extensions.active(rec):
            continue
        for conv in rec.plugin.converters:
            if _matches(conv, ext, to):
                return rec, conv
    return None


def missing_binary(ext: str, to: str) -> Record | None:
    """An enabled plugin that would convert ``ext`` to ``to`` but lacks a required program."""
    disabled = set(extensions.read_state()["disabled"])
    for rec in extensions.loaded():
        if rec.plugin is None or rec.id in disabled or not rec.status.startswith("missing binary"):
            continue
        if any(_matches(conv, ext, to) for conv in rec.plugin.converters):
            return rec
    return None


def cache_dir(root: Path) -> Path:
    return Path(root) / ".alfrd" / "cache" / "convert"


def cache_path(root: Path, src: Path, rec: Record, to: str) -> Path:
    """``<root>/.alfrd/cache/convert/<sha256(source bytes, plugin id, version)>.<to>``."""
    digest = hashlib.sha256()
    with open(src, "rb") as stream:
        while chunk := stream.read(_CHUNK):
            digest.update(chunk)
    version = rec.version or (rec.plugin.version if rec.plugin is not None else "")
    digest.update(b"\0" + rec.id.encode() + b"\0" + str(version).encode())
    return cache_dir(root) / f"{digest.hexdigest()}.{_to(to)}"


def _resolve(root: Path, rel: str) -> Path:
    base = Path(root).resolve()
    if not rel or rel.startswith(("/", "\\")) or Path(rel).is_absolute() or ".." in Path(rel.replace("\\", "/")).parts:
        raise ConvertError(400, "invalid path")
    try:
        path = (base / rel).resolve()
    except (OSError, RuntimeError) as exc:
        raise ConvertError(400, f"invalid path: {exc}") from exc
    if not path.is_relative_to(base):  # a symlink out of the project
        raise ConvertError(403, f"{rel} is outside the project")
    if not path.is_file():
        raise ConvertError(404, f"{rel} not found")
    return path


def _worker_cmd(plugin_id: str, to: str, src: Path, dest: Path, timeout: float) -> list[str]:
    """The worker command line (tests replace it)."""
    return [sys.executable, "-m", "alfrd.extensions.convert", plugin_id, to, str(src), str(dest), str(timeout)]


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        if hasattr(os, "killpg"):
            os.killpg(proc.pid, signal.SIGKILL)
        else:  # pragma: no cover - Windows
            proc.kill()
    except (ProcessLookupError, PermissionError):
        pass


def convert(root: Path, rel: str, to: str, *, timeout: float = TIMEOUT) -> Path:
    """The cached ``to`` rendering of ``<root>/<rel>``, converting it first if needed.

    Raises :class:`ConvertError` (400 bad path or target, 403 outside the project,
    404 no file or converter, 413 too large, 502 converter failed, 503 missing
    binary, 504 timeout).
    """
    target = _to(to)
    if target not in TARGETS:
        raise ConvertError(400, f"unknown target {to!r} (expected one of {', '.join(sorted(TARGETS))})")
    root = Path(root).resolve()
    src = _resolve(root, rel)
    ext = src.suffix.lower()
    found = find_converter(ext, target)
    if found is None:
        missing = missing_binary(ext, target)
        if missing is not None:
            raise ConvertError(503, "missing binary: " + ", ".join(missing.missing_bin))
        raise ConvertError(404, f"no converter for {ext or '(no extension)'} → {target}")
    rec, _conv = found
    try:
        size = src.stat().st_size
        if size > MAX_SOURCE:
            raise ConvertError(413, f"{rel} is {size} bytes; the converter limit is {MAX_SOURCE}")
        final = cache_path(root, src, rec, target)
    except PermissionError as exc:
        raise ConvertError(403, f"cannot read {rel}: {exc.strerror or exc}") from exc
    except OSError as exc:
        raise ConvertError(404, f"cannot read {rel}: {exc.strerror or exc}") from exc
    if final.is_file() and final.stat().st_size > 0:
        return final

    final.parent.mkdir(parents=True, exist_ok=True)
    # Ends in ".<to>": converters that pick the format from the file name still work.
    fd, tmp_name = tempfile.mkstemp(prefix=final.stem + ".", suffix=f".part.{target}", dir=final.parent)
    os.close(fd)
    tmp = Path(tmp_name)
    tmp.unlink()  # the converter creates it; an empty leftover never counts as output
    try:
        proc = subprocess.Popen(_worker_cmd(rec.id, target, src, tmp, float(timeout)), stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, start_new_session=True)
        try:
            _out, err = proc.communicate(timeout=float(timeout) + GRACE)
        except subprocess.TimeoutExpired:
            _kill_group(proc)
            proc.communicate()
            raise ConvertError(504, f"converter {rec.id!r} timed out after {timeout:g} s") from None
        tail = (err or b"").decode("utf-8", "replace")[-_TAIL:]
        if proc.returncode != 0:
            raise ConvertError(502, f"converter {rec.id!r} failed (exit {proc.returncode})", tail)
        try:
            out_size = tmp.stat().st_size
        except OSError:
            out_size = 0
        if out_size <= 0:
            raise ConvertError(502, f"converter {rec.id!r} wrote no output", tail)
        if out_size > MAX_OUTPUT:
            raise ConvertError(413, f"converter output is {out_size} bytes; the limit is {MAX_OUTPUT}")
        os.replace(tmp, final)
    finally:
        tmp.unlink(missing_ok=True)
    return final


def main(argv: list[str] | None = None) -> int:
    """Worker: ``<plugin_id> <to> <src> <dest> <timeout>``; 0 done, 1 converter failed, 2 not found."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 5:
        print("usage: python -m alfrd.extensions.convert PLUGIN_ID TO SRC DEST TIMEOUT", file=sys.stderr)
        return 2
    plugin_id, to, src, dest, timeout = args
    extensions.add_site()
    extensions.load()
    rec = next((r for r in extensions.loaded() if r.id == plugin_id and r.status == "ok" and r.plugin is not None), None)
    conv = None
    if rec is not None:
        conv = next((c for c in rec.plugin.converters if _matches(c, Path(src).suffix, to)), None)
    if conv is None:
        print(f"no active plugin {plugin_id!r} converting {Path(src).suffix or '(no extension)'} → {_to(to)}", file=sys.stderr)
        return 2
    try:
        conv.run(Path(src), Path(dest), timeout=float(timeout))
    except BaseException:  # noqa: BLE001 - report everything, even SystemExit from a plugin
        text = traceback.format_exc()
        sys.stderr.write(text[-_TAIL:])
        sys.stderr.flush()
        return 1
    return 0


__all__ = ["GRACE", "MAX_OUTPUT", "MAX_SOURCE", "TARGETS", "TIMEOUT", "ConvertError", "cache_dir", "cache_path",
           "convert", "find_converter", "main", "missing_binary"]


if __name__ == "__main__":
    sys.exit(main())
