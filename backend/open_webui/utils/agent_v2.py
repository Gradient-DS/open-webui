"""Adapt one OWUI turn to a server-owned thread and the existing chat renderer.
Feed the model picker with an OpenAI-type connection whose base URL is
<SOEV_API_URL>/v1/chat and whose API key is the soev-api key."""

import asyncio
import html
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
from open_webui.models.config import Config
from open_webui.models.files import Files
from open_webui.models.knowledge import Knowledges
from open_webui.models.users import Users
from open_webui.socket.main import get_event_emitter
from open_webui.soev import acting, agent_threads, identity, ingest, live_documents
from open_webui.soev.client import ChatEvent, SoevApiError, SoevClient
from open_webui.utils.access_control import has_permission
from open_webui.utils.chat_id import is_temporary_chat_id
from open_webui.utils.web_search_state import web_search_state
from starlette.responses import StreamingResponse

log = logging.getLogger(__name__)
_ROOT = '/v1/chat/threads'
_PLACEHOLDER = re.compile(r'{{(\w+)}}')
# [Claude] The name and call id a summary of the conversation is shown under, as the agents' tool statuses name it.
_COMPACTION = 'compaction'
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


def _marker(status: dict[str, Any]) -> dict[str, Any]:
    """The v1 anchor for one shown tool call: the frontend hides it and places the call's status and the
    reasoning around it by its position in the content. The whitespace is what its details tokenizer needs."""
    text = _PLACEHOLDER.sub(lambda match: str(status.get(match[1], match[0])), status['description'])
    name = html.escape(status['action'], quote=True)
    return _chunk(
        {
            'content': f'\n\n<details type="tool_calls" done="true" name="{name}">\n<summary>{html.escape(text)}</summary>\n</details>\n\n'
        }
    )


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


def _from_output(binding: str, elements: list[dict[str, Any]]) -> Any:
    """[Claude] `count.<type>`: how many distinct elements of a type; `first.<type>.<field>`: a field of the first."""
    how, _, rest = binding.partition('.')
    if how == 'count':
        return len({element.get('id') for element in elements if element.get('type') == rest})
    if how == 'first':
        kind, _, field = rest.partition('.')
        return next((element.get(field) for element in elements if element.get('type') == kind), None)
    return None


def _web_items(elements: list[dict[str, Any]]) -> list[dict[str, str]]:
    """[Claude] Each distinct web address among the elements, as the favicon list the status shows."""
    items: dict[str, dict[str, str]] = {}
    for element in elements:
        url = element.get('url')
        if isinstance(url, str) and url and url not in items:
            items[url] = {'link': url, 'title': str(element.get('title') or url)}
    return list(items.values())


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


class KnowledgeUnavailable(Exception):
    """Selected knowledge bases the API does not show the user: unreadable, or deleted."""

    def __init__(self, count: int) -> None:
        super().__init__(f'{count} selected knowledge bases are unavailable')
        self.count = count


async def _knowledge(metadata: dict[str, Any]) -> list[dict[str, str]]:
    """The selected knowledge bases with their current name and description, read at send time.

    Raises:
        KnowledgeUnavailable: the API does not show the user every selected one.
    """
    keys = _knowledge_keys(metadata)
    if not keys:
        return []
    user_id = None if acting.acting_ref() else metadata['user_id']
    described = await Knowledges.describe_knowledge(keys, user_id=user_id)
    if missing := [key for key in keys if key not in described]:
        raise KnowledgeUnavailable(len(missing))
    return [
        {'key': key, 'name': name, **({'description': description} if description else {})}
        for key, (name, description) in ((key, described[key]) for key in keys)
    ]


class AttachmentsUnavailable(Exception):
    """[Claude] Attached files the agent cannot read: each name with why (`gone`, `processing`, `failed`)."""

    def __init__(self, files: list[tuple[str, str]]) -> None:
        super().__init__(f'{len(files)} attached files are unavailable')
        self.files = files


async def _attachments(metadata: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    """[Claude] The files attached in the chat, as the turn's `attachments` field; none while nothing is attached.

    The collection is the one the finished upload reported, else where chat uploads go; the agent checks access.

    Raises:
        AttachmentsUnavailable: a file is gone, still being processed, or failed.
    """
    attached: list[dict[str, str]] = []
    unavailable: list[tuple[str, str]] = []
    entries = {
        entry['id']: entry
        for entry in metadata.get('files') or []
        if entry.get('type') == 'file'
        and entry.get('id')
        and not (entry.get('content_type') or '').startswith('image/')
    }
    for file_id, entry in entries.items():
        file = await Files.get_file_by_id(file_id)
        name = entry.get('name') or (file.filename if file is not None else file_id)
        status = 'gone' if file is None else (file.meta or {}).get('status')
        if file is None or status in ('processing', 'failed'):
            unavailable.append((name, status))
            continue
        key = (
            (file.meta or {}).get('collection_name')
            or entry.get('collection_name')
            or ingest.attachments_collection_key(file.user_id)
        )
        item = {'collection_key': key, 'file_id': file_id, 'name': name}
        if (file.meta or {}).get('source'):
            item['document_ref'] = file.meta['source']['ref']
        attached.append(item)
    if unavailable:
        raise AttachmentsUnavailable(unavailable)
    return {'attachments': attached} if attached else {}


def _instructions(metadata: dict[str, Any]) -> dict[str, str]:
    """[Claude] The custom model's prompt and the chat's prompt (Chat Controls or the user's own, plus the folder's),
    each sent only when it says something."""
    fields = {'assistant_instructions': 'system_prompt', 'user_instructions': 'chat_system_prompt'}
    return {field: text for field, key in fields.items() if isinstance(text := metadata.get(key), str) and text.strip()}


def _tools(
    metadata: dict[str, Any], web_search_allowed: bool, live_documents_allowed: bool
) -> dict[str, dict[str, str]]:
    """[Gradient] The turn's `tools` field, from the web search control. Uit is sent too, so the deployment's
    default never decides for the user, and so is every turn where OWUI does not allow web search."""
    state = web_search_state(metadata.get('features')) if web_search_allowed else 'off'
    documents = (metadata.get('features') or {}).get('live_documents') if live_documents_allowed else 'off'
    if documents not in ('auto', 'required'):
        documents = 'off'
    return {'tools': {'web_search': state, 'search_live_documents': documents, 'attach_live_document': documents}}


async def _live_documents_allowed() -> bool:
    from open_webui.env import AGENT_API_ENABLED

    return AGENT_API_ENABLED and bool(await Config.get('live_documents.enable'))


async def _web_search_allowed(user_id: str) -> bool:
    """[Gradient] `features` is client-supplied: web search must be on for the tenant and the user's to use."""
    if not await Config.get('web.search.enable'):
        return False
    user = await Users.get_user_by_id(user_id)
    if user is None:
        return False
    return user.role == 'admin' or await has_permission(
        user.id, 'features.web_search', await Config.get('user.permissions')
    )


def _unavailable(count: int, language: str | None) -> dict[str, Any]:
    """[Claude] A count, never names: the name of a knowledge base the user cannot read may itself be confidential."""
    if (language or '').lower().startswith('nl'):
        message = (
            f'Je hebt geen toegang tot {count} van de kennisbanken bij deze assistent of chat. '
            'Haal ze uit de chat, of vraag je beheerder om toegang.'
        )
    else:
        message = (
            f"You don't have access to {count} of the knowledge bases on this assistant or chat. "
            'Remove them from the chat, or ask your admin for access.'
        )
    return {'error': {'code': 'knowledge_unavailable', 'message': message}}


_WHY = {
    'en': {'gone': 'no longer available', 'processing': 'still being processed', 'failed': 'could not be processed'},
    'nl': {'gone': 'niet meer beschikbaar', 'processing': 'wordt nog verwerkt', 'failed': 'kon niet worden verwerkt'},
}


def _unattached(files: list[tuple[str, str]], language: str | None) -> dict[str, Any]:
    """[Claude] Names each file: the user attached it, so its name tells them nothing new."""
    dutch = (language or '').lower().startswith('nl')
    why = _WHY['nl' if dutch else 'en']
    listed = ', '.join(f'{name} ({why[reason]})' for name, reason in files)
    if dutch:
        message = (
            f'Deze bijlagen kan de assistent niet lezen: {listed}. '
            'Verwijder ze onder Besturingselementen → Bestanden en verstuur je bericht opnieuw.'
        )
    else:
        message = (
            f"The assistant can't read these attached files: {listed}. "
            'Remove them under Controls → Files and send your message again.'
        )
    return {'error': {'code': 'attachments_unavailable', 'message': message}}


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


def _as_source(element: dict[str, Any], whole: dict[str, Any] | None) -> dict[str, Any]:
    """[Claude] A text the agent read (a chunk, an opened document's text) with what its document says about it,
    in the shape `Citations.add` takes."""
    whole = whole or {}
    properties = {
        'title': whole.get('title') or whole.get('filename'),
        'source_id': whole.get('source_id'),
        'source_url': whole.get('source_url'),
        'page_numbers': element.get('pages'),
        'bboxes': element.get('bboxes'),
    }
    return {
        'id': element['id'],
        'ref': element['ref'],
        'text': element.get('text') or '',
        'properties': {key: value for key, value in properties.items() if value is not None},
    }


def _read(element: dict[str, Any]) -> bool:
    """[Claude] Whether the agent read this element's text, so an answer may cite it."""
    return isinstance(element.get('text'), str) and isinstance(element.get('ref'), str)


class Citations:
    """Keep source numbers stable: one per document, in order of first appearance."""

    def __init__(self) -> None:
        self.sources: dict[str, dict[str, Any]] = {}
        self.document_numbers: dict[str, int] = {}

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

    def number(self, citation: dict[str, Any]) -> int | None:
        """[Claude] The number a served citation shows under; `None` when it is invalid or names no source."""
        source = self.sources.get(citation.get('source') or '') if citation.get('status') == 'resolved' else None
        return None if source is None else source['n']


def _marked(text: str, markers: list[tuple[int, int]]) -> str:
    """[Claude] `text` with ` [n]` inserted at each `(position, n)`, positions in code points; a number repeated at
    one position shows once."""
    shown: set[tuple[int, int]] = set()
    pieces, start = [], 0
    for at, number in sorted(markers, key=lambda marker: marker[0]):
        if (at, number) in shown or not 0 <= at <= len(text):
            continue
        shown.add((at, number))
        pieces.append(text[start:at] + f' [{number}]')
        start = at
    return ''.join(pieces) + text[start:]


class AgentTurn:
    """Bind one submitted input and its cancellation guard to an assistant message."""

    def __init__(self, client: SoevClient, metadata: dict[str, Any], as_user: str) -> None:
        self.client, self.metadata, self.as_user = client, metadata, as_user
        self.thread_id: str | None = None
        self.position = 0
        self.input_position: int | None = None
        self.terminal = False
        self.citations = Citations()
        # [Claude] The source ids this turn flagged, per flag: the panel lists the cited ones.
        self.turn_sources: dict[str, set[str]] = {'current_turn': set(), 'cited_this_turn': set()}
        self.partial = ''
        # [Claude] Markers already shown in the answer being streamed, as (position, number).
        self.streamed: list[tuple[int, int]] = []
        self.streamed_citations = 0
        self.partial_reasoning = ''
        self.tool_calls = 0
        # [Claude] How many calls the last summary counted.
        self.summarized = 0
        self.started = time.monotonic()
        self.tool_statuses: dict[str, dict[str, Any]] = {}
        self.knowledge_names: dict[str, str] = {}
        # [Claude] Every element the thread showed, by real id; a call's arguments name them by that id.
        self.elements: dict[str, dict[str, Any]] = {}
        # [Claude] The calls shown as running, by call id: their tool's name and arguments.
        self.running: dict[str, tuple[str, dict[str, Any]]] = {}
        # [Claude] Done lines of calls whose output landed, held until the model moves on.
        self.settling: list[dict[str, Any]] = []
        self.model: str | None = None
        self.attached_files: dict[str, dict] = {}
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
            if (
                event['position'] <= position
                and event['type'] in {'tool_output', 'attached'}
                and event.get('stream') == 'root'
            ):
                self.keep(event['payload'])

    def keep(self, output: dict[str, Any]) -> list[dict[str, Any]]:
        """[Claude] Remember a tool output's elements, and number each text the agent read; the panel entries of
        the ones not seen before."""
        elements = output.get('elements') or []
        self.elements.update({element['id']: element for element in elements if isinstance(element.get('id'), str)})
        added = []
        for element in elements:
            if _read(element):
                source = self.citations.add(_as_source(element, self.elements.get(element['ref'])))
                if source:
                    added.append(source)
        return added

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
        """Show each call as running. It stays a passing status until its output lands; the frontend replaces a
        status by a later one with the same `call_id`."""
        for call in calls:
            self.tool_calls += 1
            name, arguments = call['name'], call.get('arguments') or {}
            self.running[call['id']] = (name, arguments)
            await self.emit('status', {**self.tool_status(name, arguments), 'call_id': call['id'], 'done': False})

    async def summary(self, event: ChatEvent) -> list[dict[str, Any]]:
        """[Claude] Show the agent summarising the conversation as a call named `compaction`: running on
        `compacting`, done when the root stream's `compaction` event lands. It is not counted as a tool call."""
        if event.event == 'compacting':
            self.running[_COMPACTION] = (_COMPACTION, {})
            await self.emit('status', {**self.tool_status(_COMPACTION, {}), 'call_id': _COMPACTION, 'done': False})
            return []
        if event.data.get('stream') != 'root':
            return []
        return await self.end_tool({'call_id': _COMPACTION})

    async def end_tool(self, output: dict[str, Any]) -> list[dict[str, Any]]:
        """[Claude] Anchor a call's done line in the content once its output lands, and show it once the model
        moves on (see `settle`): until then the model is still working with what the call returned.

        A call whose output is an error shows what it tried, as it did while running."""
        if (call := self.running.pop(output.get('call_id') or '', None)) is None:
            return []
        name, arguments = call
        status = self.tool_status(name, arguments, None if output.get('error') else output)
        self.settling.append({**status, 'call_id': output['call_id'], 'done': True})
        return [_marker(status)]

    async def settle(self) -> None:
        """[Claude] Show the held done lines: the model wrote answer text, called the next tool, or the turn ended."""
        settling, self.settling = self.settling, []
        for status in settling:
            await self.emit('status', status)

    def tool_status(self, name: str, arguments: dict, output: dict | None = None) -> dict[str, Any]:
        """The tool's declared `running` status, or `done` once there is an `output`, else a generic line."""
        # The action names the tool: the frontend lays a turn out as tool activity only for statuses with one.
        # A template like the declared ones, so the frontend translates it.
        generic = (
            {'action': name, 'description': 'Searching the knowledge base…'}
            if name == 'search'
            else {'action': name, 'description': 'Running {{tool}}…', 'tool': name}
        )
        declared = (self.tool_statuses.get(name) or {}).get('running' if output is None else 'done')
        filled = (
            _filled(declared, self.tool_params(declared, arguments, output)) if isinstance(declared, dict) else None
        )
        status = {'action': name, **filled} if filled else generic
        if items := _web_items(self.touched(arguments, output)):
            status['items'] = items
        return status

    def touched(self, arguments: dict, output: dict | None) -> list[dict[str, Any]]:
        """[Claude] The elements a call reads: its output's once it lands, else the ones its arguments name."""
        if output is not None:
            return output.get('elements') or []
        named = [value for value in arguments.values() if isinstance(value, str)]
        return [self.elements[value] for value in named if value in self.elements]

    def tool_params(self, declared: dict[str, Any], arguments: dict, output: dict | None) -> dict[str, str]:
        """Each declared param's value: `argument.<name>`, `knowledge.<argument>`, `element.<argument>.<field>`,
        `output.count.<type>` or `output.first.<type>.<field>`."""
        params = {}
        for param, binding in (declared.get('params') or {}).items():
            kind, _, rest = str(binding).partition('.')
            value = None
            if kind == 'argument':
                value = arguments.get(rest)
            elif kind == 'knowledge':
                key = arguments.get(rest)
                only = list(self.knowledge_names.values()) if len(self.knowledge_names) == 1 else [None]
                value = self.knowledge_names.get(key) if key else only[0]
            elif kind == 'element':
                argument, _, field = rest.partition('.')
                named = arguments.get(argument)
                value = (self.elements.get(named) or {}).get(field) if isinstance(named, str) else None
            elif kind == 'output' and output is not None:
                value = _from_output(rest, output.get('elements') or [])
            if isinstance(value, str | int | float) and str(value).strip():
                params[param] = str(value)
        return params

    async def stop_tools(self) -> list[dict[str, Any]]:
        """[Claude] End the calls the tool budget stopped: they never get an output, and the model answers next,
        so each shows what it tried, anchored before that answer, as a call whose output is an error does."""
        chunks = []
        for call_id in [call_id for call_id in self.running if call_id != _COMPACTION]:
            chunks += await self.end_tool({'call_id': call_id, 'error': 'budget_exceeded'})
        return chunks

    async def clear_tools(self) -> None:
        """Forget the calls still shown as running: a call that ended without an output shows no done line."""
        self.running.clear()

    async def attached(self, payload: dict) -> None:
        file = await live_documents.register_attachment(self.metadata['user_id'], payload)
        chat_id, message_id = self.metadata.get('chat_id'), self.metadata.get('message_id')
        stored = {}
        if chat_id and message_id and not is_temporary_chat_id(chat_id):
            stored = await Chats.get_message_by_id_and_message_id(chat_id, message_id) or {}
        self.attached_files.update({item['id']: item for item in stored.get('files') or [] if item.get('id')})
        self.attached_files[file.id] = live_documents.chat_file(file)
        update = {'files': list(self.attached_files.values())}
        if chat_id and message_id and not is_temporary_chat_id(chat_id):
            await Chats.upsert_message_to_chat_by_id_and_message_id(chat_id, message_id, update)
        await self.emit('files', update)

    async def record_output(self, payload: dict, *, attached: bool = False) -> list[dict[str, Any]]:
        """[Claude] Offer every text a root tool output read to the citation panel, and end its call's status."""
        if attached:
            await self.attached(payload)
        self.keep(payload)
        for element in payload.get('elements') or []:
            if element.get('type') == 'action-required' and element.get('kind') == 'connect':
                await self.emit('action_required', {'kind': 'connect', 'provider': element['provider']})
            if _read(element):
                await self.show_source(element['id'], 'current_turn')
        return await self.end_tool(payload)

    async def show_source(self, source_id: str, flag: str) -> None:
        """[Claude] Flag a source once per turn: `current_turn` when a tool read it now, `cited_this_turn` when the
        answer cites it. The panel keeps the last flags it got for a source and lists the cited ones."""
        if source_id in self.turn_sources[flag] or source_id not in self.citations.sources:
            return
        self.turn_sources[flag].add(source_id)
        await self.emit('source', {**self.citations.sources[source_id], flag: True})

    async def cite(self, citation: dict[str, Any]) -> str:
        """[Claude] The marker a streamed citation adds after the text already sent, once per number there."""
        self.streamed_citations += 1
        number = self.citations.number(citation)
        at = citation.get('at')
        if number is None or not isinstance(at, int) or (at, number) in self.streamed:
            return ''
        await self.show_source(citation['source'], 'cited_this_turn')
        self.streamed.append((at, number))
        return f' [{number}]'

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
                if event.event in {'tool_output', 'attached'} and event.data.get('stream') == 'root':
                    if event.event == 'attached':
                        await self.attached(event.data['payload'])
                    for source in self.keep(event.data['payload']):
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
        if event.event == 'reasoning_delta':
            self.partial_reasoning += event.data['text']
            return [_chunk({'reasoning_content': event.data['text']})]
        if event.event == 'delta':
            if event.data['text']:
                await self.settle()
            if event.data['text'] and not self.running:
                await self.summarize()
            self.partial += event.data['text']
            return [_chunk({'content': event.data['text']})] if event.data['text'] else []
        if event.event == 'citation':
            marker = await self.cite(event.data)
            return [_chunk({'content': marker})] if marker else []
        if event.event == 'model_output' and event.data.get('stream') == 'root':
            return await self.model_output(payload)
        if event.event in {'tool_output', 'attached'} and event.data.get('stream') == 'root':
            return await self.record_output(payload, attached=event.event == 'attached')
        if event.event in {'compacting', 'compaction'}:
            return await self.summary(event)
        if event.event == 'budget_exceeded' and event.data.get('stream') == 'root':
            return await self.stop_tools()
        if event.event == 'failure' and event.data.get('stream') == 'root':
            await self.clear_tools()
        if event.event in {'status', 'error'}:
            return await self.finish(event)
        return []

    async def model_output(self, payload: dict) -> list[dict[str, Any]]:
        await self.settle()
        await self.start_tools(payload.get('tool_calls', []))
        reasoning = self.remaining_reasoning(payload.get('reasoning') or '')
        content, partial = payload['content'], self.partial
        citations = (payload.get('citations') or [])[self.streamed_citations :]
        streamed = self.streamed
        self.partial, self.streamed, self.streamed_citations = '', [], 0
        if not content.startswith(partial):
            log.warning('Durable model output disagrees with streamed text', extra={'thread_id': self.thread_id})
            return []
        markers = [
            (citation['at'] - len(partial), number)
            for citation in citations
            if (number := self.citations.number(citation)) is not None
            and isinstance(citation.get('at'), int)
            and citation['at'] >= len(partial)
            and (citation['at'], number) not in streamed
        ]
        for citation in citations:
            if self.citations.number(citation) is not None:
                await self.show_source(citation['source'], 'cited_this_turn')
        text = _marked(content[len(partial) :], markers)
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
        await self.settle()
        await self.clear_tools()
        if event.event == 'status':
            self.position = event.data['position']
        await self.persist()
        chunks: list[dict[str, Any]] = []
        state = event.data.get('state')
        if state != 'idle':
            await self.emit('status', {'description': state or 'error', 'done': True})
        else:
            await self.summarize()
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

    async def summarize(self) -> None:
        """[Claude] Settle the tools called since the last summary into the v1 closing line, which the frontend keeps
        as the header of the tool list: once the answer starts, and at the end if more were called after it."""
        if self.tool_calls == self.summarized:
            return
        self.summarized = self.tool_calls
        summary = _summary(self.tool_calls, time.monotonic() - self.started, self.metadata.get('user_language'))
        await self.emit('status', {'action': 'summary', 'description': summary, 'done': True})

    async def run(self, body: dict, agent: str | None) -> AsyncIterator[dict[str, Any]]:
        self.model = body.get('model')
        try:
            await identity.ensure_link(self.as_user, self.client)
            self.tool_statuses = await _tool_statuses(self.client)
            self.knowledge_names = {entry['key']: entry['name'] for entry in body['input'].get('knowledge') or []}
            await self.prepare()
            self.emitter = await get_event_emitter(self.metadata)
            # Earlier turns' sources, so their numbers resolve; flagged so the panel leaves them out.
            for source in self.citations.sources.values():
                await self.emit('source', {**source, 'current_turn': False, 'cited_this_turn': False})
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
    chunks = await _sent(turn, _input_text(metadata, form_data), metadata, agent=agent, model=model)
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


async def _sent(
    turn: AgentTurn, text: str, metadata: dict[str, Any], *, agent: str | None, model: str | None
) -> AsyncIterator[dict[str, Any]]:
    """[Claude] The turn's chunks: run with its input, or refused before anything is sent."""
    try:
        knowledge = await _knowledge(metadata)
        attachments = await _attachments(metadata)
    except KnowledgeUnavailable as unavailable:
        return _refused(_unavailable(unavailable.count, metadata.get('user_language')))
    except AttachmentsUnavailable as unavailable:
        return _refused(_unattached(unavailable.files, metadata.get('user_language')))
    tools = _tools(metadata, await _web_search_allowed(metadata['user_id']), await _live_documents_allowed())
    body = {
        'input': {
            'text': text,
            'knowledge': knowledge,
            'attachment_collection': await ingest.ensure_attachments_collection(metadata['user_id'], turn.client),
            **attachments,
            **_instructions(metadata),
            **tools,
        }
    }
    if isinstance(model, str) and model:
        body['model'] = model
    return turn.run(body, agent)


async def _refused(error: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    """A turn refused before anything is sent, shown like any agent error."""
    yield error
