"""Telegram scheduling against the real Studio scheduling backend."""

from datetime import datetime, timedelta, timezone

import pytest
from test_telegram_chat import bot, chat_bot, mode_message, project  # noqa: F401
from test_telegram_plugin import sent, texts
from test_chat_orchestration import fake, plan, result, run, task

from alfrd import chat


@pytest.fixture
def schedule_bot(chat_bot):
    b, api, root, spawned, module = chat_bot
    b.route["admin_chat_ids"] = "7, 9"
    return b, api, root, spawned, module


@pytest.mark.parametrize("value,expected", [
    ("in 2h hello\nworld", "2027-01-01T14:00:00+00:00"),
    ("in 30m hello", "2027-01-01T12:30:00+00:00"),
    ("in 1d hello", "2027-01-02T12:00:00+00:00"),
    ("2027-01-02 09:00 hello", "2027-01-02T09:00:00+02:00"),
    ("2027-07-02 09:00 hello", "2027-07-02T09:00:00+03:00"),
    ("2027-01-02T09:00Z hello", "2027-01-02T09:00:00+00:00"),
    ("2027-01-02 09:00-05:00 hello", "2027-01-02T09:00:00-05:00"),
    ("2027-01-02 09:00 Europe/London hello", "2027-01-02T09:00:00+00:00"),
])
def test_parse_schedule(schedule_bot, value, expected):
    from alfrd_telegram.schedule_control import parse_schedule
    at, zone, text = parse_schedule(value, now=datetime(2027, 1, 1, 12, tzinfo=timezone.utc))
    assert at == expected
    assert text == ("hello\nworld" if "\n" in value else "hello")
    assert zone == ("Europe/London" if "Europe/London" in value else "Europe/Athens")


@pytest.mark.parametrize("value", ["", "in 0h hello", "in -2h hello", "in 2h", "tomorrow hi",
    "2026-01-01 09:00 hi", "2027-02-30 09:00 hi", "2027-01-02 25:00 hi",
    "2027-01-02 09:00 Unknown/Zone hi", "2027-03-28 03:30 hi", "2027-10-31 03:30 hi",
    "in 99999999999999999999999999d hi", "2027-01-02 09:00 UTC"])
def test_parse_rejects_invalid_past_and_dst(schedule_bot, value):
    from alfrd_telegram.schedule_control import parse_schedule
    with pytest.raises(ValueError):
        parse_schedule(value, now=datetime(2027, 1, 1, 12, tzinfo=timezone.utc))


@pytest.mark.parametrize("sender", [None, 8, -100])
def test_auth_fails_closed(schedule_bot, sender):
    b, api, root, spawned, _ = schedule_bot
    for command in ("/schedule in 2h build", "/scheduled", "/unschedule sch_" + "a" * 32):
        mode_message(b, command, sender=sender)
        assert "Only a configured superuser" in texts(api)[-1]
    assert not chat.list_sessions(root) and not spawned
    b.route["admin_chat_ids"] = ""
    mode_message(b, "/schedule in 2h build")
    assert not chat.list_sessions(root)


def test_wrong_chat_and_invalid_input_create_nothing(schedule_bot):
    b, api, root, spawned, _ = schedule_bot
    mode_message(b, "/schedule in 2h build", destination=99)
    assert not api.calls
    for value in ("2020-01-01 09:00 hi", "in 0h hi", "nonsense"):
        mode_message(b, "/schedule " + value)
    assert "Use /schedule" in texts(api)[-1]
    assert not chat.list_sessions(root) and not spawned


def test_backend_queue_replay_and_results_after_restart(schedule_bot):
    b, api, root, spawned, module = schedule_bot
    fake(root, plan=plan(task("build"), message="Scheduled plan ready"), build=result(summary="Scheduled result"))
    mode_message(b, "/schedule@my_bot in 2h Build this\nKeep formatting", mid=101)
    assert b.active_chat is None and not spawned
    sid = chat.list_sessions(root)[0]["id"]
    state = chat.schedule_state(root, sid)  # Same queue contract that Studio renders.
    batch = state["schedules"][0]
    assert batch["items"][0]["text"] == "Build this\nKeep formatting"
    assert batch["client_schedule_id"] == "telegram.42.7.101"
    assert not chat.session(root, sid).requests()
    mode_message(b, "/schedule@my_bot in 2h Build this\nKeep formatting", mid=101)
    assert len(chat.list_sessions(root)) == 1
    assert len(chat.schedule_state(root, sid)["schedules"][0]["items"]) == 1
    due = datetime.fromisoformat(batch["run_at"].replace("Z", "+00:00")).timestamp() + 1
    chat.dispatch_due_schedules(root, sid, now=due)
    assert spawned == [f"chat-{sid}"]
    run(root, sid)
    again = module.Bot(b.route, b.plans, state=b.state)
    again.checked_watch()
    assert any("Scheduled plan ready" in text for text in texts(api))
    assert any("Scheduled result" in text for text in texts(api))
    assert all(str(c["chat_id"]) == "42" for c in sent(api))
    mode_message(again, "/scheduled")
    assert "No pending" in texts(api)[-1]


def test_list_cancel_scoped_to_owner_and_chat(schedule_bot):
    b, api, root, _, module = schedule_bot
    mode_message(b, "/schedule in 2h Mine", mid=102)
    sid = chat.list_sessions(root)[0]["id"]
    bid = chat.schedule_state(root, sid)["schedules"][0]["id"]
    mode_message(b, "/scheduled", sender=9)
    assert "No pending" in texts(api)[-1]
    mode_message(b, "/unschedule " + bid, sender=9)
    assert "belongs to you" in texts(api)[-1]
    other = module.Bot({**b.route, "chat_id": "99"}, b.plans)
    mode_message(other, "/unschedule " + bid)
    assert "belongs to you" in texts(api)[-1]
    again = module.Bot(b.route, b.plans, state=b.state)
    mode_message(again, "/scheduled")
    assert bid in texts(api)[-1] and "Mine" in texts(api)[-1] and "Europe/Athens" in texts(api)[-1]
    mode_message(again, "/unschedule " + bid)
    assert "Cancelled" in texts(api)[-1]
    assert chat.schedule_state(root, sid)["last_schedule"]["status"] == "cancelled"
    mode_message(again, "/scheduled")
    assert "No pending" in texts(api)[-1]


def test_absolute_schedule_and_ambiguous_project(schedule_bot):
    b, api, root, _, _ = schedule_bot
    b.plans.items["q"] = {"name": "Other", "root": str(root)}
    mode_message(b, "/schedule in 2h build")
    assert "No project selected" in texts(api)[-1] and not chat.list_sessions(root)
    b.selected = "p"
    at = (datetime.now(timezone.utc) + timedelta(days=1)).replace(microsecond=0).isoformat()
    mode_message(b, f"/schedule {at} build", mid=103)
    sid = chat.list_sessions(root)[0]["id"]
    assert chat.schedule_state(root, sid)["schedules"][0]["run_at"] == at.replace("+00:00", "Z")


def test_schedule_does_not_publish_if_reply_route_cannot_be_saved(schedule_bot, monkeypatch):
    b, api, root, spawned, _ = schedule_bot
    def fail(**kwargs):
        raise OSError("secret path")
    monkeypatch.setattr(b, "save_state", fail)
    mode_message(b, "/schedule in 2h hello")
    assert "secret path" not in texts(api)[-1]
    assert not spawned
    assert all(not chat.schedule_state(root, s["id"])["schedules"] for s in chat.list_sessions(root))
