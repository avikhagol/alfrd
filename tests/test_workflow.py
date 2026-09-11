from pathlib import Path

from alfrd.core.project import Project
from alfrd.core.workflow import Workflow


def _create_workflow_project(root: Path) -> Project:
    plugin = root / "workflow_plugin.py"
    plugin.write_text(
        "from alfrd.plugins import register, validate, validator\n"
        "@validator(desc='before', after=False)\n"
        "def before():\n"
        "    print('before')\n"
        "    return True\n"
        "@validator(desc='after', after=True)\n"
        "def after():\n"
        "    print('after')\n"
        "    return True\n"
        "@validate(by=['before'])\n"
        "@register(desc='step one')\n"
        "def step_one():\n"
        "    print('step one')\n"
        "@validate(by=['after'])\n"
        "@register(desc='step two')\n"
        "def step_two():\n"
        "    print('step two')\n",
        encoding="utf-8",
    )
    project = Project("workflow-test")
    project.create()
    project.add(plugin)
    project.load_project(purge_existing=True)
    return project


def test_workflow_runs_configured_sequence(tmp_path: Path, capsys):
    project = _create_workflow_project(tmp_path)
    workflow = Workflow(name="test-workflow", proj=project)
    workflow.configure().set_sequence("step_one", "step_two")

    workflow.run("step_one")

    output = capsys.readouterr().out
    assert output.index("step one") < output.index("step two")
    assert workflow.validation_success is True
    assert workflow.prev_step_success is True
    assert workflow.params["ret"] is None
    project.rm()


def test_workflow_configuration_round_trip(tmp_path: Path):
    project = _create_workflow_project(tmp_path)
    workflow = Workflow(name="round-trip", proj=project)
    config = workflow.configure()
    config.set_sequence("step_one", "step_two")
    config.save()

    restored = Workflow(name="round-trip", proj=project)
    restored.configure().load()

    assert restored.sequence == ["step_one", "step_two"]
    assert Path(config.configfile).is_relative_to(tmp_path)
    project.rm()


def test_rejecting_validator_skips_step(tmp_path: Path, capsys):
    plugin = tmp_path / "rejecting_plugin.py"
    plugin.write_text(
        "from alfrd.plugins import register, validate, validator\n"
        "@validator(desc='reject')\n"
        "def reject():\n"
        "    return False\n"
        "@validate(by=['reject'])\n"
        "@register(desc='must not run')\n"
        "def forbidden_step():\n"
        "    print('FORBIDDEN EXECUTION')\n",
        encoding="utf-8",
    )
    project = Project("rejecting-workflow")
    project.create()
    project.add(plugin)
    project.load_project(purge_existing=True)
    workflow = Workflow(name="rejecting", proj=project)

    workflow.run("forbidden_step")

    assert workflow.validation_success is False
    assert "FORBIDDEN EXECUTION" not in capsys.readouterr().out


def test_workflow_config_preserves_multiple_named_workflows(tmp_path: Path):
    project = _create_workflow_project(tmp_path)
    first = Workflow(name="first", proj=project)
    first.configure().set_sequence("step_one")
    first.configure().save()
    second = Workflow(name="second", proj=project)
    second.configure().set_sequence("step_two")
    second.configure().save()

    restored_first = Workflow(name="first", proj=project)
    restored_first.configure().load()
    restored_second = Workflow(name="second", proj=project)
    restored_second.configure().load()

    assert restored_first.sequence == ["step_one"]
    assert restored_second.sequence == ["step_two"]
