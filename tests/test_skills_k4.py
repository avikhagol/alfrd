"""K4 guarded settings, native startup and safe preview routes."""
import pytest

from alfrd import chat, skills
from test_chat_orchestration import project  # noqa: F401
from test_chat_setup import _client


def test_native_controls_round_trip_and_reach_runner(project, tmp_path):
    client, headers, base = _client(project, tmp_path)
    loaded = client.get(base + "/settings").get_json()
    block = {**loaded["block"], "agents": {**loaded["block"].get("agents", {}),
             "claude": {"lean": True, "skills": True, "plugins": False, "mcp": "default"}}}
    folder = project / "skills" / "safe"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text("<script>alert(1)</script> guidance")
    block["skill_selection"] = {"enabled": True, "selected_ids": ["safe"]}
    assert client.post(base + "/settings", json={"chat": block, "base_hash": loaded["hash"]}).status_code == 403
    saved = client.post(base + "/settings", json={"chat": block, "base_hash": loaded["hash"]}, headers=headers)
    assert saved.status_code == 200
    assert saved.get_json()["settings"]["block"] == block
    assert client.post(base + "/settings", json={"chat": {}, "base_hash": loaded["hash"]}, headers=headers).status_code == 409
    view = client.get(base + "/skills").get_json()["agents"]["claude"]
    assert view["native"] == {"skills": True, "plugins": False, "mcp": True}
    sid = chat.create_session(project)["id"]
    step = chat.execution_config(project, sid).step("claude")
    assert "--setting-sources" in step.argv
    assert "--disable-slash-commands" not in step.argv
    assert "--strict-mcp-config" not in step.argv
    assert "--setting-sources" in view["effective_args"]
    assert "--disable-slash-commands" not in view["effective_args"]
    assert "--strict-mcp-config" not in view["effective_args"]
    for options in ({"skills": "yes"}, {"plugins": 1}, {"mcp": True}):
        with pytest.raises(Exception):
            chat.merge_settings({"agents": {"claude": options}})


def test_preview_is_bounded_and_project_discovered(project, tmp_path):
    client, _, base = _client(project, tmp_path)
    folder = project / "skills" / "safe"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text("<img src=x onerror=alert(1)>")
    preview = client.get(base + "/skills/preview?id=safe")
    assert preview.status_code == 200
    assert preview.get_json()["content"] == "<img src=x onerror=alert(1)>"
    assert client.get(base + "/skills/preview?id=../safe").status_code == 404
    outside = tmp_path / "outside.md"
    outside.write_text("secret")
    (folder / "SKILL.md").unlink()
    (folder / "SKILL.md").symlink_to(outside)
    assert client.get(base + "/skills/preview?id=safe").status_code == 404
    (folder / "SKILL.md").unlink()
    (folder / "SKILL.md").write_bytes(b"x" * (skills.MAX_SKILL_BYTES + 1))
    assert client.get(base + "/skills/preview?id=safe").status_code == 404
