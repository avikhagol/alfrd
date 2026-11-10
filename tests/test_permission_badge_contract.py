"""S4 backend contract: the fields the permission badge reads (dangerous, default, effective) and the stale-revision 409
for a risky Machine change. The badge itself is covered by tests/studio_js/permission_ui.test.mjs."""
from __future__ import annotations

from alfrd import superuser
from tests.test_ui_flags_save import URL, make_client, project, revisions, fake, run, say  # noqa: F401  (fixtures)


def flags(client) -> dict:
    return {f["key"]: f for f in client.get(f"{URL}?project={client.project}").get_json()["flags"]}


def test_flags_carry_what_the_badge_reads(make_client):
    rows = flags(make_client())
    for key in ("lockdown.file_edits", "lockdown.project_delete"):
        assert rows[key]["dangerous"] is True and rows[key]["default"] is True and rows[key]["effective"] is True
    assert rows["ui.theme"]["dangerous"] is False


def test_turning_a_permission_off_makes_it_non_default_in_the_payload(make_client):
    client = make_client()
    rev = revisions(client)["machine"]
    ok = client.post("/api/studio/superuser", headers=client.h,
                     json={"settings": {"lockdown.file_edits": False}, "expected_revision": rev})
    assert ok.status_code == 200
    row = flags(client)["lockdown.file_edits"]
    assert row["dangerous"] and row["effective"] is False and row["effective"] != row["default"]
    assert flags(client)["lockdown.project_delete"]["effective"] is True


def test_read_only_is_a_dangerous_non_default_row_the_lockdown_badge_reads(make_client):
    client = make_client()
    row = flags(client)["lockdown.read_only"]
    assert row["dangerous"] is True and row["default"] is False and row["effective"] is False
    rev = revisions(client)["machine"]
    assert client.post("/api/studio/superuser", headers=client.h,
                       json={"settings": {"lockdown.read_only": True}, "expected_revision": rev}).status_code == 200
    row = flags(client)["lockdown.read_only"]
    assert row["dangerous"] and row["effective"] is True and row["effective"] != row["default"]


def test_a_stale_risky_change_is_409_and_changes_nothing(make_client):
    client = make_client()
    rev = revisions(client)["machine"]
    assert client.post("/api/studio/superuser", headers=client.h,
                       json={"settings": {"lockdown.project_delete": False}, "expected_revision": rev}).status_code == 200
    stale = client.post("/api/studio/superuser", headers=client.h,
                        json={"settings": {"lockdown.project_delete": True, "lockdown.file_edits": False}, "expected_revision": rev})
    assert stale.status_code == 409 and "stale expected_revision" in stale.get_json()["error"]["message"]
    data = superuser.load()["lockdown"]
    assert data["project_delete"] is False and data["file_edits"] is True


def test_presentation_flags_do_not_change_dangerous_rows(make_client):
    client = make_client()
    before = {k: f["effective"] for k, f in flags(client).items() if f["dangerous"]}
    rev = revisions(client)["machine"]
    assert client.post("/api/studio/superuser", headers=client.h,
                       json={"settings": {"studio.live_interval": 7}, "expected_revision": rev}).status_code == 200
    assert {k: f["effective"] for k, f in flags(client).items() if f["dangerous"]} == before
