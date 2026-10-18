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

from open_webui.models.config import Config
from open_webui.routers.feedback_report import router
from open_webui.utils.auth import get_verified_user
from open_webui.utils.feedback_report import (
    build_feedback_event,
    build_http_error_body,
    get_current_trace_id,
    post_feedback_to_router,
    post_feedback_to_slack,
)


def _make_client(monkeypatch, *, enabled=True, webhook_url='', router_url='', include_identity=True):
    # The router reads its settings via ``await Config.get_many('feedback_report.*')``
    # (the storage keys of ENABLE_FEEDBACK_REPORTING & co); patch the per-key
    # store so the tests stay hermetic — no config DB involved.
    values = {
        'feedback_report.enable': enabled,
        'feedback_report.include_user_identity': include_identity,
        'feedback_report.slack_webhook_url': webhook_url,
        'feedback_report.webhook_url': router_url,
        'feedback_report.trace_url_template': '',
    }

    async def fake_get_many(*keys):
        return {key: values[key] for key in keys if key in values}

    monkeypatch.setattr(Config, 'get_many', staticmethod(fake_get_many))

    app = FastAPI()
    app.include_router(router, prefix='/api/v1/feedback')
    app.dependency_overrides[get_verified_user] = lambda: SimpleNamespace(
        id='user-1', email='user@example.com', name='Test User'
    )
    return TestClient(app)


def test_submit_happy_path(monkeypatch):
    client = _make_client(monkeypatch)
    res = client.post('/api/v1/feedback/report', json={'category': 'bug', 'description': 'It broke'})
    assert res.status_code == 200
    assert res.json() == {'status': True}


def test_submit_disabled_returns_404(monkeypatch):
    client = _make_client(monkeypatch, enabled=False)
    res = client.post('/api/v1/feedback/report', json={'category': 'bug', 'description': 'It broke'})
    assert res.status_code == 404


def test_context_allowlist_rejects_unknown_key(monkeypatch):
    # The allowlist (extra='forbid') is the guarantee that no chat content leaks through.
    client = _make_client(monkeypatch)
    res = client.post(
        '/api/v1/feedback/report',
        json={'category': 'bug', 'description': 'x', 'context': {'chat_content': 'leaked message'}},
    )
    assert res.status_code == 422


def test_empty_description_rejected(monkeypatch):
    client = _make_client(monkeypatch)
    res = client.post('/api/v1/feedback/report', json={'category': 'bug', 'description': ''})
    assert res.status_code == 422


def test_invalid_category_rejected(monkeypatch):
    client = _make_client(monkeypatch)
    res = client.post('/api/v1/feedback/report', json={'category': 'spam', 'description': 'x'})
    assert res.status_code == 422


def test_allowlisted_context_accepted(monkeypatch):
    client = _make_client(monkeypatch)
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


# --- Notification router: delivery precedence --------------------------------


@pytest.mark.asyncio
async def test_post_feedback_to_router_no_url_returns_false():
    assert await post_feedback_to_router({'category': 'bug'}, '') is False


@pytest.mark.asyncio
async def test_post_feedback_to_router_bad_url_returns_false():
    # Unreachable router must not raise — delivery is best-effort, and a
    # submission must never fail because the router is down.
    assert await post_feedback_to_router({'category': 'bug'}, 'http://127.0.0.1:9/nope') is False


def test_router_url_takes_precedence_over_slack(monkeypatch):
    # A migrated tenant still has slack_webhook_url persisted in its config DB,
    # so without this precedence it would deliver twice.
    calls = {'router': 0, 'slack': 0}

    async def fake_router(event, url, template=''):
        calls['router'] += 1
        return True

    async def fake_slack(event, url, template=''):
        calls['slack'] += 1
        return True

    monkeypatch.setattr('open_webui.routers.feedback_report.post_feedback_to_router', fake_router)
    monkeypatch.setattr('open_webui.routers.feedback_report.post_feedback_to_slack', fake_slack)

    client = _make_client(monkeypatch, webhook_url='https://hooks.slack.com/x', router_url='http://router/feedback')
    res = client.post('/api/v1/feedback/report', json={'category': 'bug', 'description': 'x'})

    assert res.status_code == 200
    assert calls == {'router': 1, 'slack': 0}


def test_falls_back_to_slack_when_router_unset(monkeypatch):
    # A tenant on the new image but the old chart must behave exactly as before.
    calls = {'router': 0, 'slack': 0}

    async def fake_router(event, url, template=''):
        calls['router'] += 1
        return True

    async def fake_slack(event, url, template=''):
        calls['slack'] += 1
        return True

    monkeypatch.setattr('open_webui.routers.feedback_report.post_feedback_to_router', fake_router)
    monkeypatch.setattr('open_webui.routers.feedback_report.post_feedback_to_slack', fake_slack)

    client = _make_client(monkeypatch, webhook_url='https://hooks.slack.com/x')
    res = client.post('/api/v1/feedback/report', json={'category': 'bug', 'description': 'x'})

    assert res.status_code == 200
    assert calls == {'router': 0, 'slack': 1}


def test_router_url_is_non_persistent():
    # seed_defaults writes every DEFAULT_CONFIG key at first boot. If this key
    # were persistent, that boot would pin '' and later wiring the chart value
    # would silently do nothing — the trap that hit sync_daemon.enabled.
    assert Config.persistent_enabled_for('feedback_report.webhook_url') is False
    # The sibling keys are admin-toggleable and must stay persistent.
    assert Config.persistent_enabled_for('feedback_report.enable') is True
    assert Config.persistent_enabled_for('feedback_report.slack_webhook_url') is True


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
