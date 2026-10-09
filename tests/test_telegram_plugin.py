"""Exercise the reference bot through the shared HTTP helper; no network."""

import importlib
import json
from pathlib import Path

import pytest
from test_plan_status_api import project  # noqa: F401 - scratch project fixture
from test_telegram_notifier import ROUTE, TOKEN, FakeAPI
from typer.testing import CliRunner

from alfrd import notifiers, notify

STEPS = ["s1", "s2", "s3", "s4"]
T1 = {"row": "r1", "target": "T1", "cells": {"s1": "done", "s2": "done", "s3": "running", "s4": "todo"},
      "detail": {"s1": {"started_at": "2026-10-09T10:00:00Z", "finished_at": "2026-10-09T10:00:42Z", "duration_s": 42.0},
                 "s2": {"started_at": "2026-10-09T10:01:00Z", "finished_at": "2026-10-09T11:03:05Z", "duration_s": 3725.0},
                 "s3": {"started_at": "2026-10-09T11:05:00Z"}}}
T2 = {"row": "r2", "target": "T2", "cells": {"s1": "done", "s2": "failed", "s3": "blocked", "s4": "skip"},
      "detail": {"s1": {"started_at": "2026-10-09T09:00:00Z", "finished_at": "2026-10-09T09:00:10Z", "duration_s": 10.0},
                 "s2": {"started_at": "2026-10-09T09:00:10Z", "finished_at": "2026-10-09T09:00:50Z", "duration_s": 40.0}}}
T0 = {"row": "r0", "target": "T0", "cells": dict.fromkeys(STEPS, "todo"), "detail": {}}
T1_CARD = """Project - T1 - s3
-------------------------------

finished:
 - s1 (42s)
 - s2 (1h 02m)

running:
 - s3 (2m 05s)

next:
 - s4"""


class FakePlans:
    def __init__(self):
        self.actions = []
        self.roots = []
        self.error = None
        self.items = {"p": {"name": "Project", "root": "/fake/project"}}
        self.active = {"/fake/project": "2026-10-09T11:05:00Z"}
        self.rows = [T0, T2, T1]  # CSV order; T1 is the most recently active

    def projects(self):
        return self.items

    def activity(self, root):
        return self.active.get(root, "")

    def grid(self, root):
        self.roots.append(root)
        if self.error:
            raise self.error
        return {"plan": {"id": "plan-1", "status": "running", "steps": STEPS},
                "running": [{"row": "r1", "step": "s3", "elapsed_s": 125.0}], "rows": self.rows}

    def status(self, root):
        self.roots.append(root)
        if self.error:
            raise self.error
        return {"plan": {"id": "plan-1", "status": "running"},
                "summary": "1 running; 2 done", "counts": {"running": 1, "done": 2}}

    def runs(self, root):
        return [{"id": "plan-1", "status": "running"}]

    def log(self, root, target, step):
        assert (target, step) == ("target", "step")
        return [f"line {i}" for i in range(40)] + [TOKEN]

    def control(self, root, plan, action):
        if self.error:
            raise self.error
        self.actions.append((root, plan, action))


@pytest.fixture
def bot(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "examples/plugins/alfrd-telegram"))
    module = importlib.import_module("alfrd_telegram")
    api = FakeAPI()
    monkeypatch.setattr(notifiers.urllib.request, "build_opener", api)
    logs = []
    instance = module.Bot(ROUTE, FakePlans(), log=logs.append)
    return instance, api, logs, module


def message(bot, text, chat=42):
    bot.handle({"message": {"chat": {"id": chat}, "text": text}})


def callback(bot, data, chat=42):
    bot.handle({"callback_query": {"id": "query-1", "data": data,
                                   "message": {"chat": {"id": chat}}}})


def sent(api):
    return [c["body"] for c in api.calls if c["url"].endswith("/sendMessage")]


def test_plugin_cli_mount_and_route_selection(bot, monkeypatch, tmp_path):
    _, _, _, module = bot
    config = tmp_path / "config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    path = notify.user_config_file()
    path.parent.mkdir(parents=True)
    route2 = {**ROUTE, "token": "123456:FAKE-second", "chat_id": "99"}
    path.write_text(json.dumps({"routes": [ROUTE, route2]}))
    seen = []
    monkeypatch.setattr(module.Bot, "run", lambda self: seen.append((self.token, self.chat)))
    import typer
    app = typer.Typer()
    from alfrd import extensions
    from alfrd.cli import mount_plugin_commands

    record = extensions.Record("telegram", "entry_point", status="ok", plugin=module.plugin)
    monkeypatch.setattr(extensions, "load", lambda: [record])
    mount_plugin_commands(app)
    result = CliRunner().invoke(app, ["telegram", "run", "--db", str(tmp_path / "runtime.sqlite")])
    assert result.exit_code == 0, result.output
    assert seen == [(TOKEN, "42")]
    assert TOKEN not in result.output
    monkeypatch.setattr(module.Bot, "run", lambda self: 401)
    result = CliRunner().invoke(app, ["telegram", "run", "--db", str(tmp_path / "runtime.sqlite")])
    assert result.exit_code == 2, result.output  # bad settings: Settings → Plugins doesn't restart it
    monkeypatch.setattr(module.Bot, "run", lambda self: 409)
    result = CliRunner().invoke(app, ["telegram", "run", "--db", str(tmp_path / "runtime.sqlite")])
    assert result.exit_code == 1, result.output
    path.write_text(json.dumps({"routes": [{**ROUTE, "chat_id": "@channel"}]}))
    result = CliRunner().invoke(app, ["telegram", "run", "--db", str(tmp_path / "runtime.sqlite")])
    assert result.exit_code == 2 and "numeric chat id" in result.output
    assert "@channel" not in result.output and TOKEN not in result.output
    path.write_text("{}")
    result = CliRunner().invoke(app, ["telegram", "run"])
    assert result.exit_code == 2 and "No Telegram settings" in result.output
    assert module.plugin.kinds == ["cli", "settings", "service"]


def test_other_chats_are_silent_even_with_valid_confirmation(bot):
    b, api, _, _ = bot
    message(b, "/pause")
    key = sent(api)[-1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    api.calls.clear()
    message(b, "/status", chat=99)
    callback(b, key, chat=99)
    assert api.calls == [] and b.plans.actions == []
    callback(b, key)
    assert b.plans.actions == [("/fake/project", "plan-1", "pause")]


@pytest.mark.parametrize("action", ["pause", "resume"])
@pytest.mark.parametrize("choice", ["yes", "no"])
def test_yes_no_single_use_and_acknowledgment(bot, action, choice):
    b, api, _, _ = bot
    message(b, f"/{action}")
    assert not b.plans.actions
    buttons = sent(api)[-1]["reply_markup"]["inline_keyboard"][0]
    assert [v["text"] for v in buttons] == ["Yes", "No"]
    key = buttons[0]["callback_data"].split(":")[1]
    callback(b, f"{choice}:{key}")
    expected = [("/fake/project", "plan-1", action)] if choice == "yes" else []
    assert b.plans.actions == expected
    callback(b, f"yes:{key}")
    callback(b, "unknown")
    assert b.plans.actions == expected
    assert len([c for c in api.calls if c["url"].endswith("/answerCallbackQuery")]) == 3


def test_expired_confirmation_and_project_switch(bot, monkeypatch):
    b, api, _, _ = bot
    b.plans.items["q"] = {"name": "Other", "root": "/fake/other"}
    message(b, "/status p")
    message(b, "/pause")
    key = sent(api)[-1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    message(b, "/status q")
    callback(b, key)
    assert b.plans.actions == [("/fake/project", "plan-1", "pause")]
    message(b, "/resume")
    key = sent(api)[-1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    import alfrd_telegram.bot as module
    monkeypatch.setattr(module.time, "monotonic", lambda: float("inf"))
    callback(b, key)
    assert len(b.plans.actions) == 1 and "expired" in sent(api)[-1]["text"]


def test_read_commands_project_defaults_and_plain_text(bot):
    b, api, _, _ = bot
    for command in ("/help", "/status", "1", "/runs", "/log target step"):
        message(b, command)
    texts = [p["text"] for p in sent(api)]
    assert "ALFRD commands" in texts[0]
    assert texts[1].startswith("Select targets in Project (1–3 of 3)")  # one project: straight to targets
    assert texts[2] == T1_CARD
    assert "plan-1 · running" in texts[3]
    lines = texts[4].split("\n\n")[1].splitlines()
    assert len(lines) == 30 and lines[-1] == "<token>"
    assert all("parse_mode" not in p for p in sent(api))
    b.plans.items["q"] = {"name": "Other", "root": "/fake/other"}
    message(b, "/status q")
    assert b.selected == "q" and b.plans.roots[-1] == "/fake/other"
    b.selected = None
    message(b, "/runs")
    assert "/status new" in sent(api)[-1]["text"]


def test_poll_offset_and_long_timeout(bot):
    b, api, _, _ = bot
    api.replies = [(200, {"ok": True, "result": [
        {"update_id": 8, "message": {"chat": {"id": 99}, "text": "/help"}}]}),
        (200, {"ok": True, "result": []})]
    b.poll()
    b.poll()
    assert b.offset == 9
    assert api.calls[0]["body"] == {"offset": 0, "timeout": 50,
                                    "allowed_updates": ["message", "callback_query"]}
    assert api.calls[0]["timeout"] == 60 and api.calls[1]["body"]["offset"] == 9


def test_errors_and_replies_never_disclose_token(bot, monkeypatch):
    b, api, logs, _ = bot
    b.plans.error = RuntimeError(f"private URL /bot{TOKEN}/")
    message(b, "/status")
    assert "Could not read" in sent(api)[-1]["text"]
    b.plans.error = None
    message(b, "/pause")
    key = sent(api)[-1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    b.plans.error = ValueError(f"bad {TOKEN}")
    callback(b, key)
    assert TOKEN not in json.dumps(sent(api))
    api.replies = [(401, {"ok": False, "description": f"Unauthorized {TOKEN}"})]
    b.run()
    assert logs == ["Telegram API unavailable (HTTP 401)."]
    assert TOKEN not in "\n".join(logs)


def test_transient_poll_error_retries_without_request_url(bot, monkeypatch):
    b, api, logs, _ = bot
    import alfrd_telegram.bot as module
    sleeps = []
    monkeypatch.setattr(module.time, "sleep", sleeps.append)
    api.replies = [(500, {"ok": False, "description": TOKEN}), (401, {"ok": False})]
    b.run()
    assert sleeps == [3] and len(api.calls) == 2
    assert TOKEN not in "\n".join(logs) and "https://" not in "\n".join(logs)


def test_real_plan_backend_status_runs_log_and_control(bot, project, tmp_path, monkeypatch):  # noqa: F811 - imported pytest fixture
    from test_plan_status_api import _plan

    from alfrd.runtime import RuntimeService, RuntimeStore, scheduler

    _, api, _, module = bot
    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    service = RuntimeService(store)
    service.create_project("proj", project)
    plans = module.Plans(service)
    b = module.Bot(ROUTE, plans)
    _plan(project, targets=("T1",))
    folder = scheduler.create_plan(project)
    spawned = []
    monkeypatch.setattr(scheduler, "spawn_runner", lambda f: spawned.append(f.id))
    message(b, "/status")  # one project with one target: its card straight away
    assert sent(api)[-1]["text"] == ("proj - T1 - preprocess_fitsidi (next)\n" + "-" * 31
                                     + "\n\nnext:\n - preprocess_fitsidi\n - fits_to_ms\n - avica_avg")
    message(b, "/runs")
    assert folder.id in sent(api)[-1]["text"]
    message(b, "/pause")
    key = sent(api)[-1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    callback(b, key)
    assert folder.control() == "pause" and folder.load()["status"] == "paused"
    message(b, "/resume")
    key = sent(api)[-1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    callback(b, key)
    assert folder.control() == "run" and spawned == [folder.id]
    scheduler.Runner(project, folder.id).run()
    message(b, "/log T1 fits_to_ms")
    assert "proj · T1 / fits_to_ms" in sent(api)[-1]["text"]
    assert "Could not" not in sent(api)[-1]["text"]
    message(b, "/status")
    card = sent(api)[-1]["text"]
    assert card.startswith("proj - T1 - done") and "finished:\n - preprocess_fitsidi (" in card and "running:" not in card
    assert plans.activity(project) >= "2026"


def test_replaced_confirmation_and_command_help(bot):
    b, api, _, _ = bot
    message(b, "/pause")
    old = sent(api)[-1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    message(b, "/resume")
    new = sent(api)[-1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    callback(b, old)
    assert not b.plans.actions
    callback(b, new)
    assert b.plans.actions == [("/fake/project", "plan-1", "resume")]
    for command in ("/log target", "/pause unexpected", "/status p extra", "/cancel"):
        message(b, command)
    assert len(b.plans.actions) == 1
    assert "Unknown command" in sent(api)[-1]["text"]
    message(b, "/help@FakeBot")
    assert "ALFRD commands" in sent(api)[-1]["text"]


def test_rejected_reply_or_odd_update_does_not_stop_polling(bot):
    b, api, logs, _ = bot
    api.replies = [
        (200, {"ok": True, "result": [
            {"update_id": 5, "callback_query": {"id": "old", "data": "yes:x", "message": {"chat": {"id": 42}}}},
            {"update_id": 6, "callback_query": {"data": "yes:x", "message": {"chat": {"id": 42}}}},
            {"update_id": 7, "message": {"chat": {"id": 42}, "text": "/help"}}]}),
        (400, {"ok": False, "description": f"query is too old {TOKEN}"}),  # stale answerCallbackQuery
        (200, {"ok": True, "result": {}}),  # /help reply
        (401, {"ok": False}),  # next getUpdates is fatal
    ]
    assert b.run() == 401
    assert b.offset == 8
    assert "ALFRD commands" in sent(api)[-1]["text"]
    assert logs == ["Telegram reply failed (HTTP 400).", "Update skipped (KeyError).",
                    "Telegram API unavailable (HTTP 401)."]


def test_settings_win_over_notify_json_and_check_sends_nothing(bot, monkeypatch):
    _, api, _, module = bot
    from alfrd.extensions import settings

    path = notify.user_config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"routes": [ROUTE]}))
    route, where = module.configured_route()
    assert where == "notify.json" and route["token"] == TOKEN
    other = "999:SETTINGS-token-abcdefghijklmnopqrstuv"
    settings.save(module.plugin, {"token": other, "chat_id": "-100123"})
    route, where = module.configured_route()
    assert where == "settings" and route["token"] == other and route["chat_id"] == "-100123"
    api.replies = [(200, {"ok": True, "result": {"username": "alfrd_bot"}}),
                   (200, {"ok": True, "result": {"type": "group"}})]
    message = module.check(settings.values("telegram"))
    assert message.startswith("Connected as @alfrd_bot to a group chat.") and "private chat" in message
    assert [c["url"].rsplit("/", 1)[1] for c in api.calls] == ["getMe", "getChat"]
    api.replies = [(401, {"ok": False, "description": f"Unauthorized {other}"})]
    with pytest.raises(ValueError) as bad:
        module.check(settings.values("telegram"))
    assert "HTTP 401" in str(bad.value) and other not in str(bad.value)
    with pytest.raises(ValueError, match="not in the expected format"):
        settings.save(module.plugin, {"chat_id": "@channel"})


def test_one_bot_per_token(bot):
    _, _, _, module = bot
    first = module._lock(TOKEN)
    assert first is not None
    assert module._lock(TOKEN) is None
    assert module._lock("other:token") is not None
    first.close()
    assert module._lock(TOKEN) is not None


def test_network_errors_name_the_cause_without_secrets(bot):
    instance, _, _, _ = bot
    error = notifiers.TelegramError(f"telegram sendMessage: URLError: timed out for {TOKEN} in chat 42")
    assert instance.reason(error) == "sendMessage: URLError: timed out for <token> in chat <chat>"
    assert instance.reason(notifiers.TelegramError("x", 409)) == "HTTP 409"


def texts(api):
    return [p["text"] for p in sent(api)]


def test_status_picks_project_then_targets_and_remembers_them(bot, tmp_path):
    b, api, _, module = bot
    state = tmp_path / "status.json"
    b = module.Bot(ROUTE, b.plans, state=state)
    b.plans.items["q"] = {"name": "Quiet", "root": "/fake/quiet"}  # no runs: listed last
    message(b, "/status")
    picker = sent(api)[-1]
    assert picker["text"] == ("Select project (1–2 of 2)\n\n1. Project · " + module.view.ago("2026-10-09T11:05:00Z")
                              + "\n2. Quiet · no runs yet\n\nTap a number or reply with it.")
    assert picker["reply_markup"] == {"inline_keyboard": [[{"text": "1", "callback_data": "pick:1"},
                                                            {"text": "2", "callback_data": "pick:2"}]]}
    callback(b, "pick:1")  # a tapped button answers like a typed number
    assert texts(api)[-1] == ("Select targets in Project (1–3 of 3)\n\n1. T1 · s3\n2. T2 · s2 (failed)"
                              "\n3. T0 · s1 (next)\n\nTap a number to pick one target, or reply with numbers (e.g. 1 3 or 1,3).\n"
                              "Tap All or reply all to pick every target across all pages.")
    message(b, "2,1")
    cards = texts(api)[-1]
    assert cards.startswith(T1_CARD + "\n\n")  # most recently active first, whatever the reply order
    assert cards.endswith("Project - T2 - s2 (failed)\n" + "-" * 31
                          + "\n\nfinished:\n - s1 (10s)\n\nfailed:\n - s2 (40s)\n - s3 (blocked)")
    assert json.loads(state.read_text()) == {"project": "p", "targets": ["r2", "r1"]}
    again = module.Bot(ROUTE, b.plans, state=state)  # a restarted bot remembers
    message(again, "/status last")
    assert texts(api)[-1] == cards
    message(again, "/status")
    assert texts(api)[-1] == cards


def test_status_latest_new_and_name_selection(bot):
    b, api, _, _ = bot
    b.plans.items["q"] = {"name": "Quiet", "root": "/fake/quiet"}
    message(b, "/status last")
    assert texts(api)[-2] == "Nothing remembered yet." and texts(api)[-1].startswith("Select project")
    message(b, "/status latest")
    assert texts(api)[-1] == T1_CARD and b.selected is None  # latest does not change the memory
    message(b, "/status QUI")  # any case, unique prefix
    assert b.selected == "q" and texts(api)[-1].startswith("Select targets in Quiet (1–3 of 3)")
    assert b.tracked == []  # picking a project asks for targets again
    message(b, "/status nope")
    assert "No projects match 'nope'" in texts(api)[-1]
    message(b, "/status 9")  # the target list is still open: numbers answer it
    assert texts(api)[-1] == "Pick a number from 1 to 3."
    message(b, "/status new")
    assert texts(api)[-1].startswith("Select project")
    message(b, "3")
    assert texts(api)[-1] == "Pick a number from 1 to 2."
    message(b, "hello")
    assert "Reply with a number from the list" in texts(api)[-1]
    message(b, "/status 1")
    message(b, "all")
    assert b.selected == "p" and b.tracked == ["r1", "r2", "r0"]  # most recently active first
    callback(b, "pick:1")
    assert texts(api)[-1] == "This list expired. Send /status new."
    message(b, "/status extra words")
    assert texts(api)[-1].startswith("Use /status")


def test_lists_and_cards_page_by_five(bot):
    b, api, _, _ = bot
    b.plans.rows = [{**T0, "row": f"r{i}", "target": f"T{i}"} for i in range(7)]
    message(b, "/status")
    first = sent(api)[-1]
    assert first["text"].startswith("Select targets in Project (1–5 of 7)") and first["text"].endswith(
        "Tap More ▸ or reply more for the next 5.")
    assert first["reply_markup"]["inline_keyboard"][1] == [{"text": "All", "callback_data": "pick:all"},
                                                           {"text": "More ▸", "callback_data": "pick:more"}]
    callback(b, "pick:more")
    assert texts(api)[-1].startswith("Select targets in Project (6–7 of 7)\n\n6. T5")
    message(b, "more")
    assert texts(api)[-1] == "That was the whole list."
    message(b, "7 1")
    assert b.tracked == ["r6", "r0"]
    message(b, "all")  # no list open any more
    assert texts(api)[-1] == "Unknown command. Use /help."
    message(b, "/status new")
    message(b, "all")
    page = texts(api)[-1]
    assert page.count("-" * 31) == 5 and page.endswith("Targets 1–5 of 7 · /status more for the next 5.")
    message(b, "/status more")
    assert texts(api)[-1].count("-" * 31) == 2 and texts(api)[-1].endswith("Targets 6–7 of 7.")
    message(b, "/status more")
    assert texts(api)[-1] == "That was the whole list."


def test_card_for_a_target_missing_from_the_latest_plan(bot, tmp_path):
    b, api, _, module = bot
    state = tmp_path / "status.json"
    state.write_text(json.dumps({"project": "p", "targets": ["gone", "r1"]}))
    b = module.Bot(ROUTE, b.plans, state=state)
    message(b, "/status")
    assert texts(api)[-1] == T1_CARD + "\n\nProject - gone\n" + "-" * 31 + "\nNot in the latest plan (plan-1)."
    state.write_text("not json")
    assert module.Bot(ROUTE, b.plans, state=state).selected is None


def test_elapsed_format():
    from alfrd_telegram import view

    assert [view.elapsed(s) for s in (None, 0, 59.9, 60, 3599, 3600, 90061)] == [
        "?", "0s", "59s", "1m 00s", "59m 59s", "1h 00m", "25h 01m"]
