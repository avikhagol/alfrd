"""Regression coverage for verification hangs and orphaned runner children."""
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys

import psutil
import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts/run_studio_tests.py"


def test_studio_runner_preserves_failure_exit_status():
    run = runpy.run_path(str(RUNNER))["run"]
    assert run([sys.executable, "-c", "raise SystemExit(7)"], timeout=10) == 7


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups")
def test_studio_deadline_kills_descendants(tmp_path):
    run = runpy.run_path(str(RUNNER))["run"]
    pidfile = tmp_path / "child.pid"
    child = "import time; time.sleep(60)"
    code = ("import pathlib, subprocess, sys, time; "
            f"p = subprocess.Popen([sys.executable, '-c', {child!r}]); "
            f"pathlib.Path({str(pidfile)!r}).write_text(str(p.pid)); "
            "time.sleep(60)")
    assert run([sys.executable, "-c", code], timeout=2) == 124
    pid = int(pidfile.read_text())
    assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE


def test_progressive_fixture_closes_server_when_browser_launch_fails(tmp_path, monkeypatch):
    if not shutil.which("node"):
        pytest.skip("Node.js not available")
    fake = tmp_path / "broken-playwright.mjs"
    fake.write_text("export const chromium = { launch: async () => { throw new Error('launch regression sentinel'); } };\n")
    monkeypatch.setenv("ALFRD_PLAYWRIGHT", str(fake))
    result = subprocess.run(
        [sys.executable, str(RUNNER), "--timeout", "10",
         "tests/studio_js/progressive_load.browser.test.mjs"],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "launch regression sentinel" in result.stdout + result.stderr
