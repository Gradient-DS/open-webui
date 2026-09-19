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
from open_webui.models.agent_configs import AgentConfigs
from open_webui.models.chats import Chats
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
        **metadata: Any,
    ) -> StreamingResponse | dict:
        return await agent.call_agent_api(
            None,
            {
                'model': 'custom',
                'stream': stream,
                'messages': [
                    {'role': 'system', 'content': 'HISTORY MUST NOT LEAVE'},
                    {'role': 'user', 'content': 'wrong input'},
                ],
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
            override_agent='test',
        )

    async def turn(self, text: Any, message_id: str, parent: str | None = None, **kwargs: Any) -> list[dict]:
        response = await self.response(text, message_id, parent, **kwargs)
        assert isinstance(response, StreamingResponse)
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
        ('/v1/chat/threads', {'input': 'turn 1', 'collections': [], 'agent': 'test'}),
        ('/v1/chat/threads/thr-1/inputs', {'input': 'turn 2', 'collections': []}),
        ('/v1/chat/threads/thr-1/inputs', {'input': 'turn 3', 'collections': []}),
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
        ('/v1/chat/threads/thr-2/inputs', {'input': text, 'collections': []}),
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


SOURCE = {'id': 'source-a', 'ref': 'document', 'text': 'quoted passage', 'properties': {'title': 'Document', 'page': 2}}


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
    second = {**SOURCE, 'id': 'source-b'}
    third = {**SOURCE, 'id': 'source-c'}
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
async def test_only_an_explicit_agent_model_is_forwarded(
    chat: Chat, monkeypatch: pytest.MonkeyPatch, model: Any
) -> None:
    monkeypatch.setattr(
        AgentConfigs,
        'get_agent_config_by_id',
        AsyncMock(return_value=SimpleNamespace(meta={'runtime': 'v2', 'model': model})),
    )
    await chat.turn('first', 'a1')
    await chat.turn('second', 'a2', 'a1')
    await chat.turn('regenerated', 'retry', 'a1')
    for path, body in chat.mutations():
        if path.endswith('/fork'):
            continue
        if model == 'host-inference-endpoint':
            assert body['model'] == model
        else:
            assert 'model' not in body


@pytest.mark.asyncio
async def test_stream_renders_sources_reasoning_and_tools_without_duplicate_text(chat: Chat) -> None:
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
    assert content(chunks) == 'Answer [2] and [1].'
    assert content(chunks, 'reasoning_content') == 'PlanExplain'
    sources = [event['data'] for event in chat.socket if event['type'] == 'source']
    assert [source['n'] for source in sources] == [1, 2]
    assert sources[0]['source'] == {'id': 'source-a', 'name': 'Document', 'url': 'document'}
    assert sources[0]['document'] == ['quoted passage']
    assert sources[0]['metadata'][0]['source'] == 'source-a'
    assert sources[1]['metadata'][0]['source'] == 'source-b'
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
    assert 'partial' in await anext(response.body_iterator)
    await response.body_iterator.aclose()
    assert chat.mutations()[-1] == ('/v1/chat/threads/thr-1/cancel', {'input': 2})
    assert chat.api.chat.threads['thr-1']['state'] == 'idle'


@pytest.mark.asyncio
async def test_late_disconnect_guard_does_not_cancel_a_newer_turn(chat: Chat) -> None:
    chat.api.chat.turns = [[('delta', {'text': 'partial'})]]
    response = await chat.response('first', 'a1')
    assert isinstance(response, StreamingResponse)
    await anext(response.body_iterator)
    await chat.turn('second', 'a2', 'a1')
    chat.api.chat.threads['thr-1']['state'] = 'running'
    await response.body_iterator.aclose()
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
async def test_missing_new_input_never_falls_back_to_history(chat: Chat) -> None:
    with pytest.raises(ValueError, match='user_message'):
        await chat.response(None, 'a1')
    assert not chat.api.chat.requests


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
    chat.api.chat.turns = [[('delta', {'text': 'partial'})]]
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
        while not streams:
            await asyncio.sleep(0)
        await asyncio.wait_for(streams[0].waiting.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert chat.mutations()[-1] == ('/v1/chat/threads/thr-1/cancel', {'input': 2})
    assert streams[0].closed
    assert chat.api.chat.threads['thr-1']['state'] == 'idle'


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
    raw = ''.join([part async for part in response.body_iterator])
    assert raw == 'data: {"choices": [{"delta": {"content": "legacy answer"}}]}\n\ndata: [DONE]\n\n'
    assert emitted == [{'type': event.event_type, 'data': event.data} for event in events]
    assert chat.messages['chat', 'a1'] == {
        'subagents': [{'value': 'subagent'}],
        'contextUsage': {'value': 'context_usage'},
    }
