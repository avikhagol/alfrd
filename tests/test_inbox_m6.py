"""M6 summaries, presentation order and competing browser replies."""
import pytest

from alfrd import chat
from alfrd.mailbox import Mailbox, MailboxError, USER
from test_agent_mailbox import AGENT, answer, box, permission, project, question  # noqa: F401


def test_session_counts_mail_not_info_or_duplicate_blockers(box):
    store = box.session
    box.send(AGENT, question("info", kind="info", payload={}))
    box.send(AGENT, question("team", recipient={"kind": "orchestrator"}))
    question_id = box.send(AGENT, question())["message"]["id"]
    box.send(AGENT, permission())
    box.send(AGENT, question("expired", expires_at="2000-01-01T00:00:00Z"))
    for did, extra in [("linked", {"mailbox_message_id": question_id}), ("legacy", {}),
                       ("orchestrator", {"needs": "orchestrator"}), ("resolved", {"status": "resolved"})]:
        store.save_decision({"id": did, "request": box.run_id, "status": "pending", "needs": "user", **extra})
    assert box.summary()["pending_user"] == 2
    assert chat.list_sessions(store.root)[0]["pending_user"] == 3
    box.reply(question_id, USER, answer())
    assert chat.list_sessions(store.root)[0]["pending_user"] == 2
    box.cancel()
    assert chat.list_sessions(store.root)[0]["pending_user"] == 1
    empty = chat.create_session(store.root)
    assert next(s for s in chat.list_sessions(store.root) if s["id"] == empty["id"])["pending_user"] == 0


def test_pending_priority_permission_first_without_changing_cursor_pages(box):
    messages = [box.send(AGENT, payload)["message"] for payload in [
        question("low", priority="low"), question("urgent", priority="urgent"),
        permission("permission-low", priority="low"), permission("permission-high", priority="high"),
        question("urgent-next", priority="urgent"), question("normal", priority="normal")]]
    before = box.summary()["last_cursor"]
    assert [m["seq"] for m in box.pending()["messages"]] == [4, 3, 2, 5, 6, 1]
    assert box.pending()["cursor"] == before
    page = box.list(limit=2)
    assert [m["seq"] for m in page["messages"]] == [1, 2]
    rest = box.list(after=page["next_cursor"])
    assert [m["id"] for m in rest["messages"]] == [m["id"] for m in messages[2:]]
    assert rest["next_cursor"] == before


def test_two_tabs_get_answered_elsewhere_but_retry_remains_idempotent(box):
    mid = box.send(AGENT, question())["message"]["id"]
    other = Mailbox(box.session.root, box.session.id, box.run_id)
    first = box.reply(mid, USER, answer("tab-1"))
    assert box.reply(mid, USER, answer("tab-1")) == first
    with pytest.raises(MailboxError, match="Answered elsewhere") as conflict:
        other.reply(mid, USER, answer("tab-2"))
    assert conflict.value.status == 409
    assert conflict.value.details["state"] == "resolved"
    assert other.summary()["pending_user"] == 0
    assert chat.list_sessions(box.session.root)[0]["pending_user"] == 0


@pytest.mark.parametrize("status", sorted(chat.REQUEST_ACTIVE))
def test_compact_activity_counts_sessions_once_without_transcripts(project, monkeypatch, status):
    sid = chat.create_session(project)["id"]
    store = chat.session(project, sid)
    store.save_request({"id": "r1", "status": status})
    store.save_request({"id": "r2", "status": status})
    chat.create_session(project, "recent but idle")
    def forbidden(*args, **kwargs):
        raise AssertionError("compact activity read a transcript or detail graph")
    monkeypatch.setattr(chat.Session, "messages", forbidden)
    monkeypatch.setattr(chat.Session, "tasks", forbidden)
    assert chat.session_activity(project) == {"active_sessions": 1, "needs_user_sessions": 0}
    store.save_request({"id": "r1", "status": "done"})
    store.save_request({"id": "r2", "status": "failed"})
    assert chat.session_activity(project) == {"active_sessions": 0, "needs_user_sessions": 0}


def test_compact_attention_resolution_expiry_and_link_deduplication(box):
    store = box.session
    req = store.request(box.run_id)
    mid = box.send(AGENT, question())["message"]["id"]
    box.send(AGENT, permission())
    box.send(AGENT, question("expired", expires_at="2000-01-01T00:00:00Z"))
    store.save_decision({"id": "linked", "status": "pending", "needs": "user", "mailbox_message_id": mid})
    assert chat.session_activity(store.root) == {"active_sessions": 1, "needs_user_sessions": 1}
    store.save_request({**req, "status": "done"})
    assert chat.session_activity(store.root) == {"active_sessions": 1, "needs_user_sessions": 1}
    store.save_request(req)
    box.reply(mid, USER, answer())
    box.cancel()
    store.save_request({**req, "status": "done"})
    assert chat.session_activity(store.root) == {"active_sessions": 0, "needs_user_sessions": 0}
