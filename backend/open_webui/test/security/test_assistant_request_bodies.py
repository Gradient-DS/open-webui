"""Pin the opt-in split contract at the real chat-completion HTTP seam."""

import copy
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from open_webui.test.security.test_model_request_bodies import application as load_application


@pytest.fixture(scope='module')
def application(tmp_path_factory):
    """Import the actual app without connecting to a deployment's vector service."""
    with pytest.MonkeyPatch.context() as patch:
        temporary = tmp_path_factory.mktemp('assistant-seam')
        patch.setenv('DATABASE_URL', f'sqlite:///{temporary}/test.db')
        patch.setenv('DATA_DIR', str(temporary))
        patch.setenv('VECTOR_DB', 'weaviate')
        for part in ('TYPE', 'USER', 'PASSWORD', 'HOST', 'PORT', 'NAME'):
            patch.setenv(f'DATABASE_{part}', '')
        from open_webui.retrieval.vector.dbs.weaviate_multitenancy import WeaviateClient

        patch.setattr(WeaviateClient, '__init__', lambda self: None)
        yield load_application.__wrapped__()


@pytest.fixture
def split_harness(application, monkeypatch):  # noqa: C901 - Hand-written I/O doubles share fixture state.
    """Use actual model access checks with explicit in-memory grants and rows."""
    from open_webui.models.models import ModelModel
    from open_webui.utils import assistant_requests, models
    from open_webui.utils.agent_routing import AgentRoute
    from open_webui.utils.auth import get_verified_user

    def row(id, base=None, active=True, params=None):
        return ModelModel(
            id=id,
            name=id,
            user_id='owner',
            base_model_id=base,
            is_active=active,
            params=params or {},
            meta={'capabilities': {'vision': True}, 'knowledge': [{'id': 'kb'}], 'skillIds': ['skill']},
            created_at=1,
            updated_at=1,
        )

    rows = {
        'llm': row('llm', params={'temperature': 0.1, 'seed': 4}),
        'other-llm': row('other-llm'),
        'assistant': row('assistant', 'stale-private-llm', params={'system': 'Assistant prompt', 'temperature': 0.8}),
        'inactive': row('inactive', 'stale-private-llm', False),
        'override': row('override'),
        'unreadable': row('unreadable', 'stale-private-llm'),
    }
    allowed = {'llm', 'other-llm', 'assistant', 'inactive', 'override'}
    seen, processed, provider = [], [], []
    chats, messages, bindings, writes = {}, {}, [], []
    message_lookups = []

    async def get_row(id, **kwargs):
        return rows.get(id)

    async def grants(**kwargs):
        return kwargs['resource_id'] in allowed

    async def groups(*args, **kwargs):
        return []

    async def config(key, default=None):
        if key == 'openai.enable':
            return True
        return {'temperature': 0.0, 'top_p': 0.9} if key == 'models.default_params' else default

    async def insert(chat_id, user_id, form):
        chats[chat_id] = SimpleNamespace(meta={}, chat=copy.deepcopy(form.chat), variables=form.variables)

    async def upsert(chat_id, message_id, data):
        writes.append((chat_id, message_id, copy.deepcopy(data)))

    async def owned(*args, **kwargs):
        return True

    async def noop(*args, **kwargs):
        return None

    async def get_chat(id):
        return chats.get(id)

    async def get_owned_chat(id, user_id):
        chat = chats.get(id)
        return chat if chat and getattr(chat, 'user_id', 'user') == user_id else None

    async def is_owner(id, user_id):
        return await get_owned_chat(id, user_id) is not None

    async def has_assistant_message(id):
        message_lookups.append(id)
        return any(message.role == 'assistant' for message in messages.get(id, []))

    async def get_messages(id):
        raise AssertionError('Binding preflight must not load all messages')

    async def bind(id, assistant_id):
        bindings.append((id, assistant_id))
        chats[id].meta['assistant_id'] = assistant_id
        return True

    async def payload(request, form_data, user, metadata, model):
        processed.append(copy.deepcopy(model))
        return form_data, metadata, []

    async def capture(request, form_data, *args, **kwargs):
        seen.append(copy.deepcopy(form_data))
        return {'choices': []}

    async def context(*args, **kwargs):
        return None

    async def response(value, ctx):
        return value

    monkeypatch.setattr(application.Models, 'get_model_by_id', get_row)
    monkeypatch.setattr(models.AccessGrants, 'has_access', grants)
    monkeypatch.setattr(models.Groups, 'get_groups_by_member_id', groups)
    monkeypatch.setattr(application.Config, 'get', config)
    monkeypatch.setattr(application, 'BYPASS_MODEL_ACCESS_CONTROL', False)
    monkeypatch.setattr(application, 'BYPASS_ADMIN_ACCESS_CONTROL', False)
    monkeypatch.setattr(application, 'check_model_access', models.check_model_access)
    monkeypatch.setattr(application, 'resolve_agent_route', lambda **kwargs: AgentRoute(True))
    monkeypatch.setattr(application, 'FEATURE_AGENT_PICKER', False)
    monkeypatch.setattr(application, 'process_chat_payload', payload)
    monkeypatch.setattr(application, 'call_agent_api', capture)
    monkeypatch.setattr(application, 'chat_completion_handler', capture)
    monkeypatch.setattr(application, 'build_chat_response_context', context)
    monkeypatch.setattr(application, 'process_chat_response', response)
    monkeypatch.setattr(application.Chats, 'get_chat_by_id', get_chat)
    monkeypatch.setattr(application.Chats, 'get_chat_by_id_and_user_id', get_owned_chat)
    monkeypatch.setattr(application.Chats, 'upsert_message_to_chat_by_id_and_message_id', upsert)
    monkeypatch.setattr(application.Chats, 'insert_new_chat', insert)
    monkeypatch.setattr(application, 'emit_chat_list_event', noop)
    monkeypatch.setattr(application, 'publish_event', noop)
    monkeypatch.setattr(application, 'cleanup_task', noop)
    monkeypatch.setattr(application, 'has_active_tasks', owned)
    monkeypatch.setattr(application.Chats, 'is_chat_owner', is_owner)
    monkeypatch.setattr(application.Chats, 'update_chat_by_id', noop)
    monkeypatch.setattr(application.Chats, 'update_chat_variables_by_id', noop)
    monkeypatch.setattr(application.Chats, 'bind_chat_assistant_by_id', bind)
    monkeypatch.setattr(assistant_requests.ChatMessages, 'get_messages_by_chat_id', get_messages)
    monkeypatch.setattr(assistant_requests.ChatMessages, 'has_assistant_message', has_assistant_message)
    app = FastAPI()
    app.add_api_route('/api/chat/completions', application.chat_completion, methods=['POST'])
    user = SimpleNamespace(id='user', role='user', name='User', email='user@example.test')
    app.dependency_overrides[get_verified_user] = lambda: user
    app.state.MODELS = {
        'llm': {
            'id': 'llm',
            'name': 'LLM',
            'owned_by': 'openai',
            'info': {'meta': {'capabilities': {'vision': False}}},
        },
        'other-llm': {'id': 'other-llm', 'owned_by': 'openai'},
        'assistant': {
            'id': 'assistant',
            'name': 'Legacy Assistant',
            'owned_by': 'openai',
            'info': rows['assistant'].model_dump(exclude={'params'}),
        },
    }
    app.state.redis = None

    @app.middleware('http')
    async def internal_request(request, call_next):
        request.state.internal = True
        return await call_next(request)

    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client,
            app=app,
            rows=rows,
            allowed=allowed,
            seen=seen,
            processed=processed,
            provider=provider,
            chats=chats,
            messages=messages,
            message_lookups=message_lookups,
            bindings=bindings,
            writes=writes,
            user=user,
        )


def send(h, **overrides):
    """Send a minimal completion with optional contract overrides."""
    return h.client.post(
        '/api/chat/completions',
        json={
            'model': 'llm',
            'assistant_id': 'assistant',
            'messages': [{'role': 'user', 'content': 'Hello'}],
            'stream': False,
            **overrides,
        },
    )


def test_split_keeps_llm_and_assistant_prompt(split_harness):
    """The request-private composition reaches the agent without a body assistant field."""
    h = split_harness
    before = copy.deepcopy(h.app.state.MODELS)
    response = send(h, params={'temperature': 0.6})
    assert response.status_code == 200, response.text
    body = h.seen[-1]
    assert body['model'] == body['metadata']['model']['id'] == 'llm'
    assert body['metadata']['system_prompt'] == 'Assistant prompt'
    assert body['params'] == {'temperature': 0.6, 'seed': 4, 'system': 'Assistant prompt', 'top_p': 0.9}
    assert body['metadata']['model']['info']['meta']['capabilities']['vision'] is False
    assert body['metadata']['model']['info']['meta']['skillIds'] == ['skill']
    assert 'assistant_id' not in body
    assert h.processed[-1] == body['metadata']['model']
    assert h.app.state.MODELS == before


@pytest.mark.parametrize('assistant_id', ['missing', 'inactive', 'override', 'unreadable'])
def test_invalid_assistants_are_indistinguishable(split_harness, assistant_id):
    """Unknown, disabled, non-assistant and forbidden rows share the upstream refusal."""
    h = split_harness
    response = send(h, assistant_id=assistant_id)
    assert response.status_code == 400
    assert response.json() == {'detail': 'Model not found'}
    assert h.seen == []


def test_llm_access_is_checked_independently(split_harness):
    """A readable assistant never grants access to a forbidden picked LLM."""
    h = split_harness
    h.allowed.remove('llm')
    response = send(h)
    assert response.status_code == 400
    assert response.json() == {'detail': 'Model not found'}
    assert h.seen == []


def test_split_refuses_assistant_in_model_slot(split_harness):
    """Explicit split requests cannot route through another assistant's legacy default."""
    h = split_harness
    h.user.role = 'admin'
    h.rows['assistant'].base_model_id = 'llm'
    response = send(h, model='assistant')
    assert response.status_code == 400
    assert response.json() == {'detail': 'Model not found'}


def test_legacy_metadata_and_params_are_unchanged(split_harness):
    """An omitted assistant_id preserves the historical custom-model contract."""
    h = split_harness
    h.rows['assistant'].base_model_id = 'llm'
    h.app.state.MODELS['assistant']['info']['base_model_id'] = 'llm'
    legacy = copy.deepcopy(h.app.state.MODELS['assistant'])
    response = h.client.post(
        '/api/chat/completions',
        json={
            'model': 'assistant',
            'messages': [{'role': 'user', 'content': 'Hello'}],
            'stream': False,
        },
    )
    assert response.status_code == 200, response.text
    body = h.seen[-1]
    assert body['metadata']['model'] == legacy
    assert body['model'] == 'assistant'
    assert body['params'] == {'temperature': 0.8, 'top_p': 0.9, 'system': 'Assistant prompt'}
    assert body['metadata']['system_prompt'] == 'Assistant prompt'
    assert 'assistant_id' not in body
    assert h.bindings == []


@pytest.mark.parametrize('has_message', [True, False])
def test_chat_binding_refusal_happens_before_processing(split_harness, has_message):
    """Only a chat with an existing assistant message refuses a changed identity."""
    h = split_harness
    h.chats['chat'] = SimpleNamespace(meta={'assistant_id': 'other'}, chat={}, variables={})
    h.messages['chat'] = [SimpleNamespace(role='assistant')] if has_message else []
    response = send(h, chat_id='chat')
    assert response.status_code == (400 if has_message else 200), response.text
    if has_message:
        assert 'assistant is fixed' in response.json()['detail']
        assert not h.seen and not h.bindings
    else:
        assert h.bindings == [('chat', 'assistant')]


def test_bound_assistant_allows_changing_llm(split_harness):
    """A fixed assistant may use a different readable LLM on the next turn."""
    h = split_harness
    h.chats['chat'] = SimpleNamespace(meta={'assistant_id': 'assistant'}, chat={}, variables={})
    h.messages['chat'] = [SimpleNamespace(role='assistant')]
    for llm in ['llm', 'other-llm']:
        response = send(h, model=llm, chat_id='chat')
        assert response.status_code == 200, response.text
        assert h.seen[-1]['metadata']['model']['id'] == llm
    assert h.bindings == []


def test_local_chat_skips_binding(split_harness):
    """Ephemeral chats use the assistant without reading or writing a chat binding."""
    h = split_harness
    assert send(h, chat_id='local:chat').status_code == 200
    assert h.bindings == []


def test_direct_body_discards_assistant_id(split_harness):
    """Direct-mode compatibility ignores and strips the opt-in field."""
    h = split_harness
    response = send(h, model_item={'id': 'llm', 'direct': True, 'owned_by': 'openai'})
    assert response.status_code == 200, response.text
    assert 'assistant_id' not in h.seen[-1]
    assert 'assistant_id' not in h.seen[-1]['metadata']['model']


def test_openai_router_does_not_replace_picked_llm(split_harness, monkeypatch):
    """The real OpenAI router keeps a base override's model ID in its provider body."""
    from open_webui import main
    from open_webui.routers import openai
    from open_webui.utils.agent_routing import AgentRoute

    h = split_harness
    monkeypatch.setattr(main, 'resolve_agent_route', lambda **kwargs: AgentRoute(False))

    async def connection(*args, **kwargs):
        return 'http://provider.invalid/v1', '', {}

    async def headers(*args, **kwargs):
        return {}, {}

    async def request(**kwargs):
        import json

        h.provider.append(json.loads(kwargs['data']))
        return SimpleNamespace(status=200, headers={}, json=reply)

    async def reply(**kwargs):
        return {'choices': []}

    async def session():
        return SimpleNamespace(request=request)

    async def cleanup(*args):
        pass

    h.app.state.OPENAI_MODELS = {'llm': {'id': 'llm', 'urlIdx': 0}}
    monkeypatch.setattr(main, 'chat_completion_handler', openai.generate_chat_completion)
    monkeypatch.setattr(openai, 'get_openai_connection', connection)
    monkeypatch.setattr(openai, 'get_headers_and_cookies', headers)
    monkeypatch.setattr(openai, 'get_session', session)
    monkeypatch.setattr(openai, 'cleanup_response', cleanup)
    response = send(h)
    assert response.status_code == 200, response.text
    assert h.provider[-1]['model'] == 'llm'
    assert 'assistant_id' not in h.provider[-1]
    assert 'metadata' not in h.provider[-1]


def test_saved_chat_fanout_keeps_composition_and_message_identity(split_harness):
    """Saved-chat registry lookup cannot discard assistant settings or persistence."""
    h = split_harness
    h.chats['chat'] = SimpleNamespace(meta={}, chat={}, variables={})
    response = send(h, chat_id='chat', session_id='session', id='reply')
    assert response.status_code == 200, response.text
    assert response.json()['results'] == [{'choices': []}]
    assert h.processed[-1]['assistant_id'] == 'assistant'
    assert h.processed[-1]['info']['base_model_id'] is None
    assert h.bindings == [('chat', 'assistant')]
    assert len(h.writes) == 2
    for chat_id, message_id, data in h.writes:
        assert (chat_id, message_id) == ('chat', 'reply')
        assert data['assistant_id'] == 'assistant'
        assert data['model'] == 'llm'


def test_new_chat_placeholder_persists_assistant(split_harness):
    """The initial chat history records assistant and LLM as separate identities."""
    h = split_harness
    response = send(h, parent_id=None, session_id='session', id='reply')
    assert response.status_code == 200, response.text
    chat_id = response.json()['chat_id']
    placeholder = h.chats[chat_id].chat['history']['messages']['reply']
    assert placeholder['model'] == 'llm'
    assert placeholder['assistant_id'] == 'assistant'
    assert h.bindings == [(chat_id, 'assistant')]


def test_legacy_json_message_also_fixes_binding(split_harness):
    """A chat not yet materialized into message rows still refuses assistant changes."""
    h = split_harness
    h.chats['chat'] = SimpleNamespace(
        meta={'assistant_id': 'other'},
        variables={},
        chat={'history': {'messages': {'old': {'role': 'assistant'}}}},
    )
    response = send(h, chat_id='chat')
    assert response.status_code == 400
    assert 'assistant is fixed' in response.json()['detail']
    assert h.bindings == []


@pytest.mark.parametrize('message_ids', [[{'model_id': 'other-llm', 'message_id': 'reply'}], {'other-llm': 'reply'}])
def test_split_fanout_cannot_override_authorized_body_model(split_harness, message_ids):
    """A split request cannot select a different LLM through placeholder IDs."""
    h = split_harness
    response = send(h, message_ids=message_ids)
    assert response.status_code == 400
    assert response.json() == {'detail': 'Split requests must use the selected LLM for every message.'}
    assert h.seen == []


@pytest.mark.asyncio
@pytest.mark.parametrize('chat_id', ['foreign', 'missing'])
async def test_binding_preflight_does_not_reveal_other_users_chat(split_harness, chat_id):
    """Foreign chats are indistinguishable from missing chats during binding preflight."""
    from open_webui.utils.assistant_requests import resolve_assistant_request

    h = split_harness
    h.chats['foreign'] = SimpleNamespace(
        user_id='other-user',
        meta={'assistant_id': 'other'},
        chat={},
        variables={},
    )
    h.messages['foreign'] = [SimpleNamespace(role='assistant')]
    model, info, bind = await resolve_assistant_request(
        'assistant',
        h.app.state.MODELS['llm'],
        h.rows['llm'],
        h.user,
        check_access=True,
        chat_id=chat_id,
    )
    assert model['assistant_id'] == info.id == 'assistant'
    assert bind is True
    assert h.message_lookups == []
    assert h.bindings == []


def test_foreign_chat_reaches_upstream_ownership_refusal(split_harness):
    """The upstream ownership check returns the same refusal for foreign and missing chats."""
    h = split_harness
    h.chats['foreign'] = SimpleNamespace(
        user_id='other-user',
        meta={'assistant_id': 'other'},
        chat={},
        variables={},
    )
    h.messages['foreign'] = [SimpleNamespace(role='assistant')]
    foreign = send(h, chat_id='foreign')
    missing = send(h, chat_id='missing')
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()
    assert h.message_lookups == []
    assert not h.seen and not h.bindings


@pytest.mark.asyncio
@pytest.mark.parametrize('bound_assistant,expected_bind', [(None, True), ('assistant', False)])
async def test_binding_cheap_paths_skip_messages_and_history(split_harness, bound_assistant, expected_bind):
    """Unbound and unchanged assistants need neither a message query nor a JSON walk."""
    from open_webui.utils.assistant_requests import resolve_assistant_request

    h = split_harness
    h.chats['chat'] = SimpleNamespace(meta={'assistant_id': bound_assistant}, chat=object())
    h.messages['chat'] = [SimpleNamespace(role='assistant')]
    _, _, bind = await resolve_assistant_request(
        'assistant',
        h.app.state.MODELS['llm'],
        h.rows['llm'],
        h.user,
        check_access=True,
        chat_id='chat',
    )
    assert bind is expected_bind
    assert h.message_lookups == []


def test_existing_unbound_chat_binds_without_reading_messages(split_harness):
    """A pre-split chat may bind lazily despite already containing assistant messages."""
    h = split_harness
    h.chats['chat'] = SimpleNamespace(
        meta={},
        variables={},
        chat={'history': {'messages': {'old': {'role': 'assistant'}}}},
    )
    h.messages['chat'] = [SimpleNamespace(role='assistant')]
    response = send(h, chat_id='chat')
    assert response.status_code == 200, response.text
    assert h.bindings == [('chat', 'assistant')]
    assert h.message_lookups == []
