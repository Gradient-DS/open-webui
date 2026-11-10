"""Regression tests for application findings the attack plane recorded.

Each test drives the handler through FastAPI, as the plane did, so an unhandled exception reads as a 500.
"""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _client(endpoint, path, method='POST', models=None):
    from open_webui.utils.auth import get_admin_user, get_verified_user

    app = FastAPI()
    app.add_api_route(path, endpoint, methods=[method], response_model=None)
    user = SimpleNamespace(id='control-user', role='admin', email='control@example.com', name='Control')
    app.dependency_overrides[get_verified_user] = lambda: user
    app.dependency_overrides[get_admin_user] = lambda: user
    app.state.MODELS = models or {}
    return TestClient(app, raise_server_exceptions=False)


def _endpoint(router, path, method='POST'):
    return next(route.endpoint for route in router.routes if route.path == path and method in route.methods)


async def _enabled(*args, **kwargs):
    return True


async def _nothing(*args, **kwargs):
    return None


@pytest.mark.parametrize('task', ['title', 'follow_up', 'tags', 'image_prompt', 'queries', 'auto', 'emoji', 'moa'])
def test_a_task_completion_without_a_model_is_refused(task, monkeypatch):
    # PLANE-009: every task handler read form_data['model'], so a body without
    # one raised KeyError -- 500 x75 per task in the seeding pass.
    from open_webui.routers import tasks

    monkeypatch.setattr(tasks.Config, 'get', _enabled)
    path = f'/{task}/completions'
    response = _client(_endpoint(tasks.router, path), path).post(
        path, json={'messages': [{'role': 'user', 'content': 'probe'}], 'prompt': 'probe'}
    )
    assert 400 <= response.status_code < 500, (response.status_code, response.text)


def test_the_ollama_anthropic_proxy_refuses_a_body_without_a_model(monkeypatch):
    # /ollama/v1/messages resolved the model with .get('model', '') and then
    # indexed payload['model']: KeyError, 500 x181.
    from open_webui.routers import ollama

    monkeypatch.setattr(ollama.Config, 'get', _enabled)
    monkeypatch.setattr(ollama.Models, 'get_model_by_id', _nothing)
    monkeypatch.setattr(ollama, 'check_model_access', _nothing)
    path = '/v1/messages'
    response = _client(_endpoint(ollama.router, path), path).post(
        path, json={'messages': [{'role': 'user', 'content': 'probe'}], 'max_tokens': 1}
    )
    assert response.status_code == 400, (response.status_code, response.text)


def test_no_error_message_factory_is_used_uncalled():
    # /openai/audio/speech raised HTTPException(detail=ERROR_MESSAGES.OPENAI_NOT_FOUND):
    # the lambda itself, which the exception handler cannot serialise -- 500.
    import re
    from pathlib import Path

    import open_webui

    root = Path(open_webui.__file__).parent
    factories = re.findall(r'^\s+([A-Z_]+)\s*=\s*lambda', (root / 'constants.py').read_text(), re.M)
    assert factories
    uncalled = [
        f'{path.relative_to(root)}:{number}'
        for path in root.rglob('*.py')
        if 'test' not in path.relative_to(root).parts
        for number, line in enumerate(path.read_text(errors='replace').splitlines(), 1)
        if any(re.search(rf'ERROR_MESSAGES\.{name}\b(?!\s*\()', line) for name in factories)
    ]
    assert not uncalled, uncalled


def test_a_stored_timezone_that_is_not_a_zone_key_reads_as_utc():
    # users/usage: a user's stored timezone was a URL, ZoneInfo raised ValueError
    # (only ZoneInfoNotFoundError was caught), and the usage page answered 500.
    from zoneinfo import ZoneInfo

    from open_webui.models.chat_messages import _timezone

    assert _timezone('http://[::ffff:169.254.169.254]/probe') == ZoneInfo('UTC')
    assert _timezone('Europe/Amsterdam') == ZoneInfo('Europe/Amsterdam')


UNREACHABLE = 'http://127.0.0.1:1/attack.py'


@pytest.mark.parametrize(
    'data',
    [{'content': 'a string'}, {'content': {'md': 5}}, {'content': ['x']}, ['not', 'a', 'dict'], {'content': None}],
)
def test_a_malformed_stored_note_still_lists(data):
    # PLANE-003: a stored note whose data is not the expected shape broke every
    # later listing and search for its owner.
    from open_webui.routers.notes import _truncate_note_data

    assert _truncate_note_data(data) == {'content': {'md': ''}}


def test_embeddings_for_an_unknown_model_are_refused_not_crashed():
    # PLANE-004: generate_embeddings raised a bare Exception('Model not found'),
    # and both embeddings aliases answered 500.
    import asyncio

    from fastapi import HTTPException
    from open_webui.utils.embeddings import generate_embeddings

    request = SimpleNamespace(state=SimpleNamespace(), app=SimpleNamespace(state=SimpleNamespace(MODELS={})))
    user = SimpleNamespace(id='control-user', role='admin')
    with pytest.raises(HTTPException) as refused:
        asyncio.run(generate_embeddings(request, {'model': 'plane-004-unregistered', 'input': 'probe'}, user))
    assert 400 <= refused.value.status_code < 500


def test_conversation_feedback_is_answered(monkeypatch):
    # evaluations/feedback/conversation/{chat_id} called a FeedbackTable method the
    # v0.11.3 merge had dropped, and without await: every request answered 500.
    from open_webui.routers import evaluations

    monkeypatch.setattr(evaluations.Feedbacks, 'get_conversation_feedback_by_chat_id_and_user_id', _nothing)
    path = '/feedback/conversation/{chat_id}'
    response = _client(_endpoint(evaluations.router, path, 'GET'), path, 'GET').get('/feedback/conversation/c1')
    assert response.status_code == 200, (response.status_code, response.text)


def test_a_totp_secret_that_is_not_base32_does_not_verify():
    # PLANE-014: pyotp raised binascii.Error on the request's secret, and
    # 2fa/totp/enable answered 500 instead of refusing the code.
    from open_webui.utils.totp import verify_totp

    assert verify_totp('not base32!', '123456', None) == (False, None)


@pytest.mark.parametrize('body', ['[1, 2]', '"text"', '7', 'true', 'null'])
def test_speech_refuses_a_body_that_is_not_an_object(body, monkeypatch):
    # PLANE-015: every TTS engine handler writes into the payload as an object;
    # one shapes pass got a 500 per JSON type.
    from open_webui.routers import audio

    async def config(key, default=None):
        return {'audio.tts.engine': 'openai'}.get(key, default)

    monkeypatch.setattr(audio.Config, 'get', config)
    path = '/speech'
    response = _client(_endpoint(audio.router, path), path).post(
        path, content=body.encode(), headers={'Content-Type': 'application/json'}
    )
    assert response.status_code == 422, (response.status_code, response.text)


def test_a_legacy_webhook_url_the_guard_refuses_does_not_break_notifications(monkeypatch):
    # PLANE-016: migrating a stored legacy webhook_url ran it through the SSRF
    # guard, whose ValueError reached the notifications page as a 500.
    import asyncio

    from open_webui.utils import notifications

    async def user(*args, **kwargs):
        return SimpleNamespace(settings={'notifications': {'webhook_url': 'http://169.254.169.254/latest'}})

    monkeypatch.setattr(notifications.Users, 'get_user_by_id', user)
    monkeypatch.setattr(notifications.Users, 'update_user_settings_by_id', _nothing)
    loaded = asyncio.run(notifications._load_notifications('control-user'))
    assert not loaded.get('targets')


def test_accepting_a_data_warning_returns_the_logged_row(monkeypatch):
    # PLANE-005: insert_log validated the ORM row without from_attributes, so the
    # handler answered 500 after the audit row was already committed.
    import asyncio
    from contextlib import asynccontextmanager

    from open_webui.models import data_warnings

    class Session:
        def add(self, row):
            pass

        async def commit(self):
            pass

        async def refresh(self, row):
            pass

    @asynccontextmanager
    async def session(db=None):
        yield Session()

    monkeypatch.setattr(data_warnings, 'get_async_db_context', session)
    form = data_warnings.DataWarningLogForm(chat_id='chat-1', model_id='model-1', capabilities=['web'])
    logged = asyncio.run(data_warnings.DataWarningLogs.insert_log('control-user', form))
    assert logged.user_id == 'control-user'


@pytest.mark.parametrize(
    'order_by, direction',
    [('no_such', 'asc'), ('title', 'sideways'), ('chat', 'asc'), ('meta', 'desc'), ('__class__', 'desc'), ('', 'asc')],
)
def test_a_chat_list_sort_the_request_cannot_have_keeps_the_default(order_by, direction):
    # PLANE-008: chats/archived, /shared and /list/user/{user_id} handed the query
    # string's order_by and direction to getattr, and raised on anything unknown.
    from open_webui.models.chats import Chat
    from open_webui.models.ordering import request_order

    default = (Chat.updated_at.desc(),)
    assert request_order(Chat, order_by, direction, default) is default


def test_a_chat_list_sort_by_a_real_column_is_honoured():
    from open_webui.models.chats import Chat
    from open_webui.models.ordering import request_order

    (clause,) = request_order(Chat, 'title', 'ASC', ())
    assert str(clause) == str(Chat.title.asc())


def test_autocompletion_without_a_prompt_is_refused(monkeypatch):
    # tasks/auto/completions measured the prompt's length before checking it
    # existed: with autocompletion on, a body without one raised TypeError (500).
    from open_webui.routers import tasks

    async def config(key, default=None):
        return {'task.autocomplete.enable': True, 'task.autocomplete.input_max_length': 100}.get(key, default)

    monkeypatch.setattr(tasks.Config, 'get', config)
    path = '/auto/completions'
    response = _client(_endpoint(tasks.router, path), path).post(path, json={'model': 'm', 'messages': []})
    assert response.status_code == 400, (response.status_code, response.text)


def test_ollama_embeddings_without_an_input_are_refused(monkeypatch):
    # With PLANE-004's model check passing, a body with no input reached
    # GenerateEmbedForm, whose ValidationError nobody caught: 500.
    import asyncio

    from fastapi import HTTPException

    from open_webui.utils.embeddings import generate_embeddings

    monkeypatch.setattr('open_webui.utils.embeddings.check_model_access', _nothing)

    models = {'m': {'id': 'm', 'owned_by': 'ollama'}}
    request = SimpleNamespace(state=SimpleNamespace(), app=SimpleNamespace(state=SimpleNamespace(MODELS=models)))
    user = SimpleNamespace(id='control-user', role='admin')
    with pytest.raises(HTTPException) as refused:
        asyncio.run(generate_embeddings(request, {'model': 'm'}, user))
    assert refused.value.status_code == 400


def test_moving_a_folder_to_its_current_parent_is_not_a_clash(monkeypatch):
    # folders/{id}/update/parent found the folder itself as "already exists" when
    # the target parent was its current one: a no-op move answered 400.
    from open_webui.routers import folders

    folder = SimpleNamespace(id='f1', name='Attack', parent_id=None, user_id='control-user')

    async def found(*args, **kwargs):
        return folder

    async def no_subtree(*args, **kwargs):
        return []

    monkeypatch.setattr(folders, 'check_folders_permission', _nothing)
    monkeypatch.setattr(folders.Folders, 'get_folder_by_id_and_user_id', found)
    monkeypatch.setattr(folders.Folders, 'get_folder_by_parent_id_and_user_id_and_name', found)
    monkeypatch.setattr(folders.Folders, 'get_folder_ids_by_id_and_user_id_in_subtree', no_subtree)
    monkeypatch.setattr(folders.Folders, 'update_folder_parent_id_by_id_and_user_id', found)
    monkeypatch.setattr(folders, 'publish_event', _nothing)
    path = '/{id}/update/parent'
    response = _client(_endpoint(folders.router, path), path).post('/f1/update/parent', json={'parent_id': None})
    assert response.status_code == 200, (response.status_code, response.text)
