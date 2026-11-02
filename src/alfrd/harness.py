"""Read-only harness projections of existing chat records and plan events.

No recovery, telemetry advancement, model calls or writes belong here. Legacy
snapshots lack a complete temporal history; the response reports that limitation.
"""
from __future__ import annotations

import copy
import json
import math
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache
from itertools import pairwise
from pathlib import Path
from zoneinfo import ZoneInfo

from alfrd import chat
from alfrd.events import EventLog

DEFAULTS = {
    "default_project": None,
    "stuck_after_minutes": 10,
    "poll_seconds": 2,
    "role_order": ["orchestrator", "research", "design", "build", "verify", "fix"],
    "default_scope": "current", "feed_limit": 200, "replay_speed": 1.0,
    "show_completed": True, "runner_stale_after_seconds": 30,
}
STATES = ("pending", "running", "blocked", "done", "failed", "cancelled", "stopped")
USAGE_KEYS = ("input_tokens", "output_tokens", "total_tokens", "total_cost_usd")


def merge_settings(raw=None):
    """Missing or malformed optional observer settings always keep safe defaults."""
    out = copy.deepcopy(DEFAULTS)
    if not isinstance(raw, dict):
        return out
    for key, value in raw.items():
        if key in ("poll_seconds", "stuck_after_minutes", "replay_speed", "runner_stale_after_seconds"):
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0:
                out[key] = value
        elif key == "feed_limit" and type(value) is int and 1 <= value <= 1000 or key == "default_project" and (value is None or isinstance(value, str)) or key == "default_scope" and value in ("current", "all") or key == "show_completed" and isinstance(value, bool):
            out[key] = value
        elif key == "role_order" and isinstance(value, list) and all(isinstance(v, str) and v for v in value):
            out[key] = list(value)
    # Compatibility with the study's proposed presentation settings.
    if "poll_seconds" not in raw and isinstance(raw.get("refresh_seconds"), (int, float)):
        out = merge_settings({**out, "poll_seconds": raw["refresh_seconds"]})
    if "stuck_after_minutes" not in raw and type(raw.get("stuck_after_seconds")) in (int, float):
        out = merge_settings({**out, "stuck_after_minutes": raw["stuck_after_seconds"] / 60})
    return out


def settings(root):
    from alfrd.execution import merged_manifest
    try:
        return merge_settings(merged_manifest(root).get("harness"))
    except (OSError, ValueError, TypeError):
        return merge_settings()


def timestamp(value, *, strict=False):
    """Legacy naive records use server local time; API replay requires an offset."""
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            if strict:
                raise ValueError("at must include a timezone")
            parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
            # TZ honors deployments where the system timezone is configured explicitly.
            import os
            zone = os.environ.get("TZ")
            if zone:
                parsed = parsed.replace(tzinfo=ZoneInfo(zone))
            elif Path("/etc/localtime").exists():
                with open("/etc/localtime", "rb") as handle:
                    parsed = parsed.replace(tzinfo=ZoneInfo.from_file(handle))
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError, OSError, KeyError):
        if strict:
            raise ValueError("at must be an ISO-8601 timestamp with timezone") from None
        return None


def iso(value):
    return value.isoformat().replace("+00:00", "Z") if value else None


def read_json(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def records(folder, pattern="*.json"):
    return [item for path in sorted(folder.glob(pattern)) if (item := read_json(path))]


def read_events(path):
    """Read both retained generations without EventLog's UI pagination limit."""
    path = Path(path)
    fingerprints = []
    for p in (path.with_name(path.name + ".1"), path):
        try:
            stat = p.stat()
            fingerprints.append((stat.st_ino, stat.st_size, stat.st_mtime_ns))
        except OSError:
            fingerprints.append(None)
    return copy.deepcopy(_indexed_events(str(path), tuple(fingerprints)))


@lru_cache(maxsize=16)
def _indexed_events(path, fingerprints):
    events = EventLog(path).read_since(limit=2**63 - 1)
    valid = {}
    for event in events:
        if not timestamp(event.get("at")) or not isinstance(event.get("kind"), str):
            continue
        normalized = {**event, "data": event["data"] if isinstance(event.get("data"), dict) else {}}
        for key in ("unit", "agent", "target"):
            if not isinstance(normalized.get(key), str):
                normalized[key] = None
        valid[event["seq"]] = normalized
    return sorted(valid.values(), key=lambda e: e["seq"])


def empty_usage():
    return {**dict.fromkeys(USAGE_KEYS), "known_at": None, "complete": False}


def usage_total(observations):
    observations = list(observations)
    out = empty_usage()
    for key in USAGE_KEYS:
        values = [u.get(key) for u in observations if type(u.get(key)) in (int, float) and math.isfinite(u[key])]
        out[key] = sum(values) if values else None
    out["known_at"] = max((u["known_at"] for u in observations if u.get("known_at")), default=None)
    out["complete"] = bool(observations) and all(u.get("complete") for u in observations)
    return out


def _event_task(event, units):
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    unit = units.get(event.get("unit"), {})
    task = data.get("task") or data.get("row") or unit.get("row") or event.get("target")
    return task if isinstance(task, str) and task.startswith("r") and "." in task else None


def _known(record, at):
    created = timestamp(record.get("created"))
    return created is not None and created <= at


def _task_view(rec, status, owner, units, replay):
    task = rec.get("task") if isinstance(rec.get("task"), dict) else {}
    view = {"row": rec["row"], "request": rec.get("request"), "kind": rec.get("kind"),
            "owner": owner if isinstance(owner, str) else None, "status": status, "created": iso(timestamp(rec.get("created"))),
            "depends_on": [d for d in rec.get("depends_on", []) if isinstance(d, str)] if isinstance(rec.get("depends_on"), list) else [], "units": [],
            "task": {key: task[key] for key in ("id", "title", "role", "description", "acceptance_criteria", "expected_outputs", "budget") if key in task}}
    if not replay:
        if isinstance(rec.get("result"), dict):
            view["result"] = {k: rec["result"][k] for k in ("status", "summary", "artifacts") if k in rec["result"]}
        if isinstance(rec.get("error"), str):
            view["error"] = rec["error"]
        if "cost_warning" in rec:
            view["cost_warning"] = rec["cost_warning"]
        view["logs"] = [{"unit": u["id"], **{k: u.get(k) for k in
            ("log", "status", "started", "finished", "total_cost_usd")},
            "model": u.get("model") or u.get("requested_model"),
            "handoff": f".alfrd/plans/{u['_harness_plan']}/handoffs/{u['id']}"} for u in units]
    return view


def _fold(events, tasks, units, at, replay):
    """Fold event state; keep one segment per unit, including concurrent attempts."""
    statuses, owners, activity, segments, usage, latest = {}, {}, {}, {}, {}, {}
    for event in events:
        task = _event_task(event, units)
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        kind, when = event.get("kind", ""), timestamp(event.get("at"))
        if task and kind not in ("runner.heartbeat", "usage.updated"):
            latest[task] = {"kind": kind, "at": iso(when),
                            **{k: data[k] for k in ("status", "attempt") if k in data}}
        unit_id = event.get("unit")
        agent = event.get("agent") or (data.get("owner") if isinstance(data.get("owner"), str) else None)
        if task and agent:
            owners[task] = agent
        if task and kind != "runner.heartbeat":
            sample = data.get("usage") if isinstance(data.get("usage"), dict) else {}
            meaningful = kind != "usage.updated" or any(sample.get(k) != usage.get(unit_id, {}).get(k) for k in USAGE_KEYS)
            if meaningful:
                activity[unit_id or task] = when
        status = {"task.created": "pending", "task.started": "running", "turn.started": "running",
                  "task.blocked": "blocked", "task.failed": "failed", "turn.failed": "failed",
                  "task.finished": data.get("status", "done"), "turn.finished": data.get("status", "done"),
                  "turn.retrying": "pending", "task.reassigned": "pending"}.get(kind)
        if task and status in STATES:
            statuses[task] = status
        if unit_id and task and kind in ("turn.started", "task.started") and unit_id not in segments:
            segments[unit_id] = {"id": f"{unit_id}:running:{event['seq']}", "request": task.split(".")[0],
                "task": task, "unit": unit_id, "agent": agent or units.get(unit_id, {}).get("agent"),
                "attempt": data.get("attempt", 1), "state": "running", "start": iso(when),
                "end": iso(at), "open": True, "source_seq": event["seq"]}
        if unit_id in segments and kind in ("turn.finished", "turn.failed", "task.finished", "task.blocked"):
            segment = segments[unit_id]
            if segment["open"]:
                segment.update(end=iso(when), open=False, state=status or "done")
        if unit_id and isinstance(data.get("usage"), dict):
            usage[unit_id] = {**empty_usage(), **{k: data["usage"].get(k) for k in USAGE_KEYS},
                              "known_at": iso(when), "complete": all(data["usage"].get(k) is not None for k in USAGE_KEYS)}
    # Immutable completed attempts supply gaps in legacy event history. Do not use
    # mutable status/owner/usage to fill historical running attempts.
    for uid, unit in units.items():
        start, end = timestamp(unit.get("started")), timestamp(unit.get("finished"))
        if not start or start > at:
            continue
        row = unit.get("row")
        if uid not in segments:
            segments[uid] = {"id": f"{uid}:snapshot", "request": str(row).split(".")[0], "task": row,
                "unit": uid, "agent": unit.get("agent") or next(iter(unit.get("steps") or []), None),
                "attempt": unit.get("attempt_number", 1), "state": "running", "start": iso(start),
                "end": iso(at), "open": True, "source_seq": None}
        if end and end <= at:
            segments[uid].update(end=iso(end), open=False, state=unit.get("status", "done"))
        if (not replay or (end and end <= at)) and uid not in usage:
            from alfrd.agent_io import normalized_usage
            nested = normalized_usage(unit) or {}
            usage[uid] = {**empty_usage(), **{k: unit.get(k, nested.get(k)) for k in USAGE_KEYS}, "known_at": iso(end)}
        usage.setdefault(uid, empty_usage())
    for uid in segments:
        usage.setdefault(uid, empty_usage())
    views = []
    for rec in tasks:
        row = rec["row"]
        status = statuses.get(row, "pending") if replay else rec.get("status", statuses.get(row, "pending"))
        status = "running" if status == "working" else status if status in STATES else "pending"
        view = _task_view(rec, status, owners.get(row) if replay else rec.get("owner"),
                          [u for u in units.values() if u.get("row") == row], replay)
        view["units"] = [uid for uid, segment in segments.items() if segment["task"] == row]
        view["attempts"] = len(view["units"])
        view["usage"] = usage_total(usage[uid] for uid in view["units"])
        view["latest"] = latest.get(row)
        views.append(view)
    return views, list(segments.values()), activity, usage


def _agents(tasks, segments, activity, usage, at, threshold):
    out = []
    names = sorted({t["owner"] for t in tasks if t.get("owner")} | {s["agent"] for s in segments if s.get("agent")})
    for name in names:
        mine = [s for s in segments if s["agent"] == name]
        rows = [t for t in tasks if t.get("owner") == name]
        running = [s for s in mine if s["open"] and next((t["status"] for t in tasks if t["row"] == s["task"]), "running") == "running"]
        def progress(segment):
            return max(activity.get(segment["unit"], timestamp(segment["start"])),
                       activity.get(segment["task"], timestamp(segment["start"])))
        # A torn/missing unit file must not turn a known working task idle.
        untracked = [t for t in rows if t["status"] == "running" and not t["units"]]
        baselines = [activity.get(t["row"], timestamp(t["created"])) for t in untracked]
        last = max([progress(s) for s in mine] + baselines, default=None)
        stuck = any((at - value).total_seconds() >= threshold for value in [progress(s) for s in running] + baselines)
        status = "stuck" if stuck else "running" if running or untracked else next((v for v in ("blocked", "failed", "done") if any(t["status"] == v for t in rows)), "idle")
        out.append({"id": name, "label": name, "status": status, "tasks": [t["row"] for t in rows],
            "units": [s["unit"] for s in running], "last_activity_at": iso(last),
            "stuck": {"suspected": stuck, "reason": "no recorded progress; legacy evidence may be incomplete" if stuck else None,
                      "since": iso(last) if stuck else None},
            "usage": usage_total(usage.get(s["unit"], empty_usage()) for s in mine)})
    return out


def _edges(tasks, replay):
    by_id = {t["row"]: t for t in tasks}
    edges = []
    for task in tasks:
        for dep in task["depends_on"]:
            if dep not in by_id:
                continue
            producer = by_id[dep]
            edges.append({"id": f"{dep}>{task['row']}:depends_on", "request": task["request"],
                "from_task": dep, "to_task": task["row"], "from_agent": producer.get("owner"),
                "to_agent": task.get("owner"), "kind": "depends_on",
                "status": "available" if producer["status"] == "done" else "waiting", "at": task["created"],
                "unit": None, "artifacts": [], "source": "task_snapshot", "historical_certainty": "partial"})
    return edges


def _published_edges(dependencies, events, units):
    """Publication evidence is separate from a dependency's planned transfer."""
    out = []
    for event in events:
        if event.get("kind") != "handoff.published":
            continue
        producer = _event_task(event, units)
        for edge in dependencies:
            if edge["from_task"] == producer:
                out.append({**edge, "id": f"{edge['id']}:published:{event['seq']}", "kind": "published",
                            "status": "available", "at": iso(timestamp(event["at"])),
                            "unit": event.get("unit"), "source": "handoff.published"})
    return out


@lru_cache(maxsize=64)
def _provider_times(path, fingerprint):
    """Read a bounded provider tail; retain timestamps, never transcript content."""
    times = []
    try:
        with open(path, "rb") as handle:
            size = handle.seek(0, 2)
            handle.seek(max(0, size - 2 * 1024 * 1024))
            if size > 2 * 1024 * 1024:
                handle.readline()  # discard the partial first line
            for line in handle:
                if len(line) > 1024 * 1024:
                    continue
                try:
                    data = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(data, dict) or data.get("type") not in (
                    "assistant", "user", "item.started", "item.completed", "turn.completed",
                    "content_block_delta", "message_delta",
                ):
                    continue
                when = timestamp(data.get("timestamp") or data.get("at") or data.get("time"))
                if when:
                    times.append(when)
    except OSError:
        pass
    return times


def _provider_activity(plan, segments, activity, at):
    for segment in segments:
        uid = segment["unit"]
        if not segment["open"] or not isinstance(uid, str) or Path(uid).name != uid:
            continue
        path = plan / "logs" / f"{uid}.events.jsonl"
        try:
            stat = path.stat()
        except OSError:
            continue
        times = [t for t in _provider_times(str(path), (stat.st_ino, stat.st_size, stat.st_mtime_ns)) if t <= at]
        if times:
            activity[uid] = max([activity.get(uid, timestamp(segment["start"]))] + times)


def _request_fields(record):
    line = next((line.strip() for line in str(record.get("text") or "").splitlines() if line.strip()), "")
    title = " ".join(line.split())
    bounds = record.get("bounds")
    budget = bounds.get("max_cost_usd") if isinstance(bounds, dict) else None
    return {"title": (title[:79] + "…" if len(title) > 80 else title) or None,
            "max_cost_usd": budget if type(budget) in (int, float) and math.isfinite(budget) and budget > 0 else None,
            "mode": record.get("mode") if record.get("mode") in ("direct", "orchestrated") else None}


def _attention(store, requests, decisions, replay):
    from alfrd.mailbox import Mailbox, validate

    class ObserverMailbox(Mailbox):
        """Apply mailbox repairs/expiry to memory, never to the observed files.

        Mailbox.locked() creates files, truncates torn tails and persists repairs;
        its read methods cannot be used directly by a read-only projection.
        """
        @contextmanager
        def locked(self):
            def read(name):
                out = []
                path = self.dir / f"{name}.jsonl"
                if not path.exists():
                    return out
                with path.open("rb") as stream:
                    for line in stream:
                        if not line.endswith(b"\n"):
                            break
                        record = json.loads(line)
                        validate("agent_message" if name == "messages" else "agent_mailbox_event", record)
                        out.append(record)
                return out
            self.messages, self.events = read("messages"), read("events")
            self._reindex()
            self._repair_resolutions()
            self._expire()
            yield

        def _append(self, name, value):
            getattr(self, name).append(value)
            self._reindex()

    items, by_message = [], {}
    if not replay:
        for request in requests:
            box = ObserverMailbox(store.root, store.id, request["id"])
            if box.summary()["pending_user"] > 0:
                for message in box.pending()["messages"]:
                    item = {"source": "mailbox", "id": message["id"], "request": request["id"],
                            "kind": message["kind"], "at": message.get("created_at")}
                    if item["id"] not in by_message:
                        items.append(item)
                        by_message[item["id"]] = item
    for decision in decisions:
        if decision.get("status") != "pending" or decision.get("needs") != "user":
            continue
        linked = decision.get("mailbox_message_id")
        if not replay and linked:
            if linked in by_message:
                by_message[linked]["decision"] = decision["id"]
            continue
        items.append({"source": "decision", "id": decision["id"],
                      **{k: decision[k] for k in ("request", "task", "kind") if k in decision},
                      "at": decision.get("created")})
    items.sort(key=lambda item: (item.get("kind") != "permission_request", item.get("at") or ""))
    return {"count": len(items), "mailbox_known": not replay, "items": items}


def _view(store, requests, tasks, edges, usage, configured, clock, scope, request, replay):
    """Adapt scoped records to the shared Chat/graph model without future outcomes."""
    messages = [m for m in store.messages() if timestamp(m.get("at")) and timestamp(m["at"]) <= clock
                and (scope == "all" or m.get("request") == request)]
    decisions = []
    for record in records(store.decisions_dir):
        if not _known(record, clock) or (scope != "all" and record.get("request") != request):
            continue
        decision = {k: record[k] for k in ("id", "request", "task", "kind", "needs", "status", "created", "question", "options", "reason",
            "mailbox_message_id", "repeat", "delegated", "choice", "answer", "decided_by", "resolved") if k in record}
        resolved = timestamp(record.get("resolved"))
        if replay and (not resolved or resolved > clock):
            decision["status"] = "pending"
            for key in ("choice", "answer", "decided_by", "resolved"):
                decision.pop(key, None)
        decisions.append(decision)
    nodes = [{"id": t["row"], "request": t["request"], "kind": t["kind"],
              "title": t["task"].get("title", t["row"]), "owner": t["owner"], "status": t["status"],
              "status_text": t["status"].title(), "depth": 1} for t in tasks]
    graph_edges = [{"from": e["from_task"], "to": e["to_task"], "kind": e["kind"]} for e in edges if e["kind"] == "depends_on"]
    return {"session": {k: v for k, v in store.load().items() if k in ("id", "title", "created")},
        "requests": [{"id": r["id"], "created": r.get("created"), "status": "unknown" if replay else r.get("status"),
                      **_request_fields(r)} for r in requests],
        "tasks": tasks, "graph": {"nodes": nodes, "edges": graph_edges, "groups": []},
        "messages": [{k: m[k] for k in ("id", "at", "role", "text", "request", "attachments", "kind") if k in m} for m in messages],
        "decisions": decisions, "blockers": [d for d in decisions if d.get("status") == "pending"],
        "attention": _attention(store, requests, decisions, replay),
        "usage": usage, "settings": {"agents": {name: {k: a[k] for k in ("label", "model") if k in a} for name, a in configured.items()}},
        "read_only": replay}


def _feed(events, units, plan):
    out = []
    for event in events:
        task = _event_task(event, units)
        kind = event.get("kind", "unknown")
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        out.append({"id": f"{plan}:{event['seq']}", "seq": event["seq"], "at": iso(timestamp(event["at"])),
            "kind": kind, "request": data.get("request") or (task.split(".")[0] if task else None),
            "task": task, "unit": event.get("unit"), "agent": event.get("agent"),
            "severity": "error" if "failed" in kind or data.get("status") == "failed" else "warning" if "blocked" in kind else "info",
            # Never expose arbitrary event text/data (may contain paths or commands).
            "text": kind.replace(".", " "),
            "data": {k: data[k] for k in ("attempt", "status", "task_kind") if k in data}})
    return out


def state(root, sid, *, project=None, scope=None, request=None, at=None, before_seq=None, limit=None, now=None):
    """Project a session; feed cursors never change aggregate scope or replay time."""
    store = chat.session(root, sid)
    conf = settings(root)
    current = now or datetime.now(timezone.utc)
    clock = min(timestamp(at, strict=True), current) if at is not None else current
    if scope == "request" and not request:
        raise ValueError("request scope requires a request id")
    if scope is None:
        scope = "all" if conf["default_scope"] == "all" else "request"
    if scope not in ("all", "request") or (scope == "all" and request):
        raise ValueError("scope must be request or all; all cannot include request")
    if before_seq is not None and (type(before_seq) is not int or before_seq < 1):
        raise ValueError("before_seq must be a positive integer")
    if limit is not None and (type(limit) is not int or limit < 1):
        raise ValueError("limit must be a positive integer")
    requests = records(store.requests_dir)
    if scope == "request" and not request:
        request = next((r["id"] for r in reversed(requests) if r.get("status") in chat.REQUEST_ACTIVE), None)
        request = request or next((r["id"] for r in reversed(requests)), None)
    if request and not any(r.get("id") == request for r in requests):
        raise LookupError("unknown request")
    rev = store.rev
    plan = Path(root) / ".alfrd" / "plans" / store.plan_id
    all_events = read_events(plan / "events.jsonl")
    tasks = [t for t in records(store.tasks_dir, "*/task.json") if isinstance(t.get("row"), str) and _known(t, clock)
             and (scope == "all" or t.get("request") == request)]
    requests = [r for r in requests if _known(r, clock) and (scope == "all" or r.get("id") == request)]
    rows = {t["row"] for t in tasks}
    units = {u["id"]: u for u in records(plan / "units") if isinstance(u.get("id"), str) and isinstance(u.get("row"), str) and u["row"] in rows}
    for unit in units.values():
        unit["_harness_plan"] = store.plan_id
    events = [e for e in all_events if timestamp(e["at"]) <= clock and
              (scope == "all" or (e.get("data") or {}).get("request") == request or _event_task(e, units) in rows)]
    views, segments, activity, usage = _fold(events, tasks, units, clock, at is not None)
    _provider_activity(plan, segments, activity, clock)
    agents = _agents(views, segments, activity, usage, clock, conf["stuck_after_minutes"] * 60)
    try:
        configured = chat.settings(root).get("agents", {})
    except (ValueError, OSError):
        configured = {}
    for agent in agents:
        metadata = configured.get(agent["id"], {})
        agent.update(label=metadata.get("label") or agent["id"], model=metadata.get("model"),
                     tasks_done=sum(t["status"] == "done" and t.get("owner") == agent["id"] for t in views))
    edges = _edges(views, at is not None)
    edges += _published_edges(edges, events, units)
    feed = list(reversed(_feed(events, units, store.plan_id)))
    feed = [e for e in feed if before_seq is None or e["seq"] < before_seq]
    count = min(limit or conf["feed_limit"], 1000)
    page = feed[:count]
    warnings = ["legacy_history_partial", "legacy_usage_not_sampled"]
    seqs = [e["seq"] for e in all_events]
    if seqs and (seqs[0] > 1 or any(b != a + 1 for a, b in pairwise(seqs))):
        warnings.append("event_sequence_gap")
    if all_events and clock < timestamp(all_events[0]["at"]):
        warnings.append("before_first_event")
        views, segments, agents, edges, page, requests, usage = [], [], [], [], [], [], {}
    durations = [(timestamp(s["end"]) - timestamp(s["start"])).total_seconds() for s in segments]
    starts = [timestamp(s["start"]) for s in segments]
    ends = [timestamp(s["end"]) for s in segments]
    summary = {"requests": len(requests), "tasks": {"total": sum(t.get("kind") == "task" for t in views),
        **{s: sum(t.get("kind") == "task" and t["status"] == s for t in views) for s in STATES}},
        "agents_active": sum(a["status"] in ("running", "stuck") for a in agents), "attempts": len(segments),
        "stuck": sum(a["stuck"]["suspected"] for a in agents),
        "wall_seconds": max(0, (max(ends) - min(starts)).total_seconds()) if starts else 0,
        "agent_seconds": sum(max(0, d) for d in durations), "usage": usage_total(usage.values()),
        "runner": {"alive": None, "heartbeat_at": None, "stale": None}}
    summary["morning"] = {"finished": bool(views) and all(t["status"] in ("done", "failed", "cancelled", "stopped") for t in views),
        "handoffs": sum(e.get("kind") == "handoff.published" for e in events),
        "dependency_edges": len(edges), "longest_stuck_agent": None, "longest_stuck_seconds": None,
        "errors": [e for e in _feed(events, units, store.plan_id) if e["severity"] == "error"][-3:][::-1],
        "note": "Longest historical stuck interval is unknown without timestamped provider progress."}
    view = _view(store, requests, views, edges, summary["usage"], configured, clock, scope, request, at is not None)
    return {"schema": "alfrd.harness/1", "project": project or Path(root).name, "session": sid, "plan": store.plan_id,
        "scope": {"kind": scope, "request": request if scope == "request" else None},
        "clock": {"mode": "replay" if at is not None else "live", "at": iso(clock), "server_now": iso(current)},
        "revision": {"session": rev, "last_seq": max((e["seq"] for e in events), default=0), "consistent": rev == store.rev},
        "coverage": {"complete": False, "first_seq": min(seqs, default=None), "last_seq": max(seqs, default=None),
            "from": iso(timestamp(all_events[0]["at"])) if all_events else None,
            "to": iso(timestamp(all_events[-1]["at"])) if all_events else None,
            "timezone": str(datetime.now().astimezone().tzinfo), "warnings": warnings},
        "settings": {**conf, "refresh_seconds": conf["poll_seconds"], "stuck_after_seconds": conf["stuck_after_minutes"] * 60},
        "agents": agents, "handoff_edges": edges, "timeline_segments": segments, "feed_events": page,
        "feed_page": {"has_more": len(feed) > count, "next_before_seq": page[-1]["seq"] if len(feed) > count and page else None},
        "summary": summary,
        "view": view}
