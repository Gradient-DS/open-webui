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


class Organization(WireModel):
    """Only the caller's organisational fields from the migration's thread mapping."""

    id: str
    thread_id: str
    folder_id: str | None = None
    pinned: bool = False
    archived: bool = False
    last_read_at: int | None = None


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
        if response.status_code == 200:
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


def enabled() -> bool:
    from open_webui import config, env

    return bool(config.ENABLE_V2_THREADS and env.AGENT_API_ENABLED and config.SOEV_API_URL)


def build_client() -> ThreadClient:
    from open_webui import config

    return ThreadClient(config.SOEV_API_URL, config.SOEV_API_KEY)
