"""Adapt one OWUI turn to a server-owned thread and the existing chat renderer.
Feed the model picker with an OpenAI-type connection whose base URL is
<SOEV_API_URL>/v1/chat and whose API key is the soev-api key."""

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import aclosing
from typing import Any
from urllib.parse import quote

import anyio
from open_webui.models.chats import Chats
from open_webui.socket.main import get_event_emitter
from open_webui.soev import acting, identity
from open_webui.soev.client import ChatEvent, SoevApiError, SoevClient
from starlette.responses import StreamingResponse

log = logging.getLogger(__name__)
_MARKER = re.compile(r'\[([^\[\]]+)\]')
_ROOT = '/v1/chat/threads'


def _input_text(metadata: dict[str, Any], form_data: dict[str, Any]) -> str:
    message = metadata.get('user_message')
    if message is None:
        message = next(
            (message for message in reversed(form_data.get('messages') or []) if message.get('role') == 'user'),
            {},
        )
    content = message.get('content')
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return '\n'.join(
            part['text']
            for part in content
            if isinstance(part, dict) and part.get('type') == 'text' and isinstance(part.get('text'), str)
        )
    raise ValueError('A v2 agent turn requires user_message text')


def _collections(metadata: dict[str, Any]) -> list[str]:
    selected = [item for item in metadata.get('files') or [] if item.get('type') == 'collection']
    selected.extend(metadata.get('knowledge') or [])
    ids = [item if isinstance(item, str) else item['id'] for item in selected]
    if any(not isinstance(key, str) or not key for key in ids):
        raise ValueError('A collection requires an id')
    return list(dict.fromkeys(ids))


def _chunk(delta: dict[str, Any]) -> dict[str, Any]:
    return {'object': 'chat.completion.chunk', 'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]}


def _error(code: str, *, constraint: str | None = None, model: str | None = None) -> dict[str, Any]:
    messages = {
        'not_found': 'The agent thread is unavailable.',
        'thread_active': 'The agent thread is already running or needs recovery.',
        'invalid_field': 'The agent could not accept this input or collection selection.',
        'service_unavailable': 'The agent service is unavailable. Please try again.',
        'halted': 'The agent stopped because a step failed.',
        'orphaned': 'The agent turn was interrupted and needs recovery.',
    }
    message = messages.get(code, 'The agent request failed.')
    if code == 'invalid_field' and constraint == 'chat:model':
        message = f'The agent could not accept model "{model}". Select another model.'
    return {'error': {'code': code, 'message': message}}


class Citations:
    """Keep source numbers stable while buffering incomplete citation markers."""

    def __init__(self) -> None:
        self.sources: dict[str, dict[str, Any]] = {}
        self.pending = ''

    def add(self, source: dict[str, Any]) -> dict[str, Any] | None:
        source_id = source['id']
        if source_id in self.sources:
            return None
        properties = source.get('properties') or {}
        name = properties.get('title') or properties.get('name') or source['ref']
        result = {
            'source': {'id': source_id, 'name': name, 'url': properties.get('source_url') or source['ref']},
            'document': [source['text']],
            'metadata': [
                {**properties, 'source': source_id, 'name': name, 'chunk_id': source_id, 'ref': source['ref']}
            ],
            'n': len(self.sources) + 1,
        }
        self.sources[source_id] = result
        return result

    def rewrite(self, text: str, *, final: bool = False) -> str:
        text = self.pending + text
        self.pending = ''
        start = text.rfind('[')
        if not final and start >= 0 and ']' not in text[start:] and len(text) - start <= 64:
            text, self.pending = text[:start], text[start:]

        def replace(match: re.Match[str]) -> str:
            source_id = match[1]
            source = self.sources.get(source_id) or self.sources.get(source_id.removeprefix('<').removesuffix('>'))
            return f'[{source["n"]}]' if source else match[0]

        return _MARKER.sub(replace, text)


class AgentTurn:
    """Bind one submitted input and its cancellation guard to an assistant message."""

    def __init__(self, client: SoevClient, metadata: dict[str, Any], as_user: str) -> None:
        self.client, self.metadata, self.as_user = client, metadata, as_user
        self.thread_id: str | None = None
        self.position = 0
        self.input_position: int | None = None
        self.terminal = False
        self.citations = Citations()
        self.turn_sources: set[int] = set()
        self.partial = ''
        self.partial_reasoning = ''
        self.active_tools: dict[str, str] = {}
        self.model: str | None = None
        self.emitter: Callable[[dict], Awaitable[None]] | None = None

    def path(self, operation: str = '') -> str:
        return f'{_ROOT}/{quote(self.thread_id or "", safe="")}' + (f'/{operation}' if operation else '')

    async def prepare(self) -> None:
        parent_id = self.metadata.get('parent_message_id')
        chat_id = self.metadata.get('chat_id')
        if not parent_id or not chat_id:
            return
        parent = await Chats.get_message_by_id_and_message_id(chat_id, parent_id)
        bookmark = (parent or {}).get('agent_v2')
        if not bookmark:
            return
        self.thread_id, self.position = bookmark['thread_id'], bookmark['position']
        thread = await self.client.get(self.path(), as_user=self.as_user)
        self._seed_sources(thread['events'], self.position)
        if thread['status']['position'] != self.position:
            branch = await self.client.chat_post(self.path('fork'), {'at': self.position}, as_user=self.as_user)
            self.thread_id = branch['thread_id']

    def _seed_sources(self, events: list[dict], position: int) -> None:
        for event in events:
            if event['position'] <= position and event['type'] == 'source':
                self.citations.add(event['payload'])

    async def persist(self) -> None:
        chat_id, message_id = self.metadata.get('chat_id'), self.metadata.get('message_id')
        if self.thread_id and chat_id and message_id:
            await Chats.upsert_message_to_chat_by_id_and_message_id(
                chat_id, message_id, {'agent_v2': {'thread_id': self.thread_id, 'position': self.position}}
            )

    async def cancel(self) -> None:
        with anyio.CancelScope(shield=True):
            try:
                await self.clear_tools()
            except Exception:
                log.warning('Could not clear v2 tool activity', extra={'thread_id': self.thread_id})
            if self.terminal or self.input_position is None:
                return
            try:
                await self.client.chat_post(self.path('cancel'), {'input': self.input_position}, as_user=self.as_user)
                await self.persist()
            except Exception:
                log.warning('Could not cancel v2 agent turn', extra={'thread_id': self.thread_id})

    async def emit(self, kind: str, data: dict) -> None:
        if self.emitter:
            await self.emitter({'type': kind, 'data': data})

    async def start_tools(self, calls: list[dict]) -> None:
        await self.clear_tools()
        for call in calls:
            name = call['name']
            description = 'Searching the knowledge base…' if name == 'search' else f'Running {name}…'
            self.active_tools[call['id']] = description
            await self.emit('status', {'description': description, 'done': False})

    async def clear_tools(self, call_id: str | None = None) -> None:
        completed = list(self.active_tools) if call_id is None else [call_id]
        cleared = False
        for key in completed:
            description = self.active_tools.pop(key, None)
            if description is not None:
                cleared = True
                await self.emit('status', {'description': description, 'done': True})
        if cleared and self.active_tools:
            description = next(reversed(self.active_tools.values()))
            await self.emit('status', {'description': description, 'done': False})

    async def record_source(self, payload: dict) -> None:
        source = self.citations.add(payload)
        if source:
            await self.emit('source', source)
        self.turn_sources.add(self.citations.sources[payload['id']]['n'])
        await self.emit('panel_filter', {'ns': sorted(self.turn_sources)})

    async def resume(self) -> None:
        events = self.client.chat_stream(
            self.path('resume'), None, as_user=self.as_user, thread_id=self.thread_id, after=self.position
        )
        async with aclosing(events):
            async for event in events:
                if event.event == 'error':
                    raise SoevApiError(503, event.data.get('code', 'service_unavailable'), 'Recovery failed')
                if event.position is not None:
                    self.position = event.position
                if event.event == 'source':
                    source = self.citations.add(event.data['payload'])
                    if source:
                        await self.emit('source', source)
                if event.event == 'status' and event.data['state'] not in {'idle', 'waiting'}:
                    raise SoevApiError(409, 'thread_active', 'Recovery did not finish')

    async def events(self, body: dict) -> AsyncIterator[ChatEvent]:
        path = self.path('inputs') if self.thread_id else _ROOT
        try:
            stream = self.client.chat_stream(
                path, body, as_user=self.as_user, thread_id=self.thread_id, after=self.position
            )
            async with aclosing(stream):
                async for event in stream:
                    yield event
        except SoevApiError as error:
            if (
                path == _ROOT
                or self.input_position is not None
                or error.status != 409
                or error.code != 'thread_active'
                or 'orphaned' not in error.detail
            ):
                raise
            await self.resume()
            stream = self.client.chat_stream(
                path, body, as_user=self.as_user, thread_id=self.thread_id, after=self.position
            )
            async with aclosing(stream):
                async for event in stream:
                    yield event

    async def render(self, event: ChatEvent) -> list[dict[str, Any]]:
        if event.event == 'connection':
            self.thread_id = event.data['thread_id']
            return []
        position = event.position or event.data.get('position')
        if event.event != 'status' and position is not None:
            if position <= self.position:
                return []
            self.position = position
        return await self.render_event(event)

    async def render_event(self, event: ChatEvent) -> list[dict[str, Any]]:
        payload = event.data.get('payload') or {}
        if event.event == 'input' and event.data.get('stream') == 'root' and self.input_position is None:
            self.input_position = self.position
            await self.persist()
        if event.event == 'source':
            await self.record_source(payload)
        if event.event == 'reasoning_delta':
            await self.clear_tools()
            self.partial_reasoning += event.data['text']
            return [_chunk({'reasoning_content': event.data['text']})]
        if event.event == 'delta':
            await self.clear_tools()
            self.partial += event.data['text']
            text = self.citations.rewrite(event.data['text'])
            return [_chunk({'content': text})] if text else []
        if event.event == 'model_output' and event.data.get('stream') == 'root':
            return await self.model_output(payload)
        if event.event in {'tool_output', 'effect_result', 'failure'} and event.data.get('stream') == 'root':
            await self.clear_tools(payload.get('call_id') if event.event == 'tool_output' else None)
        if event.event in {'status', 'error'}:
            return await self.finish(event)
        return []

    async def model_output(self, payload: dict) -> list[dict[str, Any]]:
        await self.start_tools(payload.get('tool_calls', []))
        reasoning = self.remaining_reasoning(payload.get('reasoning') or '')
        content = payload['content']
        if not content.startswith(self.partial):
            log.warning('Durable model output disagrees with streamed text', extra={'thread_id': self.thread_id})
            self.partial = ''
            self.citations.pending = ''
            return []
        text = self.citations.rewrite(content[len(self.partial) :], final=True)
        self.partial = ''
        chunks = []
        if reasoning:
            chunks.append(_chunk({'reasoning_content': reasoning}))
        if text:
            chunks.append(_chunk({'content': text}))
        return chunks

    def remaining_reasoning(self, reasoning: str) -> str:
        partial, self.partial_reasoning = self.partial_reasoning, ''
        if not reasoning.startswith(partial):
            log.warning(
                'Durable model reasoning disagrees with streamed reasoning', extra={'thread_id': self.thread_id}
            )
            return ''
        return reasoning[len(partial) :]

    async def finish(self, event: ChatEvent) -> list[dict[str, Any]]:
        self.terminal = True
        await self.clear_tools()
        if event.event == 'status':
            self.position = event.data['position']
        await self.persist()
        text = self.citations.rewrite('', final=True)
        chunks = [_chunk({'content': text})] if text else []
        state = event.data.get('state')
        await self.emit('status', {'description': state or 'error', 'done': True})
        if event.event == 'error':
            chunks.append(
                _error(
                    event.data.get('code', 'service_unavailable'),
                    constraint=event.data.get('constraint'),
                    model=self.model,
                )
            )
        elif state not in {'idle', 'waiting'}:
            chunks.append(_error(state or 'service_unavailable'))
        return chunks

    async def run(self, body: dict, agent: str | None) -> AsyncIterator[dict[str, Any]]:
        self.model = body.get('model')
        try:
            await identity.ensure_link(self.as_user, self.client)
            await self.prepare()
            self.emitter = await get_event_emitter(self.metadata)
            await self.emit('panel_filter', {'ns': []})
            for source in self.citations.sources.values():
                await self.emit('source', source)
            if not self.thread_id:
                body = {**body, 'agent': agent}
            events = self.events(body)
            async with aclosing(events):
                async for event in events:
                    for chunk in await self.render(event):
                        yield chunk
        except (asyncio.CancelledError, GeneratorExit):
            await self.cancel()
            raise
        except Exception as error:
            await self.cancel()
            if isinstance(error, SoevApiError):
                yield _error(error.code, constraint=error.constraint, model=self.model)
            else:
                yield _error('service_unavailable')


async def call_agent_v2(
    form_data: dict[str, Any], metadata: dict[str, Any], *, agent: str | None, model: str | None = None
) -> StreamingResponse | dict:
    """Submit one user message; the thread owns the conversation history."""
    user_ref = acting.acting_ref() or f'owui:user:{metadata["user_id"]}'
    turn = AgentTurn(identity.build_client(), metadata, user_ref)
    body = {'input': _input_text(metadata, form_data), 'collections': _collections(metadata)}
    if isinstance(model, str) and model:
        body['model'] = model
    chunks = turn.run(body, agent)
    if not form_data.get('stream', True):
        message = {'role': 'assistant', 'content': '', 'reasoning_content': ''}
        async with aclosing(chunks):
            async for chunk in chunks:
                if 'error' in chunk:
                    return chunk
                for key, value in chunk['choices'][0]['delta'].items():
                    message[key] += value
        return {'object': 'chat.completion', 'choices': [{'index': 0, 'message': message, 'finish_reason': 'stop'}]}

    async def stream_body() -> AsyncIterator[str]:
        async with aclosing(chunks):
            async for chunk in chunks:
                yield f'data: {json.dumps(chunk)}\n\n'
        yield 'data: [DONE]\n\n'

    return StreamingResponse(stream_body(), media_type='text/event-stream')
