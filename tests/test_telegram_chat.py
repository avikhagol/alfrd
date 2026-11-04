"""Real Studio chat service, fake agents and mocked Telegram HTTP API."""

import pytest
from test_chat_orchestration import fake, plan, project, result, run, task  # noqa: F401
from test_telegram_plugin import bot, callback, message, sent, texts  # noqa: F401

from alfrd import chat
from alfrd.runtime import scheduler


@pytest.fixture
def chat_bot(bot, project, monkeypatch, tmp_path):
    b, api, logs, module = bot
    b.plans.items = {"p": {"name": "Chat project", "root": str(project)}}
    b.state = tmp_path / "telegram-state.json"
    spawned = []
    monkeypatch.setattr(scheduler, "spawn_runner", lambda folder: spawned.append(folder.id))
    return b, api, project, spawned, module


def tap(b, api, label):
    rows = sent(api)[-1]["reply_markup"]["inline_keyboard"]
    data = next(button["callback_data"] for row in rows for button in row if label in button["text"])
    callback(b, data)
    return data


def test_new_creates_activates_and_persists(chat_bot):
    b, api, root, spawned, module = chat_bot
    message(b, "/new")
    sid = b.active_chat["session"]
    assert chat.session(root, sid).load()["title"] == "New chat"
    assert b.selected == "p" and not spawned
    assert "Active chat:" in texts(api)[-1]
    again = module.Bot(b.route, b.plans, state=b.state)
    assert again.active_chat == b.active_chat
    assert again.chat_cursors == b.chat_cursors


def test_new_project_picker_and_single_use(chat_bot):
    b, api, root, _, _ = chat_bot
    b.plans.items["q"] = {"name": "Other project", "root": str(root)}
    message(b, "/new")
    assert b.active_chat is None
    data = tap(b, api, "Other project")
    assert b.active_chat["project"] == "q"
    callback(b, data)
    assert "expired" in texts(api)[-1]
    assert len(chat.list_sessions(root)) == 1


@pytest.mark.parametrize("command", ["/resume-chat", "/resume_chat", "/resume_chat@my_bot"])
def test_recent_session_selection_and_pagination(chat_bot, command):
    b, api, root, _, _ = chat_bot
    sessions = [chat.create_session(root, f"Session {i}") for i in range(7)]
    for i, item in enumerate(sessions):
        store = chat.session(root, item["id"])
        store.add_message("user", "Old prompt", at=f"2026-10-{i + 1:02d}T12:00:00Z")
    message(b, command)
    keyboard = sent(api)[-1]["reply_markup"]["inline_keyboard"]
    assert len(keyboard) == 6 and "Session 6" in keyboard[0][0]["text"]
    assert "2026-10-07" in keyboard[0][0]["text"] and "finished" in keyboard[0][0]["text"]
    tap(b, api, "Next")
    assert "page 2" in texts(api)[-1]
    tap(b, api, "Session 0")
    assert b.active_chat == {"project": "p", "session": sessions[0]["id"]}
    count = len(sent(api))
    b.relay_chat()
    assert len(sent(api)) == count  # don't replay history on first activation


def test_prompt_uses_studio_orchestrator_and_relays_results(chat_bot):
    b, api, root, spawned, _ = chat_bot
    fake(root, plan=plan(task("build"), message="I will build it."), build=result(summary="Built it."))
    message(b, "/new")
    sid = b.active_chat["session"]
    # Plain text remains a prompt even if a pipeline picker was left open.
    b.pick_project()
    update = {"message": {"chat": {"id": 42}, "message_id": 17, "text": "Build this please"}}
    b.handle(update)
    assert spawned == [f"chat-{sid}"]
    store = chat.session(root, sid)
    assert store.messages()[0]["text"] == "Build this please"
    assert store.messages()[0]["client_message_id"] == "telegram.42.17"
    assert "Accepted for chat" in texts(api)[-1]
    b.handle(update)
    assert len(store.requests()) == 1  # HTTP/update redelivery is idempotent
    state = run(root, sid)
    assert state["requests"][0]["status"] == "done"
    b.relay_chat()
    assert any("I will build it." in text for text in texts(api))
    assert any("Built it." in text for text in texts(api))
    count = len(sent(api))
    b.relay_chat()
    assert len(sent(api)) == count


def test_reply_chunking_and_restart_switch_delivery(chat_bot):
    b, api, root, _, module = chat_bot
    message(b, "/new")
    old = b.active_chat["session"]
    message(b, "/new")
    answer = "😀<&>" * 3000
    chat.session(root, old).add_message("assistant", answer)
    again = module.Bot(b.route, b.plans, state=b.state)
    before = len(sent(api))
    again.relay_chat()
    parts = sent(api)[before:]
    assert len(parts) > 3
    assert all(len(p["text"].encode("utf-16-le")) // 2 <= 4096 for p in parts)
    assert all("parse_mode" not in p for p in parts)
    assert "".join(p["text"] for p in parts).endswith(answer)
    before = len(sent(api))
    module.Bot(b.route, b.plans, state=b.state).relay_chat()
    assert len(sent(api)) == before


def test_status_menu_callbacks_tasks_and_completed_logs(chat_bot):
    b, api, root, _, _ = chat_bot
    message(b, "/status")
    assert "Status menu" in texts(api)[-1]
    tap(b, api, "Active chat status")
    assert "No active chat" in texts(api)[-1]
    fake(root, plan=plan(task("build"), message="Plan ready"), build=result())
    message(b, "/new")
    message(b, "Build")
    run(root, b.active_chat["session"])
    message(b, "/status")
    tap(b, api, "Active chat status")
    assert "Plan: finished" in texts(api)[-1] and "done" in texts(api)[-1]
    message(b, "/status")
    tap(b, api, "Chat tasks")
    assert any("build" in t and "done" in t for t in texts(api))
    tap(b, api, "Logs:")
    assert "Could not" not in texts(api)[-1]
    message(b, "/status")
    tap(b, api, "Chat logs")
    tap(b, api, "Logs:")
    assert "No task logs" not in texts(api)[-1]
    message(b, "/status")
    tap(b, api, "Switch chat")
    assert "Recent chat sessions" in texts(api)[-1]
    message(b, "/status new")
    assert "choose Pipeline" in texts(api)[-1] and b.picker is None
    message(b, "/help")
    assert "/status new" not in texts(api)[-1] and "/resume_chat" in texts(api)[-1]
    b.announce()
    commands = api.calls[-1]["body"]["commands"]
    assert {"new", "resume_chat", "status"} <= {c["command"] for c in commands}


def test_unauthorized_session_commands_prompts_and_callbacks(chat_bot):
    b, api, root, spawned, _ = chat_bot
    message(b, "/new", chat=99)
    assert not chat.list_sessions(root) and not api.calls
    message(b, "/new")
    message(b, "/status")
    data = sent(api)[-1]["reply_markup"]["inline_keyboard"][-1][0]["callback_data"]
    before = len(api.calls)
    for text in ("/new", "/resume_chat", "/resume-chat", "/status", "Delete everything"):
        message(b, text, chat=99)
    callback(b, data, chat=99)
    assert len(api.calls) == before and not spawned
    assert len(chat.list_sessions(root)) == 1
    assert not chat.session(root, b.active_chat["session"]).messages()


def test_stale_callback_and_missing_session_do_not_leak(chat_bot):
    b, api, root, _, _ = chat_bot
    message(b, "/status")
    old = sent(api)[-1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    message(b, "/status")
    callback(b, old)
    assert "expired" in texts(api)[-1]
    message(b, "/new")
    chat.delete_session(root, b.active_chat["session"])
    message(b, "Hello")
    assert "Check it in Studio" in texts(api)[-1]


def test_status_pipeline_callbacks(bot):
    b, api, _, _ = bot
    message(b, "/status")
    tap(b, api, "Pipeline / targets")
    assert "Select targets" in texts(api)[-1]
    callback(b, "pick:1")
    message(b, "/status")
    tap(b, api, "Remembered pipeline")
    assert "T1" in texts(api)[-1]
    message(b, "/status")
    tap(b, api, "Pipeline logs")
    callback(b, "pick:1")
    assert "pick a step" in texts(api)[-1]


def test_polling_relay_retries_failed_delivery(chat_bot, monkeypatch):
    from alfrd import notifiers

    b, api, root, _, _ = chat_bot
    message(b, "/new")
    sid = b.active_chat["session"]
    chat.session(root, sid).add_message("assistant", "A reply after the prompt returned.")
    before = dict(b.chat_cursors)
    send = b.text_parts

    def unavailable(text):
        raise notifiers.TelegramError("temporary network failure")

    monkeypatch.setattr(b, "text_parts", unavailable)
    monkeypatch.setattr(b, "watch", lambda: None)
    monkeypatch.setattr(b, "flush", lambda: None)
    b.checked_watch()
    assert b.chat_cursors == before
    monkeypatch.setattr(b, "text_parts", send)
    b.checked_watch()
    assert "A reply after the prompt returned." in texts(api)[-1]
    assert b.chat_cursors != before


def mode_message(b, text, sender=7, destination=None, mid=70):
    b.handle({"message": {"chat": {"id": destination or b.chat}, "from": {"id": sender},
                          "message_id": mid, "text": text}})


@pytest.mark.parametrize("sender", [None, 8, -100])
def test_chatmode_only_configured_superusers_can_toggle(chat_bot, sender):
    b, api, root, spawned, _ = chat_bot
    b.route["admin_chat_ids"] = "7, 9"
    mode_message(b, "/chatmode on", sender)
    assert "Only a configured superuser" in texts(api)[-1]
    assert b.chat_mode is None and not b.state.exists()
    mode_message(b, "/chatmode on", 7)
    assert b.chat_mode is True
    before = b.state.read_bytes()
    mode_message(b, "/chatmode off", sender)
    assert b.chat_mode is True and b.state.read_bytes() == before
    assert not spawned and not chat.list_sessions(root)


def test_chatmode_deny_by_default_and_ignore_other_chats(chat_bot):
    b, api, _, _, _ = chat_bot
    mode_message(b, "/chatmode on")
    assert "Only a configured superuser" in texts(api)[-1]
    b.route["admin_chat_ids"] = "7"
    before = len(api.calls)
    mode_message(b, "/chatmode on", destination=99)
    assert len(api.calls) == before and b.chat_mode is None


def test_chatmode_on_creates_request_for_group_member_and_relays(chat_bot):
    b, api, root, spawned, module = chat_bot
    b.chat = "-100"
    b.route.update(chat_id=b.chat, admin_chat_ids="7")
    fake(root, plan=plan(task("build"), message="Here is the plan."), build=result(summary="Task result."))
    mode_message(b, "/chatmode@my_bot on")
    # Any member of the already-authorized chat can request work; only 7 can toggle.
    mode_message(b, "Build please", sender=8, mid=71)
    sid = b.active_chat["session"]
    store = chat.session(root, sid)
    assert store.messages()[0]["client_message_id"] == "telegram.-100.71"
    assert spawned == [f"chat-{sid}"]
    mode_message(b, "Build please", sender=8, mid=71)
    assert len(store.requests()) == 1
    run(root, sid)
    again = module.Bot(b.route, b.plans, state=b.state)
    assert again.chat_mode is True
    again.checked_watch()
    assert any("Here is the plan." in text for text in texts(api))
    assert any("Task result." in text for text in texts(api))
    assert all(str(item["chat_id"]) == "-100" for item in sent(api))
    mode_message(b, "/chatmode off", sender=8)
    assert b.chat_mode is True


def test_chatmode_off_restores_picker_and_retains_reply_delivery(chat_bot):
    b, api, root, spawned, module = chat_bot
    b.route["admin_chat_ids"] = "7"
    mode_message(b, "/chatmode on")
    message(b, "/new")
    sid = b.active_chat["session"]
    mode_message(b, "/chatmode off")
    assert b.chat_mode is False
    message(b, "Do not run this")
    assert not spawned and not chat.session(root, sid).messages()
    b.pick_project()
    message(b, "1")
    assert b.tracked  # legacy numeric pipeline selection
    again = module.Bot(b.route, b.plans, state=b.state)
    assert again.chat_mode is False and again.active_chat == b.active_chat
    chat.session(root, sid).add_message("assistant", "Previously requested work finished.")
    again.relay_chat()
    assert "Previously requested work finished." in texts(api)[-1]
    message(again, "/chatmode")
    assert "Chat mode: off" in texts(api)[-1]


def test_chatmode_invalid_args_and_failed_persistence(chat_bot, monkeypatch):
    b, api, _, _, _ = chat_bot
    b.route["admin_chat_ids"] = "7"
    mode_message(b, "/chatmode banana")
    assert "Use /chatmode" in texts(api)[-1] and b.chat_mode is None
    def failed(**kwargs):
        raise OSError("private filesystem details")
    monkeypatch.setattr(b, "save_state", failed)
    mode_message(b, "/chatmode on")
    assert b.chat_mode is None and "No change made" in texts(api)[-1]
    assert "private filesystem" not in texts(api)[-1]


def test_chatmode_ambiguous_project_does_not_drop_prompt_silently(chat_bot):
    b, api, root, spawned, _ = chat_bot
    b.route["admin_chat_ids"] = "7"
    b.plans.items["q"] = {"name": "Other", "root": str(root)}
    mode_message(b, "/chatmode on")
    message(b, "Build")
    assert "No project selected" in texts(api)[-1]
    assert not chat.list_sessions(root) and not spawned
