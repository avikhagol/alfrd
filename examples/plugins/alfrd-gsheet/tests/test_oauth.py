"""Offline installed-app tests: deterministic Google transport, no live credentials."""
import base64
import json
import stat
import sys
import threading
import time
from types import ModuleType, SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

SID = "s" * 24


def record(oauth, **updates):
    return {"version": 1, "client_id": oauth.config()["client_id"], "refresh_token": "offline-refresh",
            "scopes": list(oauth.OAUTH_SCOPES), "email": "ada@example.org", "sub": "123",
            "status": "ok", "status_reason": "", **updates}


def fake_post(responses, calls):
    class Session:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, url, **kwargs):
            assert self.trust_env is False and kwargs["allow_redirects"] is False
            calls.append((url, kwargs))
            status, data = responses.pop(0)
            return SimpleNamespace(status_code=status, json=lambda: data, close=lambda: None)
    return Session


def test_pkce_and_url(oauth_modules):
    oauth = oauth_modules.oauth
    assert oauth.pkce_challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk") == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    cfg = oauth.config()
    query = parse_qs(urlsplit(oauth.authorization_url(cfg, "http://127.0.0.1:1234/", "state", "v" * 64)).query)
    assert set(query) == {"response_type", "client_id", "redirect_uri", "scope", "code_challenge", "code_challenge_method", "state", "access_type", "prompt"}
    assert query["scope"][0].split() == list(oauth.OAUTH_SCOPES)
    assert query["code_challenge_method"] == ["S256"]
    assert query["access_type"] == ["offline"] and query["prompt"] == ["consent"]


def test_config_precedence_and_safe_validation(oauth_modules, monkeypatch):
    oauth = oauth_modules.oauth
    assert oauth.config()["project_number"] == "1234567890"
    monkeypatch.setenv("ALFRD_GSHEET_GCP_PROJECT_NUMBER", "PRIVATE!")
    with pytest.raises(ValueError, match="ALFRD_GSHEET_GCP_PROJECT_NUMBER") as exc:
        oauth.config()
    assert "PRIVATE!" not in str(exc.value)


def test_token_permissions_corruption_and_client_change(oauth_modules):
    oauth = oauth_modules.oauth
    assert oauth.account()["status"] == "signed_out"
    oauth.save_token(record(oauth))
    assert stat.S_IMODE(oauth.token_path().stat().st_mode) == 0o600
    assert stat.S_IMODE(oauth.token_path().parent.stat().st_mode) == 0o700
    assert oauth.account()["signed_in"]
    oauth.save_token(record(oauth, client_id="changed"))
    assert oauth.account()["status_reason"] == "client_changed"
    oauth.token_path().write_text("{bad")
    assert oauth.account()["status"] == "signed_out"
    oauth.token_path().write_text('{"version":2}')
    assert not oauth.load_token()


def test_exchange_validates_pkce_scopes_and_identity(oauth_modules):
    oauth = oauth_modules.oauth
    claims = {"aud": oauth.config()["client_id"], "iss": "https://accounts.google.com", "exp": time.time() + 600,
              "email": "ada@example.org", "email_verified": True, "sub": "123"}
    jwt = "header." + base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=") + ".sig"
    calls = []
    data = {"scope": " ".join(oauth.OAUTH_SCOPES), "refresh_token": "offline-refresh", "id_token": jwt}
    result = oauth.exchange("offline-code", "v" * 64, "http://127.0.0.1:1234/", oauth.config(),
                            session_factory=fake_post([(200, data)], calls))
    assert result["email"] == "ada@example.org" and result["refresh_token"] == "offline-refresh"
    assert not oauth.token_path().exists()  # session commits only after checking cancellation
    form = calls[0][1]["data"]
    assert form["code_verifier"] == "v" * 64 and form["grant_type"] == "authorization_code"
    with pytest.raises(ValueError, match="callback"):
        oauth.exchange("code", "short", "http://127.0.0.1/", oauth.config())
    calls = []
    with pytest.raises(oauth.ScopeMissing):
        oauth.exchange("code", "v" * 64, "http://127.0.0.1/", oauth.config(),
                       session_factory=fake_post([(200, {"scope": "openid", "refresh_token": "offline"}), (200, {})], calls))
    assert calls[-1][0] == oauth.REVOKE_URL
    data["id_token"] = "header." + base64.urlsafe_b64encode(json.dumps({**claims, "aud": "other"}).encode()).decode() + ".sig"
    with pytest.raises(ValueError, match="identity"):
        oauth.exchange("code", "v" * 64, "http://127.0.0.1/", oauth.config(), session_factory=fake_post([(200, data)], []))
    data.pop("id_token")
    assert oauth.exchange("code", "v" * 64, "http://127.0.0.1/", oauth.config(),
                          session_factory=fake_post([(200, data)], []))["email"] == ""


@pytest.mark.parametrize("revoked", [True, False])
def test_signout_always_forgets(oauth_modules, revoked):
    oauth = oauth_modules.oauth
    oauth.save_token(record(oauth))
    session = oauth_modules.loopback.start("auth", cfg=oauth.config())
    calls = []
    assert oauth.sign_out(session_factory=fake_post([(200 if revoked else 500, {})], calls)) is revoked
    assert not oauth.token_path().exists() and not oauth._cache
    assert session.status()["state"] == "cancelled"
    assert calls[0][1]["data"] == {"token": "offline-refresh"}


@pytest.mark.parametrize("explicit,key,mode", [(None, True, "service_account"), (None, False, "oauth"),
                                             ("oauth", True, "oauth"), ("service_account", True, "service_account")])
def test_backcompat_and_mode_switching(oauth_modules, explicit, key, mode, monkeypatch):
    settings, oauth = oauth_modules.settings, oauth_modules.oauth
    raw = json.dumps({"type": "service_account", "client_email": "bot@example.org", "private_key": "offline-key",
                      "token_uri": oauth.TOKEN_URL})
    values = {"credentials_json": raw} if key else {}
    if explicit: values["auth_mode"] = explicit
    before = values.copy()
    assert settings.auth_mode(values) == mode
    assert settings.normalize(values)["auth_mode"] == mode
    assert values == before  # no settings-file migration or discarded SA key
    client = settings.make_client(values)
    assert client.oauth is (mode == "oauth")
    if mode == "service_account": assert client._raw == raw
    else: assert isinstance(client._raw, oauth.CredentialSource)
    assert settings.normalize({"auth_mode": "oauth", "credentials_json": "leftover-invalid"})["credentials_json"] == "leftover-invalid"
    with pytest.raises(ValueError, match="auth_mode"):
        settings.normalize({"auth_mode": "bogus"})


def install_fake_google(monkeypatch, outcomes, calls):
    """Exercise the real OAuth subclass without requiring Google libs in host env."""
    class RefreshError(Exception): pass
    class Credentials:
        def __init__(self, **kw):
            self.__dict__.update(kw)
            self.valid = False
        def refresh(self, request):
            calls.append(request)
            outcome = outcomes.pop(0)
            if outcome == "invalid_grant": raise RefreshError("invalid_grant: private token response")
            self.token, self.valid = outcome, True
        def apply(self, headers): headers["authorization"] = "Bearer " + self.token
    for name in ("google", "google.oauth2", "google.auth"):
        module = ModuleType(name); module.__path__ = []
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setitem(sys.modules, "google.oauth2.credentials", SimpleNamespace(Credentials=Credentials))
    monkeypatch.setitem(sys.modules, "google.auth.exceptions", SimpleNamespace(RefreshError=RefreshError))


def test_refresh_cache_invalid_grant_and_no_second_request(oauth_modules, monkeypatch):
    oauth = oauth_modules.oauth
    oauth.save_token(record(oauth))
    calls = []
    install_fake_google(monkeypatch, ["offline-access", "invalid_grant"], calls)
    creds, lock = oauth.credentials(oauth.CredentialSource(), time.monotonic() + 1)
    assert not creds.valid
    creds.refresh(lambda: None)
    assert creds.valid and creds.token == "offline-access"
    assert oauth.credentials(oauth.CredentialSource(), time.monotonic() + 1)[0] is creds
    with pytest.raises(ValueError, match="expired or was revoked") as exc:
        creds.refresh(lambda: None)
    assert exc.value.code == "auth_required"
    assert not oauth.account()["signed_in"] and oauth.account()["status"] == "needs_reauth"
    assert oauth.load_token()["status_reason"] == "invalid_grant"
    with pytest.raises(ValueError, match="expired or was revoked"):
        oauth.credentials(oauth.CredentialSource(), time.monotonic() + 1)
    assert len(calls) == 2 and not oauth._cache
    oauth.save_token(record(oauth, refresh_token="new-refresh"))
    oauth.mark_needs_reauth("old-refresh")
    assert oauth.account()["signed_in"]


def test_redaction_and_create_single_attempt(oauth_modules, monkeypatch):
    oauth, actions = oauth_modules.oauth, oauth_modules.actions
    oauth.save_token(record(oauth))
    oauth._cache["fake"] = SimpleNamespace(token="offline-access"), threading.Lock()
    raw = " ".join(oauth.secret_values())
    clean = actions._redact({"nested": [raw]}, {})
    for secret in oauth.secret_values():
        if secret: assert secret not in str(clean)
    from alfrd_gsheet.client import RestClient
    client = RestClient(oauth.CredentialSource())
    calls = []
    def request(*args, **kw):
        calls.append((args, kw)); return {"spreadsheetId": SID}
    monkeypatch.setattr(client, "_request", request)
    assert client.create_spreadsheet("Lab runs", deadline=42)["id"] == SID
    assert calls[0][1]["attempts"] == 1 and calls[0][1]["json"] == {"properties": {"title": "Lab runs"}}
    for title in ("", "x" * 101, None):
        with pytest.raises(ValueError, match="title"): client.create_spreadsheet(title, deadline=42)
    assert len(calls) == 1


def test_cli_headless_status_and_mode_switch(oauth_modules, monkeypatch):
    from alfrd.extensions import settings as store
    from alfrd_gsheet.cli import cli
    from typer.testing import CliRunner
    oauth = oauth_modules.oauth
    monkeypatch.setattr(store, "values", lambda _: {})
    runner = CliRunner()
    assert "not signed in" in runner.invoke(cli, ["auth", "status"]).output
    oauth.save_token(record(oauth))
    assert "ada@example.org" in runner.invoke(cli, ["auth", "status"]).output
    oauth.mark_needs_reauth()
    assert "invalid_grant" in runner.invoke(cli, ["auth", "status"]).output
    monkeypatch.setattr(oauth, "browser_available", lambda: False)
    result = runner.invoke(cli, ["auth", "login"])
    assert result.exit_code == 1 and "service_account" in result.output
    assert not oauth_modules.loopback._registry
    monkeypatch.setenv("ALFRD_GSHEET_OAUTH_CLIENT_ID", "")
    assert "not available" in runner.invoke(cli, ["auth", "status"]).output


def test_real_google_refresh_with_fake_http(oauth_modules):
    pytest.importorskip("google.oauth2.credentials")
    requests = pytest.importorskip("requests")
    from alfrd_gsheet.client import RestClient
    oauth = oauth_modules.oauth
    oauth.save_token(record(oauth))
    calls = []
    class Session:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def request(self, method, url, **kw):
            calls.append((url, kw))
            response = requests.Response()
            if url == oauth.TOKEN_URL:
                assert "refresh_token=offline-refresh" in str(kw["data"])
                response.status_code = 400
                response._content = b'{"error":"invalid_grant"}'
            else:
                raise AssertionError("No API request after failed refresh")
            return response
    client = RestClient(oauth.CredentialSource(), 1, session_factory=Session, credential_factory=oauth.credentials)
    for _ in range(2):
        with pytest.raises(ValueError, match="expired or was revoked"):
            client.get(SID, "A1", deadline=time.monotonic() + 2)
    assert len(calls) == 1 and oauth.account()["status"] == "needs_reauth"


@pytest.mark.parametrize("status,code", [(401, "auth_required"), (403, "file_access"), (404, "file_access")])
def test_oauth_rest_error_messages_and_codes(oauth_modules, status, code):
    from alfrd_gsheet.client import RestClient
    oauth = oauth_modules.oauth
    oauth.save_token(record(oauth))
    creds = SimpleNamespace(valid=True, refresh_token="offline-refresh", apply=lambda headers: None)
    class Session:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def request(self, *args, **kw):
            return SimpleNamespace(status_code=status, json=lambda: {}, close=lambda: None)
    client = RestClient(oauth.CredentialSource(), session_factory=Session,
                        credential_factory=lambda *args: (creds, threading.Lock()))
    with pytest.raises(ValueError) as exc:
        client.get(SID, "A1", deadline=time.monotonic() + 1)
    assert exc.value.code == code and "service-account" not in str(exc.value)
    assert (oauth.account()["status"] == "needs_reauth") is (status == 401)


def test_auth_actions_never_return_tokens_and_respect_mode(oauth_modules, monkeypatch, tmp_path):
    m = oauth_modules
    start = m.actions.auth_start(tmp_path, {}, {})
    assert start["auth_url"].startswith("https://accounts.google.com/")
    assert m.actions.auth_status(tmp_path, {}, {"session": start["session"]})["state"] == "waiting"
    session = m.loopback.get(start["session"], "auth")
    session.exchange = lambda *args: record(m.oauth)
    session.callback({"state": [session.nonce], "code": ["code"]})
    assert m.actions.auth_status(tmp_path, {}, {"session": start["session"]})["state"] == "signed_in"
    monkeypatch.setattr(m.oauth, "access_token", lambda **kw: "offline-access")
    picker = m.actions.picker_start(tmp_path, {}, {})
    assert picker["picker_url"].startswith("http://127.0.0.1:")
    assert "offline-access" not in json.dumps(picker)
    m.actions.picker_cancel(tmp_path, {}, {"session": picker["session"]})
    assert m.actions.picker_status(tmp_path, {}, {"session": picker["session"]})["state"] == "cancelled"
    with pytest.raises(ValueError, match="Sign-in method"):
        m.actions.auth_start(tmp_path, {"credentials_json": "legacy-key"}, {})
    monkeypatch.setenv("ALFRD_GSHEET_PICKER_API_KEY", "")
    assert m.oauth.account()["available"] and m.oauth.account()["create"] and not m.oauth.account()["picker"]
