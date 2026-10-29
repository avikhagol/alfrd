"""Telegram controls using the same project chat service as Studio.

Callback payloads are opaque, short, process-local capabilities; they never
accept project paths or session/task identifiers supplied by Telegram.
"""

import json
import secrets
from collections import Counter

from alfrd import chat


class ChatControl:
    def text_parts(self, text):
        """Lossless plain-text chunks, bounded in UTF-16 units (including emoji)."""
        text = str(text).replace(self.token, "<token>")
        part, size = [], 0
        for char in text:
            width = 2 if ord(char) > 0xFFFF else 1
            if size + width > 4000:
                self.call("sendMessage", {"chat_id": self.chat, "text": "".join(part)})
                part, size = [], 0
            part.append(char)
            size += width
        if part:
            self.call("sendMessage", {"chat_id": self.chat, "text": "".join(part)})

    def chat_menu(self, title, items):
        self.chat_buttons = {}
        rows = []
        for label, action in items:
            key = secrets.token_hex(8)
            self.chat_buttons[key] = action
            rows.append([{"text": label[:120], "callback_data": f"chat:{key}"}])
        self.send(title, reply_markup={"inline_keyboard": rows})

    def status_menu(self):
        self.chat_menu("📊 <b>Status menu</b>", [
            ("Active chat status", lambda: self.chat_details("status")),
            ("Chat tasks / progress", lambda: self.chat_details("tasks")),
            ("Chat logs (last 30 lines)", lambda: self.chat_details("logs")),
            ("Pipeline / targets", self.pick_project),
            ("Remembered pipeline status", lambda: self.status_command(["last"])),
            ("Pipeline logs", self.pick_log_target),
            ("Switch chat session", self.resume_chats),
            ("New chat session", self.new_chat),
        ])

    def new_chat(self):
        projects = self.plans.projects()
        if self.selected in projects:
            return self.create_chat(self.selected)
        if len(projects) == 1:
            return self.create_chat(next(iter(projects)))
        if not projects:
            return self.send("No projects remembered. Open a project in Studio first.")
        self.chat_menu("📂 <b>New chat: choose a project</b>", [
            (p["name"], lambda k=k: self.create_chat(k)) for k, p in projects.items()
        ])

    def create_chat(self, project):
        data = chat.create_session(self.plans.projects()[project]["root"])
        # Invalidate the creation button before a second click can create another.
        self.chat_buttons = {}
        self.activate_chat(project, data["id"])

    def activate_chat(self, project, sid):
        root = self.plans.projects()[project]["root"]
        store = chat.session(root, sid)
        data = store.load()
        key = json.dumps([project, sid])
        self.chat_cursors.setdefault(key, len(store.messages()))
        self.active_chat = {"project": project, "session": sid}
        self.picker = self.view = None
        self.remember(project, self.tracked if self.selected == project else [])
        self.text_parts(f"Active chat: {data['title']} ({sid}). Send a message to its orchestrator.")

    def resume_chats(self, page=0):
        found = []
        for key, project in self.plans.projects().items():
            for item in chat.list_sessions(project["root"]):
                at = (item.get("last_message") or {}).get("at") or item.get("created", "")
                state = ("needs your answer" if item.get("pending_user") else
                         "active" if item.get("active_requests") else item.get("plan_status") or "idle")
                found.append((at, key, item, state, project["name"]))
        found.sort(key=lambda row: row[0], reverse=True)
        if not found:
            return self.send("No chat sessions yet. Send /new.")
        items = []
        for at, key, item, state, name in found[page * 5:(page + 1) * 5]:
            label = f"{item['title'][:40]} · {name[:20]} · {at[:19]} · {state}"
            items.append((label, lambda k=key, s=item["id"]: self.activate_chat(k, s)))
        if page:
            items.append(("◂ Previous", lambda: self.resume_chats(page - 1)))
        if (page + 1) * 5 < len(found):
            items.append(("Next ▸", lambda: self.resume_chats(page + 1)))
        self.chat_menu(f"💬 <b>Recent chat sessions</b> · page {page + 1}", items)

    def chat_scope(self):
        if not self.active_chat:
            raise ValueError("No active chat. Send /new or /resume_chat.")
        project, sid = self.active_chat["project"], self.active_chat["session"]
        projects = self.plans.projects()
        if project not in projects:
            raise ValueError("The active chat's project is unavailable. Send /resume_chat.")
        return projects[project]["root"], sid

    def chat_prompt(self, text, message_id=None):
        root, sid = self.chat_scope()
        # Stable Telegram message identities use Studio's durable replay protection.
        cid = f"telegram.{self.chat}.{message_id}" if message_id is not None else None
        out = chat.post_message(root, sid, text, client_message_id=cid)
        request = out.get("request") or {}
        self.text_parts(f"Accepted for chat {sid}" + (f" · {request['id']}" if request.get("id") else "") + ".")
        self.relay_chat()

    def relay_chat(self):
        """Relay new durable messages for every activated session, including after switching."""
        projects = self.plans.projects()
        for key, cursor in list(self.chat_cursors.items()):
            try:
                project, sid = json.loads(key)
                if project not in projects:
                    continue
                store = chat.session(projects[project]["root"], sid)
                messages = store.messages()
            except (LookupError, ValueError, TypeError):
                continue
            for index, message in enumerate(messages[cursor:], cursor + 1):
                if message.get("role") != "user" and message.get("text"):
                    self.text_parts(f"💬 {store.load()['title']} ({sid})\n{message['text']}")
                self.chat_cursors[key] = index
                self.save_state()

    def chat_details(self, mode):
        root, sid = self.chat_scope()
        state = chat.session_state(root, sid)
        title = state["session"]["title"]
        if mode == "status":
            requests = "\n".join(f"{r['id']}: {r.get('status')}" for r in state["requests"])
            blockers = "\n".join(d.get("question") or d.get("reason") or d["id"] for d in state["blockers"])
            self.text_parts(f"{title} ({sid})\nPlan: {state['plan'].get('status')}\n"
                            + (requests or "No requests yet.")
                            + (f"\nNeeds attention (review in Studio):\n{blockers}" if blockers else ""))
            return
        tasks = state["tasks"]
        if mode == "tasks":
            counts = Counter(t.get("status") for t in tasks)
            self.text_parts(f"{title}: {dict(counts)}\n" + ("\n".join(
                f"{t['row']} · {(t.get('task') or {}).get('title') or t.get('kind')} · {t.get('status')}"
                for t in tasks) or "No tasks yet."))
        # Task log buttons also retrieve completed attempts via the scoped activity API.
        items = [(f"Logs: {t['row']}", lambda units=t["units"][-32:], r=root, s=sid: self.chat_logs(r, s, units))
                 for t in tasks if t.get("units")]
        if items:
            self.task_log_menu(items)
        elif mode == "logs":
            self.send("No task logs yet.")

    def task_log_menu(self, items, page=0):
        shown = items[page * 5:(page + 1) * 5]
        if page:
            shown += [("◂ Previous", lambda: self.task_log_menu(items, page - 1))]
        if (page + 1) * 5 < len(items):
            shown += [("Next ▸", lambda: self.task_log_menu(items, page + 1))]
        self.chat_menu("📜 <b>Task logs</b>", shown)

    def chat_logs(self, root, sid, units):
        data = chat.activity(root, sid, lines=30, units=units)
        for item in data["items"]:
            self.text_parts(f"{sid} · {item['row']} · {item['unit']}\n" +
                            ("\n".join(item.get("display_tail") or item.get("tail") or []) or "No log output yet."))
