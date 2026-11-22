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


def validate(upload: dict, *, size: int, sha256: str, platform_url: str = 'https://platform.invalid') -> None:
    from open_webui.soev.client import SoevApiError

    try:
        url = httpx.URL(upload['url'])
        headers = upload['headers']
        expected = {
            'content-length': str(size),
            'x-amz-checksum-sha256': base64.b64encode(bytes.fromhex(sha256)).decode(),
            'if-none-match': '*',
        }
        if (
            upload['method'] != 'PUT'
            or not (url.scheme == 'https' or (url.scheme == 'http' and urlsplit(platform_url).scheme == 'http'))
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
    platform_url: str,
) -> None:
    from open_webui.soev.client import StoreTransferError, _NoCookies

    validate(upload, size=size, sha256=sha256, platform_url=platform_url)
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
