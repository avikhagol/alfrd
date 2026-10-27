"""Chat sessions can be renamed and deleted (chat.rename_session / delete_session, Studio PATCH / DELETE, CLI)."""
from __future__ import annotations

import pytest

from alfrd import chat
from alfrd.runtime import scheduler

from tests.test_chat_orchestration import fake, project, run, say  # noqa: F401  (the project fixture)


def test_rename_trims_and_refuses_empty(project):
    sid = chat.create_session(project, "First")["id"]
    assert chat.rename_session(project, sid, "  Release   notes \n")["title"] == "Release notes"
    assert chat.rename_session(project, sid, "x" * 300)["title"] == "x" * chat.TITLE_MAX
    with pytest.raises(ValueError):
        chat.rename_session(project, sid, "   ")
    with pytest.raises(LookupError):
        chat.rename_session(project, "20260101-000000-abcdef", "nope")
    assert [s["title"] for s in chat.list_sessions(project)] == ["x" * chat.TITLE_MAX]


def test_delete_removes_session_and_plan_folders(project):
    keep = chat.create_session(project, "Keep")["id"]
    sid = chat.create_session(project, "Drop")["id"]
    fake(project, t1={"text": "done"})
    say(project, sid, "@codex do it")
    run(project, sid)
    store = chat.session(project, sid)
    plan = scheduler.PlanDir(store.root, store.plan_id).path
    assert store.dir.is_dir() and plan.is_dir()
    assert chat.delete_session(project, sid)["deleted"] is True
    assert not store.dir.exists() and not plan.exists()
    assert [s["id"] for s in chat.list_sessions(project)] == [keep]
    with pytest.raises(LookupError):
        chat.delete_session(project, sid)


def test_delete_is_refused_while_a_request_is_active(project):
    sid = chat.create_session(project, "Busy")["id"]
    say(project, sid, "@codex do it")  # queued, the runner never started (spawn=False)
    with pytest.raises(PermissionError, match="active requests"):
        chat.delete_session(project, sid)
    assert chat.session(project, sid).dir.is_dir()


def test_studio_rename_and_delete_endpoints(project, tmp_path, monkeypatch):
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
    headers = {"X-CSRF-Token": client.get("/api/studio/session").get_json()["csrf_token"]}
    url = f"/api/studio/projects/{ident}/chat/sessions"
    sid = client.post(url, json={"title": "One"}, headers=headers).get_json()["session"]["id"]
    assert client.patch(f"{url}/{sid}", json={"title": "Renamed"}).status_code == 403  # CSRF
    renamed = client.patch(f"{url}/{sid}", json={"title": "Renamed"}, headers=headers)
    assert renamed.status_code == 200 and renamed.get_json()["session"]["title"] == "Renamed"
    assert client.patch(f"{url}/{sid}", json={"title": ""}, headers=headers).status_code == 400
    assert client.get(url).get_json()["sessions"][0]["id"] == sid
    say(project, sid, "@codex busy")
    assert client.delete(f"{url}/{sid}", headers=headers).status_code == 409
    fake(project, t1={"text": "done"})
    run(project, sid)
    assert client.delete(f"{url}/{sid}", headers=headers).status_code == 200
    assert client.get(url).get_json()["sessions"] == []
    assert client.delete(f"{url}/{sid}", headers=headers).status_code == 404

@pytest.mark.parametrize(('prompt', 'expected'), [
    ('@codex   Fix the\n project switcher', 'Fix the project switcher'),
    ('@CODEX Cafe\u0301\x00  @CODEX test@example.com <b>safe</b>', 'Café @codex test@example.com <b>safe</b>'),
    ('@codex ' + 'x' * 70, 'x' * 47 + '…'),
    ('@codex ' + 'word ' * 20, 'word word word word word word word word word…'),
])
def test_first_prompt_title_is_normalized_frozen_and_replay_safe(project, prompt, expected):
    data = chat.create_session(project)
    assert data['title'] == 'New chat' and data['title_source'] == 'placeholder'
    sid = data['id']
    first = chat.post_message(project, sid, prompt, spawn=False, client_message_id='first')
    assert chat.session(project, sid).load()['title'] == expected
    assert chat.session(project, sid).load()['title_source'] == 'prompt'
    replay = chat.post_message(project, sid, prompt, spawn=False, client_message_id='first')
    assert replay['message']['id'] == first['message']['id']
    chat.post_message(project, sid, '@codex Later title', spawn=False)
    assert chat.session(project, sid).load()['title'] == expected


def test_explicit_titles_and_rename_win(project):
    sid = chat.create_session(project, 'My title')['id']
    say(project, sid, '@codex Generated')
    assert chat.session(project, sid).load()['title'] == 'My title'
    assert chat.rename_session(project, sid, 'Chosen')['title_source'] == 'explicit'
    say(project, sid, '@codex Another')
    assert chat.session(project, sid).load()['title'] == 'Chosen'


def test_title_repairs_first_durable_message_after_interrupted_write(project, monkeypatch):
    sid = chat.create_session(project)['id']
    store = chat.session(project, sid)
    repair = chat._repair_title
    monkeypatch.setattr(chat, '_repair_title', lambda *args: None)
    first = chat.post_message(project, sid, '@codex First durable prompt', spawn=False, client_message_id='repair')
    monkeypatch.setattr(chat, '_repair_title', repair)
    chat.post_message(project, sid, '@codex First durable prompt', spawn=False, client_message_id='repair')
    assert store.load()['title'] == 'First durable prompt'
    assert len([m for m in store.messages() if m['role'] == 'user']) == 1
    assert first['message']['client_message_id'] == 'repair'


@pytest.mark.parametrize('placeholder', [False, True])
def test_legacy_title_backfill_only_on_mutation(project, placeholder):
    sid = chat.create_session(project)['id']
    store = chat.session(project, sid)
    data = store.load()
    data.pop('title_source')
    title = f"Chat {data['created'][:16].replace('T', ' ')}" if placeholder else 'Manual legacy name'
    data['title'] = title
    chat._write(store.session_file, data)
    # Simulate a pre-upgrade durable transcript; reads must not backfill it.
    store.messages_file.write_text('{"id":"m1","role":"user","text":"@codex Original legacy prompt"}\n')
    assert chat.list_sessions(project)[0]['title'] == title
    with store.lock():
        pass
    assert store.load()['title'] == ('Original legacy prompt' if placeholder else title)
    assert store.load()['title_source'] == ('prompt' if placeholder else 'explicit')


def test_attachment_only_name_and_rename_send_concurrency(project, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    sid = chat.create_session(project)['id']
    monkeypatch.setattr(chat, '_resolve_attachments', lambda *args: [{'ref': 'image.png'}])
    chat.post_message(project, sid, '', spawn=False, attachments=['image.png'])
    assert chat.session(project, sid).load()['title'] == 'Image conversation'
    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(chat.rename_session, project, sid, 'Explicit wins'),
                   pool.submit(chat.post_message, project, sid, '@codex Concurrent prompt', spawn=False)]
        for future in futures:
            future.result()
    assert chat.session(project, sid).load()['title'] == 'Explicit wins'
    assert chat.session(project, sid).load()['title_source'] == 'explicit'


def test_prompt_title_change_invalidates_existing_live_metadata_stream(project):
    from alfrd.studio_live import AlfrdWatcher
    sid = chat.create_session(project)['id']
    watcher = AlfrdWatcher('project-key', project, lambda *args: None)
    watcher.probe()
    before = chat.session(project, sid).rev
    say(project, sid, '@codex Name this from the first prompt')
    events = watcher.probe()
    assert any(e['kind'] == 'chat.session.changed' and e['plan'] == f'chat-{sid}' for e in events)
    assert chat.session(project, sid).rev > before
    assert chat.session(project, sid).load()['title'] == 'Name this from the first prompt'
