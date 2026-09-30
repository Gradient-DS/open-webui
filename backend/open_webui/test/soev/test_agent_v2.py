"""Exercise v2 routing and branching against the authenticated fake relay."""

import asyncio
import copy
import html
import json
from dataclasses import dataclass, field
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
from open_webui.soev import agent_threads
from open_webui.soev.client import ChatEvent, SoevApiError, SoevClient
from open_webui.test.soev.fake_api import FakeSoevApi
from open_webui.utils import agent, agent_v2
from starlette.responses import StreamingResponse


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
    return result


def content(chunks: list[dict], key: str = 'content') -> str:
    return ''.join(chunk['choices'][0]['delta'].get(key, '') for chunk in chunks if 'choices' in chunk)


@pytest.mark.asyncio
async def test_third_turn_sends_only_the_new_input(chat: Chat) -> None:
    for index in range(1, 4):
        await chat.turn(f'turn {index}', f'a{index}', f'a{index - 1}' if index > 1 else None)
    assert chat.mutations() == [
        ('/v1/chat/threads', {'input': {'text': 'turn 1', 'knowledge': []}, 'agent': 'test', 'model': 'llm'}),
        ('/v1/chat/threads/thr-1/inputs', {'input': {'text': 'turn 2', 'knowledge': []}, 'model': 'llm'}),
        ('/v1/chat/threads/thr-1/inputs', {'input': {'text': 'turn 3', 'knowledge': []}, 'model': 'llm'}),
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
        ('/v1/chat/threads/thr-2/inputs', {'input': {'text': text, 'knowledge': []}, 'model': 'llm'}),
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
        files=[
            {'type': 'collection', 'id': 'kb-a', 'name': 'Name when picked'},
            {'type': 'file', 'id': 'ignored'},
        ],
        knowledge=[{'id': 'kb-a'}, {'id': 'kb-b'}],
    )
    assert chat.mutations()[0][1] == {
        'input': {
            'text': 'one\ntwo',
            'knowledge': [
                {'key': 'kb-a', 'name': 'Contracten', 'description': 'Getekende contracten'},
                {'key': 'kb-b', 'name': 'Notulen'},
            ],
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


@pytest.mark.asyncio
async def test_knowledge_the_api_does_not_show_is_still_sent_by_its_key(chat: Chat) -> None:
    await chat.turn('next', 'a1', files=[{'type': 'collection', 'id': 'kb-gone', 'name': 'Old'}])
    assert chat.mutations()[-1][1]['input']['knowledge'] == [{'key': 'kb-gone', 'name': 'kb-gone'}]


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
    assert content(await chat.turn('first', 'a1')) == 'Eerst [1] [2].'
    sources = [event['data'] for event in chat.socket if event['type'] == 'source']
    assert [source['n'] for source in sources] == [1, 1, 1, 2]
    assert [event['data'] for event in chat.socket if event['type'] == 'panel_filter'][-1] == {'ns': [1, 2]}
    chat.socket.clear()
    chat.api.chat.turns = [
        [
            found(DOCUMENT, chunk('chunk-3'), second, chunk('second-2', 'second'), document('third', 'Third')),
            found(chunk('third-1', 'third'), call_id='c1'),
            answered('Dan.', cited(3, 'chunk-0'), cited(3, 'third-1', 'third')),
        ]
    ]
    assert content(await chat.turn('next', 'a2', 'a1')) == 'Dan [1] [3].'
    sources = [event['data'] for event in chat.socket if event['type'] == 'source']
    assert [source['n'] for source in sources] == [1, 1, 1, 2, 1, 2, 3]
    assert [event['data'] for event in chat.socket if event['type'] == 'panel_filter'][-1] == {'ns': [1, 2, 3]}


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
async def test_panel_filter_scopes_chips_to_this_turn_with_cumulative_numbers(chat: Chat) -> None:
    """Filter retrieved documents using their cumulative numbers across turns."""
    second = [document('document-b', 'Second'), chunk('source-b', 'document-b')]
    third = [document('document-c', 'Third'), chunk('source-c', 'document-c')]
    chat.api.chat.turns = [[found(DOCUMENT, CHUNK, *second), answered('First', cited(5, 'source-a'))]]
    await chat.turn('first', 'a1')
    assert [event['data'] for event in chat.socket if event['type'] == 'panel_filter'][-1] == {'ns': [1, 2]}
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
    assert content(await chat.turn('second', 'a2', 'a1')) == 'Second [2] and [3]; earlier [1]'
    sources = [event['data'] for event in chat.socket if event['type'] == 'source']
    assert [source['n'] for source in sources] == [1, 2, 3]
    filters = [event['data'] for event in chat.socket if event['type'] == 'panel_filter']
    assert filters[0] == {'ns': []}
    assert filters[-1] == {'ns': [2, 3]}
    chat.socket.clear()
    await chat.turn('third', 'a3', 'a2')
    assert [event['data'] for event in chat.socket if event['type'] == 'panel_filter'][-1] == {'ns': []}


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
        assert content(await chat.turn('again', message_id, 'a1')) == 'Again [1]'
        assert len([event for event in chat.socket if event['type'] == 'source']) == 1


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
    'name,description', [('search', 'Searching the knowledge base…'), ('calculate', 'Running calculate…')]
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
async def test_a_tool_call_shows_running_then_done_only_when_its_output_lands(
    name: str, description: str, kind: str, payload: dict
) -> None:
    turn = agent_v2.AgentTurn(AsyncMock(), {}, 'owui:user:alice')
    turn.emitter = AsyncMock()
    status = {'type': 'status', 'data': {'action': name, 'description': description, 'call_id': 'c1', 'done': False}}
    async with asyncio.timeout(2):
        started = await turn.render(
            ChatEvent(
                'model_output',
                {'stream': 'root', 'payload': {'content': '', 'tool_calls': [{'id': 'c1', 'name': name}]}},
            )
        )
        assert [call.args[0] for call in turn.emitter.call_args_list] == [status]
        data = payload if kind.endswith('delta') else {'stream': 'root', 'payload': payload}
        ended = await turn.render(ChatEvent(kind, data))
    answered_call = kind == 'tool_output'
    assert [call.args[0] for call in turn.emitter.call_args_list] == [status] * (1 + answered_call)
    assert '<details type="tool_calls"' not in content(started)
    assert ('<details type="tool_calls"' in content(ended)) == answered_call


@pytest.mark.asyncio
async def test_parallel_tools_each_show_once() -> None:
    turn = agent_v2.AgentTurn(AsyncMock(), {}, 'owui:user:alice')
    turn.emitter = AsyncMock()
    async with asyncio.timeout(2):
        await turn.render(
            ChatEvent(
                'model_output',
                {
                    'stream': 'root',
                    'payload': {
                        'content': '',
                        'tool_calls': [{'id': 'c1', 'name': 'search'}, {'id': 'c2', 'name': 'calculate'}],
                    },
                },
            )
        )
        for call_id in ['c2', 'c1']:
            await turn.render(ChatEvent('tool_output', {'stream': 'root', 'payload': {'call_id': call_id}}))
    search = {'action': 'search', 'description': 'Searching the knowledge base…', 'call_id': 'c1', 'done': False}
    calculate = {'action': 'calculate', 'description': 'Running calculate…', 'call_id': 'c2', 'done': False}
    assert [call.args[0]['data'] for call in turn.emitter.call_args_list] == [search, calculate, calculate, search]


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
        {'description': 'error', 'done': True}
        if failure
        else {'action': 'summary', 'description': '1 tool called in less than a second', 'done': True},
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
        ('/v1/chat/threads', {'input': {'text': 'last question', 'knowledge': []}, 'agent': 'test', 'model': 'llm'})
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
        ('/v1/chat/threads/thr-1/inputs', {'input': {'text': 'what was I asking?', 'knowledge': []}, 'model': 'llm'}),
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
        {**status, **done, **counted},
    ]
    summary = done['description'].replace('{{passages}}', '3').replace('{{documents}}', '2')
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
        {**opening, 'description': 'Read {{doc_title}}'},
    ]
    assert '<summary>Read Leave policy</summary>' in content(chunks)


@pytest.mark.asyncio
async def test_a_tool_without_a_declared_status_shows_the_generic_line(declared: Chat) -> None:
    declared.api.chat.turns = [
        [call('list_documents'), ('tool_output', {'call_id': 'c1'}), ('model_output', {'content': 'done'})]
    ]
    await declared.turn('q', 'a1')

    generic = {'action': 'list_documents', 'description': 'Running list_documents…', 'call_id': 'c1', 'done': False}
    assert statuses(declared) == [generic, generic]


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
