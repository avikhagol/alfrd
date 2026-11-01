"""Actual loopback HTTP parsing with in-memory sockets and fake Google backends."""
import json
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError

import pytest

from test_oauth import record

SID = "s" * 24


def http(session, path, data=None, **headers):
    import io
    body = b"" if data is None else json.dumps(data).encode()
    headers = {"Host": session.host, "Content-Type": "application/json", "Content-Length": str(len(body)), **headers}
    raw = (("GET" if data is None else "POST") + " " + path + " HTTP/1.0\r\n" +
           "".join(f"{key}: {value}\r\n" for key, value in headers.items()) + "\r\n").encode() + body
    class Connection:
        output = b""
        def makefile(self, *args): return io.BytesIO(raw)
        def sendall(self, data): self.output += bytes(data)
    connection = Connection()
    session.server.handler(connection, ("127.0.0.1", 1234), session.server)
    head, body = connection.output.split(b"\r\n\r\n", 1)
    code = int(head.split(b" ")[1])
    if code >= 400: raise HTTPError(session.origin + path, code, "", {}, None)
    return body.decode()


@pytest.mark.parametrize("query,state", [("state=wrong&code=x", "mismatch"), ("error=access_denied", "denied"), ("code=x", "signed_in")])
def test_auth_callback_state_and_single_use(oauth_modules, query, state):
    oauth, lb = oauth_modules.oauth, oauth_modules.loopback
    calls = []
    def exchange(*args):
        calls.append(args); return record(oauth)
    session = lb.start("auth", cfg=oauth.config(), exchange=exchange)
    suffix = "" if query.startswith("state=") else "&state=" + session.nonce
    page = http(session, "/?" + query + suffix)
    assert "Return to ALFRD" in page and session.status()["state"] == state
    assert len(calls) == (1 if state == "signed_in" else 0)
    session.callback({"state": [session.nonce], "code": ["replay"]})
    assert len(calls) <= 1
    assert bool(oauth.load_token()) is (state == "signed_in")


def test_cancel_during_exchange_cannot_sign_in(oauth_modules):
    import threading
    oauth, lb = oauth_modules.oauth, oauth_modules.loopback
    entered, release = threading.Event(), threading.Event()
    def exchange(*args):
        entered.set(); assert release.wait(2); return record(oauth)
    session = lb.start("auth", cfg=oauth.config(), exchange=exchange)
    with ThreadPoolExecutor() as pool:
        pending = pool.submit(session.callback, {"state": [session.nonce], "code": ["code"]})
        assert entered.wait(2)
        session.finish("cancelled")
        release.set(); pending.result(2)
    assert not oauth.load_token()
    assert session.status()["state"] == "cancelled"


def test_picker_page_nonce_host_and_callback(oauth_modules):
    oauth, lb = oauth_modules.oauth, oauth_modules.loopback
    session = lb.start("picker", cfg=oauth.config(), token="offline-access")
    for path, headers, status in [("/unknown", {}, 404), ("/picker?n=wrong", {}, 403),
                                  ("/picker?n=" + session.nonce, {"Host": "evil.example"}, 403)]:
        with pytest.raises(HTTPError) as exc: http(session, path, **headers)
        assert exc.value.code == status
    page = http(session, "/picker?n=" + session.nonce)
    assert "offline-access" in page and "offline-client-secret" not in page and "offline-refresh" not in page
    with pytest.raises(HTTPError) as exc: http(session, "/picker?n=" + session.nonce)
    assert exc.value.code == 403
    data = {"nonce": session.nonce, "action": "picked", "id": SID, "mimeType": lb.MIME}
    with pytest.raises(HTTPError) as exc: http(session, "/picked", data)
    assert exc.value.code == 403
    for change in ({"nonce": "wrong"}, {"id": "bad"}, {"mimeType": "text/plain"}, {"nonce": 123}):
        with pytest.raises(HTTPError) as exc: http(session, "/picked", {**data, **change}, Origin=session.origin)
        assert exc.value.code == 400
    with pytest.raises(HTTPError) as exc: http(session, "/picked", {**data, "x": "x" * 5000}, Origin=session.origin)
    assert exc.value.code == 413
    assert "Return" in http(session, "/picked", data, Origin=session.origin)
    assert session.status()["state"] == "picked" and session.file == {"id": SID}
    with pytest.raises(ValueError): session.picked(data)
    assert session.token is None


def test_expiry_cancellation_and_registry(oauth_modules):
    oauth, lb = oauth_modules.oauth, oauth_modules.loopback
    now = [0]
    first = lb.start("auth", cfg=oauth.config(), clock=lambda: now[0])
    now[0] = 301
    assert first.status()["state"] == "expired"
    second = lb.start("auth", cfg=oauth.config())
    third = lb.start("auth", cfg=oauth.config())
    assert second.status()["state"] == "cancelled"
    assert third.state == "waiting"
    lb.cancel_all()
    assert third.state == "cancelled"


def test_picker_action_validates_drive_and_caches(oauth_modules, monkeypatch, tmp_path):
    from types import SimpleNamespace
    m = oauth_modules
    session = m.loopback.start("picker", cfg=m.oauth.config(), token="offline-access")
    session.picked({"nonce": session.nonce, "action": "picked", "id": SID, "mimeType": m.loopback.MIME})
    calls = []
    def drive_file(sid, **kw):
        calls.append(sid); return {"id": sid, "name": "Lab runs", "can_edit": True}
    monkeypatch.setattr(m.settings, "make_client", lambda _: SimpleNamespace(drive_file=drive_file))
    for _ in range(2):
        result = m.actions.picker_status(tmp_path, {}, {"session": session.id})
        assert result["file"]["id"] == SID and result["state"] == "picked"
    assert calls == [SID]
    session.file = {"id": SID}
    def denied(*a, **kw):
        raise m.oauth.SyncError("Not found (404)", code="file_access")
    monkeypatch.setattr(m.settings, "make_client", lambda _: SimpleNamespace(drive_file=denied))
    assert m.actions.picker_status(tmp_path, {}, {"session": session.id})["state"] == "error"
