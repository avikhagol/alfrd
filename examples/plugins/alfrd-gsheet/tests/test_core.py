"""Mapping and sync behavior with a fake Sheets client; no credentials/network.

The real CSV updater is used for inbound safety. Each assertion inspects the
client boundary, not internal algorithm calls, including exact write ranges.
"""

from __future__ import annotations

import copy
import json
import threading
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest
import yaml

from alfrd.extensions import StepContext
from alfrd.runtime import plan_csv

SID = "1AbCdEfGhIjKlMnOpQrStUvWxYz"


class FakeClient:
    def __init__(self, values):
        self.values = values
        self.live = {}
        self.calls = []
        self.writes = []
        self.error = None

    def get(self, spreadsheet_id, range_, *, deadline):
        assert time.monotonic() < deadline
        self.calls.append(("get", spreadsheet_id, range_))
        if self.error:
            raise self.error
        return copy.deepcopy(self.values)

    def batch_get(self, spreadsheet_id, ranges, *, deadline):
        assert time.monotonic() < deadline
        self.calls.append(("batch_get", spreadsheet_id, ranges))
        return [[[self.live.get(address, "")]] for address in ranges]

    def batch_update(self, spreadsheet_id, data, *, value_input_option, deadline):
        assert time.monotonic() < deadline
        self.calls.append(("batch_update", spreadsheet_id, data))
        if self.error:
            raise self.error
        self.writes.append((spreadsheet_id, data, value_input_option))
        for item in data:
            self.live[item["range"]] = item["values"][0][0]

    def worksheet_title(self, spreadsheet_id, gid, *, deadline):
        self.calls.append(("title", spreadsheet_id, gid))
        return "Targets"


@pytest.fixture
def case(gsheet, tmp_path):
    config = {"version": 1, "spreadsheet_id": SID, "worksheet": "Targets", "rows": {"key_column": "TARGET_NAME"},
              "outbound": [{"step": "s1", "column": "s1", "field": "status"}]}
    path = tmp_path / "alfrd.gsheet.yaml"

    def save(data=None):
        path.write_text(yaml.safe_dump(data if data is not None else config))

    save()
    csv = tmp_path / "plan.csv"
    csv.write_text("TARGET_NAME,PROJECT_CODE,WORKDIR,FITS_FILE,s1,s2,s3,notes\nT1,,,,running,todo,running,old\n")
    access = gsheet.sync.PlanAccess(csv, tmp_path / ".alfrd/locks/plan.csv.lock", ("s1", "s2", "s3"),
                                   {"key_column": "TARGET_NAME", "code_column": "PROJECT_CODE",
                                    "workdir_column": "WORKDIR", "files_column": "FITS_FILE"})
    ctx = StepContext(str(tmp_path), "plan-1", "unit-1", "step", ("s1",),
                      ({"key": "T1", "target": "T1", "code": "", "workdir": ""},),
                      status="done", cells={"T1": {"s1": "done"}})
    client = FakeClient([["TARGET_NAME", "s1", "s2", "s1 RAM", "notes"], ["T1", "", "", "", "new"]])
    engine = gsheet.sync.Sync(client, plan_loader=lambda _: access)
    return SimpleNamespace(config=config, save=save, path=path, access=access,
                           ctx=ctx, client=client, engine=engine, root=tmp_path)


def history(case):
    return [json.loads(line) for line in (case.root / ".alfrd/gsheet/sync.jsonl").read_text().splitlines()]


@pytest.mark.parametrize(("number", "letters"), [(1, "A"), (26, "Z"), (27, "AA"), (52, "AZ"),
                                                   (53, "BA"), (702, "ZZ"), (703, "AAA"), (18278, "ZZZ")])
def test_a1_roundtrip(gsheet, number, letters):
    assert gsheet.a1.letters(number) == letters
    assert gsheet.a1.index(letters.lower()) == number


def test_a1_quoting_headers_and_bad_inputs(gsheet):
    a1 = gsheet.a1
    assert a1.cell("It's", 12, 6) == "'It''s'!F12"
    assert a1.column([" Target ", "RAM"], "target") == 1
    assert a1.column(["Z", "A"], "A") == 2  # header takes precedence
    assert a1.column(["target"], "AA") == 27
    for value in (0, -1, True, 1.5):
        with pytest.raises(ValueError):
            a1.letters(value)
    with pytest.raises(ValueError, match="ambiguous"):
        a1.column(["target", " TARGET "], "target")
    with pytest.raises(ValueError):
        a1.index("A1")
    assert a1.origin("BA3:ZZ") == (3, 53)
    with pytest.raises(ValueError):
        a1.origin("'Other'!A1:B2")


def test_one_changed_cell_and_one_batch(case):
    state = case.engine.before(case.ctx)
    result = case.engine.after(case.ctx, state)
    assert [c[0] for c in case.client.calls] == ["get", "batch_get", "batch_update"]
    assert case.client.writes == [(SID, [{"range": "'Targets'!B2", "values": [["done"]]}], "RAW")]
    assert result.cells_written == 1
    assert history(case)[0]["steps"] == ["s1"]


def test_no_changes_one_read_no_writes(case):
    case.client.values[1][1] = "done"
    state = case.engine.before(case.ctx)
    result = case.engine.after(case.ctx, state)
    assert result.result == "no changes"
    assert [c[0] for c in case.client.calls] == ["get"]
    assert case.client.writes == []


@pytest.mark.parametrize("mode", ["absent", "disabled", "unmapped"])
def test_no_mapping_no_api(case, mode):
    if mode == "absent":
        case.path.unlink()
    elif mode == "disabled":
        case.config["enabled"] = False
        case.save()
    else:
        case.config["outbound"][0]["step"] = "s2"
        case.save()
    assert case.engine.before(case.ctx) is None
    assert case.engine.after(case.ctx, None) is None
    assert case.client.calls == []


def test_none_after_failed_before_no_calls_and_readopted_takes_snapshot(case):
    assert case.engine.after(case.ctx, None) is None
    assert case.client.calls == []
    result = case.engine.after(replace(case.ctx, readopted=True), None)
    assert result.cells_written == 1
    assert [c[0] for c in case.client.calls] == ["get", "batch_get", "batch_update"]


def test_multiple_fields_single_write_batch(case):
    case.config["outbound"].append({"step": "s1", "column": "s1 RAM", "field": "usage.peak_mem", "format": "bytes"})
    case.save()
    ctx = replace(case.ctx, usage={"peak_mem": 1024})
    result = case.engine.after(ctx, case.engine.before(ctx))
    assert result.cells_written == 2
    assert len(case.client.writes) == 1
    assert case.client.writes[0][1] == [{"range": "'Targets'!B2", "values": [["done"]]},
                                      {"range": "'Targets'!D2", "values": [["1 KiB"]]}]


def test_batch_multistep_wildcard_and_row_codes(case):
    case.config["rows"]["code_column"] = "PROJECT_CODE"
    case.config["outbound"] = [{"step": "*", "column": "{step}", "field": "status"}]
    case.save()
    case.client.values = [["TARGET_NAME", "PROJECT_CODE", "s1", "s2", "s3"], ["T1", "P1"], ["T1", "P2"]]
    ctx = replace(case.ctx, mode="batch", steps=("s1", "s2"), status="failed",
                  rows=({"key": "T1@P1", "target": "T1", "code": "P1"},
                        {"key": "T1@P2", "target": "T1", "code": "P2"}),
                  cells={"T1@P1": {"s1": "done", "s2": "failed"}, "T1@P2": {"s1": "done", "s2": "blocked"}})
    result = case.engine.after(ctx, case.engine.before(ctx))
    assert result.changes == {"'Targets'!C2": "done", "'Targets'!D2": "failed",
                              "'Targets'!C3": "done", "'Targets'!D3": "blocked"}
    assert len(case.client.writes) == 1


def test_when_uses_individual_step_status(case):
    case.config["outbound"] = [{"step": "*", "column": "{step}", "field": "status", "when": ["done"]}]
    case.save()
    case.client.values[0].append("s3")
    ctx = replace(case.ctx, steps=("s1", "s2"), status="failed", cells={"T1": {"s1": "done", "s2": "failed"}})
    result = case.engine.after(ctx, case.engine.before(ctx))
    assert result.changes == {"'Targets'!B2": "done"}


@pytest.mark.parametrize(("fmt", "value", "expected"), [
    ("raw", 0, "0"), ("raw", None, ""), ("raw", float("nan"), ""), ("bytes", 1048576, "1 MiB"),
    ("seconds", 12.5, "12.5s"), ("duration", 3661, "01:01:01"),
    ("datetime", "2026-10-09T12:00:00Z", "2026-10-09T12:00:00+00:00"),
    ("percent", 0.125, "12.5%"), ("json", {"a": [1]}, '{"a":[1]}'),
])
def test_formats(gsheet, fmt, value, expected):
    assert gsheet.mapping.format_value(value, fmt) == expected


def test_template_and_usage_aliases(case, gsheet):
    ctx = replace(case.ctx, duration_s=12.3, usage={"cpu_s": 2.5}, log_path=str(case.root / "step.log"))
    rule = gsheet.mapping.Outbound("s1", "s1", "template", template="{status} ({duration_s:.0f}s) {usage.cpu}")
    assert gsheet.mapping.value(ctx, ctx.rows[0], "s1", rule) == "done (12s) 2.5"
    assert gsheet.mapping.resolve(ctx, ctx.rows[0], "s1", "log_path_rel") == "step.log"
    assert gsheet.mapping.resolve(ctx, ctx.rows[0], "s1", "results.missing") is None
    assert gsheet.mapping.resolve(replace(ctx, cells={}), ctx.rows[0], "s1", "status") == "done"


@pytest.mark.parametrize(("change", "message"), [
    ({"field": "unknown"}, "unknown field"), ({"field": "usage.nope"}, "unknown field"),
    ({"step": "nope"}, "unknown step"), ({"column": "no such header"}, "unknown sheet column"),
    ({"format": "bad"}, "format must be"), ({"when": ["banana"]}, "when must be"),
    ({"column": "TARGET_NAME"}, "row key column"),
])
def test_bad_mapping_recorded_without_write(case, gsheet, change, message):
    case.config["outbound"][0].update(change)
    case.save()
    # Include the unknown step in this launched context so validation is reached.
    ctx = replace(case.ctx, steps=("s1", "nope"))
    with pytest.raises(gsheet.client.SyncError, match=message):
        case.engine.before(ctx)
    assert not case.client.writes
    assert history(case)[0]["result"] == "error"
    assert ctx.status == "done"


def test_duplicates_after_wildcard_and_header_resolution(case, gsheet):
    case.config["outbound"].append({"step": "*", "column": "{step}", "field": "exit_code"})
    case.save()
    with pytest.raises(gsheet.client.SyncError, match="duplicate"):
        case.engine.before(case.ctx)
    assert case.client.calls == []
    case.config["outbound"] = [{"step": "s1", "column": "B", "field": "status"},
                               {"step": "s1", "column": " S1 ", "field": "status"}]
    case.save()
    with pytest.raises(gsheet.client.SyncError, match="duplicate"):
        case.engine.before(case.ctx)
    assert len(case.client.calls) == 1


@pytest.mark.parametrize(("policy", "written"), [("overwrite", 2), ("skip", 1), ("fail-soft", 0)])
def test_conflict_policies(case, gsheet, policy, written):
    case.engine.settings["conflict_policy"] = policy
    case.config["outbound"].append({"step": "s1", "column": "s1 RAM", "field": "unit_id"})
    case.save()
    state = case.engine.before(case.ctx)
    case.client.live["'Targets'!B2"] = "human edit"
    if policy == "fail-soft":
        with pytest.raises(gsheet.client.SyncError, match="cells changed"):
            case.engine.after(case.ctx, state)
        assert len(history(case)) == 1
    else:
        result = case.engine.after(case.ctx, state)
        assert result.conflicts == ["'Targets'!B2"]
        assert result.cells_written == written
    assert sum(len(data) for _, data, _ in case.client.writes) == written


def test_all_conflicts_no_write(case):
    state = case.engine.before(case.ctx)
    case.client.live["'Targets'!B2"] = "human edit"
    result = case.engine.after(case.ctx, state)
    assert result.result == "skipped (conflict)"
    assert not case.client.writes


def test_verify_disabled_and_value_input_option(case):
    case.config["verify_before_write"] = False
    case.save()
    case.engine.settings["value_input_option"] = "USER_ENTERED"
    case.engine.after(case.ctx, case.engine.before(case.ctx))
    assert [c[0] for c in case.client.calls] == ["get", "batch_update"]
    assert case.client.writes[0][2] == "USER_ENTERED"


def test_dry_run_never_writes_sheet_or_plan_csv(case):
    case.engine.settings["dry_run"] = True
    case.config["inbound"] = [{"column": "notes", "to": "plan_column", "plan_column": "notes"}]
    case.save()
    original = case.access.path.read_bytes()
    result = case.engine.after(case.ctx, case.engine.before(case.ctx))
    assert result.result == "dry run"
    assert result.changes == {"'Targets'!B2": "done"}
    assert case.access.path.read_bytes() == original
    assert [c[0] for c in case.client.calls] == ["get"]


def test_inbound_respects_running_and_never_ctx_steps(case, monkeypatch):
    case.config["inbound"] = [{"column": "s1", "to": "plan_cell", "step": "s1"},
                              {"column": "s2", "to": "plan_cell", "step": "s2"},
                              {"column": "s1 RAM", "to": "plan_cell", "step": "s3"},
                              {"column": "notes", "to": "plan_column", "plan_column": "notes"}]
    case.save()
    case.client.values[1][1:4] = ["skip", "skip", "skip"]
    original_update = plan_csv.update
    updates = []

    def update(*args, **kwargs):
        updates.append((args, kwargs))
        # Simulate a cell becoming running between the first read and locked update.
        case.access.path.write_text(case.access.path.read_text().replace("running,todo,running", "running,running,running"))
        return original_update(*args, **kwargs)

    monkeypatch.setattr(plan_csv, "update", update)
    case.engine.before(case.ctx)
    table = plan_csv.read(case.access.path, case.access.steps, **case.access.columns)
    assert table.rows[0].values["notes"] == "new"
    assert [table.rows[0].cell(s) for s in case.access.steps] == ["running"] * 3
    args, kwargs = updates[0]
    assert ("T1", "s1") not in args[3]
    assert "running" not in kwargs["only_if"]["T1", "s2"]
    assert args[1] == case.access.lock
    assert kwargs["lock_timeout"] > 0


def test_inbound_todo_skip_only(case):
    case.config["inbound"] = [{"column": "s2", "to": "plan_cell", "step": "s2"}]
    case.save()
    for value, expected in [("done", "todo"), ("skip", "skip"), ("todo", "todo")]:
        case.client.values[1][2] = value
        case.engine.before(case.ctx)
        table = plan_csv.read(case.access.path, case.access.steps, **case.access.columns)
        assert table.rows[0].cell("s2") == expected


@pytest.mark.parametrize("destination", ["s2", "TARGET_NAME", "WORKDIR"])
def test_inbound_extra_column_cannot_change_steps_or_identity(case, gsheet, destination):
    case.config["inbound"] = [{"column": "notes", "to": "plan_column", "plan_column": destination}]
    case.save()
    original = case.access.path.read_bytes()
    with pytest.raises(gsheet.client.SyncError):
        case.engine.before(case.ctx)
    assert case.access.path.read_bytes() == original


def test_missing_row_skip_and_append(case):
    case.client.values = [case.client.values[0], ["other", "", "", "", ""]]
    result = case.engine.after(case.ctx, case.engine.before(case.ctx))
    assert result.result == "no changes"
    case.config["rows"]["missing_row"] = "append"
    case.save()
    state = case.engine.before(case.ctx)
    # Another launched unit appended while this one ran.
    case.client.values.append(["new target"])
    result = case.engine.after(case.ctx, state)
    assert result.changes == {"'Targets'!A4": "T1", "'Targets'!B4": "done"}
    assert len(case.client.writes) == 1
    assert "T1" not in state.row_numbers  # reusable snapshot


def test_append_uses_row_already_added_during_step(case):
    case.config["rows"]["missing_row"] = "append"
    case.save()
    case.client.values = [case.client.values[0]]
    state = case.engine.before(case.ctx)
    case.client.values.append(["T1"])
    case.client.live["'Targets'!A2"] = "T1"
    result = case.engine.after(case.ctx, state)
    assert result.changes == {"'Targets'!B2": "done"}


def test_literal_column_beyond_trimmed_headers(case):
    case.config["outbound"][0]["column"] = "AA"
    case.save()
    result = case.engine.after(case.ctx, case.engine.before(case.ctx))
    assert result.changes == {"'Targets'!AA2": "done"}


def test_partial_range_and_header_row_offsets(case):
    case.config.update(read_range="B3:F", header_row=3, worksheet="It's")
    case.save()
    result = case.engine.after(case.ctx, case.engine.before(case.ctx))
    assert case.client.calls[0][2] == "'It''s'!B3:F"
    assert result.changes == {"'It''s'!C4": "done"}


def test_gid_and_mapping_changes_apply_next_unit(case):
    case.config.pop("worksheet")
    case.config["gid"] = 0
    case.save()
    state = case.engine.before(case.ctx)
    case.config["outbound"][0]["column"] = "s2"
    case.save()
    result = case.engine.after(case.ctx, state)
    assert result.changes == {"'Targets'!B2": "done"}
    result = case.engine.after(case.ctx, case.engine.before(case.ctx))
    assert result.changes == {"'Targets'!C2": "done"}


def test_unsafe_errors_and_values_never_in_history_or_exception(case, gsheet):
    secret = "FAKE-SERVICE-ACCOUNT-KEY"
    case.engine.settings["credentials_json"] = json.dumps({"private_key": secret})
    case.client.error = RuntimeError("leaked " + secret)
    with pytest.raises(gsheet.client.SyncError) as caught:
        case.engine.before(case.ctx)
    assert secret not in str(caught.value)
    case.client.error = None
    case.config["outbound"][0]["field"] = "error"
    case.save()
    ctx = replace(case.ctx, error=secret)
    case.engine.after(ctx, case.engine.before(ctx))
    assert secret not in (case.root / ".alfrd/gsheet/sync.jsonl").read_text()
    case.client.error = gsheet.client.SyncError("auth failed: " + secret)
    with pytest.raises(gsheet.client.SyncError) as caught:
        case.engine.before(case.ctx)
    assert secret not in str(caught.value)
    assert secret not in (case.root / ".alfrd/gsheet/sync.jsonl").read_text()


def test_timeout_error_is_recorded(case, gsheet):
    case.client.error = TimeoutError("a sensitive HTTP request")
    with pytest.raises(gsheet.client.SyncError, match="TimeoutError"):
        case.engine.before(case.ctx)
    assert "sensitive" not in history(case)[0]["error"]


def test_bounded_spreadsheet_lock(case, gsheet):
    deadline = time.monotonic() + 2
    path = case.root / "lock"
    with (gsheet.sync.locked(path, deadline), pytest.raises(gsheet.client.SyncError, match="lock timed out"),
          gsheet.sync.locked(path, time.monotonic() + 0.02)):
        pytest.fail("lock should be held")


def test_plan_lock_timeout_leaves_csv_untouched(case):
    if plan_csv.fcntl is None:
        pytest.skip("requires POSIX flock")
    original = case.access.path.read_bytes()
    with plan_csv.locked(case.access.lock), pytest.raises(TimeoutError, match="plan CSV lock"):
        plan_csv.update(case.access.path, case.access.lock, case.access.steps, {("T1", "s2"): "skip"},
                        lock_timeout=0.02, **case.access.columns)
    assert case.access.path.read_bytes() == original


def test_per_spreadsheet_lock_serializes_writes(case):
    state = case.engine.before(case.ctx)
    active = 0
    maximum = 0
    original = case.client.batch_update

    def slow_write(*args, **kwargs):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        time.sleep(0.02)
        original(*args, **kwargs)
        active -= 1

    case.client.batch_update = slow_write
    case.engine.settings["conflict_policy"] = "overwrite"
    results = []
    threads = [threading.Thread(target=lambda: results.append(case.engine.after(case.ctx, state))) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(results) == 2
    assert maximum == 1
