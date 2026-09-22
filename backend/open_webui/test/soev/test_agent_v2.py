"""Exercise v2 routing and branching against the authenticated fake relay."""

import asyncio
import copy
import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from open_webui import env
from open_webui.models.agent_configs import AgentConfigs
from open_webui.models.chats import Chats
from open_webui.soev.client import ChatEvent
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
        return self.messages[chat_id, message_id]['agent_v2']

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
        ('/v1/chat/threads', {'input': 'turn 1', 'collections': [], 'agent': 'test', 'model': 'llm'}),
        ('/v1/chat/threads/thr-1/inputs', {'input': 'turn 2', 'collections': [], 'model': 'llm'}),
        ('/v1/chat/threads/thr-1/inputs', {'input': 'turn 3', 'collections': [], 'model': 'llm'}),
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
        ('/v1/chat/threads/thr-2/inputs', {'input': text, 'collections': [], 'model': 'llm'}),
    ]
    assert chat.api.chat.threads['thr-1']['events'] == original
    assert chat.bookmark('new-answer', chat_id) == {'thread_id': 'thr-2', 'position': at + 2}
    inputs = [
        event['payload']['payload'] for event in chat.api.chat.threads['thr-2']['events'] if event['type'] == 'input'
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


@pytest.mark.asyncio
async def test_one_text_input_and_selected_collection_keys(chat: Chat) -> None:
    await chat.turn(
        [
            {'type': 'text', 'text': 'one'},
            {'type': 'image_url', 'image_url': {'url': 'private image'}},
            {'type': 'text', 'text': 'two'},
        ],
        'a1',
        files=[{'type': 'collection', 'id': 'kb-a'}, {'type': 'file', 'id': 'ignored'}],
        knowledge=[{'id': 'kb-a'}, {'id': 'kb-b'}],
    )
    assert chat.mutations()[0][1] == {
        'input': 'one\ntwo',
        'collections': ['kb-a', 'kb-b'],
        'agent': 'test',
        'model': 'llm',
    }
    await chat.turn('next', 'a2', 'a1', files=[{'type': 'collection', 'id': 'kb-c'}])
    assert chat.mutations()[-1][1]['collections'] == ['kb-c']


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
        event['payload']['payload'] for event in chat.api.chat.threads['thr-1']['events'] if event['type'] == 'input'
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
    assert citations.rewrite('[source-a] [<source-b>]', final=True) == '[1] [1]'


@pytest.mark.asyncio
async def test_document_numbers_survive_seeded_turns(chat: Chat) -> None:
    """Number documents by first appearance across chunks and seeded turns."""
    chunks = [{**SOURCE, 'id': f'chunk-{index}'} for index in range(3)]
    second = {**SOURCE, 'id': 'second-1', 'ref': 'second', 'properties': {'title': 'Second'}}
    chat.api.chat.turns = [
        [
            *(('source', chunk) for chunk in chunks),
            ('source', second),
            ('model_output', {'content': '[chunk-0] [chunk-1] [chunk-2] [second-1]'}),
        ]
    ]
    assert content(await chat.turn('first', 'a1')) == '[1] [1] [1] [2]'
    sources = [event['data'] for event in chat.socket if event['type'] == 'source']
    assert [source['n'] for source in sources] == [1, 1, 1, 2]
    assert [event['data'] for event in chat.socket if event['type'] == 'panel_filter'][-1] == {'ns': [1, 2]}
    chat.socket.clear()
    chat.api.chat.turns = [
        [
            ('source', {**SOURCE, 'id': 'chunk-3'}),
            ('source', {**second, 'id': 'second-2'}),
            ('source', {**SOURCE, 'id': 'third-1', 'ref': 'third', 'properties': {'title': 'Third'}}),
            ('model_output', {'content': '[chunk-0] [chunk-3] [second-1] [second-2] [third-1]'}),
        ]
    ]
    assert content(await chat.turn('next', 'a2', 'a1')) == '[1] [1] [2] [2] [3]'
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
    properties = {
        'title': 'report.pdf',
        'source_id': 'owui-file-id',
        'page_numbers': [2],
        'bboxes': json.dumps(rects) if as_json else rects,
        'chunk_content': 'duplicate',
        'embedding_text': 'duplicate embedding',
        'author': 'Author',
        'derived_raw': '{}',
        'source_url': None,
    }
    original = copy.deepcopy(properties)
    chat.api.chat.turns = [
        [('source', {**SOURCE, 'properties': properties}), ('model_output', {'content': 'Answer [source-a]'})]
    ]
    assert content(await chat.turn('question', 'a1')) == 'Answer [1]'
    source = next(event['data'] for event in chat.socket if event['type'] == 'source')
    assert source['document'] == [SOURCE['text']]
    assert source['metadata'][0] == {
        'title': 'report.pdf',
        'source_id': 'owui-file-id',
        'file_id': 'owui-file-id',
        'page': 1,
        'bboxes': [{**RECT, 'page': 1}, {**RECT, 'page': 0}, rects[2]],
        'author': 'Author',
        'derived_raw': '{}',
        'source_url': None,
        'source': 'document',
        'name': 'report.pdf',
        'chunk_id': 'source-a',
        'ref': 'document',
    }
    assert properties == original


@pytest.mark.asyncio
@pytest.mark.parametrize('as_json', [False, True])
async def test_mixed_bboxes_preserve_valid_rectangles(chat: Chat, as_json: bool) -> None:
    """Keep both valid rectangles when a malformed rectangle appears between them."""
    rects = [RECT, {}, {**RECT, 'page': 1}]
    properties = {'bboxes': json.dumps(rects) if as_json else rects}
    chat.api.chat.turns = [
        [('source', {**SOURCE, 'properties': properties}), ('model_output', {'content': 'Answer [source-a]'})]
    ]
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
    chat.api.chat.turns = [
        [('source', {**SOURCE, 'properties': {'bboxes': bboxes}}), ('model_output', {'content': 'Answer [source-a]'})]
    ]
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


@pytest.mark.parametrize('marker', ['[source-a]', '[<source-a>]'])
def test_every_citation_split_is_rewritten(marker: str) -> None:
    for split in range(len(marker) + 1):
        citations = agent_v2.Citations()
        citations.add(SOURCE)
        text = citations.rewrite('Text ' + marker[:split]) + citations.rewrite(marker[split:] + ' tail', final=True)
        assert text == 'Text [1] tail'


def test_unclosed_citation_buffer_flushes_after_64_characters() -> None:
    citations = agent_v2.Citations()
    assert citations.rewrite('Text [' + 'x' * 63) == 'Text '
    assert len(citations.pending) == 64
    assert citations.rewrite('y') == '[' + 'x' * 63 + 'y'
    assert citations.pending == ''
    assert citations.rewrite(' rest') == ' rest'


@pytest.mark.asyncio
async def test_citation_marker_survives_three_deltas(chat: Chat) -> None:
    chat.api.chat.turns = [
        [
            ('source', SOURCE),
            ('delta', {'text': 'Text ['}),
            ('delta', {'text': 'source-'}),
            ('delta', {'text': 'a] tail'}),
            ('model_output', {'content': 'Text [source-a] tail'}),
        ]
    ]
    assert content(await chat.turn('question', 'a1')) == 'Text [1] tail'


@pytest.mark.asyncio
@pytest.mark.parametrize('close_after', [None, 4])
@pytest.mark.parametrize(
    'durable,expected', [('Draft [source-a].', 'Draft [1]. Next.'), ('Revised answer.', 'Draft  Next.')]
)
async def test_durable_output_prefix_and_mismatch_do_not_cancel(
    chat: Chat, caplog: pytest.LogCaptureFixture, close_after: int | None, durable: str, expected: str
) -> None:
    chat.api.chat.close_after = close_after
    chat.api.chat.turns = [
        [
            ('source', SOURCE),
            ('delta', {'text': 'Draft [source-'}),
            ('model_output', {'content': durable, 'reasoning': 'reason'}),
            ('model_output', {'content': ' Next.'}),
        ]
    ]
    chunks = await chat.turn('question', 'a1')
    assert content(chunks) == expected
    assert not any('error' in chunk for chunk in chunks)
    assert len(chat.mutations()) == 1
    assert chat.bookmark('a1')['position'] == 5
    mismatch = durable == 'Revised answer.'
    warnings = [record for record in caplog.records if 'disagrees' in record.message]
    assert len(warnings) == int(mismatch)
    if mismatch:
        assert warnings[0].thread_id == 'thr-1'
        assert 'Draft' not in warnings[0].message
        assert durable not in warnings[0].message
        assert content(chunks, 'reasoning_content') == ''


@pytest.mark.asyncio
async def test_panel_filter_scopes_chips_to_this_turn_with_cumulative_numbers(chat: Chat) -> None:
    """Filter retrieved documents using their cumulative numbers across turns."""
    second = {**SOURCE, 'id': 'source-b', 'ref': 'document-b', 'properties': {'title': 'Second'}}
    third = {**SOURCE, 'id': 'source-c', 'ref': 'document-c', 'properties': {'title': 'Third'}}
    chat.api.chat.turns = [[('source', SOURCE), ('source', second), ('model_output', {'content': 'First [source-a]'})]]
    await chat.turn('first', 'a1')
    assert [event['data'] for event in chat.socket if event['type'] == 'panel_filter'][-1] == {'ns': [1, 2]}
    chat.socket.clear()
    chat.api.chat.turns = [
        [
            ('source', second),
            ('source', third),
            ('source', third),
            ('model_output', {'content': 'Second [source-b] and [source-c]; earlier [source-a]'}),
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
            ('tool_output', {'call_id': 'c1', 'data': 'PRIVATE TOOL RESULT'}),
            ('source', SOURCE),
            ('source', SOURCE),
            ('source', {**SOURCE, 'id': 'source-b', 'text': 'another passage'}),
            ('delta', {'text': 'Answer [source-'}),
            ('delta', {'text': 'b] and [source-a].'}),
            ('model_output', {'content': 'Answer [source-b] and [source-a].', 'reasoning': 'Explain'}),
            ('future_event', {'unknown': 'ignored'}),
        ]
    ]
    chunks = await chat.turn('question', 'a1')
    assert content(chunks) == 'Answer [1] and [1].'
    assert content(chunks, 'reasoning_content') == 'PlanExplain'
    sources = [event['data'] for event in chat.socket if event['type'] == 'source']
    assert [source['n'] for source in sources] == [1, 1]
    assert sources[0]['source'] == {'id': 'document', 'name': 'Document', 'url': 'document'}
    assert sources[0]['document'] == ['quoted passage']
    assert sources[0]['metadata'][0]['source'] == 'document'
    assert sources[1]['metadata'][0]['source'] == 'document'
    assert chat.bookmark('a1')['position'] == 9


@pytest.mark.asyncio
async def test_previous_sources_are_available_in_later_turn_and_fork(chat: Chat) -> None:
    chat.api.chat.turns = [[('source', SOURCE), ('model_output', {'content': 'First [source-a]'})]]
    await chat.turn('first', 'a1')
    for message_id in ('a2', 'regenerated'):
        chat.socket.clear()
        chat.api.chat.turns = [[('model_output', {'content': 'Again [source-a]'})]]
        assert content(await chat.turn('again', message_id, 'a1')) == 'Again [1]'
        assert len([event for event in chat.socket if event['type'] == 'source']) == 1


@pytest.mark.asyncio
async def test_capped_stream_recovers_durable_suffix_and_split_marker(chat: Chat) -> None:
    chat.api.chat.turns = [
        [
            ('source', SOURCE),
            ('delta', {'text': 'Answer [source-'}),
            ('model_output', {'content': 'Answer [source-a].'}),
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
async def test_tool_activity_clears_at_result_or_next_model_text(
    name: str, description: str, kind: str, payload: dict
) -> None:
    turn = agent_v2.AgentTurn(AsyncMock(), {}, 'owui:user:alice')
    turn.emitter = AsyncMock()
    async with asyncio.timeout(2):
        await turn.render(
            ChatEvent(
                'model_output',
                {'stream': 'root', 'payload': {'content': '', 'tool_calls': [{'id': 'c1', 'name': name}]}},
            )
        )
        assert [call.args[0] for call in turn.emitter.call_args_list] == [
            {'type': 'status', 'data': {'description': description, 'done': False}}
        ]
        data = payload if kind.endswith('delta') else {'stream': 'root', 'payload': payload}
        await turn.render(ChatEvent(kind, data))
    assert [call.args[0] for call in turn.emitter.call_args_list] == [
        {'type': 'status', 'data': {'description': description, 'done': False}},
        {'type': 'status', 'data': {'description': description, 'done': True}},
    ]


@pytest.mark.asyncio
async def test_parallel_tools_keep_remaining_activity_visible() -> None:
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
        for stream, call_id in [('child', 'c2'), ('root', 'unknown')]:
            await turn.render(ChatEvent('tool_output', {'stream': stream, 'payload': {'call_id': call_id}}))
        assert turn.emitter.call_count == 2
        await turn.render(ChatEvent('tool_output', {'stream': 'root', 'payload': {'call_id': 'c2'}}))
        assert turn.emitter.call_args.args[0]['data'] == {'description': 'Searching the knowledge base…', 'done': False}
        await turn.render(ChatEvent('tool_output', {'stream': 'root', 'payload': {'call_id': 'c1'}}))
    assert [call.args[0]['data'] for call in turn.emitter.call_args_list] == [
        {'description': 'Searching the knowledge base…', 'done': False},
        {'description': 'Running calculate…', 'done': False},
        {'description': 'Running calculate…', 'done': True},
        {'description': 'Searching the knowledge base…', 'done': False},
        {'description': 'Searching the knowledge base…', 'done': True},
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [False, True])
async def test_turn_end_clears_tool_activity_without_result(chat: Chat, failure: bool) -> None:
    chat.api.chat.turns = [
        [
            ('model_output', {'content': '', 'tool_calls': [{'id': 'c1', 'name': 'search'}]}),
            *([('error', {'code': 'service_unavailable'})] if failure else []),
        ]
    ]
    chunks = await chat.turn('question', 'a1')
    assert any('error' in chunk for chunk in chunks) == failure
    assert [event['data'] for event in chat.socket if event['type'] == 'status'] == [
        {'description': 'Searching the knowledge base…', 'done': False},
        {'description': 'Searching the knowledge base…', 'done': True},
        {'description': 'error' if failure else 'idle', 'done': True},
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
        ('/v1/chat/threads', {'input': 'last question', 'collections': [], 'agent': 'test', 'model': 'llm'})
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
        {'description': 'Searching the knowledge base…', 'done': False},
        {'description': 'Searching the knowledge base…', 'done': True},
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
