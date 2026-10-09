"""Plain-text bot with chat authorization and single-use control confirmations."""

import json
import secrets
import time
from collections import Counter
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from alfrd import notifiers
from alfrd.api import status
from alfrd.runtime import scheduler

from . import view

HELP = (
    "ALFRD commands\n\n"
    "/status — your remembered project and targets\n"
    "/status last — same as /status\n"
    "/status new — pick a project, then targets\n"
    "/status NAME — pick targets in project NAME\n"
    "/status latest — most recently active project and target; keeps your choice\n"
    "/status more — the next 5 entries\n"
    "/runs — recent plans in the selected project\n"
    "/log TARGET STEP — last 30 log lines\n"
    "/pause — finish active commands; start no new ones\n"
    "/resume — continue the plan\n"
    "/help — show this list\n\n"
    "Tap a list number or reply with it to pick one.\n"
    "For several targets, reply 1 3 or 1,3; All picks every target across all pages.\n"
    "While a list is open, number replies select from it. Reply more for the next 5.\n"
    "Pause and resume ask for Yes/No before changing anything."
)
NO_PROJECT = "No project selected. Send /status new to pick one."


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
        self.picker = None  # open list: {"kind": "project"|"target", "items": [(value, label, note)], "page", "project"}
        self.view = None  # cards being paged: {"project", "keys", "page"}
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
        # Scrub before clipping so truncation cannot leave a partial token.
        text = str(text).replace(self.token, "<token>")
        return self.call("sendMessage", {
            "chat_id": self.chat, "text": notifiers._clip(text, 4096),
            "link_preview_options": {"is_disabled": True}, **options,
        })

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
                raise ValueError(f"{'Several' if matches else 'No'} projects match {selector!r}. "
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
            return self.send("Use /status, /status last, /status new, /status latest, /status more or /status NAME.")
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
            self.send("Nothing remembered yet.")
        if self.selected in projects:
            return self.pick_targets(self.selected)
        return self.pick_project()

    def pick_project(self):
        self.picker = self.view = None
        ranked = self._ranked_projects()
        if not ranked:
            raise ValueError("No projects remembered. Open the project in ALFRD Studio first.")
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
            return self.send(f"{project['name']}: the latest plan has no targets.")
        if len(found) == 1:
            self.remember(key, [found[0]["key"]])
            return self.show(key, self.tracked)
        items = [(t["key"], t["label"], view.state(view.steps(doc, t["key"])[1])) for t in found]
        self.picker = {"kind": "target", "items": items, "page": 0, "project": key}
        return self.send_picker()

    def send_picker(self):
        picker = self.picker
        multi = picker["kind"] == "target"
        title = "Select project"
        if multi:
            title = f"Select targets in {self.plans.projects()[picker['project']]['name']}"
        rows = [(label, note) for _, label, note in picker["items"]]
        text, _ = view.picker(title, rows, picker["page"], multi=multi)
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
                raise ValueError(f"Pick a number from 1 to {len(items)}.")
            if picker["kind"] == "project" and len(numbers) != 1:
                raise ValueError("Pick one project.")
            chosen = [items[n - 1][0] for n in numbers]
        else:
            return False
        self.picker = None
        if picker["kind"] == "project":
            self.remember(chosen[0], [])
            self.pick_targets(chosen[0])
        else:
            self.remember(picker["project"], chosen)
            self.show(picker["project"], chosen)
        return True

    def more(self):
        current = self.picker or self.view
        if current is None:
            return self.send("No list to continue. Send /status.")
        total = len(current["items"] if self.picker else current["keys"])
        if (current["page"] + 1) * view.PAGE >= total:
            return self.send("That was the whole list.")
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
            text += (f"\n\nTargets {start + 1}–{start + len(shown)} of {len(order)}"
                     + (" · /status more for the next 5." if more else "."))
        self.send(text)

    def latest(self):
        """Card of the most recently active target in the most recently active project; memory unchanged."""
        self.picker = self.view = None
        ranked = [item for item in self._ranked_projects() if item[2]]
        if not ranked:
            raise ValueError("No runs yet.")
        _, project, _ = ranked[0]
        doc = self.plans.grid(project["root"])
        found = view.targets(doc)
        if not found:
            return self.send(f"{project['name']}: the latest plan has no targets.")
        self.send(view.card(project["name"], doc, found[0]["key"], found[0]["label"]))

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
                    "Reply with a number from the list, or send /status new."))
                return
            self.send("Unknown command. Use /help.")
            return
        command = words[0].split("@")[0]
        args = words[1:]
        if command == "/help" or command == "/start":
            self.send(HELP)
            return
        if command not in ("/status", "/runs", "/log", "/pause", "/resume"):
            self.send("Unknown command. Use /help.")
            return
        if command == "/status":
            self.guarded(lambda: self.status_command(args))
            return
        expected = 2 if command == "/log" else 0
        if len(args) != expected:
            self.send("Use /status, /runs, /log TARGET STEP, /pause or /resume.")
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
            self.send("This project has no plans yet.")
        except Exception:  # noqa: BLE001 - do not disclose backend errors to Telegram
            # Backend errors can contain file contents or credentials. Never echo them.
            self.send("Could not read or change this plan. Check it in Studio.")

    def plan_command(self, command, args):
        project = self.project()
        root, name = project["root"], project["name"]
        if command == "/runs":
            runs = self.plans.runs(root)[:10]
            self.send(f"{name} · recent plans\n\n" + ("\n".join(
                f"{p['id']} · {p['status']}" for p in runs) or "No plans yet."))
        elif command == "/log":
            lines = self.plans.log(root, *args)[-30:]
            self.send(f"{name} · {args[0]} / {args[1]}\n\n" + ("\n".join(lines) or "Log is empty."))
        else:
            plan = self.plans.status(root)["plan"]
            action = command[1:]
            key = secrets.token_hex(8)
            # One outstanding confirmation; a new request invalidates the old one.
            self.pending = {key: (root, plan["id"], action, time.monotonic() + 300)}
            self.send(f"{action.capitalize()} {name}?\nPlan {plan['id']}\n\n"
                      + ("Active commands will finish; no new commands will start." if action == "pause"
                         else "The plan will continue. Failed cells are kept as they are."),
                      reply_markup={"inline_keyboard": [[
                          {"text": "Yes", "callback_data": f"yes:{key}"},
                          {"text": "No", "callback_data": f"no:{key}"}]]})

    def callback(self, query):
        # Acknowledge every callback in the authorized chat, including stale ones.
        self.call("answerCallbackQuery", {"callback_query_id": query["id"]})
        choice, _, key = str(query.get("data", "")).partition(":")
        if choice == "pick":
            if self.picker is None and not (key == "more" and self.view):
                self.send("This list expired. Send /status new.")
            else:
                self.guarded(self.more if key == "more" else lambda: self.answer([key]))
            return
        if choice not in ("yes", "no"):
            return
        pending = self.pending.pop(key, None)
        if pending is None or pending[3] < time.monotonic():
            self.send("This confirmation expired. Send /pause or /resume again.")
            return
        if choice == "no":
            self.send("Cancelled. No change requested.")
            return
        root, plan, action, _ = pending
        try:
            self.plans.control(root, plan, action)
        except Exception:  # noqa: BLE001 - do not disclose backend errors to Telegram
            self.send("Could not change this plan. Check it in Studio.")
        else:
            self.send(f"{action.capitalize()} requested.\nPlan {plan}")

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
            except notifiers.TelegramError as exc:
                self.log(f"Telegram API unavailable ({self.reason(exc)}).")
                if exc.status in (400, 401, 403, 404, 409):
                    return exc.status
                time.sleep(3)
