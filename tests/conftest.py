from __future__ import annotations

import sys
from pathlib import Path

import pytest

from alfrd.plugins import REGISTERED_STEPS, VALIDATE_AFTER, VALIDATE_BEFORE, VALIDATORS


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
