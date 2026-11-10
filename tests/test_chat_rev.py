"""Session ``rev`` and the ``since_rev`` short-circuit of the chat state (F1, contract C9)."""
from __future__ import annotations

import json

from alfrd import chat

from tests.test_chat_orchestration import fake, project, run, say  # noqa: F401  (the project fixture)


def test_since_rev_is_unchanged_when_idle_and_rev_grows_on_mutation(project):
    sid = chat.create_session(project, "Rev")["id"]
    state = chat.session_state(project, sid)
    rev = state["rev"]
    assert isinstance(rev, int)

    assert chat.session_state(project, sid, since_rev=rev) == {"rev": rev, "unchanged": True}
    assert chat.session_state(project, sid, since_rev=rev) == {"rev": rev, "unchanged": True}  # still idle

    chat.session(project, sid).add_message("user", "hello")
    after = chat.session_state(project, sid, since_rev=rev)
    assert "unchanged" not in after and after["rev"] > rev
    assert [m["text"] for m in after["messages"]] == ["hello"]

    rename_from = after["rev"]
    chat.rename_session(project, sid, "Renamed")
    assert chat.session_state(project, sid)["rev"] > rename_from


def test_rev_is_monotonic_across_a_run_and_written_atomically(project):
    sid = chat.create_session(project, "Run")["id"]
    seen = [chat.session_state(project, sid)["rev"]]
    fake(project, t1={"text": "done"})
    say(project, sid, "@codex do it")
    seen.append(chat.session_state(project, sid)["rev"])
    seen.append(run(project, sid)["rev"])
    assert seen == sorted(set(seen))

    store = chat.session(project, sid)
    assert json.loads(store.rev_file.read_text()) == {"rev": seen[-1]}
    assert not list(store.dir.glob("*.tmp"))


def test_state_shape_is_unchanged_apart_from_rev(project):
    sid = chat.create_session(project, "Shape")["id"]
    state = chat.session_state(project, sid)
    assert {"session", "messages", "requests", "tasks", "graph", "decisions", "blockers", "usage", "plan",
            "settings", "protocol", "mailbox_version"} <= set(state)
    assert "rev" not in state["session"]  # session.json itself is untouched


def test_studio_state_endpoint_honours_since_rev(project, tmp_path):
    from alfrd.gui import create_app
    from alfrd.gui.services import RuntimeCatalogReader
    from alfrd.runtime import RuntimeService, RuntimeStore

    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    service = RuntimeService(store)
    service.register_manifest(project / ".alfrd" / "alfrd.yaml")
    ident = service.list_projects()[0].identifier
    app = create_app({"TESTING": True, "SECRET_KEY": "k", "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'c.sqlite'}",
                      "RUNTIME_SERVICE": service, "CATALOG_READER": RuntimeCatalogReader(service),
                      "STUDIO_DEMO": False, "STUDIO_LIVE_INTERVAL": 0})
    client = app.test_client()
    sid = chat.create_session(project, "Http")["id"]
    url = f"/api/studio/projects/{ident}/chat/sessions/{sid}"
    full = client.get(url).get_json()
    assert client.get(f"{url}?since_rev={full['rev']}").get_json() == {"rev": full["rev"], "unchanged": True}
    chat.session(project, sid).add_message("user", "hi")
    fresh = client.get(f"{url}?since_rev={full['rev']}").get_json()
    assert fresh["rev"] > full["rev"] and fresh["messages"][0]["text"] == "hi"
