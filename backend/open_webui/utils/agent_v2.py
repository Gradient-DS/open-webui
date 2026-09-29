"""Adapt one OWUI turn to a server-owned thread and the existing chat renderer.
Feed the model picker with an OpenAI-type connection whose base URL is
<SOEV_API_URL>/v1/chat and whose API key is the soev-api key."""

import asyncio
import json
import logging
import math
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import aclosing
from typing import Any
from urllib.parse import quote

import anyio
from open_webui.models.chats import Chats
from open_webui.models.knowledge import Knowledges
from open_webui.socket.main import get_event_emitter
from open_webui.soev import acting, agent_threads, identity
from open_webui.soev.client import ChatEvent, SoevApiError, SoevClient
from open_webui.utils.chat_id import is_temporary_chat_id
from starlette.responses import StreamingResponse

log = logging.getLogger(__name__)
_MARKER = re.compile(r'\[([^\[\]]+)\]')
_ROOT = '/v1/chat/threads'
_PLACEHOLDER = re.compile(r'{{(\w+)}}')
# [Gradient] How the agents show their tool calls (GET /v1/chat/tools), per process for five minutes.
TOOL_STATUS_CACHE: dict[str, Any] = {'expires_at': 0.0, 'statuses': {}}


async def _tool_statuses(client: SoevClient) -> dict[str, dict[str, Any]]:
    """The declared status of each agent tool; none while soev-api cannot say."""
    if time.monotonic() < TOOL_STATUS_CACHE['expires_at']:
        return TOOL_STATUS_CACHE['statuses']
    try:
        listed = await client.get('/v1/chat/tools')
        statuses = {tool['name']: tool['status'] for tool in listed['data'] if isinstance(tool.get('status'), dict)}
    except (SoevApiError, KeyError, TypeError):
        log.warning("Could not read the agents' tool statuses")
        statuses = {}
    TOOL_STATUS_CACHE.update(expires_at=time.monotonic() + 300.0, statuses=statuses)
    return statuses


def _duration(seconds: float, dutch: bool) -> str:
    """The turn's duration as the v1 agents wrote it: `24 seconds`, `2 minutes and 5 seconds`."""
    total = int(seconds)
    if total <= 0:
        return 'minder dan een seconde' if dutch else 'less than a second'
    minutes, rest = divmod(total, 60)
    words = (
        ('minuut', 'minuten', 'seconde', 'seconden', 'en')
        if dutch
        else ('minute', 'minutes', 'second', 'seconds', 'and')
    )
    parts = [f'{minutes} {words[0] if minutes == 1 else words[1]}'] if minutes else []
    if rest or not minutes:
        parts.append(f'{rest} {words[2] if rest == 1 else words[3]}')
    return f' {words[4]} '.join(parts)


def _summary(count: int, seconds: float, language: str | None) -> str:
    """The v1 closing line of a turn that called tools, in the UI's language."""
    dutch = (language or '').lower().startswith('nl')
    noun = 'tool' if count == 1 else 'tools'
    return f'{count} {noun} {"aangeroepen" if dutch else "called"} in {_duration(seconds, dutch)}'


def _filled(declared: dict[str, Any], params: dict[str, str]) -> dict[str, Any] | None:
    """The declared template, or its fallback, when every placeholder has a value."""
    for template in (declared.get('template'), declared.get('fallback')):
        if isinstance(template, str) and all(name in params for name in _PLACEHOLDER.findall(template)):
            return {'description': template, **{name: params[name] for name in _PLACEHOLDER.findall(template)}}
    return None


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


async def _knowledge(metadata: dict[str, Any]) -> list[dict[str, str]]:
    """The selected knowledge bases with their current name and description, read at send time.

    One the API does not show the user is still sent, named by its key, so the agent refuses the turn.
    """
    keys = _knowledge_keys(metadata)
    if not keys:
        return []
    user_id = None if acting.acting_ref() else metadata['user_id']
    described = await Knowledges.describe_knowledge(keys, user_id=user_id)
    entries = []
    for key in keys:
        name, description = described.get(key, (key, ''))
        entries.append({'key': key, 'name': name, **({'description': description} if description else {})})
    return entries


def _knowledge_keys(metadata: dict[str, Any]) -> list[str]:
    """The selected knowledge bases' keys; an OWUI knowledge id is the soev-api collection key.

    A model's knowledge also lists files and notes, and legacy entries without an id; none is a collection key.
    """
    selected = [item for item in metadata.get('files') or [] if item.get('type') == 'collection']
    selected.extend(
        item
        for item in metadata.get('knowledge') or []
        if isinstance(item, str) or (item.get('type', 'collection') == 'collection' and 'id' in item)
    )
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


def _source_page(pages: Any) -> int | None:
    if isinstance(pages, list) and pages and all(type(page) is int and page >= 1 for page in pages):
        return pages[0] - 1
    return None


def _source_rect(rect: Any) -> dict[str, int | float] | None:
    if not isinstance(rect, dict):
        return None
    coordinates = {key: rect.get(key) for key in ('x0', 'y0', 'x1', 'y1')}
    if any(
        type(value) not in (int, float) or (type(value) is float and not math.isfinite(value))
        for value in coordinates.values()
    ):
        return None
    if coordinates['x1'] <= coordinates['x0'] or coordinates['y1'] <= coordinates['y0']:
        return None
    if 'page' in rect:
        page = _source_page([rect['page']])
        if page is None:
            return None
        coordinates['page'] = page
    return coordinates


def _source_bboxes(value: Any) -> list[dict[str, int | float]] | None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, RecursionError):
            return None
    if not isinstance(value, list) or not value:
        return None
    rects = [rect for item in value if (rect := _source_rect(item)) is not None]
    return rects or None


def _source_metadata(properties: dict[str, Any]) -> dict[str, Any]:
    metadata = {
        key: value
        for key, value in properties.items()
        if key not in {'chunk_content', 'embedding_text', 'bboxes', 'page_numbers', 'file_id', 'page'}
    }
    file_id = properties.get('source_id')
    if isinstance(file_id, str) and file_id:
        metadata['file_id'] = file_id
    page = _source_page(properties.get('page_numbers'))
    if page is not None:
        metadata['page'] = page
    bboxes = _source_bboxes(properties.get('bboxes'))
    if bboxes is not None:
        metadata['bboxes'] = bboxes
    return metadata


class Citations:
    """Keep source numbers stable while buffering incomplete citation markers."""

    def __init__(self) -> None:
        self.sources: dict[str, dict[str, Any]] = {}
        self.document_numbers: dict[str, int] = {}
        self.pending = ''

    def add(self, source: dict[str, Any]) -> dict[str, Any] | None:
        source_id = source['id']
        if source_id in self.sources:
            return None
        properties = source.get('properties') or {}
        ref = source['ref']
        number = self.document_numbers.setdefault(ref, len(self.document_numbers) + 1)
        name = properties.get('title') or properties.get('name') or source['ref']
        result = {
            'source': {'id': ref, 'name': name, 'url': properties.get('source_url') or ref},
            'document': [source['text']],
            'metadata': [
                {**_source_metadata(properties), 'source': ref, 'name': name, 'chunk_id': source_id, 'ref': ref}
            ],
            'n': number,
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
        self.tool_calls = 0
        self.started = time.monotonic()
        self.tool_statuses: dict[str, dict[str, Any]] = {}
        self.knowledge_names: dict[str, str] = {}
        self.awaiting_source: dict[str, tuple[str, str, dict[str, Any], dict[str, Any]]] = {}
        self.model: str | None = None
        self.emitter: Callable[[dict], Awaitable[None]] | None = None

    def path(self, operation: str = '') -> str:
        return f'{_ROOT}/{quote(self.thread_id or "", safe="")}' + (f'/{operation}' if operation else '')

    async def prepare(self) -> None:
        parent_id = self.metadata.get('parent_message_id')
        chat_id = self.metadata.get('chat_id')
        if not parent_id or not chat_id:
            return
        if is_temporary_chat_id(chat_id):
            bookmark = agent_threads.temporary_bookmark(chat_id, parent_id)
        else:
            parent = await Chats.get_message_by_id_and_message_id(chat_id, parent_id)
            bookmark = ((parent or {}).get('meta') or {}).get('agent_v2')
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
        if self.thread_id and chat_id and message_id and is_temporary_chat_id(chat_id):
            bookmark = {'thread_id': self.thread_id, 'position': self.position}
            await agent_threads.remember_temporary(chat_id, message_id, self.as_user, bookmark)
        elif self.thread_id and chat_id and message_id:
            # [Claude] Under meta: reads come from the chat_message table, which drops keys it has no column for.
            # Merged, because tool approval writes meta too.
            stored = await Chats.get_message_by_id_and_message_id(chat_id, message_id)
            meta = {
                **((stored or {}).get('meta') or {}),
                'agent_v2': {'thread_id': self.thread_id, 'position': self.position},
            }
            await Chats.upsert_message_to_chat_by_id_and_message_id(chat_id, message_id, {'meta': meta})

    async def cancel(self) -> None:
        with anyio.CancelScope(shield=True):
            try:
                await self.clear_tools()
            except Exception:
                log.warning('Could not clear v2 tool activity', extra={'thread_id': self.thread_id})
            if self.terminal or self.input_position is None:
                return
            try:
                cancelled = await self.client.chat_post(
                    self.path('cancel'), {'input': self.input_position}, as_user=self.as_user
                )
                # Bookmark the cancel itself: a fork before it copies an input with no answer, which the next
                # turn would resume, rerunning the answer the user just stopped.
                self.position = cancelled['status']['position']
                await self.persist()
            except Exception:
                log.warning('Could not cancel v2 agent turn', extra={'thread_id': self.thread_id})

    async def emit(self, kind: str, data: dict) -> None:
        if self.emitter:
            await self.emitter({'type': kind, 'data': data})

    async def start_tools(self, calls: list[dict]) -> None:
        """Show each call once, while it runs: the frontend appends every status, and settles the last one when
        the message is done."""
        await self.clear_tools()
        for call in calls:
            self.tool_calls += 1
            if (status := self.tool_status(call)) is not None:
                await self.emit('status', {**status, 'done': False})

    def tool_status(self, call: dict) -> dict[str, Any] | None:
        """The call's declared status filled from the call, else a generic line; `None` while it waits on
        the source it returns."""
        name, arguments = call['name'], call.get('arguments') or {}
        # The action names the tool: the frontend lays a turn out as tool activity only for statuses with one.
        generic = {
            'action': name,
            'description': 'Searching the knowledge base…' if name == 'search' else f'Running {name}…',
        }
        declared = self.tool_statuses.get(name)
        if not isinstance(declared, dict):
            return generic
        if isinstance(source := declared.get('source'), str) and isinstance(arguments.get(source), str):
            self.awaiting_source[arguments[source]] = (call['id'], name, declared, arguments)
            return None
        filled = _filled(declared, self.tool_params(declared, arguments, {}))
        return {'action': name, **filled} if filled else generic

    def tool_params(self, declared: dict[str, Any], arguments: dict, properties: dict) -> dict[str, str]:
        """Each declared param's value: `argument.<name>`, `knowledge.<argument>` or `source.<property>`."""
        params = {}
        for param, binding in (declared.get('params') or {}).items():
            kind, _, name = str(binding).partition('.')
            value = None
            if kind == 'argument':
                value = arguments.get(name)
            elif kind == 'knowledge':
                key = arguments.get(name)
                only = list(self.knowledge_names.values()) if len(self.knowledge_names) == 1 else [None]
                value = self.knowledge_names.get(key) if key else only[0]
            elif kind == 'source':
                value = properties.get(name)
            if isinstance(value, str | int | float) and str(value).strip():
                params[param] = str(value)
        return params

    async def clear_tools(self, call_id: str | None = None) -> None:
        """Forget the calls still waiting on their source: a call that ended without one shows nothing."""
        for key, (waiting, *_rest) in list(self.awaiting_source.items()):
            if call_id is None or waiting == call_id:
                del self.awaiting_source[key]

    async def record_source(self, payload: dict) -> None:
        if (awaiting := self.awaiting_source.pop(payload.get('ref') or payload['id'], None)) is not None:
            _call_id, name, declared, arguments = awaiting
            params = self.tool_params(declared, arguments, payload.get('properties') or {})
            if (filled := _filled(declared, params)) is not None:
                await self.emit('status', {'action': name, **filled, 'done': False})
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
        if state != 'idle':
            await self.emit('status', {'description': state or 'error', 'done': True})
        elif self.tool_calls:
            # The v1 closing line: the frontend keeps the last summary as the header of the tool list.
            summary = _summary(self.tool_calls, time.monotonic() - self.started, self.metadata.get('user_language'))
            await self.emit('status', {'action': 'summary', 'description': summary, 'done': True})
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
            self.tool_statuses = await _tool_statuses(self.client)
            self.knowledge_names = {entry['key']: entry['name'] for entry in body['input'].get('knowledge') or []}
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
    body = {'input': {'text': _input_text(metadata, form_data), 'knowledge': await _knowledge(metadata)}}
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
