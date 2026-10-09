"""File-only panel and explicit protected validation, with a fake sheet."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from test_core import SID, FakeClient

from alfrd import extensions, layout_generic
from tests.test_studio import studio_app  # noqa: F401 - shared Flask fixture

CONFIG = {"version": 1, "spreadsheet_id": SID, "worksheet": "Targets", "rows": {"key_column": "TARGET_NAME"},
          "outbound": [{"step": "calibrate", "column": "status", "field": "status"}]}


@pytest.fixture
def studio(gsheet, tmp_path, monkeypatch):
    module = importlib.import_module("alfrd_gsheet.studio")
    manifest = importlib.import_module("alfrd_gsheet").plugin
    ep = SimpleNamespace(name="gsheet", value="alfrd_gsheet:plugin", dist=None, load=lambda: manifest)
    monkeypatch.setattr(extensions, "_entry_points", lambda: [ep])
    extensions.load(force=True)
    return module


def write_mapping(root, config=CONFIG):
    (root / "alfrd.gsheet.yaml").write_text(yaml.safe_dump(config))


def history(root, records):
    path = root / ".alfrd/gsheet/sync.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + '\n{"partial":')
    return path


def record(index, **kwargs):
    return {"at": f"2026-10-09T12:{index:02}:00+00:00", "steps": [f"step-{index}"], "result": "ok",
            "cells_written": index, "conflicts": [], **kwargs}


def test_panel_reads_only_mapping_and_history(studio, tmp_path, monkeypatch):
    write_mapping(tmp_path)
    history(tmp_path, [record(i) for i in range(12)] + [record(15, steps=["step-0"], result="dry run")])
    reads = []
    original_text, original_open = Path.read_text, Path.open

    def check(path):
        assert path in (tmp_path / "alfrd.gsheet.yaml", tmp_path / ".alfrd/gsheet/sync.jsonl")
        reads.append(path)

    def read_text(path, *args, **kwargs):
        check(path)
        return original_text(path, *args, **kwargs)

    def open_file(path, *args, **kwargs):
        check(path)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr(Path, "open", open_file)
    from alfrd.extensions import settings
    monkeypatch.setattr(settings, "values", lambda _: pytest.fail("panel read settings"))
    panel = layout_generic._evaluate(tmp_path, {"panel": "gsheet_sync"}, {}, {})
    assert panel["state"] == "dry run" and panel["total"] == 12
    assert len(panel["rows"]) == 10 and len(panel["more_rows"]) == 2
    assert panel["rows"][0]["step"] == "step-0" and panel["rows"][0]["at"].endswith("+00:00")
    assert panel["sheet"]["url"] == f"https://docs.google.com/spreadsheets/d/{SID}/edit"
    assert reads


def test_panel_empty_disabled_invalid_and_safe_errors(studio, tmp_path, monkeypatch):
    model = studio.evaluate(tmp_path, {}, {}, {})
    assert model["state"] == "off" and "--spreadsheet <id>" in model["command"]
    write_mapping(tmp_path)
    model = studio.evaluate(tmp_path, {}, {}, {})
    assert model["state"] == "syncing" and model["empty"] == "No step has finished since sync was set up."
    write_mapping(tmp_path, {**CONFIG, "enabled": False})
    assert studio.evaluate(tmp_path, {}, {}, {})["label"] == "Off — disabled in mapping"
    write_mapping(tmp_path, {**CONFIG, "outbound": [{"step": "x", "column": "status", "field": "SECRET"}]})
    model = studio.evaluate(tmp_path, {}, {}, {})
    assert model["state"] == "invalid" and "SECRET" not in json.dumps(model)
    monkeypatch.setattr(studio, "_history", lambda _: (_ for _ in ()).throw(RuntimeError("SECRET")))
    assert studio.evaluate(tmp_path, {}, {}, {}) == {"error": "Cannot read the Google Sheet summary (RuntimeError)."}


def test_panel_default_sheet_gid_error_and_history_bound(studio, tmp_path, monkeypatch):
    cfg = {**CONFIG, "gid": 42}
    del cfg["worksheet"], cfg["spreadsheet_id"]
    write_mapping(tmp_path, cfg)
    model = studio.evaluate(tmp_path, {}, {}, {})
    assert "sheet" not in model and "Settings" in model["sheet_note"]
    history(tmp_path, [record(1, spreadsheet_id=SID), record(2, result="error", error="SECRET", conflicts=["A1"])])
    model = studio.evaluate(tmp_path, {}, {}, {})
    assert model["sheet"]["url"].endswith("#gid=42")
    assert model["rows"][0]["conflicts"] == 1 and "SECRET" not in json.dumps(model)
    monkeypatch.setattr(studio, "HISTORY_BYTES", 240)
    assert studio.evaluate(tmp_path, {}, {}, {})["history_truncated"] is True


def test_project_check_is_protected_registered_and_shares_cli_logic(studio, studio_app, tmp_path, monkeypatch):  # noqa: F811
    app, _ = studio_app
    client = app.test_client()
    client.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {app.config['ACCESS_TOKEN']}"
    csrf = {"X-CSRF-Token": client.get("/api/studio/session").get_json()["csrf_token"]}
    # Connect a genuine host project so unknown names/paths remain confined to the runtime catalog.
    from tests.test_studio import PROJECT_ROOT
    (tmp_path / "alfrd.yaml").write_text((PROJECT_ROOT / "examples/avica_0.3/alfrd.yaml").read_text())
    connected = client.post("/api/projects/connect", json={"path": str(tmp_path)}, headers=csrf)
    assert connected.status_code == 201, connected.get_json()
    project = connected.get_json()["name"]
    url = f"/api/studio/projects/{project}/plugins/gsheet/check"
    sheet = FakeClient([["TARGET_NAME", "status"], ["T1", "done"]])
    monkeypatch.setattr(importlib.import_module("alfrd_gsheet.settings"), "make_client", lambda _: sheet)
    from alfrd.extensions import settings
    monkeypatch.setattr(settings, "values", lambda _: {})
    # No credentials needed for the off state; no settings-required gate on project checks.
    assert client.post(url, headers=csrf).get_json()["text"].startswith("Sync is off")
    assert sheet.calls == []
    write_mapping(tmp_path, {**CONFIG, "outbound": [{"step": "preprocess_fitsidi", "column": "status", "field": "status"}]})
    assert client.post(url).status_code == 403
    from flask.testing import FlaskClient
    assert FlaskClient(app).post(url, headers=csrf).status_code == 401
    assert client.post(url, headers=csrf, environ_base={"REMOTE_ADDR": "10.0.0.1"}).status_code == 403
    assert sheet.calls == []
    result = client.post(url, headers=csrf).get_json()
    cli = importlib.import_module("alfrd_gsheet.cli")
    level, text = cli.validate_summary(tmp_path)
    assert result == {"level": level, "text": text[:120]}
    assert level == "ok" and not sheet.writes
    assert client.get("/studio/plugins/gsheet/index.js").status_code == 200
    assert client.get("/studio/plugins/gsheet/index.css").status_code == 200
    write_mapping(tmp_path, {**CONFIG, "outbound": [{"step": "unknown", "column": "status", "field": "status"}]})
    assert client.post(url, headers=csrf).get_json()["level"] == "warn"
    write_mapping(tmp_path, {**CONFIG, "outbound": [
        {"step": "preprocess_fitsidi", "column": "missing time", "field": "duration_s"},
        {"step": "preprocess_fitsidi", "column": "missing RAM", "field": "usage.peak_mem"},
    ]})
    sheet.calls.clear()
    summary = client.post(url, headers=csrf).get_json()
    assert summary["level"] == "warn" and summary["text"].startswith("2 problems:")
    assert [c[0] for c in sheet.calls] == ["get"] and sheet.writes == []
    write_mapping(tmp_path, {**CONFIG, "outbound": []})
    sheet.error = RuntimeError("SECRET")
    assert client.post(url, headers=csrf).get_json() == {"level": "fail", "text": "failed (RuntimeError)"}
    assert client.post("/api/studio/projects/nope/plugins/gsheet/check", headers=csrf).status_code == 404
    extensions.set_enabled("gsheet", False)
    assert client.post(url, headers=csrf).status_code == 404


def test_registered_actions_work_through_protected_routes(studio, studio_app, tmp_path, monkeypatch):  # noqa: F811
    app, _ = studio_app
    client = app.test_client()
    client.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {app.config['ACCESS_TOKEN']}"
    csrf = {"X-CSRF-Token": client.get("/api/studio/session").get_json()["csrf_token"]}
    from tests.test_studio import PROJECT_ROOT
    (tmp_path / "alfrd.yaml").write_text((PROJECT_ROOT / "examples/avica_0.3/alfrd.yaml").read_text())
    connected = client.post("/api/projects/connect", json={"path": str(tmp_path)}, headers=csrf)
    name = connected.get_json()["name"]
    base = f"/api/studio/projects/{name}/plugins/gsheet/actions/"
    sheet = FakeClient([["TARGET_NAME", "preprocess_fitsidi"], ["T1"]])
    monkeypatch.setattr(importlib.import_module("alfrd_gsheet.settings"), "make_client", lambda _: sheet)
    from alfrd.extensions import settings
    monkeypatch.setattr(settings, "values", lambda _: {"default_spreadsheet_id": SID})
    assert client.post(base + "state", headers=csrf).get_json()["data"]["attached"] is False
    response = client.post(base + "attach", json={"worksheet": "Targets", "key_column": "TARGET_NAME"}, headers=csrf)
    result = response.get_json()
    assert response.status_code == 200 and result["ok"], result
    data = result["data"]
    assert data["mapping"]["outbound"] == [{"step": "preprocess_fitsidi", "column": "preprocess_fitsidi", "field": "status"}]
    validation = client.post(base + "validate", headers=csrf).get_json()
    assert validation == {"ok": True, "data": {"errors": [], "warnings": []}}
    payload = {"mapping": {**data["mapping"], "enabled": False}, "base_sha256": data["mapping_sha256"]}
    saved = client.post(base + "save", json=payload, headers=csrf).get_json()
    assert saved["ok"] and saved["data"]["saved"]
    conflict = client.post(base + "save", json=payload, headers=csrf).get_json()
    assert conflict == {"ok": False, "error": "The mapping changed on disk; reload."}
    app.config["PLUGINS_ACTIONS"] = False
    assert client.post(base + "detach", json={"mode": "delete", "base_sha256": saved["data"]["mapping_sha256"]},
                       headers=csrf).status_code == 403
    assert client.post(base + "state", headers=csrf).get_json()["data"]["attached"]
    assert sheet.writes == []
