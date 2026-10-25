"""Prepared UI thread client. No content is retained between requests."""

import re
from dataclasses import dataclass
from typing import Literal, TypeVar

import httpx
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from open_webui.soev import acting
from open_webui.soev.client import _shared_client

ThreadState = Literal['reconnect', 'unavailable', 'sign_in', 'invalid_request']


class ThreadError(Exception):
    def __init__(self, status: int, state: ThreadState) -> None:
        super().__init__(state)
        self.status, self.state = status, state


class WireModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


Wire = TypeVar('Wire', bound=WireModel)


class MessageView(WireModel):
    position: int = Field(gt=0)
    content: JsonValue
    finished: bool
    version: str | None


class ProjectionPage(WireModel):
    thread_id: str
    generation: str
    title: str | None
    preferences: JsonValue
    messages: list[MessageView]
    next_cursor: str | None


class IndexItem(WireModel):
    thread_id: str
    title: str | None
    updated_at: str
    parent_thread: str | None
    parent_position: int | None


class IndexPage(WireModel):
    items: list[IndexItem]
    next_cursor: str | None


class CreatedThread(WireModel):
    thread_id: str


class FeedbackItem(WireModel):
    thread_id: str
    rating: Literal[-1, 1]
    model: str
    agent: str
    time: str
    comment: str | None


class FeedbackPage(WireModel):
    items: list[FeedbackItem]
    next_cursor: str | None


class FeedbackCount(WireModel):
    model: str
    agent: str
    rating: Literal[-1, 1]
    count: int = Field(ge=0)


class FeedbackSummary(WireModel):
    items: list[FeedbackCount]
    next_cursor: str | None


class Tokens(WireModel):
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)


class UsageMetrics(WireModel):
    model_outputs: int = Field(ge=0)
    unknown_outputs: int = Field(ge=0)
    known_tokens: Tokens
    tokens: Tokens | None


class Organization(WireModel):
    """Only the caller's organisational fields from the migration's thread mapping."""

    id: str
    thread_id: str
    folder_id: str | None = None
    pinned: bool = False
    archived: bool = False
    last_read_at: int | None = None
    branch_thread_ids: list[str] = Field(default_factory=list, exclude=True)


def join_index(page: IndexPage, records: list[Organization]) -> dict:
    by_thread = {record.thread_id: record for record in records}
    items = []
    for item in page.items:
        record = by_thread.get(item.thread_id)
        items.append({**item.model_dump(), 'organization': record.model_dump() if record else None})
    return {'items': items, 'next_cursor': page.next_cursor}


@dataclass(frozen=True)
class ProjectionResult:
    page: ProjectionPage | None
    etag: str | None
    not_modified: bool = False


def _identifier(value: str) -> str:
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', value) or value in {'.', '..'}:
        raise ThreadError(422, 'invalid_request')
    return value


def _page_size(value: int) -> int:
    if not 1 <= value <= 200:
        raise ThreadError(422, 'invalid_request')
    return value


class ThreadClient:
    def __init__(self, base_url: str, product_key: str, *, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip('/')
        self.product_key = product_key
        self.timeout = timeout

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        body: dict | None = None,
        etag: str | None = None,
    ) -> httpx.Response:
        # Resolve the actual requester on every call, never acting_as or an owner hint.
        token = await acting.user_token(acting.requester_ref())
        if not token:
            raise ThreadError(401, 'sign_in')
        headers = {
            'Authorization': f'Bearer {self.product_key}',
            'X-Soev-User-Token': token,
            'Accept': 'application/json',
        }
        if etag is not None:
            headers['If-None-Match'] = etag
        try:
            response = await _shared_client().request(
                method,
                self.base_url + path,
                headers=headers,
                params=params,
                json=body,
                timeout=self.timeout,
                follow_redirects=False,
            )
        except httpx.TransportError:
            raise ThreadError(503, 'reconnect') from None
        if response.status_code == 304 and etag is not None:
            return response
        if response.status_code in {200, 201, 204}:
            return response
        status = response.status_code
        if status in {401, 403, 404, 422}:
            mapped = {401: 'sign_in', 403: 'unavailable', 404: 'unavailable', 422: 'invalid_request'}
            raise ThreadError(404 if status == 403 else status, mapped[status])
        raise ThreadError(503, 'reconnect')

    @staticmethod
    def _parse(response: httpx.Response, model: type[Wire]) -> Wire:
        try:
            return model.model_validate_json(response.content)
        except ValidationError:
            raise ThreadError(502, 'reconnect') from None

    async def projection(
        self,
        thread_id: str,
        *,
        window: int = 20,
        before: str = '',
        etag: str | None = None,
    ) -> ProjectionResult:
        response = await self._request(
            'GET',
            f'/v1/threads/{_identifier(thread_id)}',
            params={'window': _page_size(window), 'before': before},
            etag=etag,
        )
        if response.status_code == 304:
            return ProjectionResult(None, response.headers.get('ETag', etag), True)
        page = self._parse(response, ProjectionPage)
        if page.thread_id != thread_id:
            raise ThreadError(502, 'reconnect')
        return ProjectionResult(page, response.headers.get('ETag'))

    async def index(self, *, q: str = '', limit: int = 50, cursor: str = '') -> IndexPage:
        return self._parse(
            await self._request(
                'GET',
                '/v1/threads',
                params={'q': q, 'limit': _page_size(limit), 'cursor': cursor},
            ),
            IndexPage,
        )

    async def forks(
        self,
        thread_id: str,
        *,
        at: int | None = None,
        limit: int = 50,
        cursor: str = '',
    ) -> IndexPage:
        if at is not None and at <= 0:
            raise ThreadError(422, 'invalid_request')
        return self._parse(
            await self._request(
                'GET',
                f'/v1/threads/{_identifier(thread_id)}/forks',
                params={'at': at if at is not None else '', 'limit': _page_size(limit), 'cursor': cursor},
            ),
            IndexPage,
        )

    async def fork(self, thread_id: str, *, at: int) -> CreatedThread:
        if at <= 0:
            raise ThreadError(422, 'invalid_request')
        return self._parse(
            await self._request(
                'POST',
                f'/v1/threads/{_identifier(thread_id)}/fork',
                body={'at': at},
            ),
            CreatedThread,
        )

    async def create(self, *, agent: str, input: JsonValue, model: str | None = None) -> CreatedThread:
        response = await self._request(
            'POST', '/v1/chat/threads', body={'agent': agent, 'input': input, 'model': model}
        )
        if response.status_code != 201:
            raise ThreadError(502, 'reconnect')
        try:
            payload = response.json()
            return CreatedThread.model_validate({'thread_id': _identifier(payload['thread_id'])})
        except (ValueError, KeyError, TypeError):
            raise ThreadError(502, 'reconnect') from None

    async def delete(self, thread_id: str) -> None:
        response = await self._request('DELETE', f'/v1/chat/threads/{_identifier(thread_id)}')
        if response.status_code != 204:
            raise ThreadError(502, 'reconnect')

    async def answer(self, thread_id: str, *, answering: str, input: JsonValue) -> None:
        await self._request(
            'POST',
            f'/v1/chat/threads/{_identifier(thread_id)}/inputs',
            body={'input': input, 'answering': answering},
        )

    async def cancel(self, thread_id: str) -> None:
        await self._request('POST', f'/v1/chat/threads/{_identifier(thread_id)}/cancel', body={})

    async def feedback(
        self,
        thread_id: str,
        *,
        position: int,
        rating: int,
        comment: str | None = None,
        comment_authored: bool = False,
    ) -> None:
        if position <= 0 or type(rating) is not int or rating not in {-1, 1}:
            raise ThreadError(422, 'invalid_request')
        if comment is not None and (not comment_authored or len(comment) > 2000):
            raise ThreadError(422, 'invalid_request')
        response = await self._request(
            'POST',
            f'/v1/threads/{_identifier(thread_id)}/feedback',
            body={'position': position, 'rating': rating, 'comment': comment, 'comment_authored': comment_authored},
        )
        if response.status_code != 204:
            raise ThreadError(502, 'reconnect')

    async def feedbacks(self, *, limit: int = 50, cursor: str = '') -> FeedbackPage:
        return self._parse(
            await self._request(
                'GET',
                '/v1/feedback',
                params={'limit': _page_size(limit), 'cursor': cursor},
            ),
            FeedbackPage,
        )

    async def feedback_summary(self, *, limit: int = 50, cursor: str = '') -> FeedbackSummary:
        return self._parse(
            await self._request(
                'GET',
                '/v1/feedback/summary',
                params={'limit': _page_size(limit), 'cursor': cursor},
            ),
            FeedbackSummary,
        )

    async def usage(self) -> UsageMetrics:
        return self._parse(await self._request('GET', '/v1/threads/usage'), UsageMetrics)


def enabled() -> bool:
    from open_webui import config, env

    return bool(getattr(config, 'ENABLE_V2_THREADS', False) and env.AGENT_API_ENABLED and config.SOEV_API_URL)


def build_client() -> ThreadClient:
    from open_webui import config

    return ThreadClient(config.SOEV_API_URL, config.SOEV_API_KEY)
