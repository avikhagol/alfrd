"""`alfrd gsheet …` against a real finished plan and a fake sheet; no credentials/network."""

from __future__ import annotations

import importlib
import json
import sys

import pytest
import yaml
from test_core import SID, FakeClient
from typer.testing import CliRunner

from alfrd.extensions import step_hooks
from alfrd.runtime import scheduler
from tests.test_plan_execution import (  # noqa: F401 - shared scratch fixture
    STEPS,
    _plan,
    project,
)

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")
MAPPING = {"version": 1, "spreadsheet_id": SID, "worksheet": "Targets", "rows": {"key_column": "TARGET_NAME"},
           "outbound": [{"step": "*", "column": "{step}", "field": "status"}]}


class Sheet(FakeClient):
    def metadata(self, spreadsheet_id, *, deadline):
        self.calls.append(("metadata", spreadsheet_id))
        return {"sheets": [{"properties": {"sheetId": 0, "title": "Targets"}}]}


@pytest.fixture
def cli(gsheet, project, monkeypatch):  # noqa: F811
    from alfrd.extensions import settings as host_settings

    sheet = Sheet([["TARGET_NAME", *STEPS], ["T1"], ["T2"]])
    saved = {"default_spreadsheet_id": SID}
    monkeypatch.setattr(host_settings, "values", lambda _: dict(saved))
    monkeypatch.setattr(importlib.import_module("alfrd_gsheet.settings"), "make_client", lambda _: sheet)
    app = importlib.import_module("alfrd_gsheet.cli").cli

    def run(*args):
        return CliRunner().invoke(app, [str(a) for a in args])

    run.sheet, run.saved, run.root = sheet, saved, project
    return run


@pytest.fixture
def finished(cli, monkeypatch):
    """A plan whose six units finished while sync was not set up (the backfill case)."""
    _plan(cli.root)
    folder = scheduler.create_plan(cli.root)
    monkeypatch.setattr(step_hooks, "hooks", list)
    scheduler.Runner(cli.root, folder.id).run()
    assert {u["status"] for u in folder.units()} == {"done"}
    (cli.root / "alfrd.gsheet.yaml").write_text(yaml.safe_dump(MAPPING))
    return folder


def writes(sheet):
    return [d["range"] for _, data, _ in sheet.writes for d in data]


def error_line(result):
    assert result.exit_code == 1
    lines = result.stderr.strip().splitlines()
    assert len(lines) == 1 and lines[0].startswith("gsheet: ")
    return lines[0]


def test_init_matches_steps_refuses_overwrite_and_validates(cli):
    result = cli("init", cli.root)
    assert result.exit_code == 0, result.output
    assert "3 of 3 steps match" in result.stdout
    data = yaml.safe_load((cli.root / "alfrd.gsheet.yaml").read_text())
    assert data["worksheet"] == "Targets"  # first tab from metadata
    assert "spreadsheet_id" not in data  # falls back to the default in Settings
    assert [r["step"] for r in data["outbound"]] == STEPS
    assert "usage.peak_mem" in (cli.root / "alfrd.gsheet.yaml").read_text()  # commented example
    assert ("get", SID, "'Targets'!1:1") in cli.sheet.calls
    assert "already exists" in error_line(cli("init", cli.root))
    assert cli("init", cli.root, "--force", "--spreadsheet", f"https://docs.google.com/spreadsheets/d/{SID}/edit",
               "--worksheet", "Targets").exit_code == 0
    assert yaml.safe_load((cli.root / "alfrd.gsheet.yaml").read_text())["spreadsheet_id"] == SID
    result = cli("validate", cli.root)
    assert result.exit_code == 0 and result.stdout.startswith("ok:")


def test_init_needs_a_spreadsheet_and_header(cli):
    cli.saved.clear()
    assert "--spreadsheet" in error_line(cli("init", cli.root))
    cli.saved["default_spreadsheet_id"] = SID
    cli.sheet.values = []
    assert "is empty" in error_line(cli("init", cli.root))
    assert not (cli.root / "alfrd.gsheet.yaml").exists()


def test_validate_off_offline_and_one_line_errors(cli):
    result = cli("validate", cli.root)
    assert result.exit_code == 0 and "Sync is off" in result.stdout
    (cli.root / "alfrd.gsheet.yaml").write_text(yaml.safe_dump({**MAPPING, "enabled": False}))
    assert "enabled: false" in cli("validate", cli.root).stdout
    bad = {**MAPPING, "outbound": [{"step": "nope", "column": "B", "field": "status"}]}
    (cli.root / "alfrd.gsheet.yaml").write_text(yaml.safe_dump(bad))
    assert "unknown step 'nope'" in error_line(cli("validate", cli.root, "--offline"))
    missing = {**MAPPING, "outbound": [{"step": "*", "column": "{step} RAM", "field": "usage.peak_mem"}]}
    (cli.root / "alfrd.gsheet.yaml").write_text(yaml.safe_dump(missing))
    assert cli.sheet.calls == []
    assert cli("validate", cli.root, "--offline").exit_code == 0
    assert cli.sheet.calls == []
    assert "unknown sheet column" in error_line(cli("validate", cli.root))


def test_unexpected_errors_never_print_details(cli, finished):
    cli.sheet.error = RuntimeError("HTTP body with FAKE-SECRET-KEY")
    for args in (("validate", cli.root), ("push", cli.root)):
        line = error_line(cli(*args))
        assert line == "gsheet: failed (RuntimeError)"


def test_diff_previews_one_unit_without_writing(cli, finished):
    unit = next(u for u in finished.units()
                if u["steps"] == ["fits_to_ms"] and (u.get("rows") or [u.get("row")]) == ["T2"])
    result = cli("diff", cli.root, "--unit", unit["id"])
    assert result.exit_code == 0, result.output
    assert "1 cell(s) would change" in result.stdout
    assert "'Targets'!C3: '' → 'done'" in result.stdout
    assert cli.sheet.writes == [] and [c[0] for c in cli.sheet.calls] == ["get"]
    assert not (cli.root / ".alfrd/gsheet/sync.jsonl").exists()
    assert "no unit 'missing'" in error_line(cli("diff", cli.root, "--unit", "missing"))


def test_push_backfills_changed_cells_in_one_write(cli, finished):
    result = cli("push", cli.root, "--dry-run")
    assert result.exit_code == 0, result.output
    assert "6 cell(s) would change" in result.stdout and cli.sheet.writes == []
    cli.sheet.values[1] = ["T1", "done"]  # already in the sheet: not rewritten
    result = cli("push", cli.root)
    assert result.exit_code == 0, result.output
    assert "ok: wrote 5 cell(s)" in result.stdout
    assert len(cli.sheet.writes) == 1
    assert sorted(writes(cli.sheet)) == ["'Targets'!B3", "'Targets'!C2", "'Targets'!C3", "'Targets'!D2", "'Targets'!D3"]
    entry = json.loads((cli.root / ".alfrd/gsheet/sync.jsonl").read_text().splitlines()[-1])
    assert entry["phase"] == "push" and entry["unit_id"] == "push" and entry["cells_written"] == 5
    status = cli("status", cli.root)
    assert status.exit_code == 0 and "push" in status.stdout and "5 cells" in status.stdout
    assert json.loads(cli("status", cli.root, "--json", "-n", "1").stdout)["result"] == "ok"


def test_push_filters_and_settings_dry_run(cli, finished):
    result = cli("push", cli.root, "--targets", "T2", "--steps", "fits_to_ms,avica_avg")
    assert result.exit_code == 0, result.output
    assert sorted(writes(cli.sheet)) == ["'Targets'!C3", "'Targets'!D3"]
    assert "unknown step 'nope'" in error_line(cli("push", cli.root, "--steps", "nope"))
    assert "no plan 'x'" in error_line(cli("push", cli.root, "--plan", "x"))
    cli.saved["dry_run"] = True
    result = cli("push", cli.root)
    assert "dry_run is on in Settings" in result.stdout and len(cli.sheet.writes) == 1


def test_push_across_plans_with_different_steps(cli, monkeypatch):
    monkeypatch.setattr(step_hooks, "hooks", list)
    for steps in (["preprocess_fitsidi"], ["fits_to_ms", "avica_avg"]):
        _plan(cli.root, targets=("T1",), steps=steps)
        scheduler.Runner(cli.root, scheduler.create_plan(cli.root).id).run()
    (cli.root / "alfrd.gsheet.yaml").write_text(yaml.safe_dump(MAPPING))
    result = cli("push", cli.root)
    assert result.exit_code == 0, result.output
    assert sorted(writes(cli.sheet)) == ["'Targets'!B2", "'Targets'!C2", "'Targets'!D2"]


def test_status_without_history(cli):
    result = cli("status", cli.root)
    assert result.exit_code == 0 and "No sync history yet" in result.stdout


def test_host_mounts_plugin_cli_as_alfrd_gsheet(cli, monkeypatch):
    from types import SimpleNamespace

    import typer

    from alfrd import extensions
    from alfrd.cli import mount_plugin_commands

    manifest = importlib.import_module("alfrd_gsheet").plugin
    ep = SimpleNamespace(name="gsheet", value="alfrd_gsheet:plugin", dist=None, load=lambda: manifest)
    monkeypatch.setattr(extensions, "_entry_points", lambda: [ep])
    app = typer.Typer()

    @app.command()
    def version():  # a second command so Typer keeps subcommand names
        """Core stand-in."""

    records = mount_plugin_commands(app)
    assert [r.status for r in records if r.id == "gsheet"] == ["ok"]
    assert "step_hooks" in next(r for r in records if r.id == "gsheet").kinds
    result = CliRunner().invoke(app, ["gsheet", "status", str(cli.root)])
    assert result.exit_code == 0 and "No sync history yet" in result.stdout
    assert cli.sheet.calls == []
