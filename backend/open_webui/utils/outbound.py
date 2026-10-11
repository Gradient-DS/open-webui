"""The admin-configured URL ("trusted origin") is reached without an address check; every other destination -
every redirect hop (even back to the trusted origin) and every URL taken from a server response that is not on
the trusted origin - must pass the address guard on the very connection that is used (pinned), and a redirect
hop is re-checked each time."""

import asyncio
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urljoin, urlsplit

import aiohttp
import httpx
import requests
from multidict import CIMultiDict
from open_webui.constants import ERROR_MESSAGES
from open_webui.retrieval.web.utils import (
    _assert_addresses_allowed,
    _assert_host_allowed,
    get_ssrf_safe_requests_session,
    get_ssrf_safe_session,
)

_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_CREDENTIAL_HEADERS = {'authorization', 'proxy-authorization', 'cookie'}
_BODY_HEADERS = {'content-length', 'content-type', 'transfer-encoding'}


def is_same_origin(url: str, base_url: str) -> bool:
    def origin(value: str) -> tuple[str, str | None, int]:
        parsed = urlsplit(value)
        return parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80)

    return origin(url) == origin(base_url)


def _check_characters(url: str) -> None:
    if any(char in url for char in ('\\', '\t', '\n', '\r')):
        raise ValueError(ERROR_MESSAGES.INVALID_URL)


def _check_url(url: str) -> None:
    _check_characters(url)
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
            raise ValueError
        parsed.port
    except ValueError:
        raise ValueError(ERROR_MESSAGES.INVALID_URL) from None


def _redirect(
    method: str, url: str, location: str, status: int, original_url: str, kwargs: dict[str, Any]
) -> tuple[str, str]:
    _check_characters(location)
    target = urljoin(url, location)
    _check_url(target)
    kwargs.pop('params', None)
    dropped = set()
    if not is_same_origin(target, original_url):
        dropped.update(_CREDENTIAL_HEADERS)
        kwargs.pop('cookies', None)
        kwargs.pop('auth', None)
    if (status == 303 and method != 'HEAD') or (status in {301, 302} and method == 'POST'):
        method = 'GET'
        for key in ('data', 'json', 'files'):
            kwargs.pop(key, None)
        dropped.update(_BODY_HEADERS)
    if dropped and kwargs.get('headers') is not None:
        headers = CIMultiDict(kwargs['headers'])
        for name in list(headers):
            if name.lower() in dropped:
                headers.popall(name, None)
        kwargs['headers'] = headers
    return method, target


@asynccontextmanager
async def guarded_request(
    method: str,
    url: str,
    *,
    trusted_url: str | None = None,
    allow_redirects: bool = True,
    max_redirects: int = 10,
    timeout: aiohttp.ClientTimeout | None = None,
    trust_env: bool = True,
    **request_kwargs: Any,
) -> AsyncIterator[aiohttp.ClientResponse]:
    original_url = url
    method = method.upper()
    if timeout is not None:
        request_kwargs['timeout'] = timeout
    for hop in range(max_redirects + 1):
        _check_url(url)
        trusted = hop == 0 and trusted_url is not None and is_same_origin(url, trusted_url)
        # A fresh session per hop prevents pooled connections and DNS caches skipping a new check.
        session = (
            aiohttp.ClientSession(trust_env=trust_env)
            if trusted
            else get_ssrf_safe_session(trust_env=trust_env, store_cookies=False)
        )
        async with session:
            async with session.request(method, url, allow_redirects=False, **request_kwargs) as response:
                location = response.headers.get('Location')
                if not allow_redirects or response.status not in _REDIRECT_STATUSES or location is None:
                    yield response
                    return
                method, url = _redirect(
                    method, str(response.url), location, response.status, original_url, request_kwargs
                )
    raise ValueError(ERROR_MESSAGES.INVALID_URL)


def guarded_requests_request(
    method: str,
    url: str,
    *,
    trusted_url: str | None = None,
    allow_redirects: bool = True,
    max_redirects: int = 10,
    **kwargs: Any,
) -> requests.Response:
    original_url = url
    method = method.upper()
    trust_env = kwargs.pop('trust_env', True)
    for hop in range(max_redirects + 1):
        _check_url(url)
        trusted = hop == 0 and trusted_url is not None and is_same_origin(url, trusted_url)
        session = requests.Session() if trusted else get_ssrf_safe_requests_session(trust_env, store_cookies=False)
        session.trust_env = trust_env
        with session:
            response = session.request(method, url, allow_redirects=False, **kwargs)
            location = response.headers.get('Location')
            if not allow_redirects or response.status_code not in _REDIRECT_STATUSES or location is None:
                return response
            try:
                method, url = _redirect(method, response.url, location, response.status_code, original_url, kwargs)
            finally:
                response.close()
    raise ValueError(ERROR_MESSAGES.INVALID_URL)


class GuardedAsyncClient(httpx.AsyncClient):
    def __init__(self, *args: Any, trusted_url: str | None = None, **kwargs: Any) -> None:
        self.trusted_url = trusted_url
        super().__init__(*args, **kwargs)

    # httpx 0.28.1 has no public hook between redirect construction and transport dispatch.
    def _build_redirect_request(self, request: httpx.Request, response: httpx.Response) -> httpx.Request:
        _check_characters(response.headers['Location'])
        redirect = super()._build_redirect_request(request, response)
        redirect._outbound_redirect = True
        return redirect

    async def _send_single_request(self, request: httpx.Request) -> httpx.Response:
        _check_url(str(request.url))
        if (
            not getattr(request, '_outbound_redirect', False)
            and self.trusted_url is not None
            and is_same_origin(str(request.url), self.trusted_url)
        ):
            return await super()._send_single_request(request)

        host = request.url.host
        _assert_host_allowed(host)
        try:
            addresses = await asyncio.to_thread(
                socket.getaddrinfo,
                host,
                request.url.port or (443 if request.url.scheme == 'https' else 80),
                0,
                socket.SOCK_STREAM,
            )
        except (OSError, UnicodeError):
            raise ValueError(ERROR_MESSAGES.INVALID_URL) from None
        if not addresses:
            raise ValueError(ERROR_MESSAGES.INVALID_URL)
        _assert_addresses_allowed([info[4][0] for info in addresses])
        pinned = httpx.Request(
            method=request.method,
            url=request.url.copy_with(host=addresses[0][4][0]),
            headers=request.headers,
            stream=request.stream,
            extensions={**request.extensions, 'sni_hostname': host},
        )
        response = await super()._send_single_request(pinned)
        response.request = request
        return response
