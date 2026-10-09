"""Idle signals describe quiet periods without changing history or stopping units."""
import os
import sys
import time
from pathlib import Path

import pytest
import yaml

from test_agent_loop import RESPONSE, project, service, wait  # noqa: F401
from test_events import events
from test_loop_features import set_turns
from alfrd.agent_loop import approve_response, sha256, submit_response
from alfrd.execution import ExecutionError, load_execution
from alfrd.runtime import scheduler

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")


def configure(project, **sections):
    path = project / "alfrd.yaml"
    data = yaml.safe_load(path.read_text())
    for section, values in sections.items():
        data.setdefault(section, {}).update(values)
    path.write_text(yaml.safe_dump(data))


@pytest.mark.parametrize("sections, expected", [
    ({}, 600),
    ({"execution": {"idle_after": 2}}, 2),
    ({"notify": {"idle_after": 1.5}}, 1.5),
    ({"execution": {"idle_after": 2}, "notify": {"idle_after": 3}}, 3),
    ({"execution": {"idle_after": "bad"}, "notify": {"idle_after": 3}}, 3),
    ({"execution": {"idle_after": 0}}, 0),
    ({"execution": {"idle_after": False}}, 0),
    ({"execution": {"idle_after": 2}, "notify": {"idle_after": 0}}, 0),
    ({"execution": {"idle_after": 2}, "notify": {"idle_after": False}}, 0),
])
def test_idle_config(project, sections, expected):
    configure(project, **sections)
    assert load_execution(project).settings["idle_after"] == expected


@pytest.mark.parametrize("section", ["notify", "execution"])
@pytest.mark.parametrize("value", [-1, "bad", None, True, [], {}, float("nan"), float("inf")])
def test_bad_idle_config(project, section, value):
    configure(project, **{section: {"idle_after": value}})
    with pytest.raises(ExecutionError, match=rf"{section}\.idle_after must be non-negative seconds"):
        load_execution(project)


@pytest.mark.serial  # 0.4 s margin around the idle threshold
@pytest.mark.parametrize("periods", [1, 2])
def test_silent_agent_reports_once_per_quiet_period(project, periods):
    set_turns(project, 1)
    configure(project, notify={"idle_after": 1})
    data = yaml.safe_load((project / "alfrd.yaml").read_text())
    script = Path(data["entrypoint"][0]["cmd"][1])
    script.write_text(script.read_text().replace(
        "print('stderr diagnostic',file=sys.stderr)",
        "\n".join("print('activity', file=sys.stderr, flush=True); time.sleep(1.4)" for _ in range(periods))))
    folder = scheduler.create_plan(project)
    scheduler.Runner(project, folder.id).run()
    idle = [e for e in events(folder) if e["kind"] == "turn.idle"]
    assert len(idle) == periods
    assert all(e["data"]["quiet_for"] > 1 and e["unit"] and e["text"] is None for e in idle)
    assert folder.units()[0]["status"] == "done"
    history = [h["event"] for h in folder.load()["history"]]
    assert len(history) == 4  # created, runner started, handoff published, runner stopped
    assert history[2].startswith("handoff published:")


@pytest.mark.parametrize("stream", [".log", ".events.jsonl", ".usage.jsonl"])
def test_idle_activity_rearms_even_between_polls(project, monkeypatch, stream):
    configure(project, notify={"idle_after": 1})
    folder = scheduler.create_plan(project)
    runner = scheduler.Runner(project, folder.id)
    stamp = time.time()
    unit = {"id": "quiet", "status": "running", "steps": [runner.cfg.step_ids[0]],
            "log": str((folder.path / "logs/quiet.log").relative_to(project)),
            "started": scheduler.datetime.fromtimestamp(stamp).isoformat()}
    live = scheduler.Live(unit)
    runner.live[unit["id"]] = live
    monkeypatch.setattr(runner, "_alive", lambda live: True)
    monkeypatch.setattr(runner, "agent_metadata", lambda unit: None)
    monkeypatch.setattr(scheduler.time, "time", lambda: stamp + 2)
    runner.poll()  # missing files fall back to the unit's start
    runner.poll()
    path = (project / unit["log"]).with_suffix(stream)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("activity\n")
    os.utime(path, (stamp + 3, stamp + 3))
    monkeypatch.setattr(scheduler.time, "time", lambda: stamp + 5)
    runner.poll()  # activity resumed and became quiet again between polls
    runner.poll()
    assert len(events(folder)) == 2
    assert all(e["kind"] == "turn.idle" for e in events(folder))
    assert [h["event"] for h in folder.load()["history"]] == ["created"]
    runner.cfg.settings["idle_after"] = 0
    os.utime(path, (stamp + 4, stamp + 4))
    runner.poll()
    assert len(events(folder)) == 2


@pytest.mark.parametrize("hold", ["review", "manual"])
def test_review_and_manual_waits_are_not_idle(project, hold):
    set_turns(project, 1, turns={1: {"human_review": hold == "review"}})
    configure(project, notify={"idle_after": 1})
    if hold == "manual":
        path = project / "alfrd.yaml"
        data = yaml.safe_load(path.read_text())
        data["entrypoint"][0]["manual"] = True
        path.write_text(yaml.safe_dump(data))
    folder = scheduler.create_plan(project)
    scheduler.spawn_runner(folder)
    try:
        assert wait(lambda: folder.units() and (folder.units()[0].get("review_status") == "pending"
                                               if hold == "review" else folder.units()[0].get("manual")))
        # Startup can legitimately be silent for >1 s under load. Exercise only the hold.
        before = [e for e in events(folder) if e["kind"] == "turn.idle"]
        time.sleep(1.4)
        assert [e for e in events(folder) if e["kind"] == "turn.idle"] == before
        unit = folder.units()[0]
        if hold == "review":
            text = Path(unit["handoff"]["response_file"]).read_text()
            approve_response(project, folder.id, unit["id"], text, sha256(text))
        else:
            submit_response(project, folder.id, unit["id"], RESPONSE)
        assert wait(lambda: folder.load()["status"] == "finished")
    finally:
        if folder.load()["status"] == "running":
            scheduler.control(project, folder.id, "cancel")
    assert [e for e in events(folder) if e["kind"] == "turn.idle"] == before
