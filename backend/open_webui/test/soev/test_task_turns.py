"""Exercise typed tasks and caller isolation through fictional platform HTTP."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException, Request
from httpx import AsyncClient
from open_webui.routers import tasks
from open_webui.soev import model_catalog
from open_webui.soev.client import SoevApiError
from open_webui.soev.errors import problem_message
from open_webui.soev.task_turns import TaskTurnRequest, TaskTurnResult, task_request
from open_webui.utils.auth import get_verified_user
from pydantic import ValidationError
from starlette.responses import JSONResponse

KINDS = [
    ('title', 'title', {'title': 'Fictional answer 1'}),
    ('tags', 'tags', {'tags': ['Fictional answer 1']}),
    ('follow_up', 'follow_ups', {'follow_ups': ['Fictional answer 1']}),
    ('queries', 'queries', {'queries': ['Fictional answer 1']}),
    ('auto', 'autocomplete', {'text': 'Fictional answer 1'}),
    ('emoji', 'emoji', '🪶'),
    ('moa', 'moa', 'Fictional answer 1'),
]
FORM = {
    'model': 'fictional-chat-model',
    'chat_id': 'fictional-thread',
    'message_id': 'fictional-message',
    'messages': [{'id': 'fictional-message', 'role': 'user', 'content': 'Fictional source text'}],
    'prompt': 'Fictional draft',
    'type': 'web_search',
    'responses': ['Fictional first answer', 'Fictional second answer'],
}


@pytest_asyncio.fixture
async def task_api(chat_http, monkeypatch):
    monkeypatch.setattr(model_catalog, 'is_v2', lambda: True)
    chat_http.task_threads = {
        'owui:user:fern': {'fictional-thread', 'fictional-shared'},
        'owui:user:lark': {'fictional-shared'},
    }

    async def config(key):
        assert key.endswith('.enable') or key == 'task.autocomplete.input_max_length', key
        return 2000 if key.endswith('input_max_length') else True

    monkeypatch.setattr(tasks.Config, 'get', config)
    monkeypatch.setattr(tasks.Config, 'get_many', AsyncMock(side_effect=AssertionError('Task model settings used')))
    monkeypatch.setattr(tasks, 'generate_chat_completion', AsyncMock(side_effect=AssertionError('Template path used')))

    async def user(request: Request):
        name = request.headers.get('X-Fictional-User', 'fern')
        if name == 'anonymous':
            raise HTTPException(status_code=401)
        return SimpleNamespace(id=name, role='user')

    async def problem(request: Request, error: SoevApiError):
        return JSONResponse({'code': error.code, 'detail': problem_message(error, 'en')}, status_code=error.status)

    app = FastAPI()
    app.state.MODELS = {}
    app.include_router(tasks.router, prefix='/tasks')
    app.dependency_overrides[get_verified_user] = user
    app.add_exception_handler(SoevApiError, problem)
    async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test.invalid') as browser:
        yield SimpleNamespace(browser=browser, platform=chat_http)


@pytest.mark.asyncio
@pytest.mark.parametrize('route,kind,expected', KINDS)
async def test_routes_submit_typed_input_and_map_results(task_api, route, kind, expected):
    form = {**FORM, 'language': 'nl-NL', 'temperature': 0.9, 'agent': 'fictional-other', 'project': 'fictional-project'}
    response = await task_api.browser.post(f'/tasks/{route}/completions', json=form)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['model'] == 'task-model'
    content = result['choices'][0]['message']['content']
    assert (json.loads(content) if isinstance(expected, dict) else content) == expected
    [(subject, body)] = task_api.platform.task_bodies
    assert subject == 'owui:user:fern'
    assert set(body) == {'contract', 'task', 'source_thread', 'input', 'language', 'idempotency_key'}
    assert body['contract'] == 'soev.task/1' and body['task'] == kind
    assert body['source_thread'] == FORM['chat_id'] and body['language'] == 'nl-NL'
    expected_input = {
        'queries': {'text': FORM['prompt'], 'purpose': 'web_search'},
        'autocomplete': {'text': FORM['prompt']},
        'emoji': {'text': FORM['prompt']},
        'moa': {'text': FORM['prompt'], 'responses': FORM['responses']},
    }.get(kind, {})
    assert body['input'] == expected_input
    [request] = task_api.platform.requests
    assert request.url.path == '/v1/completions/task'
    assert request.headers['X-Soev-User-Token'] == 'fictional-login:owui:user:fern'
    assert 'Idempotency-Key' not in request.headers


@pytest.mark.asyncio
@pytest.mark.parametrize('route', [route for route, _, _ in KINDS])
@pytest.mark.parametrize('actor', ['lark', 'fictional-other-tenant'])
async def test_thread_tasks_match_missing_for_unreadable_threads(task_api, route, actor):
    responses = []
    for source in ['fictional-thread', 'fictional-missing']:
        response = await task_api.browser.post(
            f'/tasks/{route}/completions', json={**FORM, 'chat_id': source}, headers={'X-Fictional-User': actor}
        )
        responses.append(response)
    assert responses[0].status_code == responses[1].status_code == 404
    assert responses[0].content == responses[1].content
    assert responses[0].headers == responses[1].headers
    assert task_api.platform.task_results == {}


@pytest.mark.asyncio
@pytest.mark.parametrize('route', [route for route, _, _ in KINDS])
async def test_explicit_reader_can_run_thread_tasks(task_api, route):
    response = await task_api.browser.post(
        f'/tasks/{route}/completions',
        json={**FORM, 'chat_id': 'fictional-shared'},
        headers={'X-Fictional-User': 'lark'},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize('route,kind,expected', KINDS)
async def test_anonymous_tasks_never_reach_platform(task_api, route, kind, expected):
    response = await task_api.browser.post(
        f'/tasks/{route}/completions', json=FORM, headers={'X-Fictional-User': 'anonymous'}
    )
    assert response.status_code == 401
    assert task_api.platform.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize('route', ['title', 'tags', 'follow_up'])
async def test_thread_tasks_require_a_source(task_api, route):
    response = await task_api.browser.post(f'/tasks/{route}/completions', json={**FORM, 'chat_id': None})
    assert response.status_code == 422
    assert task_api.platform.requests == []


@pytest.mark.asyncio
async def test_retry_keys_follow_thread_kind_message_and_typed_input(task_api):
    async def run(route='title', **changes):
        response = await task_api.browser.post(f'/tasks/{route}/completions', json={**FORM, **changes})
        assert response.status_code == 200, response.text
        return task_api.platform.task_bodies[-1][1]['idempotency_key']

    first = await run()
    assert first == await run(model='fictional-other-model', temperature=0.8)
    assert first != await run(message_id='fictional-next-message')
    assert first != await run(chat_id='fictional-shared')
    assert first != await run('tags')
    draft = await run('auto')
    assert draft == await run('auto')
    assert draft != await run('auto', prompt='Fictional next draft')


@pytest.mark.asyncio
async def test_same_key_never_returns_another_callers_result(task_api):
    form = {**FORM, 'chat_id': 'fictional-shared'}
    results = []
    for actor in ['fern', 'lark', 'fern']:
        response = await task_api.browser.post(
            '/tasks/title/completions', json=form, headers={'X-Fictional-User': actor}
        )
        assert response.status_code == 200
        results.append(response.json()['choices'])
    assert len({body['idempotency_key'] for _, body in task_api.platform.task_bodies}) == 1
    assert results[0] == results[2] and results[0] != results[1]


@pytest.mark.asyncio
async def test_moa_adapts_one_json_result_to_frontend_sse(task_api):
    response = await task_api.browser.post('/tasks/moa/completions', json={**FORM, 'stream': True})
    assert response.status_code == 200
    assert response.headers['content-type'].startswith('text/event-stream')
    frames = [line.removeprefix('data: ') for line in response.text.splitlines() if line.startswith('data: ')]
    assert json.loads(frames[0])['choices'][0]['delta']['content'] == 'Fictional answer 1'
    assert json.loads(frames[1])['choices'][0]['finish_reason'] == 'stop'
    assert frames[-1] == '[DONE]'
    assert 'stream' not in task_api.platform.task_bodies[0][1]


@pytest.mark.asyncio
async def test_image_prompt_is_unavailable_in_platform_mode(task_api):
    response = await task_api.browser.post('/tasks/image_prompt/completions', json=FORM)
    assert response.status_code == 404
    assert task_api.platform.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize('route,kind,expected', KINDS)
async def test_platform_refusals_preserve_status_without_details(task_api, route, kind, expected):
    task_api.platform.failures = [503]
    response = await task_api.browser.post(f'/tasks/{route}/completions', json=FORM)
    assert response.status_code == 503
    assert 'injected_failure' not in response.json()['detail']
    assert not task_api.platform.task_results


@pytest.mark.parametrize(
    'kind,output',
    [
        ('title', {'title': 'x' * 257}),
        ('tags', {'tags': ['x'] * 17}),
        ('tags', {'tags': ['x' * 65]}),
        ('follow_ups', {'follow_ups': ['x'] * 9}),
        ('follow_ups', {'follow_ups': ['x' * 513]}),
        ('queries', {'queries': ['x'] * 9}),
        ('queries', {'queries': ['x' * 201]}),
        ('autocomplete', {'completion': 'x' * 4097}),
        ('emoji', {'emoji': 'x' * 33}),
        ('moa', {'response': 'x' * 16385}),
        ('title', {'tags': []}),
        ('title', {'title': 7}),
    ],
)
def test_result_bounds_match_wire_contract(kind, output):
    with pytest.raises(ValidationError):
        TaskTurnResult.model_validate({'contract': 'soev.task/1', 'task': kind, 'output': output, 'model': 'fictional'})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'raw',
    [
        {'choices': []},
        {'contract': 'soev.task/1', 'task': 'emoji', 'output': {'emoji': '🪶'}, 'model': 'fictional-model'},
    ],
)
async def test_invalid_result_is_refused_before_stream_headers(task_api, monkeypatch, raw):
    from open_webui.soev.client import SoevClient

    monkeypatch.setattr(SoevClient, 'submit_task', AsyncMock(return_value=raw))
    response = await task_api.browser.post('/tasks/moa/completions', json={**FORM, 'stream': True})
    assert response.status_code == 502
    assert response.headers['content-type'] == 'application/json'


def test_message_and_snapshot_keys_are_stable():
    request = Request({'type': 'http', 'headers': []})
    form = {key: value for key, value in FORM.items() if key != 'message_id'}
    original = task_request(request, 'title', form)
    assert original.idempotency_key == task_request(request, 'title', {**form, 'temperature': 0.1}).idempotency_key
    messages = [{'role': 'user', 'content': 'Fictional snapshot'}]
    without_ids = task_request(request, 'title', {**form, 'messages': messages})
    assert without_ids.idempotency_key == task_request(request, 'title', {**form, 'messages': messages}).idempotency_key
    assert without_ids.idempotency_key != original.idempotency_key
    reference = {'chat_id': FORM['chat_id']}
    assert (
        task_request(request, 'title', reference).idempotency_key
        != task_request(request, 'title', reference).idempotency_key
    )


def test_old_message_contract_is_deleted():
    from open_webui.soev.client import SoevClient

    with pytest.raises(ValidationError):
        TaskTurnRequest.model_validate({'messages': FORM['messages'], 'stream': False})
    assert not hasattr(SoevClient, 'complete_task')
    assert not hasattr(SoevClient, 'stream_task')
    assert not hasattr(model_catalog, 'task_body')


@pytest.mark.asyncio
async def test_generic_chat_helper_refuses_platform_messages(task_api):
    from open_webui.utils.chat import generate_chat_completion

    with pytest.raises(HTTPException) as error:
        await generate_chat_completion(SimpleNamespace(), FORM, SimpleNamespace(id='fern'))
    assert error.value.status_code == 400
    assert task_api.platform.requests == []


@pytest.mark.asyncio
async def test_queries_extract_only_raw_user_text(task_api):
    messages = [
        {'role': 'system', 'content': 'Fictional instructions'},
        {
            'role': 'user',
            'content': [
                {'type': 'text', 'text': 'Fictional question'},
                {'type': 'image_url', 'image_url': {'url': 'https://example.invalid/fictional.png'}},
            ],
        },
        {'role': 'assistant', 'content': 'Fictional answer'},
    ]
    response = await task_api.browser.post(
        '/tasks/queries/completions', json={**FORM, 'prompt': None, 'messages': messages, 'type': 'retrieval'}
    )
    assert response.status_code == 200
    assert task_api.platform.task_bodies[0][1]['input'] == {'text': 'Fictional question', 'purpose': 'retrieval'}


@pytest.mark.asyncio
@pytest.mark.parametrize('route', ['auto', 'emoji', 'queries', 'moa'])
async def test_text_tasks_can_run_without_a_saved_thread(task_api, route):
    response = await task_api.browser.post(f'/tasks/{route}/completions', json={**FORM, 'chat_id': None})
    assert response.status_code == 200
    assert 'source_thread' not in task_api.platform.task_bodies[0][1]


@pytest.mark.asyncio
@pytest.mark.parametrize('route', ['emoji', 'queries', 'moa'])
async def test_text_input_bounds_are_checked_before_sending(task_api, route):
    response = await task_api.browser.post(f'/tasks/{route}/completions', json={**FORM, 'prompt': 'x' * 16385})
    assert response.status_code == 422
    assert task_api.platform.requests == []


@pytest.mark.asyncio
async def test_disabled_generation_never_calls_platform(task_api, monkeypatch):
    monkeypatch.setattr(tasks.Config, 'get', AsyncMock(return_value=False))
    for route in ['title', 'tags', 'follow_up', 'queries', 'auto']:
        response = await task_api.browser.post(f'/tasks/{route}/completions', json=FORM)
        assert response.status_code in {200, 400}
        assert 'choices' not in response.json()
    assert task_api.platform.requests == []


@pytest.mark.asyncio
async def test_service_cannot_submit_a_person_task(task_api):
    from open_webui.soev.client import SoevClient

    turn = TaskTurnRequest(task='emoji', input={'text': 'Fictional text'}, idempotency_key='fictional-key')
    client = SoevClient('https://soev.invalid', 'fictional-key').as_service()
    with pytest.raises(ValueError):
        await client.submit_task(turn, as_user='owui:user:fern')
    assert task_api.platform.requests == []
