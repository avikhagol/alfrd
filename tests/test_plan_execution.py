"""Plan CSV execution: alfrd.yaml commands, result-CSV checks, surviving the runner.

Uses tests/fixtures/bin/avica, a fake `avica pipe run` that writes result CSV
rows (and exits 0 even when a step fails, like AVICA).
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path

import pytest

from alfrd.execution import ExecutionError, RenderError, load_execution, render
from alfrd.runtime import plan_csv as pc
from alfrd.runtime import scheduler

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")

FAKE_BIN = Path(__file__).parent / "fixtures" / "bin"
STEPS = ["preprocess_fitsidi", "fits_to_ms", "avica_avg"]


@pytest.fixture()
def project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "alfrd.yaml").write_text(
        "version: 1\nname: proj\ntemplate: avica\n"
        "workflows:\n  - name: avica\n    steps: [preprocess_fitsidi, fits_to_ms, avica_avg]\n"
    )
    (root / "avica.inp").write_text("target_dir = reductions\n")
    fake = FAKE_BIN / "avica"
    if not os.access(fake, os.X_OK):  # the executable bit can get lost when files are copied
        fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{FAKE_BIN}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("FAKE_AVICA_SLEEP", "0.05")
    for key in ("FAKE_AVICA_FAIL", "FAKE_AVICA_NAMES", "FAKE_AVICA_EXIT"):
        monkeypatch.delenv(key, raising=False)
    return root


def _plan(root: Path, targets=("T1", "T2"), steps=STEPS, files="a.idifits") -> Path:
    cfg = load_execution(root)
    return pc.create(cfg.plan_csv, [{"target": t, "files": files} for t in targets], cfg.step_ids, steps,
                     key_column=cfg.key_column, files_column=cfg.files_column,
                     code_column=cfg.code_column, workdir_column=cfg.workdir_column)


def _cells(root: Path) -> dict[str, dict[str, str]]:
    cfg = load_execution(root)
    table = scheduler.table_for(cfg, cfg.plan_csv)
    return {r.key: {s: r.cell(s) for s in table.steps} for r in table.rows}


def _wait(predicate, timeout=30.0, step=0.1):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return False


def test_execution_config_from_template_and_render(project):
    cfg = load_execution(project)
    assert cfg.step_ids == STEPS
    assert cfg.step("fits_to_ms").argv == ("avica", "pipe", "run", "--t", "{target}", "--f", "{FILENAMES}", "{step}")
    assert cfg.settings["status_from"] == "both"
    assert render(cfg.step("fits_to_ms").argv, {"target": "T1", "FILENAMES": "a,b", "step": "fits_to_ms"})[-3:] == ["--f", "a,b", "fits_to_ms"]
    with pytest.raises(RenderError) as info:
        render(cfg.step("fits_to_ms").argv, {"target": "T1", "step": "fits_to_ms"})
    assert info.value.missing == ["FILENAMES"]
    # alfrd.yaml wins: a step-level cmd, and an entrypoint of the same name.
    (project / "alfrd.yaml").write_text(
        "version: 1\nname: proj\ntemplate: avica\n"
        "entrypoint:\n  - {name: avica-step, cmd: [echo, '{target}', '{step}']}\n"
        "execution: {concurrency: 3}\n"
        "workflows:\n  - name: avica\n    steps:\n      - preprocess_fitsidi\n      - {id: fits_to_ms, cmd: [true], timeout: 5}\n"
    )
    cfg = load_execution(project)
    assert cfg.step("preprocess_fitsidi").argv == ("echo", "{target}", "{step}")
    assert cfg.step("fits_to_ms").argv == ("true",) and cfg.step("fits_to_ms").timeout == 5
    assert cfg.settings["concurrency"] == 3 and cfg.settings["mode"] == "step"
    (project / "alfrd.yaml").write_text("version: 1\nname: proj\ntemplate: avica\nentrypoint:\n  - {name: x, cmd: 'avica pipe run'}\n")
    with pytest.raises(ExecutionError, match="no shell"):
        load_execution(project)


def test_plan_csv_update_keeps_user_edits(tmp_path):
    path = tmp_path / "plan.csv"
    pc.create(path, [{"target": "A"}, {"target": "B", "code": "BV019"}], STEPS, STEPS[:2],
              key_column="TARGET_NAME", files_column="FILENAMES", code_column="PROJECT_CODE", workdir_column="WORKDIR")
    cols = dict(key_column="TARGET_NAME", files_column="FILENAMES", code_column="PROJECT_CODE", workdir_column="WORKDIR")
    table = pc.read(path, STEPS, **cols)
    assert [r.key for r in table.rows] == ["A", "B@BV019"]
    # The user switches B's first step to skip; ALFRD's later update must not undo it.
    path.write_text(path.read_text().replace("B,,BV019,,todo", "B,,BV019,,skip"))
    pc.update(path, tmp_path / "lock", STEPS, {("B@BV019", "preprocess_fitsidi"): pc.RUNNING, ("A", "preprocess_fitsidi"): pc.DONE},
              {("A", "WORKDIR"): "wd_1"}, only_if={("B@BV019", "preprocess_fitsidi"): {pc.TODO}}, **cols)
    table = pc.read(path, STEPS, **cols)
    assert table.row("B@BV019").cell("preprocess_fitsidi") == "skip"
    assert table.row("A").cell("preprocess_fitsidi") == pc.DONE
    assert table.row("A").workdir == "wd_1"


def test_add_row_appends_and_rejects_duplicate(tmp_path):
    path = tmp_path / "plan.csv"
    cols = dict(key_column="TARGET_NAME", files_column="FILENAMES", code_column="PROJECT_CODE", workdir_column="WORKDIR")
    pc.create(path, [{"target": "A"}], STEPS, STEPS[:1], **cols)
    table = pc.add_row(path, tmp_path / "lock", STEPS, target="B", files="c.idifits", code="BV019",
                       selected=["fits_to_ms"], **cols)
    assert [r.key for r in table.rows] == ["A", "B@BV019"]
    row = table.row("B@BV019")
    assert row.files == "c.idifits"
    assert row.cell("fits_to_ms") == pc.TODO and row.cell("preprocess_fitsidi") == "skip"
    with pytest.raises(pc.DuplicateRowError):
        pc.add_row(path, tmp_path / "lock", STEPS, target="A", **cols)
    # A brand-new file (create=True) gets a header built from the given steps, all marked todo by default.
    new_path = tmp_path / "new.csv"
    pc.add_row(new_path, tmp_path / "lock2", STEPS, target="C", create=True, **cols)
    new_table = pc.read(new_path, STEPS, **cols)
    assert new_table.row("C").cell(STEPS[0]) == pc.TODO and new_table.row("C").cell(STEPS[-1]) == pc.TODO


PC_COLS = dict(key_column="TARGET_NAME", files_column="FILENAMES", code_column="PROJECT_CODE", workdir_column="WORKDIR")


def test_add_row_adds_a_missing_column_before_the_steps(tmp_path):
    path = tmp_path / "plan.csv"
    path.write_text("TARGET_NAME,FILENAMES,preprocess_fitsidi,fits_to_ms,avica_avg\nA,a.idifits,done,done,done\n")
    table = pc.add_row(path, tmp_path / "lock", STEPS, target="A", code="BV019", **PC_COLS)
    assert table.header == ["TARGET_NAME", "FILENAMES", "PROJECT_CODE", *STEPS]
    assert [r.key for r in table.rows] == ["A", "A@BV019"]
    assert table.row("A").code == "" and table.row("A").cell("avica_avg") == pc.DONE
    assert [r.key for r in pc.read(path, STEPS, **PC_COLS).rows] == ["A", "A@BV019"]
    # Same target, no code: would be read back as A#2, so it is a duplicate.
    with pytest.raises(pc.DuplicateRowError):
        pc.add_row(path, tmp_path / "lock", STEPS, target="A", **PC_COLS)


def test_add_row_never_creates_a_file_by_default(tmp_path):
    with pytest.raises(FileNotFoundError):
        pc.add_row(tmp_path / "missing.csv", tmp_path / "lock", STEPS, target="A", **PC_COLS)
    assert not (tmp_path / "missing.csv").exists()


@pytest.mark.parametrize("name", ["#A", "A@B", "  ", "# note"])
def test_add_row_rejects_bad_target_names(tmp_path, name):
    path = tmp_path / "plan.csv"
    pc.create(path, [{"target": "A"}], STEPS, STEPS, **PC_COLS)
    before = path.read_text()
    with pytest.raises(ValueError):
        pc.add_row(path, tmp_path / "lock", STEPS, target=name, **PC_COLS)
    assert path.read_text() == before


def test_join_files_accepts_strings_and_lists():
    assert pc.join_files("a.idifits, b.idifits\nc.idifits  d.idifits") == "a.idifits,b.idifits,c.idifits,d.idifits"
    assert pc.join_files(["a.idifits", " b.idifits,c.idifits ", ""]) == "a.idifits,b.idifits,c.idifits"
    assert pc.join_files(None) == ""


def test_scheduler_add_row_uses_the_given_step_list(project):
    _plan(project, targets=("T1",))
    cfg = load_execution(project)
    table = scheduler.add_row(cfg, cfg.plan_csv, target="T2", steps=STEPS[:2])
    # avica_avg is a column in the file but not one of the plan's steps: left empty.
    assert {s: table.row("T2").cell(s) for s in STEPS} == {STEPS[0]: pc.TODO, STEPS[1]: pc.TODO, STEPS[2]: "skip"}
    assert table.steps == STEPS[:2]


def test_cli_add_row(project, monkeypatch):
    from typer.testing import CliRunner

    from alfrd.cli import alfrd_cli

    runner = CliRunner()
    monkeypatch.chdir(project.parent)
    missing = runner.invoke(alfrd_cli, ["plan", "add-row", "--root", str(project), "--target", "T1"])
    assert missing.exit_code == 1 and "does not exist" in missing.output
    assert not load_execution(project).plan_csv.exists()
    _plan(project, targets=("T1",))
    name = load_execution(project).plan_csv.name
    ok = runner.invoke(alfrd_cli, ["plan", "add-row", name, "--root", str(project), "--target", "T2",
                                   "--filenames", "b.idifits c.idifits", "--steps", "fits_to_ms"])
    assert ok.exit_code == 0, ok.output
    cells = _cells(project)["T2"]
    assert cells == {STEPS[0]: "skip", STEPS[1]: pc.TODO, STEPS[2]: "skip"}
    cfg = load_execution(project)
    assert scheduler.table_for(cfg, cfg.plan_csv).row("T2").files == "b.idifits,c.idifits"
    bad = runner.invoke(alfrd_cli, ["plan", "add-row", "--root", str(project), "--target", "#T3"])
    assert bad.exit_code == 1 and "comment" in bad.output


def test_added_row_is_picked_up_while_the_plan_runs(project, monkeypatch, tmp_path):
    _plan(project, targets=("T1",))
    hold = tmp_path / "hold"
    hold.write_text("")
    monkeypatch.setenv("FAKE_AVICA_HOLD", str(hold))  # T1's first step waits while this exists
    folder = scheduler.create_plan(project)
    scheduler.spawn_runner(folder)
    try:
        assert _wait(lambda: _cells(project)["T1"]["preprocess_fitsidi"] == "running")
        cfg = load_execution(project)
        scheduler.add_row(cfg, cfg.plan_csv, target="T2", files="b.idifits")
        assert folder.load()["status"] == "running"
    finally:
        hold.unlink()
    assert _wait(lambda: folder.load()["status"] == "finished", timeout=40)
    assert _cells(project)["T2"] == {s: "done" for s in STEPS}


def test_dry_run_lists_commands_and_missing_values(project):
    _plan(project, targets=("T1",), files="")
    result = scheduler.preview(project)
    assert [u["steps"] for u in result["units"]] == [[s] for s in STEPS]
    assert all("FILENAMES" in u["missing"] for u in result["units"])
    _plan_path = _plan(project, targets=("T1",))
    result = scheduler.preview(project, _plan_path, mode="target")
    assert result["units"][0]["argv"][-2:] == ["--resume-from", "preprocess_fitsidi"]


def test_runs_steps_and_trusts_the_result_csv_over_exit_code(project, monkeypatch):
    _plan(project)
    monkeypatch.setenv("FAKE_AVICA_FAIL", "T2:fits_to_ms")  # fake avica still exits 0
    folder = scheduler.create_plan(project)
    assert scheduler.Runner(project, folder.id).run() == 0
    cells = _cells(project)
    assert cells["T1"] == {s: "done" for s in STEPS}
    assert cells["T2"] == {"preprocess_fitsidi": "done", "fits_to_ms": "failed", "avica_avg": "blocked"}
    plan = folder.load()
    assert plan["status"] == "finished"
    failed = [u for u in folder.units() if u["status"] == "failed"]
    assert len(failed) == 1 and failed[0]["exit_code"] == 0 and failed[0]["error"] == "boom"
    log = (project / failed[0]["log"]).read_text()
    assert "$ avica pipe run --t T2 --f a.idifits fits_to_ms" in log and "exit code 0" in log
    # Retry: failed / blocked cells back to todo, the fixed step passes.
    monkeypatch.delenv("FAKE_AVICA_FAIL")
    folder = scheduler.create_plan(project, retry_failed=True)
    scheduler.Runner(project, folder.id).run()
    assert _cells(project)["T2"] == {s: "done" for s in STEPS}


@pytest.mark.parametrize("names", ["new", "single"])  # result__T__CODE__wd.csv / result_T_CODE_wd.csv
def test_target_mode_and_new_result_names_fill_code_and_workdir(project, monkeypatch, names):
    _plan(project, targets=("T1",))
    monkeypatch.setenv("FAKE_AVICA_NAMES", names)
    monkeypatch.setenv("FAKE_AVICA_CODE", "RDV41")
    monkeypatch.setenv("FAKE_AVICA_WD", "wd_1")
    monkeypatch.setenv("FAKE_AVICA_FAIL", "avica_avg")
    (project / "reductions" / "RDV41" / "wd_1").mkdir(parents=True)
    folder = scheduler.create_plan(project, mode="target")
    scheduler.Runner(project, folder.id).run()
    cfg = load_execution(project)
    row = scheduler.table_for(cfg, cfg.plan_csv).rows[0]
    assert {s: row.cell(s) for s in STEPS} == {"preprocess_fitsidi": "done", "fits_to_ms": "done", "avica_avg": "failed"}
    assert (row.code, row.workdir) == ("RDV41", "wd_1")
    (unit,) = folder.units()
    assert unit["argv"][-2:] == ["--resume-from", "preprocess_fitsidi"]


def test_user_skips_a_row_while_the_plan_runs(project, monkeypatch):
    _plan(project)
    monkeypatch.setenv("FAKE_AVICA_SLEEP", "0.4")
    folder = scheduler.create_plan(project)
    scheduler.spawn_runner(folder)
    assert _wait(lambda: _cells(project)["T1"]["preprocess_fitsidi"] == "running")
    cfg = load_execution(project)
    text = cfg.plan_csv.read_text().replace("T2,a.idifits,,,todo,todo,todo", "T2,a.idifits,,,skip,skip,skip")
    cfg.plan_csv.write_text(text)
    assert _wait(lambda: folder.load()["status"] == "finished", timeout=40)
    assert _cells(project)["T2"] == {s: "skip" for s in STEPS}
    assert _cells(project)["T1"] == {s: "done" for s in STEPS}


def test_command_survives_runner_kill_and_is_readopted(project, monkeypatch):
    _plan(project, targets=("T1",), steps=["preprocess_fitsidi", "fits_to_ms"])
    monkeypatch.setenv("FAKE_AVICA_SLEEP", "3")
    folder = scheduler.create_plan(project)
    scheduler.spawn_runner(folder)
    assert _wait(lambda: any(u.get("status") == "running" and u.get("pid") for u in folder.units()))
    runner_pid = None
    assert _wait(lambda: (folder.load().get("runner") or {}).get("pid") is not None)
    runner_pid = folder.load()["runner"]["pid"]
    unit = next(u for u in folder.units() if u["status"] == "running")
    os.kill(runner_pid, signal.SIGKILL)  # the runner (or the machine's ALFRD) dies
    assert _wait(lambda: not folder.runner_alive(), timeout=10)
    assert scheduler.pid_alive(unit["pid"], unit["proc_start"]), "the command must outlive its runner"
    actions = scheduler.reconcile(project)
    assert actions and actions[0]["action"] == "runner started" and unit["id"] in actions[0]["alive"]
    assert _wait(lambda: folder.load()["status"] == "finished", timeout=40)
    assert _cells(project)["T1"] == {"preprocess_fitsidi": "done", "fits_to_ms": "done", "avica_avg": "skip"}
    events = [h["event"] for h in folder.load()["history"]]
    assert any(e.startswith("re-adopted") for e in events)


def test_dead_runner_without_live_commands_marks_the_plan_interrupted(project, monkeypatch):
    _plan(project, targets=("T1",), steps=["preprocess_fitsidi"])
    monkeypatch.setenv("FAKE_AVICA_SLEEP", "2")
    folder = scheduler.create_plan(project)
    scheduler.spawn_runner(folder)
    assert _wait(lambda: any(u.get("status") == "running" and u.get("pid") for u in folder.units()))
    assert _wait(lambda: (folder.load().get("runner") or {}).get("pid") is not None)
    unit = next(u for u in folder.units() if u["status"] == "running")
    os.kill(folder.load()["runner"]["pid"], signal.SIGKILL)
    os.killpg(unit["pgid"], signal.SIGKILL)  # e.g. a reboot: everything is gone
    assert _wait(lambda: not scheduler.pid_alive(unit["pid"], unit["proc_start"]), timeout=10)
    # kill() returns before the runner has exited; it holds runner.lock until then,
    # and reconcile rightly leaves a plan alone while its runner looks alive.
    assert _wait(lambda: not folder.runner_alive(), timeout=10)
    actions = scheduler.reconcile(project)
    assert actions == [{"plan": folder.id, "action": "interrupted"}]
    assert _cells(project)["T1"]["preprocess_fitsidi"] == "interrupted"
    # Resume (with retry) runs it again.
    monkeypatch.setenv("FAKE_AVICA_SLEEP", "0.05")
    scheduler.control(project, folder.id, "resume", retry_failed=True)
    assert _wait(lambda: folder.load()["status"] == "finished", timeout=40)
    assert _cells(project)["T1"]["preprocess_fitsidi"] == "done"


def test_cancel_stops_the_running_command(project, monkeypatch):
    _plan(project, targets=("T1",))
    monkeypatch.setenv("FAKE_AVICA_SLEEP", "30")
    folder = scheduler.create_plan(project)
    scheduler.spawn_runner(folder)
    assert _wait(lambda: any(u.get("status") == "running" and u.get("pid") for u in folder.units()))
    unit = next(u for u in folder.units() if u["status"] == "running")
    scheduler.control(project, folder.id, "cancel")
    assert _wait(lambda: folder.load()["status"] == "cancelled", timeout=40)
    assert not scheduler.pid_alive(unit["pid"], unit["proc_start"])
    assert _cells(project)["T1"] == {"preprocess_fitsidi": "cancelled", "fits_to_ms": "todo", "avica_avg": "todo"}


def test_plan_status_has_grid_queue_and_durations(project):
    _plan(project)
    folder = scheduler.create_plan(project)
    status = scheduler.plan_status(project, folder.id)
    assert status["table"]["steps"] == STEPS
    assert [q["step"] for q in status["queue"]][:3] == STEPS
    assert status["runner"]["alive"] is False
    json.dumps(status, default=str)


# ---------------------------------------------------------------------------
# Studio endpoints


@pytest.fixture()
def studio(project, tmp_path):
    pytest.importorskip("flask")
    from alfrd.gui import create_app
    from alfrd.gui.services import RuntimeCatalogReader
    from alfrd.manifest_default import register_project_folder
    from alfrd.runtime import RuntimeService, RuntimeStore

    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    service = RuntimeService(store)
    registered = register_project_folder(service, project)
    identifier = getattr(registered, "identifier", None) or registered[0].identifier
    app = create_app({
        "TESTING": True, "SECRET_KEY": "k",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'catalog.sqlite'}",
        "RUNTIME_SERVICE": service, "CATALOG_READER": RuntimeCatalogReader(service),
        "STUDIO_DEFAULT_PROJECT": identifier, "STUDIO_DEMO": False,
    })
    client = app.test_client()
    token = client.get("/api/studio/session").get_json()["csrf_token"]
    return client, {"X-CSRF-Token": token}, identifier


def test_studio_run_plan_endpoints(studio, project):
    client, h, pid = studio
    info = client.get(f"/api/studio/projects/{pid}/execution").get_json()
    assert info["configured"] is True and info["plan_csv_exists"] is False
    assert [s["id"] for s in info["steps"]] == STEPS
    rows = [{"target": "T1", "files": "a.idifits"}, {"target": "T2", "files": ""}]
    preview = client.post(f"/api/studio/projects/{pid}/plans/preview", json={"rows": rows, "steps": STEPS[:2]}, headers=h).get_json()
    assert [u["target"] for u in preview["units"]] == ["T1", "T1", "T2", "T2"]
    assert preview["units"][2]["missing"] == ["FILENAMES"]
    # Without the CSRF token nothing starts.
    assert client.post(f"/api/studio/projects/{pid}/plans", json={"rows": rows, "steps": STEPS[:2]}).status_code == 403
    rows[1]["files"] = "b.idifits"
    resp = client.post(f"/api/studio/projects/{pid}/plans", json={"rows": rows, "steps": STEPS[:2]}, headers=h)
    assert resp.status_code == 201, resp.get_json()
    plan_id = resp.get_json()["plan"]["id"]
    folder = scheduler.PlanDir(project, plan_id)
    assert _wait(lambda: folder.load()["status"] == "finished", timeout=40)
    status = client.get(f"/api/studio/projects/{pid}/plans").get_json()
    assert status["plan"]["id"] == plan_id
    assert {r["key"]: r["cells"]["fits_to_ms"] for r in status["table"]["rows"]} == {"T1": "done", "T2": "done"}
    unit = status["units"][0]
    log = client.get(f"/api/studio/projects/{pid}/file", query_string={"path": unit["log"]})
    assert log.status_code == 200 and b"$ avica pipe run" in log.data
    # A second start without confirming the overwrite of the plan CSV is refused.
    again = client.post(f"/api/studio/projects/{pid}/plans", json={"rows": rows, "steps": STEPS}, headers=h)
    assert again.status_code == 409
    # Retry one cell and resume.
    assert client.post(f"/api/studio/projects/{pid}/plans/{plan_id}/cells",
                       json={"cells": [{"row": "T2", "step": "avica_avg", "value": "todo"}]}, headers=h).status_code == 200
    assert client.post(f"/api/studio/projects/{pid}/plans/{plan_id}/cancel", json={}, headers=h).status_code == 400
    assert client.post(f"/api/studio/projects/{pid}/plans/{plan_id}/resume", json={}, headers=h).status_code == 200
    assert _wait(lambda: folder.load()["status"] == "finished" and _cells(project)["T2"]["avica_avg"] == "done", timeout=40)


def test_studio_add_row_endpoint(studio, project):
    client, h, pid = studio
    rows = [{"target": "T1", "files": "a.idifits"}]
    resp = client.post(f"/api/studio/projects/{pid}/plans", json={"rows": rows, "steps": STEPS[:1], "start": False}, headers=h)
    assert resp.status_code == 201, resp.get_json()
    plan_id = resp.get_json()["plan"]["id"]
    add = client.post(f"/api/studio/projects/{pid}/plans/{plan_id}/rows",
                      json={"target": "T2", "files": "b.idifits", "code": "BV019", "steps": STEPS[:1]}, headers=h)
    assert add.status_code == 201, add.get_json()
    table = add.get_json()["table"]
    assert {r["key"] for r in table["rows"]} == {"T1", "T2@BV019"}
    dup = client.post(f"/api/studio/projects/{pid}/plans/{plan_id}/rows", json={"target": "T1"}, headers=h)
    assert dup.status_code == 409
    bad_step = client.post(f"/api/studio/projects/{pid}/plans/{plan_id}/rows",
                           json={"target": "T3", "steps": ["nope"]}, headers=h)
    assert bad_step.status_code == 400
    missing_target = client.post(f"/api/studio/projects/{pid}/plans/{plan_id}/rows", json={}, headers=h)
    assert missing_target.status_code == 400
    no_steps = client.post(f"/api/studio/projects/{pid}/plans/{plan_id}/rows", json={"target": "T3", "steps": []}, headers=h)
    assert no_steps.status_code == 400 and "at least one step" in no_steps.get_json()["error"]["message"]
    comment = client.post(f"/api/studio/projects/{pid}/plans/{plan_id}/rows", json={"target": "#T3"}, headers=h)
    assert comment.status_code == 400
    at_sign = client.post(f"/api/studio/projects/{pid}/plans/{plan_id}/rows", json={"target": "T3@X"}, headers=h)
    assert at_sign.status_code == 400
    no_plan = client.post(f"/api/studio/projects/{pid}/plans/20000101-000000-0000/rows", json={"target": "T3"}, headers=h)
    assert no_plan.status_code == 404
    listed = client.post(f"/api/studio/projects/{pid}/plans/{plan_id}/rows",
                         json={"target": "T5", "files": ["c.idifits", "d.idifits\ne.idifits"]}, headers=h)
    assert listed.status_code == 201
    t5 = next(r for r in listed.get_json()["table"]["rows"] if r["key"] == "T5")
    assert t5["files"] == "c.idifits,d.idifits,e.idifits"
    # Without the CSRF token nothing is written.
    assert client.post(f"/api/studio/projects/{pid}/plans/{plan_id}/rows", json={"target": "T4"}).status_code == 403


def test_studio_add_row_uses_the_plan_steps_and_never_creates_the_csv(studio, project):
    client, h, pid = studio
    resp = client.post(f"/api/studio/projects/{pid}/plans",
                       json={"rows": [{"target": "T1", "files": "a"}], "steps": STEPS, "start": False}, headers=h)
    plan_id = resp.get_json()["plan"]["id"]
    folder = scheduler.PlanDir(project, plan_id)
    plan = folder.load()
    folder.save({**plan, "steps": STEPS[:2]})
    url = f"/api/studio/projects/{pid}/plans/{plan_id}/rows"
    assert client.post(url, json={"target": "T2", "steps": ["avica_avg"]}, headers=h).status_code == 400
    added = client.post(url, json={"target": "T2"}, headers=h)
    assert added.status_code == 201, added.get_json()
    assert _cells(project)["T2"] == {STEPS[0]: pc.TODO, STEPS[1]: pc.TODO, STEPS[2]: "skip"}
    csv_path = folder.csv_path()
    csv_path.unlink()
    gone = client.post(url, json={"target": "T3"}, headers=h)
    assert gone.status_code == 400 and "does not exist" in gone.get_json()["error"]["message"]
    assert not csv_path.exists()


def test_command_gets_the_users_pythonpath_not_alfrds(project, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "/user/lib")
    (project / "alfrd.yaml").write_text(
        "version: 1\nname: proj\ntemplate: avica\n"
        "execution: {status_from: exit_code}\n"
        "workflows:\n  - name: avica\n    steps:\n"
        "      - {id: preprocess_fitsidi, cmd: [python3, -c, \"import os; print('PP=' + os.environ.get('PYTHONPATH', '-'))\"]}\n"
    )
    _plan(project, targets=("T1",), steps=["preprocess_fitsidi"])
    folder = scheduler.create_plan(project)
    scheduler.Runner(project, folder.id).run()
    (unit,) = folder.units()
    assert unit["status"] == "done"
    assert "PP=/user/lib\n" in (project / unit["log"]).read_text()


def test_command_marker_names_the_plan_and_the_unit(project):
    # Unit ids (0001-<target>-<step>) repeat across plans; usage counts processes by this marker.
    (project / "alfrd.yaml").write_text(
        "version: 1\nname: proj\ntemplate: avica\n"
        "execution: {status_from: exit_code}\n"
        "workflows:\n  - name: avica\n    steps:\n"
        "      - {id: preprocess_fitsidi, cmd: [python3, -c, \"import os; print('UNIT=' + os.environ['ALFRD_UNIT'])\"]}\n"
    )
    _plan(project, targets=("T1",), steps=["preprocess_fitsidi"])
    folder = scheduler.create_plan(project)
    scheduler.Runner(project, folder.id).run()
    (unit,) = folder.units()
    assert f"UNIT={folder.id}/{unit['id']}\n" in (project / unit["log"]).read_text()


def test_plans_made_in_the_same_second_keep_their_order(tmp_path):
    root = tmp_path / "p"
    for n, plan_id in enumerate(["b", "a", "c"]):  # ids don't sort in creation order
        folder = scheduler.PlanDir(root, plan_id)
        folder.path.mkdir(parents=True)
        folder.save({"id": plan_id, "created": "2026-10-09T10:00:00", "created_ns": 1000 + n})
    assert [p["id"] for p in scheduler.list_plans(root)] == ["c", "a", "b"]


def test_fast_runner_is_not_mistaken_for_one_still_starting(tmp_path, monkeypatch):
    # A runner that records "started" before spawn_runner returns (in an earlier
    # second than a stamp taken after Popen) used to look "starting" for 30 s,
    # so reconcile skipped the plan (seen as a flaky interrupted-plan test on CI).
    import itertools
    from datetime import datetime, timedelta

    root = tmp_path / "p"
    root.mkdir()
    folder = scheduler.PlanDir(root, "x")
    folder.path.mkdir(parents=True)
    folder.save({"id": "x", "status": "running", "runner": {"started": "2000-01-01T00:00:00"}})
    t0 = datetime.now().replace(microsecond=0) - timedelta(seconds=10)
    clock = itertools.count()
    monkeypatch.setattr(scheduler, "now_iso", lambda: (t0 + timedelta(seconds=next(clock))).isoformat())

    class InstantRunner:  # the runner starts at once and records itself
        pid = 4242

        def __init__(self, *args, **kwargs):
            assert scheduler._starting(folder.load()), "spawned but not started yet"
            plan = folder.load()
            plan["runner"] = {**(plan.get("runner") or {}), "pid": 4242, "started": scheduler.now_iso()}
            folder.save(plan)

    monkeypatch.setattr(scheduler.subprocess, "Popen", InstantRunner)
    assert scheduler.spawn_runner(folder) == 4242
    plan = folder.load()
    assert plan["runner"]["pid"] == 4242  # what the runner wrote is kept
    assert not scheduler._starting(plan)

    # A launch that fails does not hold reconcile off.
    def broken(*args, **kwargs):
        raise OSError("no python")

    monkeypatch.setattr(scheduler.subprocess, "Popen", broken)
    with pytest.raises(OSError):
        scheduler.spawn_runner(folder)
    assert not scheduler._starting(folder.load())
