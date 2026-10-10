"""Explicit, reviewed column creation; no mapping save and no cell overwrite."""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import replace
from pathlib import Path

from . import a1, mapping, project, settings
from .sync import locked

TIMEOUT = 25


def _review(root, values, data, client, deadline):
    config = mapping.parse({**data, "enabled": True}, values)
    steps = project._execution(root).step_ids
    mapping.validate(config, steps)
    metadata = client.metadata(config.spreadsheet_id, deadline=deadline)
    props = next((s["properties"] for s in metadata.get("sheets", [])
                  if s["properties"].get("sheetId" if isinstance(config.worksheet, int) else "title") == config.worksheet), None)
    if props is None:
        raise mapping.MappingError("worksheet could not be found")
    grid = props.get("gridProperties", {})
    width, height = grid.get("columnCount"), grid.get("rowCount")
    if type(width) is not int or type(height) is not int or width < 1 or config.header_row > height:
        raise mapping.MappingError("sheet grid capacity could not be checked")
    title = props["title"]
    rows = client.get(config.spreadsheet_id, f"{a1.quote(title)}!{config.header_row}:{config.header_row}", deadline=deadline)
    header = [mapping.text(v) for v in (rows[0] if rows else [])]
    first_col = 1
    visible = header
    if config.read_range:
        _, first_col = a1.origin(config.read_range)
        end_col = a1.index("".join(c for c in config.read_range.split(":")[-1] if c.isalpha()))
        visible = header[first_col - 1:end_col]
    mapping.validate(replace(config, outbound=(), inbound=()), steps, visible, first_col=first_col)
    named = [h.strip().casefold() for h in header if h.strip()]
    if len(named) != len(set(named)):
        raise mapping.MappingError("correct ambiguous duplicate sheet headers before creating columns")
    names = mapping.missing_columns(config, steps, header)
    if width + len(names) > 18278:
        raise mapping.MappingError("new columns would exceed the sheet column limit")
    if config.read_range and names:
        _, first_col = a1.origin(config.read_range)
        end_col = a1.index("".join(c for c in config.read_range.split(":")[-1] if c.isalpha()))
        if not first_col <= width + 1 or width + len(names) > end_col:
            raise mapping.MappingError("new columns would be outside read_range; widen or clear it before reviewing")
    columns = [{"name": name, "letter": a1.letters(width + i)} for i, name in enumerate(names, 1)]
    checked = {"spreadsheet_id": config.spreadsheet_id, "sheet_id": props["sheetId"], "worksheet": title,
               "header_row": config.header_row, "start": width, "header": header, "columns": columns}
    sha = hashlib.sha256(json.dumps(checked, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return config, checked, sha


def preview(root: Path, values: dict, payload: dict) -> dict:
    data = payload.get("mapping")
    if not isinstance(data, dict):
        raise mapping.MappingError("mapping must be an object")
    client = settings.make_engine(TIMEOUT, values=values).client
    _, checked, sha = _review(root, values, data, client, time.monotonic() + TIMEOUT)
    return {"worksheet": checked["worksheet"], "header_row": checked["header_row"],
            "columns": checked["columns"], "preview_sha256": sha, "dry_run": bool(values.get("dry_run", False))}


def create(root: Path, values: dict, payload: dict) -> dict:
    if payload.get("confirm") is not True:
        raise mapping.MappingError("Confirm column creation before writing headers.")
    data = payload.get("mapping")
    if not isinstance(data, dict):
        raise mapping.MappingError("mapping must be an object")
    config = mapping.parse({**data, "enabled": True}, values)
    deadline = time.monotonic() + TIMEOUT
    client = settings.make_engine(TIMEOUT, values=values).client
    name = hashlib.sha256(config.spreadsheet_id.encode()).hexdigest()[:24]
    with locked(root / ".alfrd" / "locks" / f"gsheet-{name}.lock", deadline):
        config, checked, sha = _review(root, values, data, client, deadline)
        if payload.get("preview_sha256") != sha:
            raise mapping.MappingError("Sheet headers or destinations changed. Review column creation again.")
        if values.get("dry_run", False):
            return {"created": 0, "dry_run": True, "columns": checked["columns"]}
        names = [c["name"] for c in checked["columns"]]
        if names:
            client.create_columns(config.spreadsheet_id, sheet_id=checked["sheet_id"],
                                  header_row=config.header_row, start=checked["start"], names=names, deadline=deadline)
        # Read back after the write. If the response/read fails, a new review
        # reconciles already-created names and never replays the old write.
        rows = client.get(config.spreadsheet_id, f"{a1.quote(checked['worksheet'])}!{config.header_row}:{config.header_row}", deadline=deadline)
        header = [mapping.text(v) for v in (rows[0] if rows else [])]
        if any(name in mapping.missing_columns(config, project._execution(root).step_ids, header) for name in names):
            raise mapping.MappingError("Created headers could not be verified. Review column creation again.")
        return {"created": len(names), "dry_run": False,
                "headers": [{"letter": a1.letters(i), "name": name} for i, name in enumerate(header, 1)]}
