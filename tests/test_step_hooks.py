"""Plugin step hooks (alfrd.extensions.StepHooks) called by the plan runner."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest
from test_extensions import fake_plugins  # noqa: F401
from test_plan_execution import STEPS, _cells, _plan, project  # noqa: F401

from alfrd import extensions as ext
from alfrd.events import EVENTS_FILE
from alfrd.extensions import Plugin, StepContext, StepHooks
from alfrd.extensions import step_hooks as sh
from alfrd.runtime import scheduler

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")


class Recorder:
    """Fake plugin hooks: records every call, with the unit file and plan cells seen by ``before``."""

    def __init__(self, root: Path, plan_id: str, before=None, after=None):
        self.root, self.plan_id, self.calls = root, plan_id, []
        self._before, self._after = before, after

    def before(self, ctx: StepContext):
        unit = json.loads((self.root / ".alfrd" / "plans" / self.plan_id / "units" / f"{ctx.unit_id}.json").read_text())
        self.calls.append(("before", ctx, {"unit_status": unit["status"], "pid": unit.get("pid"),
                                           "cells": _cells(self.root)}))
        if self._before:
            return self._before(ctx)
        return {"token": ctx.unit_id}

    def after(self, ctx: StepContext, state):
        self.calls.append(("after", ctx, state))
        if self._after:
            self._after(ctx, state)

    def hooks(self, timeout=5.0):
        return [("rec", StepHooks(before=self.before, after=self.after, timeout=timeout))]


def _events(folder, kind):
    path = folder.path / EVENTS_FILE
    return [e for e in map(json.loads, path.read_text().splitlines()) if e["kind"] == kind] if path.exists() else []


def test_manifest_field_and_exports():
    hooks = StepHooks(before=lambda ctx: None, timeout=2)
    plugin = Plugin(id="h", version="1", step_hooks=hooks)
    assert "step_hooks" in plugin.kinds and Plugin(id="x", version="1").step_hooks is None
    assert {"StepHooks", "StepContext"} <= set(ext.__all__)
    with pytest.raises(TypeError):
        Plugin(id="h", version="1", step_hooks={"before": print})
    with pytest.raises(TypeError):
        StepHooks(before="not callable")
    with pytest.raises(ValueError):
        StepHooks(timeout=0)
    ctx = StepContext(project_root="/p", plan_id="p1", unit_id="u1", mode="step", steps=["a"],
                      rows=[{"key": "T1", "target": "T1", "code": "", "workdir": ""}])
    assert json.loads(json.dumps(ctx.to_dict()))["steps"] == ["a"]


def test_hooks_run_around_launched_units_only(project, monkeypatch):  # noqa: F811
    _plan(project)
    monkeypatch.setenv("FAKE_AVICA_FAIL", "T2:fits_to_ms")   # T2's avica_avg ends up blocked
    folder = scheduler.create_plan(project)
    rec = Recorder(project, folder.id)
    monkeypatch.setattr(sh, "hooks", rec.hooks)
    assert scheduler.Runner(project, folder.id).run() == 0
    units = {u["id"]: u for u in folder.units()}
    assert len(units) == 5                                     # T1 ×3, T2 ×2 (avica_avg blocked: never launched)
    befores = [c for c in rec.calls if c[0] == "before"]
    afters = [c for c in rec.calls if c[0] == "after"]
    assert {c[1].unit_id for c in befores} == {c[1].unit_id for c in afters} == set(units)
    for unit_id in units:                                      # before precedes after, once each
        kinds = [c[0] for c in rec.calls if c[1].unit_id == unit_id]
        assert kinds == ["before", "after"]
    # before: unit recorded, its cell running, command not spawned yet; no outcome fields.
    for _, ctx, seen in befores:
        (row,) = ctx.rows
        assert seen["unit_status"] == "running" and seen["pid"] is None
        assert seen["cells"][row["key"]][ctx.steps[0]] == "running"
        assert ctx.status is None and ctx.log_path is None and ctx.cells == {}
        assert ctx.project_root == str(project.resolve()) and ctx.plan_id == folder.id and ctx.mode == "step"
    # after: the outcome, the before-state, and the final cells.
    for _, ctx, state in afters:
        unit = units[ctx.unit_id]
        assert state == {"token": ctx.unit_id} and not ctx.readopted
        assert ctx.status == unit["status"] and ctx.exit_code == unit["exit_code"] and ctx.error == unit["error"]
        assert Path(ctx.log_path).is_absolute() and Path(ctx.log_path).is_file()
        assert ctx.duration_s is not None and ctx.duration_s >= 0 and ctx.started and ctx.finished
        (row,) = ctx.rows
        assert ctx.cells == {row["key"]: {ctx.steps[0]: _cells(project)[row["key"]][ctx.steps[0]]}}
    failed = next(c[1] for c in afters if c[1].status == "failed")
    assert failed.rows[0]["target"] == "T2" and failed.steps == ("fits_to_ms",) and failed.error == "boom"
    assert _cells(project)["T2"]["avica_avg"] == "blocked"


def test_skipped_rows_and_no_plugins_mean_no_calls(project, monkeypatch):  # noqa: F811
    _plan(project, targets=("T1",))
    cfg_csv = project / "alfrd.plan.csv"
    if cfg_csv.exists():
        cfg_csv.write_text(cfg_csv.read_text().replace("todo", "skip"))
    else:  # the plan CSV path comes from the template
        from alfrd.execution import load_execution

        path = load_execution(project).plan_csv
        path.write_text(path.read_text().replace("todo", "skip"))
    folder = scheduler.create_plan(project)
    rec = Recorder(project, folder.id)
    monkeypatch.setattr(sh, "hooks", rec.hooks)
    scheduler.Runner(project, folder.id).run()
    assert folder.units() == [] and rec.calls == []


def test_exceptions_and_timeouts_never_change_the_step(project, monkeypatch, capsys):  # noqa: F811
    _plan(project, targets=("T1",), steps=["preprocess_fitsidi", "fits_to_ms"])
    folder = scheduler.create_plan(project)

    def boom(ctx):
        raise RuntimeError("sheet\nexploded")

    def hang(ctx, state):
        time.sleep(5)

    rec = Recorder(project, folder.id, before=boom, after=hang)
    monkeypatch.setattr(sh, "hooks", lambda: rec.hooks(timeout=0.3))
    started = time.time()
    scheduler.Runner(project, folder.id).run()
    assert time.time() - started < 4, "a hanging hook is abandoned after its timeout"
    assert _cells(project)["T1"] == {"preprocess_fitsidi": "done", "fits_to_ms": "done", "avica_avg": "skip"}
    assert all(u["status"] == "done" for u in folder.units())
    assert all(state is None for kind, _, state in rec.calls if kind == "after"), "a failed before gives None"
    failed = _events(folder, "plugin.hook_failed")
    assert {(e["data"]["phase"], e["data"]["plugin"]) for e in failed} == {("before", "rec"), ("after", "rec")}
    assert any(e["data"]["error"] == "RuntimeError: sheet exploded" for e in failed)
    assert any("timed out after 0.3 s" in e["data"]["error"] for e in failed)
    out = capsys.readouterr().out
    assert "plugin rec before hook failed: RuntimeError: sheet exploded" in out and "Traceback" not in out


def test_readopted_unit_gets_after_with_none_state(project, monkeypatch):  # noqa: F811
    _plan(project, targets=("T1",), steps=["preprocess_fitsidi"])
    monkeypatch.setenv("FAKE_AVICA_SLEEP", "1")
    folder = scheduler.create_plan(project)
    first = scheduler.Runner(project, folder.id)
    rec = Recorder(project, folder.id)
    monkeypatch.setattr(sh, "hooks", rec.hooks)
    assert first.schedule() == 1                              # launched by a runner that then goes away
    (unit,) = folder.units()
    assert [c[0] for c in rec.calls] == ["before"]
    rec.calls.clear()
    second = scheduler.Runner(project, folder.id)             # a new runner re-adopts the unit
    second.run()
    first.live[unit["id"]].process.wait()
    assert [c[0] for c in rec.calls] == ["after"]
    _, ctx, state = rec.calls[0]
    assert ctx.unit_id == unit["id"] and state is None and ctx.readopted and ctx.status == "done"


def test_cannot_start_unit_gets_no_hooks(project, monkeypatch):  # noqa: F811
    _plan(project, targets=("T1",), steps=["preprocess_fitsidi"])
    (project / "alfrd.yaml").write_text(
        "version: 1\nname: proj\ntemplate: avica\n"
        "workflows:\n  - name: avica\n    steps:\n      - {id: preprocess_fitsidi, cmd: ['{missing_value}']}\n"
        "      - fits_to_ms\n      - avica_avg\n")
    folder = scheduler.create_plan(project)
    rec = Recorder(project, folder.id)
    monkeypatch.setattr(sh, "hooks", rec.hooks)
    scheduler.Runner(project, folder.id).run()
    assert [u["status"] for u in folder.units()] == ["failed"] and rec.calls == []


GOOD_HOOKS = """
from alfrd.extensions import Plugin, StepHooks
plugin = Plugin(id="hooky", version="1", step_hooks=StepHooks(before=lambda ctx: "state", after=lambda ctx, s: None))
"""


def test_safe_mode_and_disabled_plugins_have_no_hooks(fake_plugins, monkeypatch):  # noqa: F811
    fake_plugins("hooky", GOOD_HOOKS)
    ext.reset()
    assert [pid for pid, _ in sh.hooks()] == ["hooky"]
    ext.set_enabled("hooky", False)
    assert sh.hooks() == [], "disabling applies without a restart"
    ext.set_enabled("hooky", True)
    assert [pid for pid, _ in sh.hooks()] == ["hooky"]
    monkeypatch.setenv("ALFRD_NO_PLUGINS", "1")
    ext.reset()
    assert sh.hooks() == []


def test_call_helpers_isolate_each_plugin():
    calls = []

    def bad(ctx):
        raise SystemExit(3)

    def good(ctx):
        ctx.rows[0]["target"] = "changed"           # a hook's copy; the next hook sees the original
        calls.append(ctx.rows[0]["target"])
        return 7

    def seen(ctx):
        calls.append(ctx.rows[0]["target"])

    ctx = StepContext(project_root="/p", plan_id="p", unit_id="u", mode="step", steps=["s"],
                      rows=[{"key": "T", "target": "T", "code": "", "workdir": ""}])
    active = [("a", StepHooks(before=bad)), ("b", StepHooks(before=good)), ("c", StepHooks(before=seen)),
              ("d", StepHooks(after=lambda c, s: None))]
    states, failures = sh.call_before(ctx, active)
    assert states == {"a": None, "b": 7, "c": None} and calls == ["changed", "T"]
    assert [(f.plugin, f.phase, f.error) for f in failures] == [("a", "before", "SystemExit: 3")]
    assert sh.call_after(ctx, states, active) == []
