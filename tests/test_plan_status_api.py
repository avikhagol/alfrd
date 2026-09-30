"""The read-only plan status contract (alfrd.api.status): CLI, HTTP v1, JSON Schema."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import sys
import threading
import time
from importlib import resources
from pathlib import Path

import jsonschema
import pytest
from typer.testing import CliRunner

from alfrd.api import status as api
from alfrd.cli import alfrd_cli
from alfrd.execution import load_execution
from alfrd.runtime import plan_csv as pc
from alfrd.runtime import scheduler

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")

FAKE_BIN = Path(__file__).parent / "fixtures" / "bin"
STEPS = ["preprocess_fitsidi", "fits_to_ms", "avica_avg"]
SCHEMA = json.loads(resources.files("alfrd.schemas").joinpath("plan_status.v1.json").read_text())
cli = CliRunner()


def _valid(doc):
    jsonschema.validate(doc, SCHEMA)
    return doc


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
    if not os.access(fake, os.X_OK):
        fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{FAKE_BIN}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("FAKE_AVICA_SLEEP", "0.05")
    for key in ("FAKE_AVICA_FAIL", "FAKE_AVICA_NAMES", "FAKE_AVICA_EXIT", "FAKE_AVICA_HOLD"):
        monkeypatch.delenv(key, raising=False)
    return root


def _plan(root, targets=("T1", "T2")):
    cfg = load_execution(root)
    pc.create(cfg.plan_csv, [{"target": t, "files": "a.idifits", "code": "BV019"} for t in targets], cfg.step_ids, STEPS,
              key_column=cfg.key_column, files_column=cfg.files_column, code_column=cfg.code_column,
              workdir_column=cfg.workdir_column)


def _wait(predicate, timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def _snapshot(root: Path) -> dict[str, str]:
    """Hash of every file ALFRD keeps for plans, plus the plan CSV."""
    out = {}
    for path in sorted([*(root / ".alfrd").rglob("*"), root / "alfrd.plan.csv"]):
        if path.is_file() and "runner.lock" not in path.name:
            out[str(path.relative_to(root))] = hashlib.sha1(path.read_bytes()).hexdigest()
    return out


def _cli(*args):
    return cli.invoke(alfrd_cli, ["plan", *args])


def test_finished_and_failed_documents(project, monkeypatch):
    _plan(project)
    monkeypatch.setenv("FAKE_AVICA_FAIL", "T2:fits_to_ms")
    folder = scheduler.create_plan(project)
    scheduler.Runner(project, folder.id).run()
    doc = _valid(api.plan_status(project))
    assert doc["schema"] == "alfrd.plan_status/1" and doc["plan"]["id"] == folder.id
    assert doc["project"]["name"] == "proj" and doc["project"]["identifier"].endswith(".proj")
    assert doc["plan"]["status"] == "finished" and doc["runner"] == {**doc["runner"], "alive": False, "stale": False}
    assert doc["counts"] == {"todo": 0, "queued": 0, "running": 0, "done": 4, "failed": 1, "skipped": 1, "cancelled": 0, "interrupted": 0}
    assert doc["progress"] == {"cells_done": 4, "cells_total": 6, "eta_s": None}
    [fail] = doc["failures"]
    assert (fail["target"], fail["step"], fail["reason"], fail["exit_code"]) == ("T2", "fits_to_ms", "boom", 0)
    assert fail["entity"] == {"project": doc["project"]["identifier"], "target": "T2", "project_code": "BV019",
                              "step": "fits_to_ms", "file": fail["log"]}
    assert doc["summary"].startswith(f"Plan {folder.id} is finished, 4/6 cells done, 1 failed (T2 fits_to_ms: boom)")
    assert doc["exit_code"] == api.EXIT_FAILED
    rows = _valid(api.plan_status(project, detail="rows", limit=1, offset=1))
    assert rows["rows_total"] == 2 and [r["row"] for r in rows["rows"]] == ["T2@BV019"]
    full = _valid(api.plan_status(project, detail="full"))
    cell = full["rows"][0]["detail"]["preprocess_fitsidi"]
    assert cell["command"][:3] == ["avica", "pipe", "run"] and cell["duration_s"] is not None and cell["status"] == "done"
    result = _cli("status", "--root", str(project), "--json")
    assert result.exit_code == 1 and json.loads(result.output)["plan"]["id"] == folder.id
    human = _cli("status", "--root", str(project))
    assert human.exit_code == 1 and "1 failed" in human.output


def test_paused_not_found_and_clean_finish_exit_codes(project):
    assert _cli("status", "--root", str(project), "--json").exit_code == api.EXIT_NOT_FOUND
    with pytest.raises(api.StatusNotFound):
        api.plan_status(project)
    _plan(project, targets=("T1",))
    folder = scheduler.create_plan(project)
    scheduler.control(project, folder.id, "pause")
    doc = _valid(api.plan_status(project))
    assert doc["plan"]["status"] == "paused" and doc["plan"]["control"] == "pause" and doc["exit_code"] == api.EXIT_STOPPED
    assert doc["counts"]["queued"] == 1 and doc["counts"]["todo"] == 2
    assert _cli("status", folder.id, "--root", str(project), "--json").exit_code == 3
    assert _cli("status", "nope", "--root", str(project), "--json").exit_code == 4
    scheduler.control(project, folder.id, "resume", spawn=False)
    scheduler.Runner(project, folder.id).run()
    assert _cli("status", "--root", str(project), "--json").exit_code == 0


def test_running_and_stale_documents_and_reads_never_mutate(project, monkeypatch, tmp_path):
    _plan(project, targets=("T1",))
    hold = tmp_path / "hold"
    hold.write_text("")
    monkeypatch.setenv("FAKE_AVICA_HOLD", str(hold))
    folder = scheduler.create_plan(project)
    scheduler.spawn_runner(folder)
    unit = None
    try:
        assert _wait(lambda: any(u.get("status") == "running" and u.get("pid") for u in folder.units()))
        assert _wait(lambda: (folder.load().get("runner") or {}).get("pid") is not None)
        doc = _valid(api.plan_status(project))
        assert doc["plan"]["status"] == "running" and doc["runner"]["alive"] and not doc["runner"]["stale"]
        [run] = doc["running"]
        assert (run["target"], run["step"], run["project_code"]) == ("T1", "preprocess_fitsidi", "BV019")
        assert run["entity"]["step"] == "preprocess_fitsidi" and doc["exit_code"] == api.EXIT_RUNNING
        assert "running preprocess_fitsidi on T1 (BV019)" in doc["summary"]
        assert _cli("status", "--root", str(project), "--json").exit_code == 2
        # The runner dies; the command keeps running. Status says so and fixes nothing.
        unit = next(u for u in folder.units() if u["status"] == "running")
        os.kill(folder.load()["runner"]["pid"], signal.SIGKILL)
        assert _wait(lambda: not folder.runner_alive(), timeout=10)
        before = _snapshot(project)
        stale = _valid(api.plan_status(project, detail="full"))
        assert stale["runner"]["alive"] is False and stale["runner"]["stale"] is True
        assert stale["exit_code"] == api.EXIT_STALE and "stale" in stale["summary"]
        assert _cli("status", "--root", str(project), "--json").exit_code == 5
        assert _cli("status", "--root", str(project)).exit_code == 5
        list(api.follow(project))
        api.list_plans(project)
        api.log_tail(project, target="T1")
        assert _snapshot(project) == before, "reading status must not change the plan or its CSV"
        assert folder.load()["status"] == "running"
    finally:
        hold.unlink()
        if unit:
            _wait(lambda: not scheduler.pid_alive(unit["pid"], unit["proc_start"]), timeout=20)
    # --reconcile is the explicit way to recover.
    doc = api.plan_status(project, reconcile=True)
    assert doc["plan"]["status"] in ("interrupted", "running", "finished")


def test_since_cursor_events_and_wait(project):
    _plan(project, targets=("T1",))
    folder = scheduler.create_plan(project)
    scheduler.control(project, folder.id, "pause")
    doc = api.plan_status(project)
    same = _valid(api.plan_status(project, since=doc["cursor"]))
    assert same["changed"] is False and same["cursor"] == doc["cursor"] and "counts" not in same
    with pytest.raises(ValueError):
        api.plan_status(project, since="bogus")
    # Nothing happens: wait times out.
    t0 = time.monotonic()
    waited = api.wait(project, until="any-change", timeout=0.6, poll=0.1)
    assert waited["reason"] == "timeout" and time.monotonic() - t0 >= 0.5
    events = list(api.follow(project))
    assert [e["type"] for e in events] == ["plan", "plan"]  # created, pause
    scheduler.control(project, folder.id, "resume", spawn=False)
    scheduler.Runner(project, folder.id).run()
    changed = _valid(api.plan_status(project, since=doc["cursor"]))
    assert changed["changed"] is True and {e["type"] for e in changed["events"]} >= {"started", "finished"}
    later = list(api.follow(project, since=events[-1]["cursor"]))
    assert later and later[0]["id"] not in {e["id"] for e in events}, "--since resumes after the last event seen"
    assert [e["id"] for e in list(api.follow(project))[len(events):]] == [e["id"] for e in later]
    out = _cli("events", "--root", str(project), "--since", events[-1]["cursor"])
    assert out.exit_code == 0 and [json.loads(l)["id"] for l in out.output.splitlines()] == [e["id"] for e in later]
    done = _cli("wait", "--root", str(project), "--until", "done", "--timeout", "5", "--json")
    assert done.exit_code == 0 and json.loads(done.output)["reason"] == "done"


def test_wait_returns_when_the_plan_changes(project):
    _plan(project, targets=("T1",))
    folder = scheduler.create_plan(project)
    scheduler.control(project, folder.id, "pause")
    timer = threading.Timer(0.5, lambda: scheduler.control(project, folder.id, "cancel", spawn=False))
    timer.start()
    t0 = time.monotonic()
    doc = api.wait(project, until="any-change", timeout=10, poll=0.1)
    timer.join()
    assert doc["reason"] == "changed" and time.monotonic() - t0 < 5
    assert doc["plan"]["status"] == "cancelled"


def test_log_tail_is_bounded_and_strips_ansi(project):
    _plan(project, targets=("T1",))
    folder = scheduler.create_plan(project)
    scheduler.Runner(project, folder.id).run()
    unit = folder.units()[0]
    log = project / unit["log"]
    log.write_text("\x1b[1mbold\x1b[0m start\n" + "".join(f"line {i}\n" for i in range(5000)))
    doc = api.log_tail(project, target="T1", step="preprocess_fitsidi", lines=1000)
    assert len(doc["lines"]) == 200 and doc["lines"][-1] == "line 4999" and doc["truncated"]
    log.write_text("\x1b[1mbold\x1b[0m start\n")
    assert api.log_tail(project, unit=unit["id"])["lines"] == ["bold start"]
    with pytest.raises(api.StatusNotFound):
        api.log_tail(project, target="nope")
    assert _cli("log", "--root", str(project), "--target", "T1", "--step", "preprocess_fitsidi").output.strip().endswith("bold start")


# ---------------------------------------------------------------------------
# HTTP v1


@pytest.fixture()
def server(project, tmp_path):
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
        "TESTING": True, "SECRET_KEY": "k", "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'catalog.sqlite'}",
        "RUNTIME_SERVICE": service, "CATALOG_READER": RuntimeCatalogReader(service), "STUDIO_DEMO": False,
        "API_EVENTS_POLL": 0.1,
    })
    return app, identifier


def test_http_status_list_404_and_schema(server, project):
    app, pid = server
    client = app.test_client()
    assert client.get(f"/api/v1/projects/{pid}/plans/latest").status_code == 404
    _plan(project, targets=("T1",))
    folder = scheduler.create_plan(project)
    scheduler.Runner(project, folder.id).run()
    listing = client.get(f"/api/v1/projects/{pid}/plans").get_json()
    assert listing["plans"][0]["id"] == folder.id
    doc = _valid(client.get(f"/api/v1/projects/{pid}/plans/{folder.id}?detail=rows").get_json())
    assert doc["counts"]["done"] == 3 and doc["rows"][0]["row"] == "T1@BV019"
    by_name = client.get("/api/v1/projects/proj/plans/latest").get_json()
    assert by_name["plan"]["id"] == folder.id and by_name["project"]["identifier"] == pid
    assert client.get(f"/api/v1/projects/{pid}/plans/nope").status_code == 404
    assert client.get("/api/v1/projects/unknown/plans").status_code == 404
    assert client.get(f"/api/v1/projects/{pid}/plans/latest?detail=everything").status_code == 400
    assert client.get("/api/v1/schema/plan_status").get_json()["title"] == "alfrd.plan_status/1"
    assert client.get("/api/v1/projects").get_json()["projects"][0]["identifier"] == pid
    log = client.get(f"/api/v1/projects/{pid}/plans/latest/log?target=T1&step=fits_to_ms").get_json()
    assert log["steps"] == ["fits_to_ms"] and log["lines"]
    events = client.get(f"/api/v1/projects/{pid}/plans/latest/events?timeout=1")
    body = events.get_data(as_text=True)
    assert events.mimetype == "text/event-stream" and "event: started" in body and body.rstrip().endswith("data: {}")


def test_http_long_poll_returns_early_on_change(server, project):
    app, pid = server
    client = app.test_client()
    _plan(project, targets=("T1",))
    folder = scheduler.create_plan(project)
    scheduler.control(project, folder.id, "pause")
    cursor = client.get(f"/api/v1/projects/{pid}/plans/latest").get_json()["cursor"]
    t0 = time.monotonic()
    same = client.get(f"/api/v1/projects/{pid}/plans/latest?since={cursor}&wait=0.5").get_json()
    assert same["changed"] is False and time.monotonic() - t0 >= 0.4
    timer = threading.Timer(0.4, lambda: scheduler.control(project, folder.id, "cancel", spawn=False))
    timer.start()
    t0 = time.monotonic()
    doc = client.get(f"/api/v1/projects/{pid}/plans/latest?since={cursor}&wait=30").get_json()
    timer.join()
    assert doc["changed"] is True and doc["plan"]["status"] == "cancelled" and time.monotonic() - t0 < 10
    assert any(e["type"] == "plan" and e["text"] == "cancel" for e in doc["events"])


def test_http_is_loopback_only_unless_a_token_is_set(server, project, monkeypatch):
    app, pid = server
    client = app.test_client()
    remote = {"REMOTE_ADDR": "10.1.2.3"}
    url = f"/api/v1/projects/{pid}/plans"
    assert client.get(url, environ_base=remote).status_code == 403
    monkeypatch.setenv("ALFRD_API_TOKEN", "s3cret")
    denied = client.get(url, environ_base=remote)
    assert denied.status_code == 401 and denied.headers["WWW-Authenticate"].startswith("Bearer")
    assert client.get(url, environ_base=remote, headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get(url, environ_base=remote, headers={"Authorization": "Bearer s3cret"}).status_code == 200
    assert client.get(url).status_code == 200  # loopback needs no token


def test_cursor_keeps_events_that_land_in_the_same_second():
    t = "2026-09-29T10:00:00Z"
    first = [{"id": "pzzz", "at": t, "type": "plan"}]
    cursor = api._cursor(first)
    later = [{"id": "paaa", "at": t, "type": "plan"}, *first, {"id": "s1", "at": "2026-09-29T10:00:01Z", "type": "started"}]
    assert [e["id"] for e in api.events_since(later, cursor)] == ["paaa", "s1"]
    assert api.events_since(later, api._cursor(later)) == []
    for bad in ("c1.x|y", "c1.2026-09-29T10:00:00Z|", "z", "c1.2026-09-29T10:00:00Z|nothex!"):
        with pytest.raises(ValueError):
            api.parse_cursor(bad)
