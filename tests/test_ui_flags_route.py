"""GET /api/studio/ui-flags: the flag registry over HTTP; remote and browser-only requests get read-only flags with reasons."""
from __future__ import annotations

import pytest

from alfrd import superuser, ui_flags

REMOTE = {"REMOTE_ADDR": "203.0.113.7"}


@pytest.fixture
def make_client(tmp_path):
    pytest.importorskip("flask")
    from alfrd.gui import create_app
    from alfrd.gui.services import RuntimeCatalogReader
    from alfrd.runtime import RuntimeService, RuntimeStore

    def build(runtime: bool = True):
        config = {"TESTING": True, "SECRET_KEY": "k", "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'c.sqlite'}",
                  "STUDIO_DEMO": False, "STUDIO_LIVE_INTERVAL": 2}
        if runtime:
            store = RuntimeStore(tmp_path / "runtime.sqlite")
            store.initialize()
            service = RuntimeService(store)
            config.update(RUNTIME_SERVICE=service, CATALOG_READER=RuntimeCatalogReader(service))
        return create_app(config).test_client()

    return build


def flags_by_key(response) -> dict:
    body = response.get_json()
    return {flag["key"]: flag for flag in body["flags"]}


def test_local_request_gets_every_registered_flag(make_client):
    client = make_client()
    response = client.get("/api/studio/ui-flags")
    assert response.status_code == 200
    body = response.get_json()
    assert isinstance(body["revision"], int)
    assert [flag["key"] for flag in body["flags"]] == [flag.key for flag in ui_flags.FLAGS]
    flags = flags_by_key(response)
    assert flags["ui.live_updates"]["scope"] == "browser" and flags["ui.live_updates"]["locked"] is False
    assert flags["ui.live_updates"]["value"] is True  # the default: the page applies its own value on top
    assert flags["studio.live_interval"]["scope"] == "machine" and flags["studio.live_interval"]["locked"] is False


def test_settings_file_drives_machine_flags_and_revision(make_client):
    client = make_client()
    before = client.get("/api/studio/ui-flags").get_json()
    superuser.merge({"lockdown.read_only": True, "studio.live_interval": 7})
    after = client.get("/api/studio/ui-flags").get_json()
    assert after["revision"] != before["revision"]
    flags = {flag["key"]: flag for flag in after["flags"]}
    assert flags["studio.live_interval"]["value"] == 7
    # read-only Studio locks every browser and project flag, with its reason
    assert flags["ui.live_updates"]["locked"] is True and flags["ui.live_updates"]["reason"] == "Read-only Studio"
    assert flags["lockdown.read_only"]["locked"] is False


def test_remote_request_gets_read_only_flags_with_reasons(make_client):
    client = make_client()
    response = client.get("/api/studio/ui-flags", environ_base=REMOTE)
    assert response.status_code == 200
    flags = flags_by_key(response)
    assert len(flags) == len(ui_flags.FLAGS)
    assert all(flag["locked"] for flag in flags.values())
    assert all("server machine" in flag["reason"] for flag in flags.values())
    # a flag that already had a reason keeps it, after the access reason
    assert flags["ui.experimental_chat"]["reason"].endswith("Requires features.experimental")


def test_browser_only_request_gets_read_only_flags_with_reasons(make_client):
    client = make_client(runtime=False)
    response = client.get("/api/studio/ui-flags")
    assert response.status_code == 200
    flags = flags_by_key(response)
    assert all(flag["locked"] for flag in flags.values())
    assert all("Browser-only mode" in flag["reason"] for flag in flags.values())


def test_local_access_checks_for_the_settings_routes_are_unchanged(make_client):
    client = make_client()
    assert client.get("/api/studio/superuser", environ_base=REMOTE).status_code == 403
    assert client.post("/api/studio/superuser", json={"settings": {"lockdown.runs": False}}, environ_base=REMOTE).status_code == 403
    assert client.get("/api/studio/superuser").status_code == 403  # local, but no CSRF token
    assert superuser.allowed("runs")
