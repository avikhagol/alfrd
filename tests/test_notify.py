"""Notification dispatch: routes, cursor resume, batching, retries (Ticket N3)."""
import json
import sys
import threading
import time
from datetime import datetime, timedelta

import pytest
import yaml

from test_agent_loop import project, service, wait  # noqa: F401
from test_events import events
from test_idle import configure
from test_loop_features import set_turns
from alfrd import notify
from alfrd.events import EVENTS_FILE, EventLog
from alfrd.execution import ExecutionError, load_execution
from alfrd.manifest import ManifestError, validate_manifest
from alfrd.runtime import scheduler


def stamp(seconds_ago=0.0):
    return (datetime.now() - timedelta(seconds=seconds_ago)).isoformat(timespec="seconds")


def record(kind, unit="0001-a", target="t-x", ago=0.0, **data):
    return {"at": stamp(ago), "kind": kind, "plan": "p1", "project": "/proj", "target": target, "unit": unit,
            "turn": 1, "turns": 4, "agent": "claude", "roles": [], "text": "secret handoff text", "data": data}


class Sink:
    """A sender that records messages; ``fail`` makes the first n calls raise, ``hang`` blocks."""

    def __init__(self, fail=0, hang=0.0):
        self.messages, self.calls, self.fail, self.hang = [], 0, fail, hang

    def __call__(self, route, message, timeout):
        self.calls += 1
        if self.hang:
            time.sleep(self.hang)
        if self.calls <= self.fail:
            raise ConnectionRefusedError("refused")
        self.messages.append(message)


def dispatcher(tmp_path, sink, on=("*",), logs=None, **kw):
    kw = {"batch_window": 0.2, "timeout": 1.0, "retries": 3, "backoff": (0,), **kw}
    return notify.Dispatcher(tmp_path, [{"via": "fake", "on": list(on)}], senders={"fake": sink},
                             log=(logs if logs is not None else []).append, **kw)


def write(tmp_path, *records):
    log = EventLog(tmp_path / EVENTS_FILE)
    return [{"seq": log.append(r), **r} for r in records]


# -- routes -------------------------------------------------------------------

def test_route_schema_and_warnings(tmp_path):
    routes = notify.validate_routes([{"via": "desktop", "on": "plan.*"}, {"via": "pager", "on": ["nope", "turn.idle"]}])
    assert routes[0]["on"] == ["plan.*"]
    warnings = notify.route_warnings(routes)
    assert any("'pager' is unknown" in w for w in warnings) and any("'nope' matches no event kind" in w for w in warnings)
    for bad in ("x", [{"on": ["plan.*"]}], [{"via": "desktop"}], [{"via": "desktop", "on": []}], [{"via": "desktop", "on": [1]}]):
        with pytest.raises(notify.RouteError):
            notify.validate_routes(bad)
    for via in ("webhook", "command"):
        with pytest.raises(notify.RouteError, match="only allowed in the user"):
            notify.validate_routes([{"via": via, "on": ["*"]}])
        assert notify.validate_routes([{"via": via, "on": ["*"]}], user=True)[0]["via"] == via


def test_project_routes_come_before_user_routes(tmp_path):
    user = tmp_path / "notify.json"
    user.write_text(json.dumps({"routes": [{"via": "webhook", "on": ["plan.*"], "url": "https://x"}]}))
    routes, warnings = notify.load_routes([{"via": "desktop", "on": ["review.pending"]}], user)
    assert [r["via"] for r in routes] == ["desktop", "webhook"] and warnings == []
    user.write_text("{broken")
    routes, warnings = notify.load_routes([{"via": "desktop", "on": ["review.pending"]}], user)
    assert [r["via"] for r in routes] == ["desktop"] and "cannot read" in warnings[0]


def test_user_file_follows_the_config_dir(tmp_path):
    assert str(notify.user_config_file()).startswith(str(tmp_path))  # conftest's XDG_CONFIG_HOME


def test_prefix_matching_and_stop_plan_duplicate():
    route = {"via": "x", "on": ["plan.*", "turn.failed"]}
    assert notify.route_matches(route, record("plan.failed"))
    assert notify.route_matches(route, record("turn.failed"))
    assert not notify.route_matches(route, record("turn.finished"))
    assert not notify.route_matches(route, record("turn.failed", unit=None, stop_plan=True))


def test_manifest_and_execution_check_routes(project):
    base = yaml.safe_load((project / "alfrd.yaml").read_text())
    validate_manifest({**base, "notify": {"routes": [{"via": "desktop", "on": ["plan.*"]}]}})
    for routes in ([{"via": "desktop"}], [{"via": "webhook", "on": ["*"]}], "x"):
        with pytest.raises(ManifestError, match="notify"):
            validate_manifest({**base, "notify": {"routes": routes}})
    configure(project, notify={"routes": [{"via": "desktop", "on": "review.pending"}]})
    assert load_execution(project).settings["notify_routes"] == [{"via": "desktop", "on": ["review.pending"]}]
    configure(project, notify={"routes": [{"via": "command", "on": ["*"]}]})
    with pytest.raises(ExecutionError, match="only allowed in the user"):
        load_execution(project)


# -- messages -----------------------------------------------------------------

def test_message_payload_and_batch_title():
    message = notify.build_message([record("turn.failed", unit=f"000{i}-a") for i in range(3)])
    assert message["title"] == "3 turns failed in t-x"
    assert message["link"] == "/studio/#/workflow?project=%2Fproj&plan=p1"
    one = notify.build_message([record("review.pending", status="pending")])
    assert one["title"] == "review pending in t-x"
    assert one["link"] == "/studio/#/workflow?project=%2Fproj&plan=p1&unit=0001-a"
    assert {k: one["events"][0][k] for k in ("turn", "turns", "agent", "status")} == \
        {"turn": 1, "turns": 4, "agent": "claude", "status": "pending"}
    assert "secret handoff text" not in json.dumps(one) and "text" not in one["events"][0]


# -- dispatcher ---------------------------------------------------------------

def test_batching_merges_and_advances_the_cursor(tmp_path):
    sink = Sink()
    d = dispatcher(tmp_path, sink, on=["turn.failed"]).start()
    for r in write(tmp_path, *(record("turn.failed", unit=f"u{i}") for i in range(3)), record("turn.started")):
        d.submit(r)
    assert wait(lambda: sink.messages, 5)
    d.stop(5)
    assert len(sink.messages) == 1 and sink.messages[0]["title"] == "3 turns failed in t-x"
    assert notify.read_cursor(tmp_path / notify.CURSOR_FILE) == 4


def test_cursor_resume_has_no_duplicates(tmp_path):
    first = write(tmp_path, record("plan.started"))  # before notifications existed: never sent
    sink = Sink()
    d = dispatcher(tmp_path, sink).start()
    assert notify.read_cursor(tmp_path / notify.CURSOR_FILE) == first[-1]["seq"]
    for r in write(tmp_path, record("review.pending")):
        d.submit(r)
    d.stop(5)
    pending = write(tmp_path, record("turn.idle"), record("plan.finished"))  # the runner died before sending these
    again = Sink()
    d2 = dispatcher(tmp_path, again).start()
    for r in pending:
        d2.submit(r)  # the live feed repeating the backlog is ignored
    d2.stop(5)
    assert [m["kinds"] for m in sink.messages] == [["review.pending"]]
    assert [m["kinds"] for m in again.messages] == [["turn.idle", "plan.finished"]]
    d3 = dispatcher(tmp_path, Sink()).start()
    d3.stop(5)
    assert d3.senders["fake"].messages == []


def test_old_backlog_becomes_one_summary(tmp_path):
    write(tmp_path, record("plan.started"))
    notify.write_cursor(tmp_path / notify.CURSOR_FILE, 1)
    write(tmp_path, *(record("turn.failed", ago=7200) for _ in range(5)), record("review.pending"))
    sink = Sink()
    dispatcher(tmp_path, sink).start().stop(5)
    assert [m["title"] for m in sink.messages] == ["5 earlier event(s) not sent in t-x", "review pending in t-x"]
    assert sink.messages[0]["events"] == [] and sink.messages[0]["skipped"] == 5
    assert notify.read_cursor(tmp_path / notify.CURSOR_FILE) == 7


def test_retry_then_success_and_give_up_never_raise(tmp_path):
    logs = []
    flaky = Sink(fail=2)
    d = dispatcher(tmp_path, flaky, logs=logs).start()
    d.submit(write(tmp_path, record("plan.failed"))[0])
    d.stop(5)
    assert flaky.calls == 3 and len(flaky.messages) == 1
    dead = Sink(fail=99)
    d = dispatcher(tmp_path, dead, logs=logs).start()
    d.submit(write(tmp_path, record("plan.failed"))[0])
    d.stop(5)
    assert dead.calls == 4 and any("gave up after 4 attempts" in line for line in logs)
    assert notify.read_cursor(tmp_path / notify.CURSOR_FILE) == 2  # given up: not retried by the next runner


def test_a_hanging_notifier_times_out_without_blocking_submit(tmp_path):
    logs = []
    slow = Sink(hang=5)
    d = dispatcher(tmp_path, slow, logs=logs, timeout=0.2, retries=1).start()
    started = time.monotonic()
    for r in write(tmp_path, *(record("turn.started") for _ in range(50))):
        d.submit(r)
    assert time.monotonic() - started < 0.5
    d.stop(5)
    assert slow.calls == 2 and any("timed out after 0.2 s" in line for line in logs)


def test_queue_overflow_drops_the_oldest(tmp_path):
    gate = threading.Event()
    sink = Sink()
    d = dispatcher(tmp_path, lambda *a: (gate.wait(5), sink(*a)), queue_size=3, batch_window=0).start()
    d.submit(write(tmp_path, record("turn.started", unit="first"))[0])
    assert wait(lambda: not d.queue, 2)  # the first batch is out, its delivery blocked
    for r in write(tmp_path, *(record("turn.started", unit=f"u{i}") for i in range(5))):
        d.submit(r)
    gate.set()
    d.stop(5)
    last = sink.messages[-1]
    assert last["dropped"] == 2 and [e["unit"] for e in last["events"]] == ["u2", "u3", "u4"]


def test_unknown_via_is_reported_once(tmp_path):
    logs = []
    d = notify.Dispatcher(tmp_path, [{"via": "pager", "on": ["*"]}], senders={}, log=logs.append, batch_window=0).start()
    for r in write(tmp_path, record("plan.started"), record("plan.finished")):
        d.submit(r)
    d.stop(5)
    assert sum("no notifier 'pager'" in line for line in logs) == 1


# -- runner -------------------------------------------------------------------

@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")
def test_runner_delivers_routed_events(project, monkeypatch, capsys):
    set_turns(project, 1)
    configure(project, notify={"routes": [{"via": "fake", "on": ["plan.*", "handoff.published"]}]})
    sink = Sink()
    monkeypatch.setitem(notify.SENDERS, "fake", sink)
    monkeypatch.setattr(notify, "BATCH_WINDOW", 0.05)
    folder = scheduler.create_plan(project)
    assert scheduler.Runner(project, folder.id).run() == 0
    kinds = [k for m in sink.messages for k in m["kinds"]]
    assert kinds == ["plan.started", "handoff.published", "plan.finished"]
    assert notify.read_cursor(folder.path / notify.CURSOR_FILE) == events(folder)[-1]["seq"]
    assert "'fake' is unknown" in capsys.readouterr().out  # a plugin-less via warns in runner.log
