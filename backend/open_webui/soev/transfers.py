"""Send declared originals through short-lived store capabilities."""

import asyncio
import base64
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import aclosing
from contextvars import ContextVar
from http.cookiejar import CookieJar
from urllib.parse import urlsplit

import httpx

_active = ContextVar('soev_transfer', default=False)


class _HideTransfer(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not _active.get()


# httpx logs full request URLs at INFO, httpcore can log them at DEBUG.
_filter = _HideTransfer()
for _name in (
    'httpx',
    'httpcore.connection',
    'httpcore.http11',
    'httpcore.http2',
    'httpcore.proxy',
    'httpcore.socks',
):
    logging.getLogger(_name).addFilter(_filter)


def validate(upload: dict, *, size: int, sha256: str, store_host: str) -> None:
    from open_webui.soev.client import SoevApiError

    try:
        configured = urlsplit(store_host if '://' in store_host else f'https://{store_host}')
        if (
            not configured.hostname
            or configured.scheme not in ('http', 'https')
            or configured.username is not None
            or configured.password is not None
            or configured.path not in ('', '/')
            or configured.query
            or configured.fragment
        ):
            raise ValueError
        port = configured.port if configured.port is not None else (443 if configured.scheme == 'https' else 80)
        if port < 1:
            raise ValueError
        url = httpx.URL(upload['url'])
        headers = upload['headers']
        expected = {
            'content-length': str(size),
            'x-amz-checksum-sha256': base64.b64encode(bytes.fromhex(sha256)).decode(),
            'if-none-match': '*',
        }
        if (
            upload['method'] != 'PUT'
            or url.scheme != configured.scheme
            or url.host != configured.hostname
            or (url.port if url.port is not None else (443 if url.scheme == 'https' else 80)) != port
            or not url.host
            or url.userinfo
            or urlsplit(upload['url']).username is not None
            or url.fragment
            or '#' in upload['url']
            or not isinstance(headers, dict)
            or len(headers) != 3
            or {key.lower(): value for key, value in headers.items()} != expected
        ):
            raise ValueError
    except (KeyError, ValueError, TypeError, AttributeError, httpx.InvalidURL):
        raise SoevApiError(502, 'upload_declaration_invalid', 'Invalid upload declaration') from None


async def put(
    upload: dict,
    chunks: Callable[[], AsyncIterator[bytes]],
    *,
    size: int,
    sha256: str,
    timeout: float,
    store_host: str,
) -> None:
    from open_webui.soev.client import StoreTransferError, _NoCookies

    validate(upload, size=size, sha256=sha256, store_host=store_host)
    token = _active.set(True)
    try:
        async with httpx.AsyncClient(
            follow_redirects=False,
            cookies=CookieJar(policy=_NoCookies()),
            trust_env=False,
        ) as client:
            # Only Host plus the three returned headers belong on the store request.
            client.headers.clear()
            for attempt in range(3):
                try:
                    async with aclosing(chunks()) as source:
                        async with client.stream(
                            upload['method'],
                            upload['url'],
                            headers=upload['headers'],
                            content=source,
                            timeout=timeout,
                        ) as response:
                            status = response.status_code
                except httpx.TransportError:
                    status = 502
                if status in (200, 412):
                    return
                if not 500 <= status <= 599 or attempt == 2:
                    raise StoreTransferError(status, 'upload_rejected', 'Upload rejected') from None
                await asyncio.sleep(0.1 * 2**attempt)
    finally:
        _active.reset(token)
