"""Incremental alfrd.telemetry/1 sidecar: bounded reads, rebuild triggers, read-only raw log, _render_log guard."""

import hashlib
import json
import os
import pathlib

from alfrd import chat, telemetry


def call(i, name="Read"):
    return json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": f"t{i}", "name": name, "input": {}}]}}) + "\n"


def write(path, n, start=0):
    with open(path, "ab") as out:
        for i in range(start, start + n):
            out.write(call(i).encode())


def calls(doc):
    return doc["counts"]["tool_calls"]["value"]


class Reads:
    """Records each read() size on the log under test."""

    def __init__(self, monkeypatch, target):
        self.sizes = []
        real = pathlib.Path.open
        sizes = self.sizes

        class Wrap:
            def __init__(self, stream):
                self.stream = stream

            def read(self, n=-1):
                sizes.append(n)
                return self.stream.read(n)

            def __getattr__(self, name):
                return getattr(self.stream, name)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                self.stream.close()

        def patched(self, mode="r", *a, **k):
            stream = real(self, mode, *a, **k)
            return Wrap(stream) if self == target and "b" in mode else stream

        monkeypatch.setattr(pathlib.Path, "open", patched)


def test_read_bound_and_chunks(tmp_path, monkeypatch):
    log = tmp_path / "u.events.jsonl"
    write(log, 60000)  # ~ 6 MB, over the 4 MB bound
    assert log.stat().st_size > telemetry.ADVANCE_BYTES
    reads = Reads(monkeypatch, log)
    doc = telemetry.advance(log, "claude")
    assert reads.sizes and all(0 < n <= 64 * 1024 for n in reads.sizes)
    assert sum(reads.sizes) <= 4 * 1024 * 1024
    assert doc["behind"] and doc["offset"] <= 4 * 1024 * 1024
    assert doc["coverage"] == "partial"
    first = calls(doc)
    doc = telemetry.advance(log, "claude")
    assert not doc["behind"] and doc["offset"] == log.stat().st_size
    assert first < calls(doc) == 60000 and doc["coverage"] == "complete"
    assert doc["schema"] == "alfrd.telemetry/1"


def test_incremental_matches_full_parse(tmp_path):
    log = tmp_path / "u.events.jsonl"
    write(log, 10)
    telemetry.advance(log, "claude")
    write(log, 10, 5)  # ids 5..9 repeat: dedupe state must persist
    with open(log, "ab") as out:
        out.write(call(99)[:20].encode())  # partial tail
    doc = telemetry.advance(log, "claude")
    assert calls(doc) == 15 and not doc["rebuilt"]
    with open(log, "ab") as out:
        out.write(call(99)[20:].encode())
    assert calls(telemetry.advance(log, "claude")) == 16
    final = telemetry.advance(log, "claude", finalize=True)
    assert calls(final) == calls(telemetry.parse_log(log, "claude")) == 16 and final["finalized"]


def test_finalize_marks_partial_tail(tmp_path):
    log = tmp_path / "u.events.jsonl"
    write(log, 2)
    with open(log, "ab") as out:
        out.write(b'{"type": "assistant", "mess')
    final = telemetry.advance(log, "claude", finalize=True)
    assert final["partial_tail"] and final["coverage"] == "partial" and calls(final) == 2


def test_rebuild_triggers(tmp_path, monkeypatch):
    log = tmp_path / "u.events.jsonl"
    write(log, 5)
    assert telemetry.advance(log, "claude")["rebuilt"]
    write(log, 1, 5)
    assert not telemetry.advance(log, "claude")["rebuilt"]
    # size: file shrinks
    log.write_bytes(call(0).encode())
    doc = telemetry.advance(log, "claude")
    assert doc["rebuilt"] and calls(doc) == 1
    # inode: replaced by a larger file
    write(log, 3, 1)
    assert not telemetry.advance(log, "claude")["rebuilt"]
    other = tmp_path / "other"
    other.write_bytes(log.read_bytes() + call(50).encode())
    os.replace(other, log)
    doc = telemetry.advance(log, "claude")
    assert doc["rebuilt"] and calls(doc) == 5
    # parser version
    write(log, 1, 60)
    monkeypatch.setattr(telemetry, "PARSER_VERSION", "2")
    doc = telemetry.advance(log, "claude")
    assert doc["rebuilt"] and doc["parser_version"] == "2" and calls(doc) == 6


def test_oversize_line_across_calls(tmp_path):
    log = tmp_path / "u.events.jsonl"
    write(log, 1)
    with open(log, "ab") as out:
        out.write(b'{"type": "assistant", "pad": "' + b"x" * (3 * 1024 * 1024) + b'"}\n')
    write(log, 1, 1)
    doc = telemetry.advance(log, "claude", budget=1024 * 1024)
    assert doc["skipping"] or doc["behind"]
    while doc["behind"]:
        doc = telemetry.advance(log, "claude", budget=1024 * 1024)
    assert doc["oversize_lines"] == 1 and calls(doc) == 2
    full = telemetry.parse_log(log, "claude")
    assert full["oversize_lines"] == 1 and calls(full) == 2


def test_raw_log_untouched(tmp_path):
    log = tmp_path / "u.events.jsonl"
    write(log, 20)
    with open(log, "ab") as out:
        out.write(b"{broken")
    before = hashlib.sha256(log.read_bytes()).hexdigest()
    mtime = log.stat().st_mtime_ns
    telemetry.advance(log, "claude")
    telemetry.advance(log, "claude", finalize=True)
    assert hashlib.sha256(log.read_bytes()).hexdigest() == before
    assert log.stat().st_mtime_ns == mtime
    assert telemetry.sidecar_path(log).is_file()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["u.events.telemetry.json", "u.events.jsonl"] or \
        sorted(p.name for p in tmp_path.iterdir()) == sorted(["u.events.jsonl", telemetry.sidecar_path(log).name])


def test_unchanged_log_is_not_rescanned(tmp_path, monkeypatch):
    log = tmp_path / "u.events.jsonl"
    write(log, 3)
    telemetry.advance(log, "claude")
    reads = Reads(monkeypatch, log)
    assert calls(telemetry.advance(log, "claude")) == 3
    assert reads.sizes == []


def test_missing_log_is_unavailable(tmp_path):
    doc = telemetry.advance(tmp_path / "none.events.jsonl", "claude")
    assert doc["coverage"] == "unavailable"


def test_render_log_guard():
    huge = '{"type": "result", "result": "' + "x" * (chat.RENDER_MAX_LINE + 10) + '"}'
    out = chat._render_log("before\n" + huge + "\nafter")
    assert out[0] == "before" and out[-1] == "after"
    assert all(len(line) <= 2000 for line in out) and len(out) == 3
    assert chat._render_log('{"type": "turn.completed", "usage": {}}') == ["[turn.completed] {}"]
