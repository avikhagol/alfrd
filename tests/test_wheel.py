from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import venv
import zipfile
import tarfile
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
UV = shutil.which("uv")  # cached build env and package cache: seconds instead of a minute
pytestmark = pytest.mark.slow


def _project_version() -> str:
    """Version from pyproject.toml (the only place it is written)."""
    text = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    return re.search(r'^version\s*=\s*"([^"]+)"', text, re.M).group(1)


VERSION = _project_version()
EXPECTED_WHEEL_PATHS = {
    "alfrd/schemas/project-manifest-v1.schema.json",
    "alfrd/core/logframe.py",
    "alfrd/core/pipeline.py",
    "alfrd/gui/templates/auth/landing.htm",
    "alfrd/gui/auth.py",
    "alfrd/gui/model/schema.sql",
    "alfrd/gui/studio.py",
    "alfrd/web/__init__.py",
    "alfrd/web/index.html",
    "alfrd/web/css/studio.css",
    "alfrd/web/theme.css",
    "alfrd/web/css/themes/obsidian-orbit/theme.css",
    "alfrd/web/css/themes/obsidian-orbit/theme.json",
    "alfrd/web/css/themes/daylight-orbit/theme.css",
    "alfrd/web/css/themes/daylight-orbit/theme.json",
    "alfrd/schemas/plugin_catalog.v1.json",
    "alfrd/extensions/__init__.py",
    "alfrd/extensions/catalog.py",
    "alfrd/extensions/convert.py",
    "alfrd/extensions/installer.py",
    "alfrd/extensions/jobs.py",
    "alfrd/extensions/scaffold.py",
    "alfrd/extensions/themes.py",
    "alfrd/web/js/components/viewers.js",
    "alfrd/web/js/components/plugin_api.js",
    "alfrd/web/js/components/settings_plugins.js",
    "alfrd/web/js/components/settings_plugins_view.js",
    "alfrd/web/js/components/plugins_browse.js",
    "alfrd/web/js/components/plugins_install_dialog.js",
    "alfrd/web/js/components/plugin_jobs.js",
    "alfrd/web/js/vendor/purify.es.mjs",
    "alfrd/web/js/vendor/LICENSE-dompurify.txt",
    "alfrd/web/js/vendor/README.txt",
    "alfrd/web/js/app.js",
    "alfrd/web/js/components/canvas.js",
    "alfrd/web/js/utils/yaml_parser.js",
    "alfrd/web/assets/templates/avica.yaml",
    "alfrd/web/assets/favicon.ico",
    "alfrd/web/assets/apple-touch-icon.png",
    "alfrd/web/assets/site.webmanifest",
}


@pytest.fixture(scope="module")
def built_wheel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    output_dir = tmp_path_factory.mktemp("wheel-dist")
    command = [UV, "build", "--sdist", "--wheel", "--out-dir"] if UV else [sys.executable, "-m", "build", "--outdir"]
    subprocess.run(
        [*command, str(output_dir)],
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
    venv.EnvBuilder(with_pip=not UV).create(environment)
    bin_dir = environment / ("Scripts" if os.name == "nt" else "bin")
    python = bin_dir / ("python.exe" if os.name == "nt" else "python")
    alfrd_command = bin_dir / ("alfrd.exe" if os.name == "nt" else "alfrd")

    install = [UV, "pip", "install", "--python", str(python)] if UV else [str(python), "-m", "pip", "install", "--disable-pip-version-check"]
    subprocess.run(
        [*install, f"{built_wheel}"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )

    smoke = """
from importlib import resources
from pathlib import Path
from alfrd import (
    ArtifactDefinition, ArtifactRef, BatchResult, Config, PipelineContext, PipelineCore,
    PipelineStepBase, PipelineStepValidatorBase, PipelineStepValidatorResult,
    LogFrame, LogFrameEventSink, ProjectManifest, RepositoryService, StepResult,
    __version__,
)
from alfrd.core import LogFrame as CoreLogFrame
from alfrd.core.logframe import LogFrameAdapter
from alfrd.core.logging import logger
from alfrd.runtime import RuntimeService, RuntimeStore
from alfrd.gui.services import RuntimeCatalogReader
import logging
assert __version__ == '@VERSION@'
assert ProjectManifest and RepositoryService and LogFrame and Config
assert all((ArtifactRef, BatchResult, PipelineContext, PipelineCore, PipelineStepBase,
            PipelineStepValidatorBase, PipelineStepValidatorResult, StepResult))
assert all((ArtifactDefinition, LogFrameEventSink, RuntimeService, RuntimeStore, RuntimeCatalogReader))
assert LogFrame is CoreLogFrame
assert LogFrameAdapter
assert isinstance(logger, logging.Logger)
root = resources.files('alfrd.gui')
for item in (
    'templates/auth/landing.htm',
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
assert client.get('/api/projects').status_code == 401
assert client.get('/login').status_code == 200
bearer = {'Authorization': 'Bearer ' + app.config['ACCESS_TOKEN']}
assert client.get('/api/projects', headers=bearer).get_json() == {'projects': []}
assert client.get('/', headers=bearer).headers['Location'].endswith('/studio/')
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
    assert "serve" in help_result.stdout
    assert "runtime" in help_result.stdout
    for command in (
        "serve",
        "gui",
        "url",
        "projects",
        "runtime",
        "manifest",
        "import",
        "plan",
        "plugin",
    ):
        subprocess.run(
            [str(alfrd_command), command, "--help"],
            cwd=tmp_path,
            env=environment_vars,
            check=True,
            capture_output=True,
            text=True,
        )
