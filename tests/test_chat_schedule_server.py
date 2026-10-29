# ruff: noqa: F811 - imported pytest fixtures are requested by name
from types import SimpleNamespace
from alfrd import chat, cli
from tests.test_chat_schedule import queued, project, add  # noqa: F401


def test_startup_catchup_dynamic_scope_and_shutdown(queued, monkeypatch):
    root, sid, _ = queued
    add(queued)
    store = chat.session(root, sid)
    with store.lock():
        data = store.load_schedule(); data['batches'][0]['run_at'] = '2000-01-01T00:00:00Z'; store.save_schedule(data)
    p = SimpleNamespace(identifier='yes', root_path=root)
    ignored = SimpleNamespace(identifier='no', root_path='/ignored')
    projects = [p, p, ignored]
    service = SimpleNamespace(list_projects=lambda: projects)
    calls, targets = [], []
    original = chat.dispatch_due_schedules
    def dispatch(root, *, spawn):
        calls.append(root)
        return original(root, spawn=spawn)
    monkeypatch.setattr(chat, 'dispatch_due_schedules', dispatch)
    class Thread:
        def __init__(self, **kw): targets.append(kw['target'])
        def start(self): pass
    class Stopped:
        ticks = 0
        def is_set(self): return self.ticks >= 3
        def wait(self, seconds):
            assert seconds == 1
            self.ticks += 1
            if self.ticks == 1: projects.clear()
            if self.ticks == 2: projects.extend([p, p])
    monkeypatch.setattr(cli.threading, 'Thread', Thread)
    cli._chat_schedule_later(service, ['yes'], Stopped(), spawn=True)
    targets[0]()
    assert calls == [root.resolve(), root.resolve()]
    assert len(store.requests()) == 1
    assert chat.schedule_state(root, sid)['last_schedule']['status'] == 'submitted'


def test_sweep_isolates_bad_sessions(queued):
    root, sid, _ = queued
    add(queued)
    other = chat.create_session(root)['id']
    chat.session(root, other).schedule_file.write_text('bad')
    out = chat.dispatch_due_schedules(root, now=4102444800)
    assert any(r.get('error') for r in out)
    assert len(chat.session(root, sid).requests()) == 1
