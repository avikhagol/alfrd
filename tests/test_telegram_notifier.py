"""The built-in ``telegram`` notifier: Bot API faked, no network."""
import io
import json
import urllib.error

import pytest
from test_agent_loop import wait
from test_notify import record, write

from alfrd import notifiers, notify

TOKEN = "123456:FAKE-test-token"
ROUTE = {"via": "telegram", "on": ["*"], "token": TOKEN, "chat_id": "42", "studio_url": "http://127.0.0.1:5122/"}
MESSAGE = notify.build_message([record("review.pending", status="pending")])


class FakeAPI:
    """Replaces ``build_opener``; ``replies`` is a list of (status, json) answered in turn (last one repeats)."""

    def __init__(self, replies=((200, {"ok": True, "result": {}}),)):
        self.replies, self.calls = list(replies), []

    def __call__(self, *handlers):
        return self

    def open(self, request, timeout):
        self.calls.append({"url": request.full_url, "body": json.loads(request.data), "timeout": timeout,
                           "headers": dict(request.header_items())})
        status, reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        data = json.dumps(reply).encode()
        if status >= 400:
            raise urllib.error.HTTPError(request.full_url, status, "error", {}, io.BytesIO(data))
        return _Response(data)


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def api(monkeypatch):
    fake = FakeAPI()
    monkeypatch.setattr(notifiers.urllib.request, "build_opener", fake)
    return fake


def test_registered_known_and_user_only():
    assert notify.SENDERS["telegram"] is notifiers.telegram
    assert "telegram" in notify.KNOWN_VIA and "telegram" in notify.USER_ONLY_VIA
    assert notify.route_warnings(notify.validate_routes([ROUTE], user=True)) == []
    with pytest.raises(notify.RouteError, match="only allowed in the user's notify.json"):
        notify.validate_routes([ROUTE])


def test_options_are_checked():
    assert notify.validate_routes([{**ROUTE, "chat_id": -1001234}], user=True)[0]["chat_id"] == -1001234
    assert notify.validate_routes([{k: v for k, v in ROUTE.items() if k != "studio_url"}], user=True)
    for bad, msg in (({"token": ""}, "token"), ({"token": 5}, "token"), ({"token": None}, "token"),
                     ({"chat_id": ""}, "chat_id"), ({"chat_id": True}, "chat_id"), ({"chat_id": 4.2}, "chat_id"),
                     ({"chat_id": None}, "chat_id"), ({"studio_url": "127.0.0.1:5122"}, "studio_url"),
                     ({"studio_url": 1}, "studio_url")):
        with pytest.raises(notify.RouteError, match=msg):
            notify.validate_routes([{**ROUTE, **bad}], user=True)


def test_sends_styled_html_with_the_studio_link(api):
    notifiers.telegram(ROUTE, MESSAGE, 7)
    call = api.calls[0]
    assert call["url"] == f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    assert call["timeout"] == 7 and call["headers"]["Content-type"] == "application/json"
    body = call["body"]
    assert body["chat_id"] == "42" and body["parse_mode"] == "HTML"
    assert body["link_preview_options"] == {"is_disabled": True}
    text = body["text"]
    assert text.startswith("📝 <b>review pending in t-x</b>\n🎯 t-x\n🧾 <code>p1</code>\n")
    assert "📝 review pending · <code>0001-a</code> · turn 1/4 · 🤖 claude" in text
    assert text.endswith('">Open in Studio</a>') and "http://127.0.0.1:5122/studio/#/workflow?" in text
    assert "&amp;plan=p1" in text  # the href is escaped


def test_html_rejected_falls_back_to_plain_text(api):
    api.replies = [(400, {"ok": False, "description": "can't parse entities"}), (200, {"ok": True, "result": {}})]
    notifiers.telegram(ROUTE, MESSAGE, 5)
    plain = api.calls[1]["body"]
    assert "parse_mode" not in plain
    assert plain["text"] == (f"{MESSAGE['title']}\n\n{MESSAGE['body']}\n\nOpen in Studio\n"
                             f"http://127.0.0.1:5122{MESSAGE['link']}")


def test_no_studio_url_means_no_link(api):
    notifiers.telegram({**ROUTE, "studio_url": None}, MESSAGE, 5)
    assert "Open in Studio" not in api.calls[0]["body"]["text"]
    text = notifiers.telegram_text({}, {"title": "Title\nwith   spaces", "body": "Body"})
    assert text == "Title with spaces\n\nBody"
    assert "&lt;b&gt;" in notifiers.telegram_html({}, {"title": "<b>x</b>"})  # values are escaped


def test_ended_plan_has_no_studio_link_and_a_run_summary():
    message = notify.build_message([record("plan.finished")], project={"identifier": "p", "name": "Proj"})
    message["summary"] = {"targets": [{"target": "3C273", "steps": [
        {"step": "flag", "status": "done", "duration_s": 75},
        {"step": "image", "status": "failed", "duration_s": 3700}]}], "more": 2}
    assert "Open in Studio" not in notifiers.telegram_text(ROUTE, message)
    text = notifiers.telegram_html(ROUTE, message)
    assert "Open in Studio" not in text
    assert text.startswith("⚠️ <b>plan finished in t-x</b>\n📁 <b>Proj</b> · 🎯 t-x")
    assert "📊 <b>Run summary</b>" in text and "⏱️ 1h 02m total step time · ✅ 1 done · ❌ 1 failed" in text
    assert "🎯 <b>3C273</b>\n✅ <code>flag</code> · done · 1m 15s\n❌ <code>image</code> · failed · 1h 01m" in text
    assert "…and 2 more target(s)" in text


def test_long_summary_is_cut_by_whole_lines():
    steps = [{"step": f"s{i}", "status": "done", "duration_s": i} for i in range(400)]
    message = {**notify.build_message([record("plan.finished")]), "summary": {"targets": [{"target": "t", "steps": steps}]}}
    text = notifiers.telegram_html(ROUTE, message)
    assert notifiers._utf16_len(text) <= notifiers.TELEGRAM_LIMIT
    assert text.endswith("✂️ Summary shortened to fit Telegram.</i>") and text.count("<code>") == text.count("</code>")


def test_run_summary_only_for_avica_projects(tmp_path, monkeypatch):
    assert notify.run_summary(tmp_path, "p1") is None  # no manifest
    (tmp_path / "alfrd.yaml").write_text("template: avica\n")
    doc = {"plan": {"steps": ["flag", "image"]}, "rows": [
        {"target": "A", "cells": {"flag": "done", "image": "skip"}, "detail": {"flag": {"duration_s": 3}}},
        {"target": "B", "cells": {"flag": "skip", "image": "skip"}}]}
    monkeypatch.setattr("alfrd.api.status.plan_status", lambda root, plan, detail: doc)
    assert notify.run_summary(tmp_path, "p1") == {"template": "avica", "plan": "p1", "more": 0, "targets": [
        {"target": "A", "steps": [{"step": "flag", "status": "done", "duration_s": 3}]}]}
    (tmp_path / "alfrd.yaml").write_text("name: other\n")
    assert notify.run_summary(tmp_path, "p1") is None


def test_dispatcher_attaches_the_summary_to_plan_end_only(tmp_path, monkeypatch):
    sink = []
    monkeypatch.setattr(notify, "run_summary", lambda root, plan: {"targets": [{"target": "A", "steps": []}]})
    d = notify.Dispatcher(tmp_path, [{"via": "x", "on": ["*"]}], senders={"x": lambda r, m, t: sink.append(m)},
                          log=lambda line: None, batch_window=0, project={"identifier": "p", "name": "p"}).start()
    d.submit(write(tmp_path, record("turn.started"))[0])
    assert wait(lambda: len(sink) == 1, 5)
    d.submit(write(tmp_path, record("plan.finished"))[0])
    assert wait(lambda: len(sink) == 2, 5)
    d.stop(2)
    assert "summary" not in sink[0] and sink[1]["summary"]["targets"][0]["target"] == "A"


def test_mute_while_studio_is_active(api):
    from alfrd import presence

    presence.touch()
    with pytest.raises(notify.Skip, match="muted"):
        notifiers.telegram({**ROUTE, "mute_when_active": True}, MESSAGE, 5)
    notifiers.telegram(ROUTE, MESSAGE, 5)  # not muted without the option
    assert len(api.calls) == 1
    presence.touch(now=0)  # long ago
    notifiers.telegram({**ROUTE, "mute_when_active": True}, MESSAGE, 5)
    assert len(api.calls) == 2


def test_hold_collects_a_digest_and_flushes_when_due(api):
    route = {**ROUTE, "hold_minutes": 10}
    notifiers.telegram(route, MESSAGE, 5)
    notifiers.telegram(route, MESSAGE, 5)
    assert api.calls == [] and len(notifiers._read_outbox(notifiers.outbox_file("42"))) == 2
    assert notifiers.telegram_flush(TOKEN, "42", 600) == 0  # not due yet
    sent = notifiers.telegram_flush(TOKEN, "42", 600, now=notifiers.time.time() + 601)
    assert sent == 1 and len(api.calls) == 1
    text = api.calls[0]["body"]["text"]
    assert text.startswith("🗂️ <b>ALFRD digest</b> · 2 notifications") and text.count("review pending in t-x") == 2
    assert not notifiers.outbox_file("42").exists()


def test_digest_splits_only_past_the_message_limit_and_keeps_the_outbox_on_errors(api):
    big = "x" * 3000
    for _ in range(3):
        notifiers.telegram_hold("42", big, big)
    api.replies = [(500, {"ok": False})]
    with pytest.raises(notifiers.TelegramError):
        notifiers.telegram_flush(TOKEN, "42", 0, force=True)
    assert len(notifiers._read_outbox(notifiers.outbox_file("42"))) == 3  # kept for the next flush
    api.replies = [(200, {"ok": True, "result": {}})]
    api.calls.clear()
    assert notifiers.telegram_flush(TOKEN, "42", 0, force=True) == 3
    assert all(notifiers._utf16_len(c["body"]["text"]) <= 4096 for c in api.calls)


def test_hold_and_mute_options_are_checked():
    for bad, msg in (({"hold_minutes": -1}, "hold_minutes"), ({"hold_minutes": "5"}, "hold_minutes"),
                     ({"mute_when_active": "yes"}, "mute_when_active")):
        with pytest.raises(notify.RouteError, match=msg):
            notify.validate_routes([{**ROUTE, **bad}], user=True)
    assert notifiers.hold_minutes("") == 0 and notifiers.hold_minutes("2.5") == 2.5 and notifiers.hold_minutes(True) == 0


def test_text_stays_under_4096_utf16_units():
    for filler in ("x", "é", "😀"):
        long = {**MESSAGE, "title": filler * 500, "body": filler * 9000}
        text = notifiers.telegram_text(ROUTE, long)
        assert notifiers._utf16_len(text) <= notifiers.TELEGRAM_LIMIT
        assert text.endswith(MESSAGE["link"]) and "…" in text
        assert notifiers._utf16_len(text.split("\n\n")[0]) <= 256
        assert notifiers._utf16_len(notifiers.telegram_html(ROUTE, long)) <= notifiers.TELEGRAM_LIMIT
    short = notifiers.telegram_text(ROUTE, MESSAGE)
    assert "…" not in short


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_bad_token_or_chat_is_skipped_without_the_token(api, status):
    api.replies = [(status, {"ok": False, "description": f"Unauthorized {TOKEN}"})]
    with pytest.raises(notify.Skip) as info:
        notifiers.telegram(ROUTE, MESSAGE, 5)
    assert f"HTTP {status}" in str(info.value) and TOKEN not in str(info.value)


@pytest.mark.parametrize("status", [429, 500, 502])
def test_rate_limit_and_server_errors_raise_for_retry(api, status):
    api.replies = [(status, {"ok": False, "description": "Too Many Requests: retry after 3"})]
    with pytest.raises(notifiers.TelegramError) as info:
        notifiers.telegram(ROUTE, MESSAGE, 5)
    assert not isinstance(info.value, notify.Skip) and info.value.status == status
    assert TOKEN not in str(info.value)


def test_network_errors_never_show_the_token(api):
    api.replies = [(0, urllib.error.URLError(f"cannot reach /bot{TOKEN}/sendMessage"))]
    with pytest.raises(notifiers.TelegramError) as info:
        notifiers.telegram(ROUTE, MESSAGE, 5)
    assert TOKEN not in str(info.value) and "URLError" in str(info.value)
    api.replies = [(200, {"ok": False, "description": "nope"})]
    with pytest.raises(notifiers.TelegramError, match="not ok: nope"):
        notifiers.telegram(ROUTE, MESSAGE, 5)


def test_missing_options_skip_before_any_request(api):
    for bad in ({"token": ""}, {"chat_id": None}, {"chat_id": False}):
        with pytest.raises(notify.Skip):
            notifiers.telegram({**ROUTE, **bad}, MESSAGE, 5)
    assert api.calls == []


def test_dispatcher_retries_429_and_gives_up_on_401(tmp_path, api):
    logs = []
    api.replies = [(429, {"ok": False}), (200, {"ok": True, "result": {}})]
    d = notify.Dispatcher(tmp_path, [ROUTE], senders={"telegram": notifiers.telegram}, log=logs.append,
                          batch_window=0, timeout=2, retries=3, backoff=(0,),
                          project={"identifier": "p", "name": "p"}).start()
    d.submit(write(tmp_path, record("plan.failed"))[0])
    assert wait(lambda: len(api.calls) == 2, 5)
    d.stop(2)
    assert TOKEN not in "\n".join(logs)

    api.calls.clear()
    api.replies = [(401, {"ok": False, "description": "Unauthorized"})]
    logs.clear()
    other = tmp_path / "other"
    other.mkdir()
    d = notify.Dispatcher(other, [ROUTE], senders={"telegram": notifiers.telegram}, log=logs.append,
                          batch_window=0, timeout=2, retries=3, backoff=(0,),
                          project={"identifier": "p", "name": "p"}).start()
    d.submit(write(other, record("plan.failed"))[0])
    assert wait(lambda: any("HTTP 401" in line for line in logs), 5)
    d.stop(2)
    assert len(api.calls) == 1  # skipped, not retried
    assert TOKEN not in "\n".join(logs)


def test_long_studio_link_is_preserved_or_explicitly_skipped():
    link = "/studio/" + "x" * 3500
    text = notifiers.telegram_text(ROUTE, {"title": "Title", "body": "x" * 9000, "link": link})
    assert text.endswith(link) and notifiers._utf16_len(text) <= 4096
    with pytest.raises(notify.Skip, match="Studio link exceeds"):
        notifiers.telegram_text(ROUTE, {"link": "x" * 4096})


@pytest.mark.parametrize("status", [200, 401])
def test_token_scrubbed_before_error_description_is_clipped(api, status):
    api.replies = [(status, {"ok": False, "description": "x" * 195 + TOKEN})]
    with pytest.raises(notifiers.TelegramError) as error:
        notifiers.telegram_call(TOKEN, "getUpdates", {}, 5)
    assert "123456" not in str(error.value)


def test_route_without_credentials_uses_plugin_settings(api):
    from alfrd.extensions import Plugin, SettingField, settings

    bare = {"via": "telegram", "on": ["*"]}
    assert notify.validate_routes([bare], user=True)[0]["via"] == "telegram"
    with pytest.raises(notifiers.Skip, match="needs token"):
        notifiers.telegram(bare, MESSAGE, 5)
    plugin = Plugin(id="telegram", version="1", settings=[SettingField("token", kind="secret"), SettingField("chat_id")])
    settings.save(plugin, {"token": TOKEN, "chat_id": "77"})
    notifiers.telegram(bare, MESSAGE, 5)
    assert api.calls[-1]["body"]["chat_id"] == "77" and TOKEN in api.calls[-1]["url"]
    notifiers.telegram({**bare, "chat_id": "88"}, MESSAGE, 5)  # a route value wins
    assert api.calls[-1]["body"]["chat_id"] == "88"
    with pytest.raises(notify.RouteError, match="token must be"):
        notify.validate_routes([{**bare, "token": ""}], user=True)


def test_summary_unknown_time_and_skip_are_explicit():
    lines = notifiers._summary_lines({"targets": [{"target": "T", "steps": [
        {"step": "image", "status": "skip", "duration_s": None}]}]})
    assert "time unavailable total step time" in "\n".join(lines)
    assert "⏭️ <code>image</code> · skip · time unavailable" in lines
    assert notifiers._KIND_ICONS["plugin.hook_failed"] == "⚠️"
