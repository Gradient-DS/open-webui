"""Adapt one OWUI turn to a server-owned thread and the existing chat renderer.
The model picker, its default and task completions come from soev-api (soev/model_catalog.py)."""

import asyncio
import base64
import hashlib
import html
import json
import logging
import math
import re
import time
import unicodedata
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import aclosing
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import anyio
from open_webui.models.access_grants import AccessGrants
from open_webui.models.chats import Chats
from open_webui.models.config import Config
from open_webui.models.files import FileModel, Files
from open_webui.models.folders import Folders
from open_webui.models.knowledge import Knowledges
from open_webui.models.notes import Notes
from open_webui.models.users import Users
from open_webui.socket.main import get_event_emitter
from open_webui.soev import acting, agent_threads, assistant_files, identity, ingest, live_documents
from open_webui.soev.client import ChatEvent, SoevApiError, SoevClient
from open_webui.soev.meetings import MEETING_TEXT_PREFIX, MeetingUnavailable, meeting_document
from open_webui.storage.provider import Storage
from open_webui.utils.access_control import has_permission
from open_webui.utils.access_control.files import get_owner_accessible_folder_files, has_access_to_file
from open_webui.utils.access_control.folders import has_folder_access
from open_webui.utils.chat_id import is_temporary_chat_id
from open_webui.utils.mail_status import mail_search_status

from open_webui.utils.features import is_feature_enabled
from open_webui.utils.misc import get_content_from_message, get_message_list
from open_webui.utils.tool_state import tool_state
from starlette.responses import JSONResponse, StreamingResponse
from open_webui.utils.access_control import can_access_admin_chats, can_bypass_access_control

log = logging.getLogger(__name__)
_ROOT = '/v1/chat/threads'
_PLACEHOLDER = re.compile(r'{{(\w+)}}')
# [Claude] The name and call id a summary of the conversation is shown under, as the agents' tool statuses name it.
_COMPACTION = 'compaction'
# [Claude] The thread states in which the last turn is over: the agent finished it, or the user cancelled it.
_ENDED = frozenset({'finished', 'cancelled'})
# [Claude] The element type of a file attached to the chat; in a root tool output, the agent attached it.
_ATTACHED = 'attached-document'
# [Gradient] How the agents show their tool calls (GET /v1/chat/tools), per process for five minutes.
TOOL_STATUS_CACHE: dict[str, Any] = {'expires_at': 0.0, 'statuses': {}}
# [Claude] Yielded once by a turn when soev-api accepted it: an error before it gets an HTTP status, not a stream.
_ACCEPTED: dict[str, Any] = {}
# [Claude] Upstream statuses the user can act on, passed through; any other failure maps by code, else 502.
_USER_STATUSES = frozenset({403, 404, 409, 413, 415, 422, 429})
_CODE_STATUSES = {'invalid_field': 422, 'not_found': 404, 'thread_active': 409, 'service_unavailable': 503}


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
            'content': (
                f'\n\n<details type="tool_calls" done="true" name="{name}">\n'
                f'<summary>{html.escape(text)}</summary>\n</details>\n\n'
            )
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


def _field(element: dict[str, Any], field: str) -> Any:
    """[Claude] A field of an element; a document without a title goes by its filename, as an attached one does."""
    value = element.get(field)
    if field == 'title' and not (isinstance(value, str) and value.strip()):
        value = element.get('filename')
    return value


def _from_output(binding: str, elements: list[dict[str, Any]]) -> Any:
    """[Claude] `count.<type>`: how many distinct elements of a type; `first.<type>.<field>`: a field of the first."""
    how, _, rest = binding.partition('.')
    if how == 'count':
        return len({element.get('id') for element in elements if element.get('type') == rest})
    if how == 'first':
        kind, _, field = rest.partition('.')
        return next((_field(element, field) for element in elements if element.get('type') == kind), None)
    return None


# [Claude] How a call shows when soev-api declares no status for its tool or the declared one cannot be filled:
# running, done and failed, each a template the frontend translates. A named variant takes the document's `title`.
_GENERIC = {
    'search': ('Searching the knowledge base…', 'Searched the knowledge base', 'Could not search the knowledge bases'),
    'find_documents': ('Finding documents…', 'Found documents', 'Could not find documents'),
    'list_documents': ('Listing documents…', 'Listed documents', 'Could not list documents'),
    'open_document': ('Opening document...', 'Opened document', 'Could not open document'),
    'web_search': ('Searching the web…', 'Searched the web', 'Could not search the web'),
    'fetch': ('Fetching web pages...', 'Read web pages', 'Could not read web page'),
    'reopen': (
        'Looking back at earlier results...',
        'Looked back at earlier results',
        'Could not reopen earlier results',
    ),
    'search_live_documents': ('Searching your files...', 'Searched your files', 'Could not search your files'),
    'list_live_folder': ('Browsing your files...', 'Browsed your files', 'Could not browse your files'),
    'attach_live_document': ('Opening documents...', 'Opened documents', 'Could not open document'),
    'search_mail': ('Searching your mail...', 'Searched your mail', 'Could not search your mail'),
    'read_mail': ('Reading email...', 'Read email', 'Could not read email'),
    _COMPACTION: (
        'Summarising the earlier conversation...',
        'Summarised the earlier conversation',
        'Could not summarise the earlier conversation',
    ),
}
_NAMED = {'open_document': ('Opening document: {{title}}', 'Opened document: {{title}}')}
# [Claude] A tool OWUI has no label for still shows its name, but reads as finished once it is.
_UNKNOWN = ('Running {{tool}}…', 'Ran {{tool}}', 'Could not run {{tool}}')
_PHASES = ('running', 'done', 'failed')


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


@dataclass(frozen=True)
class Skipped:
    """[Claude] An item of the chat or assistant this turn runs without: `kind` is knowledge, file, note, chat or meeting;
    `reason` gone (deleted), denied (exists, not for this user), failed, processing, unmigrated (a v1 upload never
    ingested for v2) or limit (a folder note past the turn's ten texts). `in_chat` is False for an assistant's or a
    folder's own item, which the chat cannot drop; `folder` marks an item of the chat's folder or an attached one."""

    kind: str
    reason: str
    id: str
    name: str | None = None
    in_chat: bool = True
    folder: bool = False


_FOLDER_ITEMS = ('collection', 'file', 'note')


async def _folder_items(metadata: dict[str, Any]) -> tuple[list[dict[str, Any]], list[Skipped]]:
    """[Claude] The knowledge bases, files and notes of the chat's folder (`folder_knowledge`, set by the middleware)
    and of folders attached to the chat or to the assistant, once each, as far as each folder's owner can still
    delegate them. A folder the user cannot read is left out; only the chat's own entry is dropped when it is gone."""
    items = list(metadata.get('folder_knowledge') or [])
    skipped = []
    attached: dict[str, tuple[dict[str, Any], bool]] = {}
    for source, in_chat in ((metadata.get('files'), True), (metadata.get('knowledge'), False)):
        for entry in source if isinstance(source, list) else []:
            if isinstance(entry, dict) and entry.get('type') == 'folder' and isinstance(entry.get('id'), str):
                attached.setdefault(entry['id'], (entry, in_chat))
    user = await Users.get_user_by_id(metadata['user_id']) if attached else None
    for entry, in_chat in attached.values():
        folder = await Folders.get_folder_by_id(entry['id'])
        allowed = bool(
            folder
            and user
            and (can_bypass_access_control(user) or await has_folder_access(user.id, folder, 'read', db=None))
        )
        if not allowed:
            name = entry.get('name') or (folder.name if folder else None)
            skipped.append(Skipped('folder', 'denied' if folder else 'gone', entry['id'], name, in_chat))
            continue
        items.extend({**item, 'folder_id': folder.id} for item in await get_owner_accessible_folder_files(folder))
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for item in items:
        if isinstance(item, dict) and item.get('type') in _FOLDER_ITEMS and isinstance(item.get('id'), str):
            unique.setdefault((item['type'], item['id']), item)
    return list(unique.values()), skipped


async def _knowledge(
    metadata: dict[str, Any], folder: list[dict[str, Any]] = ()
) -> tuple[list[dict[str, str]], list[Skipped]]:
    """The selected knowledge bases with their current name and description, read at send time, and the ones the
    API does not show the user: deleted for everyone, or hidden from them. The folder's come after the chat's and the
    assistant's, read as the user too."""
    keys = _knowledge_keys(metadata, folder)
    if not keys:
        return [], []
    user_id = None if acting.acting_ref() else metadata['user_id']
    described = await Knowledges.describe_knowledge(keys, user_id=user_id)
    skipped = []
    if missing := [key for key in keys if key not in described]:
        existing = await Knowledges.existing_knowledge(missing)
        chosen = {
            item['id']: item.get('name')
            for item in metadata.get('files') or []
            if item.get('type') == 'collection' and item.get('id')
        }
        folder_only = set(_knowledge_keys(metadata, folder)) - set(_knowledge_keys(metadata))
        skipped = [
            Skipped(
                'knowledge',
                'denied' if key in existing else 'gone',
                key,
                chosen.get(key),
                key in chosen,
                folder=key in folder_only,
            )
            for key in missing
        ]
    knowledge = [
        {'key': key, 'name': name, **({'description': description} if description else {})}
        for key, (name, description) in ((key, described[key]) for key in keys if key in described)
    ]
    return knowledge, skipped


async def _knowledge_document_reason(knowledge_id: str, file_id: str, user_id: str | None) -> str:
    """[Claude] Why a KB-picked document cannot be read: still landing or failed on its File row, denied while its
    KB exists but not for the user, else gone."""
    file = await Files.get_file_by_id(file_id)
    status = (file.meta or {}).get('status') if file is not None else None
    if status in ('processing', 'failed'):
        return status
    if user_id is not None and await Knowledges.existing_knowledge([knowledge_id]):
        if not await Knowledges.describe_knowledge([knowledge_id], user_id=user_id):
            return 'denied'
    return 'gone'


async def _assistant_file_reason(key: str, file: FileModel | None, model_id: str, user_id: str | None) -> str:
    """[Claude] Why an assistant's file is not in its collection for the user: deleted, failed there, denied while
    the collection exists but not for the user, else still on its way; a file no sync has submitted asks for one."""
    if file is None:
        return 'gone'
    meta = file.meta or {}
    if meta.get('status') == 'failed' and meta.get('soev_collection_key') == key:
        return 'failed'
    if user_id is not None and await Knowledges.existing_knowledge([key]):
        if not await Knowledges.describe_knowledge([key], user_id=user_id):
            return 'denied'
    if meta.get('soev_job') is None:
        assistant_files.schedule(model_id)
    return 'processing'


async def _assistant_attachments(
    metadata: dict[str, Any],
    in_chat: set[str],
    user_id: str | None,
    attached: list[dict[str, str]],
    unavailable: list[Skipped],
) -> None:
    """[Claude] The assistant's own files not also attached in the chat, read in its collection as the user."""
    # An assistant/LLM split turn runs the picked LLM as `model_id`; the assistant is `assistant_id`.
    model_id = metadata.get('assistant_id') or metadata.get('model_id')
    if not model_id:
        return
    key = ingest.assistant_collection_key(model_id)
    names = {
        entry['id']: entry.get('name')
        for entry in metadata.get('knowledge') or []
        if isinstance(entry, dict) and entry.get('type') == 'file' and entry.get('id')
    }
    for file_id in assistant_files.file_ids({'knowledge': metadata.get('knowledge')}):
        if file_id in in_chat:
            continue
        document = await Knowledges.knowledge_document(key, file_id, user_id=user_id)
        if document is not None:
            name = names[file_id] or document.get('filename') or file_id
            attached.append({'collection_key': key, 'file_id': file_id, 'name': name})
            continue
        file = await Files.get_file_by_id(file_id)
        name = names[file_id] or (file.filename if file is not None else None)
        reason = await _assistant_file_reason(key, file, model_id, user_id)
        unavailable.append(Skipped('file', reason, file_id, name, in_chat=False))


async def _attachments(metadata: dict[str, Any], folder: list[dict[str, Any]] = ()) -> dict[str, Any]:
    """[Claude] The files attached in the chat, the assistant's own files and the folder's files not among them, as
    the turn's `attachments` field; none while nothing is attached.

    The collection is the one the file's own record names (a live document, or an upload the v2 migration ingested),
    else where chat uploads go; the agent checks access. A chat entry's `collection_name` is never read: a v1 entry
    names the per-file vector collection `file-<id>`, which soev-api does not have. A record that still names it is
    an upload the migration left behind, skipped as `unmigrated`.
    A file picked from a knowledge base (`knowledge_id`) is that KB's document, read there as the user: synced
    documents have no File row, and an uploaded one's row does not name the KB. A file the agent cannot read is
    left out under `skipped`; a processing one is also noted for the agent.
    An assistant's file (a `file` entry of its knowledge) is read in the assistant's collection as the user, so a
    shared assistant's files reach everyone it is shared with (`soev/assistant_files.py`).
    """
    attached: list[dict[str, str]] = []
    unavailable: list[Skipped] = []
    notes: list[str] = []
    entries = {entry['id']: entry for entry in metadata.get('files') or [] if ingest.is_chat_attachment(entry)}
    user_id = None if acting.acting_ref() else metadata['user_id']
    for file_id, entry in entries.items():
        await _chat_attachment(file_id, entry, user_id, attached, unavailable, notes)
    await _assistant_attachments(metadata, set(entries), user_id, attached, unavailable)
    seen = set(entries) | {item['file_id'] for item in attached} | {item.id for item in unavailable}
    for entry in folder:
        if entry['type'] == 'file' and entry['id'] not in seen and ingest.is_chat_attachment(entry):
            seen.add(entry['id'])
            await _folder_attachment(entry, user_id, attached, unavailable)
    notes.extend(f'still processing: {item.name}' for item in unavailable if item.reason == 'processing')
    return {
        **({'attachments': attached} if attached else {}),
        **({'attachment_notes': notes} if notes else {}),
        **({'skipped': unavailable} if unavailable else {}),
    }


async def _chat_attachment(
    file_id: str,
    entry: dict[str, Any],
    user_id: str | None,
    attached: list[dict[str, str]],
    unavailable: list[Skipped],
    notes: list[str],
) -> None:
    """[Claude] One file attached in the chat, as `_attachments` reads it."""
    if knowledge_id := entry.get('knowledge_id'):
        document = await Knowledges.knowledge_document(knowledge_id, file_id, user_id=user_id)
        name = entry.get('name') or (document or {}).get('filename') or file_id
        if document is not None:
            attached.append({'collection_key': knowledge_id, 'file_id': file_id, 'name': name})
            return
        unavailable.append(
            Skipped('file', await _knowledge_document_reason(knowledge_id, file_id, user_id), file_id, name)
        )
        return
    file = await Files.get_file_by_id(file_id)
    name = entry.get('name') or (file.filename if file is not None else file_id)
    status = 'gone' if file is None else (file.meta or {}).get('status')
    if file is None or status in ('processing', 'failed'):
        if entry.get('attached_by') or (file is not None and (file.meta or {}).get('attached_by')):
            log.info('Skipping reference attachment %s (%s)', file_id, status)
            if status == 'processing':
                notes.append(f'still processing: {name}')
        else:
            unavailable.append(Skipped('file', status or 'gone', file_id, name))
        return
    key = (file.meta or {}).get('collection_name') or ingest.attachments_collection_key(file.user_id)
    if key == f'file-{file.id}':
        # [Claude] Still in the v1 per-file collection: `migrate --uploads` did not ingest it.
        unavailable.append(Skipped('file', 'unmigrated', file_id, name))
        return
    item = {'collection_key': key, 'file_id': file_id, 'name': name}
    if (file.meta or {}).get('source'):
        item['document_ref'] = file.meta['source']['ref']
    attached.append(item)


async def _folder_attachment(
    entry: dict[str, Any], user_id: str | None, attached: list[dict[str, str]], unavailable: list[Skipped]
) -> None:
    """[Claude] A folder's file, in the collection a chat file would be read from (its KB, else the one its record
    names, else its uploader's attachments), checked there as the user first: a shared folder's file may sit in a
    collection only its owner reads, which the agent would refuse the whole turn for."""
    file_id = entry['id']
    collection = entry.get('collection')
    key = entry.get('knowledge_id') or (collection.get('id') if isinstance(collection, dict) else None)
    file = await Files.get_file_by_id(file_id)
    name = entry.get('name') or (file.filename if file is not None else None) or file_id
    if not key and file is not None and entry.get('folder_id'):
        # [Gradient] A shared folder's file is read in the folder's own collection (`soev/folder_files.py`).
        shared = ingest.folder_collection_key(entry['folder_id'])
        if await Knowledges.knowledge_document(shared, file_id, user_id=user_id) is not None:
            key = shared
    if not key and file is not None:
        key = (file.meta or {}).get('collection_name') or ingest.attachments_collection_key(file.user_id)
        if key == f'file-{file.id}':
            unavailable.append(Skipped('file', 'unmigrated', file_id, name, in_chat=False, folder=True))
            return
    if not key:
        unavailable.append(Skipped('file', 'gone', file_id, name, in_chat=False, folder=True))
        return
    document = await Knowledges.knowledge_document(key, file_id, user_id=user_id)
    if document is None:
        reason = await _knowledge_document_reason(key, file_id, user_id)
        unavailable.append(Skipped('file', reason, file_id, name, in_chat=False, folder=True))
        return
    item = {'collection_key': key, 'file_id': file_id, 'name': name}
    if file is not None and (file.meta or {}).get('source'):
        item['document_ref'] = file.meta['source']['ref']
    attached.append(item)


def _attachment(document: dict[str, Any]) -> dict[str, Any]:
    """[Claude] The record `register_attachment` takes, of an `attached-document` element an agent tool recorded."""
    return {
        'source_id': document['file_id'],
        'collection_key': document['collection_key'],
        'job_id': document['job_id'],
        'name': document['filename'],
        'web_url': document.get('web_url'),
        'provider': document['provider'],
        'provider_ref': document['document_ref'],
        'attached_by': 'agent',
        'content_type': document.get('content_type'),
    }


# [Gradient] The most characters of a note or chat the agent takes (its `MAX_CHARACTERS`); `length` says how much
# there was.
TEXT_CHARACTERS = 50_000
# [Gradient] Match quoted attributes too: document titles and tool arguments may contain `>`.
_ATTRIBUTES = r"""((?:[^>"']|"[^"]*"|'[^']*')*)"""
_DETAILS = re.compile(r'<details\b' + _ATTRIBUTES + r'>.*?</details>', re.DOTALL)
_ATTRIBUTE = re.compile(r"""([\w-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")
# [Claude] GLM's tool-call token leaked into a document tag: `<tool_call>document title=...>` (also with `<`).
_TOOL_CALL = '<tool_call>'
_LEAKED_DOCUMENT = re.compile(re.escape(_TOOL_CALL) + r'\s*<?(?=document\b)')


def _unleaked(text: str) -> str:
    """[Claude] `text` with a leaked tool-call token in front of a document tag turned back into `<document`."""
    return _LEAKED_DOCUMENT.sub('<', text)


def _undecided(text: str) -> int:
    """[Claude] Where the tail of streamed answer text starts that may still turn out to be a leaked document tag
    (`<tool_c`, `<tool_call>`, `<tool_call>doc`); its length when there is none."""
    start = text.rfind(_TOOL_CALL)
    if start != -1 and 'document'.startswith(text[start + len(_TOOL_CALL) :].lstrip().removeprefix('<')):
        return start
    for size in range(min(len(_TOOL_CALL) - 1, len(text)), 0, -1):
        if _TOOL_CALL.startswith(text[-size:]):
            return len(text) - size
    return len(text)


@dataclass(frozen=True)
class _Document:
    start: int
    end: int
    attributes: str

    def label(self) -> str:
        attributes = {key: double or single for key, double, single in _ATTRIBUTE.findall(self.attributes)}
        return f'[document: {html.unescape(attributes.get("title", ""))}]'


def _documents(text: str) -> list[_Document]:
    """[Claude] Each closed `<document ...>...</document>` in `text`, found in one pass: a regular expression takes
    quadratic time on many unclosed tags, which a model (or text it quotes) can write. An unclosed tag ends the
    search, since no later tag can close before it does."""
    found, position = [], 0
    while (start := text.find('<document', position)) != -1:
        after = start + len('<document')
        if after < len(text) and (text[after].isalnum() or text[after] in '_-'):
            position = after
            continue
        end, quote = after, ''
        while end < len(text) and (quote or text[end] != '>'):
            if quote and text[end] == quote:
                quote = ''
            elif not quote and text[end] in '"\'':
                quote = text[end]
            end += 1
        close = text.find('</document>', end)
        if end == len(text) or close == -1:
            break
        position = close + len('</document>')
        found.append(_Document(start, position, text[after:end]))
    return found


def _labelled(text: str) -> str:
    """[Claude] `text` with each document replaced by a line naming it."""
    parts, position = [], 0
    for document in _documents(text):
        parts += [text[position : document.start], document.label()]
        position = document.end
    return ''.join(parts) + text[position:]


# [Gradient] A citation marker the agent would read as a number of this turn: `[3]`, `[3, 4]`, `[#3]`, `[3#p2]`,
# `【3】`, `【3†source】`; with the space OWUI puts before it. Code spans and blocks are left alone.
_MARKER_NUMBER = r'#?\d+(?:#[^\]\s]*)?'
_CITATION_MARKER = re.compile(
    r'[ \t]*(?:\[\s*' + _MARKER_NUMBER + r'(?:\s*[,;]\s*' + _MARKER_NUMBER + r')*\s*\]'
    r'|【\s*\d+(?:\s*[,;]\s*\d+)*(?:†[^】]*)?\s*】)'
)
_CODE = re.compile(r'(```.*?(?:```|\Z)|~~~.*?(?:~~~|\Z)|`[^`\n]*`)', re.DOTALL)


def _uncited(text: str) -> str:
    """[Gradient] `text` without citation markers outside code: numbers from another turn or chat, which the
    agent would resolve against this turn's sources. To cite such a document the model reads it again."""
    parts = _CODE.split(text)
    return ''.join(part if index % 2 else _CITATION_MARKER.sub('', part) for index, part in enumerate(parts))


def _conversation(chat: Any, message_id: str | None) -> list[str]:
    """[Gradient] A chat's branch up to a message as `role: content` blocks, without tool, reasoning and document
    bodies or citation markers."""
    blocks = []
    for message in get_message_list(chat.chat.get('history', {}).get('messages', {}), message_id):
        text = _DETAILS.sub('', get_content_from_message(message) or '')
        text = _uncited(_labelled(text)).strip()
        if text:
            blocks.append(f'{message.get("role", "user")}: {text}')
    return blocks


def _chat_text(chat: Any) -> str:
    return '\n\n'.join(_conversation(chat, chat.chat.get('history', {}).get('currentId')))


# [Gradient] A chat from before the agent cutover has no thread: its first agent turn carries the start and the end
# of the conversation so far, as messages kept at each end, inside the message text so the agent needs nothing for it.
EARLIER_KEPT = 6
EARLIER_HEADER = 'Earlier in this conversation:'
EARLIER_FOOTER = '\n\n---\n\n'


def _with_earlier(text: str, blocks: list[str]) -> str:
    """[Gradient] `text` after the first and last `EARLIER_KEPT` blocks, each cut evenly to fit `TEXT_CHARACTERS`."""
    if len(blocks) > 2 * EARLIER_KEPT:
        left_out = f'(... {len(blocks) - 2 * EARLIER_KEPT} messages left out ...)'
        blocks = [*blocks[:EARLIER_KEPT], left_out, *blocks[-EARLIER_KEPT:]]
    overhead = len(EARLIER_HEADER) + len(EARLIER_FOOTER) + 2 * len(blocks)
    share = (TEXT_CHARACTERS - len(text) - overhead) // max(len(blocks), 1)
    if not blocks or share < 200:
        return text
    kept = [block if len(block) <= share else block[: share - 3] + '...' for block in blocks]
    return '\n\n'.join([EARLIER_HEADER, *kept]) + EARLIER_FOOTER + text


async def _earlier(metadata: dict[str, Any]) -> list[str]:
    """[Gradient] The stored conversation up to the message this turn answers; none in a temporary chat."""
    chat_id, parent_id = metadata.get('chat_id'), metadata.get('parent_message_id')
    if not chat_id or not parent_id or is_temporary_chat_id(chat_id):
        return []
    chat = await Chats.get_chat_by_id(chat_id)
    return _conversation(chat, parent_id) if chat is not None else []


async def _drop_chat_files(chat_id: str, ids: list[str]) -> None:
    """[Claude] Remove entries from the chat's own `files`, the selection every next turn sends; the messages that
    attached them keep their record."""
    chat = await Chats.get_chat_by_id(chat_id)
    files = (chat.chat.get('files') or []) if chat is not None else []
    kept = [entry for entry in files if not (isinstance(entry, dict) and entry.get('id') in ids)]
    if len(kept) != len(files):
        await Chats.update_chat_by_id(chat_id, {'files': kept}, touch=False)


async def _texts(
    entries: list[dict[str, Any]], user_id: str, *, origin: str = 'chat'
) -> tuple[list[dict[str, Any]], list[Skipped]]:
    """[Gradient] Read attached notes and chats (`origin` chat), a folder's notes or an assistant's notes and chats with
    the same grants as upstream retrieval; the ones deleted or not readable by the user are left out."""
    if not entries:
        return [], []
    user = await Users.get_user_by_id(user_id)
    texts, unavailable = [], []
    for entry in {(entry['type'], entry.get('id')): entry for entry in entries}.values():
        kind, item_id = entry['type'], entry.get('id')
        if kind == 'meeting':
            # [Gradient] A meeting is read from soev-api as the user and sent as an attached text.
            # The agent's AttachedText knows notes and chats only, so it travels as kind "note"
            # with a `meeting-` id; citations link it back to /meetings.
            try:
                title, text = await meeting_document(user, item_id)
            except MeetingUnavailable:
                unavailable.append(Skipped(kind, 'gone', item_id or '', entry.get('name') or item_id or kind))
                continue
            texts.append(
                {
                    'id': f'note:{MEETING_TEXT_PREFIX}{item_id}',
                    'kind': 'note',
                    'title': title,
                    'text': text[:TEXT_CHARACTERS],
                    'length': len(text),
                }
            )
            continue
        item = await (Notes.get_note_by_id(item_id) if kind == 'note' else Chats.get_chat_by_id(item_id))
        allowed = bool(
            item
            and user
            and (
                item.user_id == user.id
                or (can_bypass_access_control(user) if kind == 'note' else can_access_admin_chats(user))
            )
        )
        if item and user and not allowed:
            allowed = await AccessGrants.has_access(
                user_id=user.id,
                resource_type='note' if kind == 'note' else 'shared_chat',
                resource_id=item.id,
                permission='read',
            )
        if item and user and not allowed and kind == 'chat' and item.folder_id:
            folder = await Folders.get_folder_by_id(item.folder_id)
            allowed = folder and await has_folder_access(user.id, folder, 'read', db=None)
        if not allowed:
            name = entry.get('name') or (item.title if item else None) or item_id or kind
            reason = 'denied' if item else 'gone'
            in_chat, folder_item = origin == 'chat', origin == 'folder'
            unavailable.append(Skipped(kind, reason, item_id or '', name, in_chat=in_chat, folder=folder_item))
            continue
        text = item.data.get('content', {}).get('md', '') if kind == 'note' else _chat_text(item)
        title = item.title or entry.get('name') or ('Notitie' if kind == 'note' else 'Chat')
        texts.append(
            {
                'id': f'{kind}:{item.id}',
                'kind': kind,
                'title': title,
                'text': text[:TEXT_CHARACTERS],
                'length': len(text),
            }
        )
    return texts, unavailable


# [Gradient] The most notes and chats one turn sends; attached ones beyond it refuse the turn.
TEXTS = 10


async def _more_texts(
    folder: list[dict[str, Any]], entries: list[dict[str, Any]], metadata: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[Skipped]]:
    """[Claude] The folder's notes, then the assistant's notes and chats, not attached in the chat too, in the room
    the chat's own texts leave; the rest are skipped, never refusing the turn."""
    taken = {(entry['type'], entry.get('id')) for entry in entries}
    room = max(TEXTS - len(entries), 0)
    assistant = [
        item
        for item in metadata.get('knowledge') or []
        if isinstance(item, dict) and item.get('type') in ('note', 'chat') and isinstance(item.get('id'), str)
    ]
    texts, skipped = [], []
    for items, origin in (([item for item in folder if item['type'] == 'note'], 'folder'), (assistant, 'assistant')):
        fresh = []
        for item in items:
            if (item['type'], item['id']) not in taken:
                taken.add((item['type'], item['id']))
                fresh.append(item)
        read, more = await _texts(fresh[:room], metadata['user_id'], origin=origin)
        texts += read
        skipped += more
        skipped += [
            Skipped(item['type'], 'limit', item['id'], item.get('name'), in_chat=False, folder=origin == 'folder')
            for item in fresh[room:]
        ]
        room = max(room - len(fresh), 0)
    return texts, skipped


def _urls(metadata: dict[str, Any]) -> dict[str, list[str]]:
    """[Gradient] Attached pages reach the agent even when the web search control is off."""
    urls = []
    for entry in metadata.get('files') or []:
        url = entry.get('url')
        if entry.get('type') != 'url' or not isinstance(url, str) or len(url) > 2000 or url in urls:
            continue
        try:
            parsed = urlsplit(url)
            valid = parsed.scheme in ('http', 'https') and parsed.hostname
        except ValueError:
            continue
        if valid:
            urls.append(url)
            if len(urls) == 20:
                break
    return {'urls': urls} if urls else {}


class ImagesUnavailable(Exception):
    def __init__(self, reason: str, name: str = '') -> None:
        super().__init__(reason)
        self.reason = reason
        self.name = name


def _image_data(url: str, name: str) -> bytes:
    match = re.fullmatch(r'data:image/[^;,]+;base64,(.+)', url, re.IGNORECASE)
    if match is None:
        raise ImagesUnavailable('invalid', name)
    try:
        return base64.b64decode(match[1], validate=True)
    except ValueError:
        raise ImagesUnavailable('invalid', name) from None


async def _image_bytes(entry: dict, user: str) -> tuple[bytes, FileModel | None, str]:
    name = entry.get('name') or 'image'
    url = entry.get('url') or ''
    file_id = entry.get('id') or (url if not url.startswith('data:') else None)
    if not file_id:
        return _image_data(url, name), None, name
    file = await Files.get_file_by_id(file_id)
    reader = await identity.user_of(user)
    if file is None or reader is None:
        raise ImagesUnavailable('gone', name)
    if (
        file.user_id != reader.id
        and not can_bypass_access_control(reader)
        and not await has_access_to_file(file.id, 'read', reader)
    ):
        raise ImagesUnavailable('gone', name)
    name = entry.get('name') or (file.meta or {}).get('name') or file.filename
    try:
        path = await asyncio.to_thread(Storage.get_file, file.path)
        body = await asyncio.to_thread(Path(path).read_bytes)
    except OSError:
        raise ImagesUnavailable('gone', name) from None
    return body, file, name


async def _images(metadata: dict[str, Any], user: str) -> list[dict]:
    from open_webui.soev import capabilities

    entries = [
        entry
        for entry in (metadata.get('user_message') or {}).get('files') or []
        if entry.get('type') == 'image' or (entry.get('content_type') or '').startswith('image/')
    ]
    if len(entries) > 4:
        raise ImagesUnavailable('limit')
    if entries and not await capabilities.has(capabilities.IMAGES):
        raise ImagesUnavailable('gone', entries[0].get('name') or 'image')
    images = []
    client = identity.build_client() if entries else None
    for entry in entries:
        body, file, name = await _image_bytes(entry, user)
        meta = (file.meta or {}) if file is not None else {}
        cached = meta.get('soev_image')
        # A shared OWUI file can have an image reference owned by a different caller.
        if (
            isinstance(cached, dict)
            and cached.get('sha256') == hashlib.sha256(body).hexdigest()
            and meta.get('soev_image_user') == user
        ):
            images.append(cached)
            continue
        await identity.ensure_link(user, client)
        try:
            uploaded = await client.post_bytes('/v1/chat/images', body, as_user=user, params={'name': name})
        except SoevApiError as error:
            if error.status in (413, 415):
                raise ImagesUnavailable('size' if error.status == 413 else 'type', name) from None
            raise
        if file is not None:
            await Files.update_file_metadata_by_id(file.id, {'soev_image': uploaded, 'soev_image_user': user})
        images.append(uploaded)
    return images


def _unimaged(error: ImagesUnavailable, language: str | None) -> dict[str, Any]:
    dutch = (language or '').lower().startswith('nl')
    if error.reason == 'limit':
        message = (
            'Je kunt maximaal 4 afbeeldingen per bericht toevoegen. Verwijder afbeeldingen en probeer het opnieuw.'
            if dutch
            else 'You can attach at most 4 images per message. Remove images and try again.'
        )
    else:
        reasons = {
            'size': ('groter dan 10 MiB', 'larger than 10 MiB'),
            'type': ('alleen PNG, JPEG, WebP en GIF zijn toegestaan', 'only PNG, JPEG, WebP and GIF are allowed'),
            'invalid': ('ongeldige afbeeldingsgegevens', 'invalid image data'),
            'gone': ('niet beschikbaar of geen toegang', 'unavailable or access denied'),
        }
        reason = reasons[error.reason][0 if dutch else 1]
        message = (
            f'De assistent kan deze afbeelding niet lezen: {error.name} ({reason}). '
            'Verwijder de afbeelding of vervang deze en verstuur je bericht opnieuw.'
            if dutch
            else f"The assistant can't read this image: {error.name} ({reason}). "
            'Remove or replace it and send your message again.'
        )
    return {'error': {'code': 'images_unavailable', 'message': message}}


def _instructions(metadata: dict[str, Any]) -> dict[str, str]:
    """[Claude] The custom model's prompt and the chat's prompt (Chat Controls or the user's own, plus the folder's),
    each sent only when it says something."""
    fields = {'assistant_instructions': 'system_prompt', 'user_instructions': 'chat_system_prompt'}
    return {field: text for field, key in fields.items() if isinstance(text := metadata.get(key), str) and text.strip()}


def _zone(metadata: dict[str, Any]) -> dict[str, str]:
    """[Claude] The browser's IANA time zone from the request's prompt variables, sent only when zoneinfo knows it."""
    variables = metadata.get('variables')
    name = variables.get('{{CURRENT_TIMEZONE}}') if isinstance(variables, dict) else None
    if not isinstance(name, str) or not name:
        return {}
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return {}
    return {'zone': name}


def _tools(
    metadata: dict[str, Any], web_search_allowed: bool, live_documents_allowed: bool, live_mail_allowed: bool
) -> dict[str, dict[str, str]]:
    """[Gradient] The turn's `tools` field, from the web search control. Uit is sent too, so the deployment's
    default never decides for the user, and so is every turn where OWUI does not allow web search."""
    state = tool_state(metadata.get('features'), 'web_search') if web_search_allowed else 'off'
    documents = (metadata.get('features') or {}).get('live_documents') if live_documents_allowed else 'off'
    if documents not in ('auto', 'required'):
        documents = 'off'
    mail = (metadata.get('features') or {}).get('live_mail') if live_mail_allowed else 'off'
    if mail not in ('auto', 'required'):
        mail = 'off'
    return {
        'tools': {
            'web_search': state,
            'fetch': 'off' if state == 'off' else 'auto',
            'search_live_documents': documents,
            'list_live_folder': documents,
            'attach_live_document': documents,
            'search_mail': mail,
            'read_mail': mail,
        }
    }


async def _live_mail_allowed() -> bool:
    from open_webui.env import AGENT_API_ENABLED
    from open_webui.soev import capabilities

    return (
        AGENT_API_ENABLED
        and bool(await Config.get('live_mail.enable'))
        and await capabilities.has(capabilities.LIVE_MAIL)
    )


async def _live_documents_allowed() -> bool:
    from open_webui.env import AGENT_API_ENABLED
    from open_webui.soev import capabilities

    return (
        AGENT_API_ENABLED
        and bool(await Config.get('live_documents.enable'))
        and await capabilities.has(capabilities.LIVE_DOCUMENTS)
    )


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


async def _documents_allowed(user_id: str) -> bool:
    """Client-supplied document writer flags require tenant access, the admin setting and user permission."""
    if not is_feature_enabled('document_writer') or not await Config.get('document_writer.enable'):
        return False
    user = await Users.get_user_by_id(user_id)
    if user is None:
        return False
    return user.role == 'admin' or await has_permission(
        user.id, 'features.document_writer', await Config.get('user.permissions')
    )


# [Claude] What a notice above the answer calls a skipped item, by language: named, or unnamed.
_SUBJECTS = {
    'en': {
        'knowledge': ("Knowledge base '{name}'", 'A knowledge base'),
        'file': ("File '{name}'", 'A file'),
        'note': ("Note '{name}'", 'A note'),
        'chat': ("Chat '{name}'", 'A chat'),
        'meeting': ("Meeting '{name}'", 'A meeting'),
        'folder': ("Folder '{name}'", 'A folder'),
    },
    'nl': {
        'knowledge': ("Kennisbank '{name}'", 'Een kennisbank'),
        'file': ("Bestand '{name}'", 'Een bestand'),
        'note': ("Notitie '{name}'", 'Een notitie'),
        'chat': ("Chat '{name}'", 'Een chat'),
        'meeting': ("Vergadering '{name}'", 'Een vergadering'),
        'folder': ("Map '{name}'", 'Een map'),
    },
}
_WHY = {
    'en': {
        'gone': '{subject} no longer exists and was removed from this chat.',
        'gone_assistant': '{subject} of this assistant no longer exists and was skipped.',
        'denied': '{subject} is not accessible and was skipped.',
        'failed': '{subject} could not be processed and was skipped.',
        'processing': '{subject} is still being processed and was skipped this time.',
        'unmigrated': '{subject} was uploaded before an update and cannot be read. Upload the file again.',
        'gone_folder': '{subject} no longer exists and was skipped.',
        'limit': '{subject} was skipped: a message reads at most 10 notes or chats.',
    },
    'nl': {
        'gone': '{subject} bestaat niet meer en is uit deze chat gehaald.',
        'gone_assistant': '{subject} van deze assistent bestaat niet meer en is overgeslagen.',
        'denied': '{subject} is niet toegankelijk en is overgeslagen.',
        'failed': '{subject} kon niet worden verwerkt en is overgeslagen.',
        'processing': '{subject} wordt nog verwerkt en is deze keer overgeslagen.',
        'unmigrated': '{subject} is vóór een update geüpload en kan niet worden gelezen. Upload het bestand opnieuw.',
        'gone_folder': '{subject} bestaat niet meer en is overgeslagen.',
        'limit': '{subject} is overgeslagen: een bericht leest maximaal 10 notities of chats.',
    },
}
# [Claude] Where a folder's skipped item comes from, after its subject.
_OF_FOLDER = {'en': ' of this folder', 'nl': ' van deze map'}
# [Claude] Knowledge bases the user cannot see are only counted: their names may themselves be confidential. An
# assistant's or folder's own deleted one cannot be dropped from the chat, so it is counted as skipped.
_COUNTED = {
    'en': {
        ('denied', 'chat'): (
            '{n} knowledge base in this chat is not accessible and was skipped.',
            '{n} knowledge bases in this chat are not accessible and were skipped.',
        ),
        ('denied', 'assistant'): (
            '{n} knowledge base of this assistant is not accessible and was skipped.',
            '{n} knowledge bases of this assistant are not accessible and were skipped.',
        ),
        ('gone', 'assistant'): (
            '{n} knowledge base of this assistant no longer exists and was skipped.',
            '{n} knowledge bases of this assistant no longer exist and were skipped.',
        ),
        ('denied', 'folder'): (
            '{n} knowledge base of this folder is not accessible and was skipped.',
            '{n} knowledge bases of this folder are not accessible and were skipped.',
        ),
        ('gone', 'folder'): (
            '{n} knowledge base of this folder no longer exists and was skipped.',
            '{n} knowledge bases of this folder no longer exist and were skipped.',
        ),
    },
    'nl': {
        ('denied', 'chat'): (
            '{n} kennisbank in deze chat is niet toegankelijk en is overgeslagen.',
            '{n} kennisbanken in deze chat zijn niet toegankelijk en zijn overgeslagen.',
        ),
        ('denied', 'assistant'): (
            '{n} kennisbank van deze assistent is niet toegankelijk en is overgeslagen.',
            '{n} kennisbanken van deze assistent zijn niet toegankelijk en zijn overgeslagen.',
        ),
        ('gone', 'assistant'): (
            '{n} kennisbank van deze assistent bestaat niet meer en is overgeslagen.',
            '{n} kennisbanken van deze assistent bestaan niet meer en zijn overgeslagen.',
        ),
        ('denied', 'folder'): (
            '{n} kennisbank van deze map is niet toegankelijk en is overgeslagen.',
            '{n} kennisbanken van deze map zijn niet toegankelijk en zijn overgeslagen.',
        ),
        ('gone', 'folder'): (
            '{n} kennisbank van deze map bestaat niet meer en is overgeslagen.',
            '{n} kennisbanken van deze map bestaan niet meer en zijn overgeslagen.',
        ),
    },
}


def _origin(item: Skipped) -> str:
    return 'folder' if item.folder else 'chat' if item.in_chat else 'assistant'


def _notices(skipped: list[Skipped], language: str | None) -> list[str]:
    """[Claude] One line per item this turn runs without, in the UI's language, shown above the answer. Names come
    from the chat's own entries; a knowledge base is named only when it was deleted from this chat."""
    lang = 'nl' if (language or '').lower().startswith('nl') else 'en'
    notices = []
    for (reason, origin), templates in _COUNTED[lang].items():
        count = sum(item.kind == 'knowledge' and (item.reason, _origin(item)) == (reason, origin) for item in skipped)
        if count:
            notices.append(templates[count != 1].format(n=count))
    for item in skipped:
        if item.kind == 'knowledge' and (item.reason, item.in_chat) != ('gone', True):
            continue
        named, unnamed = _SUBJECTS[lang][item.kind]
        subject = (named.format(name=item.name) if item.name else unnamed) + (_OF_FOLDER[lang] if item.folder else '')
        reason = f'gone_{_origin(item)}' if item.reason == 'gone' and not item.in_chat else item.reason
        notices.append(_WHY[lang][reason].format(subject=subject))
    return notices


def _skipped_notes(skipped: list[Skipped]) -> list[str]:
    """[Claude] What the agent is told of the items left out, so it does not answer as if it had read them."""
    notes = [
        f'not available, left out: {item.name or item.kind}'
        for item in skipped
        if item.kind != 'knowledge' and item.reason != 'processing'
    ]
    if count := sum(item.kind == 'knowledge' for item in skipped):
        notes.append(f'{count} selected knowledge base{"s" if count != 1 else ""} not available, left out')
    return notes


def _knowledge_keys(metadata: dict[str, Any], folder: list[dict[str, Any]] = ()) -> list[str]:
    """The selected knowledge bases' keys (the chat's, the assistant's, then the folder's); an OWUI knowledge id is
    the soev-api collection key.

    A model's knowledge also lists files and notes, and legacy entries without an id; none is a collection key.
    """
    selected = [item for item in metadata.get('files') or [] if item.get('type') == 'collection']
    selected.extend(
        item
        for item in metadata.get('knowledge') or []
        if isinstance(item, str) or (item.get('type', 'collection') == 'collection' and 'id' in item)
    )
    selected.extend(item for item in folder if item['type'] == 'collection')
    ids = [item if isinstance(item, str) else item['id'] for item in selected]
    if any(not isinstance(key, str) or not key for key in ids):
        raise ValueError('A collection requires an id')
    return list(dict.fromkeys(ids))


def _chunk(delta: dict[str, Any]) -> dict[str, Any]:
    return {'object': 'chat.completion.chunk', 'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]}


_USAGE_KEYS = ('prompt_tokens', 'completion_tokens', 'total_tokens')


def _summed_usage(total: dict[str, int] | None, usage: Any) -> dict[str, int] | None:
    """[Claude] Add one model call's token counts to the turn's; a call without usage adds nothing."""
    if not isinstance(usage, dict):
        return total
    return {key: (total or {}).get(key, 0) + int(usage.get(key) or 0) for key in _USAGE_KEYS}


def _error(
    code: str,
    *,
    constraint: str | None = None,
    model: str | None = None,
    detail: str | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    dutch = (language or '').lower().startswith('nl')
    message = _ERRORS['nl' if dutch else 'en'].get(code) or _ERRORS['nl' if dutch else 'en']['']
    if code == 'invalid_field' and constraint is not None:
        message = _refusal(constraint, model=model, detail=detail, language=language) or message
    return {'error': {'code': code, 'message': message}}


# [Claude] What the user sees when a turn fails, by error code; '' for any other code. Never upstream text.
_ERRORS = {
    'en': {
        'not_found': 'The agent thread is unavailable.',
        'thread_active': 'The agent thread is already running or needs recovery.',
        'invalid_field': 'The agent could not accept this input or collection selection.',
        'service_unavailable': 'The agent service is unavailable. Please try again.',
        'failed': 'The agent stopped because a step failed.',
        'interrupted': 'The agent turn was interrupted and needs recovery.',
        '': 'The agent request failed.',
    },
    'nl': {
        'not_found': 'Dit gesprek met de assistent is niet meer beschikbaar.',
        'thread_active': 'De assistent is nog bezig met dit gesprek of moet eerst worden hersteld.',
        'invalid_field': 'De assistent kon deze invoer of kennisbankselectie niet accepteren.',
        'service_unavailable': 'De assistent is niet beschikbaar. Probeer het opnieuw.',
        'failed': 'De assistent is gestopt omdat een stap mislukte.',
        'interrupted': 'Het antwoord van de assistent is onderbroken en moet worden hersteld.',
        '': 'Het verzoek aan de assistent is mislukt.',
    },
}


def _http_status(code: str, upstream: int | None = None) -> int:
    """[Claude] The HTTP status of a failed turn: the user's own 4xx passes through, else by code, else 502."""
    if upstream in _USER_STATUSES:
        return upstream
    return _CODE_STATUSES.get(code, 502)


# [Gradient] Why the agent refused a turn, by soev-api's `chat:<reason>` constraint. `{model}` is the chosen model;
# `{detail}` is the agent's own words, shown only where they name what to fix (file names, the missing capability).
_REFUSALS = {
    'en': {
        'chat:model': 'The agent could not accept model "{model}". Select another model.',
        'chat:unknown_model': 'The agent could not accept model "{model}". Select another model.',
        'chat:not_chat_model': 'Model "{model}" is not a chat model. Select another model.',
        'chat:model_required': 'The agent received no model for this message. Select a model and send it again.',
        'chat:knowledge_unavailable': (
            'One or more knowledge bases on this assistant or chat are unavailable to you, or no longer exist. '
            'Remove them from the chat and send your message again.'
        ),
        'chat:attachments_unreadable': (
            "The assistant can't read some attached files ({detail}). "
            'Remove them under Controls → Files and send your message again.'
        ),
        'chat:tool_unsupported': (
            'The selected model cannot use a tool this message requires ({detail}). '
            'Select another model or turn the tool off.'
        ),
        'chat:credential_not_live': 'Your access to the assistant has expired. Sign in again and retry.',
    },
    'nl': {
        'chat:model': 'De assistent kon model "{model}" niet gebruiken. Kies een ander model.',
        'chat:unknown_model': 'De assistent kon model "{model}" niet gebruiken. Kies een ander model.',
        'chat:not_chat_model': 'Model "{model}" is geen chatmodel. Kies een ander model.',
        'chat:model_required': (
            'De assistent kreeg geen model mee voor dit bericht. Kies een model en verstuur het opnieuw.'
        ),
        'chat:knowledge_unavailable': (
            'Een of meer kennisbanken bij deze assistent of chat zijn niet voor jou beschikbaar, of bestaan niet meer. '
            'Haal ze uit de chat en verstuur je bericht opnieuw.'
        ),
        'chat:attachments_unreadable': (
            'De assistent kan sommige bijlagen niet lezen ({detail}). '
            'Verwijder ze onder Besturingselementen → Bestanden en verstuur je bericht opnieuw.'
        ),
        'chat:tool_unsupported': (
            'Het gekozen model kan een hulpmiddel dat dit bericht vereist niet gebruiken ({detail}). '
            'Kies een ander model of zet het hulpmiddel uit.'
        ),
        'chat:credential_not_live': 'Je toegang tot de assistent is verlopen. Log opnieuw in en probeer het nog eens.',
    },
}


def _refusal(constraint: str, *, model: str | None, detail: str | None, language: str | None) -> str | None:
    """[Gradient] The localised reason for a refused turn, or None for a constraint without one."""
    template = _REFUSALS['nl' if (language or '').lower().startswith('nl') else 'en'].get(constraint)
    if template is None:
        return None
    if not detail:
        template = template.replace(' ({detail})', '')
    return template.format(model=model, detail=detail)


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


_GRANULARITY = {'document-text': 'document', 'search-result': 'snippet'}


async def _as_source(element: dict[str, Any], whole: dict[str, Any] | None) -> dict[str, Any]:
    """[Claude] A text the agent read (a chunk, an opened document's text) with what its document says about it,
    in the shape `Citations.add` takes. A whole document's text is a document-granularity source, a web search
    result's snippet a snippet-granularity one: the page itself was not read."""
    whole = whole or {}
    properties = {
        'title': whole.get('title') or whole.get('filename'),
        'source_id': whole.get('source_id'),
        'source_url': whole.get('source_url'),
        'page_numbers': element.get('pages'),
        'bboxes': element.get('bboxes'),
        'granularity': _GRANULARITY.get(element.get('type')),
    }
    provider = None
    if element.get('type') in {'mail-text', 'mail-reference'}:
        provider = 'outlook_mail'
    elif isinstance(properties['source_id'], str) and properties['source_id']:
        file = await Files.get_file_by_id(properties['source_id'])
        if file is not None:
            provider = ((file.meta or {}).get('source') or {}).get('provider')
    return {
        'id': element['id'],
        'ref': element['ref'],
        'text': element.get('text') or '',
        **({'provider': provider} if isinstance(provider, str) and provider else {}),
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
            'source': {
                'id': ref,
                'name': name,
                'url': properties.get('source_url') or ref,
                **({'provider': source['provider']} if source.get('provider') else {}),
            },
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


def _citation_marker(before: str, number: int) -> str:
    """[Claude] The marker for `number` after `before`: spaced off the text, but flush after an opening `*`, `_` or
    `~` run, which a following space would stop from opening (CommonMark flanking), so `**[1] text**` stays bold."""
    run = before.rstrip('*_~')
    opens = run != before and (not run or run[-1].isspace() or unicodedata.category(run[-1])[0] in 'PS')
    return f'[{number}]' if opens else f' [{number}]'


def _marked(text: str, markers: list[tuple[int, int]], start: int = 0) -> str:
    """[Claude] `text[start:]` with a marker inserted at each `(position, n)`, positions in code points into `text`;
    a number repeated at one position shows once."""
    shown: set[tuple[int, int]] = set()
    pieces = []
    for at, number in sorted(markers, key=lambda marker: marker[0]):
        if (at, number) in shown or not start <= at <= len(text):
            continue
        shown.add((at, number))
        pieces.append(text[start:at] + _citation_marker(text[:at], number))
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
        # [Claude] The source ids this turn already sent to the message's `sources`.
        self.shown_sources: set[str] = set()
        self.partial = ''
        # [Claude] Streamed answer text not shown yet, because it may still be a leaked document tag.
        self.held = ''
        # [Claude] Markers already shown in the answer being streamed, as (position, number).
        self.streamed: list[tuple[int, int]] = []
        self.streamed_citations = 0
        self.partial_reasoning = ''
        # [Claude] The turn's document writer state, and every text its tools returned.
        self.documents = 'off'
        self.tool_texts: list[str] = []
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
        # [Claude] Calls of the last model output, shown as running once its reasoning and text are out, so a
        # call's status never lands above the thoughts that led to it.
        self.pending_calls: list[dict] = []
        # [Claude] Done lines of calls whose output landed, held until the model thinks again or moves on.
        self.settling: list[dict[str, Any]] = []
        self.model: str | None = None
        # [Claude] The catalog id that answered, once soev-api reports one (a fallback may differ from self.model).
        self.answered_model: str | None = None
        self.attached_files: dict[str, dict] = {}
        self.emitter: Callable[[dict], Awaitable[None]] | None = None
        # [Claude] The lines shown above the answer for the items this turn runs without, and the chat entries
        # dropped because they were deleted.
        self.notices: list[str] = []
        self.removed: list[str] = []
        # [Claude] Whether soev-api accepted the turn, and the HTTP status of its failure, if it failed.
        self.accepted = False
        self.status: int | None = None
        # [Claude] Token counts summed over every model call of the turn, sent once as the stream's usage chunk.
        self.usage: dict[str, int] | None = None

    def fail(self, status: int, code: str, *, upstream: int | None = None, constraint: str | None = None) -> None:
        """[Claude] Keep the failure's HTTP status and log it once: ids and codes only, never content (GRA-219)."""
        self.status = status
        log.log(
            logging.ERROR if status == 500 else logging.WARNING,
            'v2 agent turn failed: chat=%s message=%s thread=%s status=%s upstream=%s code=%s constraint=%s',
            self.metadata.get('chat_id'),
            self.metadata.get('message_id'),
            self.thread_id,
            status,
            upstream,
            code,
            constraint,
        )

    def failed(self, error: Exception) -> dict[str, Any]:
        """[Claude] The error chunk for a turn that raised; soev-api's detail reaches a refusal only on a 4xx."""
        language = self.metadata.get('user_language')
        if not isinstance(error, SoevApiError):
            self.fail(500, type(error).__name__)
            return _error('service_unavailable', language=language)
        self.fail(
            _http_status(error.code, error.status), error.code, upstream=error.status, constraint=error.constraint
        )
        return _error(
            error.code,
            constraint=error.constraint,
            model=self.model,
            detail=error.detail if error.status < 500 else None,
            language=language,
        )

    def left_out(self, skipped: list[Skipped]) -> None:
        self.notices = _notices(skipped, self.metadata.get('user_language'))
        gone = (item.id for item in skipped if item.reason == 'gone' and item.in_chat and item.id)
        self.removed = list(dict.fromkeys(gone))

    async def notify(self) -> None:
        """[Claude] Show the notices on the answer and drop deleted items from the chat, so the next turn does not
        send them again; the frontend drops them from its selection too."""
        if not self.notices:
            return
        chat_id, message_id = self.metadata.get('chat_id'), self.metadata.get('message_id')
        if chat_id and not is_temporary_chat_id(chat_id):
            if self.removed:
                await _drop_chat_files(chat_id, self.removed)
            if message_id:
                await Chats.upsert_message_to_chat_by_id_and_message_id(chat_id, message_id, {'notices': self.notices})
        await self.emit('chat:message:notices', {'notices': self.notices, 'removed': self.removed})

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
        await self._seed_sources(thread['events'], self.position)
        if thread['status']['position'] != self.position:
            branch = await self.client.chat_post(self.path('fork'), {'at': self.position}, as_user=self.as_user)
            self.thread_id = branch['thread_id']

    async def _seed_sources(self, events: list[dict], position: int) -> None:
        for event in events:
            if event['position'] > position or event.get('stream') != 'root':
                continue
            if event['type'] == 'tool_output':
                await self.keep(event['payload'])
            elif event['type'] == 'input':
                self.keep_texts((event['payload'].get('payload') or {}).get('texts') or [])

    def keep_texts(self, texts: list[dict[str, Any]]) -> None:
        # [Gradient] The agent exposes these as DocumentText without a root tool output.
        for entry in texts:
            text_id, text = entry['id'], entry['text']
            route = 'notes' if entry['kind'] == 'note' else 'c'
            url = f'/{route}/{text_id.split(":", 1)[1]}'
            if text_id.startswith(f'note:{MEETING_TEXT_PREFIX}'):
                # [Gradient] A meeting sent as a note text links to its meeting page.
                url = f'/meetings/{text_id.removeprefix(f"note:{MEETING_TEXT_PREFIX}")}'
            self.citations.add(
                {
                    'id': f'{text_id}#0-{len(text)}',
                    'ref': text_id,
                    'text': text,
                    'properties': {'title': entry['title'], 'source_url': url},
                }
            )

    def keep_attachments(self, attachments: list[dict[str, Any]]) -> None:
        """[Claude] Know the document the agent makes of each attached file, `<collection_key>/<file_id>` by its
        filename, so a call opening one names it before any output shows it; a document already seen stays."""
        for item in attachments:
            document_id = f'{item["collection_key"]}/{item["file_id"]}'
            self.elements.setdefault(document_id, {'type': 'document', 'id': document_id, 'filename': item['name']})

    async def keep(self, output: dict[str, Any]) -> list[dict[str, Any]]:
        """[Claude] Remember a tool output's elements, and number each text the agent read; the panel entries of
        the ones not seen before."""
        elements = output.get('elements') or []
        self.elements.update({element['id']: element for element in elements if isinstance(element.get('id'), str)})
        added = []
        for element in elements:
            if _read(element) and element['id'] not in self.citations.sources:
                source = self.citations.add(await _as_source(element, self.elements.get(element['ref'])))
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
                **({'answered_model': self.answered_model} if self.answered_model else {}),
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

    async def start_pending_tools(self) -> list[dict[str, Any]]:
        """[Claude] Show the last model output's calls as running, after its chunks went out."""
        calls, self.pending_calls = self.pending_calls, []
        return await self.start_tools(calls)

    async def start_tools(self, calls: list[dict]) -> list[dict[str, Any]]:
        """Show each call as running and anchor it in the content where it starts: the anchor ends the reasoning
        before it, so the call shows as the latest step while it runs. The frontend replaces a status by a later
        one with the same `call_id`."""
        anchors = []
        for call in calls:
            self.tool_calls += 1
            name, arguments = call['name'], call.get('arguments') or {}
            self.running[call['id']] = (name, arguments)
            status = self.tool_status(name, arguments)
            await self.emit('status', {**status, 'call_id': call['id'], 'done': False})
            anchors.append(_marker(status))
        return anchors

    async def summary(self, event: ChatEvent) -> list[dict[str, Any]]:
        """[Claude] Show the agent summarising the conversation as a call named `compaction`: running from a
        `calling` that produces a `compaction`, done at the next `calling`, when the answer written from the summary
        starts, or when the root stream's `compaction` event lands, whichever comes first. It is not counted as a
        tool call."""
        if event.event == 'calling':
            if event.data.get('produces') != _COMPACTION:
                self.end_tool({'call_id': _COMPACTION})
                return []
            self.running[_COMPACTION] = (_COMPACTION, {})
            status = self.tool_status(_COMPACTION, {})
            await self.emit('status', {**status, 'call_id': _COMPACTION, 'done': False})
            return [_marker(status)]
        if event.data.get('stream') == 'root':
            self.end_tool({'call_id': _COMPACTION})
        return []

    def end_tool(self, output: dict[str, Any]) -> None:
        """[Claude] Hold a call's done line once its output lands, until the model thinks again or moves on (see
        `settle`): until then the model is still working with what the call returned.

        An output with an `error` shows the tool's `failed` line: the agent decides what failed."""
        if (call := self.running.pop(output.get('call_id') or '', None)) is None:
            return
        name, arguments = call
        status = self.tool_status(name, arguments, output)
        self.settling.append({**status, 'call_id': output['call_id'], 'done': True})

    async def settle(self) -> None:
        """[Claude] Show the held done lines: the model thinks again, wrote answer text, called the next tool, or the
        turn ended."""
        settling, self.settling = self.settling, []
        for status in settling:
            await self.emit('status', status)

    def tool_status(self, name: str, arguments: dict, output: dict | None = None) -> dict[str, Any]:
        """The tool's declared `running` status, `done` once there is an `output`, or `failed` when that output is
        an error; else a generic line."""
        # The action names the tool: the frontend lays a turn out as tool activity only for statuses with one.
        phase = 'running' if output is None else 'failed' if output.get('error') is not None else 'done'
        declared = (self.tool_statuses.get(name) or {}).get(phase)
        filled = (
            _filled(declared, self.tool_params(declared, arguments, output)) if isinstance(declared, dict) else None
        )
        status = {'action': name, **filled} if filled else self.generic_status(name, phase, arguments, output)
        if name == 'search_mail':
            status = mail_search_status(status, arguments)
        if items := _web_items(self.touched(arguments, output)):
            status['items'] = items
        return status

    def generic_status(self, name: str, phase: str, arguments: dict, output: dict | None) -> dict[str, Any]:
        """[Claude] The line OWUI knows for a tool, a template the frontend translates: a readable label, naming the
        opened document where it is known."""
        index = _PHASES.index(phase)
        if name in _NAMED and phase != 'failed':
            documents = [element for element in self.touched(arguments, output) if element.get('type') == 'document']
            titles = [title for element in documents if isinstance(title := _field(element, 'title'), str)]
            if titles and titles[0].strip():
                return {'action': name, 'description': _NAMED[name][index], 'title': titles[0]}
        if name in _GENERIC:
            return {'action': name, 'description': _GENERIC[name][index]}
        return {'action': name, 'description': _UNKNOWN[index], 'tool': name}

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
                if isinstance(value, list) and all(isinstance(item, str) for item in value):
                    value = ', '.join(item for item in value if item.strip())
            elif kind == 'knowledge':
                key = arguments.get(rest)
                only = list(self.knowledge_names.values()) if len(self.knowledge_names) == 1 else [None]
                value = self.knowledge_names.get(key) if key else only[0]
            elif kind == 'element':
                argument, _, field = rest.partition('.')
                named = arguments.get(argument)
                names = named if isinstance(named, list) else [named]
                values = [_field(self.elements.get(name) or {}, field) for name in names if isinstance(name, str)]
                value = ', '.join(
                    str(item) for item in values if isinstance(item, str | int | float) and str(item).strip()
                )
            elif kind == 'output' and output is not None:
                value = _from_output(rest, output.get('elements') or [])
            if isinstance(value, str | int | float) and str(value).strip():
                params[param] = str(value)
        return params

    def stop_tools(self) -> None:
        """[Claude] End the calls the tool budget stopped: they never get an output, and the model answers next,
        so each shows its failed line."""
        for call_id in [call_id for call_id in self.running if call_id != _COMPACTION]:
            self.end_tool({'call_id': call_id, 'error': 'budget_exceeded'})

    async def clear_tools(self) -> None:
        """Forget the calls still shown as running: a call that ended without an output shows no done line."""
        self.running.clear()

    async def attached(self, payload: dict) -> None:
        """[Claude] Add the documents a tool output attached to the message's files."""
        files = {}
        documents = [element for element in payload.get('elements') or [] if element.get('type') == _ATTACHED]
        for document in documents:
            try:
                file = await live_documents.register_attachment(self.metadata['user_id'], _attachment(document))
            except live_documents.AttachmentMismatch as error:
                log.warning('Skipping mismatched attachment event (%s)', error.status)
                continue
            files[file.id] = live_documents.chat_file(file)
        if not files:
            return
        chat_id, message_id = self.metadata.get('chat_id'), self.metadata.get('message_id')
        stored = {}
        if chat_id and message_id and not is_temporary_chat_id(chat_id):
            stored = await Chats.get_message_by_id_and_message_id(chat_id, message_id) or {}
        self.attached_files.update({item['id']: item for item in stored.get('files') or [] if item.get('id')})
        self.attached_files.update(files)
        update = {'files': list(self.attached_files.values())}
        if chat_id and message_id and not is_temporary_chat_id(chat_id):
            await Chats.upsert_message_to_chat_by_id_and_message_id(chat_id, message_id, update)
        # The full list, stored above: a `files` event would be appended to the stored files again.
        await self.emit('chat:message:files', update)

    async def record_output(self, payload: dict) -> None:
        """[Claude] Offer every text a root tool output read to the citation panel, and end its call's status."""
        await self.attached(payload)
        await self.keep(payload)
        self.tool_texts += [
            text
            for text in [payload.get('data'), *(element.get('text') for element in payload.get('elements') or [])]
            if isinstance(text, str) and text
        ]
        for element in payload.get('elements') or []:
            if element.get('type') == 'action-required' and element.get('kind') == 'connect':
                await self.emit('action_required', {'kind': 'connect', 'provider': element['provider']})
            if _read(element):
                await self.show_source(element['id'])
        self.end_tool(payload)

    async def show_source(self, source_id: str) -> None:
        """[Claude] Send a source once per turn, when a tool reads it or the answer cites it, so its `[N]` resolves
        while the answer streams. The frontend lists a message's sources from the markers in its text."""
        if source_id in self.shown_sources or source_id not in self.citations.sources:
            return
        self.shown_sources.add(source_id)
        await self.emit('source', self.citations.sources[source_id])

    async def cite(self, citation: dict[str, Any]) -> str:
        """[Claude] The marker a streamed citation adds after the text already sent, once per number there."""
        self.streamed_citations += 1
        number = self.citations.number(citation)
        at = citation.get('at')
        if number is None or not isinstance(at, int) or (at, number) in self.streamed:
            return ''
        await self.show_source(citation['source'])
        self.streamed.append((at, number))
        return self.release() + _citation_marker(self.partial[:at], number)

    def shown(self, text: str) -> str:
        """[Claude] The part of streamed answer text to show now; a tail that may still be a leaked document tag
        waits for the next text."""
        text = _unleaked(self.held + text)
        cut = _undecided(text)
        self.held = text[cut:]
        return text[:cut]

    def release(self) -> str:
        """[Claude] The answer text held back, shown as it is now that no more text joins it."""
        held, self.held = self.held, ''
        return held

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
                if event.event == 'tool_output' and event.data.get('stream') == 'root':
                    await self.attached(event.data['payload'])
                    for source in await self.keep(event.data['payload']):
                        await self.emit('source', source)
                if event.event == 'status' and event.data['state'] not in {*_ENDED, 'waiting'}:
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
                or 'interrupted' not in error.detail
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

    async def answered(self, event: ChatEvent, payload: dict) -> None:
        """[Claude] Note the answering model wherever soev-api reports it; not every relay sends it yet."""
        answered = (payload.get('answered_model') if isinstance(payload, dict) else None) or event.data.get(
            'answered_model'
        )
        if isinstance(answered, str) and answered and answered != self.answered_model:
            self.answered_model = answered
            await self.emit('chat:completion', {'answered_model': answered})

    async def render_event(self, event: ChatEvent) -> list[dict[str, Any]]:
        payload = event.data.get('payload') or {}
        await self.answered(event, payload)
        if event.event == 'input' and event.data.get('stream') == 'root' and self.input_position is None:
            self.input_position = self.position
            await self.persist()
        if event.event == 'reasoning_delta':
            await self.settle()
            self.partial_reasoning += event.data['text']
            return [_chunk({'reasoning_content': event.data['text']})]
        if event.event == 'delta':
            if event.data['text']:
                await self.settle()
            if event.data['text'] and not self.running:
                await self.summarize()
            self.partial += event.data['text']
            text = self.shown(event.data['text'])
            return [_chunk({'content': text})] if text else []
        if event.event == 'citation':
            marker = await self.cite(event.data)
            return [_chunk({'content': marker})] if marker else []
        if event.event == 'model_output':
            self.usage = _summed_usage(self.usage, payload.get('usage'))
        if event.event == 'model_output' and event.data.get('stream') == 'root':
            return await self.model_output(payload)
        if event.event == 'tool_output' and event.data.get('stream') == 'root':
            await self.record_output(payload)
            return []
        if event.event in {'calling', 'compaction'}:
            return await self.summary(event)
        if event.event == 'budget_exceeded' and event.data.get('stream') == 'root':
            self.stop_tools()
            return []
        if event.event == 'failure' and event.data.get('stream') == 'root':
            await self.clear_tools()
        if event.event in {'status', 'error'}:
            return await self.finish(event)
        return []

    async def model_output(self, payload: dict) -> list[dict[str, Any]]:
        await self.settle()
        self.pending_calls = list(payload.get('tool_calls', []))
        reasoning = self.remaining_reasoning(payload.get('reasoning') or '')
        content, partial = payload['content'], self.partial
        citations = (payload.get('citations') or [])[self.streamed_citations :]
        streamed = self.streamed
        self.partial, self.streamed, self.streamed_citations = '', [], 0
        held = self.release()
        if not content.startswith(partial):
            log.warning('Durable model output disagrees with streamed text', extra={'thread_id': self.thread_id})
            return [_chunk({'content': held})] if held else []
        markers = [
            (citation['at'], number)
            for citation in citations
            if (number := self.citations.number(citation)) is not None
            and isinstance(citation.get('at'), int)
            and citation['at'] >= len(partial)
            and (citation['at'], number) not in streamed
        ]
        for citation in citations:
            if self.citations.number(citation) is not None:
                await self.show_source(citation['source'])
        text = _unleaked(held + _marked(content, markers, len(partial)))
        if (document := self.reasoning_document(content, payload.get('reasoning') or '')) is not None:
            text += f'\n\n{document}'
        chunks = []
        if reasoning:
            chunks.append(_chunk({'reasoning_content': reasoning}))
        if text:
            chunks.append(_chunk({'content': text}))
        return chunks

    def reasoning_document(self, content: str, reasoning: str) -> str | None:
        """[Claude] The document a model wrote in its reasoning instead of its answer, for the answer to show: only
        when the user turned the document writer on, the call ends the turn and its answer has none, and only the
        last one, which a tool did not return as it is (text the model quoted from a mail or file is not its own)."""
        if self.documents not in ('auto', 'required') or self.pending_calls or _documents(_unleaked(content)):
            return None
        reasoning = _unleaked(reasoning)
        if not (found := _documents(reasoning)):
            return None
        last = found[-1]
        body = reasoning[last.start + len('<document') + len(last.attributes) + 1 : last.end - len('</document>')]
        return None if any(body.strip() in text for text in self.tool_texts) else reasoning[last.start : last.end]

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
        held = self.release()
        chunks: list[dict[str, Any]] = [_chunk({'content': held})] if held else []
        state = event.data.get('state')
        if state not in _ENDED:
            await self.emit('status', {'description': state or 'error', 'done': True})
        else:
            await self.summarize()
        language = self.metadata.get('user_language')
        if event.event == 'error':
            code, constraint = event.data.get('code', 'service_unavailable'), event.data.get('constraint')
            self.fail(_http_status(code), code, constraint=constraint)
            detail = event.data.get('detail')
            chunks.append(
                _error(
                    code,
                    constraint=constraint,
                    model=self.model,
                    detail=detail if isinstance(detail, str) else None,
                    language=language,
                )
            )
        elif state not in {*_ENDED, 'waiting'}:
            self.fail(_http_status(state or 'service_unavailable'), state or 'service_unavailable')
            chunks.append(_error(state or 'service_unavailable', language=language))
        if self.usage:
            # [Claude] The OpenAI terminal usage chunk, which the middleware stores on the message as v1 does.
            chunks.append({'object': 'chat.completion.chunk', 'choices': [], 'usage': self.usage})
            self.usage = None
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
        self.documents = body.get('input', {}).get('documents', 'off')
        try:
            self.emitter = await get_event_emitter(self.metadata)
            await self.notify()
            await identity.ensure_link(self.as_user, self.client)
            self.tool_statuses = await _tool_statuses(self.client)
            self.knowledge_names = {entry['key']: entry['name'] for entry in body['input'].get('knowledge') or []}
            await self.prepare()
            self.keep_attachments(body['input'].get('attachments') or [])
            if not self.thread_id and (earlier := await _earlier(self.metadata)):
                body = {**body, 'input': {**body['input'], 'text': _with_earlier(body['input']['text'], earlier)}}
            self.keep_texts(body['input'].get('texts') or [])
            # Earlier turns' sources and this turn's attached texts, so their numbers resolve, sent in number order
            # before any tool output numbers more; the panel lists only the ones this answer cites.
            for source_id in self.citations.sources:
                await self.show_source(source_id)
            if not self.thread_id:
                body = {**body, 'agent': agent}
            events = self.events(body)
            async with aclosing(events):
                async for event in events:
                    for chunk in await self.render(event):
                        yield chunk
                    if event.event == 'connection' and not self.accepted:
                        self.accepted = True
                        yield _ACCEPTED
                    for chunk in await self.start_pending_tools():
                        yield chunk
        except (asyncio.CancelledError, GeneratorExit):
            await self.cancel()
            raise
        except Exception as error:
            await self.cancel()
            yield self.failed(error)


async def call_agent_v2(
    form_data: dict[str, Any], metadata: dict[str, Any], *, agent: str | None, model: str | None = None
) -> StreamingResponse | dict:
    """Submit one user message; the thread owns the conversation history."""
    user_ref = acting.acting_ref() or f'owui:user:{metadata["user_id"]}'
    turn = AgentTurn(identity.build_client(), metadata, user_ref)
    text = _input_text(metadata, form_data)
    try:
        chunks = await _sent(turn, text, metadata, agent=agent, model=model)
    except Exception as error:
        chunks = _refused(turn.failed(error))
    early = await _opened(chunks)
    if isinstance(early, dict):
        return JSONResponse(early, status_code=turn.status or 502)
    if not form_data.get('stream', True):
        message = {'role': 'assistant', 'content': '', 'reasoning_content': ''}
        usage: dict[str, int] | None = None
        async with aclosing(chunks):
            rest = [chunk async for chunk in chunks]
        for chunk in early + rest:
            if 'error' in chunk:
                return JSONResponse(chunk, status_code=turn.status or 502)
            usage = chunk.get('usage') or usage
            for choice in chunk['choices'][:1]:
                for key, value in choice['delta'].items():
                    message[key] += value
        return {
            'object': 'chat.completion',
            'choices': [{'index': 0, 'message': message, 'finish_reason': 'stop'}],
            **({'usage': usage} if usage else {}),
        }

    async def stream_body() -> AsyncIterator[str]:
        async with aclosing(chunks):
            for chunk in early:
                yield f'data: {json.dumps(chunk)}\n\n'
            async for chunk in chunks:
                yield f'data: {json.dumps(chunk)}\n\n'
        yield 'data: [DONE]\n\n'

    return StreamingResponse(stream_body(), media_type='text/event-stream')


async def _opened(chunks: AsyncIterator[dict[str, Any]]) -> list[dict[str, Any]] | dict[str, Any]:
    """[Claude] The chunks before soev-api accepted the turn, or the error chunk that ended it before then."""
    early: list[dict[str, Any]] = []
    async for chunk in chunks:
        if chunk is _ACCEPTED:
            return early
        if 'error' in chunk:
            await chunks.aclose()
            return chunk
        early.append(chunk)
    return early


async def _sent(
    turn: AgentTurn, text: str, metadata: dict[str, Any], *, agent: str | None, model: str | None
) -> AsyncIterator[dict[str, Any]]:
    """[Claude] The turn's chunks: run with its input, or refused before anything is sent."""
    entries = [entry for entry in metadata.get('files') or [] if entry.get('type') in ('note', 'chat', 'meeting')]
    if len(entries) > TEXTS:
        message = (
            'Voeg maximaal 10 notities of chats toe.'
            if (metadata.get('user_language') or '').lower().startswith('nl')
            else 'Attach at most 10 notes or chats.'
        )
        turn.fail(422, 'attachments_unavailable')
        return _refused({'error': {'code': 'attachments_unavailable', 'message': message}})
    try:
        folder, skipped_folders = await _folder_items(metadata)
        knowledge, skipped = await _knowledge(metadata, folder)
        attachments = await _attachments(metadata, folder)
        texts, skipped_texts = await _texts(entries, metadata['user_id'])
        more_texts, skipped_more_texts = await _more_texts(folder, entries, metadata)
        images = await _images(metadata, turn.as_user)
    except ImagesUnavailable as unavailable:
        turn.fail({'size': 413, 'type': 415}.get(unavailable.reason, 422), 'images_unavailable')
        return _refused(_unimaged(unavailable, metadata.get('user_language')))
    texts += more_texts
    skipped = skipped_folders + skipped + attachments.pop('skipped', []) + skipped_texts + skipped_more_texts
    if skipped:
        turn.left_out(skipped)
    tools = _tools(
        metadata,
        await _web_search_allowed(metadata['user_id']),
        await _live_documents_allowed(),
        await _live_mail_allowed(),
    )
    notes = attachments.pop('attachment_notes', []) + _skipped_notes(skipped)
    collection = {}
    references = any(entry.get('attached_by') or entry.get('source') for entry in metadata.get('files') or [])
    if tools['tools']['search_live_documents'] != 'off' or references:
        try:
            collection['attachment_collection'] = await live_documents.attachment_collection(
                metadata['user_id'], turn.client
            )
        except Exception:
            log.warning('Live document collection unavailable this turn', exc_info=False)
            notes.append('live documents unavailable this turn')
            tools['tools'].update(search_live_documents='off', list_live_folder='off', attach_live_document='off')
    if notes:
        text += '\n\nAttachment status:\n' + '\n'.join(notes)
    documents = (
        tool_state(metadata.get('features'), 'document_writer')
        if await _documents_allowed(metadata['user_id'])
        else 'off'
    )
    body = {
        'input': {
            'text': text,
            'knowledge': knowledge,
            **collection,
            **attachments,
            **_urls(metadata),
            **_instructions(metadata),
            **_zone(metadata),
            **tools,
            'documents': documents,
        }
    }
    if images:
        body['input']['images'] = images
    if texts:
        body['input']['texts'] = texts
    if isinstance(model, str) and model:
        body['model'] = model
    return turn.run(body, agent)


async def _refused(error: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    """A turn refused before anything is sent, shown like any agent error."""
    yield error
