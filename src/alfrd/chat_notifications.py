"""Read-only, bounded notification projection of durable chat state.

These records identify work needing attention; they never authorize an action.
"""
import json
from pathlib import Path
from urllib.parse import quote, urlencode


def snapshot(root, project=None):
    from alfrd import chat
    from alfrd.mailbox import Mailbox

    out = []
    base = Path(root) / chat.CHAT_DIR
    for folder in sorted(base.iterdir()) if base.is_dir() else []:
        if not (folder / "session.json").is_file():
            continue
        try:
            out.extend(_session(chat, Mailbox, root, folder.name, project))
        except Exception:  # noqa: BLE001, S112 - one unreadable session must not hide the others
            continue
    return out


def _session(chat, Mailbox, root, name, project):
    out = []
    store = chat.session(root, name)
    decisions = store.decisions()
    for req in store.requests():
        rid = req["id"]
        link = "/studio/#/chat/" + quote(store.id, safe="") + "?" + urlencode({"p": project or str(root)})

        def add(identity, kind, title, body):
            out.append({"notification_id": f"{store.id}:{rid}:{identity}", "kind": kind,
                        "title": title, "body": str(body)[:1800], "link": link,
                        "plan": store.plan_id, "request": rid, "session": store.id,
                        "project": project or str(root), "status": req.get("status")})

        pending = Mailbox(root, store.id, rid).pending()["messages"]
        waiting = [d for d in decisions if d.get("request") == rid and d.get("status") == "pending"]
        for message in pending:
            payload = message["payload"]
            approval = message["kind"] == "permission_request"
            body = message.get("body") or message.get("subject") or ""
            if approval:
                prefix = str(payload.get("action_hash", "")).removeprefix("sha256:")[:12]
                body = (f"Approval {prefix}. {str(payload.get('action', ''))[:160]}: "
                        f"{str(payload.get('description', ''))[:700]}. "
                        f"Target: {str(payload.get('target', ''))[:400]}. Review in chat.")
            add(message["id"], "approval.needed" if approval else "decision.needed",
                "Approval needed" if approval else "Your answer is needed", body)
        for decision in waiting:
            if decision.get("needs") != "user" or decision.get("mailbox_message_id"):
                continue  # linked mail owns the identity, including after expiry/reply
            permission = decision.get("kind") == "permission"
            body = decision.get("question") or decision.get("reason") or "Open chat to review."
            if permission:
                scope = decision.get("requires") or {}
                body += " Required: " + ", ".join(str(x) for k in ("folders", "bash_commands") for x in scope.get(k, []))
            add(f"{decision['id']}:{decision.get('created', '')}",
                "permission.needed" if permission else "decision.needed",
                "Permission needed" if permission else "Your answer is needed", body)
        state = req.get("status")
        if state in chat.REQUEST_ACTIVE:
            for task in store.tasks():
                if task.get("request") == rid and task.get("status") == "failed":
                    attempt = (task.get("units") or [""])[-1]
                    add(f"task:{task['row']}:{attempt}:failed", "task.failed", "Task failed",
                        task.get("error") or task.get("title") or task["row"])
        if not pending and not waiting and state in ("done", "failed", "cancelled", "stopped"):
            # A runner's idle/finished state is deliberately irrelevant.
            if state == "done" and any((store.task(row) or {}).get("status") not in chat.TERMINAL for row in req.get("rows", [])):
                continue
            add(f"state:{state}:{req.get('finished', '')}", f"request.{state if state != 'done' else 'finished'}",
                f"Request {'finished' if state == 'done' else state}", req.get("error") or req.get("summary") or req.get("title") or rid)
    return out


class Changes:
    """One alert per durable identity/state, including across repeated polls."""
    def __init__(self, state=None):
        self.state = Path(state) if state else None
        try:
            saved = json.loads(self.state.read_text()) if self.state else []
        except (OSError, ValueError):
            saved = []
        self.seen = set(saved) if isinstance(saved, list) else set()

    def save(self):
        if self.state:
            self.state.parent.mkdir(parents=True, exist_ok=True)
            temp = self.state.with_suffix(".tmp")
            temp.write_text(json.dumps(sorted(self.seen)))
            temp.replace(self.state)

    def poll(self, root, project=None, *, baseline=False):
        records = snapshot(root, project)
        fresh = [r for r in records if r["notification_id"] not in self.seen]
        self.seen.update(r["notification_id"] for r in records)
        return [] if baseline else fresh
