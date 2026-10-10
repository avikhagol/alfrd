"""Studio project actions; credentials stay server-side and writes use revision locks.

Drafts use the same parser and sync engine as the CLI. Google failures are reduced
to a safe connection message; arbitrary transport bodies are never returned.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import yaml

from alfrd.extensions import ProjectAction
from alfrd.yaml_text import dump

from . import a1, columns, mapping, project, settings, studio

ORDER = ("version", "enabled", "spreadsheet_id", "worksheet", "gid", "header_row", "rows",
         "read_range", "verify_before_write", "outbound", "inbound")
COMMENTS_NOTICE = "Saving rewrites the file; comments are removed."
TIMEOUT = 25
BACKFILL_TIMEOUT = 290


def _read(root: Path) -> tuple[dict | None, str]:
    """Read bytes once so the draft and its revision always describe the same file."""
    import hashlib

    try:
        raw = (root / project.MAPPING).read_bytes()
    except FileNotFoundError:
        return None, ""
    sha = hashlib.sha256(raw).hexdigest()
    try:
        data = yaml.safe_load(raw.decode("utf-8"))
    except (yaml.YAMLError, UnicodeError):
        raise mapping.MappingError("cannot read alfrd.gsheet.yaml: expected valid UTF-8 YAML") from None
    if not isinstance(data, dict):
        raise mapping.MappingError("alfrd.gsheet.yaml must be a mapping")
    return data, sha


def _ordered(data: dict) -> dict:
    return {k: data[k] for k in dict.fromkeys([*ORDER, *data]) if k in data}


def _revision(payload: dict) -> str:
    sha = payload.get("base_sha256")
    if not isinstance(sha, str) or (sha and not re.fullmatch(r"[0-9a-f]{64}", sha)):
        raise mapping.MappingError("base_sha256 must be the loaded mapping revision")
    return sha


def _sid(values: dict, payload: dict) -> str:
    return mapping.spreadsheet_id(payload.get("spreadsheet") or values.get("default_spreadsheet_id"))


def _sheet(client: Any, sid: str, payload: dict, deadline: float) -> str:
    if "gid" in payload and payload.get("worksheet") is not None:
        raise mapping.MappingError("choose worksheet or gid, not both")
    if "gid" in payload:
        gid = payload["gid"]
        if type(gid) is not int or gid < 0:
            raise mapping.MappingError("gid must be a non-negative integer")
        return client.worksheet_title(sid, gid, deadline=deadline)
    title = payload.get("worksheet")
    if not isinstance(title, str) or not title.strip():
        raise mapping.MappingError("worksheet must be a non-empty string")
    return title


def _header_row(payload: dict) -> int:
    row = payload.get("header_row", 1)
    if type(row) is not int or row < 1:
        raise mapping.MappingError("header_row must be a positive integer")
    return row


def _path(message: str, prefix: str = "") -> str:
    """Point parser/validator messages at editable controls without exposing YAML."""
    if prefix:
        for field in ("column", "step", "field", "format", "when", "template", "to", "plan_column"):
            if field in message or (field == "column" and "destination" in message):
                return f"{prefix}.{field}"
        return prefix
    for field in ("code_column", "key_column", "missing_row", "match_against"):
        if field in message:
            return f"rows.{field}"
    for field in ("read_range", "header_row",
                  "verify_before_write", "spreadsheet_id", "worksheet", "version", "enabled", "outbound", "inbound"):
        if field in message:
            return field
    return "mapping"


def _errors(data: Any, values: dict, steps: tuple, header=None, first_col=1) -> tuple[Any, list[dict]]:
    """Collect independent rule errors and duplicate destinations through the shared checks."""
    errors = []

    def add(exc, prefix=""):
        message = " ".join(str(exc).split())[:800]
        item = {"path": _path(message, prefix), "message": message}
        if item not in errors:
            errors.append(item)

    if not isinstance(data, dict):
        return None, [{"path": "mapping", "message": "mapping must be an object"}]
    # Disabled drafts still need to be valid so the editor can safely re-enable them.
    checked = {**data, "enabled": True}
    if type(data.get("enabled", True)) is not bool:
        errors.append({"path": "enabled", "message": "enabled must be true or false"})
    base = {**checked, "outbound": [], "inbound": []}
    config = None
    try:
        config = mapping.parse(base, values)
        mapping.validate(config, steps, header, first_col=first_col)
    except mapping.MappingError as exc:
        add(exc)
        if config is not None and header is not None and errors[-1]["path"] == "mapping":
            for name, column in (("key_column", config.key_column), ("code_column", config.code_column)):
                if not column:
                    continue
                try:
                    a1.column(header, column, first_col=first_col)
                except ValueError:
                    errors[-1]["path"] = f"rows.{name}"
                    break
        return None, errors
    for kind in ("outbound", "inbound"):
        rules = checked.get(kind, [])
        if not isinstance(rules, list) or any(not isinstance(r, dict) for r in rules):
            errors.append({"path": kind, "message": f"{kind} must be a list of rules"})
            continue
        accepted = []
        for i, rule in enumerate(rules):
            try:
                candidate = mapping.parse({**base, kind: [rule]}, values)
                mapping.validate(candidate, steps, header, first_col=first_col)
                # A cumulative check assigns duplicates to the later conflicting row.
                candidate = mapping.parse({**base, kind: [*accepted, rule]}, values)
                mapping.validate(candidate, steps, header, first_col=first_col)
                accepted.append(rule)
            except mapping.MappingError as exc:
                add(exc, f"{kind}[{i}]")
    try:
        return mapping.parse(checked, values), errors
    except mapping.MappingError:
        return None, errors


def _validation(root: Path, values: dict, data: Any, *, live: bool) -> dict:
    steps = tuple(project._execution(root).step_ids)
    config, errors = _errors(data, values, steps)
    result = {"errors": errors, "warnings": []}
    if config is None or not live:
        return result
    try:
        state = settings.make_engine(TIMEOUT, values=values).snapshot(config, steps, validate_rules=False, project_root=root)
    except mapping.MappingError as exc:
        result["errors"].append({"path": _path(str(exc)), "message": str(exc)})
    except Exception:  # noqa: BLE001 - never expose credentials or Google response bodies
        result["warnings"].append("The sheet could not be checked. Check the connection in Settings → Plugins → Google Sheet.")
    else:
        missing = mapping.missing_columns(config, steps, state.header, first_col=state.first_col)
        if missing:
            result["missing_columns"] = missing
        _, live_errors = _errors(data, values, steps, state.header, state.first_col)
        result["errors"] += [e for e in live_errors if e not in result["errors"]]
    return result


def state(root: Path, values: dict, payload: dict) -> dict:
    read_errors = []
    try:
        data, sha = _read(root)
    except mapping.MappingError as exc:
        data, sha = None, project.mapping_sha(root)
        read_errors = [{"path": "mapping", "message": str(exc)}]
    steps = tuple(project._execution(root).step_ids)
    errors = _errors(data, values, steps)[1] if data is not None else read_errors
    records, _ = studio._history(root / project.HISTORY)
    latest = records[0] if records else {}
    fields = [{"id": name, "label": name.replace("_", " ").capitalize(), "takes_key": False}
              for name in sorted(mapping.FIELDS)]
    fields += [{"id": name, "label": label, "takes_key": True, "keys": keys}
               for name, label, keys in (("usage.*", "Resource usage", sorted(mapping.USAGE)),
                                          ("agent_usage.*", "Agent usage", []), ("results.*", "Results", []))]
    fields.append({"id": "template", "label": "Template", "takes_key": False})
    return {"attached": bool(sha), "enabled": data.get("enabled", True) if data else False,
            "path": project.MAPPING, "mapping": _ordered(data) if data else None, "mapping_sha256": sha,
            "intrinsic_errors": errors, "steps": list(steps), "fields": fields,
            "formats": sorted(mapping.FORMATS), "statuses": sorted(mapping.STATUSES),
            "credentials": bool(values.get("credentials_json")),
            "default_spreadsheet": bool(values.get("default_spreadsheet_id")),
            "spreadsheet_id": (data or {}).get("spreadsheet_id") or values.get("default_spreadsheet_id") or "",
            "dry_run": bool(values.get("dry_run", False)), "comments_notice": COMMENTS_NOTICE,
            "last_sync": {k: latest[k] for k in ("at", "result", "cells_written", "steps") if k in latest}}


def sheet_info(root: Path, values: dict, payload: dict) -> dict:
    sid = _sid(values, payload)
    client = settings.make_engine(TIMEOUT, values=values).client
    metadata = client.metadata(sid, deadline=time.monotonic() + TIMEOUT)
    return {"spreadsheet_id": sid,
            "title": metadata.get("properties", {}).get("title", ""),
            "tabs": [{"title": s["properties"]["title"], "gid": s["properties"]["sheetId"]}
                     for s in metadata.get("sheets", [])]}


def headers(root: Path, values: dict, payload: dict) -> dict:
    sid, row = _sid(values, payload), _header_row(payload)
    client = settings.make_engine(TIMEOUT, values=values).client
    deadline = time.monotonic() + TIMEOUT
    title = _sheet(client, sid, payload, deadline)
    rows = client.get(sid, f"{a1.quote(title)}!{row}:{row + 20}", deadline=deadline)
    header = [mapping.text(v).strip() for v in (rows[0] if rows else [])]
    key = payload.get("key_column")
    if not key:
        aliases = (project._execution(root).key_column, "TARGET_NAME", "target", "source", "name", "FILENAMES")
        key = next((h for alias in aliases for h in header if h.casefold() == alias.casefold()), None)
    col = a1.column(header, key) - 1 if key else None
    sample = [mapping.text(r[col]) for r in rows[1:21] if col is not None and col < len(r)]
    from .row_match import Resolver

    mode = payload.get("match_against", "target")
    code_column = payload.get("code_column", "")
    code_col = a1.column(header, code_column) - 1 if code_column else None
    resolver = Resolver(project.plan_rows(root), mode, with_code=bool(code_column))
    matches = [resolver.sample(mapping.text(r[col]), mapping.text(r[code_col]) if code_col is not None and code_col < len(r) else "")
               for r in rows[1:21] if col is not None and col < len(r)]
    return {"matches": matches, "match_against": mode,
            "headers": [{"letter": a1.letters(i), "name": name} for i, name in enumerate(header, 1)],
            "sample_keys": sample, "key_column": key, "worksheet": title}


def validate(root: Path, values: dict, payload: dict) -> dict:
    data = payload["mapping"] if "mapping" in payload else _read(root)[0]
    if data is None:
        return {"errors": [], "warnings": ["Sync is off: no alfrd.gsheet.yaml."]}
    return _validation(root, values, data, live=True)


def save(root: Path, values: dict, payload: dict) -> dict:
    sha = _revision(payload)
    data = payload.get("mapping")
    validation = _validation(root, values, data, live=True)
    if validation["errors"]:
        return {"saved": False, **validation}
    new_sha = project.write_mapping(root, dump(_ordered(data)), base_sha256=sha, timeout=TIMEOUT)
    return {"saved": True, "mapping_sha256": new_sha, **validation}


def attach(root: Path, values: dict, payload: dict) -> dict:
    force = payload.get("force", False)
    if type(force) is not bool:
        raise mapping.MappingError("force must be true or false")
    # Refuse before making a network call; write_mapping rechecks inside the lock.
    if (root / project.MAPPING).exists() and not force:
        raise mapping.MappingError("A mapping already exists; confirm replacement first.")
    key = payload.get("key_column")
    code = payload.get("code_column", "")
    if not isinstance(key, str) or not key.strip():
        raise mapping.MappingError("key_column must be a non-empty string")
    if not isinstance(code, str):
        raise mapping.MappingError("code_column must be a string")
    sid = _sid(values, payload)
    client = settings.make_engine(TIMEOUT, values=values).client
    title = _sheet(client, sid, payload, time.monotonic() + TIMEOUT)
    result = project.init_project(root, spreadsheet=payload.get("spreadsheet"), worksheet=title,
                                  header_row=_header_row(payload), key_column=key, code_column=code,
                                  force=force, values=values, timeout=TIMEOUT, match_against=payload.get("match_against", "target"))
    return {"mapping": result.mapping, "mapping_sha256": result.mapping_sha256, "matched": list(result.matched)}


def detach(root: Path, values: dict, payload: dict) -> dict:
    sha = _revision(payload)
    mode = payload.get("mode")
    if mode not in ("disable", "delete"):
        raise mapping.MappingError("mode must be disable or delete")
    if mode == "delete":
        new_sha = project.write_mapping(root, None, base_sha256=sha, timeout=TIMEOUT)
        return {"mapping_sha256": new_sha, "attached": False, "enabled": False}
    data, current = _read(root)
    if sha != current:
        raise mapping.MappingError("The mapping changed on disk; reload.")
    text = dump(_ordered({**data, "enabled": False})) if mode == "disable" and data is not None else None
    new_sha = project.write_mapping(root, text, base_sha256=sha, timeout=TIMEOUT)
    return {"mapping_sha256": new_sha, "attached": mode == "disable" and data is not None, "enabled": False}


def _push(root: Path, values: dict, payload: dict, *, preview_only: bool) -> dict:
    for key in ("targets", "steps"):
        if key in payload and (not isinstance(payload[key], list) or any(not isinstance(v, str) for v in payload[key])):
            raise mapping.MappingError(f"{key} must be a list of strings")
    if "plan" in payload and not isinstance(payload["plan"], str):
        raise mapping.MappingError("plan must be a string")
    outcome = project.push_project(root, targets=payload.get("targets"), steps=payload.get("steps"),
                                   plan=payload.get("plan"), dry_run=preview_only, values=values,
                                   timeout=BACKFILL_TIMEOUT)
    result = outcome.result
    changes = result.changes if result else {}
    cells = [{"a1": address, **result.labels.get(address, {"target": "", "step": "", "column": ""}),
              "old": result.previous.get(address, ""), "new": value}
             for address, value in list(changes.items())[:500]]
    return {"cells": cells, "total_cells": len(changes), "truncated": len(changes) > 500,
            "conflicts": result.conflicts[:500] if result else [], "contexts": outcome.contexts,
            "result": result.result if result else "no matching results",
            "cells_written": result.cells_written if result else 0,
            "dry_run": preview_only or bool(values.get("dry_run", False))}


def preview(root: Path, values: dict, payload: dict) -> dict:
    return _push(root, values, payload, preview_only=True)


def backfill(root: Path, values: dict, payload: dict) -> dict:
    if payload.get("confirm") is not True:
        raise mapping.MappingError("Confirm the backfill before writing cells.")
    return _push(root, values, payload, preview_only=False)


def _redact(value: Any, values: dict) -> Any:
    """Remove configured credential material even if a cell/error contains it."""
    raw = values.get("credentials_json")
    secrets = [raw] if isinstance(raw, str) and raw else []
    try:
        parsed = json.loads(raw or "{}")
        if isinstance(parsed, dict):
            secrets += [v for v in parsed.values() if isinstance(v, str) and v]
    except (ValueError, TypeError):
        pass

    def clean(item):
        if isinstance(item, str):
            for secret in secrets:
                item = item.replace(secret, "<credentials>")
            return item
        if isinstance(item, dict):
            return {clean(k): clean(v) for k, v in item.items()}
        if isinstance(item, (list, tuple)):
            return [clean(v) for v in item]
        if item is None or isinstance(item, (bool, int, float)):
            return item
        return clean(mapping.text(item))  # malformed hand-edited YAML can contain dates/sets

    return clean(value)


def _guard(run):
    def guarded(root, values, payload):
        try:
            return _redact(run(Path(root).resolve(), values, payload), values)
        except mapping.MappingError as exc:
            raise ValueError(_redact(" ".join(str(exc).split())[:800], values)) from None
        except Exception as exc:  # noqa: BLE001 - transport/auth text is untrusted, including ValueError
            raise ValueError(f"Google Sheet action failed ({type(exc).__name__}). Check the mapping and connection settings.") from None
    return guarded


ACTIONS = tuple(ProjectAction(name, _guard(run), mutating=mutating, timeout=timeout)
                for name, run, mutating, timeout in (
                    ("state", state, False, 30), ("sheet_info", sheet_info, False, 30),
                    ("headers", headers, False, 30), ("validate", validate, False, 30),
                    ("save", save, True, 60), ("attach", attach, True, 60), ("detach", detach, True, 30),
                    ("column_preview", columns.preview, False, 30), ("create_columns", columns.create, True, 60),
                    ("preview", preview, False, 300), ("backfill", backfill, True, 300)))
