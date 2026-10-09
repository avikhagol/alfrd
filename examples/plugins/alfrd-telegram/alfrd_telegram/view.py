"""Plain-text /status views: target cards and numbered pickers, built from ``alfrd.plan_status/1`` (detail=full)."""

from collections import Counter
from datetime import datetime, timezone

PAGE = 5
RULE = "-" * 31
STOPPED = ("failed", "blocked", "cancelled", "interrupted")


def elapsed(seconds):
    """``42s``, ``12m 03s``, ``1h 05m``; ``?`` when unknown."""
    if seconds is None:
        return "?"
    seconds = int(max(0, float(seconds)))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60:02d}s"
    return f"{seconds // 3600}h {seconds % 3600 // 60:02d}m"


def _since(stamp, now=None):
    """Seconds since a ``YYYY-MM-DDTHH:MM:SSZ`` stamp, or None."""
    if not stamp:
        return None
    try:
        then = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return ((now or datetime.now(timezone.utc)) - then).total_seconds()


def ago(stamp, now=None):
    seconds = _since(stamp, now)
    return "no runs yet" if seconds is None else f"{elapsed(seconds)} ago"


def _active(row):
    stamps = [c.get(k) for c in (row.get("detail") or {}).values() for k in ("started_at", "finished_at")]
    return max((s for s in stamps if s), default="")


def targets(doc):
    """``[{key, label, active}]`` for the plan's rows, most recently active first (never-run rows keep CSV order)."""
    rows = doc.get("rows") or []
    names = Counter(r["target"] for r in rows)
    out = []
    for row in rows:
        label = row["target"]
        if names[label] > 1:  # same target in several project codes / work dirs
            label += f" ({row.get('project_code') or row.get('workdir') or row['row']})"
        out.append({"key": row["row"], "label": label, "active": _active(row)})
    return sorted(out, key=lambda t: t["active"], reverse=True)


def steps(doc, key):
    """``{finished, running, failed, next}`` as lists of display lines for one row, or None if the row is gone."""
    row = next((r for r in doc.get("rows") or [] if r["row"] == key), None)
    if row is None:
        return None
    running = {r["step"]: r.get("elapsed_s") for r in doc.get("running") or [] if r.get("row") == key}
    groups = {"finished": [], "running": [], "failed": [], "next": []}
    for step in doc["plan"]["steps"]:
        cell, info = row["cells"].get(step, "skip"), (row.get("detail") or {}).get(step) or {}
        if cell == "done":
            groups["finished"].append((step, elapsed(info.get("duration_s"))))
        elif cell == "running":
            seconds = running.get(step, _since(info.get("started_at")))
            groups["running"].append((step, elapsed(seconds)))
        elif cell in STOPPED:
            took = elapsed(info.get("duration_s")) if info.get("duration_s") is not None else None
            groups["failed"].append((step, ", ".join(x for x in (None if cell == "failed" else cell, took) if x)))
        elif cell == "todo":
            groups["next"].append((step, ""))
    return row, groups


def state(groups):
    """The STEP part of the card title: what the target is doing now."""
    if groups["running"]:
        return groups["running"][0][0]
    if groups["failed"]:
        return f"{groups['failed'][0][0]} (failed)"
    if groups["next"]:
        return f"{groups['next'][0][0]} (next)"
    return "done" if groups["finished"] else "nothing to run"


def card(project, doc, key, label=None):
    found = steps(doc, key)
    if found is None:
        return f"{project} - {label or key}\n{RULE}\nNot in the latest plan ({doc['plan']['id']})."
    row, groups = found
    lines = [f"{project} - {label or row['target']} - {state(groups)}", RULE]
    for name, items in groups.items():
        if items:
            lines += ["", f"{name}:"] + [f" - {step} ({extra})" if extra else f" - {step}" for step, extra in items]
    status = doc["plan"].get("status")
    if status not in ("running", "finished"):
        lines += ["", f"Plan {doc['plan']['id']} is {status}."]
    return "\n".join(lines)


def page_of(items, page):
    start = page * PAGE
    return start, items[start:start + PAGE]


def picker(title, items, page, *, multi):
    """Numbered list ``title (a–b of n)`` with a reply hint; ``items`` are ``(label, note)``."""
    start, shown = page_of(items, page)
    lines = [f"{title} ({start + 1}–{start + len(shown)} of {len(items)})", ""]
    lines += [f"{start + i}. {label}" + (f" · {note}" if note else "") for i, (label, note) in enumerate(shown, 1)]
    hint = ("Tap a number to pick one target, or reply with numbers (e.g. 1 3 or 1,3).\n"
            "Tap All or reply all to pick every target across all pages." if multi
            else "Tap a number or reply with it.")
    more = start + len(shown) < len(items)
    return "\n".join(lines + ["", hint + ("\nTap More ▸ or reply more for the next 5." if more else "")]), more


def buttons(items, page, *, multi):
    """Inline keyboard: one button per shown number, then All / More."""
    start, shown = page_of(items, page)
    rows = [[{"text": str(start + i), "callback_data": f"pick:{start + i}"} for i in range(1, len(shown) + 1)]]
    extra = ([{"text": "All", "callback_data": "pick:all"}] if multi and len(items) > 1 else [])
    if start + len(shown) < len(items):
        extra.append({"text": "More ▸", "callback_data": "pick:more"})
    return {"inline_keyboard": rows + ([extra] if extra else [])}
