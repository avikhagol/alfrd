"""Connect the real runner hooks to a fake sheet, including units that never launch."""

from __future__ import annotations

import importlib
import json
import sys

import pytest
import yaml
from test_core import SID, FakeClient

from alfrd.events import EVENTS_FILE
from alfrd.extensions import StepHooks, step_hooks
from alfrd.runtime import scheduler
from tests.test_plan_execution import (  # noqa: F401 - shared scratch fixture
    STEPS,
    _plan,
    project,
)

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")


def engine(gsheet, root):
    mapping = {"version": 1, "spreadsheet_id": SID, "worksheet": "Targets", "rows": {"key_column": "TARGET_NAME"},
               "outbound": [{"step": "*", "column": "{step}", "field": "status"}]}
    (root / "alfrd.gsheet.yaml").write_text(yaml.safe_dump(mapping))
    client = FakeClient([["TARGET_NAME", *STEPS], ["T1"], ["T2"]])
    return gsheet.sync.Sync(client), client


def attach(monkeypatch, instance):
    monkeypatch.setattr(step_hooks, "hooks", lambda: [("gsheet", StepHooks(instance.before, instance.after, timeout=5))])


def test_skipped_plan_has_zero_client_calls(gsheet, project, monkeypatch):  # noqa: F811
    path = _plan(project)
    path.write_text(path.read_text().replace("todo", "skip"))
    folder = scheduler.create_plan(project)
    instance, client = engine(gsheet, project)
    attach(monkeypatch, instance)
    scheduler.Runner(project, folder.id).run()
    assert folder.units() == []
    assert client.calls == []


def test_blocked_step_has_zero_calls_and_launched_steps_sync(gsheet, project, monkeypatch):  # noqa: F811
    _plan(project)
    monkeypatch.setenv("FAKE_AVICA_FAIL", "T2:fits_to_ms")
    folder = scheduler.create_plan(project)
    instance, client = engine(gsheet, project)
    attach(monkeypatch, instance)
    scheduler.Runner(project, folder.id).run()
    assert len([c for c in client.calls if c[0] == "get"]) == 5  # 3 T1 + 2 T2
    assert "'Targets'!D3" not in [d["range"] for _, data, _ in client.writes for d in data]
    entries = [json.loads(line) for line in (project / ".alfrd/gsheet/sync.jsonl").read_text().splitlines()]
    assert len(entries) == 5
    assert all(entry["result"] == "ok" for entry in entries)


def test_command_build_failure_has_zero_client_calls(gsheet, project, monkeypatch):  # noqa: F811
    _plan(project, targets=("T1",), steps=["preprocess_fitsidi"])
    (project / "alfrd.yaml").write_text(
        "version: 1\nname: proj\ntemplate: avica\n"
        "workflows:\n  - name: avica\n    steps:\n      - {id: preprocess_fitsidi, cmd: ['{missing_value}']}\n"
        "      - fits_to_ms\n      - avica_avg\n")
    folder = scheduler.create_plan(project)
    instance, client = engine(gsheet, project)
    attach(monkeypatch, instance)
    scheduler.Runner(project, folder.id).run()
    assert [u["status"] for u in folder.units()] == ["failed"]
    assert client.calls == []


def test_client_failure_logged_once_and_step_still_succeeds(gsheet, project, monkeypatch, capsys):  # noqa: F811
    _plan(project, targets=("T1",), steps=["preprocess_fitsidi"])
    folder = scheduler.create_plan(project)
    instance, client = engine(gsheet, project)
    client.error = RuntimeError("HTTP body contains FAKE-SECRET-KEY")
    attach(monkeypatch, instance)
    scheduler.Runner(project, folder.id).run()
    assert [u["status"] for u in folder.units()] == ["done"]
    events = [e for e in map(json.loads, (folder.path / EVENTS_FILE).read_text().splitlines())
              if e["kind"] == "plugin.hook_failed"]
    assert len(events) == 1 and events[0]["data"]["plugin"] == "gsheet"
    log = capsys.readouterr().out  # production redirects runner stdout to runner.log
    assert log.count("plugin gsheet before hook failed") == 1
    assert "FAKE-SECRET-KEY" not in log
    assert "FAKE-SECRET-KEY" not in (project / ".alfrd/gsheet/sync.jsonl").read_text()


def test_registered_manifest_hooks_sync_real_runner(gsheet, project, monkeypatch):  # noqa: F811
    from alfrd.extensions import settings as host_settings

    _plan(project, targets=("T1",), steps=["preprocess_fitsidi"])
    folder = scheduler.create_plan(project)
    _, client = engine(gsheet, project)
    settings = importlib.import_module("alfrd_gsheet.settings")
    manifest = importlib.import_module("alfrd_gsheet")
    monkeypatch.setattr(host_settings, "values", lambda _: {"value_input_option": "", "conflict_policy": ""})
    monkeypatch.setattr(settings, "make_client", lambda _: client)
    monkeypatch.setattr(step_hooks, "hooks", lambda: [("gsheet", manifest.plugin.step_hooks)])
    scheduler.Runner(project, folder.id).run()
    assert [u["status"] for u in folder.units()] == ["done"]
    assert len(client.writes) == 1
    assert client.writes[0][2] == "RAW"
    assert client.live["'Targets'!B2"] == "done"
