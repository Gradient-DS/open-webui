from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from open_webui.soev.client import ChatEvent, SoevApiError

STATE = {'status': 'ready', 'title': 'Weekoverleg'}


class FakeSoev:
    def __init__(self, *, fail: SoevApiError | None = None, thread: dict | None = None):
        self.calls: list[tuple] = []
        self.fail = fail
        self.thread = thread
        self.closed = False

    def _maybe_fail(self):
        if self.fail is not None:
            raise self.fail

    async def get(self, path, *, as_user=None, params=None):
        self.calls.append(('GET', path, as_user, params))
        self._maybe_fail()
        if path.startswith('/v1/chat/threads/'):
            return self.thread
        return {'data': [{'name': 'meeting', 'kind': 'surface'}]}

    async def post_bytes(self, path, body, *, as_user, params=None):
        self.calls.append(('POST_BYTES', path, as_user, params, len(body)))
        self._maybe_fail()
        return {'id': 'a' * 32, 'size': len(body)}

    async def chat_delete(self, path, *, as_user):
        self.calls.append(('DELETE', path, as_user))
        self._maybe_fail()

    async def chat_stream(self, path, body, *, as_user, thread_id=None, after=0):
        self.calls.append(('STREAM', path, as_user, body, thread_id))
        self._maybe_fail()
        try:
            yield ChatEvent('connection', {'thread_id': thread_id or 'thr-new'})
            yield ChatEvent('meeting_state', {})
        finally:
            self.closed = True


def _client(monkeypatch, fake=None, *, enabled=True, url='http://soev-api'):
    from open_webui.routers import meetings
    from open_webui.utils.auth import get_verified_user

    monkeypatch.setattr(meetings, 'is_feature_enabled', lambda feature: enabled if feature == 'meetings' else True)
    monkeypatch.setattr(meetings.config, 'SOEV_API_URL', url)
    app = FastAPI()
    app.include_router(meetings.router, prefix='/api/v1/meetings')
    user = SimpleNamespace(id='u1', role='admin', email='u@example.com', name='U')
    app.dependency_overrides[get_verified_user] = lambda: user
    if fake is not None:
        app.dependency_overrides[meetings.caller] = lambda: meetings.Caller(fake, 'owui:user:u1')
    return TestClient(app, raise_server_exceptions=False)


def test_meetings_are_refused_for_admins_when_the_flag_is_off(monkeypatch):
    """The tenant gate binds admins."""
    client = _client(monkeypatch, enabled=False)
    assert client.get('/api/v1/meetings/agents').status_code == 403


def test_meetings_need_soev_api(monkeypatch):
    """Without soev-api there is nothing to forward to."""
    client = _client(monkeypatch, url='')
    assert client.get('/api/v1/meetings/agents').status_code == 503


def test_agents_and_list_forward_as_the_caller(monkeypatch):
    """Discovery and listing go to soev-api unchanged, the list scoped to the meeting agent."""
    fake = FakeSoev()
    client = _client(monkeypatch, fake)
    assert client.get('/api/v1/meetings/agents').json()['data'][0]['name'] == 'meeting'
    assert client.get('/api/v1/meetings', params={'limit': 5}).status_code == 200
    assert fake.calls == [
        ('GET', '/v1/agents', 'owui:user:u1', None),
        ('GET', '/v1/chat/threads', 'owui:user:u1', {'agent': 'meeting', 'limit': 5}),
    ]


def test_start_opens_a_meeting_thread_and_returns_once_accepted(monkeypatch):
    """Start forwards the input to the meeting agent and stops following after acceptance."""
    fake = FakeSoev()
    client = _client(monkeypatch, fake)
    start = {'type': 'start', 'title': 'Weekoverleg', 'consent': {'text_version': '2026-10-07', 'at': 'now'}}
    response = client.post('/api/v1/meetings', json={'input': start})
    assert response.status_code == 201, response.text
    assert response.json() == {'id': 'thr-new'}
    assert fake.calls == [('STREAM', '/v1/chat/threads', 'owui:user:u1', {'agent': 'meeting', 'input': start}, None)]
    assert fake.closed


def test_inputs_forward_to_the_thread(monkeypatch):
    """Inputs go to the thread's inputs route unchanged."""
    fake = FakeSoev()
    client = _client(monkeypatch, fake)
    response = client.post('/api/v1/meetings/thr-1/inputs', json={'input': {'type': 'action', 'kind': 'summary'}})
    assert response.status_code == 202, response.text
    assert fake.calls == [
        (
            'STREAM',
            '/v1/chat/threads/thr-1/inputs',
            'owui:user:u1',
            {'input': {'type': 'action', 'kind': 'summary'}},
            'thr-1',
        )
    ]


def test_busy_thread_conflict_reaches_the_client(monkeypatch):
    """A 409 while a turn runs is passed on so the recorder retries."""
    fake = FakeSoev(fail=SoevApiError(409, 'turn_running', 'A turn is running'))
    client = _client(monkeypatch, fake)
    response = client.post('/api/v1/meetings/thr-1/inputs', json={'input': {'type': 'chunk', 'seq': 1}})
    assert response.status_code == 409
    assert response.json()['detail'] == {'code': 'turn_running', 'detail': 'A turn is running'}


def test_get_returns_the_latest_meeting_state(monkeypatch):
    """Only the last meeting_state snapshot is returned, with the thread's run state."""
    events = [
        {'type': 'input', 'payload': {}},
        {'type': 'meeting_state', 'payload': {'status': 'recording'}},
        {'type': 'meeting_state', 'payload': STATE},
        {'type': 'model_output', 'payload': {}},
    ]
    fake = FakeSoev(thread={'thread_id': 'thr-1', 'status': {'state': 'idle', 'position': 4}, 'events': events})
    client = _client(monkeypatch, fake)
    response = client.get('/api/v1/meetings/thr-1')
    assert response.json() == {'status': 'idle', 'state': STATE}


def test_get_without_state_yet(monkeypatch):
    """A thread whose first turn has not produced a snapshot reads as no state."""
    fake = FakeSoev(thread={'thread_id': 'thr-1', 'status': {'state': 'running', 'position': 1}, 'events': []})
    client = _client(monkeypatch, fake)
    assert client.get('/api/v1/meetings/thr-1').json() == {'status': 'running', 'state': None}


def test_delete_forwards_and_maps_not_found(monkeypatch):
    """Delete goes to the thread; a missing thread stays a 404."""
    fake = FakeSoev()
    client = _client(monkeypatch, fake)
    assert client.delete('/api/v1/meetings/thr-1').status_code == 204
    assert fake.calls == [('DELETE', '/v1/chat/threads/thr-1', 'owui:user:u1')]
    fake.fail = SoevApiError(404, 'not_found', 'No such thread')
    assert client.delete('/api/v1/meetings/thr-1').status_code == 404


def test_audio_is_forwarded_with_its_name(monkeypatch):
    """Audio bytes pass through to soev-api's audio store."""
    fake = FakeSoev()
    client = _client(monkeypatch, fake)
    response = client.post('/api/v1/meetings/audio', params={'name': 'chunk-1.webm'}, content=b'\x1a\x45\xdf\xa3' * 10)
    assert response.status_code == 201, response.text
    assert fake.calls == [('POST_BYTES', '/v1/audio', 'owui:user:u1', {'name': 'chunk-1.webm'}, 40)]


@pytest.mark.parametrize('declared', [True, False])
def test_audio_over_64_mib_is_refused(monkeypatch, declared):
    """The cap holds whether or not the client declares a length."""
    from open_webui.routers import meetings

    monkeypatch.setattr(meetings, 'MAX_AUDIO_BYTES', 16)
    fake = FakeSoev()
    client = _client(monkeypatch, fake)
    body = b'x' * 17
    content = body if declared else iter([body[:8], body[8:]])
    response = client.post('/api/v1/meetings/audio', params={'name': 'a.webm'}, content=content)
    assert response.status_code == 413
    assert fake.calls == []


def test_upstream_errors_keep_status_and_code(monkeypatch):
    """soev-api problems map to the same status with code and detail."""
    fake = FakeSoev(fail=SoevApiError(403, 'agent_not_allowed', 'Agent not allowed', retry_after='3'))
    client = _client(monkeypatch, fake)
    response = client.get('/api/v1/meetings/agents')
    assert response.status_code == 403
    assert response.json()['detail'] == {'code': 'agent_not_allowed', 'detail': 'Agent not allowed'}
    assert response.headers['Retry-After'] == '3'
