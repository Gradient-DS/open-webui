"""Keep real URL parsing, redirects and connection guards above fake transports."""

import asyncio
import ipaddress
import socket
from collections import Counter
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest
import requests
from multidict import CIMultiDict
from open_webui.retrieval.web import utils as web

PUBLIC = 'http://public.example/'
INTERNAL = 'http://169.254.169.254/'
HOSTS = (
    '10.0.0.1',
    '172.16.0.1',
    '192.168.1.1',
    '127.0.0.1',
    '[::1]',
    '169.254.169.254',
    '[fd00::1]',
    '0.0.0.0',
    '2130706433',
    '0177.0.0.1',
    '0x7f000001',
    '127.1',
    '[::ffff:127.0.0.1]',
    '[::ffff:169.254.169.254]',
)
BLOCKED_URLS = [f'http://{host}/' for host in HOSTS] + [
    'file:///etc/passwd',
    'gopher://127.0.0.1/',
    'ftp://127.0.0.1/',
    'dict://127.0.0.1/',
    'data:text/plain,hello',
    'HtTp://127.0.0.1/',
    'FiLe:///etc/passwd',
    'http://public.example@127.0.0.1/',
    'http://127.0.0.1#@public.example/',
    'http://127.0.0.1\\@public.example/',
    'http://public.example\\@127.0.0.1/',
]
# These sinks impose no port restrictions; private destinations are denied on every port.


class OfflineDNS:
    def __init__(self):
        self.calls = Counter()
        self.rebind = False

    def __call__(self, host, port, family=0, type=0, proto=0, flags=0):
        host = host.decode() if isinstance(host, bytes) else host
        self.calls[host] += 1
        aliases = dict.fromkeys(('2130706433', '0177.0.0.1', '0x7f000001', '127.1'), '127.0.0.1')
        address = aliases.get(host, host)
        try:
            ipaddress.ip_address(address)
        except ValueError:
            address = '127.0.0.1' if self.rebind and self.calls[host] > 1 else '93.184.216.34'
        family = socket.AF_INET6 if ':' in address else socket.AF_INET
        sockaddr = (address, port or 0, 0, 0) if family == socket.AF_INET6 else (address, port or 0)
        return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', sockaddr)]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    dns = OfflineDNS()
    monkeypatch.setattr(socket, 'getaddrinfo', dns)
    monkeypatch.setattr(web, 'ENABLE_LOCAL_WEB_FETCH', False)
    monkeypatch.setattr(web, 'WEB_FETCH_FILTER_LIST', [])
    monkeypatch.setattr(aiohttp.connector, 'DefaultResolver', aiohttp.resolver.ThreadedResolver)
    for key in ('http_proxy', 'https_proxy', 'all_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('NO_PROXY', '*')

    def forbidden(*args, **kwargs):
        pytest.fail('Unmocked network access in SSRF test')

    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    return dns


@pytest.fixture
def http_boundary(monkeypatch, offline):
    state = SimpleNamespace(sent=[], connected=[], responses={}, redirect=None)

    def response_for(url):
        if url in state.responses:
            return state.responses[url]
        if state.redirect and url == PUBLIC:
            return 302, {'Location': state.redirect}, b''
        return 200, {'Content-Type': 'text/html'}, b'<html>public content</html>'

    async def create_connection(connector, req, traces, timeout):
        addresses = await connector._resolve_host(req.url.raw_host, req.url.port, traces)
        state.connected.extend(item['host'] for item in addresses)
        protocol = MagicMock()
        protocol.is_connected.return_value = True
        protocol.should_close = True
        protocol.closed = asyncio.get_running_loop().create_future()
        protocol.closed.set_result(None)
        return protocol

    async def send(req, connection):
        url = str(req.url)
        state.sent.append(url)
        status, headers, body = response_for(url)
        response = MagicMock()
        response.__aenter__.return_value = response
        response.__aexit__.side_effect = lambda *args: connection.close()
        response.status = status
        response.ok = status < 400
        response.url = req.url
        response.method = req.method
        response.headers = CIMultiDict(headers)
        response._raw_cookie_headers = None
        response.cookies = {}
        response._connection = connection
        response.start = AsyncMock()
        response.read = AsyncMock(return_value=body)
        response.text = AsyncMock(return_value=body.decode())
        import json

        async def json_body(*args, **kwargs):
            return json.loads(body)

        response.json = json_body
        response.wait_for_close = AsyncMock()
        response.release.side_effect = connection.close
        response.close.side_effect = connection.close

        async def chunks(*args):
            yield body

        response.content.iter_chunked = chunks
        return response

    def connect(sock, sockaddr):
        state.connected.append(sockaddr[0])

    def requests_send(adapter, request, **kwargs):
        pool = adapter.get_connection_with_tls_context(request, kwargs.get('verify', True), proxies={})
        connection = pool.ConnectionCls(host=pool.host, port=pool.port, timeout=1)
        sock = connection._new_conn()
        sock.close()
        state.sent.append(request.url)
        status, headers, body = response_for(request.url)
        response = requests.Response()
        response.status_code = status
        response.headers.update(headers)
        response.url = request.url
        response.request = request
        response._content = body
        response._content_consumed = True
        response.raw = MagicMock()
        response.raw.headers.getlist.return_value = []
        return response

    monkeypatch.setattr(aiohttp.TCPConnector, '_create_connection', create_connection)
    monkeypatch.setattr(aiohttp.ClientRequest, 'send', send)
    monkeypatch.setattr(socket.socket, 'connect', connect)
    monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send', requests_send)
    return state


def assert_public_only(boundary):
    assert all(ipaddress.ip_address(ip).is_global for ip in boundary.connected), boundary.connected


@pytest.fixture
def user():
    return SimpleNamespace(id='ssrf-user', role='user', settings={})
