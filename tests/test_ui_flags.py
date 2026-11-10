from __future__ import annotations

import pytest

from alfrd.ui_flags import RevisionConflict, UIFlagError, UIFlagRegistry, resolve, validate_ui


def test_validate_ui_rejects_unknown_and_bad_values():
    with pytest.raises(UIFlagError):
        validate_ui({"ui.rows_per_page": 12})
    with pytest.raises(UIFlagError):
        validate_ui({"not-a-flag": True})


def test_resolution_precedence_and_dependency_reason():
    rows = {row["key"]: row for row in resolve(
        browser={"ui.experimental_chat": True},
        project={"ui.experimental_chat": False},
        machine={"features.experimental": True},
    )}
    assert rows["ui.experimental_chat"]["effective"] is True
    assert rows["ui.experimental_chat"]["disabled"] is False
    rows = {row["key"]: row for row in resolve(browser={"ui.experimental_chat": True})}
    assert rows["ui.experimental_chat"]["disabled"] is True
    assert "features.experimental" in rows["ui.experimental_chat"]["reason"]


def test_revision_increment_and_stale_conflict():
    registry = UIFlagRegistry()
    assert registry.revision == 0
    assert registry.save("browser", {"ui.live_updates": False}, expected_revision=0) == 1
    with pytest.raises(RevisionConflict) as error:
        registry.save("browser", {"ui.live_updates": True}, expected_revision=0)
    assert error.value.status_code == 409
    assert registry.revision == 1


SKILL_FLAGS = ("chat.native_skills", "chat.native_plugins", "chat.native_mcp", "chat.saved_skills")


def test_skill_flags_are_registered_as_project_booleans():
    from alfrd.ui_flags import BY_KEY, DEFAULTS

    for key in SKILL_FLAGS:
        assert BY_KEY[key].scope == "project" and BY_KEY[key].type == "bool"
        assert DEFAULTS["ui"][key] == BY_KEY[key].default
    rows = {row["key"]: row for row in resolve(project={"chat.saved_skills": True})}
    assert rows["chat.saved_skills"]["effective"] is True and rows["chat.saved_skills"]["source"] == "project"
    assert rows["chat.native_skills"]["effective"] is True and rows["chat.native_skills"]["source"] == "default"
    assert rows["chat.native_skills"]["scope"] == "project" and rows["chat.native_skills"]["dangerous"] is False
    with pytest.raises(UIFlagError):
        validate_ui({"chat.saved_skills": "yes"})


def test_skill_flags_save_only_in_project_scope():
    registry = UIFlagRegistry()
    assert registry.save("project", {"chat.saved_skills": True, "chat.native_mcp": False}, expected_revision=0) == 1
    saved = registry.values()["project"]
    assert saved["chat.saved_skills"] is True and saved["chat.native_mcp"] is False
    with pytest.raises(UIFlagError, match="browser cannot save"):
        registry.save("browser", {"chat.saved_skills": False})


def test_machine_lock_reports_reason():
    rows = {row["key"]: row for row in resolve(
        browser={"ui.live_updates": False},
        machine={"locks": {"ui.live_updates": "disabled by administrator"}},
    )}
    assert rows["ui.live_updates"]["locked"] is True
    assert rows["ui.live_updates"]["reason"] == "disabled by administrator"
