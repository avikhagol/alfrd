"""Exercise the reference bot through the shared HTTP helper; no network."""

import html
import importlib
import json
import re
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
RULE = "━" * 16
#: Cards and lists as the user reads them (tags stripped); see test_html_markup_and_escaping for the markup.
T1_CARD = f"""📁 Project · 🎯 T1
🔄 s3
{RULE}

✅ Finished
  • s1  42s
  • s2  1h 02m

🔄 Running
  • s3  2m 05s

⏳ Next
  • s4"""
T1_HTML = f"""📁 <b>Project</b> · 🎯 <b>T1</b>
🔄 <i>s3</i>
{RULE}

✅ <b>Finished</b>
  • <code>s1</code>  <i>42s</i>
  • <code>s2</code>  <i>1h 02m</i>

🔄 <b>Running</b>
  • <code>s3</code>  <i>2m 05s</i>

⏳ <b>Next</b>
  • <code>s4</code>"""


class FakePlans:
    def __init__(self):
        self.actions = []
        self.roots = []
        self.error = None
        self.items = {"p": {"name": "Project", "root": "/fake/project"}}
        self.active = {"/fake/project": "2026-10-09T11:05:00Z"}
        self.rows = [T0, T2, T1]  # CSV order; T1 is the most recently active
        self.logs = []
        self.loop_plans, self.handoffs = {}, {}

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
        self.logs.append((target, step))
        return [f"line {i}" for i in range(40)] + [TOKEN]

    def control(self, root, plan, action):
        if self.error:
            raise self.error
        self.actions.append((root, plan, action))

    def loops(self, root):
        return [dict(p) for p in self.loop_plans.get(root, [])]

    def handoff(self, root, plan):
        return self.handoffs.get(plan)


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


def plain(text):
    """What Telegram shows for HTML ``text``."""
    return html.unescape(re.sub(r"</?(b|i|u|s|code|pre|a|blockquote)\b[^>]*>", "", text))


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
    assert [v["text"] for v in buttons] == ["✅ Yes", "✖️ No"]
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


def test_read_commands_project_defaults_and_html(bot):
    b, api, _, _ = bot
    for command in ("/help", "/status", "1", "/runs", "/log target step"):
        message(b, command)
    texts = [plain(p["text"]) for p in sent(api)]
    assert "ALFRD commands" in texts[0] and "/whoareyou" in texts[0]
    assert texts[1].startswith("🎯 Select targets in Project  (1–3 of 3)")  # one project: straight to targets
    assert texts[2] == T1_CARD
    assert "🔄 plan-1 · running" in texts[3]
    assert b.plans.logs == [("target", "step")]
    lines = texts[4].split("\n\n")[1].splitlines()
    assert len(lines) == 30 and lines[-1] == "<token>"
    assert all(p["parse_mode"] == "HTML" for p in sent(api))
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
    assert plain(sent(api)[-1]["text"]) == ("📁 proj · 🎯 T1\n⏳ preprocess_fitsidi (next)\n" + RULE
                                            + "\n\n⏳ Next\n  • preprocess_fitsidi\n  • fits_to_ms\n  • avica_avg")
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
    assert "📜 proj · 🎯 T1 / fits_to_ms" in plain(sent(api)[-1]["text"])
    assert "Could not" not in sent(api)[-1]["text"]
    message(b, "/log")  # menu: one target, so straight to its steps (newest first)
    assert plain(sent(api)[-1]["text"]).startswith("📜 Logs · T1 · pick a step  (1–3 of 3)\n\n1. avica_avg")
    message(b, "2")
    assert "📜 proj · 🎯 T1 / fits_to_ms" in plain(sent(api)[-1]["text"])
    message(b, "/status")
    card = plain(sent(api)[-1]["text"])
    assert card.startswith("📁 proj · 🎯 T1\n🏁 done") and "✅ Finished\n  • preprocess_fitsidi  " in card
    assert "Running" not in card
    assert plans.activity(project) >= "2026"
    assert plans.loops(project) == []  # not an agent loop


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
    return [plain(p["text"]) for p in sent(api)]


def test_status_picks_project_then_targets_and_remembers_them(bot, tmp_path):
    b, api, _, module = bot
    state = tmp_path / "status.json"
    b = module.Bot(ROUTE, b.plans, state=state)
    b.plans.items["q"] = {"name": "Quiet", "root": "/fake/quiet"}  # no runs: listed last
    message(b, "/status")
    picker = sent(api)[-1]
    assert plain(picker["text"]) == ("📂 Select project  (1–2 of 2)\n\n1. Project  ·  "
                                     + module.view.ago("2026-10-09T11:05:00Z")
                                     + "\n2. Quiet  ·  no runs yet\n\n💡 Tap a number or reply with it.")
    assert picker["reply_markup"] == {"inline_keyboard": [[{"text": "1", "callback_data": "pick:1"},
                                                            {"text": "2", "callback_data": "pick:2"}]]}
    callback(b, "pick:1")  # a tapped button answers like a typed number
    assert texts(api)[-1] == ("🎯 Select targets in Project  (1–3 of 3)\n\n1. T1  ·  s3\n2. T2  ·  s2 (failed)"
                              "\n3. T0  ·  s1 (next)\n\n💡 Tap a number to pick one target, or reply with numbers "
                              "(e.g. 1 3 or 1,3).\nTap All or reply all to pick every target across all pages.")
    message(b, "2,1")
    cards = texts(api)[-1]
    assert cards.startswith(T1_CARD + "\n\n")  # most recently active first, whatever the reply order
    assert cards.endswith("📁 Project · 🎯 T2\n❌ s2 (failed)\n" + RULE
                          + "\n\n✅ Finished\n  • s1  10s\n\n❌ Failed\n  • s2  40s\n  • s3  blocked")
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
    assert texts(api)[-2] == "🗂️ Nothing remembered yet." and texts(api)[-1].startswith("📂 Select project")
    message(b, "/status latest")
    assert texts(api)[-1] == T1_CARD and b.selected is None  # latest does not change the memory
    message(b, "/status QUI")  # any case, unique prefix
    assert b.selected == "q" and texts(api)[-1].startswith("🎯 Select targets in Quiet  (1–3 of 3)")
    assert b.tracked == []  # picking a project asks for targets again
    message(b, "/status nope")
    assert "No projects match 'nope'" in texts(api)[-1]
    message(b, "/status 9")  # the target list is still open: numbers answer it
    assert texts(api)[-1] == "🔢 Pick a number from 1 to 3."
    message(b, "/status new")
    assert texts(api)[-1].startswith("📂 Select project")
    message(b, "3")
    assert texts(api)[-1] == "🔢 Pick a number from 1 to 2."
    message(b, "hello")
    assert "Reply with a number from the list" in texts(api)[-1]
    message(b, "/status 1")
    message(b, "all")
    assert b.selected == "p" and b.tracked == ["r1", "r2", "r0"]  # most recently active first
    callback(b, "pick:1")
    assert texts(api)[-1] == "⌛ This list expired. Send /status new."
    message(b, "/status extra words")
    assert texts(api)[-1].startswith("ℹ️ Use /status")


def test_lists_and_cards_page_by_five(bot):
    b, api, _, _ = bot
    b.plans.rows = [{**T0, "row": f"r{i}", "target": f"T{i}"} for i in range(7)]
    message(b, "/status")
    first = sent(api)[-1]
    text = plain(first["text"])
    assert text.startswith("🎯 Select targets in Project  (1–5 of 7)") and text.endswith(
        "Tap More ▸ or reply more for the next 5.")
    assert first["reply_markup"]["inline_keyboard"][1] == [{"text": "✅ All", "callback_data": "pick:all"},
                                                           {"text": "More ▸", "callback_data": "pick:more"}]
    callback(b, "pick:more")
    assert texts(api)[-1].startswith("🎯 Select targets in Project  (6–7 of 7)\n\n6. T5")
    message(b, "more")
    assert texts(api)[-1] == "🏁 That was the whole list."
    message(b, "7 1")
    assert b.tracked == ["r6", "r0"]
    message(b, "all")  # no list open any more
    assert texts(api)[-1] == "🤔 Unknown command. Use /help."
    message(b, "/status new")
    message(b, "all")
    page = texts(api)[-1]
    assert page.count(RULE) == 5 and page.endswith("📄 Targets 1–5 of 7 · /status more for the next 5.")
    message(b, "/status more")
    assert texts(api)[-1].count(RULE) == 2 and texts(api)[-1].endswith("Targets 6–7 of 7.")
    message(b, "/status more")
    assert texts(api)[-1] == "🏁 That was the whole list."


def test_card_for_a_target_missing_from_the_latest_plan(bot, tmp_path):
    b, api, _, module = bot
    state = tmp_path / "status.json"
    state.write_text(json.dumps({"project": "p", "targets": ["gone", "r1"]}))
    b = module.Bot(ROUTE, b.plans, state=state)
    message(b, "/status")
    assert texts(api)[-1] == T1_CARD + "\n\n📁 Project · 🎯 gone\n" + RULE + "\nNot in the latest plan (plan-1)."
    state.write_text("not json")
    assert module.Bot(ROUTE, b.plans, state=state).selected is None


def test_html_markup_and_escaping(bot):
    b, api, _, _ = bot
    message(b, "/status latest")
    assert sent(api)[-1]["text"] == T1_HTML
    b.plans.items["p"]["name"] = "<b>A & B</b>"
    message(b, "/status latest")
    assert sent(api)[-1]["text"].startswith("📁 <b>&lt;b&gt;A &amp; B&lt;/b&gt;</b>")


def test_rejected_markup_is_sent_again_as_plain_text(bot):
    b, api, _, _ = bot
    api.replies = [(400, {"ok": False, "description": "can't parse entities"}), (200, {"ok": True, "result": {}})]
    b.send("<b>A &amp; B</b>")
    first, second = sent(api)
    assert first["parse_mode"] == "HTML" and "parse_mode" not in second and second["text"] == "A & B"


def test_log_menu_picks_target_then_step(bot):
    b, api, _, _ = bot
    message(b, "/log")
    menu = sent(api)[-1]
    assert plain(menu["text"]).startswith("📜 Logs · pick a target in Project  (1–2 of 2)\n\n1. T1  ·  s3\n2. T2")
    assert "T0" not in menu["text"]  # nothing started there: no log to show
    callback(b, "pick:2")
    assert plain(sent(api)[-1]["text"]).startswith(
        "📜 Logs · T2 · pick a step  (1–3 of 3)\n\n1. s3  ·  ⛔ blocked\n2. s2  ·  ❌ failed\n3. s1  ·  ✅ done")
    message(b, "1 2")
    assert texts(api)[-1] == "🔢 Pick one."
    message(b, "2")
    assert b.plans.logs == [("r2", "s2")] and b.picker is None
    text = sent(api)[-1]["text"]
    assert text.startswith("📜 <b>Project</b> · 🎯 <b>T2</b> / <code>s2</code>\n\n<pre>line 11\n")  # last 30
    assert text.endswith("&lt;token&gt;</pre>") and TOKEN not in text
    message(b, "/logs T1 s3")
    assert b.plans.logs[-1] == ("T1", "s3")


def test_finished_agent_loop_posts_its_handoff_once(bot, monkeypatch):
    b, api, _, module = bot
    import alfrd_telegram.bot as bot_module
    clock = [100.0]
    monkeypatch.setattr(bot_module.time, "monotonic", lambda: clock[0])
    b.plans.loop_plans["/fake/project"] = [{"id": "old", "status": "finished"}, {"id": "loop-1", "status": "running"}]
    b.watch()
    assert sent(api) == []  # loops that ended before the bot saw them stay quiet
    b.plans.loop_plans["/fake/project"][1]["status"] = "finished"
    b.plans.handoffs["loop-1"] = {
        "target": "T1", "unit": "0006", "agent": "claude", "iteration": 6, "iterations": 6,
        "text": "<!-- ALFRD plan=loop-1 -->\n## Goal\nShip **it** & `x<y`\n\n- [x] done\n- todo\n\n"
                "| a | b |\n|---|---|\n| 1 | 22 |\n\n```py\nprint('<hi>')\n```"}
    b.watch()
    assert sent(api) == []  # checked at most every WATCH_EVERY seconds
    clock[0] += module.bot.WATCH_EVERY
    b.watch()
    text = sent(api)[-1]["text"]
    assert text.startswith("🏁 <b>Agent loop finished</b>\n📁 <b>Project</b> · 🎯 <b>T1</b>\n"
                           "🧾 Plan <code>loop-1</code>\nturn 6/6 · 🤖 claude\n" + RULE)
    assert "🔷 <b>Goal</b>\nShip <b>it</b> &amp; <code>x&lt;y</code>" in text and "ALFRD plan" not in text
    assert "☑️ done\n• todo" in text and "<pre>a  b\n1  22</pre>" in text
    assert "<pre>print('&lt;hi&gt;')</pre>" in text
    clock[0] += module.bot.WATCH_EVERY
    b.watch()
    assert len(sent(api)) == 1
    message(b, "/handoff")  # on demand: the newest agent-loop plan ("old" in this fake list)
    assert texts(api)[-1] == f"🏁 Agent loop finished\n📁 Project\n🧾 Plan old\n{RULE}\n\nNo handoff was published."
    b.plans.loop_plans["/fake/project"] = []
    message(b, "/handoff")
    assert texts(api)[-1] == "📁 Project: no agent-loop plans yet."


def test_real_agent_loop_handoff(bot, tmp_path, monkeypatch):
    import sys

    import yaml
    from test_agent_loop import RESPONSE

    from alfrd.project_creation import create_project
    from alfrd.runtime import RuntimeService, RuntimeStore, scheduler

    _, api, _, module = bot
    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    service = RuntimeService(store)
    root = tmp_path / "loop"
    create_project(service, root, template="agent-loop", task="Fix the widget", iterations=2)
    script = tmp_path / "fake_agent.py"
    script.write_text("import pathlib,sys\nsys.stdin.read()\n"
                      f"text={RESPONSE!r}+'\\nAgent: '+sys.argv[1]\n"
                      "if sys.argv[1]=='codex': pathlib.Path(sys.argv[2]).write_text(text)\nelse: print(text)\n")
    data = yaml.safe_load((root / "alfrd.yaml").read_text())
    for entry in data["entrypoint"]:
        entry["cmd"] = [sys.executable, str(script), entry["name"], "{response_file}"]
    data["execution"]["usage_interval"] = 0
    (root / "alfrd.yaml").write_text(yaml.safe_dump(data, sort_keys=False))
    monkeypatch.setattr(scheduler, "POLL", .03)
    plans = module.Plans(service)
    folder = scheduler.create_plan(root)
    assert plans.loops(root) == [{"id": folder.id, "status": "running"}]
    assert plans.handoff(root, folder.id) is None
    scheduler.Runner(root, folder.id).run()
    assert plans.loops(root) == [{"id": folder.id, "status": "finished"}]
    found = plans.handoff(root, folder.id)
    assert found["iteration"] == found["iterations"] == 2 and found["agent"] == "codex"
    assert found["text"].startswith("## Goal") and found["text"].rstrip().endswith("Agent: codex")
    b = module.Bot(ROUTE, plans)
    message(b, "/handoff")
    text = texts(api)[-1]
    assert text.startswith(f"🏁 Agent loop finished\n📁 loop · 🎯 task\n🧾 Plan {folder.id}\nturn 2/2 · 🤖 codex")
    assert "🔷 Goal\nChecked." in text and "🔷 Blockers" in text


def test_long_handoff_is_split_into_parts():
    from alfrd_telegram import view

    text = "\n\n".join(f"## Section {i}\n" + "word " * 300 for i in range(12))
    parts = view.chunks(text)
    assert len(parts) > 1 and all(len(p) <= view.BODY * 1.5 for p in parts)
    assert parts[1].startswith("## Section") and "\n\n".join(parts).count("## Section") == 12
    fenced = view.chunks("```\n" + "x\n" * 4000 + "```")
    assert all(p.count("```") == 2 for p in fenced)  # a cut code block is closed and reopened


def test_whoareyou_names_the_machine(bot):
    b, api, _, _ = bot
    import socket
    message(b, "/whoareyou")
    text = texts(api)[-1]
    assert text.startswith("🤖 ALFRD bot · who am I") and f"Host  {socket.gethostname()}" in text
    assert "Projects  1" in text and "Telegram plugin" in text and TOKEN not in text
    message(b, "/whoami@FakeBot")
    assert texts(api)[-1].startswith("🤖 ALFRD bot")


def test_elapsed_format():
    from alfrd_telegram import view

    assert [view.elapsed(s) for s in (None, 0, 59.9, 60, 3599, 3600, 90061)] == [
        "?", "0s", "59s", "1m 00s", "59m 59s", "1h 00m", "25h 01m"]


def test_bot_flushes_the_digest_when_due_and_waits_while_studio_is_used(bot):
    from alfrd import presence
    from alfrd.extensions import settings

    b, api, _, module = bot
    settings.save(module.plugin, {"token": "123456:" + "A" * 30, "chat_id": "42", "hold_minutes": "5",
                                  "mute_when_active": True})
    notifiers.telegram_hold("42", "🏁 <b>held</b>", "held", now=notifiers.time.time() - 400)
    presence.touch()
    assert b.flush() == 0 and api.calls == []  # the user is at the Studio
    presence.touch(now=0)
    assert b.flush() == 1 and "🗂️ <b>ALFRD digest</b> · 1 notification" in api.calls[-1]["body"]["text"]
    notifiers.telegram_hold("42", "fresh", "fresh")
    assert b.flush() == 0  # not 5 minutes old yet
    settings.save(module.plugin, {"hold_minutes": "0", "mute_when_active": False})
    assert b.flush() == 1  # digest switched off: what is held goes out now


def test_digest_and_mute_settings_are_offered(bot):
    _, _, _, module = bot
    fields = {f.key: f for f in module.plugin.settings}
    assert fields["hold_minutes"].kind == "number" and fields["mute_when_active"].kind == "bool"
    assert re.fullmatch(fields["hold_minutes"].pattern, "15") and not re.fullmatch(fields["hold_minutes"].pattern, "-1")


def test_digest_settings_preserve_fractional_minutes_and_blank(bot):
    from alfrd.extensions import settings

    _, _, _, module = bot
    settings.save(module.plugin, {"token": "123456:" + "A" * 30, "chat_id": "42"})
    for value in ("", "0", "0.5", "15"):
        saved = settings.save(module.plugin, {"hold_minutes": value, "mute_when_active": False})
        assert saved.get("hold_minutes", "") == value
        assert notifiers.hold_minutes(saved.get("hold_minutes")) == float(value or 0)
    for value in ("-1", "invalid"):
        with pytest.raises(ValueError):
            settings.save(module.plugin, {"hold_minutes": value})
