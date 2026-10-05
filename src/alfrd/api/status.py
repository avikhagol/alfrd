"""Plan run status as a stable, documented, read-only contract (``alfrd.plan_status/1``).

For Claude and any other harness: ``alfrd plan status --json`` / ``plan wait`` /
``plan events`` (no server needed) and ``GET /api/v1/projects/<p>/plans/…``
(``alfrd serve``) all call the functions here, so they never disagree.

Rules:

* **Reads never mutate.** Runner liveness is checked here (lock, pid, host,
  heartbeat age); a dead runner is reported as ``runner.alive: false,
  stale: true`` and nothing is fixed. Recovery only with ``reconcile=True``.
* **Stable contract.** ``schema`` is versioned; within v1 fields are only
  added. JSON Schema: ``alfrd/schemas/plan_status.v1.json``.
* **Cursor.** ``cursor`` changes whenever the plan does. ``since=<cursor>``
  returns ``changed: false`` (and little else) when nothing changed, else the
  document plus the ``events`` since that cursor.
* ``summary`` is one sentence generated from the same fields.
* Log text is bounded (200 lines / 64 KiB, ANSI stripped) and only logs that
  belong to the plan are read.

Times are UTC (``…Z``); durations are seconds.
"""

from __future__ import annotations

import hashlib
import json
import re
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from alfrd.entities import make as make_entity
from alfrd.execution import ExecutionError, load_execution
from alfrd.runtime import plan_csv as pc
from alfrd.runtime import scheduler as sch

SCHEMA = "alfrd.plan_status/1"
LIST_SCHEMA = "alfrd.plan_list/1"
EVENT_SCHEMA = "alfrd.plan_event/1"
LOG_SCHEMA = "alfrd.plan_log/1"
DETAILS = ("summary", "rows", "full")
TERMINAL = ("finished", "failed", "cancelled", "interrupted")
MAX_LOG_LINES = 200
MAX_LOG_BYTES = 64 * 1024
MAX_EVENTS = 500

#: ``alfrd plan status|wait`` exit codes (with and without --json).
EXIT_OK, EXIT_FAILED, EXIT_RUNNING, EXIT_STOPPED, EXIT_NOT_FOUND, EXIT_STALE = 0, 1, 2, 3, 4, 5

_ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[@-Z\\-_])")


class StatusNotFound(LookupError):
    """No such project or plan."""


def strip_ansi(text: str) -> str:
    """Same rule as ``logview.js`` ``stripAnsi``: CSI, OSC and two-byte escapes."""
    return _ANSI.sub("", text) if "\x1b" in text else text


def utc(value: Any) -> str | None:
    """A plan timestamp (local, naive ISO) as UTC ``YYYY-MM-DDTHH:MM:SSZ``."""
    stamp = sch._parse_stamp(value) if not isinstance(value, datetime) else value
    if stamp is None:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.astimezone()
    return stamp.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _age(value: Any) -> float | None:
    stamp = sch._parse_stamp(value)
    return round(max(0.0, (datetime.now() - stamp).total_seconds()), 1) if stamp else None


# ---------------------------------------------------------------------------
# Project, plan, runner


def project_info(root: str | Path, *, name: str | None = None, identifier: str | None = None) -> dict[str, str]:
    from alfrd.manifest_default import manifest_data
    from alfrd.runtime.identity import project_identifier

    base = Path(root).expanduser().resolve()
    if name is None:
        data, _path, _default = manifest_data(base)
        name = str(data.get("name") or base.name)
    return {"name": name, "identifier": identifier or project_identifier(base, name), "root": str(base)}


def _plan(root: Path, plan_id: str | None) -> dict[str, Any]:
    if not (root / sch.PLANS_DIR).is_dir() and plan_id is None:
        raise StatusNotFound(f"no plans in {root}")
    if plan_id is None:
        plans = sch.list_plans(root)
        if not plans:
            raise StatusNotFound(f"no plans in {root}")
        return plans[0]
    if not re.match(r"^[A-Za-z0-9_-]+$", str(plan_id)):
        raise StatusNotFound(f"bad plan id {plan_id!r}")
    try:
        return sch.PlanDir(root, plan_id).load()
    except ExecutionError as exc:
        raise StatusNotFound(str(exc)) from exc


def runner_info(folder: sch.PlanDir, plan: Mapping[str, Any]) -> dict[str, Any]:
    """Is the plan's runner alive? Read-only: lock probe, pid/start time, heartbeat."""
    info = plan.get("runner") or {}
    host = info.get("host")
    here = socket.gethostname()
    owned = not host or host == here
    beat = float(plan.get("heartbeat") or 10)
    age = _age(info.get("heartbeat"))
    if owned:
        alive = folder.runner_alive() or sch._starting(plan)
    else:  # another host: all we have is the heartbeat
        alive = age is not None and age < 3 * beat and not info.get("stopped")
    stale = plan.get("status") == "running" and not alive
    return {"alive": bool(alive), "host": host, "pid": info.get("pid"), "heartbeat_age_s": age,
            "stale": bool(stale), "owned_by_this_host": owned}


# ---------------------------------------------------------------------------
# Cells


def _count_key(value: str) -> str | None:
    if value == "skip":
        return None
    if value == pc.BLOCKED:
        return "skipped"
    if value in ("todo", "running", "done", "failed", "cancelled", "interrupted"):
        return value
    return "todo" if value == "queued" else None


def _unit_for(units: Sequence[Mapping[str, Any]], row: str, step: str) -> Mapping[str, Any] | None:
    for unit in reversed(units):
        if row in (unit.get("rows") or [unit.get("row")]) and step in (unit.get("steps") or []):
            return unit
    return None


def _entity(project: Mapping[str, str], row: pc.PlanRow | Mapping[str, Any] | None, step: str | None = None,
            log: str | None = None) -> dict[str, Any]:
    get = (lambda k: getattr(row, k, "")) if isinstance(row, pc.PlanRow) else (lambda k: (row or {}).get(k, ""))
    try:
        return make_entity(project=project["identifier"], target=get("target"), project_code=get("code"),
                           workdir=get("workdir"), step=step, file=log)
    except ValueError:
        return {"project": project["identifier"]}


def _duration(unit: Mapping[str, Any]) -> float | None:
    start, end = sch._parse_stamp(unit.get("started")), sch._parse_stamp(unit.get("finished"))
    return round((end - start).total_seconds(), 1) if start and end else None


# ---------------------------------------------------------------------------
# Events and the cursor


def _events(plan: Mapping[str, Any], units: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Everything that happened to the plan, oldest first, each with a stable ``id``."""
    out: list[dict[str, Any]] = []
    for item in plan.get("history") or []:
        text = str(item.get("event") or "")
        at = utc(item.get("at"))
        if at:
            out.append({"id": "p" + hashlib.sha1(f"{item.get('at')}|{text}".encode()).hexdigest()[:10],
                        "at": at, "type": "plan", "text": text})
    for unit in units:
        base = {"unit": unit.get("id"), "target": unit.get("target") or None, "project_code": unit.get("code") or None,
                "steps": unit.get("steps") or [], "rows": unit.get("rows") or [unit.get("row")]}
        at = utc(unit.get("started"))
        if at:
            out.append({"id": f"s{unit.get('id')}", "at": at, "type": "started", **base, "log": unit.get("log")})
        at = utc(unit.get("finished"))
        if at and unit.get("status") != "running":
            out.append({"id": f"f{unit.get('id')}", "at": at, "type": "finished", **base, "status": unit.get("status"),
                        "exit_code": unit.get("exit_code"), "error": unit.get("error"), "log": unit.get("log")})
    out.sort(key=lambda e: e["at"])  # stable: history order, then units in id order
    return out


def _short(event_id: str) -> str:
    return hashlib.sha1(event_id.encode()).hexdigest()[:8]


def parse_cursor(cursor: str | None) -> tuple[tuple[str, set[str]] | None, str | None]:
    """``((at, ids seen at that second), digest)`` from a cursor; ValueError when it isn't one of ours.

    Event times have one-second resolution and come from several files, so the
    cursor names the events already seen in its last second instead of trusting
    an order within that second.
    """
    if not cursor:
        return None, None
    text = str(cursor)
    if not text.startswith("c1."):
        raise ValueError(f"not a plan status cursor: {cursor!r}")
    body, _, digest = text[3:].partition("~")
    if not body:
        return None, digest or None
    at, _, ids = body.partition("|")
    seen = {i for i in ids.split(",") if i}
    if not re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$", at) or not seen or not all(re.match(r"^[0-9a-f]{8}$", i) for i in seen):
        raise ValueError(f"not a plan status cursor: {cursor!r}")
    return (at, seen), digest or None


def _cursor(events: Sequence[Mapping[str, Any]], digest: str = "") -> str:
    if not events:
        return f"c1.~{digest}"
    at = events[-1]["at"]
    seen = [_short(e["id"]) for e in events if e["at"] == at][-50:]
    return f"c1.{at}|{','.join(seen)}~{digest}"


def events_since(events: Sequence[Mapping[str, Any]], cursor: str | None) -> list[dict[str, Any]]:
    key, _digest = parse_cursor(cursor)
    if key is None:
        return [dict(e) for e in events]
    at, seen = key
    return [dict(e) for e in events if e["at"] > at or (e["at"] == at and _short(e["id"]) not in seen)]


# ---------------------------------------------------------------------------
# The status document


def _summary(doc: Mapping[str, Any]) -> str:
    plan, counts, progress = doc["plan"], doc["counts"], doc["progress"]
    head = f"Plan {plan['id']} is {plan['status']}"
    if doc["runner"]["stale"]:
        head += " but its runner is not running (status is stale; `alfrd plan reconcile`)"
    parts = [head]
    if doc["running"]:
        r = doc["running"][0]
        where = "/".join(x for x in (r.get("project_code"), r.get("workdir")) if x)
        more = f" (+{len(doc['running']) - 1} more)" if len(doc["running"]) > 1 else ""
        parts.append(f"running {r['step']} on {r['target']}{f' ({where})' if where else ''}{more}")
    parts.append(f"{progress['cells_done']}/{progress['cells_total']} cells done")
    failed = doc["failures"]
    if failed:
        f = failed[0]
        parts.append(f"{len(failed)} failed ({f['target']} {f['step']}: {(f.get('reason') or 'failed')[:80]}"
                     f"{'; …' if len(failed) > 1 else ''})")
    delayed = [w for w in doc.get("waiting") or [] if w.get("kind") == "delay"]
    conflicts = len(doc.get("waiting") or []) - len(delayed)
    if conflicts:
        parts.append(f"{conflicts} waiting on a conflict")
    if delayed:
        parts.append(f"{delayed[0]['step']} starts after {delayed[0]['until']}")
    eta = progress.get("eta_s")
    if eta and plan["status"] == "running":
        parts.append(f"ETA ~{_human(eta)}")
    return ", ".join(parts) + "."


def _human(seconds: float) -> str:
    seconds = float(seconds)
    if seconds < 90:
        return f"{seconds:.0f} s"
    if seconds < 5400:
        return f"{seconds / 60:.0f} min"
    return f"{seconds / 3600:.0f} h"


def _digest(plan: Mapping[str, Any], control: str, runner: Mapping[str, Any], table: pc.PlanTable | None,
            csv_path: Path) -> str:
    try:
        stat = csv_path.stat()
        csv_sig = f"{stat.st_mtime_ns}:{stat.st_size}"
    except OSError:
        csv_sig = "-"
    cells = "" if table is None else ";".join(f"{r.key}={','.join(r.cell(s) for s in table.steps)}" for r in table.rows)
    raw = "|".join([str(plan.get("status")), control, str(runner.get("alive")), str(runner.get("stale")), csv_sig,
                    hashlib.sha1(cells.encode()).hexdigest(), str(len(plan.get("waiting") or []))])
    return hashlib.sha1(raw.encode()).hexdigest()[:10]


def plan_status(root: str | Path, plan: str | None = None, *, detail: str = "summary", since: str | None = None,
                limit: int | None = None, offset: int = 0, reconcile: bool = False,
                project: Mapping[str, str] | None = None) -> dict[str, Any]:
    """The ``alfrd.plan_status/1`` document for ``plan`` (default: the latest plan).

    ``detail``: ``summary`` (default), ``rows`` (the targets × steps grid,
    paginated with ``limit``/``offset``) or ``full`` (rows plus each cell's
    command, duration, unit, log and resource usage). Raises
    :class:`StatusNotFound`, and ``ValueError`` for a bad ``detail``/``since``.
    """
    if detail not in DETAILS:
        raise ValueError(f"detail must be one of {', '.join(DETAILS)}")
    parse_cursor(since)  # validate early
    base = Path(root).expanduser().resolve()
    if not base.is_dir():
        raise StatusNotFound(f"project folder {base} not found")
    if reconcile:
        sch.reconcile(base)
    info = dict(project) if project else project_info(base)
    data = _plan(base, plan)
    folder = sch.PlanDir(base, data["id"])
    control = folder.control()
    units = folder.units()
    runner = runner_info(folder, data)
    csv_path = folder.csv_path(data)
    table: pc.PlanTable | None = None
    cfg = None
    error = None
    try:
        cfg = load_execution(base)
        table = sch.table_for(cfg, csv_path, data.get("steps"))
    except Exception as exc:  # noqa: BLE001 - reported, never raised: the plan may still be readable
        error = str(exc)
    events = _events(data, units)
    from alfrd.agent_loop import status as loop_status

    loop = loop_status(data, units)
    digest = _digest(data, control, runner, table, csv_path)
    if loop:
        digest = hashlib.sha1((digest + json.dumps(loop, sort_keys=True)).encode()).hexdigest()[:10]
    cursor = _cursor(events, digest)
    head = {"schema": SCHEMA, "generated_at": _now_utc(), "cursor": cursor,
            "project": {"name": info["name"], "identifier": info["identifier"]}}
    if since is not None and since == cursor:
        slim = {"plan": {"id": data["id"], "status": data.get("status")}, "runner": runner}
        return {**head, "changed": False, **slim, "exit_code": exit_code({**slim, "counts": data.get("counts") or {}})}
    steps = list(table.steps if table else data.get("steps") or [])
    rows = table.rows if table else []
    running_units = [u for u in units if u.get("status") == "running"]
    counts = {k: 0 for k in ("todo", "queued", "running", "done", "failed", "skipped", "cancelled", "interrupted")}
    for row in rows:
        for step in steps:
            key = _count_key(row.cell(step))
            if key:
                counts[key] += 1
    waiting: list[dict[str, Any]] = []
    if table is not None and cfg is not None and data.get("status") in sch.ACTIVE and data.get("mode") != "batch":
        # Conflicts are recomputed; step delays only the runner knows (it records them in the plan).
        waiting = sch.waiting_rows(data, table, units, cfg) + [w for w in data.get("waiting") or [] if w.get("kind") == "delay"]
        limit_c = int(data.get("concurrency") or 1)
        names, match = sch.serialize_settings(data, cfg)
        picked = sch.pick_rows(table, mode=str(data.get("mode") or "step"), on_failure=str(data.get("on_failure") or "stop_target"),
                               limit=limit_c, free=max(0, limit_c - len(running_units)), names=names, match=match, base=cfg.cwd,
                               running_rows={k for u in running_units for k in (u.get("rows") or [u.get("row")])},
                               started_rows={k for u in units for k in (u.get("rows") or [u.get("row")]) if k},
                               locks={f"{u['code']}/{u.get('workdir') or ''}" for u in running_units if u.get("code")})
        # queued = the next cell of the rows that start as soon as the runner looks (free slots, no conflict).
        for _row, _spec in picked.start:
            if counts["todo"]:
                counts["todo"] -= 1
                counts["queued"] += 1
    total = sum(counts.values())
    durations = sch.step_durations(base)
    by_row = {r.key: r for r in rows}
    running = []
    for unit in running_units:
        for key in unit.get("rows") or [unit.get("row")]:
            row = by_row.get(key)
            step = next((s for s in unit.get("steps") or [] if row and row.cell(s) == pc.RUNNING), None) or (unit.get("steps") or [""])[0]
            started = unit.get("started")
            running.append({
                "row": key, "target": row.target if row else unit.get("target"), "project_code": (row.code if row else unit.get("code")) or None,
                "workdir": (row.workdir if row else unit.get("workdir")) or None, "step": step, "steps": unit.get("steps") or [],
                "unit": unit.get("id"), "started_at": utc(started), "elapsed_s": _age(started),
                "median_s": round(durations[step], 1) if step in durations else None,
                "pid": unit.get("pid"), "log": unit.get("log"), "entity": _entity(info, row or unit, step, unit.get("log")),
            })
    failures = []
    for row in rows:
        for step in steps:
            if row.cell(step) != pc.FAILED:
                continue
            unit = _unit_for(units, row.key, step)
            failures.append({
                "row": row.key, "target": row.target, "project_code": row.code or None, "workdir": row.workdir or None,
                "step": step, "reason": (unit or {}).get("error") or ("marked failed in the plan CSV" if unit is None else "failed"),
                "exit_code": (unit or {}).get("exit_code"), "finished_at": utc((unit or {}).get("finished")),
                "unit": (unit or {}).get("id"), "log": (unit or {}).get("log"),
                "entity": _entity(info, row, step, (unit or {}).get("log")),
            })
    doc: dict[str, Any] = {
        **head,
        "changed": True,
        "plan": {"id": data["id"], "csv": data.get("csv"), "mode": data.get("mode"), "status": data.get("status"),
                 "control": control, "concurrency": int(data.get("concurrency") or 1), "on_failure": data.get("on_failure"),
                 "steps": steps, "created_at": utc(data.get("created")), "started_at": utc((data.get("runner") or {}).get("started")),
                 "finished_at": utc((data.get("runner") or {}).get("stopped")) if data.get("status") in TERMINAL else None},
        "runner": runner,
        "counts": counts,
        "progress": {"cells_done": counts["done"], "cells_total": total, "eta_s": _eta(data, table, running_units, durations)},
        "running": running,
        "failures": failures,
        "waiting": waiting,
    }
    if loop:
        doc["loop"] = loop
    if error:
        doc["error"] = error
    if since is not None:
        doc["events"] = events_since(events, since)[-MAX_EVENTS:]
    if detail in ("rows", "full"):
        doc["rows"] = _rows(info, rows, steps, units, detail, limit, offset)
        doc["rows_total"] = len(rows)
    doc["summary"] = _summary(doc)
    doc["exit_code"] = exit_code(doc)
    return doc


def _eta(plan: Mapping[str, Any], table: pc.PlanTable | None, running: Sequence[Mapping[str, Any]],
         durations: Mapping[str, float]) -> float | None:
    if table is None or plan.get("status") not in ("running", "paused"):
        return None
    grid = table.to_dict()
    todo = {s for r in grid["rows"] for s, v in r["cells"].items() if v == pc.TODO}
    if not durations or any(s not in durations for s in todo):
        return None
    items = sch.queue(grid, durations, int(plan.get("concurrency") or 1), str(plan.get("on_failure") or "stop_target"), running)
    ends = [q["eta_start"] + (q["estimate"] or 0.0) for q in items]
    for unit in running:
        start = sch._parse_stamp(unit.get("started"))
        est = sum(durations.get(s, 0.0) for s in unit.get("steps") or [])
        if start:
            ends.append(max(0.0, est - (datetime.now() - start).total_seconds()))
    return round(max(ends), 0) if ends else 0.0


def _rows(info, rows, steps, units, detail, limit, offset) -> list[dict[str, Any]]:
    offset = max(0, int(offset or 0))
    chosen = rows[offset:offset + int(limit)] if limit else rows[offset:]
    out = []
    for row in chosen:
        item: dict[str, Any] = {"row": row.key, "target": row.target, "project_code": row.code or None,
                                "workdir": row.workdir or None, "files": row.files.split(",") if row.files else [],
                                "cells": {s: row.cell(s) for s in steps}, "entity": _entity(info, row)}
        if detail == "full":
            cells = {}
            for step in steps:
                unit = _unit_for(units, row.key, step)
                if unit is None:
                    continue
                cells[step] = {"unit": unit.get("id"), "status": unit.get("status"), "command": unit.get("argv"),
                               "started_at": utc(unit.get("started")), "finished_at": utc(unit.get("finished")),
                               "duration_s": _duration(unit), "exit_code": unit.get("exit_code"), "error": unit.get("error"),
                               "log": unit.get("log"), "usage": unit.get("usage")}
            item["detail"] = cells
        out.append(item)
    return out


def exit_code(doc: Mapping[str, Any]) -> int:
    """0 finished clean, 1 finished with failures, 2 running, 3 paused / interrupted / cancelled, 5 stale."""
    status = (doc.get("plan") or {}).get("status")
    if (doc.get("runner") or {}).get("stale"):
        return EXIT_STALE
    if status == "running":
        return EXIT_RUNNING
    if status in ("paused", "interrupted", "cancelled"):
        return EXIT_STOPPED
    counts = doc.get("counts") or {}
    return EXIT_FAILED if status == "failed" or counts.get("failed") else EXIT_OK


def list_plans(root: str | Path, *, project: Mapping[str, str] | None = None) -> dict[str, Any]:
    base = Path(root).expanduser().resolve()
    if not base.is_dir():
        raise StatusNotFound(f"project folder {base} not found")
    info = dict(project) if project else project_info(base)
    items = []
    for data in sch.list_plans(base):
        folder = sch.PlanDir(base, data["id"])
        runner = runner_info(folder, data)
        items.append({"id": data["id"], "status": data.get("status"), "csv": data.get("csv"), "mode": data.get("mode"),
                      "created_at": utc(data.get("created")), "counts": data.get("counts") or {},
                      "runner_alive": runner["alive"], "stale": runner["stale"]})
    return {"schema": LIST_SCHEMA, "generated_at": _now_utc(),
            "project": {"name": info["name"], "identifier": info["identifier"]}, "plans": items}


# ---------------------------------------------------------------------------
# Waiting and following


def _done(doc: Mapping[str, Any], until: str) -> bool:
    status = (doc.get("plan") or {}).get("status")
    stuck = status in TERMINAL or status == "paused" and not (doc.get("runner") or {}).get("alive") or (doc.get("runner") or {}).get("stale")
    if until == "done":
        return bool(stuck)
    if until == "failed":
        return bool(stuck) or bool((doc.get("counts") or {}).get("failed"))
    return False


def wait(root: str | Path, plan: str | None = None, *, until: str = "any-change", timeout: float = 3600.0,
         since: str | None = None, poll: float = 1.0, project: Mapping[str, str] | None = None,
         detail: str = "summary") -> dict[str, Any]:
    """Block until ``until`` (``done`` | ``failed`` | ``any-change``) or ``timeout`` s; returns the status document.

    ``reason`` in the result: ``done``, ``failed``, ``changed`` or ``timeout``.
    """
    if until not in ("done", "failed", "any-change"):
        raise ValueError("until must be done, failed or any-change")
    deadline = time.monotonic() + max(0.0, float(timeout))
    first = plan_status(root, plan, detail=detail, project=project)
    plan = first["plan"]["id"]
    start_cursor = since or first["cursor"]
    doc = first
    while True:
        if until == "any-change" and doc["cursor"] != start_cursor:
            return {**doc, "reason": "changed"}
        if until != "any-change" and _done(doc, until):
            failed = bool(doc["counts"].get("failed"))
            return {**doc, "reason": "failed" if failed and until == "failed" else "done"}
        if time.monotonic() >= deadline:
            return {**doc, "reason": "timeout"}
        time.sleep(min(poll, max(0.01, deadline - time.monotonic())))
        doc = plan_status(root, plan, detail=detail, project=project)


def follow(root: str | Path, plan: str | None = None, *, since: str | None = None, keep: bool = False,
           poll: float = 1.0, timeout: float | None = None, project: Mapping[str, str] | None = None) -> Iterator[dict[str, Any]]:
    """Events after ``since`` (JSON-able dicts with a ``cursor`` to resume from).

    With ``keep`` it waits for more until the plan is finished / stopped (or ``timeout`` s).
    """
    base = Path(root).expanduser().resolve()
    info = dict(project) if project else project_info(base)
    data = _plan(base, plan)
    folder = sch.PlanDir(base, data["id"])
    cursor = since
    deadline = time.monotonic() + timeout if timeout else None
    while True:
        data = folder.load()
        events = _events(data, folder.units())
        fresh = events_since(events, cursor)
        for event in fresh:
            done = [e for e in events if e["at"] < event["at"] or e["at"] == event["at"] and (
                e["id"] == event["id"] or e not in fresh or fresh.index(e) < fresh.index(event))]
            cursor = _cursor(done)
            yield {"schema": EVENT_SCHEMA, "project": info["identifier"], "plan": data["id"], **event, "cursor": cursor}
        if not keep:
            return
        runner = runner_info(folder, data)
        if data.get("status") in TERMINAL or runner["stale"] or (data.get("status") == "paused" and not runner["alive"]):
            return
        if deadline is not None and time.monotonic() >= deadline:
            return
        time.sleep(poll)


# ---------------------------------------------------------------------------
# Logs


def log_tail(root: str | Path, plan: str | None = None, *, target: str | None = None, step: str | None = None,
             unit: str | None = None, lines: int = 50, project: Mapping[str, str] | None = None) -> dict[str, Any]:
    """The last ``lines`` (≤ 200) of a plan command's log (≤ 64 KiB read, ANSI stripped).

    Pick the command by ``unit`` id, or by ``target`` (a row key works too) and
    ``step`` (the latest command of that cell; without ``step``, of that row).
    """
    base = Path(root).expanduser().resolve()
    info = dict(project) if project else project_info(base)
    data = _plan(base, plan)
    folder = sch.PlanDir(base, data["id"])
    units = folder.units()
    chosen = None
    for item in reversed(units):
        if unit and item.get("id") == unit:
            chosen = item
            break
        if not unit and target:
            rows = item.get("rows") or [item.get("row")]
            if (item.get("target") == target or target in rows) and (step is None or step in (item.get("steps") or [])):
                chosen = item
                break
    if chosen is None or not chosen.get("log"):
        raise StatusNotFound("no command log for " + (f"unit {unit}" if unit else f"{target} {step or ''}".strip()))
    path = (base / chosen["log"]).resolve()
    if folder.path.resolve() not in path.parents:
        raise StatusNotFound("that log does not belong to the plan")
    count = max(1, min(int(lines or 50), MAX_LOG_LINES))
    try:
        size = path.stat().st_size
        with open(path, "rb") as stream:
            stream.seek(max(0, size - MAX_LOG_BYTES))
            raw = stream.read(MAX_LOG_BYTES)
    except OSError as exc:
        raise StatusNotFound(f"cannot read {chosen['log']}: {exc}") from exc
    text = strip_ansi(raw.decode("utf-8", errors="replace")).replace("\r\n", "\n")
    all_lines = text.split("\n")
    if size > MAX_LOG_BYTES:
        all_lines = all_lines[1:]  # the first one is probably cut
    if all_lines and all_lines[-1] == "":
        all_lines.pop()
    out = all_lines[-count:]
    return {"schema": LOG_SCHEMA, "project": info["identifier"], "plan": data["id"], "unit": chosen.get("id"),
            "target": chosen.get("target"), "steps": chosen.get("steps") or [], "status": chosen.get("status"),
            "log": chosen["log"], "size": size, "lines": out,
            "truncated": size > MAX_LOG_BYTES or len(all_lines) > count}


__all__ = [
    "DETAILS", "EXIT_FAILED", "EXIT_NOT_FOUND", "EXIT_OK", "EXIT_RUNNING", "EXIT_STALE", "EXIT_STOPPED", "SCHEMA",
    "StatusNotFound", "exit_code", "follow", "list_plans", "log_tail", "parse_cursor", "plan_status", "project_info",
    "strip_ansi", "wait",
]
