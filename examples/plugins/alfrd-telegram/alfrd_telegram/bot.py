"""HTML-styled bot with chat authorization, pickers, handoff pushes and single-use control confirmations."""

import getpass
import json
import os
import platform
import re
import secrets
import socket
import sys
import time
from collections import Counter
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from alfrd import notifiers
from alfrd.api import status
from alfrd.runtime import scheduler

from . import view
from .view import esc

VERSION = "0.3.0"
HELP = (
    "🤖 <b>ALFRD commands</b>\n" + view.RULE + "\n\n"
    "📊 <b>Status</b>\n"
    "/status — your remembered project and targets\n"
    "/status last — same as /status\n"
    "/status new — pick a project, then targets\n"
    "/status NAME — pick targets in project NAME\n"
    "/status latest — most recently active project and target; keeps your choice\n"
    "/status more — the next 5 entries\n\n"
    "📜 <b>Runs and logs</b>\n"
    "/runs — recent plans in the selected project\n"
    "/log — pick a target, then a step: last 30 log lines\n"
    "/log TARGET STEP — the same, without the menu\n"
    "/handoff — the latest agent-loop handoff\n\n"
    "🎛️ <b>Control</b>\n"
    "/pause — finish active commands; start no new ones\n"
    "/resume — continue the plan\n\n"
    "ℹ️ <b>About</b>\n"
    "/whoareyou — the machine and user this bot runs on\n"
    "/help — show this list\n\n"
    "💡 Tap a list number or reply with it to pick one.\n"
    "For several targets, reply <code>1 3</code> or <code>1,3</code>; <b>All</b> picks every target "
    "across all pages.\n"
    "While a list is open, number replies select from it. Reply <code>more</code> for the next 5.\n"
    "Pause and resume ask for Yes/No before changing anything.\n"
    "🏁 When an agent loop ends, its final handoff is posted here."
)
#: Telegram's ``/`` menu (setMyCommands).
MENU = [("status", "Status of your targets"), ("log", "Pick a target and step: last log lines"),
        ("runs", "Recent plans"), ("handoff", "Latest agent-loop handoff"),
        ("pause", "Pause the plan (asks first)"), ("resume", "Resume the plan (asks first)"),
        ("whoareyou", "Machine and user this bot runs on"), ("help", "All commands")]
NO_PROJECT = "📂 No project selected. Send /status new to pick one."
COMMANDS = ("/status", "/runs", "/log", "/logs", "/handoff", "/pause", "/resume")
TERMINAL = ("finished", "failed", "cancelled", "interrupted")
#: Seconds between checks for ended agent loops, and the most messages one handoff may take.
WATCH_EVERY = 20
MAX_PARTS = 4
TAGS = re.compile(r"</?(b|i|u|s|code|pre|a|blockquote)\b[^>]*>")


class Plans:
    """In-process equivalents of plan status/log/list and pause/resume."""

    def __init__(self, service):
        self.service = service

    def projects(self):
        return {p.identifier: {"name": p.name, "root": p.root_path}
                for p in self.service.list_projects() if p.root_path}

    def status(self, root):
        return status.plan_status(root)

    def grid(self, root):
        """The latest plan with every target's cells and timings."""
        return status.plan_status(root, detail="full")

    def activity(self, root):
        """UTC stamp of the latest plan's newest start/finish (else its creation); "" without plans."""
        base = Path(root).expanduser().resolve()
        found = scheduler.list_plans(base)
        if not found:
            return ""
        units = scheduler.PlanDir(base, found[0]["id"]).units()
        stamps = [status.utc(found[0].get("created"))]
        stamps += [status.utc(u.get(k)) for u in units for k in ("started", "finished")]
        return max((s for s in stamps if s), default="")

    def runs(self, root):
        return status.list_plans(root)["plans"]

    def log(self, root, target, step):
        return status.log_tail(root, target=target, step=step, lines=30)["lines"]

    def control(self, root, plan, action):
        return scheduler.control(root, plan, action)

    def loops(self, root, limit=5):
        """Agent-loop plans among the newest ``limit`` plans: ``[{id, status}]``, newest first."""
        base = Path(root).expanduser().resolve()
        return [{"id": p["id"], "status": p.get("status")}
                for p in scheduler.list_plans(base)[:limit] if p.get("loop")]

    def handoff(self, root, plan):
        """The last handoff agent-loop ``plan`` published, or None.

        ``{target, unit, agent, iteration, iterations, text}``; ``text`` is the
        agent's response (the archived ``response.md``, else the published file).
        """
        base = Path(root).expanduser().resolve()
        folder = scheduler.PlanDir(base, plan)
        iterations = (folder.load().get("loop") or {}).get("iterations")
        for unit in reversed(folder.units()):
            info = unit.get("handoff") or {}
            if unit.get("status") != "done" or not info:
                continue
            for name in (info.get("response_file"), info.get("output")):
                path = Path(name) if name else None
                if path is not None and not path.is_absolute():
                    path = base / path
                if path is not None and path.is_file():
                    return {"target": unit.get("target") or info.get("target"), "unit": unit.get("id"),
                            "agent": unit.get("agent"), "iteration": unit.get("iteration"),
                            "iterations": iterations,
                            "text": path.read_text(encoding="utf-8", errors="replace")[:200_000]}
        return None


class Bot:
    def __init__(self, route: Mapping[str, Any], plans, *, log: Callable = print, state: Path | None = None):
        self.token = route["token"].strip()
        self.chat = str(route["chat_id"]).strip()
        self.plans, self.log = plans, log
        self.offset = 0
        self.pending = {}
        # Kept in ``state`` across restarts: the selected project and its tracked targets (row keys).
        self.state = Path(state) if state else None
        self.selected, self.tracked = None, []
        # Open list: {"kind": "project"|"target"|"log-target"|"log-step", "items": [(value, label, note)],
        # "page", "project"; log-step also "row" and "label"}
        self.picker = None
        self.view = None  # cards being paged: {"project", "keys", "page"}
        self.started = time.monotonic()
        # Agent-loop plans seen running, as ``(root, plan id)``: their handoff is posted when they end.
        self.watching, self.next_watch = set(), 0.0
        self._load()

    def _load(self):
        try:
            data = json.loads(self.state.read_text()) if self.state else None
        except (OSError, ValueError):
            return
        if isinstance(data, dict) and isinstance(data.get("project"), str):
            self.selected = data["project"]
            self.tracked = [k for k in data.get("targets") or [] if isinstance(k, str)]

    def remember(self, project, keys):
        self.selected, self.tracked = project, list(keys)
        if self.state is None:
            return
        try:
            self.state.parent.mkdir(parents=True, exist_ok=True)
            temp = self.state.with_suffix(".tmp")
            temp.write_text(json.dumps({"project": project, "targets": self.tracked}))
            temp.replace(self.state)
        except OSError as exc:
            self.log(f"Could not save the /status selection ({type(exc).__name__}).")

    def reason(self, exc):
        """``HTTP n``, or for a network error / ``ok: false`` the scrubbed cause (never the token or chat id)."""
        if exc.status:
            return f"HTTP {exc.status}"
        text = str(exc).replace(self.token, "<token>").replace(self.chat, "<chat>")
        return text.removeprefix("telegram ")[:200]

    def call(self, method, params, timeout=10):
        return notifiers.telegram_call(self.token, method, params, timeout)

    def send(self, text, **options):
        """Send HTML ``text``; if Telegram rejects the markup (HTTP 400), send it again as plain text."""
        # Scrub before clipping so truncation cannot leave a partial token.
        text = str(text).replace(self.token, "&lt;token&gt;")
        params = {"chat_id": self.chat, "text": notifiers._clip(text, 4096), "parse_mode": "HTML",
                  "link_preview_options": {"is_disabled": True}, **options}
        try:
            return self.call("sendMessage", params)
        except notifiers.TelegramError as exc:
            if exc.status != 400:
                raise
        params.pop("parse_mode")
        plain = view.html.unescape(TAGS.sub("", text))
        return self.call("sendMessage", {**params, "text": notifiers._clip(plain, 4096)})

    def announce(self):
        """Fill Telegram's ``/`` command menu; best effort."""
        try:
            self.call("setMyCommands", {"commands": [{"command": c, "description": d} for c, d in MENU]})
        except notifiers.TelegramError as exc:
            self.log(f"Could not set the command menu ({self.reason(exc)}).")

    def project(self, selector=None):
        """The selected project; ``selector`` (identifier or name, any case, unique prefix) selects another."""
        projects = self.plans.projects()
        if selector:
            wanted = selector.lower()
            exact = [k for k, p in projects.items() if wanted in (k.lower(), p["name"].lower())]
            prefix = [k for k, p in projects.items()
                      if k.lower().startswith(wanted) or p["name"].lower().startswith(wanted)]
            matches = exact or prefix
            if len(matches) != 1:
                raise ValueError(f"🔍 {'Several' if matches else 'No'} projects match {esc(repr(selector))}. "
                                 "Send /status new to pick from the list.")
            if matches[0] != self.selected:
                self.remember(matches[0], [])
        elif self.selected not in projects:
            if len(projects) != 1:
                raise ValueError(NO_PROJECT)
            self.remember(next(iter(projects)), [])
        return projects[self.selected]

    # -- /status --------------------------------------------------------------

    def _ranked_projects(self):
        """``[(identifier, project, last activity)]``, most recently active first."""
        found = []
        for key, project in self.plans.projects().items():
            try:
                active = self.plans.activity(project["root"])
            except Exception:  # noqa: BLE001 - a missing folder sorts last instead of failing the list
                active = ""
            found.append((key, project, active))
        return sorted(found, key=lambda item: item[2], reverse=True)

    def status_command(self, args):
        word = args[0].lower() if args else ""
        if word == "more":
            return self.more()
        if self.picker and self.answer(args):
            return None
        if len(args) > 1:
            return self.send("ℹ️ Use /status, /status last, /status new, /status latest, /status more "
                             "or /status NAME.")
        if word == "new":
            return self.pick_project()
        if word == "latest":
            return self.latest()
        if word not in ("", "last"):
            self.project(args[0])
            return self.pick_targets(self.selected)
        projects = self.plans.projects()
        if self.selected in projects and self.tracked:
            return self.show(self.selected, self.tracked)
        if word == "last" and self.selected not in projects:
            self.send("🗂️ Nothing remembered yet.")
        if self.selected in projects:
            return self.pick_targets(self.selected)
        return self.pick_project()

    def pick_project(self):
        self.picker = self.view = None
        ranked = self._ranked_projects()
        if not ranked:
            raise ValueError("📂 No projects remembered. Open the project in ALFRD Studio first.")
        if len(ranked) == 1:
            self.remember(ranked[0][0], [])
            return self.pick_targets(ranked[0][0])
        names = Counter(p["name"] for _, p, _ in ranked)
        items = [(key, p["name"] if names[p["name"]] == 1 else f"{p['name']} ({key})", view.ago(active))
                 for key, p, active in ranked]
        self.picker = {"kind": "project", "items": items, "page": 0}
        return self.send_picker()

    def pick_targets(self, key):
        self.picker = self.view = None
        project = self.plans.projects()[key]
        doc = self.plans.grid(project["root"])
        found = view.targets(doc)
        if not found:
            self.remember(key, [])
            return self.send(f"📁 <b>{esc(project['name'])}</b>: the latest plan has no targets.")
        if len(found) == 1:
            self.remember(key, [found[0]["key"]])
            return self.show(key, self.tracked)
        items = [(t["key"], t["label"], view.state(view.steps(doc, t["key"])[1])) for t in found]
        self.picker = {"kind": "target", "items": items, "page": 0, "project": key}
        return self.send_picker()

    def send_picker(self):
        picker = self.picker
        multi = picker["kind"] == "target"
        title, emoji = "Select project", "📂"
        if picker.get("project"):
            name = esc(self.plans.projects()[picker["project"]]["name"])
            title, emoji = {"target": (f"Select targets in {name}", "🎯"),
                            "log-target": (f"Logs · pick a target in {name}", "📜"),
                            "log-step": (f"Logs · {esc(picker.get('label', ''))} · pick a step", "📜"),
                            }[picker["kind"]]
        rows = [(label, note) for _, label, note in picker["items"]]
        text, _ = view.picker(title, rows, picker["page"], multi=multi, emoji=emoji)
        self.send(text, reply_markup=view.buttons(rows, picker["page"], multi=multi))

    def answer(self, words):
        """Answer the open list with numbers or ``all``; False when ``words`` aren't an answer."""
        tokens = " ".join(words).replace(",", " ").lower().split()
        picker = self.picker
        items = picker["items"]
        if picker["kind"] == "target" and tokens == ["all"]:
            chosen = [value for value, _, _ in items]
        elif tokens and all(t.isdigit() for t in tokens):
            numbers = list(dict.fromkeys(int(t) for t in tokens))
            if any(not 1 <= n <= len(items) for n in numbers):
                raise ValueError(f"🔢 Pick a number from 1 to {len(items)}.")
            if picker["kind"] != "target" and len(numbers) != 1:
                raise ValueError("🔢 Pick one project." if picker["kind"] == "project" else "🔢 Pick one.")
            chosen = [items[n - 1][0] for n in numbers]
        else:
            return False
        self.picker = None
        if picker["kind"] == "project":
            self.remember(chosen[0], [])
            self.pick_targets(chosen[0])
        elif picker["kind"] == "log-target":
            self.pick_log_step(picker["project"], chosen[0])
        elif picker["kind"] == "log-step":
            self.send_log(picker["project"], picker["row"], chosen[0], picker.get("label"))
        else:
            self.remember(picker["project"], chosen)
            self.show(picker["project"], chosen)
        return True

    def more(self):
        current = self.picker or self.view
        if current is None:
            return self.send("ℹ️ No list to continue. Send /status.")
        total = len(current["items"] if self.picker else current["keys"])
        if (current["page"] + 1) * view.PAGE >= total:
            return self.send("🏁 That was the whole list.")
        current["page"] += 1
        return self.send_picker() if self.picker else self.show(current["project"], current["keys"], current["page"])

    def show(self, key, keys, page=0):
        """Cards for ``keys`` in project ``key``, most recently active first, 5 per message."""
        self.picker = None
        project = self.plans.projects()[key]
        doc = self.plans.grid(project["root"])
        found = view.targets(doc)
        labels = {t["key"]: t["label"] for t in found}
        order = [t["key"] for t in found if t["key"] in keys] + [k for k in keys if k not in labels]
        self.view = {"project": key, "keys": order, "page": page}
        start, shown = view.page_of(order, page)
        text = "\n\n".join(view.card(project["name"], doc, k, labels.get(k)) for k in shown)
        if len(order) > view.PAGE:
            more = start + len(shown) < len(order)
            text += (f"\n\n📄 <i>Targets {start + 1}–{start + len(shown)} of {len(order)}"
                     + (" · /status more for the next 5.</i>" if more else ".</i>"))
        self.send(text)

    def latest(self):
        """Card of the most recently active target in the most recently active project; memory unchanged."""
        self.picker = self.view = None
        ranked = [item for item in self._ranked_projects() if item[2]]
        if not ranked:
            raise ValueError("💤 No runs yet.")
        _, project, _ = ranked[0]
        doc = self.plans.grid(project["root"])
        found = view.targets(doc)
        if not found:
            return self.send(f"📁 <b>{esc(project['name'])}</b>: the latest plan has no targets.")
        self.send(view.card(project["name"], doc, found[0]["key"], found[0]["label"]))

    # -- /log -----------------------------------------------------------------

    def pick_log_target(self):
        """Menu: the selected project's targets that have started a command (most recent first), then steps."""
        self.picker = self.view = None
        project = self.project()
        doc = self.plans.grid(project["root"])
        found = [t for t in view.targets(doc) if view.logged_steps(doc, t["key"])]
        if not found:
            return self.send(f"📁 <b>{esc(project['name'])}</b>: no command has started in the latest plan yet.")
        if len(found) == 1:
            return self.pick_log_step(self.selected, found[0]["key"], doc)
        items = [(t["key"], t["label"], view.state(view.steps(doc, t["key"])[1])) for t in found]
        self.picker = {"kind": "log-target", "items": items, "page": 0, "project": self.selected}
        return self.send_picker()

    def pick_log_step(self, key, row, doc=None):
        project = self.plans.projects()[key]
        doc = doc or self.plans.grid(project["root"])
        label = next((t["label"] for t in view.targets(doc) if t["key"] == row), row)
        found = view.logged_steps(doc, row)
        if len(found) == 1:
            return self.send_log(key, row, found[0][0], label)
        items = [(step, step, f"{view.icon(cell)} {cell}") for step, cell in found]
        self.picker = {"kind": "log-step", "items": items, "page": 0, "project": key, "row": row, "label": label}
        return self.send_picker()

    def send_log(self, key, target, step, label=None):
        self.picker = None
        project = self.plans.projects()[key]
        header = (f"📜 <b>{esc(project['name'])}</b> · 🎯 <b>{esc(label or target)}</b> / "
                  f"<code>{esc(step)}</code>")
        try:
            lines = self.plans.log(project["root"], target, step)[-30:]
        except status.StatusNotFound:
            return self.send(header + "\n\n<i>No log for this step yet.</i>")
        self.send(view.log_block(header, lines))

    # -- agent-loop handoffs ----------------------------------------------------

    def send_handoff(self, project, plan, state):
        """Post ``plan``'s final handoff as formatted messages (at most :data:`MAX_PARTS`)."""
        found = self.plans.handoff(project["root"], plan)
        head = [f"{view.icon(state)} <b>Agent loop {esc(state)}</b>",
                f"📁 <b>{esc(project['name'])}</b>"
                + (f" · 🎯 <b>{esc(found['target'])}</b>" if found and found.get("target") else ""),
                f"🧾 Plan <code>{esc(plan)}</code>"]
        if found:
            about = []
            if found.get("iteration"):
                about.append(f"turn {found['iteration']}/{found.get('iterations') or '?'}")
            if found.get("agent"):
                about.append(f"🤖 {esc(found['agent'])}")
            if about:
                head.append(" · ".join(about))
        head.append(view.RULE)
        if not found:
            return self.send("\n".join(head) + "\n\n<i>No handoff was published.</i>")
        parts = view.chunks(found["text"])
        for number, part in enumerate(parts[:MAX_PARTS], 1):
            lead = "\n".join(head) if number == 1 else f"<i>📄 Handoff, part {number}/{len(parts)}</i>"
            self.send(lead + "\n\n" + view.markdown(part))
        if len(parts) > MAX_PARTS:
            self.send(f"✂️ <i>{len(parts) - MAX_PARTS} more part(s): open the plan's handoff in Studio.</i>")

    def latest_handoff(self):
        project = self.project()
        loops = self.plans.loops(project["root"])
        if not loops:
            return self.send(f"📁 <b>{esc(project['name'])}</b>: no agent-loop plans yet.")
        self.send_handoff(project, loops[0]["id"], loops[0]["status"])

    def watch(self):
        """At most every :data:`WATCH_EVERY` s: post the handoff of each agent loop seen running that has ended."""
        now = time.monotonic()
        if now < self.next_watch:
            return
        self.next_watch = now + WATCH_EVERY
        for project in self.plans.projects().values():
            try:
                loops = self.plans.loops(project["root"])
            except Exception:  # noqa: BLE001, S112 - a missing project folder must not stop the others
                continue
            for plan in loops:
                seen = (project["root"], plan["id"])
                if plan["status"] not in TERMINAL:
                    self.watching.add(seen)
                elif seen in self.watching:
                    self.watching.discard(seen)
                    self.guarded(lambda p=project, i=plan["id"], s=plan["status"]: self.send_handoff(p, i, s))

    def checked_watch(self):
        """:meth:`watch` and :meth:`flush`, whose failures are logged: only a getUpdates error may stop the bot."""
        try:
            self.watch()
        except notifiers.TelegramError as exc:
            self.log(f"Telegram reply failed ({self.reason(exc)}).")
        except Exception as exc:  # noqa: BLE001 - a broken project must not stop the bot
            self.log(f"Handoff check skipped ({type(exc).__name__}).")
        try:
            self.flush()
        except notifiers.TelegramError as exc:
            self.log(f"Digest not sent yet ({self.reason(exc)}).")
        except Exception as exc:  # noqa: BLE001 - a broken outbox must not stop the bot
            self.log(f"Digest check skipped ({type(exc).__name__}).")

    def flush(self):
        """Send held notifications (Settings → Telegram → Digest) when due; waits while the Studio is in use."""
        from alfrd import presence

        saved = notifiers.telegram_settings()
        if saved.get("mute_when_active") is True and presence.active():
            return 0
        hold = notifiers.hold_minutes(saved.get("hold_minutes"))
        # With the digest switched off, whatever is still held goes out now.
        return notifiers.telegram_flush(self.token, self.chat, hold * 60, force=hold == 0)

    # -- /whoareyou -------------------------------------------------------------

    def whoami(self):
        """Which machine, user and ALFRD answer here: several machines may share one bot."""
        from alfrd import __version__, get_alfrd_dir

        try:
            user = getpass.getuser()
        except Exception:  # noqa: BLE001 - no login name in some containers
            user = str(os.getuid()) if hasattr(os, "getuid") else "?"
        projects = self.plans.projects()
        current = projects.get(self.selected, {}).get("name") if self.selected else None
        self.send("\n".join([
            "🤖 <b>ALFRD bot</b> · who am I", view.RULE,
            f"🖥️ <b>Host</b>  <code>{esc(socket.gethostname())}</code>",
            f"👤 <b>User</b>  <code>{esc(user)}</code>",
            f"💻 <b>System</b>  {esc(platform.system())} {esc(platform.release())} · {esc(platform.machine())}",
            f"🐍 <b>Python</b>  {esc(platform.python_version())} · <code>{esc(sys.executable)}</code>",
            f"📦 <b>ALFRD</b>  {esc(__version__)} · Telegram plugin {VERSION}",
            f"🏠 <b>ALFRD folder</b>  <code>{esc(get_alfrd_dir())}</code>",
            f"🆔 <b>PID</b>  {os.getpid()} · up {view.elapsed(time.monotonic() - self.started)}",
            f"📁 <b>Projects</b>  {len(projects)}" + (f" · selected <b>{esc(current)}</b>" if current else ""),
        ]))

    # -- commands -------------------------------------------------------------

    def command(self, text):
        words = text.split()
        if not words:
            return
        if not words[0].startswith("/"):
            # A bare reply answers the open list ("2", "1 3", "all", "more").
            if [w.lower() for w in words] == ["more"] and (self.picker or self.view):
                self.guarded(self.more)
                return
            if self.picker:
                self.guarded(lambda: self.answer(words) or self.send(
                    "🔢 Reply with a number from the list, or send /status new."))
                return
            self.send("🤔 Unknown command. Use /help.")
            return
        command = words[0].split("@")[0].lower()
        args = words[1:]
        if command == "/help" or command == "/start":
            self.send(HELP)
            return
        if command in ("/whoareyou", "/whoami"):
            self.guarded(self.whoami)
            return
        if command not in COMMANDS:
            self.send("🤔 Unknown command. Use /help.")
            return
        if command == "/status":
            self.guarded(lambda: self.status_command(args))
            return
        if command in ("/log", "/logs") and not args:
            self.guarded(self.pick_log_target)
            return
        if command == "/handoff" and not args:
            self.guarded(self.latest_handoff)
            return
        expected = 2 if command in ("/log", "/logs") else 0
        if len(args) != expected:
            self.send("ℹ️ Use /status, /runs, /log, /log TARGET STEP, /handoff, /pause or /resume.")
            return
        self.guarded(lambda: self.plan_command(command, args))

    def guarded(self, action):
        try:
            action()
        except notifiers.TelegramError:
            raise
        except ValueError as exc:
            self.send(str(exc))
        except status.StatusNotFound:
            self.send("💤 This project has no plans yet.")
        except Exception:  # noqa: BLE001 - do not disclose backend errors to Telegram
            # Backend errors can contain file contents or credentials. Never echo them.
            self.send("⚠️ Could not read or change this plan. Check it in Studio.")

    def plan_command(self, command, args):
        project = self.project()
        root, name = project["root"], project["name"]
        if command == "/runs":
            runs = self.plans.runs(root)[:10]
            self.send(f"🗂️ <b>{esc(name)}</b> · recent plans\n{view.RULE}\n\n" + ("\n".join(
                f"{view.icon(p['status'])} <code>{esc(p['id'])}</code> · {esc(p['status'])}" for p in runs)
                or "<i>No plans yet.</i>"))
        elif command in ("/log", "/logs"):
            self.send_log(self.selected, *args)
        else:
            plan = self.plans.status(root)["plan"]
            action = command[1:]
            key = secrets.token_hex(8)
            # One outstanding confirmation; a new request invalidates the old one.
            self.pending = {key: (root, plan["id"], action, time.monotonic() + 300)}
            self.send(f"{'⏸️' if action == 'pause' else '▶️'} <b>{action.capitalize()} {esc(name)}?</b>\n"
                      f"🧾 Plan <code>{esc(plan['id'])}</code>\n\n"
                      + ("Active commands will finish; no new commands will start." if action == "pause"
                         else "The plan will continue. Failed cells are kept as they are."),
                      reply_markup={"inline_keyboard": [[
                          {"text": "✅ Yes", "callback_data": f"yes:{key}"},
                          {"text": "✖️ No", "callback_data": f"no:{key}"}]]})

    def callback(self, query):
        # Acknowledge every callback in the authorized chat, including stale ones.
        self.call("answerCallbackQuery", {"callback_query_id": query["id"]})
        choice, _, key = str(query.get("data", "")).partition(":")
        if choice == "pick":
            if self.picker is None and not (key == "more" and self.view):
                self.send("⌛ This list expired. Send /status new.")
            else:
                self.guarded(self.more if key == "more" else lambda: self.answer([key]))
            return
        if choice not in ("yes", "no"):
            return
        pending = self.pending.pop(key, None)
        if pending is None or pending[3] < time.monotonic():
            self.send("⌛ This confirmation expired. Send /pause or /resume again.")
            return
        if choice == "no":
            self.send("✖️ Cancelled. No change requested.")
            return
        root, plan, action, _ = pending
        try:
            self.plans.control(root, plan, action)
        except Exception:  # noqa: BLE001 - do not disclose backend errors to Telegram
            self.send("⚠️ Could not change this plan. Check it in Studio.")
        else:
            self.send(f"{'⏸️' if action == 'pause' else '▶️'} <b>{action.capitalize()} requested.</b>\n"
                      f"🧾 Plan <code>{esc(plan)}</code>")

    def handle(self, update):
        query = update.get("callback_query")
        message = query.get("message", {}) if query else update.get("message", {})
        if str(message.get("chat", {}).get("id", "")) != self.chat:
            return
        if query:
            self.callback(query)
        elif isinstance(message.get("text"), str):
            self.command(message["text"])

    def poll(self):
        updates = self.call("getUpdates", {
            "offset": self.offset, "timeout": 50,
            "allowed_updates": ["message", "callback_query"],
        }, timeout=60)
        for update in updates or []:
            self.offset = max(self.offset, update["update_id"] + 1)
            try:
                self.handle(update)
            except notifiers.TelegramError as exc:
                # A rejected reply (e.g. a stale callback query) must not stop polling.
                self.log(f"Telegram reply failed ({self.reason(exc)}).")
            except Exception as exc:  # noqa: BLE001 - one odd update must not stop the bot
                self.log(f"Update skipped ({type(exc).__name__}).")

    def run(self):
        """Poll until a fatal getUpdates error; returns its HTTP status."""
        while True:
            try:
                self.poll()
                self.checked_watch()
            except notifiers.TelegramError as exc:
                self.log(f"Telegram API unavailable ({self.reason(exc)}).")
                if exc.status in (400, 401, 403, 404, 409):
                    return exc.status
                time.sleep(3)
