"""L3 current-version choice preserves the recorded ref and L2 raw checks."""
from tests.test_chat_overview import client, project, sha, task  # noqa: F401
from alfrd import chat


def test_current_reference_is_explicit_and_hash_checked(project, client):
    sid = chat.create_session(project, "Files")["id"]
    path = project / "out.txt"
    path.write_bytes(b"recorded")
    recorded = f"out.txt@{sha(b'recorded')}"
    chat.session(project, sid).save_task(task("r0001.a", artifacts=[recorded]))
    url = client.base + sid
    path.write_bytes(b"current")
    file = client.get(url + "/overview").get_json()["files"][0]
    assert file["ref"] == recorded
    assert file["state"] == "changed"
    assert file["producer_rows"] == ["r0001.a"]
    assert file["current_ref"] == f"out.txt@{sha(b'current')}"
    raw = url + "/artifacts/raw?ref="
    assert client.get(raw + recorded).status_code == 409
    assert client.get(raw + file["current_ref"]).data == b"current"
    path.write_bytes(b"raced")
    assert client.get(raw + file["current_ref"]).status_code == 409
    assert client.get(url + "/overview").get_json()["files"][0]["ref"] == recorded
