"""Regression tests for application findings the attack plane recorded.

Each test names its entry in security/plane-findings.md and drives the handler
through FastAPI, as the plane did, so an unhandled exception reads as a 500.
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


def test_accepting_an_invite_stores_a_password_hash_not_a_coroutine(monkeypatch):
    # invites/{token}/accept called the async get_password_hash without await, so
    # the account insert bound a coroutine: every invite acceptance answered 500.
    from open_webui.routers import invites

    invite = SimpleNamespace(
        revoked_at=None,
        accepted_at=None,
        expires_at=4102444800,
        email='invitee@example.com',
        name='Invitee',
        role='user',
    )

    async def found(*args, **kwargs):
        return invite

    stored = {}

    async def insert(**kwargs):
        stored.update(kwargs)
        return None

    monkeypatch.setattr(invites.Invites, 'get_invite_by_token', found)
    monkeypatch.setattr(invites.Auths, 'insert_new_auth', insert)
    monkeypatch.setattr(invites, 'validate_password', lambda password: True)
    path = '/{token}/accept'
    _client(_endpoint(invites.router, path), path).post('/t/accept', json={'password': 'Invitee-Passw0rd!'})
    assert isinstance(stored.get('password'), str), type(stored.get('password'))


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


def test_testing_an_unconfigured_email_sender_is_refused(monkeypatch):
    # configs/email/test answered 500 for "Email Graph API credentials not
    # configured": the admin's configuration, not a server fault.
    from open_webui.routers import configs
    from open_webui.services.email import graph_mail_client

    async def enabled(*keys):
        return {'email.enable_invites': True, 'email.from_name': 'Attack'}

    async def unconfigured(**kwargs):
        raise ValueError('Email Graph API credentials not configured')

    monkeypatch.setattr(configs.Config, 'get_many', enabled)
    monkeypatch.setattr(graph_mail_client, 'send_mail', unconfigured)
    path = '/email/test'
    response = _client(_endpoint(configs.router, path), path).post(path)
    assert response.status_code == 400, (response.status_code, response.text)


UNREACHABLE = 'http://127.0.0.1:1/attack.py'


@pytest.mark.parametrize('module_name', ['functions', 'tools'])
def test_loading_code_from_an_unreachable_url_is_refused(module_name):
    # functions|tools/load/url answered 500 when the admin-supplied URL could not
    # be fetched: a bad URL, not a server fault.
    import importlib

    module = importlib.import_module(f'open_webui.routers.{module_name}')
    path = '/load/url'
    response = _client(_endpoint(module.router, path), path).post(path, json={'url': UNREACHABLE})
    assert 400 <= response.status_code < 500, (response.status_code, response.text)


def test_verifying_an_unreachable_ollama_connection_is_refused():
    # ollama/verify turned the client error for an admin-supplied URL into a 500.
    from open_webui.routers import ollama

    path = '/verify'
    response = _client(_endpoint(ollama.router, path), path).post(path, json={'url': 'http://127.0.0.1:1', 'key': ''})
    assert 400 <= response.status_code < 500, (response.status_code, response.text)


def test_verifying_an_unreachable_openai_connection_is_refused(monkeypatch):
    # openai/verify turned the client error for an admin-supplied URL into a 500.
    from open_webui.routers import openai

    async def no_headers(*args, **kwargs):
        return {}, {}

    monkeypatch.setattr(openai, 'get_headers_and_cookies', no_headers)
    path = '/verify'
    response = _client(_endpoint(openai.router, path), path).post(path, json={'url': 'http://127.0.0.1:1', 'key': ''})
    assert 400 <= response.status_code < 500, (response.status_code, response.text)


def test_an_external_retrieval_test_that_cannot_reach_its_store_is_refused(monkeypatch):
    # knowledge/external/connections/{id}/retrieve-test: the Qdrant client raised
    # AssertionError on a store it could not use, and the connection test
    # answered 500. A failed connection test is the answer, not a fault.
    import asyncio

    from fastapi import HTTPException
    from open_webui.routers import knowledge

    async def unreachable(*args, **kwargs):
        raise AssertionError('Search returned None')

    monkeypatch.setattr(knowledge, 'retrieve_external_knowledge_for_connection', unreachable)
    source = knowledge.ExternalKnowledgeSourceForm(name='test', config={'content_field': 'payload.text'})
    connection = {'id': 'c1', 'name': 'Attack', 'provider': 'qdrant'}
    user = SimpleNamespace(id='control-user', role='admin')
    with pytest.raises(HTTPException) as refused:
        asyncio.run(knowledge._get_external_source_test_result(None, connection, source, 'probe', 1, user))
    assert refused.value.status_code == 400


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


@pytest.mark.parametrize('size', ['<img src=x>', '1x2x3', 'axb'])
def test_an_image_size_that_is_not_width_x_height_is_refused(size, monkeypatch):
    # PLANE-006: images/generations parsed the size with int() outside any try.
    import asyncio

    from fastapi import HTTPException
    from open_webui.routers import images

    async def config():
        return SimpleNamespace(IMAGE_SIZE='512x512')

    monkeypatch.setattr(images, 'get_image_config', config)
    form = images.CreateImageForm(prompt='probe', size=size)
    user = SimpleNamespace(id='control-user', role='admin')
    with pytest.raises(HTTPException) as refused:
        asyncio.run(images.image_generations(None, form, user=user))
    assert refused.value.status_code == 400


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
    assert response.status_code == 400, (response.status_code, response.text)


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


def test_an_embedding_engine_the_app_cannot_build_is_refused_and_not_saved(monkeypatch):
    # PLANE-001: retrieval/embedding/update saved the engine, then
    # get_embedding_function raised ValueError('Unknown embedding engine'): a 500,
    # and the poisoned engine stayed saved for every later request.
    import asyncio

    from fastapi import HTTPException
    from open_webui.routers import retrieval

    config = SimpleNamespace(
        RAG_EMBEDDING_ENGINE='openai',
        RAG_EMBEDDING_MODEL='model',
        RAG_EMBEDDING_BATCH_SIZE=1,
        ENABLE_ASYNC_EMBEDDING=True,
        RAG_EMBEDDING_CONCURRENT_REQUESTS=0,
        RAG_OPENAI_API_BASE_URL='',
        RAG_OPENAI_API_KEY='',
        RAG_OLLAMA_BASE_URL='',
        RAG_OLLAMA_API_KEY='',
        RAG_AZURE_OPENAI_BASE_URL='',
        RAG_AZURE_OPENAI_API_KEY='',
        RAG_AZURE_OPENAI_API_VERSION='',
    )
    saved = []

    async def state():
        return config

    async def upsert(values):
        saved.append(values)

    monkeypatch.setattr(retrieval, 'get_rag_config_state', state)
    monkeypatch.setattr(retrieval, 'unload_embedding_model', lambda request, config: None)
    monkeypatch.setattr(retrieval, 'get_ef', lambda *args, **kwargs: None)
    monkeypatch.setattr(retrieval.Config, 'upsert', upsert)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    form = retrieval.EmbeddingModelUpdateForm(RAG_EMBEDDING_ENGINE='<style>probe</style>', RAG_EMBEDDING_MODEL='model')
    with pytest.raises(HTTPException) as refused:
        asyncio.run(retrieval.update_embedding_config(request, form, user=SimpleNamespace(role='admin')))
    assert refused.value.status_code == 400
    assert not saved


@pytest.mark.parametrize('field', ['FOLDER_MAX_FILE_COUNT', 'AUTOMATION_MAX_COUNT', 'AUTOMATION_MIN_INTERVAL'])
def test_a_limit_that_is_not_a_whole_number_is_refused(field):
    # PLANE-001: auths/admin/config ran int() over three int|str form fields.
    from open_webui.routers import auths

    payload = {
        name: (False if info.annotation is bool else '')
        for name, info in auths.AdminConfig.model_fields.items()
        if info.is_required()
    }
    payload[field] = '[click](file:///etc/passwd)'
    path = '/admin/config'
    response = _client(_endpoint(auths.router, path), path).post(path, json=payload)
    assert response.status_code == 400, (response.status_code, response.text)


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


def test_ollama_embeddings_without_an_input_are_refused():
    # With PLANE-004's model check passing, a body with no input reached
    # GenerateEmbedForm, whose ValidationError nobody caught: 500.
    import asyncio

    from fastapi import HTTPException

    from open_webui.utils.embeddings import generate_embeddings

    models = {'m': {'id': 'm', 'owned_by': 'ollama'}}
    request = SimpleNamespace(state=SimpleNamespace(), app=SimpleNamespace(state=SimpleNamespace(MODELS=models)))
    user = SimpleNamespace(id='control-user', role='admin')
    with pytest.raises(HTTPException) as refused:
        asyncio.run(generate_embeddings(request, {'model': 'm'}, user))
    assert refused.value.status_code == 400
