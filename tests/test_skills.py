"""Skill discovery and effective-args dry run (contract C27)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from alfrd import skills
from alfrd.gui import create_app
from alfrd.gui.services import RuntimeCatalogReader
from alfrd.runtime import RuntimeService, RuntimeStore
from tests.test_chat_orchestration import project  # noqa: F401  (the project fixture)


def write_skill(project, root, name, text):
    path = project / root / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_discovers_three_roots(tmp_path):
    a = write_skill(tmp_path, ".alfrd/skills", "one", "---\nname: One\ndescription: first\n---\nbody\n")
    write_skill(tmp_path, ".claude/skills", "two", "---\nname: Two\ndescription: second\n---\n")
    write_skill(tmp_path, "skills", "three", "no frontmatter\n")
    found = {s["id"]: s for s in skills.discover_skills(tmp_path)}
    assert set(found) == {"one", "two", "three"}
    assert found["one"] == {
        "id": "one", "name": "One", "description": "first", "source": ".alfrd/skills/one/SKILL.md",
        "sha256": hashlib.sha256(a.read_bytes()).hexdigest(), "bytes": a.stat().st_size,
    }
    assert found["two"]["source"] == ".claude/skills/two/SKILL.md"
    assert found["three"]["name"] == "three" and found["three"]["description"] == ""


def test_frontmatter_parsing():
    meta = skills.parse_frontmatter('---\nname: "Quoted"\ndescription: a: b\n# c\n  indented: x\n---\nbody')
    assert meta == {"name": "Quoted", "description": "a: b"}
    assert skills.parse_frontmatter("---\nname: x\n") == {}
    assert skills.parse_frontmatter("name: x\n") == {}


def test_first_root_wins_on_duplicate_id(tmp_path):
    write_skill(tmp_path, ".alfrd/skills", "dup", "---\nname: A\n---\n")
    write_skill(tmp_path, "skills", "dup", "---\nname: B\n---\n")
    found = skills.discover_skills(tmp_path)
    assert [s["name"] for s in found] == ["A"]


def test_symlinks_rejected(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "SKILL.md").write_text("---\nname: Evil\n---\n")
    project = tmp_path / "proj"
    (project / "skills").mkdir(parents=True)
    # symlinked skill directory
    (project / "skills" / "linkdir").symlink_to(outside, target_is_directory=True)
    # symlinked SKILL.md file
    (project / "skills" / "linkfile").mkdir()
    (project / "skills" / "linkfile" / "SKILL.md").symlink_to(outside / "SKILL.md")
    # symlinked root
    (project / ".claude").mkdir()
    (project / ".claude" / "skills").symlink_to(outside, target_is_directory=True)
    write_skill(project, "skills", "ok", "---\nname: Ok\n---\n")
    assert [s["id"] for s in skills.discover_skills(project)] == ["ok"]


def test_traversal_outside_root_rejected(tmp_path):
    project = tmp_path / "proj"
    (project / "skills").mkdir(parents=True)
    # a root that is a symlink to a parent directory resolves outside the project
    (project / ".alfrd").symlink_to(tmp_path, target_is_directory=True)
    (tmp_path / "skills").mkdir()
    (tmp_path / "skills" / "x").mkdir()
    (tmp_path / "skills" / "x" / "SKILL.md").write_text("---\nname: X\n---\n")
    assert skills.discover_skills(project) == []
    # an explicit traversal root is rejected too
    assert skills.discover_skills(project, roots=("../skills",)) == []


def test_size_cap(tmp_path):
    pad = skills.MAX_SKILL_BYTES - len("---\nname: Big\n---\n")
    ok = "---\nname: Big\n---\n" + "x" * pad
    assert len(ok.encode()) == 64 * 1024
    write_skill(tmp_path, "skills", "exact", ok)
    write_skill(tmp_path, "skills", "over", ok + "x")
    found = skills.discover_skills(tmp_path)
    assert [s["id"] for s in found] == ["exact"]
    assert found[0]["bytes"] == 64 * 1024


def test_missing_roots(tmp_path):
    assert skills.discover_skills(tmp_path) == []


def test_effective_args_reuses_adapter_code(tmp_path):
    from alfrd.agent_io import agent_command, startup_options

    spec = {"argv": ["claude", "-p", "-"], "lean": True, "model": "opus"}
    out = {e["id"]: e for e in skills.effective_args({"c": spec})}
    expected = agent_command(spec["argv"], "opus", startup=startup_options(spec))[0]
    assert out["c"]["argv"] == list(expected)
    assert "--disable-slash-commands" not in out["c"]["argv"]
    assert "--setting-sources" in out["c"]["argv"]
    assert out["c"]["adapter"] == "claude"
    assert out["c"]["error"] is None


def test_effective_args_codex_and_generic_unchanged():
    out = {e["id"]: e for e in skills.effective_args({
        "x": {"argv": ["codex", "exec", "-"], "skills": False},
        "g": {"argv": ["mytool", "--flag"], "lean": True},
        "none": {},
    })}
    assert out["x"]["argv"] == ["codex", "exec", "-"]
    assert any("Codex: no effect" in n for n in out["x"]["notes"])
    assert out["g"]["argv"] == ["mytool", "--flag"] and out["g"]["adapter"] == "generic"
    assert out["none"]["argv"] == []


def test_effective_args_reports_invalid_settings():
    out = skills.effective_args({"bad": {"argv": ["claude"], "lean": "yes"}})
    assert out[0]["argv"] is None and "lean" in out[0]["error"]


def test_selection_snapshot_is_bounded_ordered_and_frozen(tmp_path):
    import pytest

    a = write_skill(tmp_path, "skills", "a", "a" * skills.MAX_SKILL_BYTES)
    write_skill(tmp_path, "skills", "b", "b" * skills.MAX_SKILL_BYTES)
    write_skill(tmp_path, "skills", "c", "c")
    selection = {"enabled": True, "selected_ids": ["b", "a", "b"]}
    snap = skills.snapshot_selection(tmp_path, selection)
    assert snap["bytes"] == skills.MAX_SELECTED_BYTES
    assert [s["id"] for s in snap["skills"]] == ["b", "a"]
    assert snap["skills"][1]["sha256"] == hashlib.sha256(a.read_bytes()).hexdigest()
    a.unlink()
    assert snap["skills"][1]["content"] == "a" * skills.MAX_SKILL_BYTES
    assert skills.snapshot_selection(tmp_path)["skills"] == []
    assert skills.snapshot_selection(tmp_path, {"selected_ids": ["missing"]})["skills"] == []
    with pytest.raises(ValueError, match="unknown.*skill id"):
        skills.snapshot_selection(tmp_path, {"enabled": True, "selected_ids": ["a"]})
    write_skill(tmp_path, "skills", "a", "a" * skills.MAX_SKILL_BYTES)
    with pytest.raises(ValueError, match="128 KiB"):
        skills.snapshot_selection(tmp_path, {"enabled": True, "selected_ids": ["a", "b", "c"]})


def test_agent_view_native_flags_and_notes():
    view = skills.agent_view({
        "claude": {"argv": ["claude", "-p", "-"], "lean": True, "model": "opus"},
        "plain": {"argv": ["claude", "-p", "-"]},
        "codex": {"argv": ["codex", "exec", "-"], "lean": True},
        "bad": {"argv": ["claude"], "lean": "yes"},
    })
    assert view["claude"]["adapter"] == "claude"
    assert view["claude"]["native"] == {"skills": True, "plugins": False, "mcp": False}
    assert "--disable-slash-commands" not in view["claude"]["effective_args"]
    assert view["plain"]["native"] == {"skills": True, "plugins": True, "mcp": True}
    assert view["codex"]["adapter"] == "codex"
    assert view["codex"]["native"] == {"skills": False, "plugins": False, "mcp": False}
    assert view["codex"]["effective_args"] == ["codex", "exec", "-"]
    assert "Codex: no effect" in view["codex"]["notes"][0]
    assert view["bad"]["native"] == {"skills": False, "plugins": False, "mcp": False}
    assert view["bad"]["effective_args"] == [] and "lean" in view["bad"]["error"]


@pytest.fixture
def skills_web(project, tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    service = RuntimeService(store)
    service.register_manifest(project / ".alfrd" / "alfrd.yaml")
    ident = service.list_projects()[0].identifier
    app = create_app({"TESTING": True, "SECRET_KEY": "k", "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'c.sqlite'}",
                      "RUNTIME_SERVICE": service, "CATALOG_READER": RuntimeCatalogReader(service),
                      "STUDIO_DEMO": False, "STUDIO_LIVE_INTERVAL": 0})
    return app.test_client(), f"/api/studio/projects/{ident}/chat", project


def test_skills_route_lists_skills_and_per_agent_native_flags(skills_web):
    client, base, project = skills_web
    write_skill(project, ".alfrd/skills", "review", "---\nname: Review\ndescription: check diffs\n---\nbody\n")
    resp = client.get(f"{base}/skills")
    assert resp.status_code == 200
    body = resp.get_json()
    assert set(body) == {"skills", "agents"}
    assert [{k: s[k] for k in ("id", "name", "description", "source")} for s in body["skills"]] == [
        {"id": "review", "name": "Review", "description": "check diffs", "source": ".alfrd/skills/review/SKILL.md"}]
    assert set(body["skills"][0]) == {"id", "name", "description", "source", "sha256", "bytes"}
    agents = body["agents"]
    assert {"claude", "codex"} <= set(agents)
    for view in agents.values():
        assert set(view) >= {"adapter", "native", "effective_args", "notes"}
        assert set(view["native"]) == {"skills", "plugins", "mcp"}
    assert agents["codex"]["adapter"] == "codex"
    assert agents["codex"]["native"] == {"skills": False, "plugins": False, "mcp": False}
    assert any("Codex: no effect" in note for note in agents["codex"]["notes"])
    assert agents["claude"]["adapter"] == "claude" and "--disable-slash-commands" not in agents["claude"]["effective_args"]


@pytest.mark.parametrize("value,disabled", [(None, False), (True, False), (False, True)])
def test_lean_selected_skills_explicit_precedence(tmp_path, value, disabled):
    write_skill(tmp_path, "skills", "review", "---\nname: Review\ndescription: check diffs\n---\nbody")
    snap = skills.snapshot_selection(tmp_path, {"enabled": True, "selected_ids": ["review"]})
    spec = {"argv": ["claude", "-p"], "lean": True}
    if value is not None:
        spec["skills"] = value
    args = skills.effective_args({"c": spec})[0]["argv"]
    assert ("--disable-slash-commands" in args) is disabled
    assert "--strict-mcp-config" in args and "--setting-sources" in args
    assert f"Use skill review for check diffs; full text in {tmp_path}/skills/review/SKILL.md" in skills.snapshot_prompt(snap)


def test_codex_exposure_preserves_existing_files_and_snapshot(tmp_path):
    source = write_skill(tmp_path, "skills", "review", "---\nname: review\ndescription: review changes\n---\nbody")
    snap = skills.snapshot_selection(tmp_path, {"enabled": True, "selected_ids": ["review"]})
    workspace = tmp_path / "run"
    workspace.mkdir()
    native = workspace / ".agents/skills/alfrd-review"
    native.mkdir(parents=True)
    user_file = native / "SKILL.md"
    user_file.write_text("user content")
    assert skills.expose_codex_skills(tmp_path, workspace, snap)[0]["delivery"] == "injected"
    assert user_file.read_text() == "user content"
    user_file.unlink()
    native.rmdir()
    result = skills.expose_codex_skills(tmp_path, workspace, snap)[0]
    assert result["delivery"] == "native" and native.is_symlink()
    assert (native / "SKILL.md").read_text() == source.read_text()
    assert skills.expose_codex_skills(tmp_path, workspace, snap)[0] == result
    source.write_text("changed")
    assert skills.expose_codex_skills(tmp_path, workspace, snap)[0]["delivery"] == "injected"
    assert skills.expose_codex_skills(tmp_path, workspace, snap, enabled=False) == []


def test_codex_exposure_rejects_symlink_root(tmp_path):
    write_skill(tmp_path, "skills", "x", "x")
    snap = skills.snapshot_selection(tmp_path, {"enabled": True, "selected_ids": ["x"]})
    workspace, outside = tmp_path / "run", tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (workspace / ".agents").symlink_to(outside, target_is_directory=True)
    assert skills.expose_codex_skills(tmp_path, workspace, snap)[0]["delivery"] == "injected"
    assert list(outside.iterdir()) == []


def test_inventory_endpoint_is_read_only_and_reports_selection(skills_web):
    from alfrd import chat

    client, base, project = skills_web
    source = write_skill(project, ".claude/skills", "review", "---\nname: Review\ndescription: check diffs\n---\nbody")
    refs = source.parent / "references"
    refs.mkdir()
    (refs / "guide.md").write_text("guide")
    (refs / "unsafe.md").symlink_to(source)
    chat.save_settings(project, {"skill_selection": {"enabled": True, "selected_ids": ["review"]}})
    response = client.get(f"{base}/skills/inventory")
    assert response.status_code == 200
    item, = response.get_json()["skills"]
    assert item["path"] == str(source)
    assert item["source_root"] == ".claude/skills"
    assert item["bytes"] == source.stat().st_size
    assert item["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert item["reference_files"] == [".claude/skills/review/references/guide.md"]
    assert item["selected_by_default"] is True
    assert item["providers"] == {"claude": {"delivery": "native"}, "codex": {"delivery": "native", "fallback": "injected"}, "generic": {"delivery": "injected"}}
    assert not (project / ".agents/skills").exists()
    assert client.post(f"{base}/skills/inventory").status_code == 405


def test_inventory_and_preview_repo_library(skills_web, monkeypatch):
    """Inventory follows the registered project root and includes unselected skills."""
    from alfrd.gui import studio_chat

    client, base, project = skills_web
    repo = Path(__file__).resolve().parents[1]
    monkeypatch.setattr(studio_chat, "_project_root", lambda name: repo)
    entries = client.get(f"{base}/skills/inventory").get_json()["skills"]
    found = {entry["id"]: entry for entry in entries}
    required = {"alfrd", "artifact-handoff", "blocker-reporting", "failure-triage",
                "mailbox-protocol", "plan-json-author", "verification-checklist"}
    assert required <= found.keys()
    for ident in required:
        item = found[ident]
        assert item["name"] and item["description"] and item["providers"]
        assert item["source_root"] == "skills"
        response = client.get(f"{base}/skills/preview", query_string={"id": ident})
        assert response.status_code == 200
        assert response.get_json()["content"] == (repo / item["source"]).read_text()
    monkeypatch.setattr(studio_chat, "_project_root", lambda name: project)
    assert client.get(f"{base}/skills/inventory").get_json()["skills"] == []
