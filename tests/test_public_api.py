from pathlib import Path

import alfrd
import alfrd.core


def test_documented_public_imports():
    from alfrd import (
        BaseConfig,
        CONFIG_MAPPING,
        Config,
        ArtifactRef,
        BatchResult,
        LogFrame,
        PipelineContext,
        PipelineCore,
        PipelineStepBase,
        PipelineStepValidatorBase,
        PipelineStepValidatorResult,
        ProjectManifest,
        RepositoryService,
        StepResult,
        get_alfrd_dir,
    )
    from alfrd.config import Config as ConfigModuleConfig
    from alfrd.core import LogFrame as CoreLogFrame, PipelineCore as CorePipelineCore
    from alfrd.core.logframe import LogFrame as LogFrameModuleLogFrame
    from alfrd.manifest import ProjectManifest as ManifestModuleProjectManifest
    from alfrd.repository import RepositoryService as RepositoryModuleRepositoryService

    assert all(
        item is not None
        for item in (
            ArtifactRef,
            BatchResult,
            PipelineContext,
            PipelineStepBase,
            PipelineStepValidatorBase,
            PipelineStepValidatorResult,
            StepResult,
        )
    )
    assert LogFrame is CoreLogFrame is LogFrameModuleLogFrame
    assert PipelineCore is CorePipelineCore
    assert Config is ConfigModuleConfig
    assert BaseConfig is type(Config)
    assert isinstance(CONFIG_MAPPING, dict)
    assert ProjectManifest is ManifestModuleProjectManifest
    assert RepositoryService is RepositoryModuleRepositoryService
    assert isinstance(get_alfrd_dir(), Path)


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
    assert alfrd.PipelineCore is alfrd.PipelineCore
