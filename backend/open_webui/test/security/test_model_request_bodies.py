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
    f'/api/v1/tasks/{task}/completions' for task in ('auto', 'emoji', 'follow_up', 'moa', 'queries', 'tags', 'title')
]
MAIN_PATHS = [
    '/api/chat/completions',
    '/api/v1/chat/completions',
    '/api/chat/completed',
]
PATHS = TASK_PATHS + MAIN_PATHS
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
    from open_webui.routers import tasks
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
    monkeypatch.setattr(application, 'check_model_access', AsyncMock())
    monkeypatch.setattr(application, 'AGENT_API_ENABLED', False)
    seen = []

    async def capture(request, form_data, user):
        seen.append(copy.deepcopy(form_data))
        return copy.deepcopy(REPLY)

    async def process_payload(request, form_data, user, metadata, model):
        return form_data, metadata, []

    async def process_response(response, context):
        return response

    monkeypatch.setattr(tasks, 'generate_chat_completion', capture)
    monkeypatch.setattr(application, 'chat_completed_handler', capture)
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
    response = client.post(path, json=payload)
    assert response.status_code == 200, response.text
    assert seen, 'A 200 without a downstream call is not a compatibility control'
    assert len(seen) == 1, f'{path} reached the handler {len(seen)} times, expected once'
    forwarded = seen[-1]
    if path in TASK_PATHS:
        assert forwarded['metadata']['task_body'] == payload
        assert TEXT in forwarded['messages'][0]['content']
    else:
        for key, value in payload.items():
            assert forwarded[key] == value, key
        assert response.json() == REPLY


@pytest.mark.parametrize('path', PATHS)
@pytest.mark.parametrize('body', [[], 'text', 42, True, None])
def test_non_object_body_is_rejected_before_handler(path, body, harness):
    client, seen = harness
    response = client.post(
        path,
        content=json.dumps(body),
        headers={'Content-Type': 'application/json'},
    )
    assert response.status_code == 422, response.text
    assert isinstance(response.json()['detail'], list)
    assert seen == []


@pytest.fixture(scope='module')
def derived_fields():
    from openapi_surface import writable_string_fields

    return writable_string_fields(json.loads((REPO / 'security/openapi.json').read_text()))


@pytest.mark.parametrize('path', PATHS)
def test_model_input_strings_are_visible(path, derived_fields):
    fields = set(derived_fields[f'POST {path}'])
    assert 'model' in fields
    assert {
        'messages[].role',
        'messages[].content',
        'messages[].content[].text',
        'messages[].content[].image_url.url',
        'messages[].content[].source.data',
        'messages[].tool_calls[].function.arguments',
    } <= fields
    if path in TASK_PATHS:
        assert {'prompt', 'responses[]'} <= fields
    else:
        assert {'tools[].function.description', 'tools[].description', 'system', 'system[].text'} <= fields


@pytest.mark.parametrize(
    'dependency_name',
    [
        'chat_completion_body',
        'task_completion_body',
    ],
)
def test_body_dependency_preserves_missing_null_and_vendor_values(dependency_name):
    from open_webui.services import model_request_bodies

    dependency = getattr(model_request_bodies, dependency_name)
    model = dependency.__annotations__['body']
    app = FastAPI()
    app.add_api_route('/body', dependency, methods=['POST'])
    payloads = [
        {},
        {field: None for field in model.model_fields},
        {'model': MODEL, 'vendor': {'opaque': [1, None, 'text']}, 'stream': 'false'},
    ]
    with TestClient(app) as client:
        for payload in payloads:
            response = client.post('/body', json=payload)
            assert response.status_code == 200, response.text
            assert response.json() == payload


def test_nested_vendor_extensions_and_argument_formats():
    from open_webui.services.model_request_bodies import chat_completion_body

    payload = {
        'messages': [
            {
                'role': 'assistant',
                'content': None,
                'tool_calls': [
                    {
                        'id': 'call-1',
                        'type': 'function',
                        'function': {
                            'name': 'weather',
                            'arguments': '{"city":"Amsterdam"}',
                            'vendor': True,
                        },
                    },
                    {'function': {'name': 'weather', 'arguments': {'city': 'Amsterdam', 'limit': 2}}},
                ],
            },
            {
                'role': 'tool',
                'tool_call_id': 'call-1',
                'content': [
                    {
                        'type': 'tool_result',
                        'tool_use_id': 'call-1',
                        'content': [
                            {'type': 'text', 'text': 'Sunny', 'vendor': {'extra': True}},
                        ],
                    },
                    {'type': 'vendor_block', 'vendor': {'data': 'opaque'}},
                ],
            },
        ],
    }
    app = FastAPI()
    app.add_api_route('/body', chat_completion_body, methods=['POST'])
    with TestClient(app) as client:
        response = client.post('/body', json=payload)
    assert response.status_code == 200, response.text
    assert response.json() == payload
