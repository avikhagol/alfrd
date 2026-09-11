from pathlib import Path

import alfrd
import alfrd.core


def test_documented_public_imports():
    from alfrd import (
        BaseConfig,
        Config,
        Pipeline,
        Project,
        ProjectManifest,
        RepositoryService,
        Workflow,
        get_alfrd_dir,
        get_project_dir,
        register,
        validate,
        validator,
    )
    from alfrd.core import Project as CoreProject, Workflow as CoreWorkflow
    from alfrd.core.config import Config as CoreConfig
    from alfrd.core.manifest import ProjectManifest as CoreProjectManifest
    from alfrd.core.repository import RepositoryService as CoreRepositoryService
    from alfrd.lib import LogFrame

    assert Pipeline is not None
    assert LogFrame is not None
    assert Project is CoreProject
    assert Config is CoreConfig
    assert BaseConfig is type(Config)
    assert ProjectManifest is CoreProjectManifest
    assert RepositoryService is CoreRepositoryService
    assert Workflow is CoreWorkflow
    assert callable(register) and callable(validate) and callable(validator)
    assert get_project_dir() == get_alfrd_dir() / "projects"


def test_version_uses_four_components():
    assert alfrd.__version__ == "0.2.1.0"
    assert len(alfrd.__version__.split(".")) == 4


def test_core_package_is_not_shadowed_by_module():
    core_path = Path(alfrd.core.__file__).resolve()
    assert core_path.name == "__init__.py"
    assert not core_path.parent.with_suffix(".py").exists()
