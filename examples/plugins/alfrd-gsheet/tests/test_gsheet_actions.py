"""Studio actions with real scratch plans and a fake Sheets transport; no network."""
from __future__ import annotations

import copy
import importlib
import json
import threading
import time
from pathlib import Path

import pytest
import yaml
from test_core import SID
from test_gsheet_cli import (  # noqa: F401 - shared fixtures
    MAPPING,
    cli,
    finished,
    project,
)


@pytest.fixture
def actions(cli):  # noqa: F811 - shared fixture
    module = importlib.import_module("alfrd_gsheet.actions")
    registered = {a.id: a for a in importlib.import_module("alfrd_gsheet").plugin.project_actions}

    def run(name, **payload):
        return registered[name].run(cli.root, cli.saved, payload)

    run.module, run.registered, run.cli = module, registered, cli
    return run


def attach(actions, **kwargs):
    return actions("attach", worksheet="Targets", key_column="TARGET_NAME", **kwargs)


def test_state_is_offline_and_returns_only_public_settings(actions):
    session = actions.cli
    session.saved.update(credentials_json=json.dumps({"private_key": "SECRET", "client_email": "PRIVATE"}), dry_run=True)
    state = actions("state")
    assert not state["attached"] and state["mapping_sha256"] == ""
    assert state["credentials"] and state["default_spreadsheet"] and state["dry_run"]
    assert state["steps"] and {"usage.*", "template"} <= {f["id"] for f in state["fields"]}
    assert "SECRET" not in json.dumps(state) and "PRIVATE" not in json.dumps(state)
    assert session.sheet.calls == []
    info = attach(actions)
    state = actions("state")
    assert state["attached"] and state["enabled"] and state["mapping"] == info["mapping"]
    assert state["mapping_sha256"] == info["mapping_sha256"]
    assert not state["intrinsic_errors"] and state["comments_notice"]


def test_sheet_info_headers_samples_and_gid(actions):
    session = actions.cli
    info = actions("sheet_info", spreadsheet=f"https://docs.google.com/spreadsheets/d/{SID}/edit#gid=0")
    assert info == {"spreadsheet_id": SID, "tabs": [{"title": "Targets", "gid": 0}]}
    assert [c[0] for c in session.sheet.calls] == ["metadata"]
    session.sheet.calls.clear()
    result = actions("headers", worksheet="Targets", header_row=1)
    assert result["headers"][0] == {"letter": "A", "name": "TARGET_NAME"}
    assert result["sample_keys"] == ["T1", "T2"]
    assert session.sheet.calls == [("get", SID, "'Targets'!1:21")]
    actions("headers", gid=0)
    assert [c[0] for c in session.sheet.calls][-2:] == ["title", "get"]
    session.sheet.values += [[f"T{i}"] for i in range(50)]
    assert len(actions("headers", worksheet="Targets")["sample_keys"]) == 20


@pytest.mark.parametrize(("name", "payload"), [
    ("sheet_info", {"spreadsheet": "bad"}),
    ("headers", {"worksheet": "Targets", "header_row": True}),
    ("headers", {"gid": -1}), ("headers", {"gid": 0, "worksheet": "Targets"}),
    ("attach", {"worksheet": "Targets", "key_column": ""}),
    ("attach", {"worksheet": "Targets", "key_column": "TARGET_NAME", "force": "yes"}),
    ("detach", {"mode": "wrong", "base_sha256": ""}),
    ("save", {"mapping": MAPPING}), ("preview", {"steps": "x"}),
    ("preview", {"plan": 4}), ("backfill", {"confirm": 1}),
])
def test_bad_payloads_never_write(actions, name, payload):
    with pytest.raises(ValueError):
        actions(name, **payload)
    assert not (actions.cli.root / "alfrd.gsheet.yaml").exists()
    assert not actions.cli.sheet.writes


def test_attach_default_explicit_force_and_column_checks(actions):
    result = attach(actions)
    assert "spreadsheet_id" not in result["mapping"]
    calls = len(actions.cli.sheet.calls)
    with pytest.raises(ValueError, match="already exists"):
        attach(actions)
    assert len(actions.cli.sheet.calls) == calls
    result = attach(actions, spreadsheet=SID, force=True)
    assert result["mapping"]["spreadsheet_id"] == SID
    assert result["matched"] == actions("state")["steps"]
    before = (actions.cli.root / "alfrd.gsheet.yaml").read_bytes()
    with pytest.raises(ValueError):
        actions("attach", worksheet="Targets", key_column="TARGET_NAME", code_column="TARGET_NAME", force=True)
    assert (actions.cli.root / "alfrd.gsheet.yaml").read_bytes() == before


def test_validate_drafts_full_paths_disabled_and_live_headers(actions):
    data = attach(actions)["mapping"]
    draft = copy.deepcopy(data)
    draft["outbound"] = [
        {"step": "missing", "column": "B", "field": "status"},
        {"step": data["outbound"][0]["step"], "column": "absent", "field": "status"},
        {"step": "*", "column": "C", "field": "usage.not_a_key"},
    ]
    errors = actions("validate", mapping=draft)["errors"]
    assert {e["path"] for e in errors} == {"outbound[0].step", "outbound[2].field"}
    draft["outbound"] = draft["outbound"][1:2]
    assert actions("validate", mapping=draft)["errors"][0]["path"] == "outbound[0].column"
    draft["enabled"] = False
    assert actions("validate", mapping=draft)["errors"]
    draft["outbound"] = [data["outbound"][0], data["outbound"][0]]
    assert actions("validate", mapping=draft)["errors"][0]["path"] == "outbound[1].column"
    assert not actions("validate", mapping=data)["errors"]


def test_save_stable_order_invalid_drafts_and_unreachable_warning(actions):
    state = attach(actions)
    data = state["mapping"]
    data["verify_before_write"] = False
    saved = actions("save", mapping=data, base_sha256=state["mapping_sha256"])
    assert saved["saved"] and not saved["errors"]
    path = actions.cli.root / "alfrd.gsheet.yaml"
    assert list(yaml.safe_load(path.read_text())) == ["version", "enabled", "worksheet", "header_row", "rows",
                                                   "verify_before_write", "outbound"]
    assert "#" not in path.read_text()
    before = path.read_bytes()
    invalid = {**data, "version": 9}
    result = actions("save", mapping=invalid, base_sha256=saved["mapping_sha256"])
    assert result["saved"] is False and result["errors"][0]["path"] == "version"
    assert path.read_bytes() == before
    actions.cli.sheet.error = RuntimeError("raw body SECRET")
    data["enabled"] = False
    result = actions("save", mapping=data, base_sha256=saved["mapping_sha256"])
    assert result["saved"] and result["warnings"] and "SECRET" not in json.dumps(result)


def test_save_and_detach_sha_conflicts_include_comment_edits(actions):
    state = attach(actions)
    path = actions.cli.root / "alfrd.gsheet.yaml"
    path.write_text(path.read_text() + "# hand edit\n")
    before = path.read_bytes()
    for name, payload in (("save", {"mapping": state["mapping"]}), ("detach", {"mode": "delete"}),
                          ("detach", {"mode": "disable"})):
        with pytest.raises(ValueError, match="changed on disk; reload"):
            actions(name, base_sha256=state["mapping_sha256"], **payload)
        assert path.read_bytes() == before


def test_disable_and_delete_leave_history_and_workflow_untouched(actions):
    state = attach(actions)
    root = actions.cli.root
    workflow = (root / "alfrd.yaml").read_bytes()
    history = root / ".alfrd/gsheet/sync.jsonl"
    history.parent.mkdir(parents=True, exist_ok=True)
    history.write_text("old history\n")
    disabled = actions("detach", mode="disable", base_sha256=state["mapping_sha256"])
    assert disabled["attached"] and not actions("state")["enabled"]
    actions("detach", mode="delete", base_sha256=disabled["mapping_sha256"])
    assert not actions("state")["attached"]
    assert history.read_text() == "old history\n" and (root / "alfrd.yaml").read_bytes() == workflow


def test_mapping_writes_are_atomic_under_lock_and_never_open_workflow(actions, monkeypatch):
    module = actions.module.project
    csv = importlib.import_module("alfrd.runtime.plan_csv")
    sync = importlib.import_module("alfrd_gsheet.sync")
    real_replace, real_open = csv.os.replace, Path.open
    replacements = []

    def replace_file(source, target):
        target = Path(target)
        assert target.name == "alfrd.gsheet.yaml" and Path(source).parent == target.parent
        # The common lock must be held at the actual replace boundary.
        with pytest.raises(sync.SyncError, match="lock timed out"), sync.locked(
                target.parent / ".alfrd/locks/alfrd.gsheet.yaml.lock", time.monotonic() + .01):
            pytest.fail("mapping lock was not held")
        replacements.append(target)
        real_replace(source, target)

    def open_file(path, mode="r", *args, **kwargs):
        assert path.name != "alfrd.yaml" or not any(flag in mode for flag in "wax+")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(csv.os, "replace", replace_file)
    monkeypatch.setattr(Path, "open", open_file)
    state = attach(actions)
    actions("save", mapping=state["mapping"], base_sha256=state["mapping_sha256"])
    sha = actions("state")["mapping_sha256"]
    actions("detach", mode="disable", base_sha256=sha)
    assert len(replacements) == 3
    assert not list(actions.cli.root.glob(".alfrd.gsheet.yaml.*.tmp"))
    assert module.mapping_sha(actions.cli.root)


def test_concurrent_saves_only_one_revision_wins(actions):
    state = attach(actions)
    results, failures = [], []

    def save(enabled):
        try:
            results.append(actions("save", mapping={**state["mapping"], "enabled": enabled},
                                   base_sha256=state["mapping_sha256"]))
        except ValueError as exc:
            failures.append(str(exc))

    threads = [threading.Thread(target=save, args=(enabled,)) for enabled in (True, False)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()
    assert len(results) == len(failures) == 1 and "changed on disk" in failures[0]


def test_atomic_replace_failure_retains_old_file_and_cleans_temp(actions, monkeypatch):
    state = attach(actions)
    path = actions.cli.root / "alfrd.gsheet.yaml"
    before = path.read_bytes()
    csv = importlib.import_module("alfrd.runtime.plan_csv")

    def fail(*args):
        raise OSError("disk failed SECRET")

    monkeypatch.setattr(csv.os, "replace", fail)
    with pytest.raises(ValueError, match="OSError") as caught:
        actions("save", mapping=state["mapping"], base_sha256=state["mapping_sha256"])
    assert "SECRET" not in str(caught.value) and path.read_bytes() == before
    assert not list(path.parent.glob(".alfrd.gsheet.yaml.*.tmp"))


def test_preview_and_backfill_share_real_engine(actions, finished):  # noqa: F811
    session = actions.cli
    before = (session.root / "alfrd.yaml").read_bytes()
    result = actions("preview", targets=["T2"], steps=["fits_to_ms"])
    assert result["cells"] == [{"a1": "'Targets'!C3", "target": "T2", "step": "fits_to_ms",
                                "column": "fits_to_ms", "old": "", "new": "done"}]
    assert result["total_cells"] == 1 and not result["truncated"]
    assert not session.sheet.writes and not (session.root / ".alfrd/gsheet/sync.jsonl").exists()
    session.saved["dry_run"] = True
    result = actions("backfill", confirm=True)
    assert result["dry_run"] and result["result"] == "dry run" and not session.sheet.writes
    session.saved["dry_run"] = False
    result = actions("backfill", confirm=True)
    assert result["cells_written"] == 6 and len(session.sheet.writes) == 1
    assert actions("state")["last_sync"]["cells_written"] == 6
    assert (session.root / "alfrd.yaml").read_bytes() == before


def test_preview_cap_and_no_secrets_in_cell_values(actions, monkeypatch):
    sync = importlib.import_module("alfrd_gsheet.sync")
    project_module = actions.module.project
    actions.cli.saved["credentials_json"] = json.dumps({"private_key": "SECRET"})
    result = sync.SyncResult("dry run", {f"'Targets'!B{i}": "SECRET" for i in range(1, 503)})
    monkeypatch.setattr(project_module, "push_project", lambda *a, **kw: project_module.PushOutcome(502, result))
    preview = actions("preview")
    assert len(preview["cells"]) == 500 and preview["truncated"] and preview["total_cells"] == 502
    assert "SECRET" not in json.dumps(preview)


@pytest.mark.parametrize("name", ["headers", "validate", "save", "preview", "backfill"])
def test_transport_errors_never_expose_body(actions, finished, name):  # noqa: F811
    state = actions("state")
    actions.cli.sheet.error = ValueError("Google raw response SECRET")
    payload = {"worksheet": "Targets", "mapping": state["mapping"],
               "base_sha256": state["mapping_sha256"], "confirm": True}
    if name in {"validate", "save"}:
        assert "SECRET" not in json.dumps(actions(name, **payload))
    else:
        with pytest.raises(ValueError) as caught:
            actions(name, **payload)
        assert "SECRET" not in str(caught.value)


def test_manifest_mutation_flags_and_timeouts(actions):
    assert {name for name, action in actions.registered.items() if action.mutating} == {
        "save", "attach", "detach", "backfill"}
    assert actions.registered["backfill"].timeout == 300
    assert len(actions.registered) == 9


def test_auto_panel_only_with_mapping_and_no_network(actions):
    panel = importlib.import_module("alfrd_gsheet").plugin.panels[0]
    assert panel.auto(actions.cli.root) is None
    path = actions.cli.root / "alfrd.gsheet.yaml"
    path.write_text("broken: [\n")
    assert panel.auto(actions.cli.root) == {"title": "Google Sheet", "scope": "project"}
    assert not actions.cli.sheet.calls
    state = actions("state")
    assert state["attached"] and state["mapping_sha256"] and state["intrinsic_errors"]
    actions("detach", mode="delete", base_sha256=state["mapping_sha256"])
    assert panel.auto(actions.cli.root) is None


def test_save_creation_conflict_and_validation_missing_mapping(actions):
    assert actions("validate") == {"errors": [], "warnings": ["Sync is off: no alfrd.gsheet.yaml."]}
    data = {**MAPPING, "outbound": []}
    result = actions("save", mapping=data, base_sha256="")
    assert result["saved"] and actions("state")["attached"]
    with pytest.raises(ValueError, match="changed on disk"):
        actions("save", mapping=data, base_sha256="")


def test_attach_rechecks_creation_race_under_lock(actions, monkeypatch):
    path = actions.cli.root / "alfrd.gsheet.yaml"
    original = actions.cli.sheet.get

    def raced_read(*args, **kwargs):
        path.write_text("# another client attached\n")
        return original(*args, **kwargs)

    monkeypatch.setattr(actions.cli.sheet, "get", raced_read)
    with pytest.raises(ValueError, match="already exists"):
        attach(actions)
    assert path.read_text() == "# another client attached\n"


def test_save_rechecks_revision_after_live_validation(actions, monkeypatch):
    state = attach(actions)
    path = actions.cli.root / "alfrd.gsheet.yaml"
    original = actions.cli.sheet.get

    def raced_read(*args, **kwargs):
        path.write_text(path.read_text() + "# external edit during validation\n")
        return original(*args, **kwargs)

    monkeypatch.setattr(actions.cli.sheet, "get", raced_read)
    with pytest.raises(ValueError, match="changed on disk"):
        actions("save", mapping=state["mapping"], base_sha256=state["mapping_sha256"])
    assert path.read_text().endswith("# external edit during validation\n")


def test_read_only_actions_do_not_write_files_or_sheet(actions, finished, monkeypatch):  # noqa: F811
    root = actions.cli.root
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    real_open = Path.open

    def open_file(path, mode="r", *args, **kwargs):
        assert not any(flag in mode for flag in "wax+"), f"read-only action opened {path} with {mode}"
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_file)
    for name in ("state", "sheet_info", "headers", "validate", "preview"):
        actions(name, worksheet="Targets")
    assert {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()} == before
    assert actions.cli.sheet.writes == []


def test_preview_appended_identity_cells_have_labels(actions, finished):  # noqa: F811
    state = actions("state")
    state["mapping"]["rows"]["missing_row"] = "append"
    actions("save", mapping=state["mapping"], base_sha256=state["mapping_sha256"])
    actions.cli.sheet.values = [actions.cli.sheet.values[0]]
    preview = actions("preview", targets=["T2"], steps=["fits_to_ms"])
    assert preview["cells"] == [
        {"a1": "'Targets'!A2", "target": "T2", "step": "", "column": "TARGET_NAME", "old": "", "new": "T2"},
        {"a1": "'Targets'!C2", "target": "T2", "step": "fits_to_ms", "column": "fits_to_ms", "old": "", "new": "done"},
    ]
    assert not actions.cli.sheet.writes


@pytest.mark.parametrize(("name", "method", "payload"), [
    ("sheet_info", "metadata", {}),
    ("attach", "get", {"worksheet": "Targets", "key_column": "TARGET_NAME"}),
    ("attach", "worksheet_title", {"gid": 0, "key_column": "TARGET_NAME"}),
])
def test_metadata_and_attach_transport_errors_are_safe(actions, monkeypatch, name, method, payload):
    def fail(*args, **kwargs):
        raise ValueError("raw Google body SECRET")

    monkeypatch.setattr(actions.cli.sheet, method, fail)
    with pytest.raises(ValueError) as caught:
        actions(name, **payload)
    assert "SECRET" not in str(caught.value) and "raw Google" not in str(caught.value)
    assert not (actions.cli.root / "alfrd.gsheet.yaml").exists()


def test_mapping_lock_timeout_keeps_revision_and_file(actions):
    state = attach(actions)
    root = actions.cli.root
    path = root / "alfrd.gsheet.yaml"
    before = path.read_bytes()
    sync = importlib.import_module("alfrd_gsheet.sync")
    with (sync.locked(root / ".alfrd/locks/alfrd.gsheet.yaml.lock", time.monotonic() + 2),
          pytest.raises(sync.SyncError, match="lock timed out")):
        actions.module.project.write_mapping(root, "invalid", base_sha256=state["mapping_sha256"], timeout=.01)
    assert path.read_bytes() == before
    assert actions("state")["mapping_sha256"] == state["mapping_sha256"]
