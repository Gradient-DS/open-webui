from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from open_webui.soev.client import SoevApiError

TRACE = b'{"format":"soev.trace/1","thread_id":"thr-abc","exported_at":"2026-10-20T14:02:11Z","events":[]}'


class FakeSoev:
    def __init__(self, *, chunks: list[bytes] | None = None, fail: SoevApiError | None = None):
        self.calls: list[tuple[str, str]] = []
        self.chunks = chunks if chunks is not None else [TRACE[:20], TRACE[20:]]
        self.fail = fail
        self.closed = False

    async def stream(self, path, *, as_user):
        self.calls.append((path, as_user))
        try:
            if self.fail is not None:
                raise self.fail
            for chunk in self.chunks:
                yield chunk
        finally:
            self.closed = True


def _client(monkeypatch, fake: FakeSoev, *, role='admin', url='http://soev-api', enabled=True):
    from open_webui import config
    from open_webui.main import soev_api_error_handler, soev_not_configured_handler
    from open_webui.routers import agent_threads
    from open_webui.soev.availability import SoevNotConfigured
    from open_webui.utils.auth import get_current_user

    monkeypatch.setattr(config, 'SOEV_API_URL', url)
    monkeypatch.setattr(config, 'ENABLE_AGENT_TRACE_DOWNLOAD', enabled)
    monkeypatch.setattr(agent_threads.identity, 'build_client', lambda **kwargs: fake)
    app = FastAPI(
        exception_handlers={SoevNotConfigured: soev_not_configured_handler, SoevApiError: soev_api_error_handler}
    )
    app.include_router(agent_threads.router, prefix='/api/v1/agent-threads')
    user = SimpleNamespace(id='u1', role=role, email='u@example.com', name='U')
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app, raise_server_exceptions=False)


def test_an_admin_downloads_the_trace_file_unchanged(monkeypatch):
    """The export bytes pass through as a JSON attachment named after the thread, read as the caller."""
    fake = FakeSoev()
    response = _client(monkeypatch, fake).get('/api/v1/agent-threads/thr-abc/trace')
    assert response.status_code == 200, response.text
    assert response.content == TRACE
    assert response.headers['content-type'] == 'application/json'
    assert response.headers['content-disposition'] == 'attachment; filename="thr-abc.trace.json"'
    assert fake.calls == [('/v1/chat/threads/thr-abc/export', 'owui:user:u1')]
    assert fake.closed


def test_a_thread_soev_api_does_not_give_the_caller_is_a_404(monkeypatch):
    """soev-api answers another user's thread and an unknown one alike: 404 not_found."""
    fake = FakeSoev(fail=SoevApiError(404, 'not_found', 'Requested resource not found'))
    response = _client(monkeypatch, fake).get('/api/v1/agent-threads/thr-other/trace')
    assert response.status_code == 404
    assert response.json()['detail']['code'] == 'not_found'
    assert fake.closed


def test_a_tenant_without_the_flag_answers_404_before_soev_api(monkeypatch):
    fake = FakeSoev()
    response = _client(monkeypatch, fake, enabled=False).get('/api/v1/agent-threads/thr-abc/trace')
    assert response.status_code == 404
    assert fake.calls == []


def test_a_user_who_is_not_an_admin_is_refused_before_soev_api(monkeypatch):
    fake = FakeSoev()
    response = _client(monkeypatch, fake, role='user').get('/api/v1/agent-threads/thr-abc/trace')
    assert response.status_code == 401
    assert fake.calls == []


@pytest.mark.parametrize('thread_id', ['%2E', '%2E%2E', 'thr%0Aabc', 'thr%22abc'])
def test_a_thread_id_outside_the_token_alphabet_never_reaches_soev_api(monkeypatch, thread_id):
    """Dot segments would resolve away upstream; quotes and newlines would break the download header."""
    fake = FakeSoev()
    response = _client(monkeypatch, fake).get(f'/api/v1/agent-threads/{thread_id}/trace')
    assert response.status_code in {404, 422}
    assert fake.calls == []


def test_the_download_needs_soev_api(monkeypatch):
    fake = FakeSoev()
    response = _client(monkeypatch, fake, url='').get('/api/v1/agent-threads/thr-abc/trace')
    assert response.status_code == 503
    assert response.json()['detail']['code'] == 'soev_not_configured'
    assert fake.calls == []
