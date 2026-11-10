"""M3: mailbox CLI v2 flags, compact read, #seq handles and the permission full-fetch rule."""
import json

import pytest
from typer.testing import CliRunner

from alfrd.cli import alfrd_cli
from alfrd.mailbox import Mailbox, MailboxError, USER, execution_env
from alfrd.mailbox_codes import CODES
from test_agent_mailbox import AGENT, box, permission, question  # noqa: F401
from test_chat_orchestration import project, say  # noqa: F401

cli = CliRunner()


@pytest.fixture
def agent_env(box, monkeypatch):
    env = execution_env(box.session, box.session.task("r0001.a"), {"id": AGENT["unit_id"]})
    monkeypatch.setenv("ALFRD_MAILBOX_CONTEXT", env["ALFRD_MAILBOX_CONTEXT"])
    return box


def run(*args, code=0):
    result = cli.invoke(alfrd_cli, ["mailbox", *args])
    assert result.exit_code == code, result.output
    return json.loads(result.stdout) if result.stdout.startswith("{") else result.stdout


def test_send_v2_flags(agent_env, tmp_path):
    body, desc = tmp_path / "b.txt", tmp_path / "d.txt"
    body.write_text("body")
    desc.write_text("long description")
    base = ["send", "--to", "user", "--subject", "S", "--body-file", str(body)]
    sent = run(*base, "--key", "k1", "--code", "ERR", "--priority", "urgent", "--summary", "short",
               "--description-file", str(desc), "--step-id", "step-3")
    message = sent["message"]
    assert (message["kind"], message["code"], message["category"], message["priority"]) == ("info", "ERR", "error", "urgent")
    assert (message["summary"], message["description"], message["step_id"]) == ("short", "long description", "step-3")
    run(*base, "--key", "k2", "--code", "BAD", code=1)
    run(*base, "--key", "k3", "--code", "ERR", "--kind", "question", code=1)
    run(*base, "--key", "k4", "--priority", "huge", code=1)
    run(*base, "--key", "k5", "--category", "nope", code=1)
    run(*base, "--key", "k6", "--code", "REP", code=1)


def test_codes_prints_table(agent_env):
    out = cli.invoke(alfrd_cli, ["mailbox", "codes"]).stdout
    for code, row in CODES.items():
        assert f"| {code} | {row['kind']} |" in out


def test_compact_read_never_includes_payloads(agent_env):
    box = agent_env
    box.send(AGENT, question("q", body="SECRET-BODY",
                             payload={"options": [{"id": "a", "label": "SECRET-LABEL"}], "allow_text": True}))
    box.send(AGENT, permission("p", body="SECRET-BODY"))
    box.send(AGENT, {**question("i", kind="info"), "payload": {"leak": "SECRET-PAYLOAD"}, "description": "SECRET-DESC"})
    out = cli.invoke(alfrd_cli, ["mailbox", "read", "--compact"])
    assert out.exit_code == 0
    assert "SECRET" not in out.stdout and "action_hash" not in out.stdout
    rows = json.loads(out.stdout)["messages"]
    assert [m["seq"] for m in rows] == [1, 2, 3]
    for row in rows:
        assert not {"payload", "body", "description"} & set(row)
    assert rows[1]["line"].startswith("#2 PRM urgent a→user pending")


def test_seq_handle_resolves_only_in_current_request(agent_env):
    box = agent_env
    first = box.send(AGENT, question("q1"))["message"]
    # A second request in the same session has its own #1 (and #2) that must stay invisible.
    say(box.session.root, box.session.id, "second")
    box.session.save_task({"row": "r0002.a", "request": "r0002", "kind": "task", "owner": "codex",
                           "task": {"id": "a"}, "units": []})
    other = Mailbox(box.session.root, box.session.id, "r0002")
    try:
        other.send(AGENT, question("o1", subject="OTHER"))
        other.send(AGENT, question("o2", subject="OTHER"))
    except MailboxError:
        pass  # a non-active second request can't take mail; the scoping assertions below still hold
    assert run("read", "--message", "#1")["message"]["id"] == first["id"]
    assert run("read", "--message", first["id"])["message"]["seq"] == 1
    out = cli.invoke(alfrd_cli, ["mailbox", "read", "--message", "#2"])
    assert out.exit_code == 1 and json.loads(out.stdout)["status"] == 404
    assert "OTHER" not in out.stdout
    run("read", "--message", "#x", code=1)
    run("read", "--message", "msg_" + "0" * 32, code=1)


def test_permission_requires_full_fetch_and_hash(agent_env):
    box = agent_env
    request = box.send(AGENT, permission("p"))["message"]
    row = json.loads(cli.invoke(alfrd_cli, ["mailbox", "read", "--compact"]).stdout)["messages"][0]
    assert "action_hash" not in row and "payload" not in row
    # The compact row alone cannot approve: a missing or guessed hash is refused.
    for attempt in (None, "0" * 64):
        with pytest.raises(MailboxError):
            box.reply(request["id"], USER, {"idempotency_key": "r", "outcome": "approve", "action_hash": attempt})
    full = run("read", "--message", "#1")
    assert full["message"]["payload"]["action_hash"] == request["payload"]["action_hash"]
    saved = box.reply(request["id"], USER, {"idempotency_key": "ok", "outcome": "approve",
                                            "action_hash": full["message"]["payload"]["action_hash"]})
    assert saved["resolution"]["state"] == "resolved"
