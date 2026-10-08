"""Studio notifications: private route listings, mutation gate and incremental event tail."""
import json

import pytest
from flask.testing import FlaskClient
from alfrd import notify
from alfrd.gui import create_app
from alfrd.studio_live import AlfrdWatcher


@pytest.fixture
def served(tmp_path, monkeypatch):
    import alfrd.gui.studio_plans as endpoints
    root = tmp_path / "project"
    root.mkdir()
    (root / "alfrd.yaml").write_text("version: 1\nname: test\ntemplate: avica\nnotify:\n  routes:\n    - via: desktop\n      'on': [review.pending]\n")
    monkeypatch.setattr(endpoints, "_project_root", lambda _: root)
    user = notify.user_config_file()
    user.parent.mkdir(parents=True, exist_ok=True)
    user.write_text(json.dumps({"routes": [
        {"via": "webhook", "on": ["plan.*"], "url": "https://user:password@example.com/hook?token=hidden#fragment", "secret": "hidden", "headers": {"Authorization": "hidden"}},
        {"via": "command", "on": ["turn.idle"], "argv": ["program", "secret-arg"]}]}))
    from alfrd.runtime import RuntimeStore, RuntimeService
    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    app = create_app({"RUNTIME_SERVICE": RuntimeService(store),"TESTING": True, "SECRET_KEY": "test", "ACCESS_TOKEN": "access", "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'catalog.db'}"})
    client = app.test_client()
    csrf = client.get("/api/studio/session").get_json()["csrf_token"]
    return app, client, csrf


def test_listing_redacts_all_sensitive_options(served):
    _, client, _ = served
    response = client.get("/api/studio/projects/test/notify")
    assert response.status_code == 200
    data = response.get_json()
    assert [r["source"] for r in data["routes"]] == ["project", "user", "user"], data
    assert data["routes"][1]["options"]["url"] == "https://example.com/hook"
    assert data["routes"][2]["options"]["argv"] == "program (1 args)"
    text = json.dumps(data)
    for secret in ("hidden", "password", "secret-arg", "Authorization"):
        assert secret not in text


def test_send_test_requires_token_csrf_and_local_access(served, monkeypatch):
    app, client, csrf = served
    path = "/api/studio/projects/test/notify/test"
    assert FlaskClient(app).post(path, json={"index": 0}).status_code == 401
    assert client.post(path, json={"index": 0}).status_code == 403
    assert client.post(path, json={"index": 0}, headers={"X-CSRF-Token": csrf}, environ_overrides={"REMOTE_ADDR": "203.0.113.1"}).status_code == 403
    calls = []
    monkeypatch.setitem(notify.SENDERS, "desktop", lambda *args: calls.append(args))
    response = client.post(path, json={"index": 0}, headers={"X-CSRF-Token": csrf})
    assert response.get_json() == {"ok": True}
    assert len(calls) == 1
    assert calls[0][1]["title"] == "ALFRD test notification"
    assert calls[0][2] == 10
    for index in (-1, 99, True, "0"):
        assert client.post(path, json={"index": index}, headers={"X-CSRF-Token": csrf}).status_code == 400


def test_send_skip_is_not_retried(served, monkeypatch):
    _, client, csrf = served
    calls = []
    def skip(*args):
        calls.append(args)
        raise notify.Skip("no session")
    monkeypatch.setitem(notify.SENDERS, "desktop", skip)
    assert client.post("/api/studio/projects/test/notify/test", json={"index": 0}, headers={"X-CSRF-Token": csrf}).get_json() == {"ok": False, "skipped": "no session"}
    assert len(calls) == 1


def test_watcher_baseline_partial_utf8_rotation_and_finished_flush(tmp_path, monkeypatch):
    from alfrd.runtime import scheduler
    plans = [{"id": "run", "status": "running"}]
    monkeypatch.setattr(scheduler, "list_plans", lambda _: plans)
    monkeypatch.setattr(notify, "project_ref", lambda _: {"identifier": "project-id"})
    path = tmp_path / ".alfrd/plans/run/events.jsonl"
    path.parent.mkdir(parents=True)
    def line(seq, kind="turn.finished"):
        return (json.dumps({"seq": seq, "kind": kind, "project": str(tmp_path), "plan": "run", "target": "t-α", "text": "PRIVATE", "data": {"PRIVATE": True}}, ensure_ascii=False) + "\n").encode()
    path.write_bytes(line(1))
    published = []
    w = AlfrdWatcher("project-id", tmp_path, published.append)
    assert w.check() is None
    with path.open("ab") as f:
        f.write(line(2)[:-2])
    assert w.check() is None
    with path.open("ab") as f:
        f.write(line(2)[-2:])
    event = w.check()
    assert [e["seq"] for e in event["events"]] == [2]
    assert "PRIVATE" not in json.dumps(event) and "data" not in event["events"][0]
    assert "project=project-id" in event["events"][0]["link"]
    with path.open("ab") as f:
        f.write(line(3))
    path.rename(path.with_name("events.jsonl.1"))
    path.write_bytes(line(4))
    assert [e["seq"] for e in w.check()["events"]] == [3, 4]
    plans[0]["status"] = "finished"
    with path.open("ab") as f:
        f.write(line(5, "plan.finished"))
    assert w.check()["events"][0]["kind"] == "plan.finished"
    assert w.check() is None


def test_hub_companion_events_are_available_through_poll_cursor(tmp_path, monkeypatch):
    from alfrd.runtime import scheduler
    from alfrd.studio_live import LiveHub
    monkeypatch.setattr(scheduler, "list_plans", lambda _: [{"id": "run", "status": "running"}])
    monkeypatch.setattr(notify, "project_ref", lambda _: {"identifier": "p"})
    path = tmp_path / ".alfrd/plans/run/events.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("")
    hub = LiveHub(lambda key: tmp_path if key == "p" else None, interval=30, idle=30)
    try:
        sub, hello = hub.subscribe(["p"])
        companion = hub._event_watchers["p"]
        assert companion.wait_ready()
        companion.stop()
        companion._thread.join(timeout=1)
        path.write_text(json.dumps({"seq": 1, "kind": "review.pending", "plan": "run"}) + "\n")
        event = companion.check()
        assert event["type"] == "alfrd"
        assert sub.queue.get(timeout=1)["type"] == "alfrd"
        state = hello["p"]
        response = hub.changes({"p": f"{state['epoch']}:{state['version']}"}, ["p"])
        assert response["events"][0]["events"][0]["kind"] == "review.pending"
        assert response["reset"] == []
    finally:
        hub.stop_all()
