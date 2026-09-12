"""Cancellation, restart, and concurrent-run edge case tests for
``alfrd.runtime.service.RuntimeService`` beyond ``tests/test_runtime.py``.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

from alfrd.runtime import InvalidTransition, RuntimeService, RuntimeStore, Status


@pytest.fixture
def runtime(tmp_path: Path):
    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    return store, RuntimeService(store)


def _make_run(service, tmp_path, name="demo"):
    project = service.create_project(name, tmp_path / name)
    workflow = service.create_workflow(
        project.id, "wf", [{"key": "one", "command": [sys.executable, "-c", "print('ok')"]}]
    )
    dataset = service.create_dataset(project.id, "d1")
    return project, workflow, dataset, service.create_run(workflow.id, dataset.id)


def test_cancel_pending_run_transitions_execution_state(runtime, tmp_path):
    store, service = runtime
    _, _, _, run = _make_run(service, tmp_path)

    cancelled = service.transition_run(run.id, Status.CANCELLED)

    assert cancelled.status == "cancelled"
    assert cancelled.finished_at is not None


def test_cancel_running_run_is_terminal_and_not_resumable_by_default(runtime, tmp_path):
    store, service = runtime
    _, _, _, run = _make_run(service, tmp_path)
    service.transition_run(run.id, Status.RUNNING)

    cancelled = service.transition_run(run.id, Status.CANCELLED)
    assert cancelled.status == "cancelled"

    with pytest.raises(InvalidTransition):
        service.transition_run(run.id, Status.RUNNING)
    with pytest.raises(InvalidTransition):
        service.resume_run(run.id)


def test_cancelled_run_can_be_retried_as_a_new_linked_run(runtime, tmp_path):
    store, service = runtime
    _, workflow, dataset, run = _make_run(service, tmp_path)
    service.transition_run(run.id, Status.RUNNING)
    service.transition_run(run.id, Status.CANCELLED)

    retried = service.retry_run(run.id)

    assert retried.id != run.id
    assert retried.parent_run_id == run.id
    assert retried.status == "pending"


def test_cannot_succeed_a_run_with_unfinished_step_executions(runtime, tmp_path):
    """Guards against silently marking a run successful while a step is still
    pending/running -- a state-loss risk called out by the roadmap."""
    store, service = runtime
    _, _, _, run = _make_run(service, tmp_path)
    service.transition_run(run.id, Status.RUNNING)
    # Do NOT complete the pending step execution.

    with pytest.raises(InvalidTransition):
        service.transition_run(run.id, Status.SUCCEEDED)


def test_cannot_start_step_execution_while_run_is_not_running(runtime, tmp_path):
    store, service = runtime
    _, _, _, run = _make_run(service, tmp_path)
    execution = service.next_pending_execution(run.id)

    # Run is still PENDING; a step cannot start running underneath it.
    with pytest.raises(InvalidTransition):
        service.transition_execution(execution.id, Status.RUNNING)


def test_restart_after_interruption_resumes_only_unfinished_steps(runtime, tmp_path):
    store, service = runtime
    project = service.create_project("demo", tmp_path / "demo")
    workflow = service.create_workflow(
        project.id,
        "wf",
        [
            {"key": "one", "command": [sys.executable, "-c", "print('one')"]},
            {"key": "two", "command": [sys.executable, "-c", "print('two')"]},
        ],
    )
    dataset = service.create_dataset(project.id, "d1")
    run = service.create_run(workflow.id, dataset.id)
    service.transition_run(run.id, Status.RUNNING)
    first = service.next_pending_execution(run.id)
    service.transition_execution(first.id, Status.RUNNING)
    service.transition_execution(first.id, Status.SUCCEEDED, exit_code=0)
    second = service.next_pending_execution(run.id)
    service.transition_execution(second.id, Status.RUNNING)
    # Simulate a crash: the process dies while "two" is still running.
    service.transition_run(run.id, Status.INTERRUPTED)

    resumed = service.resume_run(run.id)
    pending = service.next_pending_execution(resumed.id)

    assert pending.step_definition.key == "two"
    assert pending.attempt == 2
    # The already-succeeded step must not be recreated as a new attempt.
    executions = service.get_run(resumed.id).step_executions
    one_executions = [item for item in executions if item.step_definition.key == "one"]
    assert len(one_executions) == 1
    assert one_executions[0].status == "succeeded"


def test_concurrent_runs_for_same_workflow_and_dataset_remain_independent(runtime, tmp_path):
    """Two runs of the same workflow+dataset must not share execution state:
    starting/failing one must not affect the other's status."""
    store, service = runtime
    project, workflow, dataset, run_a = _make_run(service, tmp_path)
    run_b = service.create_run(workflow.id, dataset.id)

    service.transition_run(run_a.id, Status.RUNNING)
    execution_a = service.next_pending_execution(run_a.id)
    service.transition_execution(execution_a.id, Status.RUNNING)
    service.transition_execution(execution_a.id, Status.FAILED, error="boom")
    service.transition_run(run_a.id, Status.FAILED, error="boom")

    # run_b was never started and must remain untouched.
    fresh_b = service.get_run(run_b.id)
    assert fresh_b.status == "pending"
    assert fresh_b.step_executions[0].status == "pending"


def test_concurrent_run_creation_from_multiple_threads_does_not_corrupt_state(runtime, tmp_path):
    """Exercise the store's session boundary under concurrent writers: many
    threads creating runs for distinct datasets must all succeed and produce
    exactly one run each, with no cross-run id collisions."""
    store, service = runtime
    project = service.create_project("demo", tmp_path / "demo")
    workflow = service.create_workflow(
        project.id, "wf", [{"key": "one", "command": [sys.executable, "-c", "print(1)"]}]
    )
    dataset_ids = [
        service.create_dataset(project.id, f"d{i}").id for i in range(8)
    ]
    created: list[str] = []
    errors: list[Exception] = []
    lock = threading.Lock()

    def worker(dataset_id: str) -> None:
        try:
            run = service.create_run(workflow.id, dataset_id)
            with lock:
                created.append(run.id)
        except Exception as exc:  # noqa: BLE001
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=worker, args=(did,)) for did in dataset_ids]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    assert len(created) == len(dataset_ids)
    assert len(set(created)) == len(dataset_ids)
    for dataset_id in dataset_ids:
        runs = service.list_runs(dataset_id=dataset_id)
        assert len(runs) == 1
