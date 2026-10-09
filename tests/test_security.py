"""Security-focused tests for dynamic project loading and command execution
paths reviewed for the 0.2.10.0 hardening workstream.

Scope: ``alfrd.runtime.service`` (run working-directory derivation from
caller-supplied run ids).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from alfrd.runtime import RuntimeService, RuntimeStore


@pytest.fixture
def runtime(tmp_path: Path):
    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    return store, RuntimeService(store)


@pytest.mark.parametrize(
    "malicious_run_id",
    ["../../escape", "/etc/passwd", "a/b", "..", ".", "a\\b"],
)
def test_create_run_rejects_path_traversal_in_run_id(runtime, tmp_path, malicious_run_id):
    """A caller-supplied run_id must not escape <project_root>/runs/.

    Without validation, ``Path(project_root) / "runs" / run_id`` would
    resolve outside the project tree for a run id such as ``"../../escape"``
    or an absolute path, letting run state/manifest/artifacts be written
    anywhere the process can write.
    """
    store, service = runtime
    project = service.create_project("demo", tmp_path / "demo")
    workflow = service.create_workflow(
        project.id, "wf", [{"key": "one", "command": ["true"]}]
    )
    dataset = service.create_dataset(project.id, "d1")

    with pytest.raises(ValueError, match="Invalid run id"):
        service.create_run(workflow.id, dataset.id, run_id=malicious_run_id)


def test_create_run_with_explicit_working_directory_is_unaffected(runtime, tmp_path):
    """An explicit working_directory bypasses run-id-derived path join and
    remains valid regardless of the run_id's shape (it is still resolved,
    not path-joined with attacker-controlled segments)."""
    store, service = runtime
    project = service.create_project("demo", tmp_path / "demo")
    workflow = service.create_workflow(
        project.id, "wf", [{"key": "one", "command": ["true"]}]
    )
    dataset = service.create_dataset(project.id, "d1")
    workdir = tmp_path / "explicit-workdir"

    run = service.create_run(
        workflow.id, dataset.id, run_id="not/actually/used/for/path", working_directory=workdir
    )
    assert Path(run.working_directory) == workdir.resolve()
