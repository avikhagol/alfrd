# ruff: noqa: F811
from alfrd import chat, harness
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



def test_augmented_projection_http_contract(web):
    client, _, base, sid, root = web
    out = chat.post_message(root, sid, "@claude Build a game", spawn=False)
    store = chat.session(root, sid)
    request = store.request(out["request"]["id"])
    request["bounds"] = {"max_cost_usd": 30}
    store.save_request(request)
    response = client.get(f"{base}/sessions/{sid}/harness/state?scope=all&limit=1")
    assert response.status_code == 200
    view = response.get_json()["view"]
    assert view["requests"][0]["title"] == request["text"].splitlines()[0]
    assert view["requests"][0]["max_cost_usd"] == 30
    assert view["requests"][0]["mode"] == "direct"
    assert view["attention"] == {"count": 0, "mailbox_known": True, "items": []}
    assert all("attempts" in task and "usage" in task and "latest" in task and "logs" in task for task in view["tasks"])
    replay = client.get(f"{base}/sessions/{sid}/harness/state", query_string={"scope": "all", "at": harness.iso(harness.timestamp(request["created"]))})
    assert replay.status_code == 200
    assert replay.get_json()["view"]["attention"]["mailbox_known"] is False
    assert all("logs" not in task for task in replay.get_json()["view"]["tasks"])


def test_existing_serve_routes_unchanged_after_projection(web):
    client, _, base, sid, root = web
    chat.post_message(root, sid, "@claude hello", spawn=False)
    routes = ["/api/studio/session", f"{base}/sessions", f"{base}/sessions/{sid}"]
    before = {}
    for route in routes:
        response = client.get(route)
        assert response.status_code == 200
        before[route] = response.get_json()
    assert before[f"{base}/sessions/{sid}"]["session"]["id"] == sid
    assert "attention" not in before[f"{base}/sessions/{sid}"]
    for query in ("scope=all", "scope=all&limit=1", "scope=all&at=2000-01-01T00:00:00Z"):
        assert client.get(f"{base}/sessions/{sid}/harness/state?{query}").status_code == 200
    for route in routes:
        response = client.get(route)
        assert response.status_code == 200
        assert response.get_json() == before[route]
    assert client.get("/studio/").status_code == 200
    assert client.get("/harness").status_code in (301, 302, 308)
