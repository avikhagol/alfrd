"""Validate the shipped library against the public saved-skill APIs."""
from pathlib import Path
import hashlib

import pytest
import yaml

from alfrd import skills

ROOT = Path(__file__).resolve().parents[1]
SKILL_DIRS = sorted(p for p in (ROOT / "skills").iterdir() if p.is_dir())
REQUIRED = {
    "alfrd", "verification-checklist", "plan-json-author", "mailbox-protocol",
    "blocker-reporting", "failure-triage", "artifact-handoff",
}


def test_shipped_library_is_discovered():
    expected = {p.name for p in SKILL_DIRS}
    assert REQUIRED <= expected
    found = {s["id"]: s for s in skills.discover_skills(ROOT)}
    assert expected <= found.keys()
    for directory in SKILL_DIRS:
        path = directory / "SKILL.md"
        data = path.read_bytes()
        entry = found[directory.name]
        assert entry["source"] == path.relative_to(ROOT).as_posix()
        assert entry["bytes"] == len(data)
        assert entry["sha256"] == hashlib.sha256(data).hexdigest()
        assert skills.preview(ROOT, directory.name)["content"] == data.decode("utf-8")


@pytest.mark.parametrize("directory", SKILL_DIRS, ids=lambda p: p.name)
def test_shipped_skill_frontmatter_and_size(directory):
    path = directory / "SKILL.md"
    data = path.read_bytes()
    assert 0 < len(data) <= skills.MAX_SKILL_BYTES
    text = data.decode("utf-8")
    assert text.startswith("---\n")
    front, body = text[4:].split("\n---\n", 1)
    meta = yaml.safe_load(front)
    assert isinstance(meta, dict)
    assert meta["name"] == directory.name
    assert isinstance(meta["description"], str)
    assert 0 < len(meta["description"].strip()) < 300
    assert body.strip()
    parsed = skills.parse_frontmatter(text)
    assert parsed["name"] == meta["name"]
    assert parsed["description"] == meta["description"]


def test_whole_shipped_library_fits_selection_budget():
    ids = [p.name for p in SKILL_DIRS]
    snapshot = skills.snapshot_selection(ROOT, {"enabled": True, "selected_ids": ids})
    assert [s["id"] for s in snapshot["skills"]] == ids
    assert snapshot["bytes"] == sum((p / "SKILL.md").stat().st_size for p in SKILL_DIRS)
    assert snapshot["bytes"] <= skills.MAX_SELECTED_BYTES
