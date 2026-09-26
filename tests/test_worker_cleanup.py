"""Subprocess cleanup and stale-process handling tests for
``alfrd.runtime.worker.LocalSubprocessWorker``.

These cover behaviors not exercised by ``tests/test_runtime.py``: process
timeout kills the child rather than leaving it running, non-executable/
missing commands are turned into a FAILED transition instead of raising,
and stderr/stdout are captured for both success and failure paths.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from alfrd.runtime import RuntimeService, RuntimeStore, Status
from alfrd.runtime.worker import LocalSubprocessWorker


@pytest.fixture
def runtime(tmp_path: Path):
    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    return store, RuntimeService(store)


def _running_execution(service, tmp_path, command):
    project = service.create_project("demo", tmp_path / "demo")
    workflow = service.create_workflow(project.id, "wf", [{"key": "one", "command": command}])
    dataset = service.create_dataset(project.id, "d1")
    run = service.create_run(workflow.id, dataset.id)
    service.transition_run(run.id, Status.RUNNING)
    return service.next_pending_execution(run.id)


def test_worker_kills_subprocess_on_timeout(runtime, tmp_path):
    """A step that outlives its timeout must be terminated, not left orphaned,
    and the execution must transition to FAILED with a timeout error."""
    store, service = runtime
    execution = _running_execution(
        service, tmp_path, [sys.executable, "-c", "import time; time.sleep(30)"]
    )

    started = time.monotonic()
    result = LocalSubprocessWorker(service).execute(execution.id, timeout=0.5)
    elapsed = time.monotonic() - started

    assert result.status == "failed"
    assert "timed out" in (result.error or "")
    # The worker must not block for anywhere near the child's full sleep.
    assert elapsed < 10


def test_worker_transitions_failed_on_nonzero_exit(runtime, tmp_path):
    store, service = runtime
    execution = _running_execution(
        service, tmp_path, [sys.executable, "-c", "import sys; sys.exit(3)"]
    )

    result = LocalSubprocessWorker(service).execute(execution.id)

    assert result.status == "failed"
    assert result.exit_code == 3
    assert "exited with code 3" in (result.error or "")


def test_worker_transitions_failed_on_missing_executable(runtime, tmp_path):
    """A command referring to a nonexistent executable raises OSError inside
    subprocess.run; the worker must convert this into a FAILED execution
    instead of propagating the exception and leaving the run stuck RUNNING."""
    store, service = runtime
    execution = _running_execution(
        service, tmp_path, ["/definitely/does/not/exist/binary", "--flag"]
    )

    result = LocalSubprocessWorker(service).execute(execution.id)

    assert result.status == "failed"
    assert result.error
    # Run itself remains running; orchestration/finalization is caller-owned.
    assert service.get_run(execution.run_id).status == "running"


def test_worker_captures_stdout_and_stderr_on_success(runtime, tmp_path):
    store, service = runtime
    execution = _running_execution(
        service,
        tmp_path,
        [
            sys.executable,
            "-c",
            "import sys; print('out-line'); print('err-line', file=sys.stderr)",
        ],
    )

    result = LocalSubprocessWorker(service).execute(execution.id)

    assert result.status == "succeeded"
    assert result.stdout.strip() == "out-line"
    assert result.stderr.strip() == "err-line"


def test_worker_only_transitions_the_claimed_execution(runtime, tmp_path):
    """Two sibling step executions under different runs must not interfere:
    running one worker must not affect the other's persisted status."""
    store, service = runtime
    project = service.create_project("demo", tmp_path / "demo")
    workflow = service.create_workflow(
        project.id,
        "wf",
        [{"key": "one", "command": [sys.executable, "-c", "print('a')"]}],
    )
    dataset_a = service.create_dataset(project.id, "a")
    dataset_b = service.create_dataset(project.id, "b")
    run_a = service.create_run(workflow.id, dataset_a.id)
    run_b = service.create_run(workflow.id, dataset_b.id)
    service.transition_run(run_a.id, Status.RUNNING)
    service.transition_run(run_b.id, Status.RUNNING)
    execution_a = service.next_pending_execution(run_a.id)
    execution_b = service.next_pending_execution(run_b.id)

    LocalSubprocessWorker(service).execute(execution_a.id)

    assert service.get_execution(execution_a.id).status == "succeeded"
    assert service.get_execution(execution_b.id).status == "pending"


def test_recover_stale_marks_orphaned_running_execution_interrupted(runtime, tmp_path):
    """Simulates a worker process crashing mid-step: the run's heartbeat goes
    stale while the execution is still RUNNING. recover_stale must mark both
    the run and its running execution INTERRUPTED so a supervisor can retry."""
    from datetime import datetime, timedelta, timezone

    store, service = runtime
    execution = _running_execution(service, tmp_path, [sys.executable, "-c", "print('x')"])
    service.transition_execution(execution.id, Status.RUNNING)

    with store.session() as session:
        from alfrd.runtime.models import Run

        db_run = session.get(Run, execution.run_id)
        db_run.heartbeat_at = datetime.now(timezone.utc) - timedelta(hours=1)

    recovered = service.recover_stale(timedelta(minutes=5))

    assert [item.id for item in recovered] == [execution.run_id]
    stale_execution = service.get_execution(execution.id)
    assert stale_execution.status == "interrupted"
    assert stale_execution.finished_at is not None
