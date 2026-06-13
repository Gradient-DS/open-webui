"""TopdeskClient — GraphQL request shape, error envelope, pagination, retry, probe.

All tests run against an in-memory ``httpx.MockTransport`` (zero network) and
load the committed fixtures. ``asyncio.sleep`` is patched to a no-op so retry
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
    TopdeskClient,
    TopdeskAuthError,
    TopdeskGraphQLError,
)

_FIXTURES = Path(__file__).parent / 'fixtures'
_BASE_URL = 'https://tenant.topdesk.net'
_GQL_PATH = '/tas/api/knowledgeBase/graphql'


def _fixture(name: str) -> dict:
    return json.loads((_FIXTURES / name).read_text())


def _client(handler) -> TopdeskClient:
    """Build a TopdeskClient whose transport is the given mock handler."""
    client = TopdeskClient(
        base_url=_BASE_URL,
        username='operator',
        app_password='s3cr3t',
        graphql_path=_GQL_PATH,
        page_size=3,
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


def test_graphql_request_shape_and_auth_header():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured['url'] = str(request.url)
        captured['method'] = request.method
        captured['authorization'] = request.headers.get('authorization')
        captured['content_type'] = request.headers.get('content-type')
        captured['body'] = json.loads(request.content.decode('utf-8'))
        return httpx.Response(200, json=_fixture('items_page_1.json'))

    client = _client(handler)

    async def _go():
        data = await client.graphql('query Q { x }', {'first': 3})
        await client.close()
        return data

    data = _run(_go())

    assert captured['method'] == 'POST'
    assert captured['url'] == _BASE_URL + _GQL_PATH
    expected = 'Basic ' + base64.b64encode(b'operator:s3cr3t').decode('ascii')
    assert captured['authorization'] == expected
    assert captured['content_type'].startswith('application/json')
    assert captured['body'] == {'query': 'query Q { x }', 'variables': {'first': 3}}
    # graphql() returns the `data` value, not the whole envelope.
    assert 'knowledgeItems' in data


def test_graphql_url_property():
    client = TopdeskClient(base_url=_BASE_URL + '/', graphql_path='tas/api/x/graphql')
    assert client.graphql_url == _BASE_URL + '/tas/api/x/graphql'


# ---------------------------------------------------------------------
# 200-with-errors envelope → raises (findings §4.0)
# ---------------------------------------------------------------------


def test_graphql_200_with_errors_raises_with_message():
    def handler(request: httpx.Request) -> httpx.Response:
        # HTTP 200 but a top-level `errors` array — a GraphQL failure.
        return httpx.Response(200, json=_fixture('items_error.json'))

    client = _client(handler)

    async def _go():
        try:
            await client.graphql('query Q { x }', {})
        finally:
            await client.close()

    with pytest.raises(TopdeskGraphQLError) as exc:
        _run(_go())

    assert "Field 'modificationDate' is not defined" in str(exc.value)
    assert exc.value.errors  # the raw errors array is preserved


# ---------------------------------------------------------------------
# list_knowledge_items + pagination loop
# ---------------------------------------------------------------------


def test_list_knowledge_items_single_page_returns_nodes_cursor_hasnext():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_fixture('items_page_1.json'))

    client = _client(handler)

    async def _go():
        result = await client.list_knowledge_items(status='PUBLISHED')
        await client.close()
        return result

    nodes, end_cursor, has_next = _run(_go())
    assert [n['number'] for n in nodes] == ['KI 0001', 'KI 0002', 'KI 0003']
    assert end_cursor == 'Y3Vyc29yOjM='
    assert has_next is True


def test_pagination_walks_page_1_then_page_2_and_terminates():
    pages = [_fixture('items_page_1.json'), _fixture('items_page_2.json')]
    seen_after: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode('utf-8'))
        seen_after.append(body['variables'].get('after'))
        # First request carries after=None, second carries the endCursor of page 1.
        idx = 0 if body['variables'].get('after') is None else 1
        return httpx.Response(200, json=pages[idx])

    client = _client(handler)

    async def _go():
        items = await client.list_all_knowledge_items(status='PUBLISHED')
        await client.close()
        return items

    items = _run(_go())
    numbers = [n['number'] for n in items]
    assert numbers == ['KI 0001', 'KI 0002', 'KI 0003', 'KI 0004', 'KI 0005']
    # page 1 fetched with after=None, page 2 with page-1 endCursor; then stops.
    assert seen_after == [None, 'Y3Vyc29yOjM=']


def test_pagination_empty_page_yields_no_items():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_fixture('items_empty.json'))

    client = _client(handler)

    async def _go():
        items = await client.list_all_knowledge_items()
        await client.close()
        return items

    assert _run(_go()) == []


def test_pagination_safety_cap_bounds_requests():
    """A server that always reports hasNextPage with an advancing cursor must
    still terminate at the max_pages cap (no infinite loop)."""
    calls = {'count': 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls['count'] += 1
        # Always claims more pages, with a fresh advancing cursor each time.
        return httpx.Response(
            200,
            json={
                'data': {
                    'knowledgeItems': {
                        'edges': [
                            {
                                'cursor': f'c{calls["count"]}',
                                'node': {'id': str(calls['count']), 'number': f'KI {calls["count"]}'},
                            }
                        ],
                        'pageInfo': {'hasNextPage': True, 'endCursor': f'c{calls["count"]}'},
                    }
                }
            },
        )

    client = _client(handler)

    async def _go():
        items = await client.list_all_knowledge_items(max_pages=5)
        await client.close()
        return items

    items = _run(_go())
    assert calls['count'] == 5
    assert len(items) == 5


def test_pagination_stops_when_cursor_does_not_advance():
    """A buggy server that repeats the same endCursor must not loop forever."""
    calls = {'count': 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls['count'] += 1
        return httpx.Response(
            200,
            json={
                'data': {
                    'knowledgeItems': {
                        'edges': [{'cursor': 'stuck', 'node': {'id': '1', 'number': 'KI 1'}}],
                        'pageInfo': {'hasNextPage': True, 'endCursor': 'stuck'},
                    }
                }
            },
        )

    client = _client(handler)

    async def _go():
        items = await client.list_all_knowledge_items(max_pages=50)
        await client.close()
        return items

    items = _run(_go())
    # First page fetched (after=None → endCursor 'stuck'); second fetch (after='stuck')
    # returns the same 'stuck' cursor → loop detects no advance and stops.
    assert calls['count'] == 2
    assert len(items) == 2


# ---------------------------------------------------------------------
# get_knowledge_item + tree traversal
# ---------------------------------------------------------------------


def test_get_knowledge_item_returns_node_with_content():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured['body'] = json.loads(request.content.decode('utf-8'))
        return httpx.Response(200, json=_fixture('item_with_content.json'))

    client = _client(handler)

    async def _go():
        item = await client.get_knowledge_item('11111111-1111-4111-8111-111111111111', include_content=True)
        await client.close()
        return item

    item = _run(_go())
    assert item['number'] == 'KI 0001'
    assert item['content'].startswith('<h1>')
    assert item['keywords'] == ['password', 'reset', 'login', 'account']
    assert item['parent']['number'] == 'KI 0100'
    assert [t['language'] for t in item['availableTranslations']] == ['en', 'nl']
    assert item['attachments'] == []
    assert captured['body']['variables'] == {'id': '11111111-1111-4111-8111-111111111111'}


def test_get_knowledge_item_none_when_null():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={'data': {'knowledgeItem': None}})

    client = _client(handler)

    async def _go():
        item = await client.get_knowledge_item('missing')
        await client.close()
        return item

    assert _run(_go()) is None


def test_list_item_children_returns_child_nodes():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_fixture('item_children.json'))

    client = _client(handler)

    async def _go():
        children = await client.list_item_children('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa')
        await client.close()
        return children

    children = _run(_go())
    assert [c['number'] for c in children] == ['KI 0001', 'KI 0006', 'KI 0007']


def test_list_root_items_is_full_listing():
    pages = [_fixture('items_page_1.json'), _fixture('items_page_2.json')]

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode('utf-8'))
        idx = 0 if body['variables'].get('after') is None else 1
        return httpx.Response(200, json=pages[idx])

    client = _client(handler)

    async def _go():
        items = await client.list_root_items(status='PUBLISHED')
        await client.close()
        return items

    items = _run(_go())
    assert len(items) == 5


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
        nodes, _cursor, _has_next = await client.list_knowledge_items()
        await client.close()
        return nodes

    nodes = _run(_go())
    assert calls['count'] == 2
    assert len(nodes) == 3


def test_5xx_backs_off_and_retries_then_succeeds():
    calls = {'count': 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls['count'] += 1
        if calls['count'] < 3:
            return httpx.Response(503, json={'message': 'unavailable'})
        return httpx.Response(200, json=_fixture('items_empty.json'))

    client = _client(handler)

    async def _go():
        items = await client.list_all_knowledge_items()
        await client.close()
        return items

    items = _run(_go())
    assert calls['count'] == 3
    assert items == []


def test_401_is_terminal_no_retry():
    calls = {'count': 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls['count'] += 1
        return httpx.Response(401, json={'message': 'Unauthorized'})

    client = _client(handler)

    async def _go():
        try:
            await client.graphql('query Q { x }', {})
        finally:
            await client.close()

    with pytest.raises(TopdeskAuthError):
        _run(_go())
    assert calls['count'] == 1


# ---------------------------------------------------------------------
# probe
# ---------------------------------------------------------------------


def test_probe_operators_current_happy_path():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured['url'] = str(request.url)
        captured['authorization'] = request.headers.get('authorization')
        return httpx.Response(200, json={'id': 'op-1', 'loginName': 'operator'})

    client = _client(handler)

    async def _go():
        result = await client.probe()
        await client.close()
        return result

    result = _run(_go())
    assert result['ok'] is True
    assert result['probe'] == 'operators/current'
    assert result['detail'] == {'id': 'op-1', 'loginName': 'operator'}
    assert captured['url'] == _BASE_URL + '/tas/api/operators/current'
    assert captured['authorization'] is not None


def test_probe_falls_back_to_version_on_404():
    seen: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path.endswith('/operators/current'):
            return httpx.Response(404, json={'message': 'not found'})
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
    assert seen == [
        _BASE_URL + '/tas/api/operators/current',
        _BASE_URL + '/tas/api/version',
    ]
