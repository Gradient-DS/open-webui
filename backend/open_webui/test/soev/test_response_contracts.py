"""Exercise HTTP parsing with fixtures validated against published API responses."""

import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from jsonschema import Draft202012Validator
from open_webui.soev import acting, offboarding, product_config, threads
from open_webui.soev.client import SoevApiError, build_service_client
from open_webui.test.soev.test_offboarding_feed import task
from open_webui.test.soev.test_product_config import document

SPEC = json.loads(Path(__file__).with_name('soev_api_contract.json').read_text())
ACK = '/v1/offboarding/{job_id}/acknowledgement'
FEEDBACK = '/v1/threads/{thread_id}/feedback'
TOKENS = {'prompt_tokens': 3, 'completion_tokens': 2, 'total_tokens': 5}
METRICS = {'model_outputs': 1, 'unknown_outputs': 0, 'known_tokens': TOKENS, 'tokens': TOKENS}
RESPONSES = {
    '/v1/products/owui/config': document(),
    '/v1/offboarding': {'items': [task()]},
    ACK: task(),
    '/v1/bug-reports': {'status': True},
    FEEDBACK: None,
    '/v1/feedback': {
        'items': [
            {
                'id': 'fictional-feedback',
                'thread_id': 'fictional-thread',
                'position': 1,
                'rating': 1,
                'model': 'fictional/model',
                'agent': 'fictional-agent',
                'time': '2026-11-10T12:00:00Z',
                'comment': 'Fictional feedback',
                'reason': 'accuracy',
                'tags': ['language'],
            }
        ],
        'next_cursor': None,
    },
    '/v1/feedback/summary': {
        'items': [
            {'model': 'fictional/model', 'agent': 'fictional-agent', 'rating': 1, 'count': 1, 'period': '2026-11-10'}
        ],
        'next_cursor': None,
    },
    '/v1/threads/usage': {
        **METRICS,
        'by_model': [{'key': 'fictional/model', **METRICS}],
        'by_person': [{'key': task()['person'], **METRICS}],
        'by_period': [{'key': '2026-11-10', **METRICS}],
    },
}


def response(path: str, body: dict | None = None) -> httpx.Response:
    method, operation = next(iter(SPEC['paths'][path].items()))
    status, declared = next(iter(operation['responses'].items()))
    payload = copy.deepcopy(RESPONSES[path] if body is None else body)
    if 'content' not in declared:
        assert payload is None
        return httpx.Response(int(status))
    schema = {'components': SPEC['components'], **declared['content']['application/json']['schema']}
    Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER).validate(payload)
    return httpx.Response(int(status), json=payload)


def actual_path(path: str) -> str:
    return path.replace('{job_id}', task()['id']).replace('{thread_id}', 'fictional-thread')


@pytest.fixture
def requester():
    requester = acting._requester_ref.set('owui:user:cobalt')
    resolver = acting._token_resolver.set(AsyncMock(return_value='fictional-login:owui:user:cobalt'))
    yield
    acting._token_resolver.reset(resolver)
    acting._requester_ref.reset(requester)


@pytest.mark.asyncio
@pytest.mark.parametrize('path', RESPONSES)
async def test_real_service_client_parses_each_published_response(identity_config, identity_http, path):
    requests, responses = identity_http
    responses.append(response(path))
    client = build_service_client()
    if 'get' in SPEC['paths'][path]:
        result = await client.get(actual_path(path))
    else:
        result = await client.send('POST', actual_path(path), {}, idempotency_key='fictional-contract-request')
    assert result == RESPONSES[path]
    assert requests[0].url.path == actual_path(path)
    assert requests[0].headers['Authorization'] == 'Bearer ' + identity_config[0].config.SOEV_API_KEY
    assert 'X-Soev-User-Token' not in requests[0].headers


@pytest.mark.asyncio
async def test_product_config_consumes_published_object(identity_config, identity_http):
    requests, responses = identity_http
    responses.append(response('/v1/products/owui/config'))
    assert await product_config.refresh() == RESPONSES['/v1/products/owui/config']
    assert requests[0].url.path == '/v1/products/owui/config'
    assert 'X-Soev-User-Token' not in requests[0].headers


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['delete', 'transfer', 'archive'])
@pytest.mark.parametrize(
    'error', [None, 'target_missing', 'source_conflict', 'local_cleanup_failed', 'unsupported_content']
)
async def test_offboarding_poll_and_acknowledgement_use_real_http(
    identity_config, identity_http, monkeypatch, mode, error
):
    requests, responses = identity_http
    work = task(mode)
    responses.extend([response('/v1/offboarding', {'items': [work]}), response(ACK, work)])
    apply = AsyncMock(return_value=error)
    monkeypatch.setattr(offboarding, 'apply', apply)
    monkeypatch.setattr(offboarding, 'v2_enabled', lambda: True)
    await offboarding.poll()
    apply.assert_awaited_once_with(offboarding.Task.model_validate(work))
    assert [request.url.path for request in requests] == ['/v1/offboarding', actual_path(ACK)]
    assert json.loads(requests[1].content) == {'error': error}
    assert requests[1].headers['Idempotency-Key'] == 'offboarding-ack:' + work['id']
    assert all('X-Soev-User-Token' not in request.headers for request in requests)


@pytest.mark.asyncio
async def test_empty_published_feed_does_not_apply_or_acknowledge(identity_config, identity_http, monkeypatch):
    requests, responses = identity_http
    responses.append(response('/v1/offboarding', {'items': []}))
    apply = AsyncMock()
    monkeypatch.setattr(offboarding, 'apply', apply)
    monkeypatch.setattr(offboarding, 'v2_enabled', lambda: True)
    await offboarding.poll()
    apply.assert_not_awaited()
    assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'body', [{}, {'items': None}, {'items': {}}, {'items': 'invalid'}, {'items': 1}, {'items': False}, []]
)
async def test_malformed_feed_never_applies_or_acknowledges(identity_config, identity_http, monkeypatch, body):
    requests, responses = identity_http
    responses.append(httpx.Response(200, json=body))
    apply = AsyncMock()
    monkeypatch.setattr(offboarding, 'apply', apply)
    monkeypatch.setattr(offboarding, 'v2_enabled', lambda: True)
    expected = SoevApiError if isinstance(body, list) else ValueError
    with pytest.raises(expected, match='invalid JSON object|Invalid offboarding feed'):
        await offboarding.poll()
    apply.assert_not_awaited()
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_bug_report_consumes_object_with_requesting_person(identity_config, identity_http):
    from open_webui.routers.feedback_report import FeedbackReportForm, submit_feedback_report

    requests, responses = identity_http
    responses.append(response('/v1/bug-reports'))
    form = FeedbackReportForm(category='bug', description='Fictional report')
    result = await submit_feedback_report(form, user=SimpleNamespace(id='cobalt'))
    assert result == {'status': True}
    assert requests[0].url.path == '/v1/bug-reports'
    assert json.loads(requests[0].content) == form.model_dump(exclude_none=True)
    assert requests[0].headers['X-Soev-User-Token'] == 'fictional-login:owui:user:cobalt'
    assert requests[0].headers['Idempotency-Key']


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'path,method',
    [('/v1/feedback', 'feedbacks'), ('/v1/feedback/summary', 'feedback_summary'), ('/v1/threads/usage', 'usage')],
)
async def test_typed_thread_reads_consume_published_objects(identity_config, identity_http, requester, path, method):
    requests, responses = identity_http
    responses.append(response(path))
    result = await getattr(threads.build_client(), method)()
    assert result.model_dump(mode='json') == RESPONSES[path]
    assert requests[0].url.path == path
    assert requests[0].headers['X-Soev-User-Token'] == 'fictional-login:owui:user:cobalt'


@pytest.mark.asyncio
async def test_feedback_write_accepts_published_no_content(identity_config, identity_http, requester):
    requests, responses = identity_http
    responses.append(response(FEEDBACK))
    assert (
        await threads.build_client().feedback(
            'fictional-thread', position=1, rating=1, reason='accuracy', tags=['language']
        )
        is None
    )
    assert requests[0].url.path == actual_path(FEEDBACK)
    assert json.loads(requests[0].content) == {
        'position': 1,
        'rating': 1,
        'comment': None,
        'comment_authored': False,
        'reason': 'accuracy',
        'tags': ['language'],
    }
    assert requests[0].headers['X-Soev-User-Token'] == 'fictional-login:owui:user:cobalt'
