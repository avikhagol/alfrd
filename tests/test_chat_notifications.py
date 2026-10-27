"""Durable attention state rather than runner-idle completion notifications."""
import pytest

from alfrd import chat, notify, notifiers
from alfrd.chat_notifications import Changes
from alfrd.studio_live import AlfrdWatcher
from test_agent_mailbox import AGENT, box, permission, question, answer  # noqa: F401
from test_chat_orchestration import project  # noqa: F401


@pytest.mark.parametrize("make,kind", [(permission, "approval.needed"), (question, "decision.needed")])
def test_mail_and_linked_blocker_notify_once(box, make, kind):
    changes = Changes()
    assert changes.poll(box.session.root) == []
    mail = box.send(AGENT, make())["message"]
    store = box.session
    store.save_decision({"id": "d1", "request": box.run_id, "status": "pending", "needs": "user",
                         "mailbox_message_id": mail["id"], "question": "linked"})
    records = changes.poll(store.root)
    assert [r["kind"] for r in records] == [kind]
    assert changes.poll(store.root) == []
    record = records[0]
    assert "/#/chat/" in record["link"]
    message = notify.build_message(records)
    for rendered in (notifiers.telegram_html({"studio_url": "https://studio.example"}, message),
                     notifiers.telegram_text({"studio_url": "https://studio.example"}, message)):
        assert record["title"] in rendered
        assert "https://studio.example/studio/#/chat/" in rendered
        assert "task finished" not in rendered
        if kind == "approval.needed":
            full = mail["payload"]["action_hash"]
            assert full.removeprefix("sha256:")[:12] in rendered
            assert full not in rendered
            assert "Publish preview" in rendered


def test_permission_and_terminal_request_states(box, tmp_path):
    store = box.session
    changes = Changes(tmp_path / "seen.json")
    store.save_decision({"id": "d1", "request": box.run_id, "status": "pending", "needs": "user",
                         "kind": "permission", "question": "Need access", "requires": {"folders": ["/data"]}})
    record, = changes.poll(store.root)
    assert record["kind"] == "permission.needed" and "/data" in record["body"]
    changes.save()
    assert Changes(tmp_path / "seen.json").poll(store.root) == []
    req = store.request(box.run_id)
    req["status"] = "done"
    store.save_request(req)
    assert changes.poll(store.root) == []  # pending decision still forbids completion
    d = store.decision("d1")
    d["status"] = "resolved"
    store.save_decision(d)
    req["rows"] = []
    store.save_request(req)
    assert [r["kind"] for r in changes.poll(store.root)] == ["request.finished"]
    assert changes.poll(store.root) == []
    req["status"] = "failed"
    store.save_request(req)
    assert [r["kind"] for r in changes.poll(store.root)] == ["request.failed"]


def test_live_mail_only_on_idle_plan_and_no_poll_replay(box):
    watcher = AlfrdWatcher("p", box.session.root, lambda _: None)
    watcher.check()
    box.send(AGENT, question())
    from alfrd.events import EventLog
    log = EventLog(box.session.root / ".alfrd" / "plans" / box.session.plan_id / "events.jsonl")
    log.append({"kind": "task.finished", "plan": box.session.plan_id, "data": {"status": "done"}})
    log.append({"kind": "plan.finished", "plan": box.session.plan_id})
    update = watcher.check()
    assert [r["kind"] for r in update["events"] if not r.get("notification_suppressed")] == ["decision.needed"]
    assert watcher.check() is None


def test_dispatcher_suppresses_idle_and_attempt_completions(box):
    store = box.session
    box.send(AGENT, permission())
    sent = []
    folder = store.root / ".alfrd" / "plans" / store.plan_id
    dispatcher = notify.Dispatcher(folder, [{"via": "desktop", "on": ["*"]}],
                                   senders={"desktop": lambda r, m, t: sent.append(m)})
    events = [{"kind": kind, "plan": store.plan_id, "data": {"status": "done"}}
              for kind in ("turn.finished", "task.finished", "plan.finished")]
    dispatcher._deliver_batch(events)
    assert len(sent) == 1
    assert sent[0]["kinds"] == ["approval.needed"]
    dispatcher._deliver_batch(events)
    assert len(sent) == 1


def test_telegram_route_and_bot_share_dedup(monkeypatch, tmp_path):
    monkeypatch.setattr(notifiers, "outbox_file", lambda _: tmp_path / "outbox.jsonl")
    monkeypatch.setattr(notifiers, "telegram_settings", lambda: {})
    sent = []
    monkeypatch.setattr(notifiers, "_telegram", lambda *args: sent.append(args))
    record = {"notification_id": "m1", "kind": "approval.needed", "title": "Approval needed"}
    message = notify.build_message([record])
    notifiers.telegram({"chat_id": "1"}, message, 10)
    notifiers.telegram({"chat_id": "1"}, message, 10)
    assert len(sent) == 1


def test_terminal_task_failure_once(box):
    store = box.session
    rec = store.task("r0001.a")
    rec.update(status="failed", error="Could not build")
    store.save_task(rec)
    changes = Changes()
    record, = changes.poll(store.root)
    assert record["kind"] == "task.failed"
    assert record["body"] == "Could not build"
    assert changes.poll(store.root) == []


def test_unreadable_session_does_not_hide_others(box):
    store = box.session
    broken = store.root / chat.CHAT_DIR / "0-broken"
    broken.mkdir(parents=True)
    (broken / "session.json").write_text("{")
    box.send(AGENT, question())
    assert [r["kind"] for r in Changes().poll(store.root)] == ["decision.needed"]
