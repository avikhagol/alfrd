"""Import the example from its folder, as the Telegram plugin tests do."""

from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Example tests also run alone: never load real plugins/notification routes."""
    monkeypatch.setenv("ALFRD_HOME", str(tmp_path / "alfrd-home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.delenv("ALFRD_NO_PLUGINS", raising=False)
    from alfrd import extensions
    from alfrd.runtime import scheduler

    extensions.reset()
    monkeypatch.setattr(scheduler, "POLL", 0.05)
    monkeypatch.setenv("ALFRD_RUNNER_POLL", "0.05")
    yield
    extensions.reset()


@pytest.fixture
def gsheet(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    return SimpleNamespace(**{name: importlib.import_module(f"alfrd_gsheet.{name}")
                              for name in ("a1", "mapping", "sync", "client")})
