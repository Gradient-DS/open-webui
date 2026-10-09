"""Exercise the web loader entry point and its actual HTTP connection guards."""

from unittest.mock import AsyncMock, MagicMock
import socket
import ssl

import pytest

from open_webui.retrieval.web import utils as web
from .conftest import BLOCKED_URLS, INTERNAL, PUBLIC, assert_public_only


@pytest.mark.parametrize('url', BLOCKED_URLS)
@pytest.mark.parametrize('engine', ['safe_web', 'playwright', 'firecrawl', 'external', 'tavily', 'microsoft_web_iq'])
def test_loader_rejects_target(engine, url, http_boundary):
    with pytest.raises(ValueError, match='invalid'):
        web.get_web_loader(url, loader_config={'web_loader_engine': engine})
    assert http_boundary.sent == []


def test_search_result_filter_retains_only_public_urls(http_boundary):
    assert web.safe_validate_urls([*BLOCKED_URLS, PUBLIC]) == [PUBLIC]
    loader = web.get_web_loader([*BLOCKED_URLS, PUBLIC], loader_config={'web_loader_engine': 'safe_web'})
    assert loader.load()[0].page_content == 'public content'
    assert http_boundary.sent == [PUBLIC]


@pytest.mark.parametrize('asynchronous', [False, True], ids=['sync', 'async'])
@pytest.mark.parametrize('attack', ['redirect', 'rebind'])
@pytest.mark.asyncio
async def test_native_loader_connect_guard(asynchronous, attack, http_boundary, offline, monkeypatch):
    monkeypatch.setattr(web, 'AIOHTTP_CLIENT_ALLOW_REDIRECTS', True)
    http_boundary.redirect = INTERNAL if attack == 'redirect' else None
    offline.rebind = attack == 'rebind'
    loader = web.get_web_loader(PUBLIC, loader_config={'web_loader_engine': 'safe_web'})
    if asynchronous:
        await loader.aload()
    else:
        loader.load()
    if attack == 'redirect':
        assert http_boundary.sent == [PUBLIC]
    else:
        assert offline.calls['public.example'] >= 2
        assert http_boundary.sent == []
    assert_public_only(http_boundary)


@pytest.mark.parametrize('asynchronous', [False, True], ids=['sync', 'async'])
@pytest.mark.parametrize('attack', ['redirect', 'rebind'])
@pytest.mark.asyncio
async def test_playwright_interception_guards_every_hop(asynchronous, attack, http_boundary, offline, monkeypatch):
    monkeypatch.setattr(web, 'AIOHTTP_CLIENT_ALLOW_REDIRECTS', True)
    http_boundary.redirect = INTERNAL if attack == 'redirect' else None
    offline.rebind = attack == 'rebind'
    loader = web.get_web_loader(PUBLIC, verify_ssl=False, loader_config={'web_loader_engine': 'playwright'})
    route = MagicMock()
    route.request.url = PUBLIC
    route.request.method = 'GET'
    route.request.post_data_buffer = None
    if asynchronous:
        route.request.all_headers = AsyncMock(return_value={})
        route.abort = AsyncMock()
        route.fulfill = AsyncMock()
        async with web.get_ssrf_safe_session(trust_env=False) as session:
            await loader._intercept_navigation(route, session)
    else:
        route.request.all_headers.return_value = {}
        with web.get_ssrf_safe_requests_session(trust_env=False) as session:
            loader._intercept_navigation_sync(route, session)
    route.abort.assert_called_once()
    assert http_boundary.sent == ([PUBLIC] if attack == 'redirect' else [])
    assert_public_only(http_boundary)


def test_local_fetch_opt_in_retains_its_semantics(monkeypatch, http_boundary):
    monkeypatch.setattr(web, 'ENABLE_LOCAL_WEB_FETCH', True)
    loader = web.get_web_loader(INTERNAL, loader_config={'web_loader_engine': 'safe_web'})
    assert loader.load()
    assert http_boundary.sent == [INTERNAL]


@pytest.mark.parametrize('engine', ['tavily', 'microsoft_web_iq', 'playwright'])
@pytest.mark.parametrize('asynchronous', [False, True], ids=['sync', 'async'])
@pytest.mark.asyncio
async def test_ssl_probe_refuses_rebinding(engine, asynchronous, offline, monkeypatch, http_boundary):
    offline.rebind = True
    connected = []
    tls_socket = MagicMock()
    tls_socket.__enter__.return_value = tls_socket

    def connect(address):
        connected.append(socket.getaddrinfo(*address)[0][4][0])
        raise ssl.SSLError('offline TLS handshake')

    tls_socket.connect.side_effect = connect
    monkeypatch.setattr(ssl.SSLContext, 'wrap_socket', lambda *args, **kwargs: tls_socket)
    loader = web.get_web_loader('https://public.example/', loader_config={'web_loader_engine': engine})
    if engine == 'playwright':
        with pytest.raises(ValueError, match='SSL certificate verification failed'):
            if asynchronous:
                await loader._safe_process_url('https://public.example/')
            else:
                loader._safe_process_url_sync('https://public.example/')
    elif asynchronous:
        await loader.aload()
    else:
        loader.load()
    assert offline.calls['public.example'] >= 2
    assert connected == []


@pytest.mark.parametrize(
    'engine,endpoint,body',
    [
        ('firecrawl', 'http://extract.example/v2/scrape', b'{"data":{"markdown":"page"}}'),
        ('external', 'http://extract.example/', b'[{"page_content":"page"}]'),
        ('tavily', 'https://api.tavily.com/extract', b'{"results":[{"raw_content":"page"}]}'),
        ('microsoft_web_iq', 'http://extract.example/browse', b'{"content":"page"}'),
    ],
)
def test_remote_loader_sends_only_validated_urls(engine, endpoint, body, http_boundary):
    http_boundary.responses[endpoint] = (200, {'Content-Type': 'application/json'}, body)
    loader = web.get_web_loader(
        [INTERNAL, PUBLIC],
        verify_ssl=False,
        loader_config={
            'web_loader_engine': engine,
            'firecrawl_api_url': 'http://extract.example',
            'external_web_loader_url': 'http://extract.example/',
            'microsoft_web_iq_api_base_url': 'http://extract.example',
        },
    )
    assert loader.load()[0].page_content == 'page'
    assert http_boundary.sent == [endpoint]
