"""Load project mappings, validate destinations, and format public hook fields.

Mappings are independent of alfrd.yaml. Header validation uses the snapshot
already read by before(), so hook validation does not add a network request.
Dynamic results/agent_usage paths are accepted; usage names are checked against
the actual summary fields. Wildcards expand before checking duplicate cells.
"""

from __future__ import annotations

import json
import math
import re
import string
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from alfrd.extensions import StepContext

from . import a1

FORMATS = {"raw", "bytes", "seconds", "duration", "datetime", "percent", "json"}
STATUSES = {"todo", "running", "done", "failed", "blocked", "cancelled", "interrupted", "skip"}
FIELDS = {"status", "exit_code", "error", "started", "finished", "duration_s", "log_path", "log_path_rel",
          "total_cost_usd", "model", "outcome", "unit_id", "plan_id", "target", "code"}
USAGE = {"wall_s", "cpu_s", "avg_cores", "peak_mem", "peak_mem_kind", "peak_rss", "max_rss", "read_bytes",
         "write_bytes", "samples", "interval_s", "limited", "counted", "cgroup_memory_peak", "workdir",
         "workdir_start", "workdir_end"}
USAGE_ALIASES = {"cpu": "cpu_s", "cores": "avg_cores", "wall": "wall_s", "io_read": "read_bytes", "io_write": "write_bytes"}
VALID_FIELDS = ", ".join(sorted(FIELDS)) + ", usage.<summary key>, agent_usage.<key>, results.<key>, template"


class MappingError(ValueError):
    """Mapping is unsafe or cannot be resolved; sync for this unit is disabled."""


@dataclass(frozen=True)
class Outbound:
    step: str
    column: str
    field: str
    format: str = "raw"
    when: tuple[str, ...] = ()
    template: str = ""


@dataclass(frozen=True)
class Inbound:
    column: str
    to: str
    destination: str


@dataclass(frozen=True)
class MappingConfig:
    spreadsheet_id: str
    worksheet: str | int
    key_column: str
    code_column: str = ""
    header_row: int = 1
    missing_row: str = "skip"
    read_range: str | None = None
    outbound: tuple[Outbound, ...] = ()
    inbound: tuple[Inbound, ...] = ()
    verify_before_write: bool = True

    def applies(self, steps: Sequence[str]) -> bool:
        return bool(self.inbound or any(r.step == "*" or r.step in steps for r in self.outbound))


def spreadsheet_id(value: Any) -> str:
    text = str(value or "").strip()
    match = re.fullmatch(r"https://docs\.google\.com/spreadsheets/d/([A-Za-z0-9_-]{20,})(?:[/?#].*)?", text)
    if match:
        text = match[1]
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,}", text):
        raise MappingError("spreadsheet_id must be a Google Sheet id or URL")
    return text


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MappingError(f"{name} must be a non-empty string")
    return value.strip()


def _keys(data: Mapping, allowed: set[str], where: str) -> None:
    if set(data) - allowed:
        raise MappingError(f"unknown setting in {where}")


def _rules(data: dict, name: str) -> list[dict]:
    rules = data.get(name, [])
    if not isinstance(rules, list) or any(not isinstance(r, dict) for r in rules):
        raise MappingError(f"{name} must be a list of rules")
    return rules


def check_field(field: str) -> None:
    if field in FIELDS:
        return
    root, dot, tail = field.partition(".")
    if dot and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", tail):
        if root in {"agent_usage", "results"}:
            return
        if root == "usage" and (tail in USAGE or tail in USAGE_ALIASES):
            return
    raise MappingError(f"unknown field {field!r}; valid fields: {VALID_FIELDS}")


def _template(text: str) -> None:
    try:
        parts = list(string.Formatter().parse(text))
    except ValueError:
        raise MappingError("template has invalid braces") from None
    for _, field, spec, conversion in parts:
        if field is not None:
            check_field(field)
            if "{" in spec or conversion not in (None, "s", "r"):
                raise MappingError("template must use plain fields and format specifications")


def load(project: str | Path, defaults: Mapping[str, Any] | None = None) -> MappingConfig | None:
    path = Path(project) / "alfrd.gsheet.yaml"
    if not path.exists():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError, UnicodeError):
        raise MappingError("cannot read alfrd.gsheet.yaml: expected valid UTF-8 YAML") from None
    return parse(data, defaults)


def parse(data: Any, defaults: Mapping[str, Any] | None = None) -> MappingConfig | None:
    """Parse a file or Studio draft through the same mapping contract."""
    if not isinstance(data, dict):
        raise MappingError("alfrd.gsheet.yaml must be a mapping")
    _keys(data, {"version", "enabled", "spreadsheet_id", "worksheet", "gid", "header_row", "rows", "read_range",
                 "outbound", "inbound", "verify_before_write"}, "mapping")
    if type(data.get("version")) is not int or data["version"] != 1:
        raise MappingError("mapping version must be 1")
    if type(data.get("enabled", True)) is not bool:
        raise MappingError("enabled must be true or false")
    if not data.get("enabled", True):
        return None
    sid = spreadsheet_id(data.get("spreadsheet_id") or (defaults or {}).get("default_spreadsheet_id"))
    if "worksheet" in data and "gid" in data:
        raise MappingError("choose worksheet or gid, not both")
    worksheet = data.get("worksheet", data.get("gid"))
    if isinstance(worksheet, int) and not isinstance(worksheet, bool):
        if worksheet < 0:
            raise MappingError("worksheet gid must be non-negative")
    else:
        worksheet = _text(worksheet, "worksheet")
    header = data.get("header_row", 1)
    if type(header) is not int or header < 1:
        raise MappingError("header_row must be a positive integer")
    rows = data.get("rows")
    if not isinstance(rows, dict):
        raise MappingError("rows.key_column is required")
    _keys(rows, {"key_column", "code_column", "missing_row"}, "rows")
    key = _text(rows.get("key_column"), "rows.key_column")
    code = _text(rows["code_column"], "rows.code_column") if "code_column" in rows else ""
    missing = rows.get("missing_row", "skip")
    if missing not in ("skip", "append"):
        raise MappingError("rows.missing_row must be skip or append")
    read_range = data.get("read_range")
    try:
        first_row, _ = a1.origin(read_range)
    except (ValueError, TypeError):
        raise MappingError("read_range must be an A1 range without a worksheet title") from None
    if first_row > header:
        raise MappingError("read_range must include header_row")
    if type(data.get("verify_before_write", True)) is not bool:
        raise MappingError("verify_before_write must be true or false")
    outbound = []
    for rule in _rules(data, "outbound"):
        _keys(rule, {"step", "column", "field", "format", "when", "template"}, "outbound rule")
        step, col, field = (_text(rule.get(k), f"outbound.{k}") for k in ("step", "column", "field"))
        fmt = rule.get("format", "raw")
        if not isinstance(fmt, str) or fmt not in FORMATS:
            raise MappingError("format must be " + ", ".join(sorted(FORMATS)))
        when = rule.get("when", [])
        if not isinstance(when, list) or any(not isinstance(s, str) or s not in STATUSES for s in when):
            raise MappingError("when must be a list of plan statuses")
        template = ""
        if field == "template":
            template = _text(rule.get("template"), "outbound.template")
            _template(template)
        else:
            check_field(field)
            if "template" in rule:
                raise MappingError("template is only valid with field: template")
        if "{" in col.replace("{step}", "") or "}" in col.replace("{step}", ""):
            raise MappingError("column may only substitute {step}")
        outbound.append(Outbound(step, col, field, fmt, tuple(when), template))
    inbound = []
    for rule in _rules(data, "inbound"):
        _keys(rule, {"column", "to", "plan_column", "step"}, "inbound rule")
        col, to = _text(rule.get("column"), "inbound.column"), rule.get("to")
        if to not in ("plan_cell", "plan_column"):
            raise MappingError("inbound.to must be plan_column or plan_cell")
        destination = _text(rule.get("step" if to == "plan_cell" else "plan_column"), "inbound destination")
        if (to == "plan_cell" and "plan_column" in rule) or (to == "plan_column" and "step" in rule):
            raise MappingError("inbound rule has an incompatible destination")
        inbound.append(Inbound(col, to, destination))
    return MappingConfig(sid, worksheet, key, code, header, missing, read_range, tuple(outbound), tuple(inbound),
                         data.get("verify_before_write", True))


def validate(config: MappingConfig, steps: Sequence[str], headers: Sequence[str] | None = None, *, first_col: int = 1) -> None:
    def column(name: str) -> int | str:
        if headers is None:
            return name.strip().casefold()
        try:
            col = a1.column(headers, name, first_col=first_col)
        except ValueError as exc:
            raise MappingError(str(exc)) from None
        if not first_col <= col < first_col + len(headers):
            raise MappingError(f"sheet column {name!r} is outside read_range")
        return col

    key_cols = {column(config.key_column)}
    if config.code_column:
        code = column(config.code_column)
        if code in key_cols:
            raise MappingError("key_column and code_column must be different")
        key_cols.add(code)
    seen = set()
    for rule in config.outbound:
        if rule.step != "*" and rule.step not in steps:
            raise MappingError(f"unknown step {rule.step!r}")
        for step in steps if rule.step == "*" else [rule.step]:
            col = column(rule.column.replace("{step}", step))
            if col in key_cols:
                raise MappingError("outbound cannot overwrite a row key column")
            if (step, col) in seen:
                raise MappingError(f"duplicate outbound destination for step {step!r}")
            seen.add((step, col))
    inbound_seen = set()
    for rule in config.inbound:
        column(rule.column)
        if rule.to == "plan_cell" and rule.destination not in steps:
            raise MappingError(f"unknown inbound step {rule.destination!r}")
        if rule.to == "plan_column" and rule.destination.casefold() in {s.casefold() for s in steps}:
            raise MappingError("inbound plan_column cannot write a workflow step; use plan_cell")
        dest = rule.to, rule.destination.casefold()
        if dest in inbound_seen:
            raise MappingError("duplicate inbound destination")
        inbound_seen.add(dest)


def problems(config: MappingConfig, steps: Sequence[str], headers: Sequence[str] | None = None, *,
             first_col: int = 1) -> list[str]:
    """Distinct validation messages for the CLI/toast, using the same checks as hooks.

    Check each rule as well as the entire mapping, so unrelated bad columns/steps
    are counted while cross-rule duplicates still get reported.
    """
    candidates = [config, replace(config, outbound=(), inbound=())]
    candidates += [replace(config, outbound=(rule,), inbound=()) for rule in config.outbound]
    candidates += [replace(config, outbound=(), inbound=(rule,)) for rule in config.inbound]
    found = []
    for candidate in candidates:
        try:
            validate(candidate, steps, headers, first_col=first_col)
        except MappingError as exc:
            message = str(exc)
            if message not in found:
                found.append(message)
    return found


def resolve(ctx: StepContext, row: Mapping[str, str], step: str, field: str) -> Any:
    check_field(field)
    if field == "status":
        return ctx.cells.get(row["key"], {}).get(step, ctx.status)
    if field in ("target", "code"):
        return row.get(field, "")
    if field == "log_path_rel":
        if not ctx.log_path:
            return ""
        try:
            return str(Path(ctx.log_path).relative_to(ctx.project_root))
        except ValueError:
            return ctx.log_path
    root, dot, tail = field.partition(".")
    value = getattr(ctx, root, None)
    if dot:
        if root == "usage":
            tail = USAGE_ALIASES.get(tail, tail)
        for part in tail.split("."):
            value = value.get(part) if isinstance(value, Mapping) else None
    return value


def text(value: Any) -> str:
    if value is None or isinstance(value, float) and math.isnan(value):
        return ""
    return str(value)


def format_value(value: Any, fmt: str) -> str:
    if value is None or isinstance(value, float) and math.isnan(value):
        return ""
    try:
        if fmt == "raw":
            return text(value)
        if fmt == "json":
            return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if fmt == "datetime":
            dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return dt.isoformat()
        number = float(value)
        if not math.isfinite(number):
            raise ValueError
        if fmt == "seconds":
            return f"{number:g}s"
        if fmt == "duration":
            sign, total = ("-" if number < 0 else ""), int(abs(number))
            hours, remainder = divmod(total, 3600)
            minutes, seconds = divmod(remainder, 60)
            return f"{sign}{hours:02}:{minutes:02}:{seconds:02}"
        if fmt == "percent":
            return f"{number * 100:g}%"
        if fmt == "bytes":
            for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
                if abs(number) < 1024 or unit == "PiB":
                    return f"{number:g} {unit}"
                number /= 1024
    except (ValueError, TypeError, OverflowError):
        raise MappingError(f"cannot format mapped value as {fmt}") from None
    raise MappingError("unknown format")


def value(ctx: StepContext, row: Mapping[str, str], step: str, rule: Outbound) -> str:
    if rule.field != "template":
        return format_value(resolve(ctx, row, step, rule.field), rule.format)
    out = []
    for literal, field, spec, conversion in string.Formatter().parse(rule.template):
        out.append(literal)
        if field is not None:
            raw = resolve(ctx, row, step, field)
            raw = "" if raw is None else raw
            if conversion:
                raw = str(raw) if conversion == "s" else repr(raw)
            try:
                out.append(format(raw, spec))
            except (ValueError, TypeError):
                raise MappingError("template format does not match the mapped value") from None
    return format_value("".join(out), rule.format)
