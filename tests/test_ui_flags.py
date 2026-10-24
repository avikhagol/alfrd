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


def test_machine_lock_reports_reason():
    rows = {row["key"]: row for row in resolve(
        browser={"ui.live_updates": False},
        machine={"locks": {"ui.live_updates": "disabled by administrator"}},
    )}
    assert rows["ui.live_updates"]["locked"] is True
    assert rows["ui.live_updates"]["reason"] == "disabled by administrator"
