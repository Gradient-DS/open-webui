"""ConfluenceClient pagination cursor + site-URL normalization.

Two transport-correctness fixes pinned here:

1. The one-page ``list_*`` methods must return the **opaque cursor token** from
   ``_links.next`` (not the whole host-relative path). The interactive picker
   re-feeds the returned value as the bare ``cursor=`` query param, so returning
   the full path produced a malformed request and a 400 on page two. (The
   ``_paginated_get`` sync path follows the full URL instead and is unaffected.)

2. ``site_url`` is normalized to ``scheme://host`` so an admin-pasted value that
   includes the ``/wiki`` context path (or a deep link) does not double up into
   ``.../wiki/wiki/api/v2/...`` and 404.

They run without pytest-asyncio via asyncio.run, matching the sibling tests.
"""

from __future__ import annotations

import asyncio

import httpx

from open_webui.services.confluence.confluence_client import ConfluenceClient


def _client_with_handler(handler, **kwargs) -> ConfluenceClient:
    client = ConfluenceClient(access_token='t', cloud_id='cloud-1', **kwargs)
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client


# ---------------------------------------------------------------------------
# Bug 1: pagination cursor must be the opaque token, not the full next-link
# ---------------------------------------------------------------------------


def test_list_spaces_returns_opaque_cursor_not_full_next_link():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                'results': [{'id': '1'}],
                '_links': {'next': '/wiki/api/v2/spaces?limit=100&cursor=ABC123'},
            },
        )

    client = _client_with_handler(handler)

    async def _run():
        _, next_cursor = await client.list_spaces()
        await client.close()
        return next_cursor

    assert asyncio.run(_run()) == 'ABC123'


def test_list_pages_in_space_returns_opaque_cursor():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                'results': [{'id': 'p1'}],
                '_links': {'next': '/wiki/api/v2/spaces/42/pages?limit=100&cursor=PAGECUR'},
            },
        )

    client = _client_with_handler(handler)

    async def _run():
        _, next_cursor = await client.list_pages_in_space('42')
        await client.close()
        return next_cursor

    assert asyncio.run(_run()) == 'PAGECUR'


def test_list_page_children_returns_opaque_cursor():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                'results': [{'id': 'c1'}],
                '_links': {'next': '/wiki/api/v2/pages/9/children?limit=100&cursor=CHILDCUR'},
            },
        )

    client = _client_with_handler(handler)

    async def _run():
        _, next_cursor = await client.list_page_children('9')
        await client.close()
        return next_cursor

    assert asyncio.run(_run()) == 'CHILDCUR'


def test_pagination_page_two_sends_clean_cursor():
    """Faithful reproduction of the prod 400: page two must carry the opaque
    token, never a URL-encoded nested path."""
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if len(requests) == 1:
            return httpx.Response(
                200,
                json={
                    'results': [{'id': '1'}],
                    '_links': {'next': '/wiki/api/v2/spaces?limit=100&cursor=TOKEN2'},
                },
            )
        return httpx.Response(200, json={'results': [{'id': '2'}], '_links': {}})

    client = _client_with_handler(handler)

    async def _run():
        _, cursor = await client.list_spaces()
        await client.list_spaces(cursor=cursor)
        await client.close()

    asyncio.run(_run())

    assert 'cursor=TOKEN2' in requests[1]
    # A nested path would URL-encode the '?' as %3F — must not happen.
    assert 'spaces%3F' not in requests[1]


def test_returns_none_cursor_when_no_next_link():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={'results': [], '_links': {}})

    client = _client_with_handler(handler)

    async def _run():
        _, next_cursor = await client.list_spaces()
        await client.close()
        return next_cursor

    assert asyncio.run(_run()) is None


# ---------------------------------------------------------------------------
# Bug 2 (defensive): site_url normalized to scheme://host
# ---------------------------------------------------------------------------


def test_basic_mode_strips_wiki_suffix_from_site_url():
    client = ConfluenceClient(
        auth_mode='basic',
        site_url='https://acme.atlassian.net/wiki',
        basic_username='u',
        basic_api_token='t',
    )
    assert client._v2_url('spaces') == 'https://acme.atlassian.net/wiki/api/v2/spaces'


def test_basic_mode_strips_deep_link_path_from_site_url():
    client = ConfluenceClient(
        auth_mode='basic',
        site_url='https://acme.atlassian.net/wiki/spaces/ENG/overview',
        basic_username='u',
        basic_api_token='t',
    )
    assert client._v2_url('spaces') == 'https://acme.atlassian.net/wiki/api/v2/spaces'


def test_basic_mode_adds_https_scheme_when_missing():
    client = ConfluenceClient(
        auth_mode='basic',
        site_url='acme.atlassian.net',
        basic_username='u',
        basic_api_token='t',
    )
    assert client._v2_url('spaces') == 'https://acme.atlassian.net/wiki/api/v2/spaces'


def test_basic_mode_preserves_plain_site_url():
    client = ConfluenceClient(
        auth_mode='basic',
        site_url='https://acme.atlassian.net/',
        basic_username='u',
        basic_api_token='t',
    )
    assert client._v2_url('spaces') == 'https://acme.atlassian.net/wiki/api/v2/spaces'
