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
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
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


@pytest.fixture(autouse=True)
def no_external_network(monkeypatch):
    """A failed fake must never contact Google (local HTTP listeners are allowed)."""
    import socket
    connect = socket.socket.connect
    connect_ex = socket.socket.connect_ex
    def guard(original):
        def call(sock, address):
            if sock.family in (socket.AF_INET, socket.AF_INET6) and address[0] not in ("127.0.0.1", "::1", "localhost"):
                raise AssertionError("Tests may only connect to loopback addresses")
            return original(sock, address)
        return call
    monkeypatch.setattr(socket.socket, "connect", guard(connect))
    monkeypatch.setattr(socket.socket, "connect_ex", guard(connect_ex))
    for key in ("OAUTH_CLIENT_ID", "OAUTH_CLIENT_SECRET", "OAUTH_CLIENT_FILE", "PICKER_API_KEY", "GCP_PROJECT_NUMBER"):
        monkeypatch.delenv("ALFRD_GSHEET_" + key, raising=False)
    yield
    import sys
    oauth = sys.modules.get("alfrd_gsheet.oauth")
    loopback = sys.modules.get("alfrd_gsheet.loopback")
    if loopback:
        loopback.cancel_all()
        loopback._registry.clear()
    if oauth:
        oauth._cache.clear()


@pytest.fixture
def oauth_modules(gsheet, monkeypatch):
    from alfrd_gsheet import oauth, loopback, actions, settings
    # Bind/listen is unavailable in some CI sandboxes. Exercise the actual
    # BaseHTTPRequestHandler parser with in-memory connections instead.
    import threading
    class Server:
        next_port = 41000
        def __init__(self, address, handler):
            assert address == ("127.0.0.1", 0)
            self.server_port = Server.next_port
            Server.next_port += 1
            self.handler = handler
            self.closed = threading.Event()
        def serve_forever(self, **kwargs): self.closed.wait()
        def shutdown(self): self.closed.set()
        def server_close(self): self.closed.set()
    monkeypatch.setattr(loopback, "ThreadingHTTPServer", Server)
    for key, value in {"OAUTH_CLIENT_ID": "offline-client.apps.googleusercontent.com",
                       "OAUTH_CLIENT_SECRET": "offline-client-secret",
                       "PICKER_API_KEY": "offline_picker_key_" + "x" * 20,
                       "GCP_PROJECT_NUMBER": "1234567890"}.items():
        monkeypatch.setenv("ALFRD_GSHEET_" + key, value)
    return SimpleNamespace(oauth=oauth, loopback=loopback, actions=actions, settings=settings)
