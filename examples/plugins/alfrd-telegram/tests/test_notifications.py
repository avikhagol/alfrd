from alfrd import chat_notifications, notifiers
from alfrd_telegram.bot import Bot


class Plans:
    def projects(self):
        return {"p": {"root": "/project", "name": "Project"}}

    def loops(self, root):
        return []


def test_decisions_once_across_polls_and_restart(monkeypatch, tmp_path):
    records = []
    monkeypatch.setattr(chat_notifications, "snapshot", lambda *_: list(records))
    sent = []
    monkeypatch.setattr(notifiers, "telegram", lambda route, message, timeout: sent.append((route, message)))
    route = {"token": "test", "chat_id": "1", "hold_minutes": 5, "mute_when_active": True,
             "studio_url": "https://studio.example"}
    state = tmp_path / "bot.json"
    bot = Bot(route, Plans(), state=state)
    bot.watch()
    for index, kind in enumerate(("approval.needed", "decision.needed", "permission.needed", "request.finished", "request.failed")):
        records[:] = [{"notification_id": str(index), "kind": kind, "title": kind,
                       "body": "Review this", "link": "/studio/#/chat/session?p=p", "plan": "chat-session"}]
        bot.next_watch = 0
        bot.watch()
        bot.next_watch = 0
        bot.watch()
        assert len(sent) == index + 1
        assert sent[-1][0] == route
        assert sent[-1][1]["kinds"] == [kind]
        assert "reply_markup" not in sent[-1][1]  # notifications cannot approve anything
    again = Bot(route, Plans(), state=state)
    again.watch()
    assert len(sent) == 5


def test_restart_reports_requests_that_ended_while_down(monkeypatch, tmp_path):
    records = []
    monkeypatch.setattr(chat_notifications, "snapshot", lambda *_: list(records))
    sent = []
    monkeypatch.setattr(notifiers, "telegram", lambda route, message, timeout: sent.append(message))
    state = tmp_path / "bot.json"
    Bot({"token": "test", "chat_id": "1"}, Plans(), state=state).watch()
    records[:] = [{"notification_id": "done", "kind": "request.finished", "title": "Request finished"}]
    Bot({"token": "test", "chat_id": "1"}, Plans(), state=state).watch()
    assert [m["kinds"] for m in sent] == [["request.finished"]]


def test_route_event_filter_applies(monkeypatch):
    monkeypatch.setattr(chat_notifications, "snapshot", lambda *_: [
        {"notification_id": "a", "kind": "approval.needed", "title": "Approval needed"},
        {"notification_id": "f", "kind": "task.failed", "title": "Task failed"}])
    sent = []
    monkeypatch.setattr(notifiers, "telegram", lambda route, message, timeout: sent.append(message))
    Bot({"token": "test", "chat_id": "1", "on": ["approval.needed"]}, Plans()).watch()
    assert [m["kinds"] for m in sent] == [["approval.needed"]]


def test_delivery_failure_retries_next_poll(monkeypatch):
    record = {"notification_id": "retry", "kind": "decision.needed", "title": "Answer needed"}
    monkeypatch.setattr(chat_notifications, "snapshot", lambda *_: [record])
    calls = []

    def send(*_):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("offline")

    monkeypatch.setattr(notifiers, "telegram", send)
    bot = Bot({"token": "test", "chat_id": "1"}, Plans())
    bot.watch()
    bot.next_watch = 0
    bot.watch()
    assert len(calls) == 2
