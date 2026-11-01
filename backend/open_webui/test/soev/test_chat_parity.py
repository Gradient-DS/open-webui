"""Reload and live chat presentation over the real v2 transport boundaries."""

import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from open_webui.socket import main as socket
from open_webui.soev import threads
from open_webui.test.soev import test_agent_v2, test_chat_paths
from open_webui.utils import agent_v2

chat = test_agent_v2.chat
dark_api = test_chat_paths.dark_api

URL = 'https://www.python.org/downloads/release/python-3140/'
RESULT = {
    'type': 'search-result',
    'id': URL,
    'ref': URL,
    'url': URL,
    'title': 'Python 3.14 release',
    'text': 'Released 7 October 2025.',
}
ENDPOINT = 'nebul--glm-5-3'
MODEL = 'zai-org/glm-5.3'


def events():
    return [
        {
            'position': 3,
            'type': 'model_output',
            'stream': 'root',
            'payload': {
                'model': ENDPOINT,
                'answered_model': MODEL,
                'content': '',
                'tool_calls': [{'id': 'search', 'name': 'web_search', 'arguments': {'query': 'Python release'}}],
            },
        },
        {
            'position': 4,
            'type': 'tool_output',
            'stream': 'root',
            'payload': {
                'call_id': 'search',
                'elements': [RESULT],
            },
        },
        {
            'position': 5,
            'type': 'model_output',
            'stream': 'root',
            'payload': {
                'model': ENDPOINT,
                'answered_model': MODEL,
                'content': 'October 7.',
            },
        },
    ]


@pytest.mark.asyncio
async def test_reload_enriches_projected_sources_and_models_with_one_events_read(dark_api, monkeypatch):
    from open_webui.soev import identity
    from open_webui.soev.threads import ProjectionPage

    fixture = Path(__file__).parents[4] / 'src/lib/utils/fixtures/projection-websearch.json'
    page = ProjectionPage.model_validate(json.loads(fixture.read_text()))
    page.messages = page.messages[:3]
    for message in page.messages:
        if message.content.get('model'):
            message.content['model'] = ENDPOINT
    projection = AsyncMock(return_value=threads.ProjectionResult(page, 'etag'))
    monkeypatch.setattr(dark_api.client, 'projection', projection)
    # Keep only the fictional citation this test's event stream supplies.
    page.messages[-1].content['markers'] = [{'at': 10, 'text': ' [1]', 'cited': [{'source': URL, 'written': 1}]}]
    runtime = SimpleNamespace(chat_events=AsyncMock(return_value=events()))
    monkeypatch.setattr(identity, 'build_client', lambda: runtime)
    response = await dark_api.browser.get('/api/v1/chats/fictional-chat')
    assert response.status_code == 200
    returned = response.json()
    assert [m['position'] for m in returned['messages']] == [m.position for m in page.messages]
    answer = next(m['content'] for m in returned['messages'] if m['position'] == 5)
    source = next(source for source in answer['sources'] if source['n'] == 1)
    assert source['source'] == {'id': URL, 'name': RESULT['title'], 'url': URL}
    assert source['document'] == [RESULT['text']]
    assert source['metadata'][0]['granularity'] == 'snippet'
    assert answer['model'] == MODEL
    assert returned['messages'][0]['content']['statusHistory'][0]['action'] == 'web_search'
    runtime.chat_events.assert_awaited_once_with(
        page.thread_id,
        as_user='owui:user:fictional-fern',
        after=2,
        through=5,
    )
    assert projection.await_count == 1


@pytest.mark.asyncio
async def test_not_modified_page_needs_no_presentation_read(dark_api, monkeypatch):
    from open_webui.soev import identity

    runtime = SimpleNamespace(get=AsyncMock())
    monkeypatch.setattr(identity, 'build_client', lambda: runtime)
    response = await dark_api.browser.get('/api/v1/chats/fictional-chat', headers={'If-None-Match': 'etag'})
    assert response.status_code == 304
    runtime.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_plain_turn_with_user_without_model_needs_no_events_read(dark_api, monkeypatch):
    from open_webui.soev import identity
    from open_webui.soev.threads import ProjectionPage
    from open_webui.test.soev.test_threads import page

    projected = ProjectionPage.model_validate(page(5))
    projected.messages[0].content = {'role': 'assistant', 'text': 'Hello', 'model': MODEL}
    projected.messages.insert(
        0, threads.MessageView(position=4, content={'role': 'user', 'text': 'Question'}, finished=True, version=None)
    )
    monkeypatch.setattr(
        dark_api.client, 'projection', AsyncMock(return_value=threads.ProjectionResult(projected, 'etag'))
    )
    runtime = SimpleNamespace(chat_events=AsyncMock())
    monkeypatch.setattr(identity, 'build_client', lambda: runtime)
    response = await dark_api.browser.get('/api/v1/chats/fictional-chat')
    assert response.status_code == 200
    assert response.json()['messages'][0]['content'] == {'role': 'user', 'text': 'Question'}
    assert response.json()['messages'][1]['content']['model'] == MODEL
    runtime.chat_events.assert_not_awaited()


@pytest.mark.asyncio
async def test_visible_citation_can_resolve_a_source_from_an_earlier_page(dark_api, monkeypatch):
    from open_webui.soev import identity
    from open_webui.soev.threads import ProjectionPage
    from open_webui.test.soev.test_threads import page

    projected = ProjectionPage.model_validate(page(5))
    projected.messages[0].content = {
        'role': 'assistant',
        'text': 'October 7.',
        'markers': [{'cited': [{'source': URL, 'written': 7}]}],
    }
    monkeypatch.setattr(
        dark_api.client, 'projection', AsyncMock(return_value=threads.ProjectionResult(projected, 'etag'))
    )
    runtime = SimpleNamespace(chat_events=AsyncMock(side_effect=[events()[2:], events()[:2]]))
    monkeypatch.setattr(identity, 'build_client', lambda: runtime)
    response = await dark_api.browser.get('/api/v1/chats/fictional-chat')
    assert response.json()['messages'][0]['content']['sources'][0]['n'] == 7
    assert [(call.kwargs['after'], call.kwargs['through']) for call in runtime.chat_events.await_args_list] == [
        (4, 5),
        (0, 4),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize('path', ['reserved', 'inputs'])
async def test_live_v2_sends_status_sources_and_catalog_model_without_local_writes(
    chat: test_agent_v2.Chat, monkeypatch, path
):
    from open_webui.test.soev.test_chat_paths import FictionalOrganizations

    monkeypatch.setattr(threads, 'enabled', lambda: True)
    monkeypatch.setattr(agent_v2, 'get_event_emitter', socket.get_event_emitter)
    monkeypatch.setattr(socket, 'WEBSOCKET_MANAGER', 'redis')
    delivered = []

    async def emit(kind, data, **kwargs):
        delivered.append(copy.deepcopy(data))

    monkeypatch.setattr(socket.sio, 'emit', emit)
    local = AsyncMock(side_effect=AssertionError('v2 must not persist local chat content'))
    monkeypatch.setattr(socket.Chats, 'add_message_status_to_chat_by_id_and_message_id', local)
    monkeypatch.setattr(socket.Chats, 'upsert_message_to_chat_by_id_and_message_id', local)
    client = agent_v2.identity.build_client()
    reserved = await client.chat_post('/v1/chat/threads:reserve', {'agent': 'test'}, as_user='owui:user:alice')
    thread_id = reserved['thread_id']
    if path == 'inputs':
        response = client.chat_stream(
            '/v1/chat/threads',
            {
                'thread_id': thread_id,
                'agent': 'test',
                'input': {'text': 'Earlier'},
            },
            as_user='owui:user:alice',
            thread_id=thread_id,
        )
        async for _ in response:
            pass
    records = FictionalOrganizations()
    records.records = [threads.Organization(id='chat', thread_id=thread_id)]
    turn = agent_v2.AgentTurn(
        client,
        {
            'chat_id': 'chat',
            'message_id': 'answer',
            'user_id': 'alice',
            'follow_thread': path == 'reserved',
        },
        'owui:user:alice',
        records,
    )
    chat.api.chat.turns = [
        [
            (
                'model_output',
                {
                    'content': '',
                    'reasoning': 'Look it up.',
                    'model': ENDPOINT,
                    'answered_model': MODEL,
                    'tool_calls': [{'id': 'search', 'name': 'web_search'}],
                },
            ),
            ('tool_output', {'call_id': 'search', 'elements': [RESULT]}),
            (
                'model_output',
                {
                    'content': 'October 7.',
                    'model': ENDPOINT,
                    'answered_model': MODEL,
                    'citations': [{'at': 10, 'status': 'resolved', 'source': URL, 'ref': URL}],
                },
            ),
        ]
    ]
    chunks = [chunk async for chunk in turn.run({'input': {'text': 'When?'}}, 'test')]
    assert not any('error' in chunk for chunk in chunks), chunks
    statuses = [event['data']['data'] for event in delivered if event['data']['type'] == 'status']
    assert any(s['action'] == 'web_search' and s['done'] is False for s in statuses)
    assert any(s['action'] == 'web_search' and s['done'] is True for s in statuses)
    sources = [event['data']['data'] for event in delivered if event['data']['type'] == 'source']
    assert sources[0]['source']['name'] == RESULT['title']
    assert sources[0]['document'] == [RESULT['text']]
    assert any(event['data'] == {'type': 'chat:completion', 'data': {'answered_model': MODEL}} for event in delivered)
    assert '[1]' in ''.join(c['choices'][0]['delta'].get('content', '') for c in chunks if c.get('choices'))
    local.assert_not_awaited()
    posts = [r.url.path for r in chat.api.chat.requests if r.method == 'POST']
    assert posts[-1] == ('/v1/chat/threads' if path == 'reserved' else f'/v1/chat/threads/{thread_id}/inputs')


@pytest.mark.asyncio
async def test_presentation_keeps_versions_and_excludes_future_and_child_sources():
    from open_webui.soev.chat_presentation import present_projection
    from open_webui.soev.threads import ProjectionPage
    from open_webui.test.soev.test_threads import page

    projected = ProjectionPage.model_validate(page(5))
    projected.messages[0].content = {
        'role': 'assistant',
        'text': 'Answer',
        'model': ENDPOINT,
        'markers': [{'at': 6, 'text': ' [7]', 'cited': [{'source': URL, 'written': 7}]}],
        'future': {'unknown': 'ignored'},
        'tool_calls': [None, {'unknown': True}],
    }
    raw = events()
    raw += [
        {
            'position': 6,
            'type': 'tool_output',
            'stream': 'root',
            'payload': {'elements': [{**RESULT, 'id': 'future', 'ref': 'future', 'text': 'Future secret'}]},
        },
        {
            'position': 2,
            'type': 'tool_output',
            'stream': 'root/child',
            'payload': {'elements': [{**RESULT, 'id': 'child', 'ref': 'child', 'text': 'Child secret'}]},
        },
    ]
    enriched = await present_projection(projected, raw)
    assert enriched.messages[0].version == projected.messages[0].version
    assert 'sources' not in projected.messages[0].content
    assert len(enriched.messages[0].content['sources']) == 1
    assert enriched.messages[0].content['sources'][0]['n'] == 7
    assert enriched.messages[0].content['sources'][0]['document'] == [RESULT['text']]
    assert enriched.messages[0].content['model'] == MODEL


@pytest.mark.asyncio
async def test_live_model_output_can_map_an_endpoint_from_catalog_facts(monkeypatch):
    from open_webui.soev import model_catalog
    from open_webui.soev.client import ChatEvent

    monkeypatch.setitem(model_catalog.CATALOG_CACHE, 'entries', [{'id': MODEL, 'endpoint_id': ENDPOINT}])
    turn = agent_v2.AgentTurn(None, {}, '')
    turn.emitter = AsyncMock()
    await turn.render(ChatEvent('model_output', {'stream': 'root', 'payload': {'content': 'Hi', 'model': ENDPOINT}}))
    turn.emitter.assert_awaited_with({'type': 'chat:completion', 'data': {'answered_model': MODEL}})


@pytest.mark.asyncio
async def test_live_endpoint_falls_back_to_requested_catalog_model(monkeypatch):
    from open_webui.soev import model_catalog
    from open_webui.soev.client import ChatEvent

    monkeypatch.setitem(model_catalog.CATALOG_CACHE, 'entries', [{'id': MODEL}])
    turn = agent_v2.AgentTurn(None, {}, '')
    turn.model = MODEL
    turn.emitter = AsyncMock()
    await turn.render(ChatEvent('model_output', {'stream': 'root', 'payload': {'content': 'Hi', 'model': ENDPOINT}}))
    turn.emitter.assert_awaited_with({'type': 'chat:completion', 'data': {'answered_model': MODEL}})


@pytest.mark.asyncio
async def test_reload_uses_each_turns_requested_model_without_endpoint_catalog_fields(monkeypatch):
    from open_webui.soev import model_catalog
    from open_webui.soev.chat_presentation import present_projection
    from open_webui.soev.threads import ProjectionPage
    from open_webui.test.soev.test_threads import page

    other = 'other/catalog-model'
    monkeypatch.setitem(model_catalog.CATALOG_CACHE, 'entries', [{'id': MODEL}, {'id': other}])
    projected = ProjectionPage.model_validate(page(3, 5))
    for message in projected.messages:
        message.content = {'role': 'assistant', 'model': ENDPOINT, 'text': 'Hi'}
    raw = [
        {'position': 1, 'type': 'input', 'stream': 'root', 'payload': {'model': MODEL}},
        {'position': 3, 'type': 'model_output', 'stream': 'root', 'payload': {'model': ENDPOINT}},
        {'position': 4, 'type': 'input', 'stream': 'root', 'payload': {'model': other}},
        {'position': 5, 'type': 'model_output', 'stream': 'root', 'payload': {'model': ENDPOINT}},
    ]
    result = await present_projection(projected, raw)
    assert [m.content['model'] for m in result.messages] == [MODEL, other]
