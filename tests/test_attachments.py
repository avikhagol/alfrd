"""Attachment store: sniffing, limits, dedupe, mode and GC (contracts C11, C12)."""

from __future__ import annotations

import os
import stat
import struct
import zlib

import pytest

from alfrd import attachments as att


def png(w=2, h=3, pad=0):
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return att._PNG_MAGIC + chunk(b"IHDR", ihdr) + chunk(b"IEND", b"") + b"\0" * pad


def jpeg(w=5, h=4):
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\0" + b"\x01\x01\0\0\x01\0\x01\0\0"
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, h, w, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + app0 + sof + b"\xff\xd9"


def webp_vp8x(w=6, h=7):
    body = b"VP8X" + struct.pack("<I", 10) + b"\0\0\0\0" + (w - 1).to_bytes(3, "little") + (h - 1).to_bytes(3, "little")
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WEBP" + body


def webp_vp8l(w=8, h=9):
    bits = (w - 1) | ((h - 1) << 14)
    body = b"VP8L" + struct.pack("<I", 5) + b"\x2f" + struct.pack("<I", bits)
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WEBP" + body + b"\0" * 6


@pytest.fixture
def sdir(tmp_path):
    return tmp_path / ".alfrd" / "chat" / "s1"


def test_dimensions_all_formats():
    assert att.validate_image(png(2, 3), "image/png") == ("image/png", 2, 3)
    assert att.validate_image(jpeg(5, 4), "image/jpeg") == ("image/jpeg", 5, 4)
    assert att.validate_image(webp_vp8x(6, 7), "image/webp") == ("image/webp", 6, 7)
    assert att.validate_image(webp_vp8l(8, 9)) == ("image/webp", 8, 9)


def test_oversize_bytes_rejected():
    with pytest.raises(att.AttachmentError) as e:
        att.validate_image(png(pad=att.MAX_IMAGE_BYTES), "image/png")
    assert e.value.code == "too_large"


def test_long_edge_rejected():
    with pytest.raises(att.AttachmentError) as e:
        att.validate_image(png(8001, 10))
    assert e.value.code == "too_large"
    att.validate_image(png(8000, 10))


def test_too_many_pixels_rejected():
    with pytest.raises(att.AttachmentError) as e:
        att.validate_image(png(7000, 6000))  # 42 MP, edges within 8000
    assert e.value.code == "too_large"
    att.validate_image(png(8000, 5000))  # exactly 40 MP


def test_svg_rejected():
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    for declared in ("image/svg+xml", "image/png", None):
        with pytest.raises(att.AttachmentError) as e:
            att.validate_image(svg, declared)
        assert e.value.code == "unsupported_type"


def test_mismatched_magic_rejected():
    with pytest.raises(att.AttachmentError) as e:
        att.validate_image(jpeg(), "image/png")
    assert e.value.code == "unsupported_type"
    with pytest.raises(att.AttachmentError):
        att.validate_image(b"GIF89a" + b"\0" * 20, "image/png")


def test_truncated_headers_rejected():
    for data in (att._PNG_MAGIC + b"\0\0", b"\xff\xd8\xff\xd9", b"RIFF\0\0\0\0WEBPVP8X"):
        with pytest.raises(att.AttachmentError) as e:
            att.validate_image(data)
        assert e.value.code == "invalid"


def test_message_limits():
    att.check_message_limits([1] * 8)
    with pytest.raises(att.AttachmentError) as e:
        att.check_message_limits([1] * 9)
    assert e.value.code == "too_many"
    with pytest.raises(att.AttachmentError) as e:
        att.check_message_limits([5 * 1024 * 1024] * 5)
    assert e.value.code == "too_large"


def test_store_dedupe_and_ref(sdir):
    data = png(4, 4)
    a = att.store_image(sdir, data, "image/png", "../../shot.png")
    b = att.store_image(sdir, data, "image/png", "other.png")
    assert a == b
    assert a.ref == "att_" + a.sha256[:16]
    assert a.name == "shot.png"
    files = sorted(p.name for p in att.attachments_dir(sdir).iterdir())
    assert files == [f"{a.sha256}.json", f"{a.sha256}.png"]
    assert att.attachment_path(sdir, a.ref).read_bytes() == data
    assert att.get_attachment(sdir, a.ref) == a


def test_store_mode_0600(sdir):
    att.store_image(sdir, png(), "image/png")
    for p in att.attachments_dir(sdir).iterdir():
        assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_get_rejects_bad_and_foreign_refs(sdir, tmp_path):
    a = att.store_image(sdir, png(), "image/png")
    assert att.get_attachment(sdir, "att_../../etc/passwd") is None
    assert att.get_attachment(sdir, "att_" + "0" * 16) is None
    assert att.get_attachment(tmp_path / "other", a.ref) is None


def test_session_quota(sdir, monkeypatch):
    monkeypatch.setattr(att, "SESSION_QUOTA_BYTES", 100)
    att.store_image(sdir, png(1, 1), "image/png")
    with pytest.raises(att.AttachmentError) as e:
        att.store_image(sdir, png(2, 2, pad=200), "image/png")
    assert e.value.code == "quota"
    assert att.session_usage(sdir) == len(png(1, 1))


def test_gc_skips_referenced_and_recent(sdir):
    old_ref = att.store_image(sdir, png(1, 1), "image/png")
    kept = att.store_image(sdir, png(2, 2), "image/png")
    fresh = att.store_image(sdir, png(3, 3), "image/png")
    past = os.path.getmtime(att.attachment_path(sdir, fresh.ref)) - 3 * 24 * 3600
    for a in (old_ref, kept):
        for p in att.attachments_dir(sdir).glob(f"{a.sha256}.*"):
            os.utime(p, (past, past))
    removed = att.gc_attachments(sdir, {kept.ref})
    assert removed == [old_ref.ref]
    assert att.attachment_path(sdir, old_ref.ref) is None
    assert att.attachment_path(sdir, kept.ref) is not None
    assert att.attachment_path(sdir, fresh.ref) is not None
    assert not list(att.attachments_dir(sdir).glob(f"{old_ref.sha256}.*"))
