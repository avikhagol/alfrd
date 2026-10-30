"""Queue persistence, single-request dispatch and restart recovery without real agent launches."""
# ruff: noqa: F811 - imported pytest fixtures are requested by name
import time
from concurrent.futures import ProcessPoolExecutor

import pytest

from alfrd import chat
from tests.test_chat_orchestration import project  # noqa: F401


@pytest.fixture
def queued(project, monkeypatch):
    monkeypatch.setattr(chat, 'resume', lambda *a, **kw: None)
    sid = chat.create_session(project)['id']
    out = chat.create_schedule(project, sid, run_at='2099-10-28T17:00:00+02:00', timezone='Europe/Athens',
                               client_schedule_id='create', expected_schedule_revision=0)
    return project, sid, out['schedule']['id']


def add(q, text='first', cid='item', **kw):
    root, sid, _ = q
    return chat.post_message(root, sid, text, client_message_id=cid, **kw)


def mutate(q, action, **kw):
    root, sid, bid = q
    return chat.mutate_schedule(root, sid, bid, action, expected_schedule_revision=chat.schedule_state(root, sid)['schedule_revision'],
                                operation_id=kw.pop('operation_id', action), **kw)


def sweep(q, **kw):
    return chat.dispatch_due_schedules(q[0], q[1], now=time.time() + 100 * 365 * 86400, **kw)


def test_persist_edit_remove_and_replay(queued):
    root, sid, bid = queued
    before = chat.session(root, sid).csv.read_text()
    first = add(queued)
    second = add(queued, 'second', 'two')
    iid = first['item']['id']
    mutate(queued, 'edit', item_id=iid, text='edited', attachments=[])
    assert add(queued)['item']['text'] == 'edited'
    mutate(queued, 'remove', item_id=second['item']['id'])
    replay = add(queued, 'second', 'two')
    assert replay['replayed'] and replay['item']['removed_at']
    store = chat.session(root, sid)
    assert len(store.load_schedule()['batches'][0]['items']) == 2
    assert not store.requests() and not store.tasks() and not store.messages()
    assert store.csv.read_text() == before
    assert chat.session_state(root, sid)['schedule']['id'] == bid
    assert chat.list_sessions(root)[0]['schedule']['queued_count'] == 1
    with pytest.raises(chat.ScheduleConflictError):
        add(queued, 'different')
    with pytest.raises(PermissionError):
        chat.delete_session(root, sid)


def test_combines_latest_items_into_one_forced_orchestrated_request(queued):
    root, sid, bid = queued
    first = add(queued, '@codex build a usage report')
    add(queued, 'include CSV export', 'two')
    mutate(queued, 'edit', item_id=first['item']['id'], text='@codex build a weekly report', attachments=[])
    sweep(queued)
    sweep(queued)
    store = chat.session(root, sid)
    [req] = store.requests()
    assert req['mode'] == 'orchestrated' and req['orchestrator_calls'] == 1
    assert req['schedule_id'] == bid and req['rows'] == ['r0001.plan']
    assert '@codex build a weekly report' in req['text'] and 'include CSV export' in req['text']
    assert 'build a usage report' not in req['text']
    assert len(store.messages()) == len(store.tasks()) == 1
    assert store.messages()[0]['client_message_id'] == 'schedule.' + bid
    assert chat.schedule_state(root, sid)['schedule'] is None
    with pytest.raises(chat.ScheduleConflictError):
        add(queued, 'late', 'late', schedule_id=bid)
    assert add(queued, '@codex build a usage report')['replayed']


@pytest.mark.parametrize('status', chat.REQUEST_ACTIVE)
def test_waits_for_every_active_request_then_includes_waiting_edits(queued, status):
    root, sid, _ = queued
    add(queued)
    store = chat.session(root, sid)
    store.save_request({'id': 'r0001', 'status': status})
    sweep(queued)
    state = chat.schedule_state(root, sid)
    assert state['schedule']['status'] == 'waiting'
    revision = state['schedule_revision']
    sweep(queued)
    assert chat.schedule_state(root, sid)['schedule_revision'] == revision
    add(queued, 'added while waiting', 'waiting')
    store.save_request({'id': 'r0001', 'status': 'done'})
    sweep(queued)
    assert len(store.requests()) == 2
    assert 'added while waiting' in store.requests()[1]['text']


def test_deadline_empty_cancel_reschedule_run_and_operation_replay(queued):
    root, sid, bid = queued
    assert chat.dispatch_due_schedules(root, sid, now=0)[0]['schedule']['status'] == 'scheduled'
    add(queued)
    out = mutate(queued, 'time', run_at='2099-10-30T00:00:00Z', timezone='UTC')
    replay = chat.mutate_schedule(root, sid, bid, 'time', run_at='2099-10-30T00:00:00Z', timezone='UTC', expected_schedule_revision=0, operation_id='time')
    assert replay['replayed'] and replay['schedule_revision'] == out['schedule_revision']
    mutate(queued, 'run')
    assert len(chat.session(root, sid).requests()) == 1
    assert mutate(queued, 'run')['replayed']
    with pytest.raises(chat.ScheduleConflictError):
        mutate(queued, 'cancel')


def test_empty_expires_and_cancel_does_not_execute(queued):
    sweep(queued)
    assert chat.schedule_state(queued[0], queued[1])['last_schedule']['status'] == 'expired_empty'
    assert not chat.session(queued[0], queued[1]).requests()


def test_cancel_retains_replay_and_prevents_targeted_send(queued):
    add(queued)
    mutate(queued, 'cancel')
    assert add(queued)['replayed']
    assert not chat.session(queued[0], queued[1]).requests()
    with pytest.raises(chat.ScheduleConflictError):
        add(queued, 'later', 'later', schedule_id=queued[2])


@pytest.mark.parametrize('stage', ['request', 'message', 'task', 'prompt', 'row', 'submitted'])
def test_restart_recovers_claimed_dispatch_once(queued, monkeypatch, stage):
    root, sid, _ = queued
    add(queued)
    # Fail immediately after a durable preparation side effect, then sweep with a fresh Session.
    if stage in ('request', 'message', 'task'):
        name = {'request': 'save_request', 'message': 'add_message', 'task': 'save_task'}[stage]
        original = getattr(chat.Session, name)
        def fail(*a, **kw):
            original(*a, **kw)
            raise OSError('crash')
        monkeypatch.setattr(chat.Session, name, fail)
    else:
        name = {'prompt': '_write_prompt', 'row': '_publish_scheduled_row', 'submitted': '_schedule_changed'}[stage]
        original = getattr(chat, name)
        def fail(*a, **kw):
            original(*a, **kw)
            if stage != 'submitted' or a[-1] == 'submitted':
                raise OSError('crash')
        monkeypatch.setattr(chat, name, fail)
    sweep(queued)
    if stage in ('request', 'message', 'task'):
        monkeypatch.setattr(chat.Session, name, original)
    else:
        monkeypatch.setattr(chat, name, original)
    sweep(queued)
    sweep(queued)
    store = chat.session(root, sid)
    assert len(store.requests()) == len(store.messages()) == len(store.tasks()) == 1
    assert store.requests()[0]['status'] == 'planning'
    assert chat._table(store, chat.settings(root)).row('r0001.plan')
    assert chat.schedule_state(root, sid)['last_schedule']['status'] == 'submitted'


def test_corrupt_queue_never_falls_through_to_send(queued):
    root, sid, _ = queued
    chat.session(root, sid).schedule_file.write_text('{broken')
    with pytest.raises(ValueError, match='Invalid schedule'):
        add(queued)
    assert not chat.session(root, sid).requests()
    assert sweep(queued)[0]['error']


def test_revision_conflict_and_projection_repair(queued):
    root, sid, _ = queued
    before = chat.session_state(root, sid)['rev']
    add(queued)
    assert not chat.session_state(root, sid, since_rev=before).get('unchanged')
    with pytest.raises(chat.ScheduleConflictError):
        add(queued, 'stale', 'stale', expected_schedule_revision=0)
    store = chat.session(root, sid)
    data = store.load_schedule(); data['revision'] += 1
    chat._write(store.schedule_file, data)  # crash before rev projection
    rev = chat.session_state(root, sid)['rev']
    assert not chat.session_state(root, sid, since_rev=rev - 1).get('unchanged')


def test_disabled_service_and_manual_pause_hold(queued):
    root, sid, _ = queued
    add(queued)
    sweep(queued, spawn=False)
    assert chat.schedule_state(root, sid)['schedule']['hold_reason'] == 'schedule_service_disabled'
    assert not chat.session(root, sid).requests()
    folder = chat._plan_folder(chat.session(root, sid))
    plan = chat._read(folder.plan_file); plan['status'] = 'paused'; chat._write(folder.plan_file, plan)
    sweep(queued)
    assert chat.schedule_state(root, sid)['schedule']['hold_reason'] == 'workflow_resume'
    plan['status'] = 'running'; chat._write(folder.plan_file, plan)
    sweep(queued)
    assert len(chat.session(root, sid).requests()) == 1


@pytest.mark.parametrize('run_at,zone', [('2099-10-28T17:00:00', 'UTC'), ('2000-01-01T00:00:00Z', 'UTC'), ('2099-01-01T00:00:00Z', 'Bogus/Zone')])
def test_invalid_schedule_times(project, run_at, zone):
    sid = chat.create_session(project)['id']
    with pytest.raises(ValueError):
        chat.create_schedule(project, sid, run_at=run_at, timezone=zone, client_schedule_id='bad', expected_schedule_revision=0)


def _process_sweep(args):
    root, sid = args
    chat.resume = lambda *a, **kw: None
    return chat.dispatch_due_schedules(root, sid, now=time.time() + 100 * 365 * 86400)


def test_competing_processes_claim_once(queued):
    add(queued)
    with ProcessPoolExecutor(2) as pool:
        list(pool.map(_process_sweep, [queued[:2], queued[:2]]))
    store = chat.session(queued[0], queued[1])
    assert len(store.requests()) == len(store.messages()) == len(store.tasks()) == 1


def test_attachment_mapping_limits_and_validation_error(queued, monkeypatch):
    from alfrd import attachments as att
    from tests.test_attachment_routes import png
    root, sid, _ = queued
    store = chat.session(root, sid)
    image = att.store_image(store.dir, png(), 'image/png', 'report.png')
    add(queued, '', 'image-only', attachments=[image.ref])
    add(queued, 'use the same image', 'second', attachments=[image.ref])
    monkeypatch.setattr(chat, 'MAX_SCHEDULE_ITEMS', 2)
    with pytest.raises(att.AttachmentError):
        add(queued, 'third', 'third')
    assert len(chat._schedule_items(store.load_schedule()['batches'][0])) == 2
    sweep(queued)
    [req] = store.requests()
    assert req['text'].count(image.ref) == 2
    assert len(store.messages()[0]['attachments']) == 1
    assert req['scheduled_for'] == '2099-10-28T15:00:00Z'
    assert req['lateness_seconds'] > 0


def test_prompt_limit_is_explicit_and_queue_remains_unchanged(queued, monkeypatch):
    from alfrd import attachments as att
    add(queued)
    root, sid, _ = queued
    before = chat.schedule_state(root, sid)
    monkeypatch.setattr(chat, 'MAX_SCHEDULE_PROMPT', len(chat.build_scheduled_prompt(before['schedule'])))
    with pytest.raises(att.AttachmentError, match='combined prompt'):
        add(queued, 'second', 'second')
    assert chat.schedule_state(root, sid)['schedule_revision'] == before['schedule_revision']


def test_dispatch_validation_preserves_editable_error_queue(queued, monkeypatch):
    root, sid, _ = queued
    add(queued)
    original = chat.settings
    def bad(root):
        raise chat.ExecutionError('invalid agent settings')
    monkeypatch.setattr(chat, 'settings', bad)
    sweep(queued)
    state = chat.schedule_state(root, sid)
    assert state['schedule']['status'] == 'error' and 'invalid agent settings' in state['schedule']['error']
    assert not chat.session(root, sid).requests()
    monkeypatch.setattr(chat, 'settings', original)
    mutate(queued, 'run')
    [req] = chat.session(root, sid).requests()
    assert req['schedule_trigger'] == 'run_now' and req['scheduled_for'] == '2099-10-28T15:00:00Z'


def test_control_file_pause_before_plan_projection_holds(queued):
    add(queued)
    store = chat.session(queued[0], queued[1])
    chat._plan_folder(store).set_control('pause')
    sweep(queued)
    assert not store.requests()
    assert chat.schedule_state(queued[0], queued[1])['schedule']['hold_reason'] == 'workflow_resume'


def test_torn_message_repair_and_progressed_row_is_not_reset(queued, monkeypatch):
    add(queued)
    root, sid, _ = queued
    store = chat.session(root, sid)
    original = chat._publish_scheduled_row
    def crash(*a):
        original(*a)
        raise OSError('after CSV')
    monkeypatch.setattr(chat, '_publish_scheduled_row', crash)
    sweep(queued)
    monkeypatch.setattr(chat, '_publish_scheduled_row', original)
    chat._set_cell(store, 'r0001.plan', chat.settings(root)['orchestrator'], chat.pc.DONE, {chat.pc.TODO})
    sweep(queued)
    assert chat._table(store, chat.settings(root)).row('r0001.plan').cell(chat.settings(root)['orchestrator']) == chat.pc.DONE
    assert len(store.requests()) == 1
