from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from alfrd.plugins import REGISTERED_STEPS, VALIDATE_AFTER, VALIDATE_BEFORE, VALIDATORS

# Plain CLI output in every environment. On GitHub Actions rich forces colour,
# which splits option names such as "--port" with ANSI codes in --help output.
for _name in ("GITHUB_ACTIONS", "FORCE_COLOR", "PY_COLORS"):
    os.environ.pop(_name, None)
os.environ["NO_COLOR"] = "1"
os.environ["TERM"] = "dumb"


@pytest.fixture(autouse=True)
def isolated_alfrd_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Keep project files, imports, and registries local to each test."""
    monkeypatch.setenv("ALFRD_HOME", str(tmp_path / "alfrd-home"))
    monkeypatch.chdir(tmp_path)

    registries = (REGISTERED_STEPS, VALIDATE_BEFORE, VALIDATE_AFTER, VALIDATORS)
    for registry in registries:
        registry.clear()
    modules_before = set(sys.modules)
    yield tmp_path

    for registry in registries:
        registry.clear()
    for name in set(sys.modules) - modules_before:
        module = sys.modules.get(name)
        module_file = getattr(module, "__file__", None)
        if module_file:
            try:
                Path(module_file).resolve().relative_to(tmp_path.resolve())
            except ValueError:
                continue
            sys.modules.pop(name, None)


@pytest.fixture(autouse=True)
def no_real_web_server(monkeypatch: pytest.MonkeyPatch):
    """`alfrd serve` in tests: the (fake) app's run() stands in for waitress, which would block."""
    monkeypatch.setattr("alfrd.cli._serve_production",
                        lambda app, host, port: app.run(host=host, port=port, debug=False))
