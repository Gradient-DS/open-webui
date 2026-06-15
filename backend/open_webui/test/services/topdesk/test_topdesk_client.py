"""TopdeskClient — REST request shape, error envelope, offset paging, retry, probe.

All tests run against an in-memory ``httpx.MockTransport`` (zero network) and
load the committed REST fixtures. ``asyncio.sleep`` is patched to a no-op so retry
paths run instantly. They use ``asyncio.run`` (no pytest-asyncio dependency),
matching the Confluence basic-auth tests.
"""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path

import httpx
import pytest

from open_webui.services.topdesk.topdesk_client import (
    TopdeskApiError,
    TopdeskAuthError,
    TopdeskClient,
    TopdeskTransientError,
)

_FIXTURES = Path(__file__).parent / 'fixtures'
_BASE_URL = 'https://tenant.topdesk.net'
_KB_PATH = '/services/knowledge-base-v1'

_ACCEPT_LIST = 'application/x.topdesk-kb-ki-list-v1+json'
_ACCEPT_ITEM = 'application/x.topdesk-kb-ki-v1+json'


def _fixture(name: str) -> dict:
    return json.loads((_FIXTURES / name).read_text())


def _client(handler) -> TopdeskClient:
    """Build a TopdeskClient whose transport is the given mock handler."""
    client = TopdeskClient(
        base_url=_BASE_URL,
        username='operator',
        app_password='s3cr3t',
        kb_api_path=_KB_PATH,
    )
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Make all backoff/retry sleeps instant."""

    async def _instant(_seconds):
        return None

    monkeypatch.setattr('open_webui.services.topdesk.topdesk_client.asyncio.sleep', _instant)


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------
# Request shape + auth header
# ---------------------------------------------------------------------


def test_rest_list_request_shape_and_auth_header():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured['method'] = request.method
        captured['path'] = request.url.path
        captured['params'] = dict(request.url.params)
        captured['authorization'] = request.headers.get('authorization')
        captured['accept'] = request.headers.get('accept')
        return httpx.Response(200, json=_fixture('items_page_1.json'))

    client = _client(handler)

    async def _go():
        data = await client.list_knowledge_items(start=0, page_size=3, query='parent.id==X', language='nl')
        await client.close()
        return data

    data = _run(_go())

    assert captured['method'] == 'GET'
    assert captured['path'] == _KB_PATH + '/knowledgeItems'
    assert captured['params']['start'] == '0'
    assert captured['params']['page_size'] == '3'
    # The default field selector must request content/status/etc. subfields.
    assert 'modificationDate' in captured['params']['fields']
    assert 'content' in captured['params']['fields']
    assert captured['params']['query'] == 'parent.id==X'
    assert captured['params']['language'] == 'nl'
    expected = 'Basic ' + base64.b64encode(b'operator:s3cr3t').decode('ascii')
    assert captured['authorization'] == expected
    assert captured['accept'] == _ACCEPT_LIST
    # Returns the raw {item, next?} page object.
    assert [i['number'] for i in data['item']] == ['KI 0001', 'KI 0002', 'KI 0003']


def test_kb_api_url_property():
    client = TopdeskClient(base_url=_BASE_URL + '/', kb_api_path='services/knowledge-base-v1/')
    assert client.kb_api_url == _BASE_URL + '/services/knowledge-base-v1'


def test_page_size_is_clamped_to_api_max():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured['page_size'] = request.url.params.get('page_size')
        return httpx.Response(200, json=_fixture('items_empty.json'))

    client = _client(handler)

    async def _go():
        await client.list_knowledge_items(page_size=99999)
        await client.close()

    _run(_go())
    assert captured['page_size'] == '1000'


def test_async_context_manager_runs_request_and_closes_client():
    """`async with TopdeskClient(...)` exposes a working client inside the block
    and closes the underlying httpx client on exit."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_fixture('items_page_1.json'))

    client = _client(handler)
    inner = client._client  # the mock-transport httpx client

    async def _go():
        async with client as c:
            data = await c.list_knowledge_items()
            assert 'item' in data
            assert not inner.is_closed
        return inner.is_closed

    closed = _run(_go())
    assert closed is True
    # close() also drops the reference so a fresh client would be lazily created.
    assert client._client is None


# ---------------------------------------------------------------------
# Structured error envelope → raises (HTTP 400 with {errors:[...]})
# ---------------------------------------------------------------------


def test_400_error_body_raises_topdesk_api_error_with_message():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json=_fixture('items_error.json'))

    client = _client(handler)

    async def _go():
        try:
            await client.list_knowledge_items(query='modificationDate=gt=bad')
        finally:
            await client.close()

    with pytest.raises(TopdeskApiError) as exc:
        _run(_go())

    assert 'not a valid FIQL selector' in str(exc.value)
    assert exc.value.errors  # the raw errors array is preserved


# ---------------------------------------------------------------------
# list_knowledge_items + offset pagination loop
# ---------------------------------------------------------------------


def test_list_knowledge_items_returns_page_object():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(206, json=_fixture('items_page_1.json'))

    client = _client(handler)

    async def _go():
        data = await client.list_knowledge_items()
        await client.close()
        return data

    data = _run(_go())
    assert [n['number'] for n in data['item']] == ['KI 0001', 'KI 0002', 'KI 0003']
    assert data['next']  # partial page → has a next marker


def test_iter_all_walks_pages_via_offset_and_terminates():
    pages = {0: _fixture('items_page_1.json'), 3: _fixture('items_page_2.json')}
    seen_starts: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        start = int(request.url.params.get('start', '0'))
        seen_starts.append(start)
        return httpx.Response(200, json=pages[start])

    client = _client(handler)

    async def _go():
        items = await client.iter_all_knowledge_items(page_size=3)
        await client.close()
        return items

    items = _run(_go())
    numbers = [n['number'] for n in items]
    assert numbers == ['KI 0001', 'KI 0002', 'KI 0003', 'KI 0004', 'KI 0005']
    # page 1 fetched at start=0 (has next), page 2 at start=3 (short page → stop).
    assert seen_starts == [0, 3]


def test_iter_all_empty_page_yields_no_items():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_fixture('items_empty.json'))

    client = _client(handler)

    async def _go():
        items = await client.iter_all_knowledge_items()
        await client.close()
        return items

    assert _run(_go()) == []


def test_iter_all_safety_cap_bounds_requests():
    """A server that always returns a full page with a `next` must still terminate
    at the max_pages cap (no infinite loop)."""
    calls = {'count': 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls['count'] += 1
        return httpx.Response(
            206,
            json={'item': [{'id': str(calls['count']), 'number': f'KI {calls["count"]}'}], 'next': 'more'},
        )

    client = _client(handler)

    async def _go():
        items = await client.iter_all_knowledge_items(page_size=1, max_pages=5)
        await client.close()
        return items

    items = _run(_go())
    assert calls['count'] == 5
    assert len(items) == 5


# ---------------------------------------------------------------------
# get_knowledge_item + tree traversal
# ---------------------------------------------------------------------


def test_get_knowledge_item_returns_item_with_content():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured['path'] = request.url.path
        captured['accept'] = request.headers.get('accept')
        captured['fields'] = request.url.params.get('fields')
        return httpx.Response(200, json=_fixture('item_with_content.json'))

    client = _client(handler)

    async def _go():
        item = await client.get_knowledge_item('11111111-1111-4111-8111-111111111111')
        await client.close()
        return item

    item = _run(_go())
    assert item['number'] == 'KI 0001'
    assert item['translation']['content']['content'].startswith('<h1>')
    assert item['parent']['id'] == 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
    assert item['availableTranslations'] == ['en', 'nl']
    assert captured['path'] == _KB_PATH + '/knowledgeItems/11111111-1111-4111-8111-111111111111'
    assert captured['accept'] == _ACCEPT_ITEM
    assert 'content' in captured['fields']


def test_get_knowledge_item_none_when_404():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={'message': 'not found'})

    client = _client(handler)

    async def _go():
        item = await client.get_knowledge_item('missing')
        await client.close()
        return item

    assert _run(_go()) is None


def test_list_item_children_uses_parent_fiql_query():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured['query'] = request.url.params.get('query')
        return httpx.Response(200, json=_fixture('item_children.json'))

    client = _client(handler)

    async def _go():
        children = await client.list_item_children('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa')
        await client.close()
        return children

    children = _run(_go())
    assert captured['query'] == 'parent.id==aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
    # The client returns every child — visibility filtering is a worker concern.
    assert [c['number'] for c in children] == ['KI 0001', 'KI 0006', 'KI 0007']


def test_list_root_items_filters_parentless_and_forces_parent_field():
    roots_page = {
        'item': [
            {'id': 'r1', 'number': 'KI 1', 'translation': {'content': {'title': 'Root one'}}},
            {'id': 'c1', 'number': 'KI 2', 'parent': {'id': 'r1', 'name': 'Root one'}},
            {'id': 'r2', 'number': 'KI 3', 'translation': {'content': {'title': 'Root two'}}},
        ]
    }
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured['fields'] = request.url.params.get('fields')
        return httpx.Response(200, json=roots_page)

    client = _client(handler)

    async def _go():
        items = await client.list_root_items(fields='number,title,status')
        await client.close()
        return items

    items = _run(_go())
    # Only the parent-less items are roots.
    assert [i['id'] for i in items] == ['r1', 'r2']
    # 'parent' is force-added to the field set so the filter has the data it needs.
    assert 'parent' in captured['fields']


# ---------------------------------------------------------------------
# Retry / status handling
# ---------------------------------------------------------------------


def test_429_retries_after_retry_after_then_succeeds():
    calls = {'count': 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls['count'] += 1
        if calls['count'] == 1:
            return httpx.Response(429, headers={'Retry-After': '1'}, json={'message': 'slow down'})
        return httpx.Response(200, json=_fixture('items_page_1.json'))

    client = _client(handler)

    async def _go():
        data = await client.list_knowledge_items()
        await client.close()
        return data

    data = _run(_go())
    assert calls['count'] == 2
    assert len(data['item']) == 3


def test_5xx_backs_off_and_retries_then_succeeds():
    calls = {'count': 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls['count'] += 1
        if calls['count'] < 3:
            return httpx.Response(503, json={'message': 'unavailable'})
        return httpx.Response(200, json=_fixture('items_empty.json'))

    client = _client(handler)

    async def _go():
        items = await client.iter_all_knowledge_items()
        await client.close()
        return items

    items = _run(_go())
    assert calls['count'] == 3
    assert items == []


def test_exhausted_5xx_raises_transient_error_with_status():
    """A server that always 503s exhausts retries and raises TopdeskTransientError
    carrying the last status code and a meaningful (non-None) message."""
    calls = {'count': 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls['count'] += 1
        return httpx.Response(503, json={'message': 'unavailable'})

    client = _client(handler)

    async def _go():
        try:
            await client.list_knowledge_items()
        finally:
            await client.close()

    with pytest.raises(TopdeskTransientError) as exc:
        _run(_go())

    assert exc.value.status_code == 503
    assert exc.value.args[0]  # non-None, non-empty message
    assert 'None' not in str(exc.value)
    # Default max_retries is 3 — three attempts then give up.
    assert calls['count'] == 3


def test_exhausted_429_raises_transient_error_carrying_429():
    calls = {'count': 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls['count'] += 1
        return httpx.Response(429, headers={'Retry-After': '60'}, json={'message': 'slow down'})

    client = _client(handler)

    async def _go():
        try:
            await client.list_knowledge_items()
        finally:
            await client.close()

    with pytest.raises(TopdeskTransientError) as exc:
        _run(_go())

    assert exc.value.status_code == 429
    assert calls['count'] == 3


def test_final_attempt_does_not_sleep_on_persistent_failure(monkeypatch):
    """On a persistent 5xx, the loop sleeps only between attempts — never after the
    final one. With 3 attempts that means 2 sleeps, fewer than the attempt count."""
    sleeps = {'count': 0}

    async def _counting_sleep(_seconds):
        sleeps['count'] += 1

    monkeypatch.setattr('open_webui.services.topdesk.topdesk_client.asyncio.sleep', _counting_sleep)

    calls = {'count': 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls['count'] += 1
        return httpx.Response(503, json={'message': 'unavailable'})

    client = _client(handler)

    async def _go():
        try:
            await client.list_knowledge_items()
        finally:
            await client.close()

    with pytest.raises(TopdeskTransientError):
        _run(_go())

    assert calls['count'] == 3
    # No sleep on the final attempt → sleeps strictly fewer than attempts.
    assert sleeps['count'] == 2
    assert sleeps['count'] < calls['count']


def test_401_is_terminal_no_retry():
    calls = {'count': 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls['count'] += 1
        return httpx.Response(401, json={'message': 'Unauthorized'})

    client = _client(handler)

    async def _go():
        try:
            await client.list_knowledge_items()
        finally:
            await client.close()

    with pytest.raises(TopdeskAuthError):
        _run(_go())
    assert calls['count'] == 1


# ---------------------------------------------------------------------
# probe
# ---------------------------------------------------------------------


def test_probe_version_happy_path():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured['url'] = str(request.url)
        captured['authorization'] = request.headers.get('authorization')
        return httpx.Response(200, json={'version': '2025.2'})

    client = _client(handler)

    async def _go():
        result = await client.probe()
        await client.close()
        return result

    result = _run(_go())
    assert result['ok'] is True
    assert result['probe'] == 'version'
    assert result['detail'] == {'version': '2025.2'}
    assert captured['url'] == _BASE_URL + '/tas/api/version'
    assert captured['authorization'] is not None


def test_probe_falls_back_to_kb_list_on_version_404():
    seen: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path.endswith('/tas/api/version'):
            return httpx.Response(404, json={'message': 'not found'})
        return httpx.Response(200, json=_fixture('items_empty.json'))

    client = _client(handler)

    async def _go():
        result = await client.probe()
        await client.close()
        return result

    result = _run(_go())
    assert result['ok'] is True
    assert result['probe'] == 'knowledgeItems'
    assert result['detail'] == {'returned': 0}
    assert seen == [
        '/tas/api/version',
        _KB_PATH + '/knowledgeItems',
    ]
