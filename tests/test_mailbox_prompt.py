"""Compact mailbox prompt section (M4): size budgets, body rules, rule text and orchestrator digests."""
import pytest

from alfrd import chat
from alfrd.mailbox import Mailbox, USER, action_hash
from test_chat_orchestration import project, say  # noqa: F401

A = {"kind": "agent", "agent_id": "codex", "task_id": "a", "unit_id": "0002-r0001.a-codex"}
B = {"kind": "agent", "agent_id": "codex", "task_id": "b", "unit_id": "0003-r0001.b-codex"}
TO_A = {"kind": "agent", "agent_id": "codex", "task_id": "a"}
TO_B = {"kind": "agent", "agent_id": "codex", "task_id": "b"}
ORCH = {"kind": "orchestrator"}

# The 0.3 contract's approval and sandbox sentences, copied here so a rewrite cannot drift silently.
RULES_V03 = ("a read or ack is not approval. Before acting, require an unexpired approve reply with the exact action hash; "
             "native sandbox policy still applies: an approval cannot widen a running session's folders or "
             "commands, so for those exit blocked with kind permission and requires (ALFRD re-launches you).")

LONG = ("The build on the staging host failed in the migrations step: table `files` already has column `rev`. "
        "I rolled back and kept the logs under logs/staging-migrate.txt. ") * 12


def info(code, to, summary, body=LONG, key=None, **extra):
    return {"recipient": to, "kind": "info", "code": code, "subject": summary[:60], "summary": summary,
            "body": body, "idempotency_key": key or f"{code}-{summary[:20]}", "payload": {}, **extra}


def ask(to, summary, key, code="ASK"):
    return {"recipient": to, "kind": "question", "code": code, "subject": summary, "summary": summary, "body": LONG,
            "idempotency_key": key, "payload": {"options": [{"id": "y", "label": "Yes"}, {"id": "n", "label": "No"}],
                                                "allow_text": True}}


def prm(summary, key):
    payload = {"action": "migrate", "target": "staging", "description": summary, "scope": "once",
               "choices": ["approve", "deny"]}
    payload["action_hash"] = action_hash(payload)
    return {"recipient": USER, "kind": "permission_request", "code": "PRM", "subject": summary, "summary": summary,
            "body": LONG, "idempotency_key": key, "payload": payload}


def record(box):
    """A recorded two-task request: 20 messages of every common code, with long bodies and two replies."""
    sent = [
        (A, info("STR", ORCH, "Started on the API change")),
        (A, info("PRG", ORCH, "Step 1/4: schema read", key="p1")),
        (B, info("DEP", TO_A, "API shape: GET /files returns {rev, files[]}")),
        (A, info("CLM", TO_B, "Claiming src/alfrd/chat.py until done")),
        (A, ask(USER, "Keep the v1 route for old clients?", "q1")),
        (A, prm("Run migration on staging", "m1")),
        (B, info("WRN", TO_A, "Fixture loader is slow; tests take 40 s")),
        (A, info("PRG", ORCH, "Step 2/4: routes", key="p2")),
        (B, info("NTE", TO_A, "FYI: the old route test is flaky upstream")),
        (A, info("ART", ORCH, "docs/api.md ready")),
        (B, info("ERR", ORCH, "Lint step failed on generated file")),
        (B, ask(ORCH, "Should b also cover the CLI?", "q2", code="ASO")),
        (A, info("PRG", ORCH, "Step 3/4: tests", key="p3")),
        (B, info("DEP", TO_A, "Renamed helper to files_rev(); update imports")),
        (B, info("BLK", TO_A, "Need your schema before I can continue")),
        (A, info("DON", ORCH, "API change done, tests green")),
    ]
    out = [box.send(actor, payload)["message"] for actor, payload in sent]
    box.reply(out[4]["id"], USER, {"idempotency_key": "r1", "outcome": "answer", "answer": "Yes, keep it for 0.4."})
    box.reply(out[5]["id"], USER, {"idempotency_key": "r2", "outcome": "approve",
                                   "action_hash": out[5]["payload"]["action_hash"]})
    box.ack(out[8]["id"], A)
    out.append(box.send(USER, info("NTE", TO_A, "Please also update the changelog", key="u1"))["message"])
    out.append(box.send(USER, info("NTE", TO_B, "Thanks, b looks good", key="u2"))["message"])
    assert len(box.list()["messages"]) == 20
    return out


@pytest.fixture
def store(project):
    sid = chat.create_session(project)["id"]
    say(project, sid, "work")
    store = chat.session(project, sid)
    for tid, actor in (("a", A), ("b", B)):
        store.save_task({"row": f"r0001.{tid}", "request": "r0001", "kind": "task", "owner": "codex",
                         "task": {"id": tid, "title": tid}, "units": [actor["unit_id"]]})
    return store


def task_rec(store):
    return store.task("r0001.a")


def plan_rec():
    return {"row": "r0001.plan", "request": "r0001", "kind": "plan", "owner": "claude"}


def section(prompt):
    return prompt[prompt.index("\n## Agent mailbox"):]


def test_empty_mailbox_section_is_at_most_600_characters(store):
    for rec in (task_rec(store), plan_rec()):
        text = chat.mailbox_prompt(store, rec)
        assert len(text) <= 600 and len(text.encode()) <= 600
        assert text == chat.MAILBOX_CONTRACT and "<mailbox>" not in text


def test_approval_and_sandbox_rules_are_word_for_word(store):
    assert chat.MAILBOX_RULES == RULES_V03
    assert RULES_V03 in chat.mailbox_prompt(store, task_rec(store))
    record(Mailbox(store.root, store.id, "r0001"))
    assert RULES_V03 in chat.mailbox_prompt(store, task_rec(store))
    assert RULES_V03 in chat.mailbox_prompt(store, plan_rec())


def test_twenty_messages_fit_in_two_and_a_half_kb(store):
    record(Mailbox(store.root, store.id, "r0001"))
    for rec in (task_rec(store), plan_rec()):
        text = chat.mailbox_prompt(store, rec)
        assert len(text.encode()) <= chat.MAILBOX_SECTION_BYTES <= 2560
        assert text.startswith(chat.MAILBOX_CONTRACT) and text.rstrip().endswith("</mailbox>")


def test_compact_lines_and_body_rules(store):
    msgs = record(Mailbox(store.root, store.id, "r0001"))
    text = chat.mailbox_prompt(store, task_rec(store))
    lines = text.split("<mailbox>\n")[1].split("\n</mailbox>")[0].splitlines()
    by_seq = {line.split()[0]: line for line in lines if line.startswith("#")}
    # Every message to or from `a` gets a line, sorted by priority, then seq.
    mine = [m for m in msgs if "a" in (m["recipient"].get("task_id"), m["sender"].get("task_id") if m["kind"] in ("question", "permission_request") else None)]
    assert len(by_seq) == len(mine) and "more:" not in text
    ranks = [chat._MAIL_RANK[line.split()[2]] for line in lines if line.startswith("#")]
    assert ranks == sorted(ranks)
    blk = lines.index(f'#{msgs[14]["seq"]} BLK urgent b→you pending "Need your schema before I can continue"')
    assert lines[blk + 1].startswith("  > The build on the staging host")
    # Its own permission request shows the resolution and the exact hash; replies fold into their question.
    prm_line = by_seq[f"#{msgs[5]['seq']}"]
    assert f"PRM urgent you→user resolved:approve \"Run migration on staging\" h={msgs[5]['payload']['action_hash']}" in prm_line
    assert not lines[lines.index(prm_line) + 1].startswith("  > ")
    ask_line = by_seq[f"#{msgs[4]['seq']}"]
    assert " ASK high you→user resolved:answer " in ask_line
    assert lines[lines.index(ask_line) + 1] == "  > Yes, keep it for 0.4."
    assert " REP " not in text
    # Acked mail is listed without a body; mail between others and to the orchestrator is not listed.
    acked = lines.index(by_seq[f"#{msgs[8]['seq']}"])
    assert " acked " in lines[acked] and not lines[acked + 1].startswith("  > ")
    assert f"#{msgs[0]['seq']}" not in by_seq and f"#{msgs[-1]['seq']}" not in by_seq
    bodies = sum(len(line.encode()) for line in lines if line.startswith("  > "))
    assert 0 < bodies <= chat.MAILBOX_BODY_BYTES


def test_orchestrator_section_lists_its_mail(store):
    msgs = record(Mailbox(store.root, store.id, "r0001"))
    text = chat.mailbox_prompt(store, plan_rec())
    assert f'#{msgs[11]["seq"]} ASO high b→you pending "Should b also cover the CLI?"' in text
    assert f'#{msgs[15]["seq"]} DON normal a→you pending' in text or f'#{msgs[15]["seq"]} DON normal a→you acked' in text
    assert f'#{msgs[2]["seq"]} ' not in text  # b→a is not orchestrator mail


def test_many_and_multibyte_messages_stay_within_budget(store):
    box = Mailbox(store.root, store.id, "r0001")
    for i in range(60):
        box.send(B, info("BLK" if i % 3 else "NTE", TO_A, f"ブロッカー {i}: 依存関係を待っています " * 6,
                         body="本文 " * 3000, key=f"k{i}"))
    text = chat.mailbox_prompt(store, task_rec(store))
    assert len(text.encode()) <= chat.MAILBOX_SECTION_BYTES
    assert "more: `alfrd mailbox read --compact`)" in text
    assert text.count("\n#") <= chat.MAILBOX_LINES


def test_replan_and_summary_prompts_carry_a_digest(store):
    msgs = record(Mailbox(store.root, store.id, "r0001"))
    conf = chat.settings(store.root)
    replan = chat.replan_prompt(store, conf, task_rec(store), [{"id": "d1", "reason": "needs input"}])
    summary = chat.summary_prompt(store, {**store.request("r0001"), "tasks": ["r0001.a", "r0001.b"]})
    for prompt in (replan, summary):
        digest = prompt[prompt.index("## Mailbox digest"):prompt.index("</mailbox>")]
        assert len(digest.encode()) <= chat.MAILBOX_SECTION_BYTES
        assert f'#{msgs[2]["seq"]} DEP normal b→a pending "API shape: GET /files returns {{rev, files[]}}"' in digest
        assert f"#{msgs[5]['seq']} PRM urgent a→user resolved:approve" in digest
        assert f"#{msgs[0]['seq']} " not in digest  # orchestrator mail is in the call's own section
        assert "  > " not in digest and "staging host" not in digest


def test_no_digest_without_mail(store):
    conf = chat.settings(store.root)
    assert "Mailbox digest" not in chat.replan_prompt(store, conf, task_rec(store), [])
    assert "Mailbox digest" not in chat.summary_prompt(store, {**store.request("r0001"), "tasks": []})
