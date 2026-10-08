"""[Gradient] Vergadering: forward meeting calls to soev-api as the caller.

The meeting agent owns every meeting; Open WebUI stores nothing and adds no logic.
"""

import json
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from fastapi.responses import StreamingResponse
from open_webui import config
from open_webui.soev import identity
from open_webui.soev.client import ChatEvent, SoevApiError, SoevClient
from open_webui.soev.meetings import MeetingUnavailable, latest_state, thread_path
from open_webui.utils.auth import get_verified_user
from open_webui.utils.features import is_feature_enabled
from pydantic import BaseModel, ConfigDict, JsonValue

router = APIRouter()

AGENT = 'meeting'
MAX_AUDIO_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class Caller:
    client: SoevClient
    ref: str


class MeetingInput(BaseModel):
    model_config = ConfigDict(extra='forbid')

    input: dict[str, JsonValue]


def _http_error(error: SoevApiError) -> HTTPException:
    return HTTPException(
        status_code=error.status,
        detail={'code': error.code, 'detail': error.detail},
        headers={'Retry-After': error.retry_after} if error.retry_after is not None else None,
    )


async def require_meetings(user=Depends(get_verified_user)):
    if not is_feature_enabled('meetings'):
        raise HTTPException(status_code=403, detail="Feature 'meetings' is not available in your plan")
    if not config.SOEV_API_URL:
        raise HTTPException(status_code=503, detail='Meetings need soev-api, which is not configured')
    return user


async def caller(user=Depends(require_meetings)) -> Caller:
    client = identity.build_client(timeout=120.0)
    try:
        return Caller(client, await identity.acting_ref(user, client))
    except SoevApiError as error:
        raise _http_error(error) from None


Soev = Annotated[Caller, Depends(caller)]
MeetingId = Annotated[str, Path(min_length=1, max_length=256)]


def _thread(meeting_id: str, suffix: str = '') -> str:
    try:
        return thread_path(meeting_id, suffix)
    except MeetingUnavailable:
        raise HTTPException(status_code=404, detail='Meeting not found') from None


async def _submit(soev: Caller, path: str, body: dict, *, thread_id: str | None = None) -> str:
    """Hand one input to the agent and return once it is accepted; the turn runs on without us."""
    stream = soev.client.chat_stream(path, body, as_user=soev.ref, thread_id=thread_id)
    try:
        frame = await anext(stream)
    except SoevApiError as error:
        raise _http_error(error) from None
    finally:
        await stream.aclose()
    return frame.data['thread_id']


@router.get('/agents')
async def list_agents(soev: Soev):
    try:
        return await soev.client.get('/v1/agents', as_user=soev.ref)
    except SoevApiError as error:
        raise _http_error(error) from None


@router.get('')
async def list_meetings(
    soev: Soev,
    limit: Annotated[int | None, Query(ge=1, le=200)] = None,
    before: Annotated[str | None, Query(max_length=256)] = None,
    include: Annotated[str | None, Query(max_length=64, pattern=r'^[a-z_,]+$')] = None,
):
    params = {'agent': AGENT}
    if include is not None:
        params['include'] = include
    if limit is not None:
        params['limit'] = limit
    if before is not None:
        params['before'] = before
    try:
        return await soev.client.get('/v1/chat/threads', as_user=soev.ref, params=params)
    except SoevApiError as error:
        raise _http_error(error) from None


@router.post('', status_code=201)
async def start_meeting(body: MeetingInput, soev: Soev):
    thread_id = await _submit(soev, '/v1/chat/threads', {'agent': AGENT, 'input': body.input})
    return {'id': thread_id}


@router.post('/audio', status_code=201)
async def upload_audio(request: Request, soev: Soev, name: Annotated[str, Query(min_length=1, max_length=255)]):
    declared = request.headers.get('content-length')
    if declared is not None and declared.isdigit() and int(declared) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail='Audio exceeds 64 MiB')
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_AUDIO_BYTES:
            raise HTTPException(status_code=413, detail='Audio exceeds 64 MiB')
    if not body:
        raise HTTPException(status_code=400, detail='No audio received')
    try:
        return await soev.client.post_bytes('/v1/audio', bytes(body), as_user=soev.ref, params={'name': name})
    except SoevApiError as error:
        raise _http_error(error) from None


@router.get('/{meeting_id}')
async def get_meeting(meeting_id: MeetingId, soev: Soev):
    try:
        thread = await soev.client.get(_thread(meeting_id), as_user=soev.ref)
    except SoevApiError as error:
        raise _http_error(error) from None
    return {'status': thread['status']['state'], 'state': latest_state(thread.get('events') or [])}


@router.post('/{meeting_id}/inputs', status_code=202)
async def send_input(meeting_id: MeetingId, body: MeetingInput, request: Request, soev: Soev):
    path, payload = _thread(meeting_id, '/inputs'), {'input': body.input}
    if 'text/event-stream' not in request.headers.get('accept', ''):
        await _submit(soev, path, payload, thread_id=meeting_id)
        return {'id': meeting_id}
    stream = soev.client.chat_stream(path, payload, as_user=soev.ref, thread_id=meeting_id)
    try:
        await anext(stream)  # the client's own `connection` frame: the input was accepted
    except SoevApiError as error:
        await stream.aclose()
        raise _http_error(error) from None
    return StreamingResponse(
        _sse(stream),
        media_type='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


def _frame(event: ChatEvent) -> str:
    head = f'id: {event.position}\n' if event.position is not None else ''
    return f'{head}event: {event.event}\ndata: {json.dumps(event.data, separators=(",", ":"))}\n\n'


async def _sse(stream: AsyncGenerator[ChatEvent, None]) -> AsyncIterator[str]:
    """Pass the turn's frames through as they arrive; a client that leaves closes the upstream stream."""
    try:
        async for event in stream:
            yield _frame(event)
    except SoevApiError as error:
        yield _frame(ChatEvent('error', {'code': error.code, 'detail': error.detail}))
    finally:
        await stream.aclose()


@router.delete('/{meeting_id}', status_code=204)
async def delete_meeting(meeting_id: MeetingId, soev: Soev):
    try:
        await soev.client.chat_delete(_thread(meeting_id), as_user=soev.ref)
    except SoevApiError as error:
        raise _http_error(error) from None
    return Response(status_code=204)
