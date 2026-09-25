"""HTTP boundary for soev-api, with fresh assertions and caller-owned mutation keys.

No retries: subject assertions are single-use and mutations belong to the caller.
"""

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack, aclosing
from dataclasses import dataclass
from http.cookiejar import CookieJar, DefaultCookiePolicy
from urllib.parse import quote

import httpx

from open_webui.soev.request_cache import invalidate, memoized

log = logging.getLogger(__name__)
_http_client: httpx.AsyncClient | None = None


class _NoCookies(DefaultCookiePolicy):
    def set_ok(self, cookie, request):
        return False


def _shared_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        # Credentials remain request-local, including on presigned downloads.
        _http_client = httpx.AsyncClient(
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            follow_redirects=False,
            cookies=CookieJar(policy=_NoCookies()),
        )
    return _http_client


async def close_client() -> None:
    global _http_client
    client, _http_client = _http_client, None
    if client is not None:
        await client.aclose()


@dataclass
class ChatEvent:
    event: str
    data: dict
    position: int | None = None


async def _chat_frames(response: httpx.Response) -> AsyncIterator[ChatEvent]:
    event, data, position = 'message', [], None
    async for line in response.aiter_lines():
        if not line:
            if data:
                payload = json.loads('\n'.join(data))
                if not isinstance(payload, dict):
                    raise ValueError('Invalid chat event')
                yield ChatEvent(event, payload, position)
            event, data, position = 'message', [], None
            continue
        field, _, value = line.partition(':')
        value = value.removeprefix(' ')
        if field == 'event':
            event = value
        elif field == 'data':
            data.append(value)
        elif field == 'id':
            position = int(value)


async def _chat_activity(response: httpx.Response, *, following: bool) -> AsyncIterator[ChatEvent | None]:
    frames = _chat_frames(response)
    pending = asyncio.create_task(anext(frames))
    try:
        while True:
            ready, _ = await asyncio.wait({pending}, timeout=5.0 if following else None)
            if not ready:
                yield None
                continue
            try:
                frame = pending.result()
            except StopAsyncIteration:
                yield None
                return
            yield frame
            pending = asyncio.create_task(anext(frames))
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        await frames.aclose()


def _chat_checkpoint(frame: ChatEvent | None) -> bool:
    return frame is None or (
        frame.data.get('stream') == 'root' and frame.event in {'model_output', 'request', 'failure', 'cancelled'}
    )


def _chat_thread_id(response: httpx.Response) -> str:
    thread_id = response.headers.get('X-Soev-Thread-Id')
    if not thread_id:
        raise SoevApiError(502, 'service_unavailable', 'Missing chat thread id')
    return thread_id


class SoevApiError(Exception):
    """A soev-api problem or a sanitized upstream failure."""

    def __init__(self, status: int, code: str, detail: str, constraint: str | None = None) -> None:
        super().__init__(detail)
        self.status = status
        self.code = code
        self.detail = detail
        self.constraint = constraint


def _response_error(response: httpx.Response) -> SoevApiError:
    status = response.status_code
    fallback = SoevApiError(status, 'upstream_error', f'soev-api returned HTTP {status}')
    if response.headers.get('Content-Type', '').split(';', 1)[0].strip().lower() != 'application/problem+json':
        return fallback
    try:
        problem = response.json()
    except ValueError:
        return fallback
    if not isinstance(problem, dict):
        return fallback
    code, detail, constraint = problem.get('code'), problem.get('detail'), problem.get('constraint')
    if not isinstance(code, str) or not isinstance(detail, str):
        return fallback
    if constraint is not None and not isinstance(constraint, str):
        return fallback
    return SoevApiError(status, code, fallback.detail if status >= 500 else detail, constraint)


class SoevClient:
    """Send tenant API requests without persisting credentials or logging payloads."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        subject_minter: Callable[[str], str] | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip('/')
        self._api_key = api_key
        self._subject_minter = subject_minter
        self._timeout = timeout

    async def chat_post(self, path: str, body: dict | None, *, as_user: str) -> dict:
        """Send a thread operation once; the chat contract has no mutation replay key."""
        if not as_user:
            raise ValueError('Chat requires an acting user')
        response = await self._request('POST', path, body=body, as_user=as_user)
        result = self._json_object(response)
        if path.endswith('/fork'):
            result['thread_id'] = _chat_thread_id(response)
        return result

    async def chat_stream(
        self,
        path: str,
        body: dict | None,
        *,
        as_user: str,
        thread_id: str | None = None,
        after: int = 0,
    ) -> AsyncIterator[ChatEvent]:
        """Reconnect capped streams through events, never by replaying the POST."""
        if not path.startswith('/') or path.startswith('//'):
            raise ValueError('An API path must start with a single slash')
        if not as_user or self._subject_minter is None:
            raise ValueError('Chat requires an acting user and subject minter')
        method = 'POST'
        try:
            while True:
                headers = {
                    'Authorization': f'Bearer {self._api_key}',
                    'X-Soev-Subject': self._subject_minter(as_user),
                    'Accept': 'text/event-stream',
                }
                if method == 'GET':
                    headers['Last-Event-ID'] = str(after)
                timeout = httpx.Timeout(self._timeout, read=660.0)
                async with _shared_client().stream(
                    method, f'{self._base_url}{path}', headers=headers, json=body, timeout=timeout
                ) as response:
                    if not response.is_success:
                        await response.aread()
                        raise _response_error(response)
                    thread_id = thread_id or _chat_thread_id(response)
                    yield ChatEvent('connection', {'thread_id': thread_id})
                    frames = self._follow_chat(response, thread_id, as_user, after, method == 'GET')
                    async with aclosing(frames):
                        async for frame in frames:
                            if frame.position is not None:
                                after = max(after, frame.position)
                            yield frame
                            if frame.event in {'status', 'error'}:
                                return
                method, body = 'GET', None
                path = f'/v1/chat/threads/{quote(thread_id, safe="")}/events'
        except (httpx.TransportError, ValueError):
            raise SoevApiError(502, 'service_unavailable', 'Chat stream disconnected') from None

    async def _follow_chat(
        self, response: httpx.Response, thread_id: str, as_user: str, after: int, following: bool
    ) -> AsyncIterator[ChatEvent]:
        async with aclosing(_chat_activity(response, following=following)) as frames:
            async for frame in frames:
                if frame is not None:
                    if frame.position is not None:
                        after = max(after, frame.position)
                    yield frame
                if following and _chat_checkpoint(frame):
                    # Events tails never send status; the idle check also covers lease release after the last event.
                    thread = await self.get(f'/v1/chat/threads/{quote(thread_id, safe="")}', as_user=as_user)
                    status = thread['status']
                    if status['state'] != 'running' and status['position'] <= after:
                        yield ChatEvent('status', status)
                        return

    async def get(self, path: str, *, as_user: str | None = None, params: dict | None = None) -> dict:
        key = (self._base_url, self._api_key, as_user, path, tuple(sorted((params or {}).items())))
        response = await memoized(key, lambda: self._request('GET', path, as_user=as_user, params=params))
        return self._json_object(response)

    async def get_text(self, path: str, *, as_user: str | None = None) -> str:
        """Read API text with the same authority and error handling as JSON reads."""
        response = await self._request('GET', path, as_user=as_user)
        return response.text

    async def stream(self, path: str, *, as_user: str) -> AsyncIterator[bytes]:
        """Stream original bytes, following one presigned redirect without API credentials."""
        if not path.startswith('/') or path.startswith('//'):
            raise ValueError('An API path must start with a single slash')
        if self._subject_minter is None or not as_user:
            raise ValueError('Acting as a user requires a subject minter and user')
        headers = {'Authorization': f'Bearer {self._api_key}', 'X-Soev-Subject': self._subject_minter(as_user)}
        try:
            async with AsyncExitStack() as stack:
                client = _shared_client()
                response = await stack.enter_async_context(
                    client.stream('GET', f'{self._base_url}{path}', headers=headers, timeout=self._timeout)
                )
                if response.status_code == 303:
                    location = response.headers.get('Location', '')
                    if not location.startswith(('https://', 'http://')):
                        raise SoevApiError(502, 'upstream_error', 'Invalid original download URL')
                    response = await stack.enter_async_context(client.stream('GET', location, timeout=self._timeout))
                    if not response.is_success:
                        raise SoevApiError(502, 'upstream_error', 'Original download failed')
                elif not response.is_success:
                    await response.aread()
                    raise _response_error(response)
                async for chunk in response.aiter_bytes():
                    yield chunk
        except httpx.TransportError as error:
            status = 504 if isinstance(error, httpx.TimeoutException) else 502
            raise SoevApiError(status, 'upstream_error', 'Original download failed') from None

    async def put_bytes(self, url: str, *, headers: dict[str, str], body: bytes) -> None:
        """Upload bytes using the presigned URL as the sole credential."""
        try:
            response = await _shared_client().put(url, headers=headers, content=body, timeout=self._timeout)
        except httpx.TransportError as error:
            status = 504 if isinstance(error, httpx.TimeoutException) else 502
            raise SoevApiError(status, 'upload_failed', 'File upload failed') from None
        if not response.is_success:
            raise SoevApiError(502, 'upload_failed', f'File upload returned HTTP {response.status_code}')

    async def pages(self, path: str, *, as_user: str | None = None, params: dict | None = None) -> AsyncIterator[dict]:
        """Yield each page's data items, minting a fresh assertion for every cursor request."""
        query = dict(params or {})
        while True:
            page = await self.get(path, as_user=as_user, params=query)
            for item in page['data']:
                yield item
            cursor = page.get('next_cursor')
            if not cursor:
                return
            query['cursor'] = cursor

    async def send(
        self, method: str, path: str, body: dict | None, *, as_user: str | None = None, idempotency_key: str
    ) -> dict | None:
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            raise ValueError('A mutation requires a nonempty idempotency key')
        method = method.upper()
        if method not in {'POST', 'PUT', 'PATCH', 'DELETE'}:
            raise ValueError('send requires a mutation method')
        try:
            response = await self._request(method, path, body=body, as_user=as_user, idempotency_key=idempotency_key)
        finally:
            invalidate()
        if response.status_code == 204:
            return None
        return self._json_object(response)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict | None = None,
        as_user: str | None = None,
        params: dict | None = None,
        idempotency_key: str | None = None,
    ) -> httpx.Response:
        if not path.startswith('/') or path.startswith('//'):
            raise ValueError('An API path must start with a single slash')
        headers = {'Authorization': f'Bearer {self._api_key}'}
        if as_user is not None:
            if self._subject_minter is None:
                raise ValueError('Acting as a user requires a subject minter')
            headers['X-Soev-Subject'] = self._subject_minter(as_user)
        if idempotency_key is not None:
            headers['Idempotency-Key'] = idempotency_key
        started = time.perf_counter()
        status = None
        try:
            response = await _shared_client().request(
                method, f'{self._base_url}{path}', headers=headers, params=params, json=body, timeout=self._timeout
            )
            status = response.status_code
        except httpx.TransportError as error:
            status = 504 if isinstance(error, httpx.TimeoutException) else 502
            log.warning('soev-api transport failure', extra={'status': status, 'request_id': None})
            raise SoevApiError(status, 'upstream_error', 'soev-api request failed') from None
        finally:
            # [Gradient] The deployed formatter renders the message only, so the timing rides in it.
            log.debug(
                'soev-api request %s %s %s %.1fms',
                method,
                path.split('?', 1)[0],
                status,
                (time.perf_counter() - started) * 1000,
            )
        log.log(
            logging.INFO if response.is_success else logging.WARNING,
            'soev-api response',
            extra={'status': response.status_code, 'request_id': response.headers.get('X-Request-ID')},
        )
        if not response.is_success:
            raise _response_error(response)
        return response

    @staticmethod
    def _json_object(response: httpx.Response) -> dict:
        try:
            result = response.json()
        except ValueError:
            result = None
        if not isinstance(result, dict):
            raise SoevApiError(502, 'upstream_error', 'soev-api returned an invalid JSON object')
        return result
