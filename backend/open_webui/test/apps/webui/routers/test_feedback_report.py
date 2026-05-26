"""Unit tests for the product-feedback router and service.

The router is mounted on a minimal FastAPI app with the auth dependency
overridden, so these run without a database or the full application.
"""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry import context as otel_context, trace
from opentelemetry.sdk.trace import TracerProvider

from open_webui.routers.feedback_report import router
from open_webui.utils.auth import get_verified_user
from open_webui.utils.feedback_report import (
    build_feedback_event,
    build_http_error_body,
    get_current_trace_id,
    post_feedback_to_slack,
)


def _make_client(*, enabled=True, webhook_url='', include_identity=True):
    app = FastAPI()
    app.state.config = SimpleNamespace(
        ENABLE_FEEDBACK_REPORTING=enabled,
        FEEDBACK_REPORT_SLACK_WEBHOOK_URL=webhook_url,
        FEEDBACK_REPORT_INCLUDE_USER_IDENTITY=include_identity,
    )
    app.include_router(router, prefix='/api/v1/feedback')
    app.dependency_overrides[get_verified_user] = lambda: SimpleNamespace(
        id='user-1', email='user@example.com', name='Test User'
    )
    return TestClient(app)


def test_submit_happy_path():
    client = _make_client()
    res = client.post('/api/v1/feedback/report', json={'category': 'bug', 'description': 'It broke'})
    assert res.status_code == 200
    assert res.json() == {'status': True}


def test_submit_disabled_returns_404():
    client = _make_client(enabled=False)
    res = client.post('/api/v1/feedback/report', json={'category': 'bug', 'description': 'It broke'})
    assert res.status_code == 404


def test_context_allowlist_rejects_unknown_key():
    # The allowlist (extra='forbid') is the guarantee that no chat content leaks through.
    client = _make_client()
    res = client.post(
        '/api/v1/feedback/report',
        json={'category': 'bug', 'description': 'x', 'context': {'chat_content': 'leaked message'}},
    )
    assert res.status_code == 422


def test_empty_description_rejected():
    client = _make_client()
    res = client.post('/api/v1/feedback/report', json={'category': 'bug', 'description': ''})
    assert res.status_code == 422


def test_invalid_category_rejected():
    client = _make_client()
    res = client.post('/api/v1/feedback/report', json={'category': 'spam', 'description': 'x'})
    assert res.status_code == 422


def test_allowlisted_context_accepted():
    client = _make_client()
    res = client.post(
        '/api/v1/feedback/report',
        json={
            'category': 'error',
            'description': 'failed',
            'context': {'route': '/c/123', 'trace_id': 'abc', 'error_message': 'boom'},
        },
    )
    assert res.status_code == 200


def test_build_feedback_event_enrichment():
    user = SimpleNamespace(id='u1', email='u@example.com', name='U')
    event = build_feedback_event(
        category='bug', description='desc', context={'route': '/x'}, user=user, include_identity=True
    )
    assert event['event_type'] == 'user_feedback'
    assert event['category'] == 'bug'
    assert event['route'] == '/x'
    assert event['user'] == {'id': 'u1', 'email': 'u@example.com', 'name': 'U'}
    assert 'ts' in event
    assert 'app_version' in event


def test_build_feedback_event_omits_identity_when_disabled():
    user = SimpleNamespace(id='u1', email='u@example.com', name='U')
    event = build_feedback_event(category='idea', description='d', context={}, user=user, include_identity=False)
    assert 'user' not in event


@pytest.mark.asyncio
async def test_post_feedback_to_slack_no_url_returns_false():
    assert await post_feedback_to_slack({'category': 'bug'}, '') is False


@pytest.mark.asyncio
async def test_post_feedback_to_slack_bad_url_returns_false():
    event = {
        'category': 'bug',
        'description': 'x',
        'tenant': 't',
        'app_version': '1',
        'client_name': '',
    }
    # An unreachable host must not raise — delivery is best-effort.
    assert await post_feedback_to_slack(event, 'http://127.0.0.1:9/nope') is False


# --- Phase 2: trace-id capture and the HTTP error body -----------------------


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
