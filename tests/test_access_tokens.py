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
    assert "paste the access link here" in body
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


# --- durable launch and in-app pairing ---------------------------------------

def _login(client, credential=TOKEN, remember=False, **kwargs):
    client.get("/login", **{k: v for k, v in kwargs.items() if k in {"base_url", "environ_overrides"}})
    with client.session_transaction(**{k: v for k, v in kwargs.items() if k == "base_url"}) as saved:
        nonce = saved["_alfrd_login_nonce"]
    return client.post("/login", data={"login_nonce": nonce, "credential": credential,
                                      "remember": "on" if remember else ""}, **kwargs)


@pytest.mark.parametrize("remember", [False, True])
def test_pairing_cookie_and_clean_redirect(app, remember):
    client = anon(app)
    response = _login(client, "http://localhost/studio/?token=" + TOKEN, remember)
    assert response.status_code == 303
    assert response.location == "/studio/"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    cookie = response.headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie and "Path=/" in cookie
    assert "Domain=" not in cookie and "Secure;" not in cookie
    assert ("Expires=" in cookie) == remember
    assert app.permanent_session_lifetime.days == 30
    assert client.get("/api/studio/session").status_code == 200
    with client.session_transaction() as saved:
        assert "_alfrd_login_nonce" not in saved
        assert saved["_alfrd_csrf_token"]


def test_https_pairing_sets_secure_cookie(app):
    response = _login(anon(app), base_url="https://localhost")
    assert response.status_code == 303
    assert "Secure;" in response.headers["Set-Cookie"]


@pytest.mark.parametrize("origin", ["null", None, "http://localhost"])
@pytest.mark.parametrize("fetch_site", ["same-origin", None])
def test_login_private_form_origin(app, origin, fetch_site):
    # no-referrer suppresses Referer and makes Firefox's form POST Origin null.
    headers = {"Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}
    if origin is not None:
        headers["Origin"] = origin
    if fetch_site is not None:
        headers["Sec-Fetch-Site"] = fetch_site
    client = anon(app)
    response = _login(client, "http://localhost/studio/?token=" + TOKEN, headers=headers)
    assert response.status_code == 303
    assert response.location == "/studio/"
    assert client.get("/api/studio/session").status_code == 200


def test_login_firefox_loopback_form_headers(app):
    client = anon(app)
    base_url = "http://127.0.0.1:5000"
    response = _login(client, base_url + "/studio/?token=" + TOKEN, base_url=base_url,
                      headers={"Origin": "null", "Sec-Fetch-Site": "same-origin",
                               "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"})
    assert response.status_code == 303
    assert response.location == "/studio/"
    assert client.get("/api/studio/session", base_url=base_url).status_code == 200


@pytest.mark.parametrize("headers", [
    {"Origin": "https://evil.example", "Sec-Fetch-Site": "same-origin"},
    {"Origin": "http://localhost:5000"},
    {"Origin": "http://127.0.0.1"},
    {"Origin": "null", "Referer": "https://evil.example/page"},
    {"Referer": "https://evil.example/page"},
    {"Origin": "null", "Sec-Fetch-Site": "cross-site"},
    {"Origin": "null", "Sec-Fetch-Site": "same-site"},
    {"Origin": "malformed"},
])
def test_login_foreign_source_shows_recovery(app, headers):
    client = anon(app)
    response = _login(client, headers=headers)
    assert response.status_code == 403
    assert b'role="alert"' in response.data
    assert b"Reload the access page" in response.data
    assert TOKEN.encode() not in response.data
    assert client.get("/api/studio/session").status_code == 401


@pytest.mark.parametrize("nonce", [None, "wrong"])
def test_login_private_form_requires_nonce(app, nonce):
    client = anon(app)
    client.get("/login")
    data = {"credential": TOKEN}
    if nonce is not None:
        data["login_nonce"] = nonce
    response = client.post("/login", headers={"Origin": "null", "Sec-Fetch-Site": "same-origin"}, data=data)
    assert response.status_code == 403
    assert b'role="alert"' in response.data
    assert b"Reload the access page" in response.data
    assert client.get("/api/studio/session").status_code == 401


def test_login_private_form_expired_session_nonce(app, monkeypatch):
    from itsdangerous import TimestampSigner
    now = 2_000_000_000
    monkeypatch.setattr(TimestampSigner, "get_timestamp", lambda self: now)
    client = anon(app)
    client.get("/login")
    with client.session_transaction() as saved:
        nonce = saved["_alfrd_login_nonce"]
    now += 30 * 24 * 60 * 60 + 1
    response = client.post("/login", headers={"Origin": "null"},
                           data={"credential": TOKEN, "login_nonce": nonce})
    assert response.status_code == 403
    assert b"form has expired" in response.data
    assert client.get("/api/studio/session").status_code == 401


@pytest.mark.parametrize("credential", ["", "wrong", "https://foreign.example/studio/?token=" + TOKEN,
    "http://localhost/other/?token=" + TOKEN, "http://localhost/studio/?token=" + TOKEN + "&token=other",
    "http://localhost:5000/studio/?token=" + TOKEN])
def test_paste_rejects_invalid_or_foreign_link(app, credential):
    client = anon(app)
    response = _login(client, credential)
    assert response.status_code == 401
    assert b'role="alert"' in response.data
    assert b"Run alfrd url" in response.data
    assert TOKEN.encode() not in response.data
    if credential:
        assert credential.encode() not in response.data
    assert client.get("/api/studio/session").status_code == 401


def test_login_nonce_origin_body_limit_and_throttle(tmp_path):
    app = _app(tmp_path, ACCESS_THROTTLE_LIMIT=2)
    client = anon(app)
    refused = client.post("/login", data={"credential": TOKEN})
    assert refused.status_code == 403
    assert refused.headers["Cache-Control"] == "no-store"
    assert refused.headers["Referrer-Policy"] == "no-referrer"
    assert _login(client, headers={"Origin": "https://foreign.example"}).status_code == 403
    assert _login(client, "x" * 9000).status_code == 413
    assert _login(client, "wrong").status_code == 401
    assert _login(client, "wrong").status_code == 401
    response = _login(client)
    assert response.status_code == 429 and response.headers["Retry-After"]
    assert b"Too many attempts" in response.data


def test_login_only_exception_works_on_lan_read_only(tmp_path, monkeypatch):
    from alfrd import superuser
    original = superuser.load
    def locked():
        data = original()
        data["lockdown"]["read_only"] = True
        return data
    monkeypatch.setattr(superuser, "load", locked)
    app = _app(tmp_path, RUNTIME_MUTATIONS_ENABLED=False)
    client = anon(app)
    assert _login(client, environ_overrides={"REMOTE_ADDR": "10.0.0.5"}).status_code == 303
    assert client.get("/api/studio/session").status_code == 200
    assert client.post("/api/projects/connect", json={"path": "/tmp"}).status_code == 403


def test_proxy_login_form_link_and_redirect(app):
    client = anon(app)
    env = {"SCRIPT_NAME": "/prefix"}
    response = client.get("/login", environ_overrides=env)
    assert b'action="/prefix/login"' in response.data
    assert b'href="/prefix/studio/assets/site.webmanifest"' in response.data
    with client.session_transaction() as saved:
        nonce = saved["_alfrd_login_nonce"]
    response = client.post("/login", environ_overrides=env, data={"login_nonce": nonce,
                           "credential": "http://localhost/prefix/studio/?token=" + TOKEN})
    assert response.status_code == 303 and response.location == "/prefix/studio/"


def test_durable_restart_rotation_policy_and_cookie(tmp_path):
    policy = {"host": "127.0.0.1", "database": "test"}
    token, secret = auth.durable_credentials(5999, policy)
    app = _app(tmp_path, ACCESS_TOKEN=token, SECRET_KEY=secret)
    client = anon(app)
    assert _login(client, token, True).status_code == 303
    cookie = client.get_cookie(app.config["SESSION_COOKIE_NAME"]).value
    auth.write_server_file(5999, "u", token, secret)
    auth.remove_server_file(5999)
    assert auth.durable_credentials(5999, policy) == (token, secret)
    restarted = anon(_app(tmp_path, ACCESS_TOKEN=token, SECRET_KEY=secret))
    restarted.set_cookie(app.config["SESSION_COOKIE_NAME"], cookie)
    assert restarted.get("/api/studio/session").status_code == 200
    new_token, new_secret = auth.durable_credentials(5999, policy, rotate=True)
    assert new_token != token and new_secret != secret
    rotated = anon(_app(tmp_path, ACCESS_TOKEN=new_token, SECRET_KEY=new_secret))
    rotated.set_cookie(app.config["SESSION_COOKIE_NAME"], cookie)
    assert rotated.get("/api/studio/session").status_code == 401
    assert rotated.get("/studio/?token=" + token).status_code == 401
    changed = auth.durable_credentials(5999, {**policy, "all_projects": True})
    assert changed != (new_token, new_secret)
    assert auth.durable_credentials(5999, policy) != (token, secret)
    path = auth.config_dir() / "credentials-5999.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_durable_concurrent_starts_share_generation():
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=8) as pool:
        pairs = list(pool.map(lambda _: auth.durable_credentials(5998, {"policy": "same"}), range(20)))
    assert len(set(pairs)) == 1


@pytest.mark.parametrize("unsafe", ["mode", "symlink", "directory", "malformed", "lock_symlink", "parent_mode", "parent_symlink"])
def test_durable_refuses_unsafe_files(tmp_path, unsafe, monkeypatch):
    auth.durable_credentials(5997, {})
    path = auth.config_dir() / "credentials-5997.json"
    if unsafe == "mode":
        path.chmod(0o644)
    elif unsafe == "symlink":
        path.unlink()
        path.symlink_to(tmp_path / "missing")
    elif unsafe == "directory":
        path.unlink()
        path.mkdir()
    elif unsafe == "malformed":
        path.write_text("{}")
    elif unsafe == "lock_symlink":
        lock = path.with_suffix(".lock")
        lock.unlink()
        lock.symlink_to(path)
    elif unsafe == "parent_mode":
        path.parent.chmod(0o755)
    else:
        link = tmp_path / "config-link"
        link.symlink_to(path.parent)
        monkeypatch.setattr(auth, "config_dir", lambda: link)
    with pytest.raises((OSError, ValueError)):
        auth.durable_credentials(5997, {})


def test_serve_restart_rotate_and_nonloopback_opt_in(monkeypatch):
    configs = []
    class FakeApp:
        def run(self, **kwargs):
            pass
    monkeypatch.setattr("alfrd.gui.create_app", lambda config=None: configs.append(config) or FakeApp())
    runner = CliRunner()
    args = ["serve", "--port", "5996", "--no-browser", "--no-discover"]
    for extra in ([], [], ["--rotate-token"]):
        result = runner.invoke(alfrd_cli, args + extra)
        assert result.exit_code == 0, result.output
    keys = [(c["ACCESS_TOKEN"], c["SECRET_KEY"]) for c in configs]
    assert keys[0] == keys[1] and keys[2][0] != keys[0][0] and keys[2][1] != keys[0][1]
    for fixed in (["--token", "fixed"], ["--no-token"]):
        assert runner.invoke(alfrd_cli, args + ["--rotate-token"] + fixed).exit_code != 0
    for _ in range(2):
        assert runner.invoke(alfrd_cli, args + ["--host", "0.0.0.0"]).exit_code == 0
    assert configs[-1]["ACCESS_TOKEN"] != configs[-2]["ACCESS_TOKEN"]
    for _ in range(2):
        result = runner.invoke(alfrd_cli, args + ["--host", "0.0.0.0", "--persist-credentials"])
        assert result.exit_code == 0, result.output
    assert configs[-1]["ACCESS_TOKEN"] == configs[-2]["ACCESS_TOKEN"]


def test_remembered_cookie_server_age_is_bounded(app, monkeypatch):
    from itsdangerous import TimestampSigner
    now = 2_000_000_000
    monkeypatch.setattr(TimestampSigner, "get_timestamp", lambda self: now)
    client = anon(app)
    assert _login(client, remember=True).status_code == 303
    now += 30 * 24 * 60 * 60 + 1
    assert client.get("/api/studio/session").status_code == 401


def test_fixed_credentials_precedence_and_policy_reuse_refusal():
    assert auth.durable_credentials(5995, {}, token="operator", secret_key="operator-key") == ("operator", "operator-key")
    assert auth.durable_credentials(5995, {}) == ("operator", "operator-key")
    with pytest.raises(ValueError, match="policy changed"):
        auth.durable_credentials(5995, {"wider": True}, token="operator", secret_key="operator-key")


def test_global_paste_throttle_bounds_distributed_attempts(app):
    client = anon(app)
    for i in range(100):
        assert _login(client, "wrong", environ_overrides={"REMOTE_ADDR": f"192.0.2.{i}"}).status_code == 401
    assert _login(client, environ_overrides={"REMOTE_ADDR": "192.0.2.200"}).status_code == 429


def test_cli_fingerprints_restored_project_scope(monkeypatch):
    import alfrd.gui.project_selection as selection
    seen = []
    original = auth.durable_credentials
    def capture(port, policy, **kwargs):
        seen.append(policy)
        return original(port, policy, **kwargs)
    class FakeApp:
        def run(self, **kwargs):
            pass
    monkeypatch.setattr("alfrd.gui.create_app", lambda config=None: FakeApp())
    monkeypatch.setattr(auth, "durable_credentials", capture)
    monkeypatch.setattr(selection, "restore", lambda app: app.config.update(STUDIO_PROJECTS=["restored-project"]))
    result = CliRunner().invoke(alfrd_cli, ["serve", "--port", "5994", "--no-browser", "--no-discover"])
    assert result.exit_code == 0, result.output
    assert seen[0]["projects"] == ["restored-project"]


def test_debug_rotation_is_not_repeated_by_reloader_child(monkeypatch):
    configs = []
    class FakeApp:
        def run(self, **kwargs):
            pass
    monkeypatch.setattr("alfrd.gui.create_app", lambda config=None: configs.append(config) or FakeApp())
    args = ["serve", "--port", "5993", "--debug", "--rotate-token", "--no-browser", "--no-discover"]
    assert CliRunner().invoke(alfrd_cli, args).exit_code == 0
    monkeypatch.setenv("WERKZEUG_RUN_MAIN", "true")
    result = CliRunner().invoke(alfrd_cli, args)
    assert result.exit_code == 0, result.output
    assert configs[0]["ACCESS_TOKEN"] == configs[1]["ACCESS_TOKEN"]
    assert configs[0]["SECRET_KEY"] == configs[1]["SECRET_KEY"]
