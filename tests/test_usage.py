"""Resource usage per step (alfrd.runtime.usage, sampled by the shim)."""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path

import pytest

from alfrd.execution import load_execution
from alfrd.runtime import plan_csv as pc
from alfrd.runtime import scheduler, usage

pytestmark = pytest.mark.skipif(not usage.available(), reason="needs Linux /proc")

FAKE_BIN = Path(__file__).parent / "fixtures" / "bin"


@pytest.fixture()
def project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "alfrd.yaml").write_text(
        "version: 1\nname: proj\ntemplate: avica\nexecution: {usage_interval: 0.2}\n"
        "workflows:\n  - name: avica\n    steps: [preprocess_fitsidi]\n"
    )
    (root / "avica.inp").write_text("target_dir = reductions\n")
    fake = FAKE_BIN / "avica"
    if not os.access(fake, os.X_OK):
        fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{FAKE_BIN}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("FAKE_AVICA_SLEEP", "0")
    for key in ("FAKE_AVICA_FAIL", "FAKE_AVICA_NAMES", "FAKE_AVICA_EXIT", "FAKE_AVICA_HOLD"):
        monkeypatch.delenv(key, raising=False)
    cfg = load_execution(root)
    pc.create(cfg.plan_csv, [{"target": "T1", "files": "a.idifits"}], cfg.step_ids, cfg.step_ids,
              key_column=cfg.key_column, files_column=cfg.files_column, code_column=cfg.code_column,
              workdir_column=cfg.workdir_column)
    return root


def _wait(predicate, timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_step_usage_summary_and_samples(project, monkeypatch):
    monkeypatch.setenv("FAKE_AVICA_BURN", "1.5")
    monkeypatch.setenv("FAKE_AVICA_MEM", "120")
    folder = scheduler.create_plan(project)
    scheduler.Runner(project, folder.id).run()
    [unit] = folder.units()
    summary = unit["usage"]
    assert 1.0 <= summary["cpu_s"] <= 4.0, summary
    assert 0.4 <= summary["avg_cores"] <= 1.6, summary
    assert summary["peak_mem"] >= 100 * 1024 * 1024, summary
    assert summary["wall_s"] >= 1.4 and summary["samples"] >= 3 and summary["limited"] is False
    rows = [json.loads(l) for l in (project / unit["usage_file"]).read_text().splitlines()]
    assert len(rows) == summary["samples"] and max(r["mem"] for r in rows) >= 100 * 1024 * 1024
    assert any(r["cores"] > 0.5 for r in rows)
    from alfrd.api import status as api

    cell = api.plan_status(project, detail="full")["rows"][0]["detail"]["preprocess_fitsidi"]
    assert cell["usage"]["peak_mem"] == summary["peak_mem"]


def test_shim_keeps_sampling_after_the_runner_dies(project, monkeypatch):
    monkeypatch.setenv("FAKE_AVICA_BURN", "3")
    folder = scheduler.create_plan(project)
    scheduler.spawn_runner(folder)
    assert _wait(lambda: any(u.get("status") == "running" and u.get("pid") for u in folder.units()))
    assert _wait(lambda: (folder.load().get("runner") or {}).get("pid") is not None)
    unit = next(u for u in folder.units() if u["status"] == "running")
    os.kill(folder.load()["runner"]["pid"], signal.SIGKILL)
    usage_file = project / unit["usage_file"]
    assert _wait(lambda: usage_file.exists())
    before = len(usage_file.read_text().splitlines())
    time.sleep(1.0)
    assert len(usage_file.read_text().splitlines()) > before, "the shim samples on its own"
    exit_file = project / unit["exit_file"]
    assert _wait(lambda: exit_file.exists(), timeout=20)
    assert json.loads(exit_file.read_text())["usage"]["cpu_s"] >= 2.0


def test_sampler_off_and_dir_size(tmp_path):
    (tmp_path / "a").write_bytes(b"x" * 1000)
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b").write_bytes(b"x" * 24)
    assert usage.dir_size(tmp_path) == {"bytes": 1024, "files": 2}
    assert usage.dir_size(tmp_path / "missing") is None
    off = usage.Sampler(os.getpid(), None, 0)
    assert off.take() is None and off.summary()["samples"] == 0
    import subprocess

    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
    try:
        time.sleep(0.2)
        reading = usage.sample()  # the descendants of this process: the child
        assert reading["procs"] >= 1 and reading["mem"] > 0
    finally:
        child.kill()
        child.wait()


FAKE_MPIRUN = FAKE_BIN / "fake_mpirun"


def _shim(tmp_path, *command, env=None):
    import subprocess

    if not os.access(FAKE_MPIRUN, os.X_OK):
        FAKE_MPIRUN.chmod(0o755)
    exit_file = tmp_path / "u.exit"
    full = {**os.environ, "ALFRD_USAGE_INTERVAL": "0.4", "ALFRD_UNIT": "u-test", **(env or {})}
    subprocess.run([sys.executable, "-m", "alfrd.runtime.shim", "--exit-file", str(exit_file), "--", *command],
                   env=full, start_new_session=True, check=True, stdout=subprocess.DEVNULL, timeout=60)
    rows = [json.loads(l) for l in exit_file.with_suffix(".usage.jsonl").read_text().splitlines()]
    return json.loads(exit_file.read_text()), rows


@pytest.mark.parametrize("mode", ["child", "setsid", "daemon"])
def test_mpi_ranks_are_counted_however_they_are_started(tmp_path, mode):
    """MPI launchers move ranks out of the process group (setsid) or orphan them (daemon)."""
    ranks, secs = 3, 2.0
    busy = min(ranks, os.cpu_count() or 1)
    data, rows = _shim(tmp_path, str(FAKE_MPIRUN), "-n", str(ranks), "--burn", str(secs), "--mode", mode)
    assert data["exit_code"] == 0
    usage = data["usage"]
    expected = busy * secs
    assert 0.8 * expected <= usage["cpu_s"] <= 1.25 * expected, usage
    middle = [r["cores"] for r in rows if 0.3 < r["t"] < secs - 0.2]
    assert middle and min(middle) >= 0.7 * busy, rows  # live samples see the ranks, not just the launcher
    assert max(r["procs"] for r in rows) >= ranks
    assert all(b["cpu_s"] >= a["cpu_s"] for a, b in zip(rows, rows[1:])), "cumulative CPU never goes down"


def test_processes_marked_with_the_unit_are_counted(tmp_path):
    """A rank started by an unrelated local daemon, but with this command's ALFRD_UNIT."""
    import subprocess

    burner = subprocess.Popen([sys.executable, "-c", "import time\nt=time.time()+2.5\nwhile time.time()<t: pass"],
                              env={**os.environ, "ALFRD_UNIT": "u-marked"}, start_new_session=True)
    try:
        data, rows = _shim(tmp_path, sys.executable, "-c", "import time; time.sleep(2)", env={"ALFRD_UNIT": "u-marked"})
    finally:
        burner.wait(10)
    assert max(r["cores"] for r in rows) >= 0.7, rows
    assert data["usage"]["cpu_s"] >= 1.2
