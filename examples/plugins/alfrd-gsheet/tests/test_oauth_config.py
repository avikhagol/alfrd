"""OAuth defaults are loaded offline using synthetic credentials only."""
import json
import os
from pathlib import Path

import pytest


@pytest.fixture
def config_files(gsheet, monkeypatch, tmp_path):
    from alfrd_gsheet import oauth

    packaged = tmp_path / "package" / "oauth_client.json"
    packaged.parent.mkdir()
    packaged.write_text(json.dumps({
        "client_id": "packaged.apps.googleusercontent.com",
        "client_secret": "packaged-secret",
        "picker_api_key": "p" * 32,
        "project_number": "1234567890",
    }))
    monkeypatch.setattr(oauth, "__file__", str(packaged.with_name("oauth.py")))
    override = tmp_path / "client_secret_offline.json"
    monkeypatch.setenv(oauth.CLIENT_FILE_ENV, str(override))
    return oauth, packaged, override


@pytest.mark.parametrize("installed", [False, True])
def test_file_formats_and_packaged_fallback(config_files, installed):
    oauth, _, override = config_files
    client = {"client_id": "downloaded.apps.googleusercontent.com", "client_secret": "downloaded-secret"}
    override.write_text(json.dumps({"installed": {**client, "project_id": "not-a-number",
                                                  "token_uri": "https://ignored.invalid"}}
                                   if installed else client))
    cfg = oauth.config()
    assert cfg["client_id"] == client["client_id"]
    assert cfg["client_secret"] == client["client_secret"]
    assert cfg["picker_api_key"] == "p" * 32
    assert cfg["project_number"] == "1234567890"
    assert client["client_secret"] not in repr(cfg)
    assert client["client_secret"] not in str(cfg)
    assert "p" * 32 not in repr(cfg)


def test_per_key_precedence(config_files, monkeypatch):
    oauth, _, override = config_files
    override.write_text(json.dumps({"client_id": "file.apps.googleusercontent.com",
                                    "client_secret": "file-secret", "picker_api_key": "f" * 32,
                                    "project_number": "2345678901"}))
    monkeypatch.setenv("ALFRD_GSHEET_OAUTH_CLIENT_ID", "env.apps.googleusercontent.com")
    monkeypatch.setenv("ALFRD_GSHEET_GCP_PROJECT_NUMBER", "3456789012")
    monkeypatch.setenv("ALFRD_GSHEET_OAUTH_CLIENT_SECRET", "")
    assert dict(oauth.config()) == {
        "client_id": "env.apps.googleusercontent.com", "client_secret": "file-secret",
        "picker_api_key": "f" * 32, "project_number": "3456789012",
    }
    monkeypatch.setenv("ALFRD_GSHEET_OAUTH_CLIENT_SECRET", "env-secret")
    monkeypatch.setenv("ALFRD_GSHEET_PICKER_API_KEY", "e" * 32)
    assert oauth.config()["client_secret"] == "env-secret"
    assert oauth.config()["picker_api_key"] == "e" * 32


def test_packaged_installed_format(config_files, monkeypatch):
    oauth, packaged, _ = config_files
    monkeypatch.delenv(oauth.CLIENT_FILE_ENV)
    packaged.write_text(json.dumps({"installed": {"client_id": "desktop.apps.googleusercontent.com",
                                                  "client_secret": "desktop-secret"}}))
    assert oauth.config()["client_id"] == "desktop.apps.googleusercontent.com"
    assert oauth.config()["client_secret"] == "desktop-secret"
    assert oauth.config()["project_number"] == ""
    assert oauth.config()["picker_api_key"] == ""


@pytest.mark.parametrize("packaged_file", [False, True])
def test_web_requires_desktop(config_files, monkeypatch, packaged_file):
    oauth, packaged, override = config_files
    path = packaged if packaged_file else override
    if packaged_file:
        monkeypatch.delenv(oauth.CLIENT_FILE_ENV)
    path.write_text(json.dumps({"web": {"client_secret": "private-web-secret"}}))
    with pytest.raises(ValueError, match="Desktop app client is required") as exc:
        oauth.config()
    assert str(path) in str(exc.value)
    assert "private-web-secret" not in repr(exc.value)
    if not packaged_file:
        assert oauth.CLIENT_FILE_ENV in str(exc.value)


@pytest.mark.parametrize("failure", ["missing", "unreadable"])
def test_file_read_errors(config_files, monkeypatch, failure):
    oauth, _, override = config_files
    if failure == "unreadable":
        original = Path.read_text
        def read(path, *args, **kwargs):
            if path == override:
                raise PermissionError("private-os-error")
            return original(path, *args, **kwargs)
        monkeypatch.setattr(Path, "read_text", read)
    with pytest.raises(ValueError, match="Cannot read") as exc:
        oauth.config()
    assert str(override) in str(exc.value)
    assert oauth.CLIENT_FILE_ENV in str(exc.value)
    assert "private-os-error" not in repr(exc.value)
    assert exc.value.__suppress_context__


@pytest.mark.parametrize("data", [
    '{"client_secret": "private-json-error",',
    '["private-json-error"]',
    '{"installed": "private-json-error"}',
    '{"client_secret": "private-json-error with spaces"}',
    '{"client_secret": ["private-json-error"]}',
])
def test_invalid_file_errors_hide_values(config_files, data):
    oauth, _, override = config_files
    override.write_text(data)
    with pytest.raises(ValueError) as exc:
        oauth.config()
    assert oauth.CLIENT_FILE_ENV in str(exc.value)
    assert str(override) in str(exc.value)
    assert "private-json-error" not in str(exc.value)
    assert "private-json-error" not in repr(exc.value)


def test_env_validation_hides_secret(config_files, monkeypatch):
    oauth, _, override = config_files
    override.write_text("{}")
    monkeypatch.setenv("ALFRD_GSHEET_OAUTH_CLIENT_SECRET", "private env secret")
    with pytest.raises(ValueError, match="ALFRD_GSHEET_OAUTH_CLIENT_SECRET") as exc:
        oauth.config()
    assert "private env secret" not in str(exc.value)
    assert "private env secret" not in repr(exc.value)


def test_repository_defaults_remain_empty(gsheet):
    from alfrd_gsheet import oauth
    path = Path(oauth.__file__).with_name("oauth_client.json")
    assert json.loads(path.read_text()) == {key: "" for key in oauth.ENV}


@pytest.mark.parametrize("installed", [False, True])
@pytest.mark.parametrize("xdg", [False, True])
def test_user_file_discovery(config_files, monkeypatch, installed, xdg):
    oauth, packaged, _ = config_files
    monkeypatch.delenv(oauth.CLIENT_FILE_ENV)
    if not xdg:
        monkeypatch.delenv("XDG_CONFIG_HOME")
    packaged.write_text("{}")
    base = Path(os.environ["XDG_CONFIG_HOME"]) if xdg else Path.home() / ".config"
    if xdg:
        decoy = Path.home() / ".config" / "alfrd" / "gsheet_oauth_client.json"
        decoy.parent.mkdir(parents=True)
        decoy.write_text("invalid JSON: the XDG override must ignore this file")
    path = base / "alfrd" / "gsheet_oauth_client.json"
    path.parent.mkdir(parents=True)
    values = {"client_id": "user.apps.googleusercontent.com", "client_secret": "user-secret",
              "picker_api_key": "u" * 32, "project_number": "4567890123"}
    path.write_text(json.dumps({"installed": values} if installed else values))
    path.chmod(0o600)
    assert oauth.user_client_path() == path
    assert dict(oauth.require_config(picker=True)) == values
    assert oauth.account()["available"] is True


def test_user_file_per_key_precedence(config_files, monkeypatch):
    oauth, _, override = config_files
    path = oauth.user_client_path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"client_id": "user.apps.googleusercontent.com",
                                "client_secret": "user-secret", "picker_api_key": "u" * 32}))
    override.write_text(json.dumps({"client_id": "file.apps.googleusercontent.com",
                                    "client_secret": "", "project_number": "2345678901"}))
    monkeypatch.setenv("ALFRD_GSHEET_OAUTH_CLIENT_ID", "env.apps.googleusercontent.com")
    assert dict(oauth.config()) == {
        "client_id": "env.apps.googleusercontent.com", "client_secret": "user-secret",
        "picker_api_key": "u" * 32, "project_number": "2345678901"}
    monkeypatch.delenv("ALFRD_GSHEET_OAUTH_CLIENT_ID")
    assert oauth.config()["client_id"] == "file.apps.googleusercontent.com"
    monkeypatch.delenv(oauth.CLIENT_FILE_ENV)
    assert oauth.config()["project_number"] == "1234567890"


@pytest.mark.parametrize("data", [
    '{"client_secret": "private-user-secret",',
    '["private-user-secret"]',
    '{"installed": "private-user-secret"}',
    '{"client_secret": "private-user-secret with spaces"}',
])
def test_invalid_user_file_is_clear_and_redacted(config_files, monkeypatch, data, caplog):
    oauth, _, _ = config_files
    monkeypatch.delenv(oauth.CLIENT_FILE_ENV)
    path = oauth.user_client_path()
    path.parent.mkdir(parents=True)
    path.write_text(data)
    with pytest.raises(ValueError, match="per-user OAuth client file") as exc:
        oauth.config()
    assert str(path) in str(exc.value)
    assert "private-user-secret" not in str(exc.value) + caplog.text


@pytest.mark.parametrize("operation", ["stat", "read_text"])
def test_unreadable_user_file_is_redacted(config_files, monkeypatch, operation):
    oauth, _, _ = config_files
    monkeypatch.delenv(oauth.CLIENT_FILE_ENV)
    path = oauth.user_client_path()
    path.parent.mkdir(parents=True)
    path.write_text("{}")
    original = getattr(Path, operation)

    def denied(candidate, *args, **kwargs):
        if candidate == path:
            raise PermissionError("private-os-error")
        return original(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, operation, denied)
    with pytest.raises(ValueError, match="Cannot read.*per-user OAuth client file") as exc:
        oauth.config()
    assert "private-os-error" not in str(exc.value)
    assert exc.value.__suppress_context__


@pytest.mark.parametrize("mode,warns", [(0o600, False), (0o640, True), (0o604, True)])
def test_user_file_permissions_warn_only(config_files, monkeypatch, caplog, mode, warns):
    oauth, _, _ = config_files
    monkeypatch.delenv(oauth.CLIENT_FILE_ENV)
    path = oauth.user_client_path()
    path.parent.mkdir(parents=True)
    path.write_text('{"client_secret": "private-permissions-secret"}')
    path.chmod(mode)
    assert oauth.config()["client_secret"] == "private-permissions-secret"
    assert ("group- or world-readable" in caplog.text) is warns
    assert "private-permissions-secret" not in caplog.text


def test_unconfigured_message_and_maintainer_log(config_files, monkeypatch, caplog, tmp_path):
    oauth, packaged, _ = config_files
    monkeypatch.delenv(oauth.CLIENT_FILE_ENV)
    packaged.write_text("{}")
    from alfrd_gsheet import actions
    message = ("Google sign-in isn't configured (missing OAuth client ID or secret). Ask the maintainer "
               "to configure Google sign-in, or use Advanced → service account in Settings.")
    with caplog.at_level("INFO"), pytest.raises(ValueError) as exc:
        actions.auth_start(tmp_path, {}, {})
    assert str(exc.value) == message
    assert oauth.CLIENT_FILE_ENV in caplog.text
    assert "ALFRD_GSHEET_OAUTH_CLIENT_ID" in caplog.text
    assert "~/.config/alfrd/gsheet_oauth_client.json" in caplog.text
    ui = Path(__file__).parents[1] / "alfrd_gsheet" / "web" / "project_ui.js"
    assert message in ui.read_text()
