from __future__ import annotations

import os
import re
import subprocess
import sys
import venv
import zipfile
import tarfile
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _project_version() -> str:
    """Version from pyproject.toml (the only place it is written)."""
    text = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    return re.search(r'^version\s*=\s*"([^"]+)"', text, re.M).group(1)


VERSION = _project_version()
EXPECTED_WHEEL_PATHS = {
    "alfrd/schemas/project-manifest-v1.schema.json",
    "alfrd/core/logframe.py",
    "alfrd/core/pipeline.py",
    "alfrd/runtime/adapters.py",
    "alfrd/gui/templates/dashboard/index.htm",
    "alfrd/gui/templates/dashboard/layout.htm",
    "alfrd/gui/templates/dashboard/project_details.htm",
    "alfrd/gui/static/alfrd.css",
    "alfrd/gui/model/schema.sql",
    "alfrd/gui/studio.py",
    "alfrd/web/__init__.py",
    "alfrd/web/index.html",
    "alfrd/web/css/studio.css",
    "alfrd/web/js/app.js",
    "alfrd/web/js/components/canvas.js",
    "alfrd/web/js/utils/yaml_parser.js",
    "alfrd/web/assets/templates/avica.yaml",
}


@pytest.fixture(scope="module")
def built_wheel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    output_dir = tmp_path_factory.mktemp("wheel-dist")
    subprocess.run(
        [sys.executable, "-m", "build", "--outdir", str(output_dir)],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    wheels = list(output_dir.glob(f"alfrd-{VERSION}-*.whl"))
    assert len(wheels) == 1
    return wheels[0]


def test_built_wheel_contains_resources_metadata_and_no_core_shadow(built_wheel: Path):
    with zipfile.ZipFile(built_wheel) as archive:
        names = set(archive.namelist())
        metadata_name = next(name for name in names if name.endswith(".dist-info/METADATA"))
        metadata = archive.read(metadata_name).decode()

    assert EXPECTED_WHEEL_PATHS <= names
    assert "alfrd/core.py" not in names
    assert f"Version: {VERSION}" in metadata
    assert "Requires-Python: >=3.10" in metadata

    source_distribution = next(built_wheel.parent.glob(f"alfrd-{VERSION}.tar.gz"))
    with tarfile.open(source_distribution) as archive:
        source_names = set(archive.getnames())
    for expected in EXPECTED_WHEEL_PATHS:
        assert any(name.endswith(f"/src/{expected}") for name in source_names), expected


def test_wheel_installs_and_public_imports_and_cli_work_outside_checkout(
    built_wheel: Path, tmp_path: Path
):
    environment = tmp_path / "venv"
    venv.EnvBuilder(with_pip=True).create(environment)
    bin_dir = environment / ("Scripts" if os.name == "nt" else "bin")
    python = bin_dir / ("python.exe" if os.name == "nt" else "python")
    alfrd_command = bin_dir / ("alfrd.exe" if os.name == "nt" else "alfrd")

    subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            f"{built_wheel}[gui]",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )

    smoke = """
from importlib import resources
from pathlib import Path
from alfrd import (
    ArtifactDefinition, ArtifactRef, BatchResult, Pipeline, PipelineContext, PipelineCore,
    PipelineStepBase, PipelineStepValidatorBase, PipelineStepValidatorResult,
    LogFrame, LogFrameEventSink, Project, ProjectManifest, RepositoryService, StepResult, Workflow,
    __version__, register, validate, validator,
)
from alfrd.core import LogFrame as CoreLogFrame
from alfrd.core.logframe import LogFrameAdapter
from alfrd.core.logging import logger
from alfrd.lib import LogFrame as LegacyLogFrame
from alfrd.runtime import RuntimeEventSink, RuntimePipelineRunner, RuntimeService, RuntimeStore
from alfrd.gui.services import RuntimeCatalogReader
import logging
assert __version__ == '@VERSION@'
assert Pipeline and Project and ProjectManifest and RepositoryService and Workflow and LogFrame
assert all((ArtifactRef, BatchResult, PipelineContext, PipelineCore, PipelineStepBase,
            PipelineStepValidatorBase, PipelineStepValidatorResult, StepResult))
assert all((ArtifactDefinition, LogFrameEventSink, RuntimeEventSink,
            RuntimePipelineRunner, RuntimeService, RuntimeStore, RuntimeCatalogReader))
assert LogFrame is CoreLogFrame is LegacyLogFrame
assert LogFrameAdapter
assert isinstance(logger, logging.Logger)
assert register and validate and validator
root = resources.files('alfrd.gui')
for item in (
    'templates/dashboard/index.htm',
    'templates/dashboard/layout.htm',
    'templates/dashboard/project_details.htm',
    'static/alfrd.css',
    'model/schema.sql',
):
    assert root.joinpath(*item.split('/')).is_file(), item
assert resources.files('alfrd.schemas').joinpath('project-manifest-v1.schema.json').is_file()
import alfrd.core
assert Path(alfrd.core.__file__).name == '__init__.py'
from alfrd.gui import create_app
app = create_app({'TESTING': True})
client = app.test_client()
assert client.get('/health').get_json() == {'status': 'ok'}
assert client.get('/api/version').get_json() == {'version': __version__}
assert client.get('/api/projects').get_json() == {'projects': []}
"""
    environment_vars = os.environ.copy()
    environment_vars.pop("PYTHONPATH", None)
    environment_vars["ALFRD_HOME"] = str(tmp_path / "installed-home")
    subprocess.run(
        [str(python), "-c", smoke.replace("@VERSION@", VERSION)],
        cwd=tmp_path,
        env=environment_vars,
        check=True,
        capture_output=True,
        text=True,
    )
    help_result = subprocess.run(
        [str(alfrd_command), "--help"],
        cwd=tmp_path,
        env=environment_vars,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "init" in help_result.stdout
    assert "run" in help_result.stdout
    assert "serve" in help_result.stdout
    for command in (
        "init",
        "ls",
        "lsp",
        "run",
        "add",
        "rm",
        "inspect",
        "nrun",
        "serve",
        "gui",
    ):
        subprocess.run(
            [str(alfrd_command), command, "--help"],
            cwd=tmp_path,
            env=environment_vars,
            check=True,
            capture_output=True,
            text=True,
        )
