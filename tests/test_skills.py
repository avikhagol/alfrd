"""Skill discovery and effective-args dry run (contract C27)."""

from __future__ import annotations

import hashlib

from alfrd import skills


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
    assert "--disable-slash-commands" in out["c"]["argv"]
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
