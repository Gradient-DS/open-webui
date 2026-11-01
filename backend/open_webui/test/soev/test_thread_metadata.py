"""Fictional owner metadata flows through the browser and post-turn tasks."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import Request
from open_webui.soev import thread_metadata
from open_webui.soev.threads import IndexPage, ThreadMetadataResponse
from open_webui.test.soev.test_chat_paths import dark_api as dark_api
from open_webui.test.soev.test_threads import index_item
from starlette.responses import StreamingResponse


@pytest.mark.asyncio
async def test_manual_title_and_tags_persist_before_canonical_events(dark_api, monkeypatch):
    dark_api.state['metadata'] = True
    events = AsyncMock()
    monkeypatch.setattr(thread_metadata, 'emit_metadata', events)
    base = '/api/v1/chats/fictional-chat'
    renamed = await dark_api.browser.post(base, json={'chat': {'title': '  Fictional Cedar  '}})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json() == {
        'thread_id': 'fictional-thread',
        'title': 'Fictional Cedar',
        'preferences': {'fictional_private': True},
        'tags': [],
    }
    added = await dark_api.browser.post(base + '/tags', json={'name': 'Fictional Grove'})
    assert added.json() == ['fictional_grove']
    assert dark_api.state['projection']['preferences'] == {'fictional_private': True, 'tags': ['fictional_grove']}
    assert events.await_args_list[-1].args[2].tags == ['fictional_grove']
    removed = await dark_api.browser.request('DELETE', base + '/tags', json={'name': 'fictional_grove'})
    assert removed.json() == []
    assert events.await_count == 3
    assert all(isinstance(call.args[2], ThreadMetadataResponse) for call in events.await_args_list)
    assert [call.method for call in dark_api.calls] == ['GET', 'PATCH'] * 3
    assert not dark_api.records.writes
    listed = await dark_api.browser.get('/api/v1/chats/?include_folders=true')
    assert listed.json()[0]['title'] == 'Fictional Cedar'


@pytest.mark.asyncio
async def test_non_owner_metadata_404_has_no_persisted_event(dark_api, monkeypatch):
    dark_api.state.update(metadata=True, owner=False)
    events = AsyncMock()
    monkeypatch.setattr(thread_metadata, 'emit_metadata', events)
    for suffix, body in [('', {'title': 'Fictional refused'}), ('/tags', {'name': 'Fictional refused'})]:
        response = await dark_api.browser.post('/api/v1/chats/fictional-chat' + suffix, json=body)
        assert response.status_code == 404
        assert response.json() == {'state': 'unavailable'}
    events.assert_not_awaited()
    assert not dark_api.records.writes


@pytest.mark.asyncio
async def test_metadata_rejects_legacy_content_and_csrf_without_platform_write(dark_api):
    for body in [{'messages': []}, {'title': 1}, {'tags': 'fictional'}, {'title': 'Fictional', 'history': {}}]:
        response = await dark_api.browser.post('/api/v1/chats/fictional-chat', json=body)
        assert response.status_code == 422
    response = await dark_api.browser.post(
        '/api/v1/chats/fictional-chat', content='{}', headers={'Content-Type': 'text/plain'}
    )
    assert response.status_code == 415
    assert not dark_api.calls


@pytest.mark.asyncio
async def test_tag_listing_follows_index_cursors_and_reads_shared_public_tags(dark_api, monkeypatch):
    shared = {**index_item('fictional-shared', owned=False), 'tags': ['fictional_shared']}
    owned = {**index_item(), 'tags': ['fictional_owned']}
    monkeypatch.setattr(
        dark_api.client,
        'index',
        AsyncMock(
            side_effect=[
                IndexPage(items=[owned], next_cursor='fictional-next'),
                IndexPage(items=[shared], next_cursor=None),
            ]
        ),
    )
    projection = AsyncMock(side_effect=AssertionError('Tag listing must read the index'))
    monkeypatch.setattr(dark_api.client, 'projection', projection)
    response = await dark_api.browser.get('/api/v1/chats/all/tags')
    assert response.json() == ['fictional_owned', 'fictional_shared']
    assert dark_api.client.index.await_args_list[1].kwargs['cursor'] == 'fictional-next'
    dark_api.client.index.side_effect = [
        IndexPage(items=[owned], next_cursor='fictional-next'),
        IndexPage(items=[shared], next_cursor=None),
    ]
    filtered = await dark_api.browser.post('/api/v1/chats/tags', json={'name': 'fictional_shared'})
    assert filtered.json()[0]['id'] == 'fictional-shared'
    assert filtered.json()[0]['readonly'] is True
    projection.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('fallback', [False, True])
@pytest.mark.parametrize('browser', [False, True])
async def test_post_turn_title_tags_use_persistence_and_canonical_events(dark_api, monkeypatch, fallback, browser):
    dark_api.state['metadata'] = True
    dark_api.state['projection'] = {
        'thread_id': 'fictional-thread',
        'generation': 'fictional-generation',
        'title': None,
        'tags': [],
        'preferences': {'fictional_private': True},
        'messages': [],
        'next_cursor': None,
    }
    generated = AsyncMock(
        side_effect=[
            {} if fallback else {'choices': [{'message': {'content': '{"title":"  Fictional Cedar  "}'}}]},
            {'choices': [{'message': {'content': '{"tags":["Fictional Grove"]}'}}]},
        ]
    )
    monkeypatch.setattr(thread_metadata, 'generate', generated)
    events = AsyncMock()
    monkeypatch.setattr(thread_metadata, 'emit_metadata', events)
    ctx = {
        'request': Request({'type': 'http', 'app': dark_api.app}),
        'user': SimpleNamespace(id='fictional-fern'),
        'metadata': {
            'chat_id': 'fictional-chat',
            **({'user_message': {'content': 'Fictional fallback'}} if browser else {}),
        },
        'tasks': {'title_generation': True, 'tags_generation': True},
        'form_data': {} if browser else {'messages': [{'role': 'user', 'content': 'Fictional fallback'}]},
    }
    await thread_metadata.complete(ctx)
    expected = 'Fictional fallback' if fallback else 'Fictional Cedar'
    assert dark_api.state['projection']['title'] == expected
    assert dark_api.state['projection']['tags'] == ['fictional_grove']
    assert events.await_args_list[0].args[2].title == expected
    assert events.await_args_list[1].args[2].tags == ['fictional_grove']
    assert [call.method for call in dark_api.calls] == ['GET', 'GET', 'PATCH', 'GET', 'PATCH']
    assert not dark_api.records.writes


@pytest.mark.asyncio
async def test_manual_metadata_emits_saved_fields_from_patch_response(dark_api, monkeypatch):
    from open_webui.socket.main import sio

    dark_api.state['metadata'] = True
    events = AsyncMock()
    monkeypatch.setattr(sio, 'emit', events)
    response = await dark_api.browser.post(
        '/api/v1/chats/fictional-chat', json={'title': '  Fictional Cedar  ', 'tags': ['Fictional Grove']}
    )
    assert response.status_code == 200, response.text
    assert [call.method for call in dark_api.calls] == ['GET', 'PATCH']
    assert events.await_count == 2
    for call, field, value in zip(
        events.await_args_list, ['title', 'tags'], ['Fictional Cedar', ['fictional_grove']], strict=True
    ):
        assert call.args == (
            'events',
            {'chat_id': 'fictional-chat', 'message_id': None, 'data': {'type': f'chat:{field}', 'data': value}},
        )
        assert call.kwargs == {'room': 'user:fictional-fern'}


@pytest.mark.asyncio
async def test_failed_post_turn_patch_emits_no_metadata_event(dark_api, monkeypatch):
    dark_api.state.update(metadata=True, owner=False)
    generated = AsyncMock(return_value={'choices': [{'message': {'content': '{"tags":["Fictional Grove"]}'}}]})
    monkeypatch.setattr(thread_metadata, 'generate', generated)
    events = AsyncMock()
    monkeypatch.setattr(thread_metadata, 'emit_metadata', events)
    await thread_metadata.complete(
        {
            'request': Request({'type': 'http', 'app': dark_api.app}),
            'user': SimpleNamespace(id='fictional-fern'),
            'metadata': {'chat_id': 'fictional-chat'},
            'tasks': {'tags_generation': True},
            'form_data': {'messages': []},
        }
    )
    events.assert_not_awaited()


@pytest.mark.asyncio
async def test_post_turn_metadata_runs_as_task_turn_on_the_chat(dark_api, monkeypatch):
    async def setting(key, *args, **kwargs):
        return key.endswith('.enable')

    monkeypatch.setattr(thread_metadata.Config, 'get', setting)
    task = AsyncMock(return_value={'choices': [{'message': {'content': '{"title":"Fictional Cedar"}'}}]})
    monkeypatch.setattr(thread_metadata.task_turns, 'complete_task', task)
    request = Request({'type': 'http', 'app': dark_api.app})
    user = SimpleNamespace(id='fictional-fern')
    messages = [{'role': 'user', 'content': 'Fictional prompt'}]
    for kind in ['title', 'tags', 'follow_ups']:
        await thread_metadata.generate(request, kind, 'fictional-chat', messages, user)
        assert task.await_args.args == (request, kind, {'chat_id': 'fictional-chat', 'messages': messages}, user)
    assert not dark_api.records.writes


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [False, True])
@pytest.mark.parametrize('task', ['title_generation', 'tags_generation', 'follow_up_generation'])
async def test_stream_runs_metadata_only_after_successful_drain(monkeypatch, failure, task):
    done = AsyncMock()
    monkeypatch.setattr(thread_metadata, 'complete', done)

    async def chunks():
        yield (
            'data: '
            + json.dumps(
                {'error': 'fictional failure'} if failure else {'choices': [{'delta': {'content': 'Fictional answer'}}]}
            )
            + '\n\n'
        )
        yield 'data: [DONE]\n\n'

    response = await thread_metadata.finish_response(StreamingResponse(chunks()), {'tasks': {task: True}})
    done.assert_not_awaited()
    assert '[DONE]' in ''.join([chunk async for chunk in response.body_iterator])
    if thread_metadata._metadata_tasks:
        await asyncio.gather(*thread_metadata._metadata_tasks)
    assert done.await_count == (0 if failure else 1)


@pytest.mark.asyncio
async def test_cancelled_stream_does_not_generate_metadata(monkeypatch):
    complete = AsyncMock()
    monkeypatch.setattr(thread_metadata, 'complete', complete)

    async def chunks():
        yield 'data: {"choices":[{"delta":{"content":"Partial answer"}}]}\n\n'
        yield 'data: [DONE]\n\n'

    response = await thread_metadata.finish_response(StreamingResponse(chunks()), {'tasks': {'title_generation': True}})
    assert 'Partial answer' in await anext(response.body_iterator)
    await response.body_iterator.aclose()
    complete.assert_not_awaited()


@pytest.mark.asyncio
async def test_done_precedes_slow_metadata_and_reader_close_does_not_cancel_it(monkeypatch):
    from open_webui.soev import acting

    started, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def complete(ctx):
        started.set()
        await release.wait()
        assert ctx['assistant_message']['content'] == 'Answer'
        assert await acting.user_token('owui:user:fictional-fern') == 'fictional-login'
        finished.set()

    monkeypatch.setattr(thread_metadata, 'complete', complete)

    async def chunks():
        yield 'data: {"choices":[{"delta":{"content":"Answer"}}]}\n\n'
        yield 'data: [DONE]\n\n'
        raise AssertionError('The browser must not need to drain past DONE')

    response = await thread_metadata.finish_response(StreamingResponse(chunks()), {'tasks': {'title_generation': True}})
    reader = response.body_iterator
    await anext(reader)
    requester = acting._requester_ref.set('owui:user:fictional-fern')
    resolver = acting._token_resolver.set(AsyncMock(return_value='fictional-login'))
    try:
        assert await asyncio.wait_for(anext(reader), 1) == 'data: [DONE]\n\n'
        await asyncio.wait_for(started.wait(), 1)
        assert not finished.is_set()
        await reader.aclose()
        assert thread_metadata._metadata_tasks
    finally:
        # Simulate the authentication dependency exiting with the response.
        acting._token_resolver.reset(resolver)
        acting._requester_ref.reset(requester)
        release.set()
        await asyncio.gather(*thread_metadata._metadata_tasks)
        await reader.aclose()
    assert finished.is_set()
    assert not thread_metadata._metadata_tasks


@pytest.mark.asyncio
async def test_detached_metadata_exception_is_logged_and_discarded(monkeypatch, caplog):
    monkeypatch.setattr(thread_metadata, 'complete', AsyncMock(side_effect=RuntimeError('metadata failed')))
    thread_metadata.detach_complete({})
    await asyncio.gather(*thread_metadata._metadata_tasks)
    assert 'Thread metadata task failed' in caplog.text
    assert not thread_metadata._metadata_tasks


@pytest.mark.asyncio
@pytest.mark.parametrize('stream', [False, True])
@pytest.mark.parametrize('follow_ups_only', [False, True])
async def test_browser_session_completes_tasks_and_emits_saved_metadata_after_reader_close(
    dark_api, identity_config, monkeypatch, stream, follow_ups_only
):
    from open_webui.socket.main import sio
    from open_webui.soev import client
    from open_webui.soev.task_turns import TaskTurnRequest, task_request
    from open_webui.utils.middleware import build_chat_response_context, process_chat_response

    dark_api.state.update(
        metadata=True,
        projection={
            'thread_id': 'fictional-thread',
            'generation': 'fictional-generation',
            'title': None,
            'tags': [],
            'preferences': {'fictional_private': True},
            'messages': [],
            'next_cursor': None,
        },
    )
    outputs = {
        'title': {'title': '  Fictional Cedar  '},
        'tags': {'tags': ['Fictional Grove']},
        'follow_ups': {'follow_ups': ['What grows here?']},
    }
    upstream = client._shared_client()

    async def handle(request):
        if request.url.path != '/v1/completions/task':
            return await upstream.send(request)
        dark_api.calls.append(request)
        assert request.headers['X-Soev-User-Token'] == 'fictional-login'
        turn = TaskTurnRequest.model_validate_json(request.content)
        assert turn.source_thread == 'fictional-thread'
        assert (
            turn.idempotency_key
            == task_request(
                ctx['request'], turn.task, {'chat_id': 'fictional-thread', 'message_id': 'fictional-assistant'}
            ).idempotency_key
        )
        return httpx.Response(
            200,
            json={'contract': 'soev.task/1', 'task': turn.task, 'output': outputs[turn.task], 'model': 'task-model'},
        )

    async def setting(key, default=None, **kwargs):
        return key in {'task.title.enable', 'task.tags.enable', 'task.follow_up.enable'}

    monkeypatch.setattr(thread_metadata.Config, 'get', setting)
    events = AsyncMock()
    monkeypatch.setattr(sio, 'emit', events)
    tasks = {'follow_up_generation': True}
    if not follow_ups_only:
        tasks.update(title_generation=True, tags_generation=True)
    ctx = await build_chat_response_context(
        Request({'type': 'http', 'app': dark_api.app, 'headers': []}),
        {},
        SimpleNamespace(id='fictional-fern'),
        {'id': 'fictional-model'},
        {
            'chat_id': 'fictional-chat',
            'message_id': 'fictional-assistant',
            'session_id': 'fictional-session',
            'follow_thread': True,
            'user_message': {'id': 'fictional-user', 'content': 'Fictional prompt'},
        },
        tasks,
        [],
    )
    message = {'role': 'assistant', 'content': 'Fictional answer'}

    async def chunks():
        yield 'data: ' + json.dumps({'choices': [{'delta': message}]}) + '\n\n'
        yield 'data: [DONE]\n\n'

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as transport:
        monkeypatch.setattr(client, '_shared_client', lambda: transport)
        response = await process_chat_response(
            StreamingResponse(chunks(), media_type='text/event-stream')
            if stream
            else {'choices': [{'message': message}]},
            ctx,
        )
        if stream:
            while '[DONE]' not in await anext(response.body_iterator):
                pass
            # Match threadCompletion.ts: stop reading immediately at [DONE].
            await response.body_iterator.aclose()
            await asyncio.gather(*thread_metadata._metadata_tasks)

    submitted = [json.loads(call.content)['task'] for call in dark_api.calls if call.method == 'POST']
    assert submitted == (['follow_ups'] if follow_ups_only else ['title', 'tags', 'follow_ups'])
    expected = []
    if not follow_ups_only:
        assert dark_api.state['projection']['title'] == 'Fictional Cedar'
        assert dark_api.state['projection']['tags'] == ['fictional_grove']
        expected = [('chat:title', 'Fictional Cedar', None), ('chat:tags', ['fictional_grove'], None)]
    expected.append(('chat:message:follow_ups', {'follow_ups': ['What grows here?']}, 'fictional-assistant'))
    assert events.await_count == len(expected)
    for call, (kind, value, message_id) in zip(events.await_args_list, expected, strict=True):
        assert call.args == (
            'events',
            {'chat_id': 'fictional-chat', 'message_id': message_id, 'data': {'type': kind, 'data': value}},
        )
        assert call.kwargs == {'room': 'user:fictional-fern'}
    assert not dark_api.records.writes


@pytest.mark.asyncio
async def test_composer_features_persist_without_overwriting_other_preferences(dark_api):
    from open_webui.test.soev.test_threads import page

    dark_api.state.update(
        metadata=True,
        projection={
            **page(1, 2),
            'tags': ['saved'],
            'preferences': {'tags': ['saved'], 'fallback': True},
        },
    )
    features = {'web_search': False, 'document_writer': True, 'tool_ids': []}
    response = await dark_api.browser.post('/api/v1/chats/fictional-chat', json={'features': features})
    assert response.status_code == 200, response.text
    assert response.json()['preferences'] == {'tags': ['saved'], 'fallback': True, 'features': features}
    assert not dark_api.records.writes


@pytest.mark.asyncio
@pytest.mark.parametrize('features', [{'web_search': 'false'}, {'history': {}}, {'tool_ids': [1]}])
async def test_composer_preferences_reject_content_and_malformed_values(dark_api, features):
    response = await dark_api.browser.post('/api/v1/chats/fictional-chat', json={'features': features})
    assert response.status_code == 422
    assert not dark_api.calls
