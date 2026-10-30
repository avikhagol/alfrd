# ruff: noqa: F811 - imported pytest fixtures are requested by name
from alfrd import chat
from tests.test_attachment_routes import web, project  # noqa: F401


def test_schedule_api_roundtrip(web, monkeypatch):
    client, headers, base, sid, root = web
    monkeypatch.setattr(chat, 'resume', lambda *a, **kw: None)
    url = f'{base}/sessions/{sid}'
    create = {'run_at': '2099-10-28T17:00:00Z', 'timezone': 'UTC', 'client_schedule_id': 'api-create', 'expected_schedule_revision': 0}
    assert client.post(url + '/schedule', json=create).status_code == 403
    made = client.post(url + '/schedule', json=create, headers=headers)
    assert made.status_code == 201, made.get_json()
    bid = made.get_json()['schedule']['id']
    assert client.post(url + '/schedule', json=create, headers=headers).status_code == 200
    added = client.post(url + '/messages', json={'text': '@codex first', 'client_message_id': 'first'}, headers=headers)
    assert added.status_code == 201 and added.get_json()['queued']
    iid = added.get_json()['item']['id']
    def mutate(path, method='post', **payload):
        state = client.get(url + '/schedule').get_json()
        return getattr(client, method)(url + '/schedule/' + bid + path,
            json={'expected_schedule_revision': state['schedule_revision'], 'operation_id': path.replace('/', '.') + method, **payload}, headers=headers)
    assert mutate('/items/' + iid, 'patch', text='first edited', attachments=[]).status_code == 200
    second = client.post(url + '/messages', json={'text': 'second', 'client_message_id': 'second'}, headers=headers).get_json()['item']
    assert mutate('/items/' + second['id'], 'delete').status_code == 200
    assert mutate('', 'patch', run_at='2099-10-29T17:00:00Z', timezone='UTC').status_code == 200
    stale = client.post(url + '/messages', json={'text': 'stale', 'schedule_id': bid, 'expected_schedule_revision': 0}, headers=headers)
    assert stale.status_code == 409 and stale.get_json()['schedule']['id'] == bid
    assert mutate('/run').status_code == 202
    assert len(chat.session(root, sid).requests()) == 1
    late = client.post(url + '/messages', json={'text': 'late', 'schedule_id': bid}, headers=headers)
    assert late.status_code == 409
    assert len(chat.session(root, sid).requests()) == 1
    assert mutate('/cancel').status_code == 409
    assert client.get(url).get_json()['last_schedule']['request_id'] == 'r0001'


def test_api_validation_and_service_disabled(web):
    client, headers, base, sid, _ = web
    url = f'{base}/sessions/{sid}/schedule'
    assert client.post(url, headers=headers, json=[]).status_code == 400
    payload = {'run_at': '2099-01-01T00:00:00', 'timezone': 'UTC', 'client_schedule_id': 'x', 'expected_schedule_revision': 0}
    assert client.post(url, headers=headers, json=payload).status_code == 400
    assert client.get(url.replace(sid, 'missing')).status_code == 404
    client.application.config['RUNTIME_MUTATIONS_ENABLED'] = False
    assert client.get(url).get_json()['schedule_service']['available'] is False
