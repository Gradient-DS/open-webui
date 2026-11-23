"""Preserve agent diagnostics across chat response and event boundaries."""

import copy
import importlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, Request
from open_webui.soev import threads
from open_webui.utils import middleware
from open_webui.utils.turn_error import TurnError
from starlette.responses import JSONResponse, StreamingResponse

ERROR: TurnError = {
    'code': 'upstream_error',
    'message': 'The agent request failed.',
    'status': 502,
    'upstream_status': 500,
    'trace_id': '1234567890abcdef1234567890abcdef',
}


@pytest.mark.asyncio
@pytest.mark.parametrize('stream', [True, False])
async def test_http_turn_failure_keeps_diagnostics_in_the_chat_event(monkeypatch, stream):
    main = importlib.import_module('open_webui.main')
    monkeypatch.setattr(threads, 'enabled', lambda: True)
    monkeypatch.setattr(main, 'AGENT_API_ENABLED', True)
    monkeypatch.setattr(main, 'BYPASS_MODEL_ACCESS_CONTROL', True)
    monkeypatch.setattr(main.Models, 'get_model_by_id', AsyncMock(return_value=None))
    monkeypatch.setattr(main.Config, 'get', AsyncMock(side_effect=lambda key, default=None: default))
    monkeypatch.setattr(main.system_prompt, 'for_model', AsyncMock(return_value=None))

    async def payload(request, form, user, metadata, model):
        return form, metadata, []

    monkeypatch.setattr(main, 'process_chat_payload', payload)
    monkeypatch.setattr(main, 'call_agent_api', AsyncMock(return_value=JSONResponse({'error': ERROR}, status_code=502)))
    emitter = AsyncMock()
    monkeypatch.setattr(main, 'get_event_emitter', AsyncMock(return_value=emitter))
    local = AsyncMock(side_effect=AssertionError('v2 must not write local chat content'))
    monkeypatch.setattr(main.Chats, 'upsert_message_to_chat_by_id_and_message_id', local)
    app = FastAPI()
    app.state.redis = None
    app.state.MODELS = {'fictional-model': {'id': 'fictional-model'}}
    request = Request({'type': 'http', 'headers': [], 'app': app})
    await main.chat_completion(
        request,
        {
            'model': 'fictional-model',
            'chat_id': 'fictional-chat',
            'id': 'fictional-answer',
            'messages': [{'role': 'user', 'content': 'Fictional prompt'}],
            'stream': stream,
        },
        SimpleNamespace(id='fictional-fern', role='admin'),
    )
    assert emitter.await_args_list[0].args == ({'type': 'chat:message:error', 'data': {'error': ERROR}},)
    assert emitter.await_args_list[1].args == ({'type': 'chat:tasks:cancel'},)
    local.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('stream', [True, False])
@pytest.mark.parametrize('v2', [True, False])
async def test_response_middleware_preserves_the_failure_trace_and_structured_fields(monkeypatch, caplog, stream, v2):
    failure: TurnError = {**ERROR, 'message': 'Fictional private refusal cedar-secret.pdf'}
    monkeypatch.setattr(threads, 'enabled', lambda: v2)
    emitter = AsyncMock()
    persisted = AsyncMock()
    monkeypatch.setattr(middleware.Chats, 'upsert_message_to_chat_by_id_and_message_id', persisted)
    monkeypatch.setattr(middleware, 'get_filter_functions', AsyncMock(return_value=[]))
    monkeypatch.setattr(middleware, 'get_system_oauth_token', AsyncMock(return_value=None))
    app = FastAPI()
    app.state.redis = None
    ctx = {
        'request': Request({'type': 'http', 'app': app, 'headers': []}),
        'form_data': {'model': 'fictional-model', 'messages': [{'role': 'user', 'content': 'Fictional prompt'}]},
        'user': SimpleNamespace(id='fictional-fern'),
        'model': {'id': 'fictional-model'},
        'metadata': {
            'chat_id': 'fictional-chat',
            'message_id': 'fictional-answer',
            'route_to_agent': True,
            'trace_id': 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
        },
        'tasks': {},
        'events': [],
        'event_emitter': emitter,
        'event_caller': None,
    }

    async def chunks():
        yield 'data: ' + json.dumps({'error': copy.deepcopy(failure)}) + '\n\n'
        yield 'data: [DONE]\n\n'

    response = await middleware.process_chat_response(
        StreamingResponse(chunks(), media_type='text/event-stream') if stream else {'error': copy.deepcopy(failure)},
        ctx,
    )
    if isinstance(response, StreamingResponse):
        async for _ in response.body_iterator:
            pass
    errors = [
        call.args[0]['data']['error'] for call in emitter.await_args_list if call.args[0].get('data', {}).get('error')
    ]
    assert errors == [failure]
    assert 'cedar-secret.pdf' not in caplog.text
    if v2:
        persisted.assert_not_awaited()
    else:
        saved = [call.args[2]['error'] for call in persisted.await_args_list if 'error' in call.args[2]]
        assert saved == [failure]
