"""Access tokens for `alfrd serve`: every data route needs the token (cookie or Bearer)."""

from __future__ import annotations

import os
import stat
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from flask.testing import FlaskClient
from typer.testing import CliRunner
from werkzeug.routing import FloatConverter, IntegerConverter, PathConverter, UUIDConverter

from alfrd.cli import alfrd_cli
from alfrd.gui import auth, create_app
from alfrd.gui.auth import PUBLIC_ENDPOINTS, SESSION_KEY
from alfrd.gui.services import RuntimeCatalogReader
from alfrd.runtime import RuntimeService, RuntimeStore

TOKEN = "test-access-token"


def _app(tmp_path: Path, **extra):
    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    service = RuntimeService(store)
    config = {
        "TESTING": True,
        "SECRET_KEY": "test-secret",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'catalog.sqlite'}",
        "RUNTIME_SERVICE": service,
        "CATALOG_READER": RuntimeCatalogReader(service),
        "ACCESS_TOKEN": TOKEN,
    }
    config.update(extra)
    return create_app(config)


@pytest.fixture
def app(tmp_path):
    return _app(tmp_path)


def anon(app) -> FlaskClient:
    """A client without the Bearer header the conftest fixture adds."""
    return FlaskClient(app, use_cookies=True)


def _sample(rule, name):
    converter = rule._converters[name]
    if isinstance(converter, PathConverter):
        return "a/b"
    if isinstance(converter, IntegerConverter):
        return 1
    if isinstance(converter, FloatConverter):
        return 1.0
    if isinstance(converter, UUIDConverter):
        return uuid.uuid4()
    items = getattr(converter, "items", None)  # AnyConverter
    if items:
        return sorted(items)[0]
    return "x"


def _requests(app):
    for rule in app.url_map.iter_rules():
        _, path = rule.build({name: _sample(rule, name) for name in rule.arguments})
        for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            yield rule, path, method


# --- the sweep ----------------------------------------------------------------

def test_every_non_public_route_needs_the_token(app):
    """Every route in the URL map, every method: 401 without the token (new routes included)."""
    client = anon(app)
    wrong = []
    requests = list(_requests(app))
    assert len(requests) > 100  # the sweep really walks the whole URL map
    for rule, path, method in requests:
        if rule.endpoint in PUBLIC_ENDPOINTS:
            continue
        response = client.open(path, method=method)
        if response.status_code != 401:
            wrong.append(f"{method} {rule.rule} ({rule.endpoint}) -> {response.status_code}")
    assert wrong == []


def test_only_the_listed_endpoints_answer_without_the_token(app):
    client = anon(app)
    open_endpoints = set()
    for rule, path, method in _requests(app):
        if client.open(path, method=method).status_code != 401:
            open_endpoints.add(rule.endpoint)
    assert open_endpoints == set(PUBLIC_ENDPOINTS)


def test_public_endpoints_answer(app):
    client = anon(app)
    assert client.get("/health").status_code == 200
    assert client.get("/api/health").get_json() == {"status": "ok"}
    assert client.get("/api/version").status_code == 200
    assert client.get("/studio/").status_code == 200
    login = client.get("/login")
    assert login.status_code == 200
    assert "alfrd url --port" in login.get_data(as_text=True)
    assert TOKEN not in login.get_data(as_text=True)


# --- 401 responses ------------------------------------------------------------

def test_api_401_is_json_with_www_authenticate(app):
    response = anon(app).get("/api/studio/session")
    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == 401
    assert response.headers["WWW-Authenticate"].lower().startswith("bearer")


def test_browser_401_is_the_landing_page(app):
    response = anon(app).get("/", headers={"Accept": "text/html,*/*;q=0.8"})
    assert response.status_code == 401
    assert response.mimetype == "text/html"
    body = response.get_data(as_text=True)
    assert "needs its access link" in body
    assert "alfrd url --port 80" in body  # test client host "localhost" has no port
    assert TOKEN not in body
    page = anon(app).get("/login", base_url="http://127.0.0.1:5055").get_data(as_text=True)
    assert "alfrd url --port 5055" in page


# --- ?token= exchange ---------------------------------------------------------

def test_token_in_url_sets_cookie_and_redirects_without_it(app):
    client = anon(app)
    response = client.get(f"/studio/?project=demo&token={TOKEN}")
    assert response.status_code == 302
    location = urlsplit(response.headers["Location"])
    assert location.path == "/studio/"
    assert "token" not in location.query
    assert location.query == "project=demo"
    cookie = response.headers["Set-Cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=Strict" in cookie
    assert "Secure" not in cookie  # plain http
    assert TOKEN not in cookie

    session = client.get("/api/studio/session")
    assert session.status_code == 200
    assert session.get_json()["app"] == "alfrd"


def test_token_redirect_keeps_the_proxy_prefix(app):
    response = anon(app).get(f"/studio/?token={TOKEN}", base_url="http://localhost/alfrd/")
    assert response.status_code == 302
    assert urlsplit(response.headers["Location"]).path == "/alfrd/studio/"


def test_cookie_is_secure_over_https(app):
    response = anon(app).get(f"/studio/?token={TOKEN}", base_url="https://localhost")
    assert response.status_code == 302
    assert "Secure" in response.headers["Set-Cookie"]


@pytest.mark.parametrize("trust", ["", "1"])
def test_forwarded_https_only_counts_with_trust_proxy(tmp_path, monkeypatch, trust):
    monkeypatch.setenv("ALFRD_TRUST_PROXY", trust)
    response = anon(_app(tmp_path)).get(f"/studio/?token={TOKEN}", headers={"X-Forwarded-Proto": "https"})
    assert response.status_code == 302
    assert ("Secure" in response.headers["Set-Cookie"]) == bool(trust)


def test_wrong_token_in_url_is_401(app):
    client = anon(app)
    response = client.get("/studio/?token=wrong", headers={"Accept": "text/html"})
    assert response.status_code == 401
    assert client.get("/api/studio/session").status_code == 401


def test_session_from_another_token_is_rejected(tmp_path):
    old = _app(tmp_path / "a", ACCESS_TOKEN="old-token")
    new = _app(tmp_path / "b", ACCESS_TOKEN="new-token")  # same SECRET_KEY
    client = anon(old)
    assert client.get("/studio/?token=old-token").status_code == 302
    cookie = client.get_cookie("alfrd-session")
    assert cookie is not None
    other = anon(new)
    other.set_cookie("alfrd-session", cookie.value)
    assert other.get("/api/studio/session").status_code == 401


# --- Bearer + throttling ------------------------------------------------------

def test_bearer_token(app):
    client = anon(app)
    assert client.get("/api/studio/session", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200
    assert client.get("/api/studio/session", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/api/studio/session", headers={"Authorization": "Basic abc"}).status_code == 401


def test_status_api_token_works_only_on_the_status_api(tmp_path):
    app = _app(tmp_path, API_TOKEN="status-only")
    client = anon(app)
    bearer = {"Authorization": "Bearer status-only"}
    assert client.get("/api/v1/projects", headers=bearer).status_code == 200
    assert client.get("/api/studio/session", headers=bearer).status_code == 401
    assert client.get("/api/v1/projects").status_code == 401  # loopback needs a token now too


def test_failed_attempts_are_throttled(app):
    client = anon(app)
    bad = {"Authorization": "Bearer nope"}
    for _ in range(10):
        assert client.get("/api/studio/session", headers=bad).status_code == 401
    throttled = client.get("/api/studio/session", headers=bad)
    assert throttled.status_code == 429
    assert int(throttled.headers["Retry-After"]) >= 1
    # Every token attempt from this address waits, the right one too.
    good = client.get("/api/studio/session", headers={"Authorization": f"Bearer {TOKEN}"})
    assert good.status_code == 429
    assert client.get(f"/studio/?token={TOKEN}").status_code == 429
    # Another address is not affected.
    other = client.get("/api/studio/session", headers={"Authorization": f"Bearer {TOKEN}"},
                       environ_base={"REMOTE_ADDR": "127.0.0.2"})
    assert other.status_code == 200


def test_throttle_window_clears(tmp_path):
    app = _app(tmp_path, ACCESS_THROTTLE_LIMIT=2, ACCESS_THROTTLE_WINDOW=0.05)
    client = anon(app)
    for _ in range(2):
        client.get("/api/studio/session", headers={"Authorization": "Bearer nope"})
    assert client.get("/api/studio/session", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 429
    import time

    time.sleep(0.1)
    assert client.get("/api/studio/session", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200


def test_throttle_memory_is_bounded(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(auth.time, "monotonic", lambda: clock[0])
    throttle = auth._Throttle(max_addresses=100)
    for i in range(1000):
        throttle.fail(f"10.0.{i // 256}.{i % 256}", 60)
    assert len(throttle.failures) == 100 and "10.0.3.231" in throttle.failures  # the newest are kept
    for _ in range(10):
        throttle.fail("10.0.3.231", 60)
    assert throttle.retry_after("10.0.3.231", 10, 60) > 0  # still throttled after the others were dropped
    clock[0] += 61
    throttle.fail("192.0.2.1", 60)
    assert list(throttle.failures) == ["192.0.2.1"]  # entries older than the window are pruned


def test_requests_without_a_token_are_not_counted(app):
    client = anon(app)
    for _ in range(30):
        assert client.get("/api/studio/session").status_code == 401
    assert client.get("/api/studio/session", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200


# --- interplay with the mutation checks ---------------------------------------

def test_unauthenticated_post_is_401_not_403(app):
    response = anon(app).post("/api/projects/connect", json={"path": "/tmp"})
    assert response.status_code == 401


def test_authenticated_post_from_another_host_is_still_403(app):
    client = app.test_client()  # Bearer token from conftest
    with client.session_transaction() as session:
        session["_alfrd_csrf_token"] = "known"
    response = client.post("/api/projects/connect", json={"path": "/tmp"}, headers={"X-CSRF-Token": "known"},
                           environ_base={"REMOTE_ADDR": "10.0.0.5"})
    assert response.status_code == 403


def test_authenticated_post_without_csrf_is_403(app):
    response = app.test_client().post("/api/projects/connect", json={"path": "/tmp"})
    assert response.status_code == 403


# --- configuration ------------------------------------------------------------

def test_create_app_generates_a_token_when_none_is_given(tmp_path):
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'c.sqlite'}"})
    assert len(app.config["ACCESS_TOKEN"]) >= 32
    assert app.config["ACCESS_TOKEN_REQUIRED"] is True
    assert anon(app).get("/api/projects").status_code == 401


def test_access_check_can_be_turned_off(tmp_path):
    app = _app(tmp_path, ACCESS_TOKEN_REQUIRED=False)
    assert anon(app).get("/api/studio/session").status_code == 200


def test_session_cookie_is_strict_and_httponly(app):
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Strict"
    assert app.config["SESSION_COOKIE_HTTPONLY"] is True


# --- token file ---------------------------------------------------------------

def test_server_file_round_trip_and_mode(tmp_path):
    path = auth.write_server_file(5099, "http://127.0.0.1:5099/studio/", "tok", "key")
    assert path == tmp_path / "config" / "alfrd" / "server-5099.json"  # XDG_CONFIG_HOME from conftest
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert auth.read_server_file(5099) == {
        "url": "http://127.0.0.1:5099/studio/", "token": "tok", "pid": os.getpid(), "secret_key": "key",
    }
    # Rewriting keeps 0600 and leaves no temporary file behind.
    auth.write_server_file(5099, "http://127.0.0.1:5099/studio/", "tok2", "key")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert [p.name for p in path.parent.iterdir()] == ["server-5099.json"]
    assert auth.remove_server_file(5099) is True
    assert not path.exists()
    assert auth.read_server_file(5099) is None


def test_server_file_of_another_process_is_kept(tmp_path):
    path = auth.write_server_file(5098, "u", "t", "k")
    data = auth.read_server_file(5098)
    data["pid"] = os.getpid() + 1
    path.write_text(__import__("json").dumps(data), encoding="utf-8")
    assert auth.remove_server_file(5098) is False
    assert path.exists()


def test_serve_writes_token_file_while_running_and_removes_it(monkeypatch, tmp_path):
    seen = {}

    class FakeApp:
        def run(self, **kwargs):
            path = auth.server_file(5097)
            seen["mode"] = stat.S_IMODE(path.stat().st_mode)
            seen["data"] = auth.read_server_file(5097)

    configs = []
    monkeypatch.setattr("alfrd.gui.create_app", lambda config=None: configs.append(config) or FakeApp())
    result = CliRunner().invoke(alfrd_cli, ["serve", "--port", "5097", "--no-browser"])
    assert result.exit_code == 0, result.output
    token = configs[0]["ACCESS_TOKEN"]
    assert seen["mode"] == 0o600
    assert seen["data"]["token"] == token
    assert seen["data"]["secret_key"] == configs[0]["SECRET_KEY"]
    assert configs[0]["SESSION_COOKIE_NAME"] == "alfrd-session-5097"
    assert f"/studio/?token={token}" in result.output
    assert not auth.server_file(5097).exists()


def test_serve_debug_shares_token_with_reloader_child(monkeypatch):
    configs = []

    class FakeApp:
        def run(self, **kwargs):
            pass

    monkeypatch.setattr("alfrd.gui.create_app", lambda config=None: configs.append(config) or FakeApp())
    monkeypatch.setenv("ALFRD_TOKEN", "pinned")
    monkeypatch.setenv("ALFRD_SECRET_KEY", "pinned-key")
    # Reloader parent: does not write the token file.
    result = CliRunner().invoke(alfrd_cli, ["serve", "--port", "5096", "--debug", "--no-browser"])
    assert result.exit_code == 0, result.output
    assert configs[0]["ACCESS_TOKEN"] == "pinned"
    assert configs[0]["SECRET_KEY"] == "pinned-key"
    assert not auth.server_file(5096).exists()


def test_system_usage_for_the_footer_needs_the_token(app):
    assert anon(app).get("/api/studio/system").status_code == 401
    data = app.test_client().get("/api/studio/system").get_json()
    assert 0 <= data["cpu"] <= 100 and data["cores"] >= 1
    assert 0 < data["mem_used"] <= data["mem_total"]
