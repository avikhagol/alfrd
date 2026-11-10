"""Local execution-scoped mailbox commands. No server or credentials required."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

import typer

from alfrd.mailbox import MailboxError, from_context
from alfrd.mailbox_codes import CATEGORIES, CODES, PRIORITIES

cli = typer.Typer(help="Send and read messages in the current ALFRD execution.")


def emit(value):
    typer.echo(json.dumps(value, ensure_ascii=False))


def label(actor):
    return actor.get("task_id") or actor["kind"]


def compact(message):
    """One display row: never the body, description or payload."""
    route = f"{label(message['sender'])}→{label(message['recipient'])}"
    return {"seq": message["seq"], "id": message["id"], "code": message["code"], "priority": message["priority"],
            "from": label(message["sender"]), "to": label(message["recipient"]), "state": message["state"],
            "summary": message["summary"],
            "line": f"#{message['seq']} {message['code']} {message['priority']} {route} {message['state']} \"{message['summary']}\""}


def resolve(box, handle):
    """`#seq` is looked up in this request's mailbox only; anything else is a message id."""
    if not handle.startswith("#"):
        return handle
    if not handle[1:].isdigit():
        raise MailboxError("message handle must be a message id or #<seq>")
    with box.locked():
        found = next((m for m in box.messages if m["seq"] == int(handle[1:])), None)
    if not found:
        raise MailboxError("unknown scoped mailbox message", 404)
    return found["id"]


def invoke(call):
    try:
        return call(*from_context())
    except (MailboxError, OSError, ValueError, LookupError) as exc:
        emit({"error": str(exc), "status": getattr(exc, "status", 400)})
        raise typer.Exit(1) from exc


@cli.command()
def send(to: str = typer.Option(..., "--to"), kind: Optional[str] = typer.Option(None),
         subject: str = typer.Option(...), body_file: Path = typer.Option(...),
         key: str = typer.Option(...), payload_file: Optional[Path] = typer.Option(None),
         expires_at: Optional[str] = typer.Option(None), code: Optional[str] = typer.Option(None),
         category: Optional[str] = typer.Option(None), priority: Optional[str] = typer.Option(None),
         summary: Optional[str] = typer.Option(None), description_file: Optional[Path] = typer.Option(None),
         step_id: Optional[str] = typer.Option(None)):
    def call(box, actor):
        sendable = [c for c in CODES if c != "REP"]
        for value, allowed, flag in ((code, sendable, "--code"), (category, CATEGORIES, "--category"),
                                     (priority, PRIORITIES, "--priority")):
            if value is not None and value not in allowed:
                raise MailboxError(f"{flag} must be one of {', '.join(allowed)}")
        if code and kind not in (None, CODES[code]["kind"]):
            raise MailboxError(f"--code {code} is a {CODES[code]['kind']} message, not {kind}")
        extra = {k: v for k, v in (("code", code), ("category", category), ("priority", priority),
                                   ("summary", summary), ("step_id", step_id)) if v is not None}
        if description_file:
            extra["description"] = description_file.read_text()
        mkind = kind or (CODES[code]["kind"] if code else "info")
        if to in ("user", "orchestrator"):
            recipient = {"kind": to}
        elif to.startswith("task:"):
            tid = to[5:]
            rec = box.session.task(f"{box.run_id}.{tid}")
            if not rec:
                raise MailboxError("unknown task", 404)
            recipient = {"kind": "agent", "agent_id": rec["owner"], "task_id": tid}
        else:
            raise MailboxError("--to must be user, orchestrator, or task:<id>")
        emit(box.send(actor, {"recipient": recipient, "kind": mkind, "subject": subject,
                              "body": body_file.read_text(), "idempotency_key": key, "expires_at": expires_at, **extra,
                              "payload": json.loads(payload_file.read_text()) if payload_file else {}}))
    invoke(call)


@cli.command()
def read(after: Optional[str] = typer.Option(None), limit: int = typer.Option(100),
         compact_view: bool = typer.Option(False, "--compact"),
         message: Optional[str] = typer.Option(None, "--message", help="Full message by id or #seq")):
    def call(box, actor):
        if message:
            emit(box.status(resolve(box, message), actor))
            return
        page = box.list(actor, after, limit)
        if compact_view:
            page = {**page, "messages": [compact(m) for m in page["messages"]]}
        emit(page)
    invoke(call)


@cli.command()
def codes():
    """Print the mailbox type-code table."""
    typer.echo("| Code | Kind | Category | Default priority | Meaning |")
    typer.echo("|---|---|---|---|---|")
    for code, row in CODES.items():
        typer.echo(f"| {code} | {row['kind']} | {row['category'] or '(inherited)'} | "
                   f"{row['priority'] or '(inherited)'} | {row['meaning']} |")


@cli.command()
def ack(message_id: str):
    invoke(lambda box, actor: emit(box.ack(message_id, actor)))


@cli.command()
def reply(message_id: str, key: str = typer.Option(...), answer_file: Optional[Path] = typer.Option(None),
          choice_id: Optional[str] = typer.Option(None), outcome: str = typer.Option("answer"),
          action_hash: Optional[str] = typer.Option(None), expected_version: int = typer.Option(1)):
    def call(box, actor):
        saved = box.reply(message_id, actor, {"idempotency_key": key,
                          "expected_version": expected_version, "outcome": outcome, "choice_id": choice_id,
                          "answer": answer_file.read_text() if answer_file else None, "action_hash": action_hash})
        from alfrd import chat
        if chat.process_mailbox(box.session):
            chat.resume(box.session)
            chat.acknowledge_mailbox_delivery(box.session)
        emit(saved)
    invoke(call)


@cli.command()
def wait(message_id: str, timeout: int = typer.Option(60, min=0, max=3600)):
    def call(box, actor):
        deadline = time.monotonic() + timeout
        while True:
            state = box.status(message_id, actor)
            if state["state"] != "pending" or time.monotonic() >= deadline:
                emit(state)
                if state["state"] == "pending":
                    raise typer.Exit(2)
                if state["state"] != "resolved" or (state["reply"]["payload"]["outcome"] == "deny"):
                    raise typer.Exit(3)
                return
            time.sleep(min(1, max(0, deadline - time.monotonic())))
    invoke(call)
