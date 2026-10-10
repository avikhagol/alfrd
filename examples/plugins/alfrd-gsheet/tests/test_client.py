"""Exercise REST/auth/deadline behavior using transport fakes, never Google."""

import importlib
import json
import sys
import threading
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

SID = "a" * 24
SECRET = "PRIVATE_SECRET_DO_NOT_REPORT"


def saved():
    return {"credentials_json": json.dumps({"type": "service_account", "client_email": "bot@example.com",
                                           "private_key": SECRET,
                                           "token_uri": "https://oauth2.googleapis.com/token"}),
            "default_spreadsheet_id": SID}


class Response:
    def __init__(self, status=200, data=None):
        self.status_code = status
        self.data = data if data is not None else {}
        self.closed = False

    def json(self):
        if isinstance(self.data, Exception):
            raise self.data
        return self.data

    def close(self):
        self.closed = True


@pytest.fixture
def transport(gsheet):
    responses, calls, sessions, auth_calls = [], [], [], []

    class Session:
        def __enter__(self):
            sessions.append(self)
            return self

        def __exit__(self, *args):
            self.closed = True

        def request(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            result = responses.pop(0)
            if callable(result):
                return result()
            if isinstance(result, Exception):
                raise result
            return result

    class Creds:
        valid = False

        def refresh(self, request):
            auth_calls.append(request)
            self.valid = True

        def apply(self, headers):
            headers["authorization"] = "Bearer SECRET_TOKEN"

    creds, lock = Creds(), threading.Lock()
    client = gsheet.client.RestClient(SECRET, 2, session_factory=Session,
                                     credential_factory=lambda raw, deadline: (creds, lock))
    return SimpleNamespace(client=client, responses=responses, calls=calls, sessions=sessions,
                           creds=creds, auth_calls=auth_calls)


def deadline():
    return time.monotonic() + 5


def test_values_payloads_order_and_sessions(transport):
    t = transport
    t.responses.extend([Response(data={"values": [["key", 4]]}),
                        Response(data={"valueRanges": [{"values": [[1]]}, {}]}), Response()])
    assert t.client.get(SID, "'It's'!AA2", deadline=deadline()) == [["key", 4]]
    assert t.client.batch_get(SID, ["A1", "A2"], deadline=deadline()) == [[[1]], []]
    data = [{"range": "A2", "values": [["done"]]}]
    t.client.batch_update(SID, data, value_input_option="RAW", deadline=deadline())
    assert "%27It%27s%27%21AA2" in t.calls[0][1]
    assert t.calls[0][2]["params"] == {"majorDimension": "ROWS", "valueRenderOption": "UNFORMATTED_VALUE"}
    assert t.calls[1][2]["params"]["ranges"] == ["A1", "A2"]
    assert t.calls[2][2]["json"] == {"valueInputOption": "RAW", "data": data}
    assert all(call[2]["timeout"] <= 2 for call in t.calls)
    assert all(not call[2]["allow_redirects"] for call in t.calls)
    assert len(t.sessions) == 3 and all(s.closed and not s.trust_env for s in t.sessions)
    t.client.batch_get(SID, [], deadline=deadline())
    t.client.batch_update(SID, [], value_input_option="RAW", deadline=deadline())
    assert len(t.calls) == 3


def test_metadata_gid_and_permissions(transport, gsheet):
    t = transport
    t.responses.extend([Response(data={"sheets": [{"properties": {"sheetId": 17, "title": "Targets"}}]}),
                        Response(data={"capabilities": {"canEdit": True}}),
                        Response(data={"sheets": []})])
    assert t.client.worksheet_title(SID, 17, deadline=deadline()) == "Targets"
    assert t.client.can_edit(SID, deadline=deadline()) is True
    assert t.calls[1][2]["params"] == {"fields": "capabilities/canEdit", "supportsAllDrives": "true"}
    with pytest.raises(gsheet.client.SyncError, match="gid"):
        t.client.worksheet_title(SID, 99, deadline=deadline())


@pytest.mark.parametrize("status", [400, 401, 403, 404, 302])
def test_safe_nonretry_errors(transport, gsheet, status):
    transport.responses.append(Response(status, {"error": SECRET}))
    with pytest.raises(gsheet.client.SyncError) as exc:
        transport.client.get(SID, "A1", deadline=deadline())
    assert SECRET not in str(exc.value) and "\n" not in str(exc.value)
    assert len(transport.calls) == 1


def _error(reason, consumer="projects/352087987693"):
    return {"error": {"code": 403, "status": "PERMISSION_DENIED", "message": SECRET,
                      "details": [{"reason": reason, "metadata": {"consumer": consumer}}]}}


@pytest.mark.parametrize("api,url_part", [("Drive API", "drive"), ("Sheets API", "sheets")])
def test_403_disabled_api_names_api_and_project(transport, gsheet, api, url_part):
    transport.responses.append(Response(403, _error("SERVICE_DISABLED")))
    with pytest.raises(gsheet.client.SyncError) as exc:
        if api == "Drive API":
            transport.client.can_edit(SID, deadline=deadline())
        else:
            transport.client.get(SID, "A1", deadline=deadline())
    text = str(exc.value)
    assert f"the {api} is disabled" in text and "project 352087987693" in text
    assert f"apis/api/{url_part}.googleapis.com/overview?project=352087987693" in text
    assert ("untick" in text) == (api == "Drive API") and SECRET not in text


def test_403_reasons_safe(transport, gsheet):
    transport.responses.extend([Response(403, _error("rateLimitExceeded")),
                                Response(403, _error("SERVICE_DISABLED", consumer="projects/" + SECRET)),
                                Response(403, {"error": {"status": "PERMISSION_DENIED"}}),
                                Response(403, ValueError("not json"))])
    messages = []
    for _ in range(4):
        with pytest.raises(gsheet.client.SyncError) as exc:
            transport.client.get(SID, "A1", deadline=deadline())
        messages.append(str(exc.value))
    assert "quota" in messages[0]
    assert "disabled" in messages[1] and "(project" not in messages[1]
    assert all("share the sheet" in m for m in messages[2:])
    assert not any(SECRET in m or "\n" in m for m in messages)


@pytest.mark.parametrize("status", [429, 500, 503])
def test_retries_and_backoff(transport, gsheet, monkeypatch, status):
    delays = []
    monkeypatch.setattr(gsheet.client.time, "sleep", delays.append)
    monkeypatch.setattr(gsheet.client.random, "uniform", lambda *args: 0.1)
    responses = [Response(status), Response(status), Response(data={"values": [["ok"]]})]
    transport.responses.extend(responses)
    assert transport.client.get(SID, "A1", deadline=deadline()) == [["ok"]]
    assert delays == [0.6, 1.1]
    assert len(transport.calls) == 3 and all(r.closed for r in responses)


def test_retry_limit_and_insufficient_backoff_budget(transport, gsheet, monkeypatch):
    monkeypatch.setattr(gsheet.client.time, "sleep", lambda _: None)
    transport.responses.extend([Response(503)] * 3)
    with pytest.raises(gsheet.client.SyncError, match="3 attempts"):
        transport.client.get(SID, "A1", deadline=deadline())
    transport.calls.clear()
    transport.responses.append(Response(429))
    with pytest.raises(gsheet.client.SyncError, match="deadline"):
        transport.client.get(SID, "A1", deadline=time.monotonic() + 0.1)
    assert len(transport.calls) == 1


@pytest.mark.parametrize("where", ["credentials", "auth", "http", "lock"])
def test_wall_deadline_and_no_later_api_calls(transport, gsheet, where):
    release = threading.Event()
    entered = threading.Event()

    def slow():
        entered.set()
        release.wait(1)

    lock = threading.Lock()
    if where == "credentials":
        def factory(*args):
            slow()
            return transport.creds, lock
        transport.client._credential_factory = factory
    elif where == "auth":
        transport.creds.refresh = lambda *args: slow()
    elif where == "http":
        def respond():
            slow()
            return Response(503)
        transport.responses.append(respond)
    else:
        lock.acquire()
        transport.client._credential_factory = lambda *args: (transport.creds, lock)
    start = time.monotonic()
    try:
        with pytest.raises(gsheet.client.SyncError, match="deadline"):
            transport.client.get(SID, "A1", deadline=start + 0.05)
        assert time.monotonic() - start < 0.5
    finally:
        release.set()
        if where == "lock":
            lock.release()
    time.sleep(0.03)
    assert len(transport.calls) == (1 if where == "http" else 0)


def test_expired_deadline_never_constructs_session(transport, gsheet):
    with pytest.raises(gsheet.client.SyncError, match="deadline"):
        transport.client.get(SID, "A1", deadline=time.monotonic() - 1)
    assert not transport.sessions


def test_auth_http_timeout_uses_same_deadline(transport, monkeypatch):
    seen = []

    class Request:
        def __init__(self, session):
            assert session is transport.sessions[0]

        def __call__(self, *args, **kwargs):
            seen.append(kwargs["timeout"])

    monkeypatch.setitem(sys.modules, "google.auth.transport.requests", SimpleNamespace(Request=Request))
    transport.creds.refresh = lambda request: request("https://oauth2.googleapis.com/token", timeout=120)
    transport.responses.append(Response())
    transport.client.get(SID, "A1", deadline=time.monotonic() + 0.2)
    assert 0 < seen[0] <= 0.2
    assert transport.calls[0][2]["timeout"] <= seen[0]


def test_credentials_cache_and_bad_key_are_safe(gsheet, monkeypatch):
    created = []

    class Credentials:
        @staticmethod
        def from_service_account_info(info, *, scopes):
            if info["private_key"] == "bad":
                raise ValueError(SECRET)
            created.append((info, scopes))
            return object()

    monkeypatch.setitem(sys.modules, "google.oauth2.service_account", SimpleNamespace(Credentials=Credentials))
    monkeypatch.setattr(gsheet.client, "_cache", gsheet.client.OrderedDict())
    raw = saved()["credentials_json"]
    first = gsheet.client._credentials(raw, deadline())
    assert gsheet.client._credentials(raw, deadline()) is first
    assert len(created) == 1 and created[0][1] == gsheet.client.SCOPES
    info = json.loads(raw)
    info["private_key"] = "bad"
    with pytest.raises(gsheet.client.SyncError, match="private key") as exc:
        gsheet.client._credentials(json.dumps(info), deadline())
    assert SECRET not in str(exc.value)
    info["token_uri"] = "https://other.example/token"
    with pytest.raises(gsheet.client.SyncError, match="endpoint"):
        gsheet.client._credentials(json.dumps(info), deadline())


def test_auth_failure_is_safe_and_never_sends_api_request(transport, gsheet):
    def fail(*args):
        raise ValueError(SECRET)
    transport.creds.refresh = fail
    with pytest.raises(gsheet.client.SyncError, match="authentication") as exc:
        transport.client.get(SID, "A1", deadline=deadline())
    assert SECRET not in str(exc.value) and not transport.calls


@pytest.mark.parametrize("response", [Response(data=ValueError(SECRET)), Response(data=[]),
                                       Response(data={"values": ["bad"]}), RuntimeError(SECRET)])
def test_transport_and_response_failures_are_safe(transport, gsheet, response):
    transport.responses.append(response)
    with pytest.raises(gsheet.client.SyncError) as exc:
        transport.client.get(SID, "A1", deadline=deadline())
    assert SECRET not in str(exc.value)


def test_settings_test_and_secret_public_view(gsheet, monkeypatch):
    settings = importlib.import_module("alfrd_gsheet.settings")
    manifest = importlib.import_module("alfrd_gsheet")
    seen = []

    class Client:
        def metadata(self, sid, *, deadline):
            seen.append((sid, deadline))
            return {"sheets": [{}, {}, {}], "properties": {"title": SECRET}}

        def can_edit(self, sid, *, deadline):
            seen.append((sid, deadline))
            return True

    monkeypatch.setattr(settings, "make_client", lambda _: Client())
    values = saved()
    values["default_spreadsheet_id"] = f"https://docs.google.com/spreadsheets/d/{SID}/edit#gid=0"
    assert settings.check(values) == ("ok: service account can read the default spreadsheet (3 sheets); "
                                      "edit access is verified on the first write.")
    assert len(seen) == 1 and seen[0][0] == SID  # no Drive call unless ticked
    seen.clear()
    assert settings.check(values | {"check_drive": True}) == "ok: service account can edit the default spreadsheet (3 sheets)."
    assert seen[0] == seen[1] and seen[0][0] == SID
    assert manifest.plugin.step_hooks.timeout == 30
    from alfrd.extensions import settings as host
    host.save(manifest.plugin, values)
    public = host.public(manifest.plugin)
    secret = next(f for f in public if f["key"] == "credentials_json")
    assert secret["set"] and "value" not in secret
    assert SECRET not in json.dumps(public)


@pytest.mark.parametrize("changes,match", [({"credentials_json": SECRET}, "JSON"),
    ({"credentials_json": "{}"}, "service-account"), ({"default_spreadsheet_id": SECRET + "!"}, "id"),
    ({"request_timeout": "nan"}, "positive"), ({"request_timeout": "inf"}, "positive"),
    ({"request_timeout": "0"}, "positive"), ({"request_timeout": True}, "positive"),
    ({"value_input_option": SECRET}, "value_input_option"), ({"conflict_policy": SECRET}, "conflict_policy"),
    ({"dry_run": "false"}, "Dry run"), ({"check_drive": "yes"}, "Check edit permission")])
def test_settings_validation_safe(gsheet, changes, match):
    settings = importlib.import_module("alfrd_gsheet.settings")
    with pytest.raises(ValueError, match=match) as exc:
        settings.normalize(saved() | changes)
    assert SECRET not in str(exc.value)


def test_readonly_and_missing_default_test(gsheet, monkeypatch):
    settings = importlib.import_module("alfrd_gsheet.settings")
    fake = SimpleNamespace(metadata=lambda *args, **kwargs: {"sheets": []},
                           can_edit=lambda *args, **kwargs: False)
    monkeypatch.setattr(settings, "make_client", lambda _: fake)
    with pytest.raises(ValueError, match="read-only"):
        settings.check(saved() | {"check_drive": True})
    assert settings.check(saved()).startswith("ok: service account can read")
    values = saved()
    del values["default_spreadsheet_id"]
    with pytest.raises(ValueError, match="default"):
        settings.check(values)


@pytest.mark.parametrize("mode", ["absent", "disabled", "unmapped"])
def test_manifest_zero_client_calls_without_applicable_mapping(gsheet, tmp_path, monkeypatch, mode):
    import yaml

    from alfrd.extensions import StepContext

    settings = importlib.import_module("alfrd_gsheet.settings")
    manifest = importlib.import_module("alfrd_gsheet")
    monkeypatch.setattr(settings, "make_client", lambda _: pytest.fail("must not construct a client"))
    if mode != "absent":
        config = {"version": 1, "enabled": mode != "disabled", "spreadsheet_id": SID,
                  "worksheet": "Targets", "rows": {"key_column": "key"},
                  "outbound": [{"step": "other", "column": "B", "field": "status"}]}
        (tmp_path / "alfrd.gsheet.yaml").write_text(yaml.safe_dump(config))
    ctx = StepContext(str(tmp_path), "plan", "unit", "step", ("s1",), ())
    assert manifest.before(ctx) is None
    assert manifest.after(ctx, None) is None
    assert manifest.after(replace(ctx, readopted=True), None) is None



def test_column_creation_is_one_atomic_literal_request_with_no_retry(transport, gsheet):
    t = transport
    t.responses.append(Response())
    t.client.create_columns(SID, sheet_id=17, header_row=3, start=26, names=["s1 RAM", "=literal"], deadline=deadline())
    assert t.calls[0][1].endswith(":batchUpdate")
    requests = t.calls[0][2]["json"]["requests"]
    assert requests[0] == {"insertDimension": {"range": {"sheetId": 17, "dimension": "COLUMNS", "startIndex": 26, "endIndex": 28}, "inheritFromBefore": True}}
    assert requests[1]["updateCells"]["start"] == {"sheetId": 17, "rowIndex": 2, "columnIndex": 26}
    assert requests[1]["updateCells"]["rows"][0]["values"][1] == {"userEnteredValue": {"stringValue": "=literal"}}
    t.responses.append(Response(503))
    with pytest.raises(gsheet.client.SyncError, match="after 1 attempts"):
        t.client.create_columns(SID, sheet_id=17, header_row=3, start=26, names=["s1 RAM"], deadline=deadline())
    assert len(t.calls) == 2
