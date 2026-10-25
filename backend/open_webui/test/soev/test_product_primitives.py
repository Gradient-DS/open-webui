"""Memory and dictation exercise the real platform client against a fake API."""

import io
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from open_webui import env
from open_webui.models.memories import MemoryModel
from open_webui.retrieval.vector.main import SearchResult
from open_webui.routers import audio, memories
from open_webui.soev import acting, identity, primitives
from open_webui.soev.client import SoevApiError
from open_webui.utils.auth import get_admin_user, get_verified_user


@pytest_asyncio.fixture
async def platform(identity_config, eligible_identity, monkeypatch):
    monkeypatch.setattr(env, 'AGENT_API_ENABLED', True)
    user = SimpleNamespace(id='fern', role='admin')
    state = SimpleNamespace(calls=[], failure=None, embedding=None, text='Fictional dictation.')
    fake = FastAPI()

    @fake.post('/v1/{path:path}')
    async def inference(path: str, request: Request):
        state.calls.append(request)
        assert request.url.hostname == 'soev.invalid'
        assert request.headers['authorization'] == 'Bearer soev_test_cred-runtime_test-secret'
        assert 'x-soev-' + 'subject' not in request.headers
        if request.headers.get('x-soev-user-token') != 'fictional-fern-token':
            return JSONResponse(
                status_code=401,
                content={'code': 'user_token_required', 'detail': 'A user token is required'},
                media_type='application/problem+json',
            )
        assert 'idempotency-key' not in request.headers
        if state.failure:
            status, code = state.failure
            return JSONResponse(
                status_code=status,
                content={'status': status, 'code': code, 'detail': 'private upstream information'},
                media_type='application/problem+json',
            )
        if path == 'embeddings':
            body = await request.json()
            assert set(body) == {'input'}
            assert len(body['input']) == 1
            return state.embedding or {'data': [{'index': 0, 'embedding': [0.1, 0.2]}]}
        if path == 'audio/speech':
            state.speech = await request.json()
            return Response(b'fictional-streamed-audio', media_type='audio/mpeg')
        assert path == 'audio/transcriptions'
        async with request.form() as form:
            assert set(form) in ({'file', 'response_format'}, {'file', 'response_format', 'language'})
            assert form['response_format'] == 'json'
            state.upload = await form['file'].read()
            state.language = form.get('language')
        return {'text': state.text}

    original_client = httpx.AsyncClient
    transport = httpx.ASGITransport(app=fake)
    outbound = original_client(transport=transport)
    monkeypatch.setattr('open_webui.soev.client._shared_client', lambda: outbound)
    monkeypatch.setattr(acting, '_requester_ref', acting.ContextVar('test-requester', default='owui:user:fern'))
    monkeypatch.setattr(
        acting,
        '_token_resolver',
        acting.ContextVar('test-token', default=AsyncMock(return_value='fictional-fern-token')),
    )
    state.user = user
    state.http = original_client
    yield state
    await outbound.aclose()


@pytest.fixture
def memory_state(platform, monkeypatch):
    row = MemoryModel(
        id='memory-fern', user_id='fern', content='Prefers fictional blue tea.', created_at=1, updated_at=1
    )
    monkeypatch.setattr(memories, 'check_memories_permission', AsyncMock())
    monkeypatch.setattr(memories, 'publish_event', AsyncMock())
    monkeypatch.setattr(memories.Memories, 'insert_new_memory', AsyncMock(return_value=row))
    monkeypatch.setattr(memories.Memories, 'get_memories_by_user_id', AsyncMock(return_value=[row]))
    monkeypatch.setattr(memories.Memories, 'update_memory_by_id_and_user_id', AsyncMock(return_value=row))
    monkeypatch.setattr(
        memories.Memories, 'apply_memory_operations', AsyncMock(return_value=[{'status': 'updated', 'memory': row}])
    )
    monkeypatch.setattr(memories.Config, 'get', AsyncMock(return_value=0.0))
    vectors = SimpleNamespace(
        upsert=AsyncMock(),
        delete_collection=AsyncMock(),
        search=AsyncMock(
            return_value=SearchResult(
                ids=[['memory-fern']], documents=[[row.content]], metadatas=[[{}]], distances=[[0.9]]
            )
        ),
    )
    monkeypatch.setattr(memories, 'ASYNC_VECTOR_DB_CLIENT', vectors)
    legacy = AsyncMock(side_effect=AssertionError('Direct embedding must not run'))
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(EMBEDDING_FUNCTION=legacy)), state=SimpleNamespace()
    )
    return SimpleNamespace(row=row, request=request, vectors=vectors, legacy=legacy)


@pytest.mark.asyncio
async def test_memory_add_and_query_use_platform(platform, memory_state):
    s = memory_state
    added = await memories.add_memory(s.request, memories.AddMemoryForm(content=s.row.content), platform.user)
    result = await memories.query_memory(s.request, memories.QueryMemoryForm(content='What tea?'), platform.user)
    assert added == s.row and result.ids == [['memory-fern']]
    assert [request.url.path for request in platform.calls] == ['/v1/embeddings'] * 2
    assert s.vectors.upsert.call_args.kwargs['items'][0]['vector'] == [0.1, 0.2]
    assert s.vectors.search.call_args.kwargs['vectors'] == [[0.1, 0.2]]
    s.legacy.assert_not_awaited()


@pytest.mark.asyncio
async def test_memory_update_and_reindex_use_requesting_person(platform, memory_state):
    s = memory_state
    await memories.update_memory_by_id(
        s.row.id, s.request, memories.MemoryUpdateModel(content='New fictional tea.'), platform.user
    )
    await memories.reindex_memory_vectors_for_user(s.request, 'moss', [s.row], user=platform.user)
    assert len(platform.calls) == 2
    assert s.vectors.upsert.call_args.kwargs['collection_name'] == 'user-memory-moss'
    s.legacy.assert_not_awaited()


@pytest.mark.asyncio
async def test_admin_reindex_does_not_impersonate_memory_owner(platform, memory_state, monkeypatch):
    monkeypatch.setattr(memories.Memories, 'get_memories', AsyncMock(return_value=[memory_state.row]))
    monkeypatch.setattr(memories.Users, 'get_users', AsyncMock(return_value={'users': [SimpleNamespace(id='moss')]}))
    memory_state.row.user_id = 'moss'
    await memories.reindex_memories_from_vector_db(memory_state.request, platform.user)
    assert len(platform.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'status,code', [(429, 'budget_spent'), (503, 'service_unavailable'), (403, 'scope_insufficient')]
)
async def test_embedding_refusal_never_uses_direct_engine(platform, memory_state, status, code):
    platform.failure = status, code
    with pytest.raises(SoevApiError) as error:
        await memories.query_memory(memory_state.request, memories.QueryMemoryForm(content='Tea?'), platform.user)
    assert (error.value.status, error.value.code) == (status, code)
    memory_state.legacy.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('enabled,url', [(False, 'https://soev.invalid'), (True, '')])
async def test_stock_memory_embedding_is_preserved(platform, memory_state, monkeypatch, enabled, url):
    monkeypatch.setattr(env, 'AGENT_API_ENABLED', enabled)
    monkeypatch.setattr(identity.config, 'SOEV_API_URL', url)
    memory_state.legacy.side_effect = None
    memory_state.legacy.return_value = [0.3]
    assert await memories.memory_embedding(memory_state.request, 'Tea', prefix='query: ', user=platform.user) == [0.3]
    memory_state.legacy.assert_awaited_once_with('Tea', prefix='query: ', user=platform.user)
    assert not platform.calls


@pytest.fixture
def dictation_app(platform, monkeypatch, tmp_path):
    from open_webui.main import soev_api_error_handler

    monkeypatch.setattr(audio, 'is_feature_enabled', lambda feature: True)
    monkeypatch.setattr(audio, 'publish_event', AsyncMock())
    monkeypatch.setattr(audio.Config, 'get', AsyncMock(side_effect=AssertionError('STT settings must not be read')))
    app = FastAPI()
    app.include_router(audio.router)
    app.add_exception_handler(SoevApiError, soev_api_error_handler)
    app.dependency_overrides[get_verified_user] = lambda: platform.user
    app.dependency_overrides[get_admin_user] = lambda: platform.user
    return app


@pytest.mark.asyncio
@pytest.mark.parametrize('language', [None, 'nl'])
async def test_dictation_multipart_uses_platform_without_disk(platform, dictation_app, tmp_path, language):
    async with platform.http(
        transport=httpx.ASGITransport(app=dictation_app), base_url='http://owui.invalid'
    ) as client:
        response = await client.post(
            '/transcriptions',
            files={'file': ('fiction.webm', b'fictional-audio', 'audio/webm')},
            data={'language': language} if language else {},
        )
    assert response.status_code == 200, response.text
    assert response.json() == {'text': 'Fictional dictation.'}
    assert platform.upload == b'fictional-audio' and platform.language == language
    assert [request.url.path for request in platform.calls] == ['/v1/audio/transcriptions']
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'language,detail',
    [
        ('en-US', 'The platform could not complete this request.'),
        ('nl-NL', 'Het platform kon dit verzoek niet uitvoeren.'),
    ],
)
@pytest.mark.parametrize('status,code', [(429, 'budget_spent'), (503, 'service_unavailable'), (422, 'invalid_field')])
async def test_dictation_refusal_uses_safe_error_mapping(platform, dictation_app, language, detail, status, code):
    platform.failure = status, code
    async with platform.http(
        transport=httpx.ASGITransport(app=dictation_app), base_url='http://owui.invalid'
    ) as client:
        response = await client.post(
            '/transcriptions',
            files={'file': ('fiction.wav', b'fiction', 'audio/wav')},
            headers={'Accept-Language': language},
        )
    assert response.status_code == status
    assert response.json()['detail'] == {'code': code, 'constraint': None, 'detail': detail}
    assert 'private upstream information' not in response.text


@pytest.mark.asyncio
async def test_dictation_size_bound_precedes_read(platform):
    file = UploadFile(io.BytesIO(b'fiction'), size=primitives.MAX_AUDIO_BYTES + 1)
    with pytest.raises(SoevApiError) as error:
        await primitives.transcribe(file, None, platform.user)
    assert error.value.status == 413 and file.file.tell() == 0
    assert not platform.calls


@pytest.mark.asyncio
async def test_voice_gate_still_binds_v2_admin(platform, dictation_app, monkeypatch):
    monkeypatch.setattr(audio, 'is_feature_enabled', lambda feature: False)
    async with platform.http(
        transport=httpx.ASGITransport(app=dictation_app), base_url='http://owui.invalid'
    ) as client:
        response = await client.post('/transcriptions', files={'file': ('fiction.wav', b'fiction', 'audio/wav')})
    assert response.status_code == 403
    assert not platform.calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'result',
    [{'data': []}, {'data': [{'index': 0, 'embedding': []}]}, {'data': [{'index': 0, 'embedding': ['invalid']}]}],
)
async def test_invalid_embedding_response_never_uses_direct_engine(platform, memory_state, result):
    platform.embedding = result
    with pytest.raises(SoevApiError) as error:
        await memories.query_memory(memory_state.request, memories.QueryMemoryForm(content='Tea?'), platform.user)
    assert error.value.status == 502
    memory_state.legacy.assert_not_awaited()


@pytest.mark.asyncio
async def test_transport_failure_is_mapped_without_fallback(platform, dictation_app, monkeypatch):
    async def unavailable(request):
        raise httpx.ConnectError('fictional unavailable server', request=request)

    async with platform.http(transport=httpx.MockTransport(unavailable)) as outbound:
        monkeypatch.setattr('open_webui.soev.client._shared_client', lambda: outbound)
        async with platform.http(
            transport=httpx.ASGITransport(app=dictation_app), base_url='http://owui.invalid'
        ) as client:
            response = await client.post('/transcriptions', files={'file': ('fiction.wav', b'fiction', 'audio/wav')})
    assert response.status_code == 502
    assert response.json()['detail']['detail'] == 'The platform could not complete this request.'


@pytest.mark.asyncio
async def test_another_users_context_never_supplies_a_token(platform, memory_state, dictation_app, monkeypatch):
    monkeypatch.setattr(acting, '_requester_ref', acting.ContextVar('other-requester', default='owui:user:moss'))
    with pytest.raises(SoevApiError) as error:
        await memories.query_memory(memory_state.request, memories.QueryMemoryForm(content='Tea?'), platform.user)
    assert (error.value.status, error.value.code, error.value.constraint) == (
        401,
        'sign_in',
        'identity:platform_login',
    )
    async with platform.http(
        transport=httpx.ASGITransport(app=dictation_app), base_url='http://owui.invalid'
    ) as client:
        response = await client.post('/transcriptions', files={'file': ('fiction.wav', b'fiction', 'audio/wav')})
    assert response.status_code == 401
    assert platform.calls == []
    memory_state.legacy.assert_not_awaited()


@pytest.mark.asyncio
async def test_user_stt_permission_precedes_platform_call(platform, dictation_app, monkeypatch):
    platform.user.role = 'user'
    monkeypatch.setattr(audio.Config, 'get', AsyncMock(return_value={}))
    monkeypatch.setattr(audio, 'has_permission', AsyncMock(return_value=False))
    async with platform.http(
        transport=httpx.ASGITransport(app=dictation_app), base_url='http://owui.invalid'
    ) as client:
        response = await client.post('/transcriptions', files={'file': ('fiction.wav', b'fiction', 'audio/wav')})
    assert response.status_code == 403
    assert not platform.calls


@pytest.mark.asyncio
async def test_offline_reindex_keeps_its_explicit_embedding_function(platform, memory_state):
    supplied = AsyncMock(return_value=[0.4, 0.5])
    await memories.reindex_memory_vectors_for_user(
        memory_state.request, 'fern', [memory_state.row], user=platform.user, embedding_function=supplied
    )
    supplied.assert_awaited_once()
    assert memory_state.vectors.upsert.call_args.kwargs['items'][0]['vector'] == [0.4, 0.5]
    assert not platform.calls


@pytest.mark.asyncio
async def test_a_login_without_a_platform_token_is_refused_before_any_call(platform, memory_state, monkeypatch):
    monkeypatch.setattr(acting, '_token_resolver', acting.ContextVar('no-token', default=AsyncMock(return_value=None)))
    with pytest.raises(SoevApiError) as error:
        await primitives.embed_memory('Fictional note.', platform.user)
    assert (error.value.status, error.value.constraint) == (401, 'identity:platform_login')
    assert platform.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'body',
    [
        {'input': 'Fictional speech.', 'response_format': 'mp3'},
        {'input': 'Fictional speech.', 'voice': 'fictional-voice', 'response_format': 'wav'},
    ],
)
async def test_speech_streams_through_platform_token_only(platform, dictation_app, body, tmp_path):
    async with platform.http(
        transport=httpx.ASGITransport(app=dictation_app), base_url='http://owui.invalid'
    ) as client:
        response = await client.post('/speech', json=body)
    assert response.status_code == 200, response.text
    assert response.content == b'fictional-streamed-audio'
    assert response.headers['content-type'] == 'audio/mpeg'
    assert response.headers['cache-control'] == 'no-store'
    assert platform.speech == body
    assert [call.url.path for call in platform.calls] == ['/v1/audio/speech']
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize('status,code', [(429, 'budget_spent'), (503, 'service_unavailable'), (422, 'invalid_field')])
@pytest.mark.parametrize(
    'language,detail',
    [
        ('en-US', 'The platform could not complete this request.'),
        ('nl-NL', 'Het platform kon dit verzoek niet uitvoeren.'),
    ],
)
async def test_speech_refusal_precedes_stream_headers(platform, dictation_app, status, code, language, detail):
    platform.failure = status, code
    async with platform.http(
        transport=httpx.ASGITransport(app=dictation_app), base_url='http://owui.invalid'
    ) as client:
        response = await client.post(
            '/speech', json={'input': 'Fictional speech.'}, headers={'Accept-Language': language}
        )
    assert response.status_code == status
    assert response.json()['detail']['detail'] == detail
    assert 'private upstream information' not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize('requester,token', [('owui:user:fern', None), ('owui:user:moss', 'fictional-moss-token')])
async def test_speech_requires_requesting_persons_platform_login(
    platform, dictation_app, monkeypatch, requester, token
):
    monkeypatch.setattr(acting, '_requester_ref', acting.ContextVar('speech-requester', default=requester))
    monkeypatch.setattr(
        acting, '_token_resolver', acting.ContextVar('speech-token', default=AsyncMock(return_value=token))
    )
    async with platform.http(
        transport=httpx.ASGITransport(app=dictation_app), base_url='http://owui.invalid'
    ) as client:
        response = await client.post('/speech', json={'input': 'Fictional speech.'})
    assert response.status_code == 401
    assert response.json()['detail']['constraint'] == 'identity:platform_login'
    assert not platform.calls


@pytest.mark.asyncio
async def test_speech_permission_and_feature_gates(platform, dictation_app, monkeypatch):
    async with platform.http(
        transport=httpx.ASGITransport(app=dictation_app), base_url='http://owui.invalid'
    ) as client:
        monkeypatch.setattr(audio, 'is_feature_enabled', lambda feature: False)
        assert (await client.post('/speech', json={'input': 'Fictional speech.'})).status_code == 403
        monkeypatch.setattr(audio, 'is_feature_enabled', lambda feature: True)
        platform.user.role = 'user'
        monkeypatch.setattr(audio.Config, 'get', AsyncMock(return_value={}))
        monkeypatch.setattr(audio, 'has_permission', AsyncMock(return_value=False))
        assert (await client.post('/speech', json={'input': 'Fictional speech.'})).status_code == 403
    assert not platform.calls


@pytest.mark.asyncio
async def test_speech_transport_failure_is_safe(platform, dictation_app, monkeypatch):
    async def unavailable(request):
        raise httpx.ConnectError('fictional private server', request=request)

    async with platform.http(transport=httpx.MockTransport(unavailable)) as outbound:
        monkeypatch.setattr('open_webui.soev.client._shared_client', lambda: outbound)
        async with platform.http(
            transport=httpx.ASGITransport(app=dictation_app), base_url='http://owui.invalid'
        ) as client:
            response = await client.post('/speech', json={'input': 'Fictional speech.'})
    assert response.status_code == 502
    assert response.json()['detail']['detail'] == 'The platform could not complete this request.'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'body',
    [
        {'input': 'Fictional', 'model': 'caller-chosen'},
        {'input': 'Fictional', 'response_format': 'invalid'},
        {'input': ''},
    ],
)
async def test_speech_rejects_provider_overrides_and_invalid_input(platform, dictation_app, body):
    async with platform.http(
        transport=httpx.ASGITransport(app=dictation_app), base_url='http://owui.invalid'
    ) as client:
        assert (await client.post('/speech', json=body)).status_code == 422
    assert not platform.calls


@pytest.mark.asyncio
async def test_speech_does_not_buffer_and_closes_upstream_on_disconnect(platform, monkeypatch):
    class AudioStream(httpx.AsyncByteStream):
        def __init__(self):
            self.yielded = 0
            self.closed = False

        async def __aiter__(self):
            for chunk in (b'fictional first', b'fictional second'):
                self.yielded += 1
                yield chunk

        async def aclose(self):
            self.closed = True

    audio_stream = AudioStream()

    async def upstream(request):
        assert request.url.path == '/v1/audio/speech'
        assert 'x-soev-' + 'subject' not in request.headers
        assert request.headers['x-soev-user-token'] == 'fictional-fern-token'
        return httpx.Response(200, stream=audio_stream, headers={'Content-Type': 'audio/mpeg'})

    async with platform.http(transport=httpx.MockTransport(upstream)) as outbound:
        monkeypatch.setattr('open_webui.soev.client._shared_client', lambda: outbound)
        response = await primitives.speech({'input': 'Fictional speech.'}, platform.user)
        assert audio_stream.yielded == 0
        assert await anext(response.body_iterator) == b'fictional first'
        assert audio_stream.yielded == 1
        await response.body_iterator.aclose()
        assert audio_stream.closed
