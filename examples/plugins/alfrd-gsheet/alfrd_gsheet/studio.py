"""Studio summary: only the mapping and local history, never settings, plans or Google.

Global defaults and dry-run changes cannot be observed under this file-only contract.
The last recorded sync supplies them; the UI labels this limitation explicitly.
"""

from __future__ import annotations

import json
import shlex
from datetime import datetime
from pathlib import Path

from . import mapping
from .project import HISTORY, MAPPING, validate_summary

HISTORY_BYTES = 1024 * 1024
_UNRESOLVED = "unresolved_default_sheet_id"
RESULTS = {"ok", "no changes", "dry run", "skipped (conflict)", "error"}


def auto_panel(root):
    """Cheap automatic panel discovery, including disabled or invalid mappings."""
    if (Path(root) / MAPPING).is_file():
        return {"title": "Google Sheet", "scope": "project"}
    return None


def _history(path: Path) -> tuple[list[dict], bool]:
    if not path.exists():
        return [], False
    with path.open("rb") as stream:
        size = stream.seek(0, 2)
        stream.seek(max(0, size - HISTORY_BYTES))
        if size > HISTORY_BYTES:
            stream.readline()  # discard a potentially partial JSON record
        lines = stream.read().splitlines()
    records = []
    for line in lines:
        try:
            record = json.loads(line)
            if not isinstance(record, dict) or record.get("result") not in RESULTS:
                continue
            stamp = datetime.fromisoformat(record["at"].replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                continue
            steps = record.get("steps")
            if not isinstance(steps, list) or not all(isinstance(s, str) for s in steps):
                continue
            records.append((stamp.timestamp(), record))
        except (ValueError, TypeError, KeyError, UnicodeError, OverflowError):
            continue  # tolerate interrupted appends and malformed old records
    records.reverse()  # latest append wins when timestamps tie
    return [r for _, r in sorted(records, key=lambda pair: pair[0], reverse=True)], size > HISTORY_BYTES


def evaluate(root, panel, values, spec):
    """Project-wide panel; ignores target scope. Returns only public summary fields."""
    root = Path(root)
    try:
        records, truncated = _history(root / HISTORY)
        latest = records[0] if records else {}
        inherited = next((r for r in records if r.get("spreadsheet_id")), {})
        config = mapping.load(root, {"default_spreadsheet_id": inherited.get("spreadsheet_id") or _UNRESOLVED})
        # No workflow file reads: validate intrinsic rules with the steps declared by this mapping.
        if config is not None:
            steps = tuple(dict.fromkeys(r.step for r in config.outbound if r.step != "*"))
            steps += tuple(r.destination for r in config.inbound if r.to == "plan_cell")
            if any(r.step == "*" for r in config.outbound):
                steps += ("__step__",)
            mapping.validate(config, steps)
        state = "off" if config is None else "dry run" if latest.get("result") == "dry run" else "syncing"
        model = {"state": state, "rows": [], "more_rows": [], "total": 0, "history_truncated": truncated,
                 "note": "State reflects the mapping and last recorded sync; validate to check current settings."}
        if config is None:
            missing = not (root / MAPPING).exists()
            model.update(label=f"Off — no {MAPPING}" if missing else "Off — disabled in mapping",
                         empty="Set up sync to send finished steps to your Google Sheet." if missing
                         else "Sync is disabled. Set enabled: true in alfrd.gsheet.yaml to resume.")
            if missing:
                model["command"] = f"alfrd gsheet init {shlex.quote(str(root))} --spreadsheet <id>"
        else:
            model["label"] = "Dry run" if state == "dry run" else "Syncing"
            sid = config.spreadsheet_id
            if sid != _UNRESOLVED:
                gid = f"#gid={config.worksheet}" if isinstance(config.worksheet, int) else ""
                model["sheet"] = {"id": sid, "worksheet": str(config.worksheet),
                                  "url": f"https://docs.google.com/spreadsheets/d/{sid}/edit{gid}"}
            else:
                model["sheet_note"] = "Using the default spreadsheet from Settings; its link appears after the first sync."
            if not records:
                model["empty"] = "No step has finished since sync was set up."
        rows, seen = [], set()
        for record in records:
            for step in record["steps"]:
                if step in seen:
                    continue
                seen.add(step)
                count = record.get("cells_written", 0)
                conflicts = record.get("conflicts")
                rows.append({"step": step, "at": record["at"],
                             "cells_written": count if type(count) is int and count >= 0 else 0,
                             "conflicts": len(conflicts) if isinstance(conflicts, list) else 0,
                             "result": record["result"],
                             # Never replay arbitrary error text/values into the browser.
                             "error": "Sync failed. Validate the mapping to check the connection." if record["result"] == "error" else ""})
        model.update(rows=rows[:10], more_rows=rows[10:], total=len(rows))
        return model
    except mapping.MappingError:
        return {"state": "invalid", "label": "Mapping invalid",
                "error": "Mapping invalid. Run Google Sheet: validate mapping for details."}
    except Exception as exc:  # noqa: BLE001 - panel errors must never contain file contents or secrets
        return {"error": f"Cannot read the Google Sheet summary ({type(exc).__name__})."}


def check_project(root, values):
    return validate_summary(Path(root), values=values)
