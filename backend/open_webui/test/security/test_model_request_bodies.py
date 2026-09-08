"""HTTP controls for the Phase 4a handlers, with provider and database I/O stubbed."""

import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[4]
TASK_PATHS = [
    f'/api/v1/tasks/{task}/completions'
    for task in ('auto', 'emoji', 'follow_up', 'image_prompt', 'moa', 'queries', 'tags', 'title')
]
OLLAMA_PATHS = [
    f'/ollama/{path}{suffix}'
    for path in ('api/chat', 'v1/chat/completions', 'v1/completions', 'v1/messages')
    for suffix in ('', '/{url_idx}')
]
MAIN_PATHS = [
    '/api/chat/completions',
    '/api/v1/chat/completions',
    '/api/chat/completed',
    '/api/chat/actions/{action_id}',
    '/api/embeddings',
    '/api/v1/embeddings',
    '/api/message',
    '/api/v1/messages',
]
PATHS = TASK_PATHS + OLLAMA_PATHS + ['/openai/chat/completions'] + MAIN_PATHS
MODEL = 'vendor-model'
TEXT = 'Explain the northern lights.'
REPLY = {
    'id': 'reply-1',
    'model': MODEL,
    'choices': [
        {'index': 0, 'message': {'role': 'assistant', 'content': 'Charged particles.'}, 'finish_reason': 'stop'}
    ],
    'usage': {'prompt_tokens': 10, 'completion_tokens': 4},
}


def vendor_payload(path):
    payload = {
        'model': MODEL,
        'messages': [{'role': 'user', 'content': TEXT, 'vendor_message': {'trace': 'nested-extra'}}],
        'stream': False,
        'vendor_extension': {'routing': ['region-a'], 'budget': 17},
    }
    if path in TASK_PATHS:
        payload.update(prompt=TEXT, type='web_search', responses=['Solar wind.', 'Magnetic fields.'])
    elif '/v1/completions' in path:
        payload.pop('messages')
        payload['prompt'] = TEXT
        payload['suffix'] = 'Answer briefly.'
    elif 'embeddings' in path:
        payload.pop('messages')
        payload['input'] = [TEXT, 'What causes auroras?']
        payload['dimensions'] = 3
    elif path.endswith('/message') or '/messages' in path:
        payload.update(
            max_tokens=128,
            system=[{'type': 'text', 'text': 'Be concise.', 'cache_control': {'type': 'ephemeral'}}],
            tools=[
                {
                    'name': 'weather',
                    'description': 'Look up weather',
                    'input_schema': {'type': 'object', 'properties': {'city': {'type': 'string'}}},
                    'vendor_tool': True,
                }
            ],
        )
        payload['messages'][0]['content'] = [
            {'type': 'text', 'text': TEXT, 'cache_control': {'type': 'ephemeral'}},
            {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': 'aW1hZ2U='}},
        ]
    else:
        payload['tools'] = [
            {
                'type': 'function',
                'function': {
                    'name': 'weather',
                    'description': 'Look up weather',
                    'parameters': {'type': 'object', 'properties': {'city': {'type': 'string'}}},
                    'vendor_function': 'extra',
                },
                'vendor_tool': True,
            }
        ]
        if '/ollama/api/chat' not in path:
            payload['messages'][0]['content'] = [
                {'type': 'text', 'text': TEXT, 'vendor_block': 'extra'},
                {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,aW1hZ2U=', 'detail': 'low'}},
            ]
    return payload


@pytest.fixture(scope='module')
def application():
    spec = importlib.util.spec_from_file_location('model_body_exporter', REPO / 'scripts/security/export_openapi.py')
    exporter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(exporter)
    exporter.build()  # Use the exporter's static-asset protection during app import.
    from open_webui import main

    return main


@pytest.fixture
def harness(application, monkeypatch):
    from open_webui.routers import ollama, openai, tasks
    from open_webui.utils.auth import get_verified_user

    # Mount the actual registered endpoints without app lifespan or unrelated middleware.
    app = FastAPI()
    selected = [route for route in application.app.routes if route.path in PATHS and 'POST' in route.methods]
    assert {route.path for route in selected} == set(PATHS)
    for route in selected:
        app.add_api_route(route.path, route.endpoint, methods=['POST'])
    user = SimpleNamespace(id='control-user', name='Control', email='control@example.test', role='admin')
    app.dependency_overrides[get_verified_user] = lambda: user
    app.state.MODELS = {MODEL: {'id': MODEL, 'owned_by': 'openai'}}
    app.state.OPENAI_MODELS = {MODEL: {'id': MODEL, 'urlIdx': 0}}

    async def config(key, default=None):
        if key.endswith('.enable'):
            return True
        if key.endswith('prompt_template') or key in ('task.model.default', 'task.model.external'):
            return ''
        if key == 'task.autocomplete.input_max_length':
            return 0
        return default

    monkeypatch.setattr(application.Config, 'get', config)
    monkeypatch.setattr(application.Models, 'get_model_by_id', AsyncMock(return_value=None))
    for module in (application, ollama, openai):
        monkeypatch.setattr(module, 'check_model_access', AsyncMock())
    monkeypatch.setattr(application, 'resolve_agent_route', lambda **kwargs: False)
    monkeypatch.setattr(ollama, 'get_ollama_url', AsyncMock(return_value=('http://provider.invalid', 0)))
    monkeypatch.setattr(openai, 'get_openai_connection', AsyncMock(return_value=('http://provider.invalid/v1', '', {})))
    monkeypatch.setattr(openai, 'get_headers_and_cookies', AsyncMock(return_value=({}, {})))
    monkeypatch.setattr(openai, 'cleanup_response', AsyncMock())
    seen = []

    async def capture(request, form_data, user):
        seen.append(copy.deepcopy(form_data))
        return copy.deepcopy(REPLY)

    async def pipeline(request, form_data, user, models):
        return form_data

    async def process_payload(request, form_data, user, metadata, model):
        return form_data, metadata, []

    async def process_response(response, context):
        return response

    async def action(request, action_id, form_data, user):
        assert action_id == 'control-action'
        return await capture(request, form_data, user)

    async def send_request(url, payload, **kwargs):
        seen.append(json.loads(payload))
        return copy.deepcopy(REPLY)

    async def provider_request(**kwargs):
        seen.append(json.loads(kwargs['data']))
        return SimpleNamespace(status=200, headers={}, json=AsyncMock(return_value=copy.deepcopy(REPLY)))

    monkeypatch.setattr(tasks, 'process_pipeline_inlet_filter', pipeline)
    monkeypatch.setattr(tasks, 'generate_chat_completion', capture)
    monkeypatch.setattr(ollama, 'send_request', send_request)
    monkeypatch.setattr(openai, 'get_session', AsyncMock(return_value=SimpleNamespace(request=provider_request)))
    monkeypatch.setattr(application, 'generate_embeddings', capture)
    monkeypatch.setattr(application, 'chat_completed_handler', capture)
    monkeypatch.setattr(application, 'chat_action_handler', action)
    monkeypatch.setattr(application, 'process_chat_payload', process_payload)
    monkeypatch.setattr(application, 'chat_completion_handler', capture)
    monkeypatch.setattr(application, 'build_chat_response_context', AsyncMock(return_value=None))
    monkeypatch.setattr(application, 'process_chat_response', process_response)
    with TestClient(app) as client:
        yield client, seen


@pytest.mark.parametrize('path', PATHS)
def test_vendor_payload_reaches_handler_unchanged(path, harness):
    client, seen = harness
    payload = vendor_payload(path)
    response = client.post(path.replace('{url_idx}', '0').replace('{action_id}', 'control-action'), json=payload)
    assert response.status_code == 200, response.text
    assert len(seen) == 1, 'A 200 without a downstream call is not a compatibility control'
    forwarded = seen[0]
    if path in TASK_PATHS:
        assert forwarded['metadata']['task_body'] == payload
        assert TEXT in forwarded['messages'][0]['content']
    elif path in ('/api/message', '/api/v1/messages'):
        # Existing Anthropic conversion intentionally selects fields; pin the converted shape.
        assert forwarded['model'] == MODEL
        assert forwarded['messages'][0] == {'role': 'system', 'content': payload['system']}
        assert forwarded['messages'][1]['content'][0] == payload['messages'][0]['content'][0]
        assert forwarded['tools'][0]['function']['name'] == 'weather'
        assert response.json()['content'] == [{'type': 'text', 'text': 'Charged particles.'}]
    else:
        for key, value in payload.items():
            assert forwarded[key] == value, key
        assert response.json() == REPLY


@pytest.mark.parametrize('path', PATHS)
@pytest.mark.parametrize('body', [[], 'text', 42, True, None])
def test_non_object_body_is_rejected_before_handler(path, body, harness):
    client, seen = harness
    response = client.post(
        path.replace('{url_idx}', '0').replace('{action_id}', 'control-action'),
        content=json.dumps(body),
        headers={'Content-Type': 'application/json'},
    )
    assert response.status_code == 422, response.text
    assert isinstance(response.json()['detail'], list)
    assert seen == []
