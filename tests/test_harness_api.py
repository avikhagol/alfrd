# ruff: noqa: F811
from alfrd import chat
from tests.test_attachment_routes import project, web  # noqa: F401


def test_read_endpoint_and_errors(web, monkeypatch):
    client, _, base, sid, _ = web
    monkeypatch.setattr(chat, "check", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("recovery")))
    url = f"{base}/sessions/{sid}/harness/state"
    response = client.get(url + "?scope=all")
    assert response.status_code == 200, response.json
    assert response.json["schema"] == "alfrd.harness/1"
    assert response.headers["Cache-Control"] == "no-store"
    assert client.get(url + "?at=nonsense").status_code == 400
    assert client.get(url + "?limit=broken").status_code == 400
    assert client.get(url + "?scope=request&request=r9999").status_code == 404
    assert client.get(url.replace(sid, "missing")).status_code == 404
    assert client.post(url).status_code in (403, 405)
    assert client.get("/api/studio/session").json["harness_state_version"] == 1
    client.application.config["STUDIO_PROJECTS"] = []
    assert client.get(url).status_code == 403


def test_auth_required(web):
    client, _, base, sid, _ = web
    client.application.config.update(ACCESS_TOKEN_REQUIRED=True, ACCESS_TOKEN="secret")
    url = f"{base}/sessions/{sid}/harness/state?scope=all"
    assert client.get(url).status_code == 401
    assert client.get(url, headers={"Authorization": "Bearer secret"}).status_code == 200
