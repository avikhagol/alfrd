"""Local execution-scoped mailbox commands. No server or credentials required."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

import typer

from alfrd.mailbox import MailboxError, from_context

cli = typer.Typer(help="Send and read messages in the current ALFRD execution.")


def emit(value):
    typer.echo(json.dumps(value, ensure_ascii=False))


def invoke(call):
    try:
        return call(*from_context())
    except (MailboxError, OSError, ValueError, LookupError) as exc:
        emit({"error": str(exc), "status": getattr(exc, "status", 400)})
        raise typer.Exit(1) from exc


@cli.command()
def send(to: str = typer.Option(..., "--to"), kind: str = typer.Option("info"),
         subject: str = typer.Option(...), body_file: Path = typer.Option(...),
         key: str = typer.Option(...), payload_file: Optional[Path] = typer.Option(None),
         expires_at: Optional[str] = typer.Option(None)):
    def call(box, actor):
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
        emit(box.send(actor, {"recipient": recipient, "kind": kind, "subject": subject,
                              "body": body_file.read_text(), "idempotency_key": key, "expires_at": expires_at,
                              "payload": json.loads(payload_file.read_text()) if payload_file else {}}))
    invoke(call)


@cli.command()
def read(after: Optional[str] = typer.Option(None), limit: int = typer.Option(100)):
    invoke(lambda box, actor: emit(box.list(actor, after, limit)))


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
