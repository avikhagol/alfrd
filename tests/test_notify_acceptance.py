"""Notifier plan A7 1, 3 and 5 end to end: a real runner, no server, routes from the user's notify.json.

A7 2 (one ``turn.idle`` for a silent agent) is ``test_idle.py::test_silent_agent_reports_once_per_quiet_period``;
A7 4 (browser notification → review) is ``tests/studio_js/live.test.mjs`` plus a manual check (docs/notifications.md).
"""
import json
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import pytest

from test_agent_loop import project, service, wait  # noqa: F401
from test_events import events
from test_loop_features import set_turns
from alfrd import notify
from alfrd.agent_loop import approve_response, sha256
from alfrd.runtime import scheduler

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")

RECORD = "import json,sys; open(sys.argv[1],'a').write(json.dumps(json.load(sys.stdin))+'\\n')"


def received(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_review_finish_and_a_dead_webhook(project, monkeypatch):
    set_turns(project, 2, turns={1: {"human_review": True}})
    inbox = project.parent / "inbox.jsonl"
    on = ["review.pending", "turn.idle", "plan.*"]
    notify.user_config_file().parent.mkdir(parents=True, exist_ok=True)
    notify.user_config_file().write_text(json.dumps({"routes": [
        {"via": "webhook", "on": on, "url": "http://127.0.0.1:9/hook"},  # connection refused, retried, given up
        {"via": "command", "on": on, "argv": [sys.executable, "-c", RECORD, str(inbox)]},
    ]}))
    monkeypatch.setattr(notify, "BATCH_WINDOW", 0.1)
    folder = scheduler.create_plan(project)
    seen = {}

    def reviewer():
        # A7 1: review pending is delivered while the runner waits; the dead webhook first in the list doesn't hold it up
        if not wait(lambda: any("review.pending" in m["kinds"] for m in received(inbox)), timeout=30):
            return
        unit = seen["unit"] = folder.units()[0]
        seen["status"] = unit["review_status"]
        text = Path(unit["handoff"]["response_file"]).read_text()
        seen["approved"] = time.time()
        approve_response(project, folder.id, unit["id"], text, sha256(text))

    thread = threading.Thread(target=reviewer, daemon=True)
    thread.start()
    assert scheduler.Runner(project, folder.id).run() == 0  # the runner needs the main thread (signals)
    thread.join(5)
    assert seen.get("status") == "pending", "review.pending never reached the command route"
    unit = seen["unit"]
    message = next(m for m in received(inbox) if "review.pending" in m["kinds"])
    assert message["link"].startswith("/studio/#/workflow?project=") and f"unit={unit['id']}" in message["link"]
    assert "awaiting" not in json.dumps(message)  # event text never travels
    # A7 3: the next turn starts at once, although the webhook is still being retried (1 + 2 + 4 s backoff)
    started = next(e for e in events(folder) if e["kind"] == "turn.started" and e["turn"] == 2)
    assert datetime.fromisoformat(started["at"]).timestamp() - seen["approved"] < 3
    assert folder.load()["status"] == "finished"
    assert any("plan.finished" in m["kinds"] for m in received(inbox))
    # A7 5: history keeps its free text (D3); the structured kinds sit only in events.jsonl
    history = [h["event"] for h in folder.load()["history"]]
    assert any(h.startswith("awaiting human review") for h in history)
    assert not any(h in notify._PHRASES for h in history)
