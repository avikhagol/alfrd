from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


# Plain CLI output in every environment. On GitHub Actions rich forces colour,
# which splits option names such as "--port" with ANSI codes in --help output.
for _name in ("GITHUB_ACTIONS", "FORCE_COLOR", "PY_COLORS"):
    os.environ.pop(_name, None)
os.environ["NO_COLOR"] = "1"
os.environ["TERM"] = "dumb"


@pytest.fixture(autouse=True)
def isolated_alfrd_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Keep project files and imports local to each test."""
    monkeypatch.setenv("ALFRD_HOME", str(tmp_path / "alfrd-home"))
    # The user's real superuser.yaml (alfrd su) must not change what tests see.
    monkeypatch.setenv("ALFRD_SUPERUSER_FILE", str(tmp_path / "alfrd-home" / "superuser.yaml"))
    # Runner ticks: in this process, and in runners started as processes.
    from alfrd.runtime import scheduler

    monkeypatch.setattr(scheduler, "POLL", 0.05)
    monkeypatch.setenv("ALFRD_RUNNER_POLL", "0.05")
    monkeypatch.chdir(tmp_path)

    modules_before = set(sys.modules)
    yield tmp_path

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
def isolated_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """`alfrd serve` writes its token file under the config dir: never the real one."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))  # plugins/ and themes/
    monkeypatch.setenv("ALFRD_NO_PLUGINS", "")  # `serve --safe-mode` exports it: restored at teardown
    monkeypatch.delenv("ALFRD_NO_PLUGINS")
    # setenv first so teardown restores the original state even when
    # `alfrd serve --debug` exports these into os.environ during a test.
    for name in ("ALFRD_TOKEN", "ALFRD_SECRET_KEY"):
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    from alfrd import extensions

    extensions.reset()
    path_before = list(sys.path)
    yield
    extensions.reset()  # plugin panels never leak into the next test
    sys.path[:] = path_before


@pytest.fixture(autouse=True)
def stop_leaked_live_threads(monkeypatch: pytest.MonkeyPatch):
    """Stop every LiveHub / Watcher a test created, so no poller thread outlives it."""
    try:
        from alfrd import studio_live
    except ImportError:  # the gui extra is optional
        yield
        return

    hubs: list = []
    watchers: list = []
    hub_init = studio_live.LiveHub.__init__
    watcher_init = studio_live.Watcher.__init__

    def tracked_hub_init(self, *args, **kwargs):
        hub_init(self, *args, **kwargs)
        hubs.append(self)

    def tracked_watcher_init(self, *args, **kwargs):
        watcher_init(self, *args, **kwargs)
        watchers.append(self)

    monkeypatch.setattr(studio_live.LiveHub, "__init__", tracked_hub_init)
    monkeypatch.setattr(studio_live.Watcher, "__init__", tracked_watcher_init)
    yield
    for hub in hubs:
        hub.stop_all()
    for watcher in watchers:
        watcher.stop()
    for watcher in watchers:
        thread = getattr(watcher, "_thread", None)
        if thread is not None and thread.is_alive():
            thread.join(timeout=2)


@pytest.fixture(autouse=True)
def authenticated_test_client(monkeypatch: pytest.MonkeyPatch):
    """`app.test_client()` sends the app's access token as a Bearer header.

    Tests of the unauthenticated behaviour build ``flask.testing.FlaskClient(app)``
    directly (see tests/test_access_tokens.py).
    """
    try:
        from flask import Flask
        from flask.testing import FlaskClient
    except ImportError:  # the gui extra is optional
        return

    class AuthenticatedClient(FlaskClient):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            token = self.application.config.get("ACCESS_TOKEN")
            if token:
                self.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {token}"

    monkeypatch.setattr(Flask, "test_client_class", AuthenticatedClient)


@pytest.fixture(autouse=True)
def no_real_web_server(monkeypatch: pytest.MonkeyPatch):
    """`alfrd serve` in tests: the (fake) app's run() stands in for waitress, which would block."""
    monkeypatch.setattr("alfrd.cli._serve_production",
                        lambda app, host, port: app.run(host=host, port=port, debug=False))
