from pathlib import Path

import alfrd
import alfrd.core


def test_documented_public_imports():
    from alfrd import (
        BaseConfig,
        Config,
        ArtifactRef,
        BatchResult,
        Pipeline,
        PipelineContext,
        PipelineCore,
        PipelineStepBase,
        PipelineStepValidatorBase,
        PipelineStepValidatorResult,
        Project,
        ProjectManifest,
        RepositoryService,
        StepResult,
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
    assert all(
        item is not None
        for item in (
            ArtifactRef,
            BatchResult,
            PipelineContext,
            PipelineCore,
            PipelineStepBase,
            PipelineStepValidatorBase,
            PipelineStepValidatorResult,
            StepResult,
        )
    )
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
    import re

    pyproject = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    expected = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.M).group(1)
    assert alfrd.__version__ == expected  # installed metadata matches pyproject.toml
    assert len(alfrd.__version__.split(".")) == 4


def test_core_package_is_not_shadowed_by_module():
    core_path = Path(alfrd.core.__file__).resolve()
    assert core_path.name == "__init__.py"
    assert not core_path.parent.with_suffix(".py").exists()


def test_runner_shim_and_cli_start_without_heavy_imports():
    # Every turn starts a shim and every plan a runner: they must not load pandas/SQLAlchemy/numpy.
    import subprocess
    import sys

    code = ("import sys, alfrd, alfrd.runtime.shim, alfrd.runtime.scheduler, alfrd.cli\n"
            "print(sorted(m for m in ('pandas', 'sqlalchemy', 'numpy') if m in sys.modules))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert out.strip() == "[]"


def test_lazy_exports_resolve():
    assert all(hasattr(alfrd, name) for name in alfrd.__all__)
    import importlib

    runtime = importlib.import_module("alfrd.runtime")
    assert all(hasattr(runtime, name) for name in runtime.__all__)
    assert all(hasattr(alfrd.core, name) for name in alfrd.core.__all__)
    assert alfrd.Pipeline is alfrd.Pipeline
