import sys
from pathlib import Path

import pytest

from alfrd import get_project_dir
from alfrd.core.project import Project


def _write_project_plugin(root: Path) -> tuple[Path, Path]:
    helpers = root / "tmp_helpers"
    helpers.mkdir()
    (helpers / "__init__.py").write_text("", encoding="utf-8")
    (helpers / "validators.py").write_text(
        "from alfrd.plugins import validator\n"
        "@validator(desc='test validator')\n"
        "def validator_for_project(name='pyt'):\n"
        "    return bool(name)\n",
        encoding="utf-8",
    )
    plugin = root / "plugin.py"
    plugin.write_text(
        "from alfrd.plugins import register, validate\n"
        "from tmp_helpers.validators import validator_for_project\n"
        "@validate(by=['validator_for_project'])\n"
        "@register(desc='test step')\n"
        "def registered_project(name='pyt'):\n"
        "    return f'hello {name}'\n",
        encoding="utf-8",
    )
    return plugin, helpers


def test_project_lifecycle_is_confined_to_configured_home(tmp_path: Path):
    plugin, helpers = _write_project_plugin(tmp_path)
    project = Project("pyt", use_symlink=True)

    project.create()
    project_dir = project.get_projdir()
    assert project_dir == get_project_dir() / "pyt"
    assert project_dir.is_relative_to(tmp_path)

    project.add(plugin, helpers)
    assert (project_dir / plugin.name).exists()
    assert (project_dir / helpers.name / "validators.py").exists()

    project.clear_project()
    project.load_project(purge_existing=True)
    assert str(project_dir) not in sys.path
    functions = project.get_functions()
    assert list(functions["REGISTERED"]) == ["registered_project"]
    assert list(functions["VALIDATORS"]) == ["validator_for_project"]
    assert list(functions["VALIDATE_BEFORE"]) == ["registered_project"]
    assert list(functions["VALIDATE_AFTER"]) == ["registered_project"]

    project.rm()
    assert not project_dir.exists()


def test_list_projects_uses_configured_home(tmp_path: Path):
    Project("one").create()
    Project("two").create()

    assert Project().list_projects() == ["one", "two"]
    assert get_project_dir().is_relative_to(tmp_path)


@pytest.mark.parametrize("name", ("../escape", "nested/name", "/tmp/escape", ".", ".."))
def test_project_names_cannot_escape_configured_home(name: str, tmp_path: Path):
    with pytest.raises(ValueError, match="project name"):
        Project(name).get_projdir(create=True)

    assert not (tmp_path / "escape").exists()


def test_loaded_plugin_files_have_distinct_project_scoped_module_names(tmp_path: Path):
    source = tmp_path / "plugins"
    source.mkdir()
    for index in (1, 2):
        (source / f"step_{index}.py").write_text(
            "from alfrd.plugins import register\n"
            f"@register(desc='step {index}')\n"
            f"def step_{index}():\n"
            f"    return {index}\n",
            encoding="utf-8",
        )

    project = Project("module-identities")
    project.create()
    project.add(*(sorted(source.glob("*.py"))))
    project.load_project(purge_existing=True)

    project_root = project.get_projdir().absolute()
    loaded_names = {
        name
        for name, module in sys.modules.items()
        if getattr(module, "__file__", None)
        and Path(module.__file__).absolute().is_relative_to(project_root)
    }
    assert len(loaded_names) == 2
    assert all(name.startswith("_alfrd_project_") for name in loaded_names)
