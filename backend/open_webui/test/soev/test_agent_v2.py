"""Exercise v2 routing and branching against the authenticated fake relay."""

import asyncio
import base64
import copy
import hashlib
import html
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from open_webui import env
from open_webui.models.agent_configs import AgentConfigs
from open_webui.models.chat_messages import ChatMessages
from open_webui.models.chats import ChatForm, Chats
from open_webui.socket import main as socket_main
from open_webui.soev import agent_threads, ingest
from open_webui.soev.client import ChatEvent, SoevApiError, SoevClient
from open_webui.test.soev.fake_api import FakeSoevApi
from open_webui.utils import agent, agent_v2
from starlette.responses import StreamingResponse

# The turn's tools field while the web search control is Uit (or absent).
WEB_SEARCH_OFF = {
    'web_search': 'off',
    'fetch': 'off',
    'search_live_documents': 'off',
    'list_live_folder': 'off',
    'attach_live_document': 'off',
    'search_mail': 'off',
    'read_mail': 'off',
}


async def render(turn: agent_v2.AgentTurn, event: ChatEvent) -> list[dict]:
    """Render one event as the stream does: its chunks, then the anchors of the calls it started."""
    return await turn.render(event) + await turn.start_pending_tools()


@dataclass
class Chat:
    api: FakeSoevApi
    messages: dict[tuple[str, str], dict] = field(default_factory=dict)
    socket: list[dict] = field(default_factory=list)

    async def response(
        self,
        text: Any,
        message_id: str,
        parent: str | None = None,
        *,
        chat_id: str = 'chat',
        stream: bool = True,
        form_data: dict[str, Any] | None = None,
        override_agent: str | None = 'test',
        **metadata: Any,
    ) -> StreamingResponse | dict:
        async with asyncio.timeout(8):
            return await agent.call_agent_api(
                None,
                {
                    'model': 'custom',
                    'stream': stream,
                    'messages': [
                        {'role': 'system', 'content': 'HISTORY MUST NOT LEAVE'},
                        {'role': 'user', 'content': 'wrong input'},
                    ],
                    **(form_data or {}),
                },
                {
                    'chat_id': chat_id,
                    'message_id': message_id,
                    'user_message_id': 'user-' + message_id,
                    'user_message': {'id': 'user-' + message_id, 'content': text},
                    'parent_message_id': parent,
                    'user_id': 'alice',
                    'model': {'info': {'base_model_id': 'llm'}},
                    **metadata,
                },
                {},
                override_agent=override_agent,
            )

    async def turn(self, text: Any, message_id: str, parent: str | None = None, **kwargs: Any) -> list[dict]:
        response = await self.response(text, message_id, parent, **kwargs)
        assert isinstance(response, StreamingResponse)
        async with asyncio.timeout(8):
            raw = ''.join([part async for part in response.body_iterator])
        assert raw.endswith('data: [DONE]\n\n')
        return [
            json.loads(line[6:]) for line in raw.splitlines() if line.startswith('data: ') and line != 'data: [DONE]'
        ]

    def bookmark(self, message_id: str, chat_id: str = 'chat') -> dict:
        return self.messages[chat_id, message_id]['meta']['agent_v2']

    def mutations(self) -> list[tuple[str, dict | None]]:
        return [
            (request.url.path, json.loads(request.content) if request.content else None)
            for request in self.api.chat.requests
            if request.method == 'POST'
        ]


@pytest.fixture
def chat(chat_http: FakeSoevApi, monkeypatch: pytest.MonkeyPatch) -> Chat:
    result = Chat(chat_http)
    monkeypatch.setattr(env, 'AGENT_API_RUNTIME', 'v1')

    async def get(chat_id: str, message_id: str) -> dict | None:
        return copy.deepcopy(result.messages.get((chat_id, message_id)))

    async def upsert(chat_id: str, message_id: str, updates: dict) -> None:
        result.messages.setdefault((chat_id, message_id), {}).update(copy.deepcopy(updates))

    async def emit(event: dict) -> None:
        result.socket.append(copy.deepcopy(event))

    monkeypatch.setattr(Chats, 'get_message_by_id_and_message_id', get)
    monkeypatch.setattr(Chats, 'upsert_message_to_chat_by_id_and_message_id', upsert)
    monkeypatch.setattr(
        AgentConfigs, 'get_agent_config_by_id', AsyncMock(return_value=SimpleNamespace(meta={'runtime': 'v2'}))
    )
    monkeypatch.setattr(agent_v2, 'get_event_emitter', AsyncMock(return_value=emit))
    monkeypatch.setattr(agent_v2, '_web_search_allowed', AsyncMock(return_value=True))
    monkeypatch.setattr(agent_v2, '_documents_allowed', AsyncMock(return_value=True))
    # [Gradient] Let this suite exercise the attached-text/page contract without changing the shared relay fake.
    refusal = result.api.chat._refusal

    def with_attachments(body: dict, *, opening: bool) -> httpx.Response | None:
        turn = body.get('input')
        if isinstance(turn, dict):
            body = {**body, 'input': {key: value for key, value in turn.items() if key not in ('texts', 'urls')}}
        return refusal(body, opening=opening)

    monkeypatch.setattr(result.api.chat, '_refusal', with_attachments)
    return result


def content(chunks: list[dict], key: str = 'content') -> str:
    return ''.join(chunk['choices'][0]['delta'].get(key, '') for chunk in chunks if 'choices' in chunk)


@pytest.mark.asyncio
async def test_third_turn_sends_only_the_new_input(chat: Chat) -> None:
    for index in range(1, 4):
        await chat.turn(f'turn {index}', f'a{index}', f'a{index - 1}' if index > 1 else None)
    assert chat.mutations() == [
        (
            '/v1/chat/threads',
            {
                'input': {'text': 'turn 1', 'knowledge': [], 'documents': 'off', 'tools': WEB_SEARCH_OFF},
                'agent': 'test',
                'model': 'llm',
            },
        ),
        (
            '/v1/chat/threads/thr-1/inputs',
            {'input': {'text': 'turn 2', 'knowledge': [], 'documents': 'off', 'tools': WEB_SEARCH_OFF}, 'model': 'llm'},
        ),
        (
            '/v1/chat/threads/thr-1/inputs',
            {'input': {'text': 'turn 3', 'knowledge': [], 'documents': 'off', 'tools': WEB_SEARCH_OFF}, 'model': 'llm'},
        ),
    ]
    assert [chat.bookmark(f'a{i}') for i in range(1, 4)] == [
        {'thread_id': 'thr-1', 'position': position} for position in (3, 5, 7)
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'operation,parent,text,at',
    [
        ('regenerate', 'a2', 'third', 5),
        ('edit', 'a1', 'edited second', 3),
        ('copy', 'a1', 'copied second', 3),
        ('switch', 'a2', 'other third', 5),
    ],
)
async def test_branch_rule_covers_regenerate_edit_copy_and_switch(
    chat: Chat, operation: str, parent: str, text: str, at: int
) -> None:
    await chat.turn('first', 'a1')
    await chat.turn('second', 'a2', 'a1')
    await chat.turn('third', 'a3', 'a2')
    chat_id = 'copy' if operation == 'copy' else 'chat'
    if operation == 'copy':
        chat.messages[chat_id, parent] = copy.deepcopy(chat.messages['chat', parent])
    original = copy.deepcopy(chat.api.chat.threads['thr-1']['events'])
    await chat.turn(text, 'new-answer', parent, chat_id=chat_id)
    assert chat.mutations()[-2:] == [
        ('/v1/chat/threads/thr-1/fork', {'at': at}),
        (
            '/v1/chat/threads/thr-2/inputs',
            {'input': {'text': text, 'knowledge': [], 'documents': 'off', 'tools': WEB_SEARCH_OFF}, 'model': 'llm'},
        ),
    ]
    assert chat.api.chat.threads['thr-1']['events'] == original
    assert chat.bookmark('new-answer', chat_id) == {'thread_id': 'thr-2', 'position': at + 2}
    inputs = [
        event['payload']['payload']['text']
        for event in chat.api.chat.threads['thr-2']['events']
        if event['type'] == 'input'
    ]
    assert inputs == (['first', 'second', text] if at == 5 else ['first', text])


@pytest.mark.asyncio
async def test_copy_at_head_uses_inputs_and_original_then_forks(chat: Chat) -> None:
    await chat.turn('first', 'a1')
    chat.messages['copy', 'a1'] = copy.deepcopy(chat.messages['chat', 'a1'])
    await chat.turn('copy continuation', 'a2', 'a1', chat_id='copy')
    assert chat.mutations()[-1][0] == '/v1/chat/threads/thr-1/inputs'
    await chat.turn('original continuation', 'a2', 'a1')
    assert chat.mutations()[-2] == ('/v1/chat/threads/thr-1/fork', {'at': 3})


@pytest.mark.asyncio
async def test_first_turn_regenerate_opens_another_thread(chat: Chat) -> None:
    await chat.turn('first', 'a1')
    await chat.turn('first', 'regenerated')
    assert [path for path, _ in chat.mutations()] == ['/v1/chat/threads', '/v1/chat/threads']
    assert chat.bookmark('regenerated')['thread_id'] == 'thr-2'


def seed_collection(api: FakeSoevApi, key: str, name: str, description: str | None = None) -> None:
    api.collections[key] = {
        'key': key,
        'name': name,
        'description': description,
        'visibility': 'public',
        'principals': [],
        'writers': [],
        'created_at': api.now,
        'updated_at': api.now,
    }


@pytest.mark.asyncio
async def test_one_text_input_and_the_selected_knowledge_by_its_current_name(chat: Chat) -> None:
    seed_collection(chat.api, 'kb-a', 'Contracten', 'Getekende contracten')
    seed_collection(chat.api, 'kb-b', 'Notulen')
    await chat.turn(
        [
            {'type': 'text', 'text': 'one'},
            {'type': 'image_url', 'image_url': {'url': 'private image'}},
            {'type': 'text', 'text': 'two'},
        ],
        'a1',
        files=[{'type': 'collection', 'id': 'kb-a', 'name': 'Name when picked'}],
        knowledge=[{'id': 'kb-a'}, {'id': 'kb-b'}],
    )
    assert chat.mutations()[0][1] == {
        'input': {
            'text': 'one\ntwo',
            'knowledge': [
                {'key': 'kb-a', 'name': 'Contracten', 'description': 'Getekende contracten'},
                {'key': 'kb-b', 'name': 'Notulen'},
            ],
            'documents': 'off',
            'tools': WEB_SEARCH_OFF,
        },
        'agent': 'test',
        'model': 'llm',
    }


@pytest.mark.asyncio
async def test_a_models_files_notes_and_legacy_entries_are_not_knowledge(chat: Chat) -> None:
    seed_collection(chat.api, 'kb-a', 'Contracten')
    await chat.turn(
        'question',
        'a1',
        knowledge=[
            {'id': 'kb-a', 'type': 'collection'},
            {'id': 'file-1', 'type': 'file'},
            {'id': 'note-1', 'type': 'note'},
            {'collection_name': 'legacy-chroma'},
        ],
    )
    assert chat.mutations()[-1][1]['input']['knowledge'] == [{'key': 'kb-a', 'name': 'Contracten'}]


def seed_hidden_collection(api: FakeSoevApi, key: str, name: str) -> None:
    """A knowledge base that exists, readable by OWUI's service principal but not by the user."""
    seed_collection(api, key, name)
    api.collections[key].update(visibility='restricted', principals=['owui:service:webui'])


def notices(chat: Chat) -> list[dict]:
    """The turn's notices events: the lines above the answer and the chat entries dropped."""
    return [event['data'] for event in chat.socket if event['type'] == 'chat:message:notices']


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'language,expected',
    [
        (
            'nl-NL',
            [
                '2 kennisbanken in deze chat zijn niet toegankelijk en zijn overgeslagen.',
                "Kennisbank 'Oud' bestaat niet meer en is uit deze chat gehaald.",
            ],
        ),
        (
            'en-US',
            [
                '2 knowledge bases in this chat are not accessible and were skipped.',
                "Knowledge base 'Oud' no longer exists and was removed from this chat.",
            ],
        ),
    ],
)
async def test_unavailable_knowledge_is_left_out_with_a_notice_naming_only_the_deleted(
    chat: Chat, language: str, expected: list[str]
) -> None:
    seed_collection(chat.api, 'kb-a', 'Contracten')
    for key in ('kb-secret', 'kb-private'):
        seed_hidden_collection(chat.api, key, 'Integriteitsonderzoek')
    chunks = await chat.turn(
        'next',
        'a1',
        files=[
            {'type': 'collection', 'id': 'kb-a'},
            {'type': 'collection', 'id': 'kb-secret', 'name': 'Integriteitsonderzoek'},
            {'type': 'collection', 'id': 'kb-private', 'name': 'Integriteitsonderzoek'},
            {'type': 'collection', 'id': 'kb-gone', 'name': 'Oud'},
        ],
        user_language=language,
    )
    assert not any('error' in chunk for chunk in chunks)
    sent = chat.mutations()[-1][1]['input']
    assert sent['knowledge'] == [{'key': 'kb-a', 'name': 'Contracten'}]
    assert sent['text'] == 'next\n\nAttachment status:\n3 selected knowledge bases not available, left out'
    assert notices(chat) == [{'notices': expected, 'removed': ['kb-gone']}]
    assert chat.messages['chat', 'a1']['notices'] == expected
    assert not any('Integriteitsonderzoek' in line or 'kb-' in line for line in expected)


@pytest.mark.asyncio
async def test_an_assistants_own_unavailable_knowledge_is_counted_and_stays(chat: Chat) -> None:
    seed_collection(chat.api, 'kb-a', 'Contracten')
    seed_hidden_collection(chat.api, 'kb-secret', 'Geheim')
    await chat.turn('next', 'a1', knowledge=[{'id': 'kb-a'}, {'id': 'kb-secret'}, {'id': 'kb-gone'}])
    assert chat.mutations()[-1][1]['input']['knowledge'] == [{'key': 'kb-a', 'name': 'Contracten'}]
    assert notices(chat) == [
        {
            'notices': [
                '1 knowledge base of this assistant is not accessible and was skipped.',
                '1 knowledge base of this assistant no longer exists and was skipped.',
            ],
            'removed': [],
        }
    ]


@pytest.mark.asyncio
async def test_a_deleted_item_is_dropped_from_the_chats_selection(chat: Chat, monkeypatch: pytest.MonkeyPatch) -> None:
    selection = [
        {'type': 'collection', 'id': 'kb-gone', 'name': 'Oud'},
        {'type': 'collection', 'id': 'kb-secret', 'name': 'Geheim'},
        {'type': 'file', 'id': 'kept'},
    ]
    stored = SimpleNamespace(chat={'files': selection})
    monkeypatch.setattr(Chats, 'get_chat_by_id', AsyncMock(return_value=stored))
    monkeypatch.setattr(Chats, 'update_chat_by_id', AsyncMock())
    seed_hidden_collection(chat.api, 'kb-secret', 'Geheim')
    await chat.turn('next', 'a1', files=selection[:2])
    Chats.update_chat_by_id.assert_awaited_once_with('chat', {'files': selection[1:]}, touch=False)


@pytest.mark.asyncio
async def test_a_temporary_chat_only_shows_its_notices(chat: Chat, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket_main, 'SESSION_POOL', {'sid-1': {}})
    monkeypatch.setattr(socket_main, 'TEMPORARY_AGENT_THREADS', {})
    monkeypatch.setattr(Chats, 'update_chat_by_id', AsyncMock())
    await chat.turn('next', 'a1', files=[{'type': 'collection', 'id': 'kb-gone'}], chat_id='temporary:sid-1:chat')
    Chats.update_chat_by_id.assert_not_awaited()
    assert not chat.messages
    assert notices(chat)[0]['removed'] == ['kb-gone']


@pytest.mark.asyncio
async def test_a_turn_without_unavailable_items_shows_no_notice(chat: Chat) -> None:
    seed_collection(chat.api, 'kb-a', 'Contracten')
    await chat.turn('next', 'a1', files=[{'type': 'collection', 'id': 'kb-a'}])
    assert notices(chat) == []
    assert 'notices' not in chat.messages['chat', 'a1']


def stored_files(monkeypatch: pytest.MonkeyPatch, **statuses: str | None) -> None:
    """File records by id, owned by alice, each with its processing status."""
    records = {
        file_id: SimpleNamespace(id=file_id, user_id='alice', filename=f'{file_id}.pdf', meta={'status': status})
        for file_id, status in statuses.items()
    }
    monkeypatch.setattr(agent_v2.Files, 'get_file_by_id', AsyncMock(side_effect=records.get))


@pytest.mark.asyncio
async def test_attached_files_are_sent_with_their_collection_and_name(
    chat: Chat, monkeypatch: pytest.MonkeyPatch
) -> None:
    stored_files(monkeypatch, f1='completed', f2='completed', f3=None)
    await chat.turn(
        'question',
        'a1',
        files=[
            {'type': 'file', 'id': 'f1', 'name': 'rapport.pdf', 'collection_name': 'owui-attachments-alice'},
            {'type': 'file', 'id': 'f2', 'name': 'besluit.docx', 'collection_name': ''},
            {'type': 'file', 'id': 'f3', 'name': 'oud.txt'},
            {'type': 'file', 'id': 'f1', 'name': 'rapport.pdf', 'collection_name': 'owui-attachments-alice'},
        ],
    )
    attachments_key = ingest.attachments_collection_key('alice')
    assert chat.mutations()[-1][1]['input']['attachments'] == [
        {'collection_key': 'owui-attachments-alice', 'file_id': 'f1', 'name': 'rapport.pdf'},
        {'collection_key': attachments_key, 'file_id': 'f2', 'name': 'besluit.docx'},
        {'collection_key': attachments_key, 'file_id': 'f3', 'name': 'oud.txt'},
    ]


@pytest.mark.asyncio
async def test_a_file_picked_from_a_knowledge_base_is_sent_as_that_kb_document(
    chat: Chat, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_collection(chat.api, 'kb-cloud', 'OneDrive')
    seed_collection(chat.api, 'kb-local', 'Contracten')
    chat.api.add_document('kb-cloud', 'onedrive-item-1', filename='202508.pdf', schedule_ids=['sch-1'])
    chat.api.add_document('kb-local', 'f-local', filename='contract.pdf')
    # The synced document has no OWUI File row; the local one keeps its upload's row, without a collection.
    stored_files(monkeypatch, **{'f-local': 'completed'})
    chunks = await chat.turn(
        'question',
        'a1',
        files=[
            {'type': 'file', 'id': 'onedrive-item-1', 'name': '202508.pdf', 'knowledge_id': 'kb-cloud'},
            {'type': 'file', 'id': 'f-local', 'name': 'contract.pdf', 'knowledge_id': 'kb-local'},
        ],
    )
    assert not any('error' in chunk for chunk in chunks)
    assert chat.mutations()[-1][1]['input']['attachments'] == [
        {'collection_key': 'kb-cloud', 'file_id': 'onedrive-item-1', 'name': '202508.pdf'},
        {'collection_key': 'kb-local', 'file_id': 'f-local', 'name': 'contract.pdf'},
    ]


@pytest.mark.asyncio
async def test_a_knowledge_base_file_the_user_cannot_read_there_is_left_out_with_a_notice(
    chat: Chat, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_collection(chat.api, 'kb-a', 'Contracten')
    seed_hidden_collection(chat.api, 'kb-secret', 'Geheim')
    chat.api.add_document('kb-a', 'elsewhere', filename='elders.pdf')
    chat.api.add_document('kb-a', 'ok', filename='goed.pdf')
    chat.api.add_document('kb-secret', 'hidden', filename='verborgen.pdf')
    stored_files(monkeypatch, busy='processing')
    chunks = await chat.turn(
        'question',
        'a1',
        files=[
            {'type': 'file', 'id': 'ok', 'name': 'goed.pdf', 'knowledge_id': 'kb-a'},
            {'type': 'file', 'id': 'removed', 'name': 'weg.pdf', 'knowledge_id': 'kb-a'},
            {'type': 'file', 'id': 'elsewhere', 'name': 'elders.pdf', 'knowledge_id': 'kb-gone'},
            {'type': 'file', 'id': 'busy', 'name': 'bezig.pdf', 'knowledge_id': 'kb-a'},
            {'type': 'file', 'id': 'hidden', 'name': 'verborgen.pdf', 'knowledge_id': 'kb-secret'},
        ],
        user_language='nl-NL',
    )
    assert not any('error' in chunk for chunk in chunks)
    sent = chat.mutations()[-1][1]['input']
    assert sent['attachments'] == [{'collection_key': 'kb-a', 'file_id': 'ok', 'name': 'goed.pdf'}]
    assert notices(chat) == [
        {
            'notices': [
                "Bestand 'weg.pdf' bestaat niet meer en is uit deze chat gehaald.",
                "Bestand 'elders.pdf' bestaat niet meer en is uit deze chat gehaald.",
                "Bestand 'bezig.pdf' wordt nog verwerkt en is deze keer overgeslagen.",
                "Bestand 'verborgen.pdf' is niet toegankelijk en is overgeslagen.",
            ],
            'removed': ['removed', 'elsewhere'],
        }
    ]
    assert sent['text'].splitlines()[2:] == [
        'Attachment status:',
        'still processing: bezig.pdf',
        'not available, left out: weg.pdf',
        'not available, left out: elders.pdf',
        'not available, left out: verborgen.pdf',
    ]


@pytest.mark.asyncio
async def test_attached_images_are_not_attachments(chat: Chat, monkeypatch: pytest.MonkeyPatch) -> None:
    stored_files(monkeypatch, img='completed')
    await chat.turn('question', 'a1', files=[{'type': 'file', 'id': 'img', 'content_type': 'image/png'}])
    assert 'attachments' not in chat.mutations()[-1][1]['input']
    assert 'images' not in chat.mutations()[-1][1]['input']


@pytest.mark.asyncio
@pytest.mark.parametrize('allowed', [True, False])
@pytest.mark.parametrize('enabled', [True, False])
async def test_attached_urls_are_filtered_and_sent_even_without_web_search(
    chat: Chat, monkeypatch: pytest.MonkeyPatch, allowed: bool, enabled: bool
) -> None:
    monkeypatch.setattr(agent_v2, '_web_search_allowed', AsyncMock(return_value=allowed))
    longest = 'https://example.org/' + 'a' * 1980
    assert len(longest) == 2000
    urls = ['https://example.org/page', 'http://example.org/other', longest]
    chunks = await chat.turn(
        'read these',
        'a1',
        features={'web_search': enabled},
        files=[
            {'type': 'url', 'url': url, 'name': 'Page'}
            for url in [
                None,
                '',
                'ftp://example.org',
                'javascript:alert(1)',
                '/relative',
                'https://',
                'https://[invalid',
                longest + 'a',
                *urls,
                urls[0],
            ]
        ]
        + [{'type': 'text', 'url': 'https://example.org/ingested'}],
    )
    assert not any('error' in chunk for chunk in chunks)
    sent = chat.mutations()[-1][1]['input']
    assert sent['urls'] == urls
    expected = {**WEB_SEARCH_OFF, 'web_search': 'auto', 'fetch': 'auto'} if allowed and enabled else WEB_SEARCH_OFF
    assert sent['tools'] == expected


@pytest.mark.asyncio
async def test_attached_urls_stop_at_twenty_distinct_pages(chat: Chat) -> None:
    urls = [f'https://example.org/{index}' for index in range(21)]
    await chat.turn('read', 'a1', files=[{'type': 'url', 'url': url} for url in [urls[0], *urls]])
    assert chat.mutations()[-1][1]['input']['urls'] == urls[:20]


@pytest.mark.asyncio
async def test_empty_attachment_fields_are_omitted(chat: Chat) -> None:
    await chat.turn('read', 'a1', files=[{'type': 'url', 'url': 'file:///tmp/private'}])
    sent = chat.mutations()[-1][1]['input']
    assert 'urls' not in sent
    assert 'texts' not in sent


@pytest.fixture
def stored_texts(monkeypatch: pytest.MonkeyPatch) -> dict[str, SimpleNamespace]:
    records = {
        'n1': SimpleNamespace(id='n1', user_id='alice', title='My note', data={'content': {'md': 'Note body'}}),
        'c1': SimpleNamespace(
            id='c1',
            user_id='alice',
            title='My chat',
            folder_id=None,
            chat={
                'history': {
                    'currentId': 'a',
                    'messages': {
                        'u': {'role': 'user', 'content': 'Question', 'parentId': None},
                        'a': {'role': 'assistant', 'content': 'Answer', 'parentId': 'u'},
                        'other': {'role': 'assistant', 'content': 'Other branch', 'parentId': 'u'},
                    },
                }
            },
        ),
    }
    monkeypatch.setattr(
        agent_v2.Users, 'get_user_by_id', AsyncMock(return_value=SimpleNamespace(id='alice', role='user'))
    )
    monkeypatch.setattr(agent_v2.Notes, 'get_note_by_id', AsyncMock(side_effect=records.get))
    monkeypatch.setattr(agent_v2.Chats, 'get_chat_by_id', AsyncMock(side_effect=records.get))
    monkeypatch.setattr(agent_v2.AccessGrants, 'has_access', AsyncMock(return_value=False))
    monkeypatch.setattr(agent_v2.Folders, 'get_folder_by_id', AsyncMock(return_value=None))
    monkeypatch.setattr(agent_v2, 'has_folder_access', AsyncMock(return_value=False))
    return records


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'kind,item_id,text', [('note', 'n1', 'Note body'), ('chat', 'c1', 'user: Question\n\nassistant: Answer')]
)
@pytest.mark.parametrize('access', ['owner', 'admin', 'grant'])
async def test_attached_texts_use_upstream_access_and_stored_titles(
    chat: Chat, stored_texts: dict, monkeypatch: pytest.MonkeyPatch, kind: str, item_id: str, text: str, access: str
) -> None:
    item = stored_texts[item_id]
    if access != 'owner':
        item.user_id = 'bob'
    if access == 'admin':
        monkeypatch.setattr(
            agent_v2.Users, 'get_user_by_id', AsyncMock(return_value=SimpleNamespace(id='alice', role='admin'))
        )
    agent_v2.AccessGrants.has_access.return_value = access == 'grant'
    chunks = await chat.turn('read', 'a1', files=[{'type': kind, 'id': item_id, 'name': 'Stale title'}])
    assert not any('error' in chunk for chunk in chunks)
    assert chat.mutations()[-1][1]['input']['texts'] == [
        {'id': f'{kind}:{item_id}', 'kind': kind, 'title': item.title, 'text': text, 'length': len(text)}
    ]
    agent_v2.Users.get_user_by_id.assert_awaited_once_with('alice')
    if access == 'grant':
        agent_v2.AccessGrants.has_access.assert_awaited_once_with(
            user_id='alice',
            resource_type='note' if kind == 'note' else 'shared_chat',
            resource_id=item_id,
            permission='read',
        )
    else:
        agent_v2.AccessGrants.has_access.assert_not_awaited()
    assert not [event for event in chat.socket if event['type'] == 'source']


@pytest.mark.asyncio
@pytest.mark.parametrize('folder_exists,allowed', [(True, True), (True, False), (False, False)])
async def test_attached_chat_uses_folder_read_access(
    chat: Chat, stored_texts: dict, folder_exists: bool, allowed: bool
) -> None:
    stored_texts['c1'].user_id, stored_texts['c1'].folder_id = 'bob', 'folder'
    folder = SimpleNamespace(id='folder') if folder_exists else None
    agent_v2.Folders.get_folder_by_id.return_value = folder
    agent_v2.has_folder_access.return_value = allowed
    chunks = await chat.turn('read', 'a1', files=[{'type': 'chat', 'id': 'c1', 'name': 'Shared chat'}])
    agent_v2.AccessGrants.has_access.assert_awaited_once_with(
        user_id='alice',
        resource_type='shared_chat',
        resource_id='c1',
        permission='read',
    )
    agent_v2.Folders.get_folder_by_id.assert_awaited_once_with('folder')
    if folder_exists:
        agent_v2.has_folder_access.assert_awaited_once_with('alice', folder, 'read', db=None)
    else:
        agent_v2.has_folder_access.assert_not_awaited()
    if allowed:
        assert chat.mutations()[-1][1]['input']['texts'][0]['id'] == 'chat:c1'
        assert not any('error' in chunk for chunk in chunks)
    else:
        assert 'texts' not in chat.mutations()[-1][1]['input']
        assert notices(chat)[0]['notices'] == ["Chat 'Shared chat' is not accessible and was skipped."]


@pytest.mark.asyncio
@pytest.mark.parametrize('kind,item_id,noun', [('note', 'n1', 'Note'), ('chat', 'c1', 'Chat')])
@pytest.mark.parametrize(
    'reason,notice,removed',
    [
        ('missing', "{noun} 'Attached title' no longer exists and was removed from this chat.", True),
        ('denied', "{noun} 'Attached title' is not accessible and was skipped.", False),
        ('unknown-user', "{noun} 'Attached title' is not accessible and was skipped.", False),
    ],
)
async def test_unreadable_text_attachment_is_left_out_with_a_notice(
    chat: Chat, stored_texts: dict, kind: str, item_id: str, noun: str, reason: str, notice: str, removed: bool
) -> None:
    if reason == 'missing':
        del stored_texts[item_id]
    elif reason == 'denied':
        stored_texts[item_id].user_id = 'bob'
    else:
        agent_v2.Users.get_user_by_id.return_value = None
    chunks = await chat.turn('read', 'a1', files=[{'type': kind, 'id': item_id, 'name': 'Attached title'}])
    assert not any('error' in chunk for chunk in chunks)
    sent = chat.mutations()[-1][1]['input']
    assert 'texts' not in sent
    assert sent['text'] == 'read\n\nAttachment status:\nnot available, left out: Attached title'
    assert notices(chat) == [{'notices': [notice.format(noun=noun)], 'removed': [item_id] if removed else []}]


@pytest.mark.asyncio
async def test_attached_chat_strips_details_and_replaces_documents(chat: Chat, stored_texts: dict) -> None:
    history = stored_texts['c1'].chat['history']
    history['messages']['a']['content'] = (
        '<details type="reasoning"><summary>Thoughts</summary>private\nthought</details>'
        'Here is the report.\n'
        '<document format="html" title="A &amp; B > C"><p>PDF body</p></document>\n'
        "<document title='Second' format='markdown'># Other body</document>"
        '<details type="tool_calls" arguments="a > b">hidden output</details>'
    )
    history['messages']['empty'] = {'role': 'assistant', 'parentId': 'a', 'content': '<details>only tools</details>  '}
    history['currentId'] = 'empty'
    await chat.turn('read', 'a1', files=[{'type': 'chat', 'id': 'c1'}])
    text = chat.mutations()[-1][1]['input']['texts'][0]['text']
    assert text == 'user: Question\n\nassistant: Here is the report.\n[document: A & B > C]\n[document: Second]'


@pytest.mark.asyncio
@pytest.mark.parametrize('kind,item_id', [('note', 'n1'), ('chat', 'c1')])
@pytest.mark.parametrize('size', [49_999, 50_000, 50_001])
async def test_attached_text_is_cut_in_characters_and_keeps_its_full_length(
    chat: Chat, stored_texts: dict, kind: str, item_id: str, size: int
) -> None:
    text = 'é🙂' * size
    text = text[:size]
    if kind == 'note':
        stored_texts[item_id].data['content']['md'] = text
    else:
        stored_texts[item_id].chat['history'] = {'currentId': 'a', 'messages': {'a': {'role': 'user', 'content': text}}}
        text = 'user: ' + text
    await chat.turn('read', 'a1', files=[{'type': kind, 'id': item_id}])
    sent = chat.mutations()[-1][1]['input']['texts'][0]
    assert sent['text'] == text[:50_000]
    assert sent['length'] == len(text)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'language,message',
    [
        ('en-US', 'Attach at most 10 notes or chats.'),
        ('nl-NL', 'Voeg maximaal 10 notities of chats toe.'),
    ],
)
async def test_more_than_ten_notes_and_chats_refuses_before_sending(chat: Chat, language: str, message: str) -> None:
    files = [{'type': 'note' if index % 2 else 'chat', 'id': str(index)} for index in range(11)]
    chunks = await chat.turn('read', 'a1', files=files, user_language=language)
    assert chunks == [{'error': {'code': 'attachments_unavailable', 'message': message}}]
    assert chat.mutations() == []


@pytest.mark.asyncio
async def test_ten_notes_and_chats_are_allowed_together(chat: Chat, stored_texts: dict) -> None:
    files = []
    for index in range(10):
        kind, original = ('note', 'n1') if index % 2 else ('chat', 'c1')
        item = copy.deepcopy(stored_texts[original])
        item.id = f'item-{index}'
        stored_texts[item.id] = item
        files.append({'type': kind, 'id': item.id})
    chunks = await chat.turn('read', 'a1', files=files)
    assert not any('error' in chunk for chunk in chunks)
    assert [text['id'] for text in chat.mutations()[-1][1]['input']['texts']] == [
        f'{entry["type"]}:{entry["id"]}' for entry in files
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize('language', ['en-US', 'nl-NL'])
async def test_attached_files_the_agent_cannot_read_are_left_out_with_a_notice(
    chat: Chat, monkeypatch: pytest.MonkeyPatch, language: str
) -> None:
    stored_files(monkeypatch, ok='completed', busy='processing', broken='failed')
    files = [
        {'type': 'file', 'id': 'ok', 'name': 'goed.pdf'},
        {'type': 'file', 'id': 'busy', 'name': 'bezig.pdf'},
        {'type': 'file', 'id': 'broken', 'name': 'kapot.pdf'},
        {'type': 'file', 'id': 'deleted', 'name': 'weg.pdf'},
    ]
    # Attached in this very message too: left out like the chat's earlier files, not refused.
    chunks = await chat.turn(
        'question', 'a1', files=files, user_message={'id': 'user-a1', 'content': 'question', 'files': files}
    )
    assert not any('error' in chunk for chunk in chunks)
    sent = chat.mutations()[-1][1]['input']
    assert [item['file_id'] for item in sent['attachments']] == ['ok']
    expected = {
        'en-US': [
            "File 'bezig.pdf' is still being processed and was skipped this time.",
            "File 'kapot.pdf' could not be processed and was skipped.",
            "File 'weg.pdf' no longer exists and was removed from this chat.",
        ],
        'nl-NL': [
            "Bestand 'bezig.pdf' wordt nog verwerkt en is deze keer overgeslagen.",
            "Bestand 'kapot.pdf' kon niet worden verwerkt en is overgeslagen.",
            "Bestand 'weg.pdf' bestaat niet meer en is uit deze chat gehaald.",
        ],
    }
    await chat.turn('question', 'a2', files=files, user_language=language)
    assert notices(chat)[-1] == {'notices': expected[language], 'removed': ['deleted']}


@pytest.mark.asyncio
async def test_the_models_prompt_and_the_chats_prompt_are_sent_as_their_own_instructions(chat: Chat) -> None:
    await chat.turn('question', 'a1', system_prompt='Je helpt behandelaars.', chat_system_prompt='Ik ben jurist.')
    sent = chat.mutations()[-1][1]['input']
    assert sent['assistant_instructions'] == 'Je helpt behandelaars.'
    assert sent['user_instructions'] == 'Ik ben jurist.'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('features', 'state'),
    [
        ({'web_search': True, 'web_search_required': True}, 'required'),
        ({'web_search': True}, 'auto'),
        ({'web_search': True, 'web_search_required': False}, 'auto'),
        ({'web_search': False}, 'off'),
        ({'web_search': False, 'web_search_required': True}, 'off'),
        ({}, 'off'),
    ],
    ids=['always', 'auto', 'auto-explicit', 'off', 'off-ignores-required', 'absent'],
)
async def test_the_web_search_control_sends_its_state_on_every_turn(chat: Chat, features: dict, state: str) -> None:
    await chat.turn('question', 'a1', features=features)
    assert chat.mutations()[-1][1]['input']['tools'] == {
        **WEB_SEARCH_OFF,
        'web_search': state,
        'fetch': 'off' if state == 'off' else 'auto',
    }


@pytest.mark.parametrize(
    ('features', 'state'),
    [
        ({'web_search': True, 'web_search_required': True}, 'required'),
        ({'web_search': True}, 'auto'),
        ({'web_search': False}, 'off'),
        (None, 'off'),
    ],
    ids=['always', 'auto', 'off', 'no-features'],
)
def test_tools_maps_the_web_search_features_to_tool_states(features: dict | None, state: str) -> None:
    assert agent_v2._tools({'features': features}, True, False, False) == {
        'tools': {**WEB_SEARCH_OFF, 'web_search': state, 'fetch': 'off' if state == 'off' else 'auto'}
    }


@pytest.mark.parametrize('state', ['off', 'auto', 'required', 'unexpected', True, None])
@pytest.mark.parametrize('allowed', [False, True])
def test_live_document_states_are_gated_on_the_server(state, allowed):
    tools = agent_v2._tools({'features': {'live_documents': state}}, False, allowed, False)['tools']
    expected = state if allowed and state in ('auto', 'required') else 'off'
    assert tools == {
        **WEB_SEARCH_OFF,
        'search_live_documents': expected,
        'list_live_folder': expected,
        'attach_live_document': expected,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize('state', ['auto', 'required'])
@pytest.mark.parametrize('setup_fails', [False, True])
async def test_live_document_tools_follow_collection_setup(
    chat: Chat, monkeypatch: pytest.MonkeyPatch, state: str, setup_fails: bool
) -> None:
    monkeypatch.setattr(agent_v2, '_live_documents_allowed', AsyncMock(return_value=True))
    collection = AsyncMock(
        return_value='owui-attachments-alice',
        side_effect=RuntimeError('collection unavailable') if setup_fails else None,
    )
    monkeypatch.setattr(agent_v2.live_documents, 'attachment_collection', collection)
    await chat.turn('question', 'a1', features={'live_documents': state, 'web_search': True})
    collection.assert_awaited_once()
    sent = chat.mutations()[-1][1]['input']
    expected = 'off' if setup_fails else state
    assert sent['tools'] == {
        **WEB_SEARCH_OFF,
        'web_search': 'auto',
        'fetch': 'auto',
        'search_live_documents': expected,
        'list_live_folder': expected,
        'attach_live_document': expected,
    }
    if setup_fails:
        assert 'attachment_collection' not in sent
        assert 'live documents unavailable this turn' in sent['text']
    else:
        assert sent['attachment_collection'] == 'owui-attachments-alice'


@pytest.mark.parametrize('features', [{'web_search': True, 'web_search_required': True}, {'web_search': True}])
def test_tools_sends_off_when_owui_does_not_allow_web_search(features: dict) -> None:
    assert agent_v2._tools({'features': features}, False, False, False) == {'tools': WEB_SEARCH_OFF}


@pytest.mark.asyncio
async def test_a_disallowed_turn_asks_the_agent_for_no_web_search(chat: Chat, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_v2, '_web_search_allowed', AsyncMock(return_value=False))
    await chat.turn('question', 'a1', features={'web_search': True, 'web_search_required': True})
    assert chat.mutations()[-1][1]['input']['tools'] == WEB_SEARCH_OFF


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('enabled', 'role', 'permitted', 'allowed'),
    [
        (False, 'admin', True, False),
        (True, 'admin', False, True),
        (True, 'user', True, True),
        (True, 'user', False, False),
        (True, None, True, False),
    ],
    ids=['tenant-off', 'admin', 'user-permitted', 'user-denied', 'unknown-user'],
)
async def test_web_search_allowed_needs_the_tenant_setting_and_the_users_permission(
    monkeypatch: pytest.MonkeyPatch, enabled: bool, role: str | None, permitted: bool, allowed: bool
) -> None:
    async def config_get(key: str, default=None):
        return {'web.search.enable': enabled, 'user.permissions': {}}.get(key, default)

    user = SimpleNamespace(id='u1', role=role) if role else None
    monkeypatch.setattr(agent_v2.Config, 'get', config_get)
    monkeypatch.setattr(agent_v2.Users, 'get_user_by_id', AsyncMock(return_value=user))
    monkeypatch.setattr(agent_v2, 'has_permission', AsyncMock(return_value=permitted))
    assert await agent_v2._web_search_allowed('u1') is allowed


@pytest.mark.asyncio
async def test_absent_or_blank_prompts_send_no_instructions(chat: Chat) -> None:
    await chat.turn('question', 'a1', system_prompt=' ', chat_system_prompt=None)
    assert chat.mutations()[-1][1]['input'] == {
        'text': 'question',
        'knowledge': [],
        'documents': 'off',
        'tools': WEB_SEARCH_OFF,
    }


@pytest.mark.asyncio
async def test_orphaned_input_resumes_before_one_new_input(chat: Chat) -> None:
    await chat.turn('first', 'a1')
    chat.api.chat.threads['thr-1']['state'] = 'orphaned'
    chunks = await chat.turn('second', 'a2', 'a1')
    assert content(chunks) == 'Answer: second'
    assert [path for path, _ in chat.mutations()][-3:] == [
        '/v1/chat/threads/thr-1/inputs',
        '/v1/chat/threads/thr-1/resume',
        '/v1/chat/threads/thr-1/inputs',
    ]
    assert [
        event['payload']['payload']['text']
        for event in chat.api.chat.threads['thr-1']['events']
        if event['type'] == 'input'
    ] == ['first', 'second']
    assert chat.bookmark('a2')['position'] == 5


@pytest.mark.asyncio
async def test_running_input_is_reported_without_resume_or_cancel(chat: Chat) -> None:
    await chat.turn('first', 'a1')
    chat.api.chat.threads['thr-1']['state'] = 'running'
    chunks = await chat.turn('second', 'a2', 'a1')
    assert chunks == [agent_v2._error('thread_active')]
    assert len(chat.mutations()) == 2


SOURCE = {
    'id': 'source-a',
    'ref': 'document',
    'text': 'quoted passage',
    'properties': {'title': 'Document', 'page_numbers': [2]},
}
RECT = {'page': 2, 'x0': 10, 'y0': 20.5, 'x1': 100, 'y1': 50}


def test_chunks_share_a_document_number_without_losing_text() -> None:
    """Keep chunk identity and text while sharing the document's citation number."""
    citations = agent_v2.Citations()
    first = citations.add(SOURCE)
    second = citations.add({**SOURCE, 'id': 'source-b', 'text': 'another passage'})
    assert first is not None and second is not None
    assert first['source']['id'] == second['source']['id'] == 'document'
    assert first['metadata'][0]['source'] == second['metadata'][0]['source'] == 'document'
    assert [item['metadata'][0]['chunk_id'] for item in (first, second)] == ['source-a', 'source-b']
    assert [item['document'] for item in (first, second)] == [['quoted passage'], ['another passage']]
    assert first['n'] == second['n'] == 1
    assert citations.add(SOURCE) is None


# [Claude] What the thread API serves: elements in a tool output, and an answer whose text carries no markers,
# its citations beside it as positions with real ids.
DOCUMENT = {'type': 'document', 'id': 'document', 'title': 'Document'}
CHUNK = {'type': 'chunk', 'id': 'source-a', 'ref': 'document', 'text': 'quoted passage', 'pages': [2]}


def document(id: str, title: str, **fields: Any) -> dict:
    return {'type': 'document', 'id': id, 'title': title, **fields}


def chunk(id: str, ref: str = 'document', text: str = 'quoted passage', **fields: Any) -> dict:
    return {'type': 'chunk', 'id': id, 'ref': ref, 'text': text, **fields}


def found(*elements: dict, call_id: str = 'c0') -> tuple[str, dict]:
    return ('tool_output', {'call_id': call_id, 'elements': list(elements)})


def cited(at: int, source: str, document: str = 'document') -> dict:
    return {'at': at, 'status': 'resolved', 'source': source, 'document': document}


def answered(text: str, *citations: dict, **payload: Any) -> tuple[str, dict]:
    return ('model_output', {'content': text, 'citations': list(citations), **payload})


def panel(chunks: list[dict], socket: list[dict]) -> list[int]:
    """[Claude] The numbers the message's source pill lists, as `citedCitations` does: each `[N]` in the answer
    that names a source sent to the message."""
    sent = {event['data']['n'] for event in socket if event['type'] == 'source'}
    return sorted({n for marker in re.findall(r'\[(\d+)\]', content(chunks)) if (n := int(marker)) in sent})


@pytest.mark.asyncio
@pytest.mark.parametrize('streamed', [False, True])
@pytest.mark.parametrize('kind,item_id,route', [('note', 'n1', 'notes'), ('chat', 'c1', 'c')])
async def test_attached_text_citations_emit_numbered_markers_and_local_source_links(
    chat: Chat, stored_texts: dict, streamed: bool, kind: str, item_id: str, route: str
) -> None:
    text = 'Note body' if kind == 'note' else 'user: Question\n\nassistant: Answer'
    text_id = f'{kind}:{item_id}'
    citation = cited(6, f'{text_id}#0-{len(text)}', text_id)
    chat.api.chat.turns = [
        [
            *([('delta', {'text': 'Answer'}), ('citation', citation)] if streamed else []),
            answered('Answer.', citation),
        ]
    ]
    chunks = await chat.turn('read', 'a1', files=[{'type': kind, 'id': item_id}])
    assert content(chunks) == 'Answer [1].'
    (source,) = [event['data'] for event in chat.socket if event['type'] == 'source']
    assert source['source'] == {'id': text_id, 'name': stored_texts[item_id].title, 'url': f'/{route}/{item_id}'}
    assert source['document'] == [text]
    assert source['metadata'][0]['chunk_id'] == citation['source']
    assert source['n'] == 1
    assert panel(chunks, chat.socket) == [1]


@pytest.mark.asyncio
@pytest.mark.parametrize('branch', [False, True])
async def test_attached_text_citations_are_seeded_from_root_inputs_at_the_parent(
    chat: Chat, stored_texts: dict, branch: bool
) -> None:
    chat.api.chat.turns = [[found(DOCUMENT, CHUNK), answered('First.')]]
    await chat.turn('read', 'a1', files=[{'type': 'note', 'id': 'n1'}])
    if branch:
        await chat.turn('later', 'a2', 'a1', files=[{'type': 'chat', 'id': 'c1'}])
    chat.socket.clear()
    chat.api.chat.turns = [[answered('Answer.', cited(6, 'note:n1#0-9', 'note:n1'), cited(6, 'source-a'))]]
    chunks = await chat.turn('again', 'regenerated', 'a1')
    assert content(chunks) == 'Answer [1] [2].'
    sources = [event['data'] for event in chat.socket if event['type'] == 'source']
    assert {source['source']['id'] for source in sources} == {'note:n1', 'document'}
    note = next(source for source in sources if source['source']['id'] == 'note:n1')
    assert note['source']['url'] == '/notes/n1'
    assert note['document'] == ['Note body']
    assert panel(chunks, chat.socket) == [1, 2]
    if branch:
        assert chat.mutations()[-2][0] == '/v1/chat/threads/thr-1/fork'


@pytest.mark.asyncio
async def test_attached_text_seeding_ignores_child_streams_and_future_inputs() -> None:
    turn = agent_v2.AgentTurn(AsyncMock(), {}, 'owui:user:alice')
    text = {'id': 'note:n1', 'kind': 'note', 'title': 'Note', 'text': 'body', 'length': 4}
    await turn._seed_sources(
        [
            {'position': 1, 'type': 'input', 'stream': 'child', 'payload': {'payload': {'texts': [text]}}},
            {'position': 3, 'type': 'input', 'stream': 'root', 'payload': {'payload': {'texts': [text]}}},
        ],
        2,
    )
    assert turn.citations.sources == {}


@pytest.mark.asyncio
async def test_new_text_attachments_follow_previously_numbered_sources(chat: Chat, stored_texts: dict) -> None:
    chat.api.chat.turns = [[found(DOCUMENT, CHUNK), answered('First.', cited(5, 'source-a'))]]
    await chat.turn('first', 'a1')
    chat.socket.clear()
    stored_texts['n1'].data['content']['md'] = 'n' * 50_001
    chat.api.chat.turns = [[answered('Note.', cited(4, 'note:n1#0-50000', 'note:n1'))]]
    chunks = await chat.turn('read', 'a2', 'a1', files=[{'type': 'note', 'id': 'n1'}])
    assert content(chunks) == 'Note [2].'
    sources = [event['data'] for event in chat.socket if event['type'] == 'source']
    assert [(source['source']['id'], source['n']) for source in sources] == [('document', 1), ('note:n1', 2)]
    assert sources[-1]['document'] == ['n' * 50_000]


@pytest.mark.asyncio
async def test_document_numbers_survive_seeded_turns(chat: Chat) -> None:
    """Number documents by first appearance across chunks and seeded turns."""
    chunks = [chunk(f'chunk-{index}') for index in range(3)]
    second = document('second', 'Second')
    chat.api.chat.turns = [
        [
            found(DOCUMENT, *chunks, second, chunk('second-1', 'second')),
            answered('Eerst.', cited(5, 'chunk-0'), cited(5, 'chunk-1'), cited(5, 'second-1', 'second')),
        ]
    ]
    answer = await chat.turn('first', 'a1')
    assert content(answer) == 'Eerst [1] [2].'
    sources = [event['data'] for event in chat.socket if event['type'] == 'source']
    assert [source['n'] for source in sources] == [1, 1, 1, 2]
    assert panel(answer, chat.socket) == [1, 2]
    chat.socket.clear()
    chat.api.chat.turns = [
        [
            found(DOCUMENT, chunk('chunk-3'), second, chunk('second-2', 'second'), document('third', 'Third')),
            found(chunk('third-1', 'third'), call_id='c1'),
            answered('Dan.', cited(3, 'chunk-0'), cited(3, 'third-1', 'third')),
        ]
    ]
    answer = await chat.turn('next', 'a2', 'a1')
    assert content(answer) == 'Dan [1] [3].'
    sources = [event['data'] for event in chat.socket if event['type'] == 'source']
    assert [source['n'] for source in sources] == [1, 1, 1, 2, 1, 2, 3]
    assert panel(answer, chat.socket) == [1, 3]


def test_different_documents_with_the_same_title_keep_distinct_numbers() -> None:
    """Preserve document identity even though the inline renderer deduplicates titles."""
    citations = agent_v2.Citations()
    first = citations.add(SOURCE)
    second = citations.add({**SOURCE, 'id': 'source-b', 'ref': 'other-document'})
    assert first is not None and second is not None
    assert first['source']['name'] == second['source']['name']
    assert first['source']['id'] != second['source']['id']
    assert [first['n'], second['n']] == [1, 2]


@pytest.mark.parametrize('source_id', [None, '', 123, False, [], 'owui-file-id'])
def test_file_id_requires_a_nonempty_source_id(source_id: Any) -> None:
    """Only the consumer's nonempty string source ID enables a file preview."""
    properties = {'source_id': source_id, 'file_id': 'untrusted', 'title': 'owui-file-id.pdf'}
    result = agent_v2.Citations().add({**SOURCE, 'properties': properties})
    metadata = result['metadata'][0]
    if source_id == 'owui-file-id':
        assert metadata['file_id'] == source_id
    else:
        assert 'file_id' not in metadata


@pytest.mark.parametrize(
    'pages,expected',
    [
        (None, None),
        ([], None),
        ('2', None),
        ([1], 0),
        ([3, 1], 2),
        ([0], None),
        ([-1], None),
        ([1, 0], None),
        ([1, '2'], None),
        ([1.0], None),
        ([True], None),
    ],
)
def test_source_pages_require_positive_integers(pages: Any, expected: int | None) -> None:
    """Convert only a nonempty list of positive integer pages to zero-based metadata."""
    result = agent_v2.Citations().add({**SOURCE, 'properties': {'page_numbers': pages, 'page': 99}})
    metadata = result['metadata'][0]
    if expected is None:
        assert 'page' not in metadata
    else:
        assert metadata['page'] == expected
    assert 'page_numbers' not in metadata


@pytest.mark.asyncio
@pytest.mark.parametrize('as_json', [False, True])
async def test_source_preview_metadata_reaches_the_panel(chat: Chat, as_json: bool) -> None:
    """Emit viewer metadata without mutating input or retaining duplicated chunk data."""
    rects = [RECT, {**RECT, 'page': 1}, {key: value for key, value in RECT.items() if key != 'page'}]
    report = document('document', 'report.pdf', source_id='owui-file-id', source_url='https://example.org/r')
    passage = {**CHUNK, 'bboxes': json.dumps(rects) if as_json else rects, 'chunk_index': 4}
    original = copy.deepcopy(passage)
    chat.api.chat.turns = [[found(report, passage), answered('Answer', cited(6, 'source-a'))]]
    assert content(await chat.turn('question', 'a1')) == 'Answer [1]'
    source = next(event['data'] for event in chat.socket if event['type'] == 'source')
    assert source['source'] == {'id': 'document', 'name': 'report.pdf', 'url': 'https://example.org/r'}
    assert source['document'] == [CHUNK['text']]
    assert source['metadata'][0] == {
        'title': 'report.pdf',
        'source_id': 'owui-file-id',
        'source_url': 'https://example.org/r',
        'file_id': 'owui-file-id',
        'page': 1,
        'bboxes': [{**RECT, 'page': 1}, {**RECT, 'page': 0}, rects[2]],
        'source': 'document',
        'name': 'report.pdf',
        'chunk_id': 'source-a',
        'ref': 'document',
    }
    assert passage == original


@pytest.mark.asyncio
@pytest.mark.parametrize('as_json', [False, True])
async def test_mixed_bboxes_preserve_valid_rectangles(chat: Chat, as_json: bool) -> None:
    """Keep both valid rectangles when a malformed rectangle appears between them."""
    rects = [RECT, {}, {**RECT, 'page': 1}]
    passage = {**CHUNK, 'bboxes': json.dumps(rects) if as_json else rects}
    chat.api.chat.turns = [[found(DOCUMENT, passage), answered('Answer', cited(6, 'source-a'))]]
    assert content(await chat.turn('question', 'a1')) == 'Answer [1]'
    metadata = next(event['data']['metadata'][0] for event in chat.socket if event['type'] == 'source')
    assert metadata['bboxes'] == [{**RECT, 'page': 1}, {**RECT, 'page': 0}]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'bboxes',
    [
        None,
        '',
        'broken json',
        'null',
        '{}',
        '[]',
        [],
        {},
        [None],
        [1],
        [{}],
        [{**RECT, 'page': 0}],
        [{**RECT, 'page': '2'}],
        [{**RECT, 'page': True}],
        [{**RECT, 'page': 1.5}],
        [{**RECT, 'x0': '10'}],
        [{**RECT, 'x0': False}],
        [{**RECT, 'x1': 10}],
        [{**RECT, 'y1': 20}],
        [None, {}, {**RECT, 'x1': 10}],
        json.dumps([None, {}, {**RECT, 'x1': 10}]),
        '[{"x0": 0, "y0": 0, "x1": 1e999, "y1": 10}]',
    ],
)
async def test_malformed_bboxes_never_fail_a_turn(chat: Chat, bboxes: Any) -> None:
    """Ignore malformed rectangle data while delivering the source and answer."""
    passage = {key: value for key, value in CHUNK.items() if key != 'pages'} | {'bboxes': bboxes}
    chat.api.chat.turns = [[found(DOCUMENT, passage), answered('Answer', cited(6, 'source-a'))]]
    assert content(await chat.turn('question', 'a1')) == 'Answer [1]'
    metadata = next(event['data']['metadata'][0] for event in chat.socket if event['type'] == 'source')
    assert 'bboxes' not in metadata
    assert 'file_id' not in metadata
    assert 'page' not in metadata


def test_absent_optional_source_properties_are_omitted() -> None:
    """Accept sources from servers that do not yet provide preview properties."""
    result = agent_v2.Citations().add({key: value for key, value in SOURCE.items() if key != 'properties'})
    assert result['metadata'][0] == {
        'source': 'document',
        'name': 'document',
        'chunk_id': 'source-a',
        'ref': 'document',
    }


@pytest.mark.asyncio
async def test_a_streamed_citation_is_placed_where_it_arrives_and_not_again_at_the_end(chat: Chat) -> None:
    chat.api.chat.turns = [
        [
            found(DOCUMENT, CHUNK),
            ('delta', {'text': 'Text'}),
            ('citation', cited(4, 'source-a')),
            ('delta', {'text': ' tail'}),
            answered('Text tail', cited(4, 'source-a')),
        ]
    ]
    assert content(await chat.turn('question', 'a1')) == 'Text [1] tail'


@pytest.mark.asyncio
async def test_a_citation_streamed_at_the_end_of_the_text_is_not_repeated_by_the_answer(chat: Chat) -> None:
    chat.api.chat.turns = [
        [
            found(DOCUMENT, CHUNK),
            ('delta', {'text': 'Text'}),
            ('citation', cited(4, 'source-a')),
            answered('Text', cited(4, 'source-a')),
        ]
    ]
    assert content(await chat.turn('question', 'a1')) == 'Text [1]'


@pytest.mark.asyncio
async def test_an_answer_not_streamed_gets_its_markers_at_the_served_positions(chat: Chat) -> None:
    other = document('other', 'Other')
    chat.api.chat.turns = [
        [
            found(DOCUMENT, CHUNK, other, chunk('b-1', 'other')),
            answered('Eén, twee.', cited(3, 'source-a'), cited(9, 'b-1', 'other')),
        ]
    ]
    assert content(await chat.turn('question', 'a1')) == 'Eén [1], twee [2].'


EMPHASIS_CASES = [
    ('- **', ' Van jou →** naar', '- **[1] Van jou →** naar'),
    ('**Van jou', '**: tekst', '**Van jou [1]**: tekst'),
    ('*', '*', '*[1]*'),
    ('(__', ' x__)', '(__[1] x__)'),
    ('~~', ' oud~~', '~~[1] oud~~'),
    ('**Bold**', ' then', '**Bold** [1] then'),
    ('2**', ' x', '2** [1] x'),
    ('Text', ' tail', 'Text [1] tail'),
]


@pytest.mark.asyncio
@pytest.mark.parametrize('before,after,expected', EMPHASIS_CASES)
async def test_a_streamed_marker_after_an_opening_emphasis_run_keeps_it_opening(
    chat: Chat, before: str, after: str, expected: str
) -> None:
    chat.api.chat.turns = [
        [
            found(DOCUMENT, CHUNK),
            ('delta', {'text': before}),
            ('citation', cited(len(before), 'source-a')),
            ('delta', {'text': after}),
            answered(before + after, cited(len(before), 'source-a')),
        ]
    ]
    assert content(await chat.turn('question', 'a1')) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize('before,after,expected', EMPHASIS_CASES)
async def test_a_served_marker_after_an_opening_emphasis_run_keeps_it_opening(
    chat: Chat, before: str, after: str, expected: str
) -> None:
    chat.api.chat.turns = [[found(DOCUMENT, CHUNK), answered(before + after, cited(len(before), 'source-a'))]]
    assert content(await chat.turn('question', 'a1')) == expected


def test_served_markers_after_streamed_text_see_the_text_before_them() -> None:
    assert agent_v2._marked('**Ja** **x**', [(9, 1)], 6) == ' **[1]x**'
    assert agent_v2._marked('Ja **x**', [(5, 1), (5, 2)], 3) == '**[1][2]x**'


@pytest.mark.asyncio
async def test_an_invalid_citation_or_an_unknown_source_gets_no_marker(chat: Chat) -> None:
    invalid = {'at': 4, 'status': 'invalid', 'reason': 'no element has id 9'}
    chat.api.chat.turns = [
        [
            found(DOCUMENT, CHUNK),
            ('delta', {'text': 'Text'}),
            ('citation', invalid),
            ('delta', {'text': ' tail'}),
            answered('Text tail', invalid, cited(9, 'never-shown')),
        ]
    ]
    assert content(await chat.turn('question', 'a1')) == 'Text tail'


@pytest.mark.asyncio
@pytest.mark.parametrize('close_after', [None, 4])
@pytest.mark.parametrize(
    'durable,expected',
    [
        (answered('Draft.', cited(5, 'source-a'), reasoning='reason'), 'Draft [1]. Next.'),
        (answered('Revised answer.', reasoning='reason'), 'Draft Next.'),
    ],
)
async def test_durable_output_prefix_and_mismatch_do_not_cancel(
    chat: Chat, caplog: pytest.LogCaptureFixture, close_after: int | None, durable: tuple, expected: str
) -> None:
    chat.api.chat.close_after = close_after
    chat.api.chat.turns = [
        [
            found(DOCUMENT, CHUNK),
            ('delta', {'text': 'Draft'}),
            durable,
            ('model_output', {'content': ' Next.'}),
        ]
    ]
    chunks = await chat.turn('question', 'a1')
    assert content(chunks) == expected
    assert not any('error' in chunk for chunk in chunks)
    assert len(chat.mutations()) == 1
    assert chat.bookmark('a1')['position'] == 5
    mismatch = durable[1]['content'] == 'Revised answer.'
    warnings = [record for record in caplog.records if 'disagrees' in record.message]
    assert len(warnings) == int(mismatch)
    if mismatch:
        assert warnings[0].thread_id == 'thr-1'
        assert 'Draft' not in warnings[0].message
        assert durable[1]['content'] not in warnings[0].message
        assert content(chunks, 'reasoning_content') == ''


@pytest.mark.asyncio
async def test_the_panel_lists_what_this_turn_cited_with_cumulative_numbers(chat: Chat) -> None:
    """List the sources this turn's answer cites, numbered across turns."""
    second = [document('document-b', 'Second'), chunk('source-b', 'document-b')]
    third = [document('document-c', 'Third'), chunk('source-c', 'document-c')]
    chat.api.chat.turns = [[found(DOCUMENT, CHUNK, *second), answered('First', cited(5, 'source-a'))]]
    assert panel(await chat.turn('first', 'a1'), chat.socket) == [1]
    chat.socket.clear()
    chat.api.chat.turns = [
        [
            found(*second, *third, third[1]),
            answered(
                'Second and; earlier',
                cited(6, 'source-b', 'document-b'),
                cited(10, 'source-c', 'document-c'),
                cited(19, 'source-a'),
            ),
        ]
    ]
    answer = await chat.turn('second', 'a2', 'a1')
    assert content(answer) == 'Second [2] and [3]; earlier [1]'
    assert panel(answer, chat.socket) == [1, 2, 3]
    chat.socket.clear()
    assert panel(await chat.turn('third', 'a3', 'a2'), chat.socket) == []


@pytest.mark.asyncio
@pytest.mark.parametrize('model', [None, '', 123, 'host-inference-endpoint'])
@pytest.mark.parametrize('picked', [{}, {'info': {'base_model_id': 'llm'}}])
async def test_resolved_picker_model_wins_over_agent_model(
    chat: Chat, monkeypatch: pytest.MonkeyPatch, model: Any, picked: dict
) -> None:
    monkeypatch.setattr(
        AgentConfigs,
        'get_agent_config_by_id',
        AsyncMock(return_value=SimpleNamespace(meta={'runtime': 'v2', 'model': model})),
    )
    await chat.turn('first', 'a1', model=picked)
    await chat.turn('second', 'a2', 'a1', model=picked)
    await chat.turn('regenerated', 'retry', 'a1', model=picked)
    for path, body in chat.mutations():
        if path.endswith('/fork'):
            continue
        assert body['model'] == ('llm' if picked else 'custom')


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('meta', 'sent'),
    [
        ({}, 'soev_react'),
        ({'runtime': 'v1'}, 'soev_react'),
        (None, 'soev_chat_manual'),
        ({'runtime': 'v2'}, 'soev_chat_manual'),
    ],
)
async def test_v2_deployment_replaces_a_v1_agent_binding_with_the_configured_agent(
    chat: Chat, monkeypatch: pytest.MonkeyPatch, meta: dict | None, sent: str
) -> None:
    monkeypatch.setattr(env, 'AGENT_API_RUNTIME', 'v2')
    monkeypatch.setattr(agent.Config, 'get', AsyncMock(return_value='soev_react'))
    monkeypatch.setattr(
        AgentConfigs,
        'get_agent_config_by_id',
        AsyncMock(return_value=SimpleNamespace(meta=meta) if meta is not None else None),
    )
    await chat.turn('first', 'a1', override_agent='soev_chat_manual')
    assert [body['agent'] for path, body in chat.mutations() if path == '/v1/chat/threads'] == [sent]


@pytest.mark.asyncio
@pytest.mark.parametrize('meta', [None, {}, {'runtime': 'v1'}, {'runtime': 'v2', 'model': 'agent-model'}])
@pytest.mark.parametrize('selected_agent', [None, 'test'])
async def test_environment_routes_every_turn_to_v2(
    chat: Chat, monkeypatch: pytest.MonkeyPatch, meta: dict | None, selected_agent: str | None
) -> None:
    monkeypatch.setattr(env, 'AGENT_API_RUNTIME', 'v2')
    monkeypatch.setattr(agent.Config, 'get', AsyncMock(return_value=selected_agent))
    monkeypatch.setattr(
        AgentConfigs,
        'get_agent_config_by_id',
        AsyncMock(return_value=SimpleNamespace(meta=meta) if meta is not None else None),
    )
    for index in range(1, 3):
        chunks = await chat.turn(f'turn {index}', f'a{index}', 'a1' if index == 2 else None, override_agent=None)
        assert content(chunks) == f'Answer: turn {index}'
    assert [path for path, _ in chat.mutations()] == ['/v1/chat/threads', '/v1/chat/threads/thr-1/inputs']
    for _, body in chat.mutations():
        assert body['model'] == 'llm'


@pytest.mark.asyncio
@pytest.mark.parametrize('stream', [False, True])
@pytest.mark.parametrize('continuation', [False, True])
@pytest.mark.parametrize('sse', [False, True])
async def test_refused_model_is_named_without_retry_or_default(
    chat: Chat, monkeypatch: pytest.MonkeyPatch, stream: bool, continuation: bool, sse: bool
) -> None:
    if continuation:
        await chat.turn('first', 'a1')
    problem = {
        'code': 'invalid_field',
        'constraint': 'chat:model',
        'detail': 'private upstream detail',
    }
    if sse:
        chat.api.chat.turns = [[('error', problem)]]
    else:
        original = chat.api.chat.handle

        def refuse(request: httpx.Request, body: dict | None, owner: tuple[str, str | None]) -> httpx.Response:
            if request.method == 'POST':
                chat.api.chat.requests.append(request)
                return httpx.Response(422, json=problem, headers={'Content-Type': 'application/problem+json'})
            return original(request, body, owner)

        monkeypatch.setattr(chat.api.chat, 'handle', refuse)
    kwargs = {'model': {'info': {'base_model_id': 'refused-model'}}}
    if stream:
        result = (await chat.turn('question', 'a2', 'a1' if continuation else None, **kwargs))[-1]
    else:
        result = await chat.response('question', 'a2', 'a1' if continuation else None, stream=False, **kwargs)
    assert result['error']['code'] == 'invalid_field'
    assert 'refused-model' in result['error']['message']
    assert 'private upstream detail' not in str(result)
    assert len(chat.mutations()) == (2 if continuation else 1)
    assert chat.mutations()[-1][1]['model'] == 'refused-model'


def refuse_turns(chat: Chat, monkeypatch: pytest.MonkeyPatch, problem: dict) -> None:
    """soev-api refuses every new turn with this 422 problem."""
    original = chat.api.chat.handle

    def refuse(request: httpx.Request, body: dict | None, owner: tuple[str, str | None]) -> httpx.Response:
        if request.method == 'POST':
            chat.api.chat.requests.append(request)
            return httpx.Response(422, json=problem, headers={'Content-Type': 'application/problem+json'})
        return original(request, body, owner)

    monkeypatch.setattr(chat.api.chat, 'handle', refuse)


# Per agent refusal reason, per language: words its message must carry; whether the agent's detail is shown.
REASONS = {
    'knowledge_unavailable': ({'en-US': 'knowledge bases', 'nl-NL': 'kennisbanken'}, False),
    'attachments_unreadable': ({'en-US': 'attached files', 'nl-NL': 'bijlagen'}, True),
    'unknown_model': ({'en-US': '"picked-model"', 'nl-NL': '"picked-model"'}, False),
    'not_chat_model': ({'en-US': 'not a chat model', 'nl-NL': 'geen chatmodel'}, False),
    'model_required': ({'en-US': 'no model', 'nl-NL': 'geen model'}, False),
    'tool_unsupported': ({'en-US': 'cannot use a tool', 'nl-NL': 'hulpmiddel'}, True),
    'credential_not_live': ({'en-US': 'expired', 'nl-NL': 'verlopen'}, False),
}


@pytest.mark.asyncio
@pytest.mark.parametrize('language', ['en-US', 'nl-NL'])
@pytest.mark.parametrize('reason', sorted(REASONS))
async def test_an_agent_refusal_shows_its_reason_localised_and_logs_only_the_code(
    chat: Chat, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, language: str, reason: str
) -> None:
    detail = 'these attached files cannot be read: geheim.pdf'
    refuse_turns(chat, monkeypatch, {'code': 'invalid_field', 'constraint': f'chat:{reason}', 'detail': detail})
    words, shows_detail = REASONS[reason]
    with caplog.at_level('WARNING', logger=agent_v2.log.name):
        chunks = await chat.turn(
            'question', 'a1', user_language=language, model={'info': {'base_model_id': 'picked-model'}}
        )
    error = chunks[-1]['error']
    assert error['code'] == 'invalid_field'
    assert words[language] in error['message']
    assert error['message'] != agent_v2._error('invalid_field')['error']['message']
    assert (detail in error['message']) == shows_detail
    assert f'code=invalid_field constraint=chat:{reason}' in caplog.text
    assert 'geheim.pdf' not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize('constraint', [None, 'chat:a_later_reason'])
async def test_a_refusal_without_a_known_reason_keeps_the_generic_text(
    chat: Chat, monkeypatch: pytest.MonkeyPatch, constraint: str | None
) -> None:
    problem = {'code': 'invalid_field', 'detail': 'input rejected'}
    if constraint is not None:
        problem['constraint'] = constraint
    refuse_turns(chat, monkeypatch, problem)
    chunks = await chat.turn('question', 'a1', user_language='nl-NL')
    assert chunks[-1] == agent_v2._error('invalid_field')


@pytest.mark.asyncio
async def test_stream_renders_sources_reasoning_and_tools_without_duplicate_text(chat: Chat) -> None:
    """Stream each chunk once and render same-document markers with one number."""
    chat.api.chat.turns = [
        [
            (
                'model_output',
                {'content': '', 'reasoning': 'Plan', 'tool_calls': [{'id': 'c1', 'name': 'search', 'arguments': {}}]},
            ),
            (
                'tool_output',
                {
                    'call_id': 'c1',
                    'data': 'PRIVATE TOOL RESULT',
                    'elements': [DOCUMENT, CHUNK, CHUNK, chunk('source-b', text='another passage')],
                },
            ),
            ('delta', {'text': 'Answer'}),
            ('citation', cited(6, 'source-b')),
            ('delta', {'text': ' and'}),
            ('citation', cited(10, 'source-a')),
            ('delta', {'text': '.'}),
            answered('Answer and.', cited(6, 'source-b'), cited(10, 'source-a'), reasoning='Explain'),
            ('future_event', {'unknown': 'ignored'}),
        ]
    ]
    chunks = await chat.turn('question', 'a1')
    marker = '\n\n<details type="tool_calls" done="true" name="search">\n<summary>Searching the knowledge base…</summary>\n</details>\n\n'
    assert content(chunks) == marker + 'Answer [1] and [1].'
    assert 'PRIVATE' not in json.dumps(chunks)
    assert content(chunks, 'reasoning_content') == 'PlanExplain'
    sources = [event['data'] for event in chat.socket if event['type'] == 'source']
    assert [source['n'] for source in sources] == [1, 1]
    assert sources[0]['source'] == {'id': 'document', 'name': 'Document', 'url': 'document'}
    assert sources[0]['document'] == ['quoted passage']
    assert sources[0]['metadata'][0]['source'] == 'document'
    assert sources[1]['metadata'][0]['source'] == 'document'
    assert chat.bookmark('a1')['position'] == 6


@pytest.mark.asyncio
async def test_previous_sources_are_available_in_later_turn_and_fork(chat: Chat) -> None:
    chat.api.chat.turns = [[found(DOCUMENT, CHUNK), answered('First', cited(5, 'source-a'))]]
    await chat.turn('first', 'a1')
    for message_id in ('a2', 'regenerated'):
        chat.socket.clear()
        chat.api.chat.turns = [[answered('Again', cited(5, 'source-a'))]]
        answer = await chat.turn('again', message_id, 'a1')
        assert content(answer) == 'Again [1]'
        assert panel(answer, chat.socket) == [1]


@pytest.mark.asyncio
async def test_capped_stream_recovers_durable_suffix_and_split_marker(chat: Chat) -> None:
    chat.api.chat.turns = [
        [
            found(DOCUMENT, CHUNK),
            ('delta', {'text': 'Answer'}),
            answered('Answer.', cited(6, 'source-a')),
        ]
    ]
    chat.api.chat.close_after = 4
    chunks = await chat.turn('question', 'a1')
    assert content(chunks) == 'Answer [1].'
    assert len(chat.mutations()) == 1
    assert chat.bookmark('a1') == {'thread_id': 'thr-1', 'position': 4}
    follow = next(request for request in chat.api.chat.requests if request.url.path.endswith('/events'))
    assert follow.headers['Last-Event-ID'] == '3'


@pytest.mark.asyncio
@pytest.mark.parametrize('state,error', [('idle', False), ('waiting', False), ('halted', True), ('orphaned', True)])
async def test_terminal_state_never_cancels(chat: Chat, state: str, error: bool) -> None:
    chat.api.chat.terminal_state = state
    chunks = await chat.turn('question', 'a1')
    assert any('error' in chunk for chunk in chunks) == error
    assert len(chat.mutations()) == 1
    assert chat.bookmark('a1')['position'] == 3


@pytest.mark.asyncio
async def test_terminal_relay_error_is_safe_and_does_not_cancel(chat: Chat) -> None:
    chat.api.chat.turns = [[('error', {'code': 'service_unavailable', 'detail': 'private prompt and traceback'})]]
    chunks = await chat.turn('question', 'a1')
    assert chunks == [agent_v2._error('service_unavailable')]
    assert len(chat.mutations()) == 1
    assert chat.bookmark('a1')['position'] == 2


@pytest.mark.asyncio
async def test_generator_close_cancels_only_its_own_input(chat: Chat) -> None:
    chat.api.chat.turns = [[('delta', {'text': 'partial'})]]
    chat.api.chat.terminal_state = 'running'
    response = await chat.response('question', 'a1')
    assert isinstance(response, StreamingResponse)
    assert 'partial' in await asyncio.wait_for(anext(response.body_iterator), timeout=2)
    await asyncio.wait_for(response.body_iterator.aclose(), timeout=2)
    assert chat.mutations()[-1] == ('/v1/chat/threads/thr-1/cancel', {'input': 2})
    assert chat.api.chat.threads['thr-1']['state'] == 'idle'


@pytest.mark.asyncio
async def test_late_disconnect_guard_does_not_cancel_a_newer_turn(chat: Chat) -> None:
    chat.api.chat.turns = [[('delta', {'text': 'partial'})]]
    response = await chat.response('first', 'a1')
    assert isinstance(response, StreamingResponse)
    await asyncio.wait_for(anext(response.body_iterator), timeout=2)
    await chat.turn('second', 'a2', 'a1')
    chat.api.chat.threads['thr-1']['state'] = 'running'
    await asyncio.wait_for(response.body_iterator.aclose(), timeout=2)
    assert chat.mutations()[-1] == ('/v1/chat/threads/thr-1/cancel', {'input': 2})
    assert chat.api.chat.threads['thr-1']['state'] == 'running'


@pytest.mark.asyncio
async def test_non_streaming_still_sends_one_input(chat: Chat) -> None:
    chat.api.chat.turns = [[('model_output', {'content': 'Answer', 'reasoning': 'Reason'})]]
    result = await chat.response('question', 'a1', stream=False)
    assert result['choices'][0]['message'] == {'role': 'assistant', 'content': 'Answer', 'reasoning_content': 'Reason'}
    assert len(chat.mutations()) == 1
    assert chat.bookmark('a1')['position'] == 3


@pytest.mark.asyncio
async def test_reasoning_deltas_arrive_before_answer_without_durable_duplication(chat: Chat) -> None:
    chat.api.chat.turns = [
        [
            ('reasoning_delta', {'text': 'Think '}),
            ('reasoning_delta', {'text': 'first.'}),
            ('delta', {'text': 'Answer'}),
            ('model_output', {'content': 'Answer', 'reasoning': 'Think first.'}),
        ]
    ]
    response = await chat.response('question', 'a1')
    assert isinstance(response, StreamingResponse)
    async with asyncio.timeout(8):
        for text in ('Think ', 'first.'):
            raw = await anext(response.body_iterator)
            chunk = json.loads(raw.removeprefix('data: '))
            assert chunk['choices'][0]['delta'] == {'reasoning_content': text}
            assert chat.bookmark('a1')['position'] == 2
        remaining = ''.join([part async for part in response.body_iterator])
    assert 'reasoning_content' not in remaining
    assert 'Answer' in remaining
    assert remaining.endswith('data: [DONE]\n\n')
    assert [event['type'] for event in chat.api.chat.threads['thr-1']['events']] == ['opened', 'input', 'model_output']


@pytest.mark.asyncio
@pytest.mark.parametrize('close_after', [None, 3])
@pytest.mark.parametrize('partial', ['', 'Think '])
async def test_durable_reasoning_emits_only_missing_suffix(chat: Chat, close_after: int | None, partial: str) -> None:
    chat.api.chat.turns = [
        [
            *([('reasoning_delta', {'text': partial})] if partial else []),
            ('model_output', {'content': 'Answer', 'reasoning': 'Think first.'}),
        ]
    ]
    chat.api.chat.close_after = close_after
    chunks = await chat.turn('question', 'a1')
    assert content(chunks, 'reasoning_content') == 'Think first.'
    assert content(chunks) == 'Answer'
    assert len(chat.mutations()) == 1


@pytest.mark.asyncio
async def test_reasoning_mismatch_warns_without_cancelling_and_resets_per_output(
    chat: Chat, caplog: pytest.LogCaptureFixture
) -> None:
    chat.api.chat.turns = [
        [
            ('reasoning_delta', {'text': 'Streamed thought'}),
            ('model_output', {'content': 'First ', 'reasoning': 'Revised thought'}),
            ('reasoning_delta', {'text': 'Next '}),
            ('model_output', {'content': 'answer', 'reasoning': 'Next thought'}),
        ]
    ]
    chunks = await chat.turn('question', 'a1')
    assert content(chunks, 'reasoning_content') == 'Streamed thoughtNext thought'
    assert content(chunks) == 'First answer'
    assert not any('error' in chunk for chunk in chunks)
    assert len(chat.mutations()) == 1
    warnings = [record for record in caplog.records if 'reasoning disagrees' in record.message]
    assert len(warnings) == 1
    assert warnings[0].thread_id == 'thr-1'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'name,generic',
    [
        ('search', {'description': 'Searching the knowledge base…'}),
        ('calculate', {'description': 'Running {{tool}}…', 'tool': 'calculate'}),
    ],
)
@pytest.mark.parametrize(
    'kind,payload',
    [
        ('tool_output', {'call_id': 'c1', 'data': 'result'}),
        ('tool_output', {'call_id': 'c1', 'error': 'failed'}),
        ('effect_result', {'intent_seq': 4, 'data': 'result'}),
        ('effect_result', {'intent_seq': 4, 'error': 'failed'}),
        ('delta', {'text': 'Answer'}),
        ('reasoning_delta', {'text': 'Think'}),
        ('model_output', {'content': 'Answer'}),
        ('failure', {'message': 'failed'}),
    ],
)
async def test_a_tool_call_shows_running_then_done_once_the_model_moves_on_from_its_output(
    name: str, generic: dict, kind: str, payload: dict
) -> None:
    turn = agent_v2.AgentTurn(AsyncMock(), {}, 'owui:user:alice')
    turn.emitter = AsyncMock()
    status = {'type': 'status', 'data': {'action': name, **generic, 'call_id': 'c1', 'done': False}}
    async with asyncio.timeout(2):
        started = await render(
            turn,
            ChatEvent(
                'model_output',
                {'stream': 'root', 'payload': {'content': '', 'tool_calls': [{'id': 'c1', 'name': name}]}},
            ),
        )
        assert [call.args[0] for call in turn.emitter.call_args_list] == [status]
        data = payload if kind.endswith('delta') else {'stream': 'root', 'payload': payload}
        ended = await render(turn, ChatEvent(kind, data))
        assert [call.args[0] for call in turn.emitter.call_args_list] == [status]
        await render(turn, ChatEvent('delta', {'text': 'Answer'}))
    answered_call = kind == 'tool_output'
    ended_status = {'type': 'status', 'data': {**status['data'], 'done': True}}
    if answered_call and payload.get('error'):
        ended_status['data'].update(description='Could not run {{tool}}', tool=name)
    shown = [call.args[0] for call in turn.emitter.call_args_list if call.args[0]['data'].get('action') != 'summary']
    assert shown == [status] + [ended_status] * answered_call
    assert '<details type="tool_calls"' in content(started)
    assert '<details type="tool_calls"' not in content(ended)


@pytest.mark.asyncio
async def test_a_call_shows_done_once_the_model_thinks_again() -> None:
    turn = agent_v2.AgentTurn(AsyncMock(), {}, 'owui:user:alice')
    turn.emitter = AsyncMock()
    calls = {'content': '', 'tool_calls': [{'id': 'c1', 'name': 'search'}]}
    async with asyncio.timeout(2):
        await render(turn, ChatEvent('model_output', {'stream': 'root', 'payload': calls}))
        await render(turn, ChatEvent('tool_output', {'stream': 'root', 'payload': {'call_id': 'c1'}}))
        await render(turn, ChatEvent('reasoning_delta', {'text': 'Think'}))
    assert [
        (call.args[0]['data']['call_id'], call.args[0]['data']['done']) for call in turn.emitter.call_args_list
    ] == [
        ('c1', False),
        ('c1', True),
    ]


@pytest.mark.asyncio
async def test_a_summary_of_the_conversation_shows_running_then_done_when_it_lands() -> None:
    turn = agent_v2.AgentTurn(AsyncMock(), {}, 'owui:user:alice')
    turn.emitter = AsyncMock()
    turn.tool_statuses = {
        'compaction': {
            'running': {'template': 'Summarising...', 'params': {}},
            'done': {'template': 'Summarised', 'params': {}},
        }
    }
    async with asyncio.timeout(2):
        started = await render(turn, ChatEvent('compacting', {}))
        ended = await render(turn, ChatEvent('compaction', {'stream': 'root', 'payload': {'summary': 'kort'}}))
        # A finished call shows done once the model moves on.
        await render(turn, ChatEvent('model_output', {'stream': 'root', 'payload': {'content': ''}}))
    shown = [call.args[0]['data'] for call in turn.emitter.call_args_list]
    assert [(status['action'], status['description'], status['done']) for status in shown] == [
        ('compaction', 'Summarising...', False),
        ('compaction', 'Summarised', True),
    ]
    assert shown[0]['call_id'] == shown[1]['call_id']
    assert '<details type="tool_calls"' in content(started)
    assert '<details type="tool_calls"' not in content(ended)
    assert 'kort' not in content(ended)


@pytest.mark.asyncio
async def test_calls_the_budget_stopped_end_before_the_answer() -> None:
    turn = agent_v2.AgentTurn(AsyncMock(), {}, 'owui:user:alice')
    turn.emitter = AsyncMock()
    calls = [{'id': 'c1', 'name': 'search'}, {'id': 'c2', 'name': 'calculate'}]
    async with asyncio.timeout(2):
        started = await render(
            turn, ChatEvent('model_output', {'stream': 'root', 'payload': {'content': '', 'tool_calls': calls}})
        )
        await render(turn, ChatEvent('tool_output', {'stream': 'root', 'payload': {'call_id': 'c1'}}))
        stopped = await render(
            turn, ChatEvent('budget_exceeded', {'stream': 'root', 'payload': {'count': 3, 'cap': 3}})
        )
        await render(turn, ChatEvent('model_output', {'stream': 'root', 'payload': {'content': ''}}))
    assert '<details type="tool_calls" done="true" name="calculate">' in content(started)
    assert not stopped
    shown = [call.args[0]['data'] for call in turn.emitter.call_args_list]
    assert [(status['call_id'], status['done']) for status in shown] == [
        ('c1', False),
        ('c2', False),
        ('c1', True),
        ('c2', True),
    ]
    assert not turn.running


@pytest.mark.asyncio
async def test_parallel_tools_each_show_once() -> None:
    turn = agent_v2.AgentTurn(AsyncMock(), {}, 'owui:user:alice')
    turn.emitter = AsyncMock()
    async with asyncio.timeout(2):
        await render(
            turn,
            ChatEvent(
                'model_output',
                {
                    'stream': 'root',
                    'payload': {
                        'content': '',
                        'tool_calls': [{'id': 'c1', 'name': 'search'}, {'id': 'c2', 'name': 'calculate'}],
                    },
                },
            ),
        )
        for call_id in ['c2', 'c1']:
            await render(turn, ChatEvent('tool_output', {'stream': 'root', 'payload': {'call_id': call_id}}))
        await render(turn, ChatEvent('model_output', {'stream': 'root', 'payload': {'content': ''}}))
    search = {'action': 'search', 'description': 'Searching the knowledge base…', 'call_id': 'c1', 'done': False}
    calculate = {
        'action': 'calculate',
        'description': 'Running {{tool}}…',
        'tool': 'calculate',
        'call_id': 'c2',
        'done': False,
    }
    ended = [{**calculate, 'done': True}, {**search, 'done': True}]
    assert [call.args[0]['data'] for call in turn.emitter.call_args_list] == [search, calculate, *ended]


@pytest.mark.asyncio
async def test_web_calls_carry_the_addresses_they_found_and_read() -> None:
    turn = agent_v2.AgentTurn(AsyncMock(), {}, 'owui:user:alice')
    turn.emitter = AsyncMock()
    link = {'type': 'link', 'id': 'https://soev.ai/', 'url': 'https://soev.ai/', 'title': 'soev.ai'}
    other = {'type': 'link', 'id': 'https://gradient-ds.com/', 'url': 'https://gradient-ds.com/'}
    calls = [('s1', 'web_search', {'query': 'soev'}, [link, other, link]), ('f1', 'fetch', {'link': link['id']}, [])]
    async with asyncio.timeout(2):
        for call_id, name, arguments, elements in calls:
            tool_call = {'id': call_id, 'name': name, 'arguments': arguments}
            await render(
                turn,
                ChatEvent('model_output', {'stream': 'root', 'payload': {'content': '', 'tool_calls': [tool_call]}}),
            )
            await render(
                turn,
                ChatEvent('tool_output', {'stream': 'root', 'payload': {'call_id': call_id, 'elements': elements}}),
            )
        # A finished call shows done once the model moves on.
        await render(turn, ChatEvent('model_output', {'stream': 'root', 'payload': {'content': ''}}))
    shown = [call.args[0]['data'].get('items') for call in turn.emitter.call_args_list]
    found = [
        {'link': 'https://soev.ai/', 'title': 'soev.ai'},
        {'link': 'https://gradient-ds.com/', 'title': 'https://gradient-ds.com/'},
    ]
    assert shown == [None, found, found[:1], None]


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [False, True])
async def test_turn_end_closes_with_the_summary_or_the_failure(chat: Chat, failure: bool) -> None:
    chat.api.chat.turns = [
        [
            ('model_output', {'content': '', 'tool_calls': [{'id': 'c1', 'name': 'search'}]}),
            *([('error', {'code': 'service_unavailable'})] if failure else []),
        ]
    ]
    chunks = await chat.turn('question', 'a1')
    assert any('error' in chunk for chunk in chunks) == failure
    assert [event['data'] for event in chat.socket if event['type'] == 'status'] == [
        {'action': 'search', 'description': 'Searching the knowledge base…', 'call_id': 'c1', 'done': False},
        (
            {'description': 'error', 'done': True}
            if failure
            else {'action': 'summary', 'description': '1 tool called in less than a second', 'done': True}
        ),
    ]


@pytest.mark.asyncio
async def test_explicit_invalid_new_input_never_falls_back_to_history(chat: Chat) -> None:
    with pytest.raises(ValueError, match='user_message'):
        await chat.response(None, 'a1')
    assert not chat.api.chat.requests


@pytest.mark.asyncio
@pytest.mark.parametrize('stream', [False, True])
@pytest.mark.parametrize('text', ['last question', [{'type': 'text', 'text': 'last question'}]])
async def test_missing_user_message_sends_only_the_last_user_message(chat: Chat, stream: bool, text: Any) -> None:
    messages = [
        {'role': 'system', 'content': 'private system history'},
        {'role': 'user', 'content': 'old question'},
        {'role': 'assistant', 'content': 'old answer'},
        {'role': 'user', 'content': text},
        {'role': 'assistant', 'content': 'partial answer'},
    ]
    kwargs = {'user_message': None, 'form_data': {'messages': messages}}
    if stream:
        assert content(await chat.turn(None, 'a1', **kwargs)) == 'Answer: last question'
    else:
        result = await chat.response(None, 'a1', stream=False, **kwargs)
        assert result['choices'][0]['message']['content'] == 'Answer: last question'
    assert chat.mutations() == [
        (
            '/v1/chat/threads',
            {
                'input': {'text': 'last question', 'knowledge': [], 'documents': 'off', 'tools': WEB_SEARCH_OFF},
                'agent': 'test',
                'model': 'llm',
            },
        )
    ]


@pytest.mark.parametrize(
    'messages',
    [
        [],
        [{'role': 'assistant', 'content': 'not user text'}],
        [{'role': 'user', 'content': 'old text'}, {'role': 'user', 'content': None}],
    ],
)
def test_missing_or_invalid_last_user_message_is_rejected(messages: list[dict]) -> None:
    with pytest.raises(ValueError, match='A v2 agent turn requires user_message text'):
        agent_v2._input_text({}, {'messages': messages})


@pytest.mark.asyncio
@pytest.mark.parametrize('meta', [{}, {'runtime': 'v1'}])
async def test_legacy_agent_keeps_existing_route(chat: Chat, monkeypatch: pytest.MonkeyPatch, meta: dict) -> None:
    monkeypatch.setattr(AgentConfigs, 'get_agent_config_by_id', AsyncMock(return_value=SimpleNamespace(meta=meta)))
    legacy = AsyncMock(return_value={'legacy': True})
    monkeypatch.setattr(agent, '_call_agent_api_non_streaming', legacy)
    assert await chat.response('question', 'a1', stream=False) == {'legacy': True}
    assert legacy.call_args.args[0]['messages'][0]['content'] == 'HISTORY MUST NOT LEAVE'
    assert not chat.api.chat.requests


class InterruptedStream(httpx.AsyncByteStream):
    def __init__(self, content: bytes, fail: bool) -> None:
        self.content, self.fail = content, fail
        self.waiting = asyncio.Event()
        self.closed = False

    async def __aiter__(self) -> Any:
        yield self.content
        self.waiting.set()
        if self.fail:
            raise httpx.ReadError('private transport detail')
        await asyncio.Event().wait()

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
@pytest.mark.parametrize('fail', [False, True])
async def test_explicit_stop_and_broken_transport_cancel_the_submitted_input(
    chat: Chat, monkeypatch: pytest.MonkeyPatch, fail: bool
) -> None:
    chat.api.chat.turns = [
        [('model_output', {'content': '', 'tool_calls': [{'id': 'c1', 'name': 'search', 'arguments': {}}]})]
    ]
    chat.api.chat.terminal_state = 'running'
    original = chat.api.chat._response
    streams = []

    def response(*args: Any, **kwargs: Any) -> httpx.Response:
        result = original(*args, **kwargs)
        stream = InterruptedStream(result.content.rsplit(b'event: status', 1)[0], fail)
        streams.append(stream)
        return httpx.Response(result.status_code, stream=stream, headers=result.headers)

    monkeypatch.setattr(chat.api.chat, '_response', response)
    if fail:
        chunks = await chat.turn('question', 'a1')
        assert chunks[-1] == agent_v2._error('service_unavailable')
    else:
        task = asyncio.create_task(chat.turn('question', 'a1'))
        async with asyncio.timeout(2):
            while not streams:
                await asyncio.sleep(0)
        await asyncio.wait_for(streams[0].waiting.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=2)
    assert chat.mutations()[-1] == ('/v1/chat/threads/thr-1/cancel', {'input': 2})
    assert streams[0].closed
    assert chat.api.chat.threads['thr-1']['state'] == 'idle'
    assert [event['data'] for event in chat.socket if event['type'] == 'status'] == [
        {'action': 'search', 'description': 'Searching the knowledge base…', 'call_id': 'c1', 'done': False},
    ]


@pytest.mark.asyncio
async def test_the_turn_after_a_stop_continues_without_rerunning_the_stopped_answer(
    chat: Chat, monkeypatch: pytest.MonkeyPatch
) -> None:
    chat.api.chat.terminal_state = 'running'
    original = chat.api.chat._response
    streams = []

    def response(*args: Any, **kwargs: Any) -> httpx.Response:
        result = original(*args, **kwargs)
        stream = InterruptedStream(result.content.rsplit(b'event: status', 1)[0], False)
        streams.append(stream)
        return httpx.Response(result.status_code, stream=stream, headers=result.headers)

    monkeypatch.setattr(chat.api.chat, '_response', response)
    task = asyncio.create_task(chat.turn('long essay', 'a1'))
    async with asyncio.timeout(2):
        while not streams:
            await asyncio.sleep(0)
    await asyncio.wait_for(streams[0].waiting.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=2)
    monkeypatch.setattr(chat.api.chat, '_response', original)
    chat.api.chat.terminal_state = 'idle'
    stopped = len(chat.mutations())
    cancelled = next(e['position'] for e in chat.api.chat.threads['thr-1']['events'] if e['type'] == 'cancelled')
    assert chat.bookmark('a1') == {'thread_id': 'thr-1', 'position': cancelled}

    chunks = await chat.turn('what was I asking?', 'a2', 'a1')

    assert content(chunks) == 'Answer: what was I asking?'
    assert chat.mutations()[stopped:] == [
        (
            '/v1/chat/threads/thr-1/inputs',
            {
                'input': {'text': 'what was I asking?', 'knowledge': [], 'documents': 'off', 'tools': WEB_SEARCH_OFF},
                'model': 'llm',
            },
        ),
    ]


@pytest.mark.asyncio
async def test_legacy_stream_preserves_socket_events_and_message_extras(
    chat: Chat, monkeypatch: pytest.MonkeyPatch
) -> None:
    events = [
        agent.SSEEvent(kind, {'value': kind})
        for kind in ('status', 'source', 'present_ui', 'subagent', 'context_usage', 'panel_filter')
    ]
    emitted = []

    async def stream(*args: Any) -> Any:
        for event in events:
            yield event
        yield agent.SSEEvent('data', {'choices': [{'delta': {'content': 'legacy answer'}}]})
        yield agent.SSEEvent('done', '[DONE]')

    async def emit(event: dict) -> None:
        emitted.append(event)

    monkeypatch.setattr(agent, 'stream_agent_response', stream)
    monkeypatch.setattr(agent, 'get_event_emitter', AsyncMock(return_value=emit))
    response = agent._build_streaming_response(None, {}, {'chat_id': 'chat', 'message_id': 'a1'})
    async with asyncio.timeout(8):
        raw = ''.join([part async for part in response.body_iterator])
    assert raw == 'data: {"choices": [{"delta": {"content": "legacy answer"}}]}\n\ndata: [DONE]\n\n'
    assert emitted == [{'type': event.event_type, 'data': event.data} for event in events]
    assert chat.messages['chat', 'a1'] == {
        'subagents': [{'value': 'subagent'}],
        'contextUsage': {'value': 'context_usage'},
    }


@pytest.mark.asyncio
async def test_split_body_sends_picked_llm(chat: Chat) -> None:
    """Split metadata leaves LLM resolution to the explicit body model."""
    await chat.turn(
        'split turn',
        'a1',
        form_data={'model': 'picked-llm'},
        model={'id': 'picked-llm', 'assistant_id': 'assistant', 'info': {'id': 'assistant', 'base_model_id': None}},
    )
    body = chat.mutations()[0][1]
    assert body['model'] == 'picked-llm'
    assert 'assistant_id' not in body


@pytest.mark.asyncio
async def test_agent_model_is_default_without_resolved_llm(chat: Chat, monkeypatch: pytest.MonkeyPatch) -> None:
    """Agent configuration supplies a model only when the request resolves none."""
    monkeypatch.setattr(
        AgentConfigs,
        'get_agent_config_by_id',
        AsyncMock(return_value=SimpleNamespace(meta={'runtime': 'v2', 'model': 'agent-default'})),
    )
    await chat.turn('default turn', 'a1', form_data={'model': ''}, model={})
    assert chat.mutations()[0][1]['model'] == 'agent-default'


@pytest.mark.asyncio
async def test_a_follow_up_continues_the_thread_in_real_chat_storage(
    chat_http: FakeSoevApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    stored = Chat(chat_http)
    monkeypatch.setattr(env, 'AGENT_API_RUNTIME', 'v1')
    monkeypatch.setattr(
        AgentConfigs, 'get_agent_config_by_id', AsyncMock(return_value=SimpleNamespace(meta={'runtime': 'v2'}))
    )
    monkeypatch.setattr(agent_v2, 'get_event_emitter', AsyncMock(return_value=AsyncMock()))
    chat_id = str(uuid4())
    await Chats.insert_new_chat(chat_id, 'alice', ChatForm(chat={'title': 'Chat', 'history': {'messages': {}}}))
    for message_id, parent in [('q1', None), ('a1', 'q1'), ('q2', 'a1'), ('a2', 'q2')]:
        role = 'user' if message_id.startswith('q') else 'assistant'
        await Chats.upsert_message_to_chat_by_id_and_message_id(
            chat_id, message_id, {'role': role, 'parentId': parent, 'content': message_id, 'meta': {'kept': message_id}}
        )

    await stored.turn('first', 'a1', None, chat_id=chat_id)
    await stored.turn('second', 'a2', 'a1', chat_id=chat_id)

    assert [path for path, _ in stored.mutations()] == ['/v1/chat/threads', '/v1/chat/threads/thr-1/inputs']
    answer = await Chats.get_message_by_id_and_message_id(chat_id, 'a2')
    assert answer['meta'] == {'kept': 'a2', 'agent_v2': {'thread_id': 'thr-1', 'position': 5}}


@pytest.fixture
def stored(chat: Chat, monkeypatch: pytest.MonkeyPatch) -> Chat:
    """ChatMessages over the fixture's messages, as the deletion paths read them."""

    def rows(chat_id: str | None = None) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(chat_id=key[0], meta=message.get('meta'))
            for key, message in chat.messages.items()
            if chat_id is None or key[0] == chat_id
        ]

    async def by_chat(chat_id: str) -> list[SimpleNamespace]:
        return rows(chat_id)

    async def by_user(user_id: str, skip: int = 0, limit: int = 50) -> list[SimpleNamespace]:
        return rows()[skip : skip + limit]

    monkeypatch.setattr(ChatMessages, 'get_messages_by_chat_id', by_chat)
    monkeypatch.setattr(ChatMessages, 'get_messages_by_user_id', by_user)
    return chat


@pytest.mark.asyncio
async def test_deleting_a_chat_deletes_its_threads_and_forks_but_not_a_clones(stored: Chat) -> None:
    await stored.turn('first', 'a1')
    await stored.turn('second', 'a2', 'a1')
    await stored.turn('second again', 'a2b', 'a1')
    await stored.turn('in the other chat', 'b1', chat_id='other')
    stored.messages['clone', 'a1'] = copy.deepcopy(stored.messages['chat', 'a1'])

    assert await agent_threads.delete_chat_threads('alice', 'chat') == []

    assert sorted(stored.api.chat.threads) == ['thr-1', 'thr-3']
    assert [request.url.path for request in stored.api.chat.requests if request.method == 'DELETE'] == [
        '/v1/chat/threads/thr-2'
    ]


@pytest.mark.asyncio
async def test_a_thread_already_gone_counts_as_deleted_and_an_outage_keeps_it(
    stored: Chat, monkeypatch: pytest.MonkeyPatch
) -> None:
    await stored.turn('first', 'a1')
    await stored.turn('elsewhere', 'b1', chat_id='later')
    del stored.api.chat.threads['thr-1']
    assert await agent_threads.delete_chat_threads('alice', 'chat') == []

    async def unavailable(*args: Any, **kwargs: Any) -> None:
        raise SoevApiError(503, 'service_unavailable', 'down')

    monkeypatch.setattr(SoevClient, 'chat_delete', unavailable)
    assert await agent_threads.delete_chat_threads('alice', 'later') == ['thr-2']


@pytest.mark.asyncio
async def test_deleting_messages_deletes_only_the_threads_no_message_still_bookmarks(stored: Chat) -> None:
    await stored.turn('first', 'a1')
    await stored.turn('second', 'a2', 'a1')
    await stored.turn('first again', 'r1')
    before = await agent_threads.chat_thread_ids('chat')
    del stored.messages['chat', 'r1']
    del stored.messages['chat', 'a2']

    assert await agent_threads.delete_released_threads('alice', before) == []

    assert sorted(stored.api.chat.threads) == ['thr-1']


TEMPORARY = 'temporary:sid-1:chat'


@pytest.fixture
def temporary(chat: Chat, monkeypatch: pytest.MonkeyPatch) -> Chat:
    """Connected sockets sid-1 and sid-2, and no temporary chat bookmarks yet."""
    monkeypatch.setattr(socket_main, 'SESSION_POOL', {'sid-1': {}, 'sid-2': {}})
    monkeypatch.setattr(socket_main, 'TEMPORARY_AGENT_THREADS', {})
    return chat


@pytest.mark.asyncio
async def test_a_temporary_chat_continues_its_thread_without_storing_messages(temporary: Chat) -> None:
    await temporary.turn('first', 'a1', chat_id=TEMPORARY)
    chunks = await temporary.turn('second', 'a2', 'a1', chat_id=TEMPORARY)

    assert content(chunks) == 'Answer: second'
    assert [path for path, _ in temporary.mutations()] == ['/v1/chat/threads', '/v1/chat/threads/thr-1/inputs']
    assert not temporary.messages
    assert socket_main.TEMPORARY_AGENT_THREADS[TEMPORARY]['bookmarks']['a2'] == {'thread_id': 'thr-1', 'position': 5}


@pytest.mark.asyncio
async def test_a_closed_socket_deletes_its_temporary_chats_threads(temporary: Chat) -> None:
    await temporary.turn('first', 'a1', chat_id=TEMPORARY)
    await temporary.turn('regenerated', 'a1b', chat_id=TEMPORARY)
    await temporary.turn('other tab', 'b1', chat_id='temporary:sid-2:chat')

    await socket_main._release_temporary_agent_threads(['sid-1'])

    assert sorted(temporary.api.chat.threads) == ['thr-3']
    assert list(socket_main.TEMPORARY_AGENT_THREADS) == ['temporary:sid-2:chat']


@pytest.mark.asyncio
async def test_a_temporary_turn_ending_after_its_socket_closed_deletes_its_thread(temporary: Chat) -> None:
    await temporary.turn('first', 'a1', chat_id='temporary:sid-gone:chat')

    assert temporary.api.chat.threads == {}
    assert socket_main.TEMPORARY_AGENT_THREADS == {}


QUERY = {'query': 'argument.query', 'collection_name': 'knowledge.knowledge_base'}
STATUSES = {
    'search': {
        'running': {
            'template': 'Searching {{collection_name}} for "{{query}}"...',
            'fallback': 'Searching all knowledge bases for "{{query}}"...',
            'params': QUERY,
        },
        'done': {
            'template': 'Searched {{collection_name}}: {{passages}} passages in {{documents}} documents',
            'fallback': 'Searched all knowledge bases: {{passages}} passages in {{documents}} documents',
            'params': {**QUERY, 'passages': 'output.count.chunk', 'documents': 'output.count.document'},
        },
    },
    'open_document': {
        'running': {'template': 'Reading {{doc_title}}...', 'params': {'doc_title': 'element.document.title'}},
        'done': {'template': 'Read {{doc_title}}', 'params': {'doc_title': 'output.first.document.title'}},
    },
}


@pytest.fixture
def declared(chat: Chat, monkeypatch: pytest.MonkeyPatch) -> Chat:
    """soev-api declares how search and open_document show; two knowledge bases exist."""
    monkeypatch.setattr(agent_v2, 'TOOL_STATUS_CACHE', {'expires_at': float('inf'), 'statuses': STATUSES})
    seed_collection(chat.api, 'kb-a', 'Contracten')
    seed_collection(chat.api, 'kb-b', 'Notulen')
    return chat


def statuses(chat: Chat) -> list[dict]:
    """The tool statuses, without the turn's closing summary."""
    return [
        event['data'] for event in chat.socket if event['type'] == 'status' and event['data'].get('action') != 'summary'
    ]


def call(name: str, call_id: str = 'c1', **arguments: Any) -> tuple[str, dict]:
    return ('model_output', {'content': '', 'tool_calls': [{'id': call_id, 'name': name, 'arguments': arguments}]})


SEARCHED = [DOCUMENT, CHUNK, chunk('source-b'), document('other', 'Other'), chunk('other-1', 'other')]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'arguments,selected,running,done',
    [
        (
            {'query': 'opzegtermijn', 'knowledge_base': 'kb-b'},
            ['kb-a', 'kb-b'],
            {'description': 'Searching {{collection_name}} for "{{query}}"...', 'collection_name': 'Notulen'},
            {'description': 'Searched {{collection_name}}: {{passages}} passages in {{documents}} documents'},
        ),
        (
            {'query': 'opzegtermijn'},
            ['kb-a'],
            {'description': 'Searching {{collection_name}} for "{{query}}"...', 'collection_name': 'Contracten'},
            {'description': 'Searched {{collection_name}}: {{passages}} passages in {{documents}} documents'},
        ),
        (
            {'query': 'opzegtermijn'},
            ['kb-a', 'kb-b'],
            {'description': 'Searching all knowledge bases for "{{query}}"...'},
            {'description': 'Searched all knowledge bases: {{passages}} passages in {{documents}} documents'},
        ),
    ],
)
async def test_a_search_shows_running_from_the_call_and_done_from_its_output(
    declared: Chat, arguments: dict, selected: list[str], running: dict, done: dict
) -> None:
    declared.api.chat.turns = [
        [call('search', **arguments), found(*SEARCHED, call_id='c1'), ('model_output', {'content': 'done'})]
    ]
    chunks = await declared.turn('q', 'a1', files=[{'type': 'collection', 'id': key} for key in selected])

    status = {'action': 'search', 'call_id': 'c1', 'done': False}
    named = {'collection_name': running['collection_name']} if 'collection_name' in running else {}
    counted = {**named, 'passages': '3', 'documents': '2'}
    assert statuses(declared) == [
        {**status, **running, 'query': 'opzegtermijn'},
        {**status, **done, **counted, 'done': True},
    ]
    summary = running['description'].replace('{{query}}', 'opzegtermijn')
    summary = summary.replace('{{collection_name}}', named.get('collection_name', ''))
    assert content(chunks).count('<details type="tool_calls"') == 1
    assert f'<summary>{html.escape(summary)}</summary>' in content(chunks)


@pytest.mark.asyncio
async def test_opening_a_document_names_it_by_the_title_already_received(declared: Chat) -> None:
    policy = document('doc-1', 'Leave policy')
    read = {'type': 'document-text', 'id': 'doc-1#0-4', 'ref': 'doc-1', 'text': 'body', 'start': 0, 'end': 4}
    declared.api.chat.turns = [
        [
            call('list_documents', 'c0'),
            found(policy, call_id='c0'),
            call('open_document', document='doc-1'),
            found(policy, {**read, 'length': 4}, call_id='c1'),
            ('model_output', {'content': 'done'}),
        ]
    ]
    chunks = await declared.turn('q', 'a1', files=[{'type': 'collection', 'id': 'kb-a'}])

    opening = {'action': 'open_document', 'call_id': 'c1', 'done': False, 'doc_title': 'Leave policy'}
    assert [status for status in statuses(declared) if status['action'] == 'open_document'] == [
        {**opening, 'description': 'Reading {{doc_title}}...'},
        {**opening, 'description': 'Read {{doc_title}}', 'done': True},
    ]
    assert '<summary>Reading Leave policy...</summary>' in content(chunks)


@pytest.mark.asyncio
async def test_a_tool_without_a_declared_status_shows_the_generic_line(declared: Chat) -> None:
    declared.api.chat.turns = [
        [call('list_documents'), ('tool_output', {'call_id': 'c1'}), ('model_output', {'content': 'done'})]
    ]
    await declared.turn('q', 'a1')

    generic = {
        'action': 'list_documents',
        'description': 'Running {{tool}}…',
        'tool': 'list_documents',
        'call_id': 'c1',
        'done': False,
    }
    assert statuses(declared) == [generic, {**generic, 'done': True}]


@pytest.mark.asyncio
async def test_the_summary_settles_the_tools_once_the_answer_starts(declared: Chat) -> None:
    declared.api.chat.turns = [
        [
            call('list_documents', 'c1'),
            ('tool_output', {'call_id': 'c1'}),
            ('delta', {'text': 'Looking further.'}),
            call('list_documents', 'c2'),
            ('tool_output', {'call_id': 'c2'}),
            ('delta', {'text': 'Answer'}),
            answered('Answer'),
        ]
    ]
    await declared.turn('q', 'a1')

    shown = [event['data'] for event in declared.socket if event['type'] == 'status']
    order = [status.get('call_id') or status['description'] for status in shown]
    assert order == [
        'c1',
        'c1',
        '1 tool called in less than a second',
        'c2',
        'c2',
        '2 tools called in less than a second',
    ]


@pytest.mark.parametrize(
    'count,seconds,language,summary',
    [
        (1, 0.4, 'en-US', '1 tool called in less than a second'),
        (3, 24, None, '3 tools called in 24 seconds'),
        (2, 125, 'nl-NL', '2 tools aangeroepen in 2 minuten en 5 seconden'),
        (1, 61, 'nl', '1 tool aangeroepen in 1 minuut en 1 seconde'),
        (4, 120, 'en', '4 tools called in 2 minutes'),
    ],
)
def test_the_closing_summary_reads_as_the_v1_agents_wrote_it(
    count: int, seconds: float, language: str | None, summary: str
) -> None:
    assert agent_v2._summary(count, seconds, language) == summary


@pytest.mark.asyncio
async def test_a_turn_that_called_tools_closes_with_a_summary_in_the_ui_language(declared: Chat) -> None:
    declared.api.chat.turns = [
        [call('list_documents'), ('tool_output', {'call_id': 'c1'}), ('model_output', {'content': 'done'})]
    ]
    await declared.turn('q', 'a1', user_language='nl-NL')

    closing = [event['data'] for event in declared.socket if event['type'] == 'status'][-1]
    assert closing == {'action': 'summary', 'description': '1 tool aangeroepen in minder dan een seconde', 'done': True}


@pytest.mark.parametrize(
    'output,expected',
    [
        ({'error': 'not_readable'}, 'Could not open document'),
        ({'error': 'processing_failed'}, 'Could not open document'),
        ({'error': 'connection_required', 'elements': [{'type': 'action-required'}]}, 'Could not open document'),
        ({'elements': [{'type': 'document', 'id': 'doc'}]}, 'Opened document'),
        ({'attachments': [{'status': 'processing'}]}, 'Opened document'),
        ({'attachments': [{'status': 'ready'}], 'text': 'Another hit failed'}, 'Opened document'),
        ({'text': 'Already attached; still processing.'}, 'Opened document'),
        ({'error': 'failed', 'elements': [{'type': 'document'}]}, 'Could not open document'),
    ],
)
def test_attach_summary_reflects_the_agent_outcome(output: dict, expected: str) -> None:
    turn = agent_v2.AgentTurn(None, {}, 'owui:user:alice')
    turn.tool_statuses = {
        'attach_live_document': {
            'done': {'template': 'Opened document'},
            'failed': {'template': 'Could not open document'},
        }
    }
    turn.running['attach'] = ('attach_live_document', {})
    turn.end_tool({'call_id': 'attach', **output})
    assert turn.settling == [
        {'action': 'attach_live_document', 'description': expected, 'call_id': 'attach', 'done': True}
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize('resume', [False, True])
async def test_attached_batch_merges_files_once_and_skips_individual_mismatches(monkeypatch, resume):
    records = [
        {
            'source_id': source,
            'collection_key': 'owui-attachments-alice',
            'job_id': 'job-' + source,
            'name': source + '.pdf',
            'web_url': 'https://files.test/' + source,
            'provider': 'onedrive',
            'attached_by': 'agent',
            'provider_ref': {'grant_id': 'g', 'drive_id': 'd', 'item_id': source, 'etag': 'v1'},
            'status': status,
            'content_type': 'application/pdf',
        }
        for source, status in [('ready', 'ready'), ('mismatch', 'ready'), ('pending', 'processing')]
    ]

    async def register(user_id, record):
        if record['source_id'] == 'mismatch':
            raise agent_v2.live_documents.AttachmentMismatch(403, 'Wrong owner')
        return SimpleNamespace(
            id=record['source_id'],
            filename=record['name'],
            meta={
                'collection_name': record['collection_key'],
                'status': 'completed' if record['status'] == 'ready' else 'processing',
                'source': {'provider': record['provider'], 'ref': record['provider_ref']},
                'web_url': record['web_url'],
                'attached_by': record['attached_by'],
            },
        )

    registration = AsyncMock(side_effect=register)
    monkeypatch.setattr(agent_v2.live_documents, 'register_attachment', registration)
    stored = {'files': [{'id': 'existing', 'name': 'Existing upload'}]}
    get_message = AsyncMock(return_value=stored)
    upsert = AsyncMock()
    monkeypatch.setattr(Chats, 'get_message_by_id_and_message_id', get_message)
    monkeypatch.setattr(Chats, 'upsert_message_to_chat_by_id_and_message_id', upsert)
    payload = {'call_id': 'attach', 'attachments': records, 'elements': [], 'text': 'Attached two files.'}
    event = ChatEvent('attached', {'stream': 'root', 'payload': payload})

    async def stream(*args, **kwargs):
        yield event

    turn = agent_v2.AgentTurn(
        SimpleNamespace(chat_stream=stream),
        {'user_id': 'alice', 'chat_id': 'chat', 'message_id': 'message'},
        'owui:user:alice',
    )
    turn.emitter = AsyncMock()
    turn.thread_id = 'thread'
    turn.running['attach'] = ('attach_live_document', {})
    for replay in range(2):
        if resume:
            await turn.resume()
        else:
            await turn.render_event(event)
        update = upsert.await_args.args[2]
        assert [file['id'] for file in update['files']] == ['existing', 'ready', 'pending']
        assert [file.get('status') for file in update['files'][1:]] == ['uploaded', 'processing']
        assert upsert.await_count == get_message.await_count == turn.emitter.await_count == replay + 1
        turn.emitter.assert_awaited_with({'type': 'chat:message:files', 'data': update})
        stored.update(update)
    assert [call.args for call in registration.await_args_list] == [('alice', record) for record in records] * 2
    if not resume:
        assert len(turn.settling) == 1
        assert turn.settling[0]['description'] != 'Could not open document'


@pytest.mark.parametrize(
    'hits,titles',
    [
        ('first', 'Budget'),
        (['first', 'unknown', 'second'], 'Budget, Minutes'),
        (['second', 'first'], 'Minutes, Budget'),
        (['unknown', None, {}, 'empty', 'missing'], None),
        ([], None),
        (None, None),
    ],
)
def test_attach_status_binds_titles_from_hit_lists(hits, titles) -> None:
    turn = agent_v2.AgentTurn(None, {}, 'owui:user:alice')
    turn.elements = {
        'first': {'title': 'Budget'},
        'second': {'title': 'Minutes'},
        'empty': {'title': ' '},
        'missing': {},
    }
    turn.tool_statuses = {
        'attach_live_document': {
            'running': {
                'template': 'Opening {{titles}}...',
                'fallback': 'Opening documents...',
                'params': {'titles': 'element.hits.title'},
            },
            'done': {
                'template': 'Opened {{titles}}',
                'fallback': 'Opened documents',
                'params': {'titles': 'element.hits.title'},
            },
        }
    }
    arguments = {'hits': hits}
    params = {'titles': titles} if titles else {}
    assert turn.tool_status('attach_live_document', arguments) == {
        'action': 'attach_live_document',
        'description': 'Opening {{titles}}...' if titles else 'Opening documents...',
        **params,
    }
    turn.running['attach'] = ('attach_live_document', arguments)
    turn.end_tool({'call_id': 'attach', 'elements': [{'type': 'document', 'id': 'opened'}]})
    assert turn.settling == [
        {
            'action': 'attach_live_document',
            'description': 'Opened {{titles}}' if titles else 'Opened documents',
            **params,
            'call_id': 'attach',
            'done': True,
        }
    ]


@pytest.mark.parametrize('state', ['off', 'auto', 'required', 'unexpected', True, None])
@pytest.mark.parametrize('allowed', [False, True])
def test_mail_states_are_gated_on_the_server(state, allowed):
    tools = agent_v2._tools({'features': {'live_mail': state}}, False, False, allowed)['tools']
    expected = state if allowed and state in ('auto', 'required') else 'off'
    assert tools['search_mail'] == tools['read_mail'] == expected
    assert tools['search_live_documents'] == 'off'


@pytest.mark.asyncio
async def test_mail_sources_link_to_outlook_without_creating_files(chat: Chat):
    card = {
        'id': 'mail-ref',
        'type': 'mail-reference',
        'title': 'Budget approved',
        'source_url': 'https://outlook.office.com/mail/id/example',
    }
    text = {'id': 'mail-text', 'type': 'mail-text', 'ref': 'mail-ref', 'text': 'Approved: 42000.'}
    chat.api.chat.turns = [[found(card, text), answered('Approved', cited(8, 'mail-text', 'mail-ref'))]]
    assert content(await chat.turn('What was approved?', 'a1')) == 'Approved [1]'
    source = next(event['data'] for event in chat.socket if event['type'] == 'source')
    assert source['source']['url'] == card['source_url']
    assert source['source']['provider'] == 'outlook_mail'
    assert 'file_id' not in source['metadata'][0]
    assert not any(event['type'] in ('files', 'chat:message:files') for event in chat.socket)
    assert not chat.messages['chat', 'a1'].get('files')


@pytest.mark.asyncio
@pytest.mark.parametrize('success', [True, False])
async def test_read_mail_status_reports_refusals(success):
    turn = agent_v2.AgentTurn(None, {}, 'owui:user:alice')
    turn.tool_statuses = {
        'read_mail': {'done': {'template': 'Read email'}, 'failed': {'template': 'Could not read email'}}
    }
    turn.running['read'] = ('read_mail', {})
    output = (
        {'elements': [{'type': 'mail-text'}]}
        if success
        else {'error': 'not_found', 'text': 'Mail request refused: not_found'}
    )
    turn.end_tool({'call_id': 'read', **output})
    assert turn.settling[0]['description'] == ('Read email' if success else 'Could not read email')


def test_status_params_join_list_arguments():
    turn = agent_v2.AgentTurn(None, {}, 'owui:user:alice')
    declared = {'params': {'keywords': 'argument.keywords'}}
    assert turn.tool_params(declared, {'keywords': ['begroting', ' ', 'fietspad']}, None) == {
        'keywords': 'begroting, fietspad'
    }


@pytest.mark.asyncio
async def test_a_call_shows_running_only_after_the_reasoning_that_led_to_it() -> None:
    turn = agent_v2.AgentTurn(AsyncMock(), {}, 'owui:user:alice')
    turn.emitter = AsyncMock()
    chunks = await turn.render(
        ChatEvent(
            'model_output',
            {
                'stream': 'root',
                'payload': {
                    'content': '',
                    'reasoning': 'Search the files.',
                    'tool_calls': [{'id': 'c1', 'name': 'search_live_documents'}],
                },
            },
        )
    )
    assert any('reasoning_content' in str(chunk) for chunk in chunks)
    assert not turn.emitter.call_args_list
    await turn.start_pending_tools()
    assert turn.emitter.call_args_list[0].args[0]['data']['done'] is False


@pytest.mark.parametrize('matches', [None, 143])
def test_search_mail_status_keeps_order_and_filters(matches):
    turn = agent_v2.AgentTurn(None, {}, 'owui:user:alice')
    turn.tool_statuses = {
        'search_mail': {
            'done': {
                'template': 'Searched your mail for "{{keywords}}": {{matches}} emails',
                'fallback': 'Searched your mail for "{{keywords}}": no emails',
                'params': {'keywords': 'argument.keywords', 'matches': 'output.first.mail-reference.matches'},
            }
        }
    }
    status = turn.tool_status(
        'search_mail',
        {
            'keywords': ['KNB'],
            'order': 'newest',
            'from_addresses': ['@knb.nl'],
            'to_addresses': ['a@example.test'],
            'cc_addresses': ['b@example.test'],
        },
        {'elements': [{'type': 'mail-reference', 'matches': matches}] if matches else []},
    )
    assert '({{options}})' in status['description']
    assert status['options'] == 'newest first; From: @knb.nl; To: a@example.test; Cc: b@example.test'
    assert len(status['mail_options']) == 4
    assert ('no emails' in status['description']) == (matches is None)


def test_plain_mail_status_is_unchanged():
    from open_webui.utils.mail_status import mail_search_status

    status = {'description': 'Searched your mail for "{{keywords}}": no emails', 'keywords': 'KNB'}
    assert mail_search_status(status, {'keywords': ['KNB']}) == status


IMAGE_BYTES = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII='
)
IMAGE_URL = 'data:image/png;base64,' + base64.b64encode(IMAGE_BYTES).decode()
IMAGE_REFERENCE = {
    'id': 'a' * 32,
    'sha256': hashlib.sha256(IMAGE_BYTES).hexdigest(),
    'size': len(IMAGE_BYTES),
    'media_type': 'image/png',
    'name': 'photo.png',
}


@pytest.fixture
def image_upload(monkeypatch):
    upload = AsyncMock(return_value=IMAGE_REFERENCE)
    monkeypatch.setattr(SoevClient, 'post_bytes', upload)
    return upload


@pytest.fixture
def saved_image(tmp_path, monkeypatch):
    path = tmp_path / 'photo.png'
    path.write_bytes(IMAGE_BYTES)
    file = SimpleNamespace(id='saved-image', user_id='alice', filename='photo.png', path=str(path), meta={})
    file.lookup = AsyncMock(return_value=file)
    monkeypatch.setattr(agent_v2.Files, 'get_file_by_id', file.lookup)
    monkeypatch.setattr(
        agent_v2.Users, 'get_user_by_id', AsyncMock(return_value=SimpleNamespace(id='alice', role='user'))
    )
    monkeypatch.setattr(agent_v2.Storage, 'get_file', lambda path: path)

    async def update(file_id, meta):
        assert file_id == file.id
        file.meta.update(meta)
        return file

    file.update = AsyncMock(side_effect=update)
    monkeypatch.setattr(agent_v2.Files, 'update_file_metadata_by_id', file.update)
    return file


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'entry', [{'type': 'image', 'id': 'saved-image'}, {'content_type': 'image/jpg', 'url': 'saved-image'}]
)
async def test_saved_image_upload_is_reused_after_access_check_on_retry(chat, image_upload, saved_image, entry):
    metadata = {'user_message': {'content': 'describe', 'files': [entry]}}
    for index in range(2):
        await chat.turn('describe', f'a{index}', **metadata)
        assert chat.mutations()[-1][1]['input']['images'] == [IMAGE_REFERENCE]
    image_upload.assert_awaited_once_with(
        '/v1/chat/images', IMAGE_BYTES, as_user='owui:user:alice', params={'name': 'photo.png'}
    )
    assert saved_image.meta['soev_image'] == IMAGE_REFERENCE
    assert saved_image.lookup.await_count == 2
    assert saved_image.update.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['bytes', 'caller'])
async def test_image_cache_does_not_reuse_changed_bytes_or_another_callers_reference(
    chat, image_upload, saved_image, change
):
    metadata = {'user_message': {'content': 'describe', 'files': [{'type': 'image', 'id': saved_image.id}]}}
    await chat.turn('describe', 'a1', **metadata)
    if change == 'bytes':
        Path(saved_image.path).write_bytes(IMAGE_BYTES + b'changed')
    else:
        saved_image.meta['soev_image_user'] = 'owui:user:bob'
    await chat.turn('describe', 'a2', **metadata)
    assert image_upload.await_count == 2


@pytest.mark.asyncio
async def test_saved_image_access_is_checked_before_using_cached_reference(
    chat, image_upload, saved_image, monkeypatch
):
    saved_image.user_id = 'bob'
    saved_image.meta = {'soev_image': IMAGE_REFERENCE, 'soev_image_user': 'owui:user:alice'}
    access = AsyncMock(return_value=False)
    monkeypatch.setattr(agent_v2, 'has_access_to_file', access)
    chunks = await chat.turn(
        'describe', 'a1', user_message={'content': 'describe', 'files': [{'type': 'image', 'id': saved_image.id}]}
    )
    assert chunks[0]['error']['code'] == 'images_unavailable'
    assert chat.mutations() == []
    image_upload.assert_not_awaited()
    access.assert_awaited_once()


@pytest.mark.asyncio
async def test_temporary_chat_images_come_only_from_current_user_message(chat, image_upload, monkeypatch):
    update = AsyncMock()
    monkeypatch.setattr(agent_v2.Files, 'update_file_metadata_by_id', update)
    await chat.turn(
        'describe',
        'a1',
        chat_id='local:test',
        user_message={
            'content': [{'type': 'text', 'text': 'describe'}, {'type': 'image_url', 'image_url': {'url': 'ignored'}}],
            'files': [{'type': 'image', 'url': IMAGE_URL, 'name': 'photo.png'}, {'type': 'file', 'id': 'document'}],
        },
        files=[{'type': 'image', 'url': 'ignored'}] * 5,
        form_data={'messages': [{'role': 'user', 'files': [{'type': 'image', 'url': 'old image'}]}]},
    )
    sent = chat.mutations()[-1][1]['input']
    assert sent == {
        'text': 'describe',
        'knowledge': [],
        'documents': 'off',
        'tools': WEB_SEARCH_OFF,
        'images': [IMAGE_REFERENCE],
    }
    image_upload.assert_awaited_once_with(
        '/v1/chat/images', IMAGE_BYTES, as_user='owui:user:alice', params={'name': 'photo.png'}
    )
    update.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('language', ['en-US', 'nl-NL'])
async def test_more_than_four_images_refuses_before_upload_or_turn(chat, image_upload, language):
    chunks = await chat.turn(
        'describe',
        'a1',
        user_language=language,
        user_message={'content': 'describe', 'files': [{'type': 'image', 'url': IMAGE_URL}] * 5},
    )
    assert chunks[0]['error']['code'] == 'images_unavailable'
    assert ('maximaal 4' if language == 'nl-NL' else 'at most 4') in chunks[0]['error']['message']
    assert chat.mutations() == []
    image_upload.assert_not_awaited()


@pytest.mark.asyncio
async def test_four_images_are_allowed(chat, image_upload):
    await chat.turn(
        'describe', 'a1', user_message={'content': 'describe', 'files': [{'type': 'image', 'url': IMAGE_URL}] * 4}
    )
    assert chat.mutations()[-1][1]['input']['images'] == [IMAGE_REFERENCE] * 4


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'url',
    [
        'data:text/plain;base64,aGk=',
        'data:image/png;base64,!!!',
        'data:image/png;base64,',
        'data:image/png;base64,a',
        'data:image/png,raw',
    ],
)
async def test_invalid_image_data_url_refuses_turn(chat, image_upload, url):
    chunks = await chat.turn(
        'describe',
        'a1',
        user_message={'content': 'describe', 'files': [{'type': 'image', 'url': url, 'name': 'bad.png'}]},
    )
    assert chunks[0]['error']['code'] == 'images_unavailable'
    assert 'bad.png (invalid image data)' in chunks[0]['error']['message']
    assert chat.mutations() == []
    image_upload.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('status,reason', [(413, '10 MiB'), (415, 'PNG, JPEG, WebP')])
@pytest.mark.parametrize('language', ['en-US', 'nl-NL'])
async def test_image_upload_validation_errors_name_the_file(chat, image_upload, status, reason, language):
    image_upload.side_effect = SoevApiError(status, 'image_invalid', 'upstream detail')
    chunks = await chat.turn(
        'describe',
        'a1',
        user_language=language,
        user_message={'content': 'describe', 'files': [{'type': 'image', 'url': IMAGE_URL, 'name': 'bad.png'}]},
    )
    error = chunks[0]['error']
    assert error['code'] == 'images_unavailable'
    assert 'bad.png' in error['message'] and reason in error['message']
    assert ('Verwijder' if language == 'nl-NL' else 'Remove') in error['message']
    assert chat.mutations() == []


@pytest.mark.asyncio
async def test_other_image_upload_errors_propagate(chat, image_upload):
    error = SoevApiError(503, 'service_unavailable', 'unavailable')
    image_upload.side_effect = error
    with pytest.raises(SoevApiError) as caught:
        await chat.turn(
            'describe', 'a1', user_message={'content': 'describe', 'files': [{'type': 'image', 'url': IMAGE_URL}]}
        )
    assert caught.value is error
    assert chat.mutations() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('features', 'state'),
    [
        ({'document_writer': True, 'document_writer_required': True}, 'required'),
        ({'document_writer': True}, 'auto'),
        ({'document_writer': False}, 'off'),
        ({'document_writer': False, 'document_writer_required': True}, 'off'),
        ({}, 'off'),
    ],
)
@pytest.mark.parametrize('allowed', [True, False])
async def test_documents_sends_its_state_on_every_turn(
    chat: Chat, monkeypatch: pytest.MonkeyPatch, features: dict, state: str, allowed: bool
) -> None:
    monkeypatch.setattr(agent_v2, '_documents_allowed', AsyncMock(return_value=allowed))
    for index in range(2):
        await chat.turn('question', f'a{index}', 'a0' if index else None, features=features)
        sent = chat.mutations()[-1][1]['input']
        assert sent['documents'] == (state if allowed else 'off')
        assert sent['tools'] == WEB_SEARCH_OFF


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('tenant', 'enabled', 'role', 'permitted', 'allowed'),
    [
        (False, True, 'admin', True, False),
        (True, False, 'admin', True, False),
        (True, True, 'admin', False, True),
        (True, True, 'user', True, True),
        (True, True, 'user', False, False),
        (True, True, None, True, False),
    ],
    ids=['tenant-off', 'enable-off', 'admin', 'user-permitted', 'user-denied', 'unknown-user'],
)
async def test_documents_allowed_needs_tenant_setting_and_permission(
    monkeypatch: pytest.MonkeyPatch, tenant: bool, enabled: bool, role: str | None, permitted: bool, allowed: bool
) -> None:
    async def config_get(key: str, default=None):
        return {'document_writer.enable': enabled, 'user.permissions': {}}.get(key, default)

    user = SimpleNamespace(id='u1', role=role) if role else None
    monkeypatch.setattr(agent_v2, 'is_feature_enabled', lambda key: tenant if key == 'document_writer' else False)
    monkeypatch.setattr(agent_v2.Config, 'get', config_get)
    monkeypatch.setattr(agent_v2.Users, 'get_user_by_id', AsyncMock(return_value=user))
    permission = AsyncMock(return_value=permitted)
    monkeypatch.setattr(agent_v2, 'has_permission', permission)
    assert await agent_v2._documents_allowed('u1') is allowed
    if tenant and enabled and role == 'user':
        permission.assert_awaited_once_with('u1', 'features.document_writer', {})


@pytest.mark.asyncio
@pytest.mark.parametrize('format', ['markdown', 'html'])
async def test_documents_stream_as_answer_text_with_ordinary_citations(chat: Chat, format: str) -> None:
    opening = f'<document title="Report" format="{format}">'
    before = opening + ('<p>Claim' if format == 'html' else '# Claim')
    after = ('</p>' if format == 'html' else '') + '</document>'
    chat.api.chat.turns = [
        [
            found(DOCUMENT, CHUNK),
            ('delta', {'text': before}),
            ('citation', cited(len(before), 'source-a')),
            ('delta', {'text': after}),
            answered(before + after, cited(len(before), 'source-a')),
        ]
    ]
    chunks = await chat.turn('write a report', 'a1', features={'document_writer': True})
    assert content(chunks).endswith(before + ' [1]' + after)
    assert panel(chunks, chat.socket) == [1]


def stored_chat(count: int, *, size: int = 10) -> SimpleNamespace:
    """A pre-cutover chat of `count` messages m1..mN, alternating user and assistant."""
    messages = {
        f'm{index}': {
            'id': f'm{index}',
            'parentId': f'm{index - 1}' if index > 1 else None,
            'role': 'user' if index % 2 else 'assistant',
            'content': f'message {index} ' + 'x' * size,
        }
        for index in range(1, count + 1)
    }
    return SimpleNamespace(chat={'history': {'messages': messages, 'currentId': f'm{count}'}})


def sent_text(chat: Chat) -> str:
    return chat.mutations()[-1][1]['input']['text']


@pytest.mark.asyncio
async def test_a_pre_cutover_chat_starts_its_thread_with_the_conversation(
    chat: Chat, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Chats, 'get_chat_by_id', AsyncMock(return_value=stored_chat(4)))
    await chat.turn('and now?', 'a5', 'm4')
    assert sent_text(chat) == (
        'Earlier in this conversation:\n\nuser: message 1 xxxxxxxxxx\n\nassistant: message 2 xxxxxxxxxx'
        '\n\nuser: message 3 xxxxxxxxxx\n\nassistant: message 4 xxxxxxxxxx\n\n---\n\nand now?'
    )


@pytest.mark.asyncio
async def test_a_long_pre_cutover_chat_sends_its_first_and_last_three_exchanges(
    chat: Chat, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Chats, 'get_chat_by_id', AsyncMock(return_value=stored_chat(20)))
    await chat.turn('and now?', 'a21', 'm20')
    text = sent_text(chat)
    kept = [int(line.split()[2]) for line in text.split('\n\n') if line.startswith(('user:', 'assistant:'))]
    assert kept == [1, 2, 3, 4, 5, 6, 15, 16, 17, 18, 19, 20]
    assert '(... 8 messages left out ...)' in text


@pytest.mark.asyncio
async def test_the_conversation_is_cut_evenly_to_the_text_limit(chat: Chat, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Chats, 'get_chat_by_id', AsyncMock(return_value=stored_chat(12, size=20_000)))
    await chat.turn('and now?', 'a13', 'm12')
    text = sent_text(chat)
    assert len(text) <= agent_v2.TEXT_CHARACTERS
    assert text.endswith('\n\n---\n\nand now?')
    assert text.count('...') == 12


@pytest.mark.asyncio
async def test_a_turn_on_a_thread_sends_no_earlier_conversation(chat: Chat, monkeypatch: pytest.MonkeyPatch) -> None:
    stored = AsyncMock(return_value=stored_chat(4))
    monkeypatch.setattr(Chats, 'get_chat_by_id', stored)
    await chat.turn('first', 'a1')
    await chat.turn('second', 'a2', 'a1')
    assert sent_text(chat) == 'second'
    stored.assert_not_awaited()


@pytest.mark.asyncio
async def test_answered_model_is_stored_and_announced_when_reported(chat: Chat) -> None:
    chat.api.chat.turns = [[('model_output', {'content': 'Hi', 'answered_model': 'fallback-llm'})]]
    await chat.turn('first', 'a1')
    assert chat.messages['chat', 'a1']['meta']['answered_model'] == 'fallback-llm'
    assert {'type': 'chat:completion', 'data': {'answered_model': 'fallback-llm'}} in chat.socket


@pytest.mark.asyncio
async def test_no_answered_model_without_a_report(chat: Chat) -> None:
    await chat.turn('first', 'a1')
    assert 'answered_model' not in chat.messages['chat', 'a1']['meta']
    assert not [event for event in chat.socket if event['type'] == 'chat:completion']


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['mail-text', 'mail-reference'])
async def test_mail_provider_comes_from_element_type(kind, monkeypatch):
    lookup = AsyncMock()
    monkeypatch.setattr(agent_v2.Files, 'get_file_by_id', lookup)
    element = {'id': 'mail', 'ref': 'mail', 'type': kind, 'text': 'Mail body'}
    whole = {'title': 'Unrelated title', 'source_url': 'https://example.test/message'}
    source = agent_v2.Citations().add(await agent_v2._as_source(element, whole))
    assert source['source']['provider'] == 'outlook_mail'
    lookup.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('provider', [None, 'onedrive', 'another_provider'])
async def test_document_provider_comes_only_from_file_metadata(provider, monkeypatch):
    file = SimpleNamespace(meta={'source': {'provider': provider}}) if provider else None
    lookup = AsyncMock(return_value=file)
    monkeypatch.setattr(agent_v2.Files, 'get_file_by_id', lookup)
    whole = {
        'source_id': 'file-id',
        'title': 'Outlook OneDrive',
        'source_url': 'https://outlook.office.com/mail/id/looks-like-mail',
    }
    source = agent_v2.Citations().add(await agent_v2._as_source(CHUNK, whole))
    assert source['source'].get('provider') == provider
    lookup.assert_awaited_once_with('file-id')


@pytest.mark.asyncio
async def test_web_sources_do_not_infer_provider_from_urls_or_names(monkeypatch):
    lookup = AsyncMock()
    monkeypatch.setattr(agent_v2.Files, 'get_file_by_id', lookup)
    whole = {'title': 'OneDrive', 'source_url': 'https://tenant.sharepoint.com/document'}
    source = agent_v2.Citations().add(await agent_v2._as_source(CHUNK, whole))
    assert 'provider' not in source['source']
    lookup.assert_not_awaited()


@pytest.mark.parametrize('declared', [True, False])
def test_list_failure_uses_declared_status_or_generic_fallback(declared):
    turn = agent_v2.AgentTurn(None, {}, 'owui:user:alice')
    turn.tool_statuses = {
        'list_live_folder': {
            'running': {'template': 'Browsing your files...'},
            'done': {'template': 'Browsed your files'},
            **({'failed': {'template': 'Could not browse your files'}} if declared else {}),
        }
    }
    turn.running['browse'] = ('list_live_folder', {})
    turn.end_tool({'call_id': 'browse', 'error': 'invalid arguments'})
    assert turn.settling == [
        {
            'action': 'list_live_folder',
            'call_id': 'browse',
            'done': True,
            'description': 'Could not browse your files' if declared else 'Could not run {{tool}}',
            **({} if declared else {'tool': 'list_live_folder'}),
        }
    ]


@pytest.mark.parametrize(
    'arguments,expected', [({'folder': 'Plans'}, 'Could not browse {{folder}}'), ({}, 'Could not browse your files')]
)
def test_failed_status_parameters_use_the_declared_fallback(arguments, expected):
    turn = agent_v2.AgentTurn(None, {}, 'owui:user:alice')
    turn.tool_statuses = {
        'list_live_folder': {
            'failed': {
                'template': 'Could not browse {{folder}}',
                'fallback': 'Could not browse your files',
                'params': {'folder': 'argument.folder'},
            }
        }
    }
    turn.running['browse'] = ('list_live_folder', arguments)
    turn.end_tool({'call_id': 'browse', 'error': 'not_found'})
    assert turn.settling[0]['description'] == expected
    if arguments:
        assert turn.settling[0]['folder'] == 'Plans'


@pytest.mark.asyncio
@pytest.mark.parametrize('kind,attached', [('document-text', False), ('document-text', True), ('chunk', False)])
async def test_citation_metadata_distinguishes_document_reads_from_chunks(kind, attached):
    turn = agent_v2.AgentTurn(None, {}, 'owui:user:alice')
    turn.emitter = AsyncMock()
    turn.attached = AsyncMock()
    element = {'type': kind, 'id': 'text', 'ref': 'doc', 'text': 'Body', 'pages': [2]}
    await turn.record_output({'elements': [document('doc', 'Plan'), element]}, attached=attached)
    source = turn.citations.sources['text']
    metadata = source['metadata'][0]
    assert metadata.get('granularity') == ('document' if kind == 'document-text' else None)
    assert metadata['page'] == 1
