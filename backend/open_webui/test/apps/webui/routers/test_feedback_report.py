"""Bug reports preserve user authority and reject message and file content."""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from open_webui.routers.feedback_report import router
from open_webui.utils.auth import get_verified_user
from open_webui.utils.feedback_report import build_http_error_body, get_current_trace_id
from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider


@pytest.fixture
def forwarding(monkeypatch):
    client = AsyncMock()
    client.send.return_value = {'status': True}
    monkeypatch.setattr('open_webui.routers.feedback_report.build_client', lambda **kw: client)
    app = FastAPI()
    app.include_router(router, prefix='/api/v1/feedback')
    app.dependency_overrides[get_verified_user] = lambda: SimpleNamespace(id='fictional-user')
    return TestClient(app), client


def test_submit_forwards_only_allowlisted_fields_with_user_authority(forwarding):
    client, api = forwarding
    body = {
        'category': 'bug',
        'description': 'Fictional report',
        'context': {'route': '/c/fictional', 'trace_id': 'fictional-trace'},
    }
    response = client.post('/api/v1/feedback/report', json=body)
    assert response.status_code == 200
    assert response.json() == {'status': True}
    call = api.send.await_args
    assert call.args == ('POST', '/v1/bug-reports', body)
    assert call.kwargs['as_user'] == 'owui:user:fictional-user'
    assert call.kwargs['idempotency_key']


@pytest.mark.parametrize('field', ['messages', 'files', 'content', 'chat_content', 'user', 'destination'])
@pytest.mark.parametrize('nested', [True, False])
def test_content_and_unknown_fields_never_forward(forwarding, field, nested):
    client, api = forwarding
    body = {'category': 'bug', 'description': 'Fictional report'}
    body.update({'context': {field: 'Fictional private text'}} if nested else {field: 'Fictional private text'})
    assert client.post('/api/v1/feedback/report', json=body).status_code == 422
    api.send.assert_not_awaited()


@pytest.mark.parametrize(
    'body',
    [
        {'category': 'spam', 'description': 'x'},
        {'category': 'bug', 'description': ''},
        {'category': 'bug', 'description': 'x' * 5001},
        {'category': 'bug', 'description': 'x', 'context': {'route': 'x' * 2049}},
    ],
)
def test_bounds_refuse_before_forward(forwarding, body):
    client, api = forwarding
    assert client.post('/api/v1/feedback/report', json=body).status_code == 422
    api.send.assert_not_awaited()


def test_anonymous_never_forwards(forwarding):
    from fastapi import HTTPException

    client, api = forwarding

    def refused():
        raise HTTPException(401)

    client.app.dependency_overrides[get_verified_user] = refused
    assert (
        client.post('/api/v1/feedback/report', json={'category': 'bug', 'description': 'Fictional report'}).status_code
        == 401
    )
    api.send.assert_not_awaited()


@contextmanager
def _active_span():
    """Run the block with a recording OTel span active in the current context."""
    span = TracerProvider().get_tracer('test').start_span('feedback-test')
    token = otel_context.attach(trace.set_span_in_context(span))
    try:
        yield
    finally:
        otel_context.detach(token)
        span.end()


def test_get_current_trace_id_returns_none_without_span():
    assert get_current_trace_id() is None


def test_get_current_trace_id_returns_hex_under_span():
    with _active_span():
        trace_id = get_current_trace_id()

    assert trace_id is not None
    assert len(trace_id) == 32
    int(trace_id, 16)  # must be valid hex


def test_build_http_error_body_omits_trace_id_without_span():
    assert build_http_error_body('Not found') == {'detail': 'Not found'}


def test_build_http_error_body_includes_trace_id_under_span():
    with _active_span():
        body = build_http_error_body('Boom')

    assert body['detail'] == 'Boom'
    assert len(body['trace_id']) == 32
