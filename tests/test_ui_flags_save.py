"""The settings form saves: Browser and Project through POST /studio/ui-flags, Machine through POST /studio/superuser.
Each section carries its own expected_revision; a stale one is a 409; remote and browser-only requests are refused."""
from __future__ import annotations

import pytest

from alfrd import superuser, ui_flags
from tests.test_chat_orchestration import fake, project, run, say  # noqa: F401  (the project fixture)

REMOTE = {"REMOTE_ADDR": "203.0.113.7"}
URL = "/api/studio/ui-flags"


@pytest.fixture
def make_client(tmp_path, project):  # noqa: F811
    pytest.importorskip("flask")
    from alfrd.gui import create_app
    from alfrd.gui.services import RuntimeCatalogReader
    from alfrd.runtime import RuntimeService, RuntimeStore

    def build(runtime: bool = True):
        config = {"TESTING": True, "SECRET_KEY": "k", "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'c.sqlite'}",
                  "STUDIO_DEMO": False, "STUDIO_LIVE_INTERVAL": 0}
        ident = None
        if runtime:
            store = RuntimeStore(tmp_path / "runtime.sqlite")
            store.initialize()
            service = RuntimeService(store)
            service.register_manifest(project / ".alfrd" / "alfrd.yaml")
            ident = service.list_projects()[0].identifier
            config.update(RUNTIME_SERVICE=service, CATALOG_READER=RuntimeCatalogReader(service))
        client = create_app(config).test_client()
        client.h = {"X-CSRF-Token": client.get("/api/studio/session").get_json()["csrf_token"]}
        client.project = ident
        return client

    return build


def revisions(client) -> dict:
    return client.get(f"{URL}?project={client.project}").get_json()["revisions"]


def test_get_reports_a_revision_for_machine_and_project(make_client):
    client = make_client()
    got = revisions(client)
    assert got["machine"] == ui_flags.revision(superuser.flat()) and isinstance(got["project"], int)


def test_project_section_saves_and_a_stale_form_gets_409(make_client):
    client = make_client()
    rev = revisions(client)["project"]
    ok = client.post(URL, headers=client.h, json={"scope": "project", "project": client.project,
                                                  "changes": {"chat.final_summary": True}, "expected_revision": rev})
    assert ok.status_code == 200
    body = ok.get_json()
    assert body["revisions"]["project"] != rev
    assert {f["key"]: f for f in body["flags"]}["chat.final_summary"]["value"] is True
    stale = client.post(URL, headers=client.h, json={"scope": "project", "project": client.project,
                                                     "changes": {"chat.final_summary": False}, "expected_revision": rev})
    assert stale.status_code == 409 and "stale expected_revision" in stale.get_json()["error"]["message"]
    assert revisions(client)["project"] == body["revisions"]["project"]  # the stale save changed nothing


def test_browser_section_validates_and_a_stale_form_gets_409(make_client):
    client = make_client()
    current = ui_flags.revision({"ui.rows_per_page": 25})
    ok = client.post(URL, headers=client.h, json={"scope": "browser", "changes": {"ui.rows_per_page": 50},
                                                  "expected_revision": current, "current_revision": current})
    assert ok.status_code == 200 and ok.get_json()["values"] == {"ui.rows_per_page": 50}
    stale = client.post(URL, headers=client.h, json={"scope": "browser", "changes": {"ui.rows_per_page": 50},
                                                     "expected_revision": current - 1, "current_revision": current})
    assert stale.status_code == 409
    assert client.post(URL, headers=client.h, json={"scope": "browser", "changes": {"ui.rows_per_page": "many"},
                                                    "expected_revision": current, "current_revision": current}).status_code == 400
    # a key of another section cannot ride along
    assert client.post(URL, headers=client.h, json={"scope": "browser", "changes": {"lockdown.runs": False},
                                                    "expected_revision": current, "current_revision": current}).status_code == 400


def test_machine_section_saves_and_a_stale_form_gets_409(make_client):
    client = make_client()
    rev = revisions(client)["machine"]
    ok = client.post("/api/studio/superuser", headers=client.h,
                     json={"settings": {"studio.live_interval": 7}, "expected_revision": rev})
    assert ok.status_code == 200 and ok.get_json()["revision"] != rev
    assert superuser.load()["studio"]["live_interval"] == 7
    stale = client.post("/api/studio/superuser", headers=client.h,
                        json={"settings": {"studio.live_interval": 9}, "expected_revision": rev})
    assert stale.status_code == 409
    assert superuser.load()["studio"]["live_interval"] == 7
    bad = client.post("/api/studio/superuser", headers=client.h,
                      json={"settings": {"studio.live_interval": 9}, "expected_revision": "x"})
    assert bad.status_code == 400


def test_read_only_refuses_the_project_section_but_machine_can_still_turn_it_off(make_client):
    client = make_client()
    superuser.merge({"lockdown.read_only": True})
    rev = revisions(client)["project"]
    refused = client.post(URL, headers=client.h, json={"scope": "project", "project": client.project,
                                                       "changes": {"chat.final_summary": True}, "expected_revision": rev})
    assert refused.status_code == 403 and refused.get_json()["error"]["reason"] == "superuser"
    off = client.post("/api/studio/superuser", headers=client.h,
                      json={"settings": {"lockdown.read_only": False}, "expected_revision": revisions(client)["machine"]})
    assert off.status_code == 200


@pytest.mark.parametrize("body", [
    {"scope": "browser", "changes": {"ui.rows_per_page": 50}, "expected_revision": 1, "current_revision": 1},
    {"scope": "project", "changes": {"chat.final_summary": True}, "expected_revision": 1, "project": "x"},
])
def test_remote_requests_are_refused(make_client, body):
    client = make_client()
    assert client.post(URL, headers=client.h, json=body, environ_base=REMOTE).status_code == 403
    machine = client.post("/api/studio/superuser", headers=client.h, environ_base=REMOTE,
                          json={"settings": {"studio.live_interval": 3}, "expected_revision": 1})
    assert machine.status_code == 403
    assert superuser.load()["studio"]["live_interval"] is None


def test_browser_only_requests_are_refused(make_client):
    client = make_client(runtime=False)
    for scope, changes in (("browser", {"ui.rows_per_page": 50}), ("project", {"chat.final_summary": True})):
        response = client.post(URL, headers=client.h, json={"scope": scope, "changes": changes, "expected_revision": 1,
                                                            "current_revision": 1, "project": "x"})
        assert response.status_code in (403, 404)
        assert response.status_code != 200
    # and the read side shows every flag locked with the reason
    flags = client.get(URL).get_json()["flags"]
    assert all(flag["locked"] and "Browser-only mode" in flag["reason"] for flag in flags)


def test_unknown_scope_and_machine_scope_are_not_saved_here(make_client):
    client = make_client()
    for scope in ("machine", "nope"):
        response = client.post(URL, headers=client.h, json={"scope": scope, "changes": {"studio.live_interval": 1}, "expected_revision": 1})
        assert response.status_code == 400


def test_checker_toggle_is_superuser_only_and_persists_per_project(make_client, project):  # noqa: F811
    from alfrd import chat

    client = make_client()
    assert client.get("/api/studio/session").get_json()["can_superuser"] is True
    for enabled in (False, True):
        body = {"scope": "project", "project": client.project,
                "changes": {"chat.watch.enabled": enabled}, "expected_revision": revisions(client)["project"]}
        assert client.post(URL, headers=client.h, json=body, environ_base=REMOTE).status_code == 403
        assert client.post(URL, json=body).status_code == 403
        response = client.post(URL, headers=client.h, json=body)
        assert response.status_code == 200, response.get_json()
        flag = next(f for f in response.get_json()["flags"] if f["key"] == "chat.watch.enabled")
        assert flag["value"] is enabled and flag["scope"] == "project" and flag["default"] is True
        assert chat.settings(project)["watch"]["enabled"] is enabled
        assert chat.settings_view(project)["block"]["watch"]["enabled"] is enabled
    # The full chat settings API cannot bypass the same superuser permission gate.
    url = f"/api/studio/projects/{client.project}/chat/settings"
    assert client.post(url, headers=client.h, json={"chat": {"watch": {"enabled": False}}},
                       environ_base=REMOTE).status_code == 403
    assert chat.settings(project)["watch"]["enabled"] is True
