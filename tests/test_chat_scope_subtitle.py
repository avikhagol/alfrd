"""Scheduled titles are useful without changing historical session/request records."""
from alfrd import chat
from tests.test_chat_orchestration import project  # noqa: F401

LEGACY = "Scheduled batch sch_secret (3 pending items)\nScheduled for: 2026-11-03\n\n1. Added yesterday; edited today; item i1\n   Stitch theme\n\n2. Added yesterday; edited today; item i2\n   Files tab\n\n3. Added yesterday; edited today; item i3\n   Graph menu\n"


def test_legacy_scheduled_summary():
    assert chat._scheduled_title(LEGACY) == "Stitch theme, Files tab, Graph menu"
    assert chat._prompt_title(LEGACY, {}) == "Stitch theme, Files tab, Graph menu"
    assert chat._scheduled_title("Ordinary request") is None
    assert chat._scheduled_title("Scheduled batch sch_secret (3 pending items)") == "Scheduled batch · 3 items"


def test_new_batch_header():
    batch = {"id": "sch_secret", "run_at": "2099-01-01", "timezone": "UTC", "items": [
        {"id": str(n), "text": text, "created_at": "today", "updated_at": "today"}
        for n, text in enumerate(["Stitch theme", "Files tab", "Graph menu", "Another task"])]}
    text = chat.build_scheduled_prompt(batch)
    assert text.splitlines()[0] == "Stitch theme, Files tab, Graph menu +1"
    assert "sch_" not in text.splitlines()[0] and "pending" not in text.splitlines()[0]
    assert "Another task" in text
    assert chat._batch_title(["", ""], 2) == "Scheduled batch · 2 items"


def test_existing_chat_display_preserves_disk(project):
    sid = chat.create_session(project)["id"]
    chat.post_message(project, sid, LEGACY, spawn=False)
    store = chat.session(project, sid)
    data = store.load()
    data.update(title="Scheduled batch sch_secret (3 pending items)", title_source="prompt")
    chat._write(store.session_file, data)
    before = {p: p.read_bytes() for p in [store.session_file, store.messages_file, store.requests_dir / "r0001.json"]}
    state = chat.session_state(project, sid)
    assert state["session"]["title"] == "Stitch theme, Files tab, Graph menu"
    assert state["requests"][0]["title"] == "Stitch theme, Files tab, Graph menu"
    assert state["requests"][0]["text"] == LEGACY.strip()
    assert chat.list_sessions(project)[0]["title"] == "Stitch theme, Files tab, Graph menu"
    assert all(p.read_bytes() == contents for p, contents in before.items())
    chat.rename_session(project, sid, "My custom name")
    assert chat.session_state(project, sid)["session"]["title"] == "My custom name"
