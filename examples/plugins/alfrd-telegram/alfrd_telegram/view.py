"""Telegram HTML views: target cards, numbered pickers and handoff Markdown, from ``alfrd.plan_status/1``.

Every dynamic value goes through :func:`esc`; Telegram's HTML mode accepts only
``b i u s code pre a blockquote``, so the views use nothing else.
"""

import html
import re
from collections import Counter
from datetime import datetime, timezone

PAGE = 5
RULE = "━" * 16
STOPPED = ("failed", "blocked", "cancelled", "interrupted")
ICONS = {"done": "✅", "running": "🔄", "failed": "❌", "blocked": "⛔", "cancelled": "🚫",
         "interrupted": "⚠️", "todo": "⏳", "skip": "▫️", "paused": "⏸️", "finished": "🏁"}
GROUPS = {"finished": ("✅", "Finished"), "running": ("🔄", "Running"),
          "failed": ("❌", "Failed"), "next": ("⏳", "Next")}
#: Telegram's message limit is 4096 UTF-16 units; leave room for the header and tags.
BODY = 3400


def esc(value):
    return html.escape(str(value), quote=False)


def icon(status):
    return ICONS.get(str(status), "•")


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


def logged_steps(doc, key):
    """``[(step, cell)]`` of one row whose command has started (so may have a log), newest first."""
    row = next((r for r in doc.get("rows") or [] if r["row"] == key), None)
    if row is None:
        return []
    found = [(s, row["cells"].get(s)) for s in doc["plan"]["steps"]
             if row["cells"].get(s) not in (None, "todo", "skip")]
    return list(reversed(found))


def state(groups):
    """The STEP part of the card title: what the target is doing now (plain text)."""
    if groups["running"]:
        return groups["running"][0][0]
    if groups["failed"]:
        return f"{groups['failed'][0][0]} (failed)"
    if groups["next"]:
        return f"{groups['next'][0][0]} (next)"
    return "done" if groups["finished"] else "nothing to run"


def state_icon(groups):
    for name in ("running", "failed", "next"):
        if groups[name]:
            return GROUPS[name][0]
    return "🏁" if groups["finished"] else "▫️"


def card(project, doc, key, label=None):
    found = steps(doc, key)
    if found is None:
        return (f"📁 <b>{esc(project)}</b> · 🎯 <b>{esc(label or key)}</b>\n{RULE}\n"
                f"<i>Not in the latest plan</i> (<code>{esc(doc['plan']['id'])}</code>).")
    row, groups = found
    lines = [f"📁 <b>{esc(project)}</b> · 🎯 <b>{esc(label or row['target'])}</b>",
             f"{state_icon(groups)} <i>{esc(state(groups))}</i>", RULE]
    for name, items in groups.items():
        if items:
            mark, title = GROUPS[name]
            lines += ["", f"{mark} <b>{title}</b>"]
            lines += [f"  • <code>{esc(step)}</code>" + (f"  <i>{esc(extra)}</i>" if extra else "")
                      for step, extra in items]
    status = doc["plan"].get("status")
    if status not in ("running", "finished"):
        lines += ["", f"{icon(status)} Plan <code>{esc(doc['plan']['id'])}</code> is <b>{esc(status)}</b>."]
    return "\n".join(lines)


def page_of(items, page):
    start = page * PAGE
    return start, items[start:start + PAGE]


HINTS = {
    "project": "Tap a number or reply with it.",
    "target": ("Tap a number to pick one target, or reply with numbers (e.g. <code>1 3</code> or <code>1,3</code>).\n"
               "Tap <b>All</b> or reply <code>all</code> to pick every target across all pages."),
}


def picker(title, items, page, *, multi, emoji="📂"):
    """Numbered list ``title (a–b of n)`` with a reply hint; ``items`` are ``(label, note)``; ``title`` is HTML."""
    start, shown = page_of(items, page)
    lines = [f"{emoji} <b>{title}</b>  <i>({start + 1}–{start + len(shown)} of {len(items)})</i>", ""]
    lines += [f"<b>{start + i}.</b> {esc(label)}" + (f"  ·  <i>{esc(note)}</i>" if note else "")
              for i, (label, note) in enumerate(shown, 1)]
    hint = HINTS["target" if multi else "project"]
    more = start + len(shown) < len(items)
    tail = "\nTap <b>More ▸</b> or reply <code>more</code> for the next 5." if more else ""
    return "\n".join(lines + ["", "💡 " + hint + tail]), more


def buttons(items, page, *, multi):
    """Inline keyboard: one button per shown number, then All / More."""
    start, shown = page_of(items, page)
    rows = [[{"text": str(start + i), "callback_data": f"pick:{start + i}"} for i in range(1, len(shown) + 1)]]
    extra = ([{"text": "✅ All", "callback_data": "pick:all"}] if multi and len(items) > 1 else [])
    if start + len(shown) < len(items):
        extra.append({"text": "More ▸", "callback_data": "pick:more"})
    return {"inline_keyboard": rows + ([extra] if extra else [])}


def log_block(header, lines):
    """``header`` (HTML) and the newest log ``lines`` that fit one message, in a ``<pre>`` block."""
    kept, used = [], 0
    for line in reversed(lines):
        line = esc(line)
        if used + len(line) + 1 > BODY:
            break
        kept.append(line)
        used += len(line) + 1
    body = "\n".join(reversed(kept))
    return header + "\n\n" + (f"<pre>{body}</pre>" if body else "<i>Log is empty.</i>")


# -- Markdown → Telegram HTML -------------------------------------------------

_FENCE = re.compile(r"^\s*(```|~~~)")
_INLINE = [
    (re.compile(r"\*\*(.+?)\*\*|__(.+?)__"), lambda m: f"<b>{m.group(1) or m.group(2)}</b>"),
    (re.compile(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?!\w)|(?<![\w_])_(?!\s)(.+?)(?<!\s)_(?!\w)"),
     lambda m: f"<i>{m.group(1) or m.group(2)}</i>"),
    (re.compile(r"~~(.+?)~~"), lambda m: f"<s>{m.group(1)}</s>"),
    (re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)"), lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>'),
]


def _inline(text):
    """Escape one line and apply inline Markdown; code spans are kept verbatim."""
    parts = re.split(r"(`[^`]+`)", text)
    out = []
    for part in parts:
        if len(part) > 2 and part.startswith("`") and part.endswith("`"):
            out.append(f"<code>{esc(part[1:-1])}</code>")
            continue
        part = esc(part)
        for pattern, repl in _INLINE:
            part = pattern.sub(repl, part)
        out.append(part)
    return "".join(out)


def _table(rows):
    """A Markdown table as aligned monospace text (Telegram has no tables)."""
    cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows
             if not re.fullmatch(r"\s*\|?[\s:|-]+\|?\s*", r)]
    width = [max(len(r[i]) if i < len(r) else 0 for r in cells) for i in range(max(map(len, cells)))]
    lines = ["  ".join((r[i] if i < len(r) else "").ljust(width[i]) for i in range(len(width))).rstrip()
             for r in cells]
    return "<pre>" + esc("\n".join(lines)) + "</pre>"


def markdown(text):
    """Telegram HTML for Markdown ``text``: headings, emphasis, code, lists, quotes, links and tables."""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL).strip("\n")
    out, fence, table = [], None, []
    for line in text.splitlines():
        if fence is not None:
            if _FENCE.match(line):
                out.append("<pre>" + esc("\n".join(fence)) + "</pre>")
                fence = None
            else:
                fence.append(line)
            continue
        if table and not line.lstrip().startswith("|"):
            out.append(_table(table))
            table = []
        if _FENCE.match(line):
            fence = []
        elif line.lstrip().startswith("|"):
            table.append(line)
        elif m := re.match(r"^(#{1,6})\s+(.*)", line):
            mark = "🔷 " if len(m.group(1)) <= 2 else "▸ "
            out.append(("" if not out or not out[-1] else "\n") + f"{mark}<b>{_inline(m.group(2))}</b>")
        elif m := re.match(r"^(\s*)[-*+]\s+\[( |x|X)\]\s+(.*)", line):
            out.append(f"{'  ' * (len(m.group(1)) // 2)}{'☑️' if m.group(2).strip() else '⬜'} {_inline(m.group(3))}")
        elif m := re.match(r"^(\s*)[-*+]\s+(.*)", line):
            out.append(f"{'  ' * (len(m.group(1)) // 2)}• {_inline(m.group(2))}")
        elif m := re.match(r"^(\s*)(\d+)[.)]\s+(.*)", line):
            out.append(f"{'  ' * (len(m.group(1)) // 2)}<b>{m.group(2)}.</b> {_inline(m.group(3))}")
        elif m := re.match(r"^\s*>\s?(.*)", line):
            out.append(f"<blockquote>{_inline(m.group(1))}</blockquote>")
        elif re.fullmatch(r"\s*([-*_])(\s*\1){2,}\s*", line):
            out.append(RULE)
        else:
            out.append(_inline(line))
    if fence is not None:
        out.append("<pre>" + esc("\n".join(fence)) + "</pre>")
    if table:
        out.append(_table(table))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def chunks(text, limit=BODY):
    """Split Markdown at blank lines or headings (never inside a code fence) into parts of ≤ ``limit`` chars."""
    parts, current, size, fenced = [], [], 0, False
    for line in text.splitlines():
        boundary = not fenced and (not line.strip() or line.startswith("#"))
        if boundary and size + len(line) > limit and current:
            parts.append("\n".join(current))
            current, size = [], 0
        if size + len(line) > limit * 1.5 and current:  # one huge block: cut anyway
            if fenced:
                current.append("```")
            parts.append("\n".join(current))
            current, size = (["```"] if fenced else []), 0
        current.append(line)
        size += len(line) + 1
        if _FENCE.match(line):
            fenced = not fenced
    if current:
        parts.append("\n".join(current))
    return [p.strip("\n") for p in parts if p.strip()]
