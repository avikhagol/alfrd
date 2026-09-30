"""Concurrent rows with execution.serialize_on (scheduler.pick_rows + live runs with the fake avica)."""

from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path

import pytest

from alfrd.execution import ExecutionError, load_execution
from alfrd.runtime import plan_csv as pc
from alfrd.runtime import scheduler

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")

FAKE_BIN = Path(__file__).parent / "fixtures" / "bin"
STEPS = ["preprocess_fitsidi", "fits_to_ms"]
COLS = dict(key_column="TARGET_NAME", files_column="FILENAMES", code_column="PROJECT_CODE", workdir_column="WORKDIR")


def _table(tmp_path, rows):
    path = tmp_path / "plan.csv"
    pc.create(path, rows, STEPS, STEPS, **COLS)
    return pc.read(path, STEPS, **COLS)


def _pick(table, *, limit=3, names=("target", "files"), match="all", running=(), started=(), base=None, locks=()):
    return scheduler.pick_rows(table, mode="step", on_failure="stop_target", limit=limit,
                               free=limit - len(running), names=list(names), match=match, base=base,
                               running_rows=running, started_rows=started, locks=locks)


def _keys(picked):
    return [row.key for row, _spec in picked.start]


def test_same_target_and_shared_file_serialize_with_match_all(tmp_path):
    table = _table(tmp_path, [
        {"target": "J0742+103", "files": "bv019a.idifits", "code": "BV019"},
        {"target": "J0742+103", "files": "bv019a.idifits,x.idifits", "code": "RDV41"},  # same target AND a shared file
        {"target": "J0742+103", "files": "other.idifits", "code": "EG01"},                # same target, other files
        {"target": "J0741+100", "files": "bv019a.idifits"},                              # other target, shared file
    ])
    picked = _pick(table)
    assert _keys(picked) == ["J0742+103@BV019", "J0742+103@EG01", "J0741+100"]
    assert picked.waiting == [{"row": "J0742+103@RDV41", "target": "J0742+103", "code": "RDV41",
                               "reason": "waiting: same target and shares bv019a.idifits with J0742+103 (BV019)",
                               "blocked_by": "J0742+103@BV019"}]


def test_the_avica_rule_is_shared_fits_files_only(tmp_path):
    """serialize_on: [files] (the avica template): a shared FITS name serializes, the target alone doesn't."""
    table = _table(tmp_path, [
        {"target": "J0742+103", "files": "bv019a.idifits", "code": "BV019"},
        {"target": "J0742+103", "files": "rdv41.idifits", "code": "RDV41"},   # same target, other file: parallel
        {"target": "J0741+100", "files": "bv019a.idifits"},                   # other target, same file: waits
    ])
    picked = _pick(table, names=["files"])
    assert _keys(picked) == ["J0742+103@BV019", "J0742+103@RDV41"]
    assert [(w["row"], w["reason"]) for w in picked.waiting] == [("J0741+100", "waiting: shares bv019a.idifits with J0742+103 (BV019)")]


def test_match_any_serializes_on_either_value(tmp_path):
    table = _table(tmp_path, [
        {"target": "A", "files": "a.idifits"},
        {"target": "A", "files": "b.idifits", "code": "X"},   # same target
        {"target": "B", "files": "./a.idifits"},              # shares a.idifits ("./" normalised)
        {"target": "C", "files": "c.idifits"},
    ])
    picked = _pick(table, match="any")
    assert _keys(picked) == ["A", "C"], "a non-conflicting row skips ahead of the blocked ones"
    reasons = {w["row"]: w["reason"] for w in picked.waiting}
    assert reasons == {"A@X": "waiting: same target with A", "B": "waiting: shares a.idifits with A"}


def test_files_resolve_to_the_same_path(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "a.idifits").write_text("x")
    table = _table(tmp_path, [{"target": "A", "files": "data/a.idifits"}, {"target": "B", "files": "./data/../data/a.idifits"}])
    assert _keys(_pick(table, names=["files"], base=tmp_path)) == ["A"]


def test_rows_hold_their_keys_between_steps_and_keep_csv_order(tmp_path):
    table = _table(tmp_path, [
        {"target": "A", "files": "a.idifits", "code": "C1"},
        {"target": "A", "files": "a.idifits", "code": "C2"},
        {"target": "A", "files": "a.idifits", "code": "C3"},
    ])
    # A@C1 ran its first step and is between steps: it still holds A / a.idifits.
    picked = _pick(table, started={"A@C1"}, limit=2)
    assert _keys(picked) == ["A@C1"]
    # While A@C1 runs, C2 waits for it and C3 stays behind C2 (CSV order).
    picked = _pick(table, running={"A@C1"}, started={"A@C1"}, limit=3)
    assert _keys(picked) == []
    assert [w["row"] for w in picked.waiting] == ["A@C2", "A@C3"]
    # A row with nothing left to run releases its keys.
    path = table.path
    pc.update(path, tmp_path / "lock", STEPS, {("A@C1", s): pc.DONE for s in STEPS}, **COLS)
    assert _keys(_pick(pc.read(path, STEPS, **COLS), started={"A@C1"}, limit=3)) == ["A@C2"]


def test_empty_serialize_on_and_concurrency_one_change_nothing(tmp_path):
    rows = [{"target": "A", "files": "a.idifits"}, {"target": "A", "files": "a.idifits", "code": "X"}]
    table = _table(tmp_path, rows)
    assert _keys(_pick(table, names=[])) == ["A", "A@X"]
    one = _pick(table, limit=1)
    assert _keys(one) == ["A"] and one.waiting == []


def test_serialize_on_is_validated_and_comes_from_the_template(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    (root / "alfrd.yaml").write_text("version: 1\nname: p\ntemplate: avica\n")
    cfg = load_execution(root)
    assert cfg.settings["serialize_on"] == ["files"] and cfg.settings["serialize_match"] == "all"
    (root / "alfrd.yaml").write_text("version: 1\nname: p\ntemplate: avica\nexecution: {serialize_on: [], serialize_match: any}\n")
    assert load_execution(root).settings["serialize_on"] == []
    (root / "alfrd.yaml").write_text("version: 1\nname: p\ntemplate: avica\nexecution: {serialize_match: some}\n")
    with pytest.raises(ExecutionError):
        load_execution(root)


# ---------------------------------------------------------------------------
# Live runs (fake avica, FAKE_AVICA_HOLD keeps steps running)


@pytest.fixture()
def project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "alfrd.yaml").write_text(
        "version: 1\nname: proj\ntemplate: avica\nexecution: {concurrency: 3}\n"
        "workflows:\n  - name: avica\n    steps: [preprocess_fitsidi, fits_to_ms]\n"
    )
    (root / "avica.inp").write_text("target_dir = reductions\n")
    fake = FAKE_BIN / "avica"
    if not os.access(fake, os.X_OK):
        fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{FAKE_BIN}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("FAKE_AVICA_SLEEP", "0.05")
    for key in ("FAKE_AVICA_FAIL", "FAKE_AVICA_NAMES", "FAKE_AVICA_EXIT"):
        monkeypatch.delenv(key, raising=False)
    return root


def _write_plan(root, rows):
    cfg = load_execution(root)
    pc.create(cfg.plan_csv, rows, cfg.step_ids, cfg.step_ids, key_column=cfg.key_column, files_column=cfg.files_column,
              code_column=cfg.code_column, workdir_column=cfg.workdir_column)


def _cells(root):
    cfg = load_execution(root)
    return {r.key: {s: r.cell(s) for s in STEPS} for r in scheduler.table_for(cfg, cfg.plan_csv).rows}


def _wait(predicate, timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_non_conflicting_rows_overlap_while_a_conflicting_one_waits(project, monkeypatch, tmp_path):
    _write_plan(project, [
        {"target": "T1", "files": "a.idifits", "code": "C1"},
        {"target": "T1", "files": "a.idifits", "code": "C2"},   # conflicts with T1@C1
        {"target": "T2", "files": "b.idifits"},
    ])
    hold = tmp_path / "hold"
    hold.write_text("")
    monkeypatch.setenv("FAKE_AVICA_HOLD", str(hold))
    folder = scheduler.create_plan(project)
    scheduler.spawn_runner(folder)
    try:
        assert _wait(lambda: _cells(project)["T1@C1"]["preprocess_fitsidi"] == "running"
                     and _cells(project)["T2"]["preprocess_fitsidi"] == "running")
        assert _wait(lambda: [w["row"] for w in folder.load().get("waiting") or []] == ["T1@C2"])
        assert _cells(project)["T1@C2"]["preprocess_fitsidi"] == "todo"
        status = scheduler.plan_status(project, folder.id)
        assert status["waiting"][0]["reason"] == "waiting: shares a.idifits with T1 (C1)"
    finally:
        hold.unlink()
    assert _wait(lambda: folder.load()["status"] == "finished", timeout=40)
    assert all(v == "done" for row in _cells(project).values() for v in row.values())
    units = {u["id"]: u for u in folder.units()}
    c1_end = max(u["finished"] for u in units.values() if u["row"] == "T1@C1")
    c2_start = min(u["started"] for u in units.values() if u["row"] == "T1@C2")
    assert c2_start >= c1_end, "T1@C2 starts only after T1@C1 ran all its steps"
    assert folder.load()["waiting"] == []


def test_preview_counts_conflict_groups(project):
    _write_plan(project, [
        {"target": "T1", "files": "a.idifits", "code": "C1"},
        {"target": "T1", "files": "a.idifits", "code": "C2"},
        {"target": "T2", "files": "b.idifits"},
        {"target": "T3", "files": "c.idifits"},
    ])
    result = scheduler.preview(project)
    assert result["conflicts"]["group_count"] == 3 and result["conflicts"]["groups"] == [["T1@C1", "T1@C2"]]
    assert result["conflicts"]["text"] == "4 row(s), 3 conflict group(s), up to 3 at once"
    assert scheduler.preview(project, concurrency=1)["conflicts"]["group_count"] == 4


def test_reconcile_keeps_the_hold_after_a_runner_kill(project, monkeypatch, tmp_path):
    _write_plan(project, [
        {"target": "T1", "files": "a.idifits", "code": "C1"},
        {"target": "T1", "files": "a.idifits", "code": "C2"},
    ])
    hold = tmp_path / "hold"
    hold.write_text("")
    monkeypatch.setenv("FAKE_AVICA_HOLD", str(hold))
    folder = scheduler.create_plan(project)
    scheduler.spawn_runner(folder)
    try:
        assert _wait(lambda: any(u.get("status") == "running" and u.get("pid") for u in folder.units()))
        assert _wait(lambda: (folder.load().get("runner") or {}).get("pid") is not None)
        os.kill(folder.load()["runner"]["pid"], signal.SIGKILL)
        assert _wait(lambda: not folder.runner_alive(), timeout=10)
        actions = scheduler.reconcile(project)
        assert actions and actions[0]["action"] == "runner started"
        assert _wait(lambda: folder.runner_alive() and (folder.load().get("runner") or {}).get("started"), timeout=10)
        time.sleep(1.5)  # several scheduling passes of the new runner
        assert _cells(project)["T1@C2"]["preprocess_fitsidi"] == "todo"
        assert [w["row"] for w in scheduler.plan_status(project, folder.id)["waiting"]] == ["T1@C2"]
    finally:
        hold.unlink()
    assert _wait(lambda: folder.load()["status"] == "finished", timeout=40)
    assert all(v == "done" for row in _cells(project).values() for v in row.values())


def test_rows_mid_way_never_block_each_other(tmp_path):
    """Two conflicting rows that both ran before (e.g. after Retry) must not deadlock."""
    table = _table(tmp_path, [
        {"target": "J1", "files": "a.idifits", "code": "P1"},
        {"target": "J1", "files": "a.idifits", "code": "P2"},
        {"target": "J1", "files": "a.idifits", "code": "P3"},
    ])
    picked = _pick(table, started={"J1@P1", "J1@P2"}, limit=2)
    assert _keys(picked) == ["J1@P1"], "the earliest mid-way row goes first"
    assert [w["row"] for w in picked.waiting] == ["J1@P2", "J1@P3"]
    # Only the later row ran before: it continues; the earlier, unstarted one waits for it.
    picked = _pick(table, started={"J1@P2"}, limit=2)
    assert _keys(picked) == ["J1@P2"]
    assert {w["row"]: w["blocked_by"] for w in picked.waiting} == {"J1@P1": "J1@P2", "J1@P3": "J1@P2"}
    # Whatever is marked started, something always starts while nothing runs.
    import itertools

    keys = [r.key for r in table.rows]
    for n in range(len(keys) + 1):
        for combo in itertools.combinations(keys, n):
            assert _keys(_pick(table, started=set(combo), limit=3)), combo


def test_a_shared_work_dir_does_not_serialize_under_a_declared_rule(tmp_path):
    """Targets of one project code with their own FITS files run side by side (serialize_on: [files])."""
    table = _table(tmp_path, [
        {"target": "A", "files": "a.idifits", "code": "RDV41"},
        {"target": "B", "files": "b.idifits", "code": "RDV41"},
    ])
    assert _keys(_pick(table, names=["files"], locks={"RDV41/"})) == ["A@RDV41", "B@RDV41"]
    # No rule declared: rows of a work dir in use still wait (the safety net).
    picked = _pick(table, names=[], locks={"RDV41/"})
    assert _keys(picked) == [] and {w["reason"] for w in picked.waiting} == {"waiting: work dir RDV41/ is in use"}
