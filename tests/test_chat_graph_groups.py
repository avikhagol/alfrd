"""Graph ``groups``, ``status_text``, ``rev`` and deterministic order (G1)."""
from __future__ import annotations

import json

from alfrd import chat

from tests.test_chat_orchestration import fake, project, run, say  # noqa: F401  (the project fixture)


def test_graph_has_groups_status_text_and_rev_per_request(project):
    sid = chat.create_session(project, "Groups")["id"]
    fake(project, t1={"text": "done"})
    say(project, sid, "@codex do the first thing")
    state = run(project, sid)
    g = state["graph"]

    assert g["rev"] == state["rev"]
    assert {"nodes", "edges", "mermaid"} <= set(g)  # the old shape stays valid
    assert all(n["status_text"] for n in g["nodes"])

    [req] = state["requests"]
    [group] = g["groups"]
    assert group["id"] == req["id"]
    assert group["title"].startswith("@codex do the first")
    assert set(group["nodes"]) == {n["id"] for n in g["nodes"]}
    assert group["counts"]["total"] == len(g["nodes"])
    assert set(group["orchestrator"]) == {n["id"] for n in g["nodes"] if n["kind"] != "task"}
    assert all(g_["id"] != "unscoped" for g_ in g["groups"])


def test_two_requests_make_two_groups_in_request_order(project):
    sid = chat.create_session(project, "Two")["id"]
    fake(project, t1={"text": "done"})
    say(project, sid, "@codex first")
    say(project, sid, "@codex second")
    groups = chat.graph(chat.session(project, sid))["groups"]
    assert [g["title"] for g in groups] == ["@codex first", "@codex second"]
    assert not set(groups[0]["nodes"]) & set(groups[1]["nodes"])


def test_legacy_session_nodes_fall_into_unscoped_group(project):
    sid = chat.create_session(project, "Legacy")["id"]
    fake(project, t1={"text": "done"})
    say(project, sid, "@codex legacy work")
    run(project, sid)
    store = chat.session(project, sid)

    # Make the records look like an old session: no created_ns, no request id, no rev file.
    for path in sorted(store.tasks_dir.glob("*/task.json")):
        rec = json.loads(path.read_text())
        rec.pop("created_ns", None)
        rec.pop("request", None)
        path.write_text(json.dumps(rec))
    store.rev_file.unlink(missing_ok=True)

    g = chat.graph(chat.session(project, sid))
    assert g["rev"] == 0
    assert g["nodes"] and all(n["request"] is None and n["status_text"] for n in g["nodes"])
    assert g["groups"][-1]["id"] == "unscoped" and g["groups"][-1]["title"] == "Unscoped"
    assert set(g["groups"][-1]["nodes"]) == {n["id"] for n in g["nodes"]}
    assert g["groups"][-1]["counts"]["total"] == len(g["nodes"])


def test_order_is_created_ns_then_row_and_stable(project):
    sid = chat.create_session(project, "Order")["id"]
    fake(project, t1={"text": "done"})
    say(project, sid, "@codex order")
    store = chat.session(project, sid)
    recs = {p: json.loads(p.read_text()) for p in sorted(store.tasks_dir.glob("*/task.json"))}
    # Same created_ns for every record: the row breaks the tie; a bigger value goes last.
    for i, (path, rec) in enumerate(recs.items()):
        rec["created_ns"] = 5 if i < len(recs) - 1 else 1
        path.write_text(json.dumps(rec))
    last = list(recs.values())[-1]["row"]
    first = chat.graph(chat.session(project, sid))
    ids = [n["id"] for n in first["nodes"]]
    assert ids[0] == last
    assert ids[1:] == sorted(ids[1:])
    assert chat.graph(chat.session(project, sid))["nodes"] == first["nodes"]


def test_graph_file_is_written_with_the_new_fields(project):
    sid = chat.create_session(project, "File")["id"]
    fake(project, t1={"text": "done"})
    say(project, sid, "@codex file")
    run(project, sid)
    written = json.loads(chat.session(project, sid).graph_file.read_text())
    assert {"groups", "rev"} <= set(written)
    assert all("status_text" in n for n in written["nodes"])
