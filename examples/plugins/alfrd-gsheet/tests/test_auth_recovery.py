"""Fresh retries and safe, actionable failures, with no Google requests."""
import importlib.util
import json
from pathlib import Path
import zipfile

import pytest


@pytest.mark.parametrize("error,advice", [
    ("access_denied", "test user"),
    ("admin_policy_enforced", "Workspace administrator"),
    ("org_internal", "audience"),
    ("redirect_uri_mismatch", "Desktop OAuth"),
    ("invalid_client", "client ID and secret"),
    ("deleted_client", "restore or replace"),
    ("disabled_client", "project status"),
    ("temporarily_unavailable", "shortly"),
    ("private-untrusted-error", "publishing status"),
])
def test_callback_advice_and_retry(oauth_modules, tmp_path, error, advice):
    m = oauth_modules
    first = m.actions.auth_start(tmp_path, {}, {})
    old = m.loopback.get(first["session"], "auth")
    failure = m.oauth.authorization_error(error)
    assert isinstance(failure, m.oauth.SyncError)
    assert failure.code == "auth_required"
    old.callback({"state": [old.nonce], "error": [error], "error_description": ["private description"]})
    status = m.actions.auth_status(tmp_path, {}, {"session": old.id})
    assert status["state"] == "denied" and advice in status["message"]
    assert "private" not in status["message"]
    assert not old.verifier and not m.oauth.load_token()
    assert m.oauth.account()["available"] and not m.oauth.account()["signed_in"]
    retry = m.actions.auth_start(tmp_path, {}, {})
    fresh = m.loopback.get(retry["session"], "auth")
    assert fresh.id != old.id and fresh.nonce != old.nonce and fresh.verifier
    assert fresh.status()["state"] == "waiting"


@pytest.mark.parametrize("state", ["waiting", "expired", "cancelled", "error", "mismatch"])
def test_retry_after_pending_or_terminal_failure(oauth_modules, tmp_path, state):
    m = oauth_modules
    old = m.loopback.get(m.actions.auth_start(tmp_path, {}, {})["session"], "auth")
    if state != "waiting":
        old.finish(state)
    fresh = m.loopback.get(m.actions.auth_start(tmp_path, {}, {})["session"], "auth")
    assert old.state != "waiting" and not old.verifier
    assert fresh.state == "waiting" and fresh.nonce != old.nonce
    if state in ("expired", "cancelled"):
        assert "try again" in old.message


def test_listener_failure_can_retry(oauth_modules, monkeypatch, tmp_path):
    m = oauth_modules
    constructor = m.loopback.Session
    def fail(*args, **kwargs):
        raise OSError("private listener details")
    monkeypatch.setattr(m.loopback, "Session", fail)
    with pytest.raises(ValueError, match="local Google sign-in callback") as exc:
        m.actions.auth_start(tmp_path, {}, {})
    assert "private" not in str(exc.value)
    monkeypatch.setattr(m.loopback, "Session", constructor)
    assert m.actions.auth_start(tmp_path, {}, {})["session"]


@pytest.mark.parametrize("configured", [False, True])
def test_paste_preserves_auth_reason(oauth_modules, monkeypatch, tmp_path, configured):
    m = oauth_modules
    if not configured:
        monkeypatch.setenv("ALFRD_GSHEET_OAUTH_CLIENT_ID", "")
    action = next(action for action in m.actions.ACTIONS if action.id == "sheet_info")
    with pytest.raises(ValueError, match="Not signed in to Google" if configured else "missing OAuth client ID or secret"):
        action.run(tmp_path, {}, {"spreadsheet": "s" * 24})



@pytest.fixture
def release_script():
    path = Path(__file__).resolve().parents[4] / "scripts/gsheet_release.py"
    spec = importlib.util.spec_from_file_location("gsheet_release", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("missing", ["client_id", "client_secret", None])
def test_release_fill_and_built_wheel(release_script, tmp_path, missing):
    m = release_script
    values = {"client_id": "test.apps.googleusercontent.com", "client_secret": "synthetic-secret"}
    if missing:
        values[missing] = ""
    env = {m.NAMES[key]: value for key, value in values.items()}
    output = tmp_path / "oauth_client.json"
    wheel = tmp_path / "alfrd_gsheet-test.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(m.MEMBER, json.dumps(values))
    if missing:
        with pytest.raises(ValueError, match=missing): m.fill(output, env)
        with pytest.raises(ValueError, match=missing): m.check(tmp_path)
        assert not output.exists()
    else:
        m.fill(output, env)
        m.check(tmp_path)  # Picker credentials optional for basic sign-in
        assert json.loads(output.read_text())["picker_api_key"] == ""


@pytest.mark.parametrize("payload", [None, "private invalid JSON", '{"client_id": "private-invalid"}'])
def test_release_rejects_absent_or_broken_defaults(release_script, tmp_path, payload):
    with zipfile.ZipFile(tmp_path / "alfrd_gsheet-test.whl", "w") as archive:
        if payload is not None: archive.writestr(release_script.MEMBER, payload)
    with pytest.raises(ValueError) as exc:
        release_script.check(tmp_path)
    assert "private" not in str(exc.value)


@pytest.mark.parametrize("mode", ["missing_secret", "missing_id", "malformed_wheel", "no_wheel"])
def test_release_cli_fails_without_leaking_values(release_script, tmp_path, monkeypatch, capsys, mode):
    import sys
    m = release_script
    monkeypatch.setenv("GSHEET_OAUTH_CLIENT_ID", "test.apps.googleusercontent.com")
    monkeypatch.setenv("GSHEET_OAUTH_CLIENT_SECRET", "private-synthetic-secret")
    if mode.startswith("missing"):
        monkeypatch.setenv("GSHEET_OAUTH_CLIENT_SECRET" if mode == "missing_secret" else "GSHEET_OAUTH_CLIENT_ID", "")
        monkeypatch.setattr(sys, "argv", ["gsheet_release.py", "fill"])
    else:
        if mode == "malformed_wheel":
            (tmp_path / "alfrd_gsheet-bad.whl").write_text("private-synthetic-secret")
        monkeypatch.setattr(sys, "argv", ["gsheet_release.py", "check", str(tmp_path)])
    assert m.main() == 1
    captured = capsys.readouterr()
    assert "private-synthetic-secret" not in captured.out + captured.err
    assert "validation failed" in captured.err
