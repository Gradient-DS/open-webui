"""HTTP boundary for soev-api, with request-local login tokens and caller-owned mutation keys."""

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, aclosing, asynccontextmanager
from dataclasses import dataclass
from http.cookiejar import CookieJar, DefaultCookiePolicy
from urllib.parse import quote

import httpx

from open_webui.soev.acting import requester_ref, user_token
from open_webui.soev.request_cache import invalidate, memoized

log = logging.getLogger(__name__)
_http_client: tuple[asyncio.AbstractEventLoop, httpx.AsyncClient] | None = None


class _NoCookies(DefaultCookiePolicy):
    def set_ok(self, cookie, request):
        return False


def _shared_client() -> httpx.AsyncClient:
    """The keep-alive client of the running loop; a client never crosses event loops."""
    global _http_client
    loop = asyncio.get_running_loop()
    if _http_client is None or _http_client[0] is not loop:
        # Credentials remain request-local, including on presigned downloads.
        # No connection cap: long chat streams must not queue other soev-api calls.
        client = httpx.AsyncClient(
            limits=httpx.Limits(max_connections=None, max_keepalive_connections=20),
            follow_redirects=False,
            cookies=CookieJar(policy=_NoCookies()),
        )
        _http_client = (loop, client)
    return _http_client[1]


async def close_client() -> None:
    global _http_client
    shared, _http_client = _http_client, None
    if shared is not None and shared[0] is asyncio.get_running_loop():
        await shared[1].aclose()


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

    def __init__(
        self,
        status: int,
        code: str,
        detail: str,
        constraint: str | None = None,
        *,
        provider: str | None = None,
        retry_after: str | None = None,
    ) -> None:
        super().__init__(detail)
        self.status = status
        self.code = code
        self.detail = detail
        self.constraint = constraint
        self.provider = provider
        self.retry_after = retry_after
        # [Gradient] The failing call, without query, body or credentials; set where _request raises.
        self.method: str | None = None
        self.path: str | None = None


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
    provider = problem.get('provider')
    return SoevApiError(
        status,
        code,
        fallback.detail if status >= 500 else detail,
        constraint,
        provider=provider if isinstance(provider, str) else None,
        retry_after=response.headers.get('Retry-After'),
    )


class SoevClient:
    """Send tenant API requests without persisting credentials or logging payloads."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = 30.0,
        service: bool = False,
    ) -> None:
        self._base_url = base_url.rstrip('/')
        self._api_key = api_key
        self._timeout = timeout
        self.is_service = service

    def as_service(self) -> 'SoevClient':
        """Use only the product credential, regardless of inherited request context."""
        return SoevClient(self._base_url, self._api_key, timeout=self._timeout, service=True)

    async def _headers(self, as_user: str | None = None, *, user_access_token: str | None = None) -> dict[str, str]:
        headers = {'Authorization': f'Bearer {self._api_key}'}
        if self.is_service:
            return headers
        ref = as_user if as_user is not None else requester_ref()
        access_token = user_access_token if user_access_token is not None else await user_token(ref)
        if access_token:
            headers['X-Soev-User-Token'] = access_token
        elif ref is not None or user_access_token is not None:
            raise SoevApiError(401, 'sign_in', 'Platform login required', 'identity:platform_login')
        return headers

    async def me(self, access_token: str) -> dict:
        """Resolve only the supplied login token, without a legacy subject assertion."""
        if self.is_service or not access_token:
            raise ValueError('Person discovery requires a user token')
        return self._json_object(await self._request('GET', '/v1/me', user_access_token=access_token))

    async def chat_post(self, path: str, body: dict | None, *, as_user: str) -> dict:
        """Send a thread operation once; the chat contract has no mutation replay key."""
        if not as_user:
            raise ValueError('Chat requires an acting user')
        response = await self._request('POST', path, body=body, as_user=as_user)
        result = self._json_object(response)
        if path.endswith('/fork'):
            result['thread_id'] = _chat_thread_id(response)
        return result

    async def post_bytes(self, path: str, body: bytes, *, as_user: str, params: dict | None = None) -> dict:
        if not as_user:
            raise ValueError('Uploading bytes requires an acting user')
        response = await self._request('POST', path, content=body, as_user=as_user, params=params)
        return self._json_object(response)

    async def complete_task(self, body: dict, *, as_user: str) -> dict:
        """Run one task completion on the client's task model; tasks carry no replay key."""
        if not as_user:
            raise ValueError('A task completion requires an acting user')
        response = await self._request('POST', '/v1/completions/task', body=body, as_user=as_user)
        return self._json_object(response)

    async def embed(self, texts: list[str], *, user_access_token: str) -> dict:
        """Embed as the person whose login token this is, without a subject assertion."""
        if self.is_service or not user_access_token:
            raise ValueError('Embedding requires the user token')
        response = await self._request(
            'POST', '/v1/embeddings', body={'input': texts}, user_access_token=user_access_token
        )
        return self._json_object(response)

    async def transcribe(
        self, audio: bytes, *, filename: str, content_type: str, language: str | None, user_access_token: str
    ) -> dict:
        """Transcribe as the person whose login token this is, without a subject assertion."""
        if self.is_service or not user_access_token:
            raise ValueError('Transcription requires the user token')
        data = {'response_format': 'json'}
        if language:
            data['language'] = language
        response = await self._request(
            'POST',
            '/v1/audio/transcriptions',
            files={'file': (filename, audio, content_type)},
            data=data,
            user_access_token=user_access_token,
        )
        return self._json_object(response)

    @asynccontextmanager
    async def speech(self, body: dict, *, user_access_token: str) -> AsyncIterator[httpx.Response]:
        if self.is_service or not user_access_token:
            raise ValueError('Speech requires the user token')
        headers = await self._headers(user_access_token=user_access_token)
        try:
            async with _shared_client().stream(
                'POST',
                f'{self._base_url}/v1/audio/speech',
                headers=headers,
                json=body,
                timeout=self._timeout,
            ) as response:
                if not response.is_success:
                    await response.aread()
                    raise _response_error(response)
                yield response
        except httpx.TransportError as error:
            status = 504 if isinstance(error, httpx.TimeoutException) else 502
            raise SoevApiError(status, 'upstream_error', 'Speech stream failed') from None

    async def stream_task(self, body: dict, *, as_user: str) -> AsyncIterator[bytes]:
        """Pass a streamed task completion's SSE bytes through unchanged."""
        if not as_user:
            raise ValueError('A task completion requires an acting user')
        headers = await self._headers(as_user)
        headers['Accept'] = 'text/event-stream'
        try:
            async with _shared_client().stream(
                'POST',
                f'{self._base_url}/v1/completions/task',
                headers=headers,
                json=body,
                timeout=httpx.Timeout(self._timeout, read=660.0),
            ) as response:
                if not response.is_success:
                    await response.aread()
                    raise _response_error(response)
                async for chunk in response.aiter_bytes():
                    yield chunk
        except httpx.TransportError as error:
            status = 504 if isinstance(error, httpx.TimeoutException) else 502
            raise SoevApiError(status, 'upstream_error', 'Task completion stream failed') from None

    async def chat_delete(self, path: str, *, as_user: str) -> None:
        """Delete a thread once; the chat contract has no mutation replay key."""
        if not as_user:
            raise ValueError('Chat requires an acting user')
        from open_webui.soev.policy_jobs import v2_enabled

        response = await self._request('DELETE', path, as_user=as_user)
        if v2_enabled() and response.status_code != 204:
            raise SoevApiError(502, 'upstream_error', 'Thread deletion did not complete')

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
        if not as_user:
            raise ValueError('Chat requires an acting user')
        method = 'POST'
        try:
            while True:
                headers = await self._headers(as_user)
                headers['Accept'] = 'text/event-stream'
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
        await self._require_thread_request(path, as_user)
        headers = await self._headers(as_user)
        key = (
            self._base_url,
            self._api_key,
            type(self),
            self.is_service,
            tuple(sorted(headers.items())),
            path,
            tuple(sorted((params or {}).items())),
        )
        response = await memoized(
            key, lambda: self._request('GET', path, as_user=as_user, params=params, admission_headers=headers)
        )
        return self._json_object(response)

    async def get_retention_policy(self, *, as_user: str) -> dict | None:
        response = await self._request('GET', '/v1/retention-policy', as_user=as_user)
        if response.json() is None:
            return None
        return self._json_object(response)

    async def get_text(self, path: str, *, as_user: str | None = None) -> str:
        """Read API text with the same authority and error handling as JSON reads."""
        response = await self._request('GET', path, as_user=as_user)
        return response.text

    async def stream(self, path: str, *, as_user: str) -> AsyncIterator[bytes]:
        """Stream original bytes, following one presigned redirect without API credentials."""
        if not path.startswith('/') or path.startswith('//'):
            raise ValueError('An API path must start with a single slash')
        if not as_user:
            raise ValueError('Acting as a user requires an acting user')
        await self._require_thread_request(path, as_user)
        headers = await self._headers(as_user)
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
        """Yield each page's data items with fresh admission for every cursor request."""
        query = dict(params or {})
        seen = {query['cursor']} if query.get('cursor') is not None else set()
        while True:
            page = await self.get(path, as_user=as_user, params=query)
            for item in page['data']:
                yield item
            cursor = page.get('next_cursor')
            if cursor is None:
                return
            if cursor in seen:
                raise SoevApiError(502, 'upstream_error', 'Repeated pagination cursor')
            seen.add(cursor)
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

    async def _require_thread_request(self, path: str, as_user: str | None) -> None:
        from open_webui.soev.policy_jobs import require_thread_owner, v2_enabled

        if v2_enabled() and path.startswith('/v1/chat/threads/'):
            if self.is_service or not as_user:
                raise SoevApiError(403, 'policy_forbids', 'Owner request required', constraint='thread:owner_request')
            await require_thread_owner(as_user.removeprefix('owui:user:'))

    async def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict | None = None,
        content: bytes | None = None,
        files: dict | None = None,
        data: dict | None = None,
        as_user: str | None = None,
        params: dict | None = None,
        idempotency_key: str | None = None,
        user_access_token: str | None = None,
        admission_headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        if not path.startswith('/') or path.startswith('//'):
            raise ValueError('An API path must start with a single slash')
        if method in {'GET', 'DELETE'}:
            await self._require_thread_request(path, as_user)
        headers = (
            admission_headers
            if admission_headers is not None
            else await self._headers(as_user, user_access_token=user_access_token)
        )
        if idempotency_key is not None:
            headers['Idempotency-Key'] = idempotency_key
        started = time.perf_counter()
        status = None
        route = path.split('?', 1)[0]
        try:
            response = await _shared_client().request(
                method,
                f'{self._base_url}{path}',
                headers=headers,
                params=params,
                json=body,
                content=content,
                files=files,
                data=data,
                timeout=self._timeout,
            )
            status = response.status_code
        except httpx.TransportError as error:
            status = 504 if isinstance(error, httpx.TimeoutException) else 502
            log.warning(
                'soev-api transport failure %s %s %s',
                method,
                route,
                status,
                extra={'status': status, 'request_id': None},
            )
            failure = SoevApiError(status, 'upstream_error', 'soev-api request failed')
            failure.method, failure.path = method, route
            raise failure from None
        finally:
            # [Gradient] The deployed formatter renders the message only, so the timing rides in it.
            log.debug(
                'soev-api request %s %s %s %.1fms',
                method,
                route,
                status,
                (time.perf_counter() - started) * 1000,
            )
        request_id = response.headers.get('X-Request-ID')
        # [Gradient] Status and request id ride in the message; the deployed formatter drops extra.
        log.log(
            logging.INFO if response.is_success else logging.WARNING,
            'soev-api response %s %s %s request_id=%s',
            method,
            route,
            response.status_code,
            request_id,
            extra={'status': response.status_code, 'request_id': request_id},
        )
        if not response.is_success:
            error = _response_error(response)
            error.method, error.path = method, route
            raise error
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


def build_service_client(*, timeout: float = 30.0) -> SoevClient:
    from open_webui import config
    from open_webui.soev.availability import ensure_configured

    ensure_configured()
    return SoevClient(config.SOEV_API_URL, config.SOEV_API_KEY, timeout=timeout, service=True)
