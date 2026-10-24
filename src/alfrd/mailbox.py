"""Durable per-request mail shared by Studio and cooperative agent commands.

JSONL is authoritative; the atomic checkpoint is disposable. All operations replay
under the same OS lock, including recovery of interrupted trailing appends.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from functools import lru_cache

import fcntl
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from alfrd.mailbox_codes import defaults

PROTOCOL_V1 = "alfrd.mailbox/1"
PROTOCOL = "alfrd.mailbox/2"
USER = {"kind": "user"}
_REPLAY = {}  # path -> (inode, validated offset, last validated line, records)
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}\Z")


class MailboxError(ValueError):
    def __init__(self, message, status=400, **details):
        super().__init__(message)
        self.status, self.details = status, details


def identifier(value):
    if not isinstance(value, str) or not _ID.fullmatch(value) or value in (".", ".."):
        raise MailboxError("invalid mailbox identifier")
    return value


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def action_hash(payload):
    return "sha256:" + hashlib.sha256(canonical({k: payload.get(k, {} if k == "arguments" else None)
                                               for k in ("action", "target", "arguments", "scope")}).encode()).hexdigest()


@lru_cache(maxsize=4)
def _validator(name, version="v1"):
    schema = json.loads((Path(__file__).parent / "schemas" / f"{name}.{version}.json").read_text())
    return Draft202012Validator(schema, format_checker=FormatChecker())


def validate(name, value):
    protocol = value.get("protocol") if isinstance(value, dict) else None
    if protocol not in (PROTOCOL_V1, PROTOCOL):
        raise MailboxError("unsupported mailbox protocol")
    try:
        _validator(name, "v2" if protocol == PROTOCOL else "v1").validate(value)
    except ValidationError as exc:
        field = ".".join(str(part) for part in exc.absolute_path) or "<root>"
        kind = value.get("kind", name) if isinstance(value, dict) else name
        raise MailboxError(f"{kind} message field {field}: {exc.message}") from exc
    # Bound the whole record, including free-form info payloads and nested values.
    if len(canonical(value).encode()) > 32768:
        raise MailboxError("message exceeds 32 KiB", 413)


def matches(recipient, actor):
    return all(actor.get(k) == v for k, v in recipient.items())


class Mailbox:
    def __init__(self, root, session_id, run_id):
        from alfrd import chat
        self.session = chat.session(root, identifier(session_id))
        self.run_id = identifier(run_id)
        # Missing means a request created before v2, even if its mailbox is empty.
        self.protocol = self.session.request(self.run_id).get("mailbox_protocol", PROTOCOL_V1)
        if self.protocol not in (PROTOCOL_V1, PROTOCOL):
            raise MailboxError("unsupported request mailbox protocol")
        self.dir = self.session.root / ".alfrd" / "plans" / self.session.plan_id / "mailbox" / self.run_id

    @contextlib.contextmanager
    def locked(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        with open(self.dir / "mailbox.lock", "a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                self.messages = self._read("messages")
                self.events = self._read("events")
                self._reindex()
                self._repair_resolutions()
                self._expire()
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _read(self, name):
        path = self.dir / f"{name}.jsonl"
        if not path.exists():
            return []
        records, last = [], b""
        with open(path, "r+b") as stream:
            # Validated prefix cached per process: replay only what was appended since. The
            # cache is dropped if the file was replaced, shrank, or its last known line changed.
            cached = _REPLAY.get(str(path))
            if cached:
                inode, offset, last, known = cached
                st = os.fstat(stream.fileno())
                stream.seek(offset - len(last))
                if inode == st.st_ino and offset <= st.st_size and stream.read(len(last)) == last:
                    records = list(known)
                else:
                    stream.seek(0)
                    last = b""
            while True:
                start = stream.tell()
                raw = stream.readline()
                if not raw:
                    break
                if not raw.endswith(b"\n"):
                    stream.truncate(start)
                    stream.flush()
                    os.fsync(stream.fileno())
                    break
                try:
                    value = json.loads(raw)
                    validate("agent_message" if name == "messages" else "agent_mailbox_event", value)
                    position = "seq" if name == "messages" else "event_seq"
                    if value[position] != len(records) + 1:
                        raise ValueError("non-monotonic sequence")
                    records.append(value)
                    last = raw
                except (ValueError, UnicodeError, KeyError) as exc:
                    raise MailboxError(f"corrupt complete mailbox record in {path.name}", 500) from exc
            _REPLAY[str(path)] = (os.fstat(stream.fileno()).st_ino, start, last, list(records))
        return records

    def _append(self, name, value):
        validate("agent_message" if name == "messages" else "agent_mailbox_event", value)
        with open(self.dir / f"{name}.jsonl", "ab") as stream:
            stream.write((canonical(value) + "\n").encode())
            stream.flush()
            os.fsync(stream.fileno())
        getattr(self, name).append(value)
        if name == "events":
            self._by_message.setdefault(value["message_id"], []).append(value)
        else:
            self._by_id[value["id"]] = value
        checkpoint = self.dir / "state.tmp"
        with checkpoint.open("w", encoding="utf-8") as stream:
            stream.write(canonical({"messages": len(self.messages), "events": len(self.events)}))
            stream.flush()
            os.fsync(stream.fileno())
        checkpoint.replace(self.dir / "state.json")
        directory = os.open(self.dir, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def _event(self, kind, message, actor, data):
        event = {"protocol": self.protocol, "event_seq": len(self.events) + 1, "type": kind,
                 "message_id": message["id"], "actor": actor, "at": now(), "data": data}
        self._append("events", event)
        return event

    def _repair_resolutions(self):
        # The reply is the commit record. Repair a crash between its append and the event.
        resolved = {e["message_id"] for e in self.events if e["type"] == "resolved"}
        for reply in self.messages:
            if reply["kind"] == "reply" and reply["reply_to"] not in resolved:
                original = self._find(reply["reply_to"])
                self._event("resolved", original, reply["sender"], {"reply_id": reply["id"]})
                resolved.add(original["id"])

    def _expire(self):
        for message in self.messages:
            if message["kind"] in ("question", "permission_request") and message["expires_at"] and \
                    (self._state(message)["state"] == "pending" or
                     (message["kind"] == "permission_request" and self._state(message)["state"] == "resolved")) and \
                    datetime.fromisoformat(message["expires_at"].replace("Z", "+00:00")) <= datetime.now(timezone.utc):
                self._event("expired", message, {"kind": "orchestrator"}, {})

    def _reindex(self):
        # Derived from the JSONL on every lock and never persisted, so it cannot go stale.
        self._by_id = {m["id"]: m for m in self.messages}
        self._by_message = {}
        for event in self.events:
            self._by_message.setdefault(event["message_id"], []).append(event)

    def _state(self, message):
        events = [e for e in self._by_message.get(message["id"], ()) if
                  (e["type"] in ("resolved", "expired", "cancelled") or
                   (e["type"] == "ack" and message["kind"] == "info"))]
        event = events[-1] if events else None
        return {"state": ("acked" if event["type"] == "ack" else event["type"]) if event else "pending", "version": len(events) + 1,
                "resolution": event}

    def _project(self, message):
        original = self._find(message["reply_to"]) if message["kind"] == "reply" else None
        return {**message, **defaults(message, original)}

    def _find(self, mid):
        return self._by_id.get(mid) or self._missing()

    def _missing(self):
        raise MailboxError("unknown scoped mailbox message", 404)

    def _cursor(self, m=None, e=None):
        return base64.urlsafe_b64encode(canonical([self.session.id, self.run_id, len(self.messages) if m is None else m,
                                                   len(self.events) if e is None else e]).encode()).decode().rstrip("=")

    def _positions(self, cursor):
        if not cursor:
            return 0, 0
        try:
            session_id, run, m, e = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
            if session_id != self.session.id or run != self.run_id or type(m) is not int or type(e) is not int or not 0 <= m <= len(self.messages) or not 0 <= e <= len(self.events):
                raise ValueError()
            return m, e
        except (ValueError, TypeError) as exc:
            raise MailboxError("invalid mailbox cursor") from exc

    def _active(self):
        from alfrd.chat import REQUEST_ACTIVE
        if self.session.request(self.run_id).get("status") not in REQUEST_ACTIVE:
            raise MailboxError("run is terminal", 409)

    def _recipient(self, recipient):
        if not isinstance(recipient, dict):
            raise MailboxError("recipient must be an actor")
        if recipient.get("kind") == "agent":
            task = self.session.task(f"{self.run_id}.{recipient.get('task_id')}")
            if not task or task["owner"] != recipient.get("agent_id"):
                raise MailboxError("unknown recipient task", 404)
            if recipient.get("unit_id") and recipient["unit_id"] not in task.get("units", []):
                raise MailboxError("unknown recipient attempt", 404)

    def _put(self, actor, payload):
        if not isinstance(payload, dict):
            raise MailboxError("message must be an object")
        fields = {"recipient", "kind", "subject", "body", "reply_to", "correlation_id", "idempotency_key", "expires_at", "payload"}
        if self.protocol == PROTOCOL:
            fields |= {"code", "category", "priority", "summary", "description", "step_id"}
        if set(payload) - fields:
            raise MailboxError("client supplied store-owned or unknown fields")
        message = {"reply_to": None, "correlation_id": None, "expires_at": None, **payload,
                   "protocol": self.protocol, "id": "msg_" + uuid.uuid4().hex, "seq": len(self.messages) + 1,
                   "run_id": self.run_id, "created_at": now(), "sender": actor}
        validate("agent_message", message)
        if self.protocol == PROTOCOL:
            message = self._project(message)
            validate("agent_message", message)
        if message["kind"] == "permission_request" and message["payload"]["action_hash"] != action_hash(message["payload"]):
            raise MailboxError("action_hash does not match exact action inputs")
        if message["kind"] == "question":
            ids = [o["id"] for o in message["payload"]["options"]]
            if len(set(ids)) != len(ids):
                raise MailboxError("duplicate question option ids")
        owned = {"id", "seq", "created_at"}
        for old in self.messages:
            if old["sender"] == actor and old["idempotency_key"] == message["idempotency_key"]:
                if {k: v for k, v in old.items() if k not in owned} != {k: v for k, v in message.items() if k not in owned}:
                    raise MailboxError("idempotency key reused with different content", 409)
                return old, False
        self._active()
        self._recipient(message["recipient"])
        self._append("messages", message)
        return message, True

    def send(self, actor, payload):
        if not isinstance(payload, dict):
            raise MailboxError("message must be an object")
        if payload.get("kind") == "reply":
            raise MailboxError("use the reply operation")
        with self.locked():
            message, created = self._put(actor, payload)
            return {"message": message, "cursor": self._cursor(), "created": created}

    def _visible(self, message, actor):
        return actor == USER or matches(message["recipient"], actor) or message["sender"] == actor

    def list(self, actor=USER, after=None, limit=100):
        if not 1 <= limit <= 200:
            raise MailboxError("limit must be between 1 and 200")
        with self.locked():
            m, e = self._positions(after)
            messages, events = [], []
            response_bytes = 0
            while m < len(self.messages) and len(messages) < limit:
                item = self.messages[m]
                if self._visible(item, actor):
                    record = {**self._project(item), **self._state(item)}
                    size = len(canonical(record).encode())
                    if response_bytes + size > 256 * 1024:
                        break
                    messages.append(record)
                    response_bytes += size
                m += 1
            while e < len(self.events) and len(messages) + len(events) < limit:
                item = self.events[e]
                if self._visible(self._find(item["message_id"]), actor):
                    size = len(canonical(item).encode())
                    if response_bytes + size > 256 * 1024:
                        break
                    events.append(item)
                    response_bytes += size
                e += 1
            return {"messages": messages, "events": events, "next_cursor": self._cursor(m, e),
                    "has_more": m < len(self.messages) or e < len(self.events), "at": now(),
                    "pending_user": len(self._pending(USER))}

    def _pending(self, actor):
        return [{**self._project(m), **self._state(m), "delivery_mode": "cooperative"} for m in self.messages
                if m["kind"] in ("question", "permission_request") and matches(m["recipient"], actor)
                and self._state(m)["state"] == "pending"]

    def pending(self, actor=USER):
        with self.locked():
            return {"messages": self._pending(actor), "cursor": self._cursor()}

    def _stamp(self):
        stamp = []
        for name in ("messages", "events"):
            try:
                st = (self.dir / f"{name}.jsonl").stat()
                stamp.append((st.st_ino, st.st_size, st.st_mtime_ns))
            except FileNotFoundError:
                stamp.append(None)
        return tuple(stamp)

    def summary(self):
        # Keyed by file stat: any append, torn-tail truncation or replacement misses.
        # A pending expiry changes the answer without touching the files, so it bounds the entry.
        stamp = self._stamp()
        cached = getattr(self, "_summary", None)
        if cached and cached[0] == stamp and datetime.now(timezone.utc) < cached[1]:
            return dict(cached[2])
        with self.locked():
            stamp = self._stamp()
            result = {"pending_user": len(self._pending(USER)),
                      "pending_orchestrator": sum(m["kind"] in ("question", "permission_request") and
                                                  m["recipient"]["kind"] == "orchestrator" and
                                                  self._state(m)["state"] == "pending" for m in self.messages), "last_cursor": self._cursor()}
            expiries = [datetime.fromisoformat(m["expires_at"].replace("Z", "+00:00")) for m in self.messages
                        if m["kind"] in ("question", "permission_request") and m["expires_at"]]
            self._summary = (stamp, min(expiries, default=datetime.max.replace(tzinfo=timezone.utc)), result)
            return dict(result)

    def status(self, mid, actor):
        with self.locked():
            message = self._find(mid)
            if not self._visible(message, actor):
                raise MailboxError("message is not addressed to this actor", 403)
            replies = [m for m in self.messages if m["reply_to"] == mid]
            return {"message": self._project(message), **self._state(message),
                    "reply": self._project(replies[0]) if replies else None}

    def ack(self, mid, actor):
        with self.locked():
            message = self._find(mid)
            if not matches(message["recipient"], actor):
                raise MailboxError("only the recipient may acknowledge", 403)
            event = next((e for e in self.events if e["type"] == "ack" and e["message_id"] == mid and e["actor"] == actor), None)
            return {"event": event or self._event("ack", message, actor, {})}

    def reply(self, mid, actor, payload):
        allowed = {"idempotency_key", "expected_version", "outcome", "choice_id", "answer", "action_hash"}
        if not isinstance(payload, dict) or set(payload) - allowed:
            raise MailboxError("invalid reply fields")
        with self.locked():
            original = self._find(mid)
            if not matches(original["recipient"], actor):
                raise MailboxError("only the addressed recipient may reply", 403)
            if original["kind"] not in ("question", "permission_request"):
                raise MailboxError("message cannot be resolved")
            body = {k: payload.get(k) for k in ("outcome", "choice_id", "answer", "action_hash")}
            permission = original["kind"] == "permission_request"
            if permission:
                if body["outcome"] not in ("approve", "deny") or body["action_hash"] != original["payload"]["action_hash"] or body["choice_id"] is not None:
                    raise MailboxError("permission reply requires approve/deny and the matching action_hash")
            else:
                options = {o["id"] for o in original["payload"]["options"]}
                if body["outcome"] != "answer" or body["action_hash"] is not None or \
                        (body["choice_id"] is not None and body["choice_id"] not in options) or \
                        (body["answer"] and not original["payload"]["allow_text"]) or not (body["answer"] or body["choice_id"]):
                    raise MailboxError("invalid question answer")
            state = self._state(original)
            existing = next((m for m in self.messages if m["reply_to"] == mid), None)
            if state["state"] == "resolved" and existing and existing["sender"] == actor and existing["idempotency_key"] == payload.get("idempotency_key") and existing["payload"] == body:
                return {"message": existing, "resolution": state, "delivery": "recorded"}
            if state["state"] != "pending" or payload.get("expected_version", 1) != state["version"]:
                raise MailboxError("request is no longer pending or version is stale", 409, **state)
            message, _ = self._put(actor, {"recipient": original["sender"], "kind": "reply", "subject": original["subject"],
                                          "body": body["answer"] or body["choice_id"] or body["outcome"], "reply_to": mid,
                                          "correlation_id": original["correlation_id"], "idempotency_key": payload.get("idempotency_key"),
                                          "payload": body})
            self._event("resolved", original, actor, {"reply_id": message["id"]})
            return {"message": message, "resolution": self._state(original), "delivery": "recorded"}

    def cancel(self, task_id=None):
        with self.locked():
            for message in self.messages:
                if message["kind"] in ("question", "permission_request") and (self._state(message)["state"] == "pending" or
                     (message["kind"] == "permission_request" and self._state(message)["state"] == "resolved")) and \
                        (task_id is None or message["sender"].get("task_id") == task_id):
                    self._event("cancelled", message, {"kind": "orchestrator"}, {})


def execution_env(store, rec, unit):
    actor = {"kind": "agent", "agent_id": rec["owner"], "task_id": rec["task"]["id"], "unit_id": unit["id"]} if rec["kind"] == "task" else {"kind": "orchestrator", "unit_id": unit["id"]}
    folder = store.root / ".alfrd" / "plans" / store.plan_id / "mailbox" / "contexts"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{identifier(unit['id'])}.json"
    with open(path, "w", encoding="utf-8") as stream:
        os.chmod(path, 0o600)
        json.dump({"root": str(store.root.resolve()), "session": store.id, "run_id": rec["request"], "actor": actor}, stream)
    return {"ALFRD_MAILBOX_CONTEXT": str(path.resolve()), "ALFRD_RUN_ID": rec["request"],
            "ALFRD_TASK_ID": actor.get("task_id", rec["row"]), "ALFRD_AGENT_ID": rec["owner"]}


def from_context():
    path = os.environ.get("ALFRD_MAILBOX_CONTEXT")
    if not path:
        raise MailboxError("ALFRD_MAILBOX_CONTEXT is required")
    context = json.loads(Path(path).read_text())
    box = Mailbox(context["root"], context["session"], context["run_id"])
    actor = context["actor"]
    if actor.get("kind") not in ("agent", "orchestrator"):
        raise MailboxError("invalid execution actor", 403)
    from alfrd.runtime.scheduler import PlanDir
    unit_id = identifier(actor.get("unit_id"))
    unit = next((u for u in PlanDir(box.session.root, box.session.plan_id).units() if u["id"] == unit_id), None)
    rec = box.session.task(str(unit.get("row"))) if unit else None
    # The context is created just before the unit is persisted; the task's attempt
    # list also permits the very first command to read during that launch window.
    if actor["kind"] == "agent":
        rec = rec or box.session.task(f"{box.run_id}.{actor.get('task_id')}")
        if not rec or rec["request"] != box.run_id or rec["owner"] != actor.get("agent_id") or \
                (rec.get("task") or {}).get("id") != actor.get("task_id") or \
                (unit is None and unit_id not in rec.get("units", [])):
            raise MailboxError("execution context does not match this run/task/attempt", 403)
    elif not rec or rec["request"] != box.run_id or rec["kind"] == "task":
        raise MailboxError("invalid orchestrator execution context", 403)
    return box, actor
