"""``_links.next`` may only be followed while it stays on the authenticated host.

``_request_with_retry`` attaches the ``Authorization`` header to whatever URL it
is handed, and ``_paginated_get`` follows an absolute ``_links.next`` verbatim.
A malicious or compromised Confluence could therefore point ``next`` at a host it
controls and collect the sync worker's bearer token / Basic credential. The client
now pins the absolute branch to the host it authenticated against and stops
paginating instead.

Same asyncio.run + MockTransport style as the sibling pagination tests.
"""

from __future__ import annotations

import asyncio

import httpx

from open_webui.services.confluence.confluence_client import ConfluenceClient


def _client_with_handler(handler, **kwargs) -> ConfluenceClient:
    client = ConfluenceClient(access_token='t', cloud_id='cloud-1', **kwargs)
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client


def _paginate(client) -> tuple[list, list]:
    """Run _paginated_get('spaces'), returning (results, hosts actually contacted)."""
    return asyncio.run(client._paginated_get('spaces'))


# ---------------------------------------------------------------------------
# The attack: absolute `next` pointing somewhere else
# ---------------------------------------------------------------------------


def test_absolute_next_on_a_foreign_host_is_not_followed():
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        if request.url.host == 'api.atlassian.com':
            return httpx.Response(
                200,
                json={
                    'results': [{'id': '1'}],
                    '_links': {'next': 'https://attacker.example/steal?cursor=ABC'},
                },
            )
        return httpx.Response(200, json={'results': [{'id': 'leaked'}], '_links': {}})

    client = _client_with_handler(handler)
    results = asyncio.run(client._paginated_get('spaces'))

    assert 'attacker.example' not in hosts, 'credentialed request sent to a foreign host'
    assert hosts == ['api.atlassian.com']
    # Page one is still returned; pagination just stops.
    assert results == [{'id': '1'}]


def test_foreign_host_is_rejected_in_basic_mode_too():
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        if request.url.host == 'acme.atlassian.net':
            return httpx.Response(
                200,
                json={
                    'results': [{'id': '1'}],
                    '_links': {'next': 'https://attacker.example/steal'},
                },
            )
        return httpx.Response(200, json={'results': [], '_links': {}})

    client = _client_with_handler(
        handler,
        auth_mode='basic',
        site_url='https://acme.atlassian.net',
        basic_username='u',
        basic_api_token='t',
    )
    results = asyncio.run(client._paginated_get('spaces'))

    assert 'attacker.example' not in hosts
    assert results == [{'id': '1'}]


def test_scheme_relative_next_is_rejected():
    """`//attacker.example/x` parses to a foreign netloc and must not be followed."""
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        if request.url.host == 'api.atlassian.com':
            return httpx.Response(
                200,
                json={'results': [{'id': '1'}], '_links': {'next': '//attacker.example/x'}},
            )
        return httpx.Response(200, json={'results': [], '_links': {}})

    client = _client_with_handler(handler)
    asyncio.run(client._paginated_get('spaces'))

    assert 'attacker.example' not in hosts


# ---------------------------------------------------------------------------
# The legitimate paths must keep working
# ---------------------------------------------------------------------------


def test_absolute_next_on_the_same_host_is_still_followed():
    hosts: list[str] = []
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        seen.append(str(request.url))
        if 'cursor=PAGE2' in str(request.url):
            return httpx.Response(200, json={'results': [{'id': '2'}], '_links': {}})
        return httpx.Response(
            200,
            json={
                'results': [{'id': '1'}],
                '_links': {'next': 'https://api.atlassian.com/ex/confluence/cloud-1/wiki/api/v2/spaces?cursor=PAGE2'},
            },
        )

    client = _client_with_handler(handler)
    results = asyncio.run(client._paginated_get('spaces'))

    assert results == [{'id': '1'}, {'id': '2'}]
    assert hosts == ['api.atlassian.com', 'api.atlassian.com']


def test_host_relative_next_is_still_followed():
    """The common v2 shape — `/wiki/api/v2/...` — is unaffected by the host pin."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if 'cursor=PAGE2' in str(request.url):
            return httpx.Response(200, json={'results': [{'id': '2'}], '_links': {}})
        return httpx.Response(
            200,
            json={
                'results': [{'id': '1'}],
                '_links': {'next': '/wiki/api/v2/spaces?limit=100&cursor=PAGE2'},
            },
        )

    client = _client_with_handler(handler)
    results = asyncio.run(client._paginated_get('spaces'))

    assert results == [{'id': '1'}, {'id': '2'}]
    assert all('attacker' not in u for u in seen)


# ---------------------------------------------------------------------------
# Follow-up from the security review: the pin matched host but not scheme, so a
# downgraded http:// URL on the right host still carried the Authorization
# header onto a cleartext connection.
# ---------------------------------------------------------------------------


def test_scheme_downgrade_on_the_right_host_is_rejected():
    schemes: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        schemes.append(request.url.scheme)
        if request.url.scheme == 'https':
            return httpx.Response(
                200,
                json={
                    'results': [{'id': '1'}],
                    '_links': {'next': 'http://api.atlassian.com/ex/confluence/cloud-1/wiki/api/v2/spaces?cursor=P2'},
                },
            )
        return httpx.Response(200, json={'results': [{'id': 'leaked'}], '_links': {}})

    client = _client_with_handler(handler)
    results = asyncio.run(client._paginated_get('spaces'))

    assert 'http' not in [s for s in schemes if s != 'https'], 'credential sent over cleartext'
    assert schemes == ['https']
    assert results == [{'id': '1'}]


def test_scheme_downgrade_is_rejected_in_basic_mode_too():
    schemes: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        schemes.append(request.url.scheme)
        if request.url.scheme == 'https':
            return httpx.Response(
                200,
                json={
                    'results': [{'id': '1'}],
                    '_links': {'next': 'http://acme.atlassian.net/wiki/api/v2/spaces?cursor=P2'},
                },
            )
        return httpx.Response(200, json={'results': [], '_links': {}})

    client = _client_with_handler(
        handler,
        auth_mode='basic',
        site_url='https://acme.atlassian.net',
        basic_username='u',
        basic_api_token='t',
    )
    results = asyncio.run(client._paginated_get('spaces'))

    assert schemes == ['https']
    assert results == [{'id': '1'}]


def test_matching_scheme_and_host_still_paginates():
    """The pin must not break the legitimate absolute-next path."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if 'cursor=P2' in str(request.url):
            return httpx.Response(200, json={'results': [{'id': '2'}], '_links': {}})
        return httpx.Response(
            200,
            json={
                'results': [{'id': '1'}],
                '_links': {'next': 'https://api.atlassian.com/ex/confluence/cloud-1/wiki/api/v2/spaces?cursor=P2'},
            },
        )

    client = _client_with_handler(handler)
    assert asyncio.run(client._paginated_get('spaces')) == [{'id': '1'}, {'id': '2'}]
    assert len(seen) == 2
