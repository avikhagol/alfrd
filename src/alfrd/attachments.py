"""Chat image attachments: sniffing, limits, content-addressed storage and GC.

Stdlib only. Dimensions are read from file headers (PNG, JPEG, WebP); no image
library is involved, so hostile pixel data is never decoded.

Layout (contract C12)::

    .alfrd/chat/<session>/attachments/<sha256>.<ext>    image bytes, mode 0600
    .alfrd/chat/<session>/attachments/<sha256>.json     metadata, mode 0600

The ref of an attachment is ``att_<sha256[:16]>``. Limits follow contract C11.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import time
from dataclasses import asdict, dataclass
from pathlib import Path

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_LONG_EDGE = 8000
MAX_PIXELS = 40_000_000
MAX_IMAGES_PER_MESSAGE = 8
MAX_BYTES_PER_MESSAGE = 20 * 1024 * 1024
SESSION_QUOTA_BYTES = 500 * 1024 * 1024
GC_MIN_AGE_SECONDS = 24 * 3600

ATTACHMENTS_DIRNAME = "attachments"
REF_PREFIX = "att_"
_REF_RE = re.compile(r"^att_[0-9a-f]{16}$")

_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class AttachmentError(ValueError):
    """Rejected attachment. ``code`` maps to an HTTP status in the route layer."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code  # unsupported_type | too_large | too_many | quota | invalid


@dataclass(frozen=True)
class Attachment:
    ref: str
    sha256: str
    media_type: str
    bytes: int
    width: int
    height: int
    name: str


# ---------------------------------------------------------------- sniffing

def sniff_media_type(data: bytes) -> str | None:
    """Media type from magic bytes, or None when not PNG/JPEG/WebP."""
    if data.startswith(_PNG_MAGIC):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _png_size(data: bytes) -> tuple[int, int]:
    if len(data) < 24 or data[12:16] != b"IHDR":
        raise AttachmentError("invalid", "PNG header is missing IHDR")
    return struct.unpack(">II", data[16:24])


_SOF = set(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}


def _jpeg_size(data: bytes) -> tuple[int, int]:
    i, n = 2, len(data)
    while i + 4 <= n:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker == 0xFF:  # fill byte
            i += 1
            continue
        if marker in (0x01, 0xD8) or 0xD0 <= marker <= 0xD7:  # no length field
            i += 2
            continue
        if marker == 0xD9:
            break
        length = struct.unpack(">H", data[i + 2:i + 4])[0]
        if length < 2:
            break
        if marker in _SOF:
            if i + 9 > n:
                break
            h, w = struct.unpack(">HH", data[i + 5:i + 9])
            return w, h
        i += 2 + length
    raise AttachmentError("invalid", "JPEG has no readable frame header")


def _webp_size(data: bytes) -> tuple[int, int]:
    if len(data) < 30:
        raise AttachmentError("invalid", "WebP header is truncated")
    kind = data[12:16]
    if kind == b"VP8 ":
        if data[23:26] != b"\x9d\x01\x2a":
            raise AttachmentError("invalid", "WebP VP8 start code is wrong")
        w, h = struct.unpack("<HH", data[26:30])
        return w & 0x3FFF, h & 0x3FFF
    if kind == b"VP8L":
        if data[20] != 0x2F:
            raise AttachmentError("invalid", "WebP VP8L signature is wrong")
        bits = struct.unpack("<I", data[21:25])[0]
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if kind == b"VP8X":
        w = int.from_bytes(data[24:27], "little") + 1
        h = int.from_bytes(data[27:30], "little") + 1
        return w, h
    raise AttachmentError("invalid", "WebP chunk type is not recognised")


def image_dimensions(data: bytes, media_type: str) -> tuple[int, int]:
    """(width, height) from the header only."""
    if media_type == "image/png":
        return _png_size(data)
    if media_type == "image/jpeg":
        return _jpeg_size(data)
    if media_type == "image/webp":
        return _webp_size(data)
    raise AttachmentError("unsupported_type", f"unsupported media type: {media_type}")


# ---------------------------------------------------------------- validation

def validate_image(data: bytes, declared_type: str | None = None) -> tuple[str, int, int]:
    """Check bytes against C11. Returns (media_type, width, height) or raises."""
    if len(data) > MAX_IMAGE_BYTES:
        raise AttachmentError("too_large", f"image exceeds {MAX_IMAGE_BYTES} bytes")
    if not data:
        raise AttachmentError("invalid", "empty file")
    declared = (declared_type or "").split(";")[0].strip().lower()
    if declared == "image/jpg":
        declared = "image/jpeg"
    if declared and declared not in _EXT:
        raise AttachmentError("unsupported_type", f"unsupported media type: {declared}")
    sniffed = sniff_media_type(data)
    if sniffed is None:
        raise AttachmentError("unsupported_type", "content is not PNG, JPEG or WebP")
    if declared and declared != sniffed:
        raise AttachmentError("unsupported_type", f"declared {declared} but content is {sniffed}")
    width, height = image_dimensions(data, sniffed)
    if width < 1 or height < 1:
        raise AttachmentError("invalid", "image has zero size")
    if max(width, height) > MAX_LONG_EDGE:
        raise AttachmentError("too_large", f"long edge {max(width, height)} exceeds {MAX_LONG_EDGE} px")
    if width * height > MAX_PIXELS:
        raise AttachmentError("too_large", f"{width * height} pixels exceeds {MAX_PIXELS}")
    return sniffed, width, height


def check_message_limits(sizes: list[int]) -> None:
    """Per-message limits: image count and total bytes."""
    if len(sizes) > MAX_IMAGES_PER_MESSAGE:
        raise AttachmentError("too_many", f"more than {MAX_IMAGES_PER_MESSAGE} images in one message")
    if sum(sizes) > MAX_BYTES_PER_MESSAGE:
        raise AttachmentError("too_large", f"images exceed {MAX_BYTES_PER_MESSAGE} bytes in one message")


# ---------------------------------------------------------------- storage

def ref_for(sha256: str) -> str:
    return REF_PREFIX + sha256[:16]


def is_valid_ref(ref: str) -> bool:
    return bool(isinstance(ref, str) and _REF_RE.match(ref))


def attachments_dir(session_dir: Path) -> Path:
    """``session_dir`` is ``.alfrd/chat/<session>``."""
    return Path(session_dir) / ATTACHMENTS_DIRNAME


def _meta_files(session_dir: Path) -> list[Path]:
    d = attachments_dir(session_dir)
    return sorted(d.glob("*.json")) if d.is_dir() else []


def session_usage(session_dir: Path) -> int:
    """Total stored image bytes for the session."""
    d = attachments_dir(session_dir)
    if not d.is_dir():
        return 0
    return sum(p.stat().st_size for p in d.iterdir() if p.suffix.lstrip(".") in _EXT.values())


def check_session_quota(session_dir: Path, incoming: int) -> None:
    if session_usage(session_dir) + incoming > SESSION_QUOTA_BYTES:
        raise AttachmentError("quota", "session attachment quota exceeded")


def _write_private(path: Path, payload: bytes) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def store_image(session_dir: Path, data: bytes, declared_type: str | None = None,
                name: str = "") -> Attachment:
    """Validate and store. Identical bytes map to the same ref and file."""
    media_type, width, height = validate_image(data, declared_type)
    sha = hashlib.sha256(data).hexdigest()
    meta_path = attachments_dir(session_dir) / f"{sha}.json"
    if meta_path.is_file():
        existing = get_attachment(session_dir, ref_for(sha))
        if existing is not None and (attachments_dir(session_dir) / f"{sha}.{_EXT[media_type]}").is_file():
            return existing
    check_session_quota(session_dir, len(data))
    d = attachments_dir(session_dir)
    d.mkdir(parents=True, exist_ok=True)
    name = os.path.basename(str(name or "").replace("\\", "/"))[:200]
    att = Attachment(ref_for(sha), sha, media_type, len(data), width, height, name)
    _write_private(d / f"{sha}.{_EXT[media_type]}", data)
    _write_private(meta_path, json.dumps(asdict(att), sort_keys=True).encode("utf-8"))
    return att


def get_attachment(session_dir: Path, ref: str) -> Attachment | None:
    """Metadata for a ref in this session, or None (unknown, malformed or other session)."""
    if not is_valid_ref(ref):
        return None
    d = attachments_dir(session_dir)
    if not d.is_dir():
        return None
    for meta in d.glob(f"{ref[len(REF_PREFIX):]}*.json"):
        try:
            return Attachment(**json.loads(meta.read_text("utf-8")))
        except (OSError, ValueError, TypeError):
            return None
    return None


def attachment_path(session_dir: Path, ref: str) -> Path | None:
    att = get_attachment(session_dir, ref)
    if att is None:
        return None
    p = attachments_dir(session_dir) / f"{att.sha256}.{_EXT[att.media_type]}"
    return p if p.is_file() else None


# ---------------------------------------------------------------- GC

def gc_attachments(session_dir: Path, referenced: set[str] | frozenset[str],
                   now: float | None = None, min_age: float = GC_MIN_AGE_SECONDS) -> list[str]:
    """Remove unreferenced attachments older than ``min_age``. Returns removed refs.

    ``referenced`` is the set of refs used by any message in the session; those
    files are never touched, whatever their age.
    """
    now = time.time() if now is None else now
    removed: list[str] = []
    for meta in _meta_files(session_dir):
        sha = meta.stem
        ref = ref_for(sha)
        if ref in referenced:
            continue
        files = [p for p in attachments_dir(session_dir).glob(f"{sha}.*")]
        try:
            newest = max(p.stat().st_mtime for p in files)
        except (OSError, ValueError):
            continue
        if now - newest < min_age:
            continue
        for p in files:
            try:
                p.unlink()
            except OSError:
                pass
        removed.append(ref)
    return removed
