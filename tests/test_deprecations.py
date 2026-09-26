"""Legacy compatibility classes must emit DeprecationWarning on construction."""

import pytest


def test_pipeline_run_emits_deprecation_warning():
    from alfrd.plugins import PipelineRun

    with pytest.warns(DeprecationWarning, match="PipelineCore"):
        PipelineRun()


def test_importing_alfrd_does_not_warn_for_module_singleton(recwarn):
    """The documented ``alfrd.Pipeline`` singleton must not warn on import."""
    import importlib

    import alfrd

    importlib.reload(alfrd)
    deprecation_warnings = [
        warning for warning in recwarn.list if issubclass(warning.category, DeprecationWarning)
    ]
    assert not deprecation_warnings


def test_workflow_manager_emits_deprecation_warning(tmp_path):
    from alfrd.core.project import Project
    from alfrd.core.workflow import WorkflowManager

    project = Project("deprecation-test")
    project.create()
    with pytest.warns(DeprecationWarning, match="PipelineCore"):
        WorkflowManager(name="wf", proj=project)


def test_workflow_alias_emits_deprecation_warning(tmp_path):
    from alfrd.core.project import Project
    from alfrd.core.workflow import Workflow

    project = Project("deprecation-test-2")
    project.create()
    with pytest.warns(DeprecationWarning, match="PipelineCore"):
        Workflow(name="wf", proj=project)
