"""[Gradient] Workspace skills use caller-owned uploads without contacting the platform."""

import hashlib
import io
import zipfile
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
import yaml
from open_webui.models.models import ModelMeta, ModelParams
from open_webui.soev import capabilities
from open_webui.soev.client import ChatEvent, SoevApiError, SoevClient
from open_webui.utils import agent_assistant_skills, agent_skill_uploads, agent_v2, assistant_skill_bundles


@pytest.fixture
def skills(monkeypatch, tmp_path, identity_config, request):
    path = tmp_path / 'template.bin'
    path.write_bytes(b'\x00\xffraw bytes')
    rows = [SimpleNamespace(path='assets/template.bin', file_id='template', updated_at=1)]
    file = SimpleNamespace(
        id='template', path=str(path), hash='original-hash', updated_at=1, meta={'size': path.stat().st_size}
    )
    monkeypatch.setattr(assistant_skill_bundles.Files, 'get_files_by_ids', AsyncMock(return_value=[file]))
    storage = Mock(side_effect=lambda path: path)
    monkeypatch.setattr(assistant_skill_bundles.Storage, 'get_file', storage)
    monkeypatch.setattr(agent_skill_uploads.identity, 'ensure_link', AsyncMock())
    upload = AsyncMock(
        return_value={
            'id': 'a' * 32,
            'sha256': 'b' * 64,
            'size': 123,
            'name': 'platform-name',
            'description': 'Platform description',
        }
    )
    if request.node.name != 'test_raw_zip_http_content_type_and_subject':
        monkeypatch.setattr(SoevClient, 'post_bytes', upload)
    monkeypatch.setattr(agent_skill_uploads, 'REFERENCES', agent_skill_uploads.OrderedDict())
    monkeypatch.setattr(capabilities, 'has', AsyncMock(return_value=True))
    return SimpleNamespace(rows=rows, path=path, file=file, upload=upload, storage=storage)


def metadata(**extra):
    return {
        'user_id': 'fern',
        'features': {'workspace': True},
        'assistant_id': 'invented-assistant',
        'model': {'info': {'meta': {'capabilities': {'workspace': True}}}},
        **extra,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('target', 'field', 'value'),
    [('file', 'updated_at', 2), ('file', 'meta', {'size': 99}), ('row', 'updated_at', 2), ('row', 'file_id', 'new')],
)
async def test_cache_invalidates_on_file_versions(skills, target, field, value):
    await agent_v2._skills(metadata(), 'owui:user:fern')
    setattr(skills.file if target == 'file' else skills.rows[0], field, value)
    if field == 'file_id':
        skills.file.id = value
    await agent_v2._skills(metadata(), 'owui:user:fern')
    assert skills.upload.await_count == 2


@pytest.mark.asyncio
async def test_cache_hit_does_not_build_a_bundle_or_read_storage(skills, monkeypatch):
    await agent_v2._skills(metadata(), 'owui:user:fern')
    bundle = AsyncMock(side_effect=AssertionError('Must not build on a cache hit'))
    monkeypatch.setattr(agent_assistant_skills, 'build_bundle', bundle)
    skills.storage.reset_mock()
    await agent_v2._skills(metadata(), 'owui:user:fern')
    bundle.assert_not_awaited()
    skills.storage.assert_not_called()
    assert skills.upload.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [400, 403, 413, 415, 422, 429, 500, 503])
async def test_refusal_notices_continue_but_server_errors_propagate(skills, status, caplog):
    skills.upload.side_effect = SoevApiError(status, 'invalid_field', 'PRIVATE DETAIL')
    if status >= 500:
        with pytest.raises(SoevApiError):
            await agent_v2._skills(metadata(), 'owui:user:fern')
    else:
        refs, notices = await agent_v2._skills(metadata(user_language='nl'), 'owui:user:fern')
        assert not refs
        assert notices == [agent_skill_uploads._notice('Invented Helper', 'nl')]
        assert not agent_skill_uploads.REFERENCES
        assert 'PRIVATE DETAIL' not in caplog.text


@pytest.mark.asyncio
async def test_missing_bundle_file_is_not_silently_dropped(skills):
    assistant_skill_bundles.Files.get_files_by_ids.return_value = []
    refs, notices = await agent_v2._skills(metadata(), 'owui:user:fern')
    assert not refs and len(notices) == 1
    skills.upload.assert_not_awaited()


@pytest.mark.asyncio
async def test_reference_cache_is_bounded(skills, monkeypatch):
    monkeypatch.setattr(agent_skill_uploads, 'CACHE_LIMIT', 2)
    for user in ('fern', 'reed', 'moss'):
        await agent_v2._skills(metadata(user_id=user), f'owui:user:{user}')
    assert len(agent_skill_uploads.REFERENCES) == 2
    await agent_v2._skills(metadata(), 'owui:user:fern')
    assert skills.upload.await_count == 4


@pytest.mark.asyncio
async def test_raw_zip_http_content_type_and_subject(identity_http, identity_config):
    requests, responses = identity_http
    responses.append(httpx.Response(201, json={'id': 'a' * 32}))
    identity, _ = identity_config
    result = await identity.build_client().post_bytes(
        '/v1/chat/skills', b'PK-raw-zip', as_user='owui:user:fern', content_type='application/zip'
    )
    assert result == {'id': 'a' * 32}
    assert requests[0].content == b'PK-raw-zip'
    assert requests[0].headers['Content-Type'] == 'application/zip'
    assert requests[0].headers['X-Soev-Subject']


@pytest.mark.asyncio
async def test_simultaneous_unchanged_turns_upload_once(skills):
    import asyncio

    await asyncio.gather(*(agent_v2._skills(metadata(), 'owui:user:fern') for _ in range(3)))
    assert skills.upload.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('refused', [False, True])
async def test_turn_input_and_saved_nonfatal_notice(skills, monkeypatch, refused):
    from unittest.mock import Mock

    for name, result in {
        '_folder_items': ([], []),
        '_knowledge': ([], []),
        '_attachments': {},
        '_texts': ([], []),
        '_more_texts': ([], []),
        '_images': [],
        '_web_search_allowed': False,
        '_live_documents_allowed': False,
        '_live_mail_allowed': False,
        '_documents_allowed': False,
    }.items():
        monkeypatch.setattr(agent_v2, name, AsyncMock(return_value=result))
    data = metadata(chat_id='invented-chat', message_id='invented-answer')
    turn = agent_v2.AgentTurn(agent_skill_uploads.identity.build_client(), data, 'owui:user:fern')
    run = Mock()
    monkeypatch.setattr(turn, 'run', run)
    if refused:
        skills.upload.side_effect = SoevApiError(422, 'invalid_field', 'Private detail')
    await agent_v2._sent(turn, 'Draft an outline', data, agent='example-agent', model='example-model')
    sent = run.call_args.args[0]['input']
    assert sent['tools']['workspace_task'] == 'auto'
    assert sent.get('skills') == (
        None if refused else [{'type': 'chat-skill', **next(iter(agent_skill_uploads.REFERENCES.values()))[0]}]
    )
    if refused:
        upsert, emit = AsyncMock(), AsyncMock()
        monkeypatch.setattr(agent_v2.Chats, 'upsert_message_to_chat_by_id_and_message_id', upsert)
        monkeypatch.setattr(turn, 'emit', emit)
        await turn.notify()
        assert len(turn.notices) == 1
        upsert.assert_awaited_once_with('invented-chat', 'invented-answer', {'notices': turn.notices})
        emit.assert_awaited_once_with('chat:message:notices', {'notices': turn.notices, 'removed': []})


@pytest.fixture
def prepared_turn(skills, monkeypatch):
    for name, result in {
        '_folder_items': ([], []),
        '_knowledge': ([], []),
        '_attachments': {},
        '_texts': ([], []),
        '_more_texts': ([], []),
        '_images': [],
        '_web_search_allowed': False,
        '_live_documents_allowed': False,
        '_live_mail_allowed': False,
        '_documents_allowed': False,
        '_earlier': None,
        '_tool_statuses': {},
        'get_event_emitter': None,
    }.items():
        monkeypatch.setattr(agent_v2, name, AsyncMock(return_value=result))
    data = metadata(chat_id='invented-chat', message_id='invented-answer')
    turn = agent_v2.AgentTurn(agent_skill_uploads.identity.build_client(), data, 'owui:user:fern')
    monkeypatch.setattr(turn, 'prepare', AsyncMock())
    monkeypatch.setattr(turn, 'emit', AsyncMock())
    monkeypatch.setattr(agent_v2.Chats, 'upsert_message_to_chat_by_id_and_message_id', AsyncMock())
    return turn


@pytest.mark.asyncio
@pytest.mark.parametrize('existing_thread', [False, True])
@pytest.mark.parametrize('give_up', [False, True])
@pytest.mark.parametrize('language', ['en', 'nl'])
async def test_skill_not_found_reuploads_once_then_continues(
    skills, prepared_turn, monkeypatch, existing_thread, give_up, language
):
    turn = prepared_turn
    turn.metadata['user_language'] = language
    turn.thread_id = 'invented-thread' if existing_thread else None
    uploaded = []

    async def upload(path, body, **kwargs):
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            frontmatter = yaml.safe_load(archive.read('SKILL.md').decode().split('---\n')[1])
        reference = {**skills.upload.return_value, 'id': f'{len(uploaded):032x}', 'name': frontmatter['name']}
        uploaded.append(reference)
        return reference

    skills.upload.side_effect = upload
    original, _ = await agent_v2._skills(turn.metadata, turn.as_user)
    skills.storage.reset_mock()
    sends = []

    async def chat_stream(client, path, body, **kwargs):
        sends.append((path, deepcopy(body), kwargs))
        if len(sends) == 1 or (give_up and len(sends) == 2):
            raise SoevApiError(404, 'skill_not_found', 'Private detail')
        yield ChatEvent('connection', {'thread_id': 'invented-thread'})
        yield ChatEvent('delta', {'text': 'An outline.'})

    monkeypatch.setattr(SoevClient, 'chat_stream', chat_stream)
    chunks = await agent_v2._sent(turn, 'Draft an outline', turn.metadata, agent='example-agent', model='example-model')
    result = [chunk async for chunk in chunks]
    assert agent_v2._ACCEPTED in result
    assert not any('error' in chunk for chunk in result)
    assert result[-1]['choices'][0]['delta']['content'] == 'An outline.'
    assert len(sends) == (3 if give_up else 2)
    assert skills.upload.await_count == 2  # original upload, then one fresh upload
    assert skills.storage.call_count == 1  # cache hit first, then rebuild the assistant bundle
    assert sends[0][1]['input']['skills'] == original
    assert sends[1][1]['input']['skills'] == [{'type': 'chat-skill', **uploaded[1]}]
    expected_path = '/v1/chat/threads/invented-thread/inputs' if existing_thread else '/v1/chat/threads'
    assert all(path == expected_path for path, _, _ in sends)
    assert all(kwargs['as_user'] == turn.as_user for _, _, kwargs in sends)
    for _, body, _ in sends:
        assert body['input']['text'] == 'Draft an outline'
        assert body['model'] == 'example-model'
    if give_up:
        assert 'skills' not in sends[2][1]['input']
        assert turn.notices == [agent_skill_uploads._notice('Invented Helper', language)]
        assert not agent_skill_uploads.REFERENCES
        agent_v2.Chats.upsert_message_to_chat_by_id_and_message_id.assert_awaited_once_with(
            'invented-chat', 'invented-answer', {'notices': turn.notices}
        )
        turn.emit.assert_any_await('chat:message:notices', {'notices': turn.notices, 'removed': []})
    else:
        assert not turn.notices
        assert len(agent_skill_uploads.REFERENCES) == 1
        agent_v2.Chats.upsert_message_to_chat_by_id_and_message_id.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('code', ['thread_not_found', 'invalid_field', 'service_unavailable'])
async def test_other_send_errors_do_not_refresh_skills(skills, prepared_turn, monkeypatch, code):
    references, _ = await agent_v2._skills(prepared_turn.metadata, prepared_turn.as_user)
    error = SoevApiError(404, code, 'Private detail')
    sends = []

    async def chat_stream(client, path, body, **kwargs):
        sends.append(body)
        raise error
        yield

    monkeypatch.setattr(SoevClient, 'chat_stream', chat_stream)
    with pytest.raises(SoevApiError) as caught:
        _ = [event async for event in prepared_turn.events({'input': {'skills': references}})]
    assert caught.value is error
    assert len(sends) == 1
    assert skills.upload.await_count == 1
    assert len(agent_skill_uploads.REFERENCES) == 1
    assert not prepared_turn.notices


@pytest.mark.asyncio
async def test_different_error_after_skill_refresh_is_not_retried(skills, prepared_turn, monkeypatch):
    references, _ = await agent_v2._skills(prepared_turn.metadata, prepared_turn.as_user)
    errors = [SoevApiError(404, 'skill_not_found', 'Missing skill'), SoevApiError(403, 'forbidden', 'Private detail')]
    sends = []

    async def chat_stream(client, path, body, **kwargs):
        sends.append(deepcopy(body))
        raise errors[len(sends) - 1]
        yield

    monkeypatch.setattr(SoevClient, 'chat_stream', chat_stream)
    with pytest.raises(SoevApiError) as caught:
        _ = [event async for event in prepared_turn.events({'input': {'skills': references}})]
    assert caught.value is errors[1]
    assert len(sends) == 2
    assert skills.upload.await_count == 2
    assert not prepared_turn.notices


@pytest.mark.asyncio
async def test_skill_not_found_without_skills_is_not_retried(skills, prepared_turn, monkeypatch):
    sends = []

    async def chat_stream(client, path, body, **kwargs):
        sends.append(body)
        raise SoevApiError(404, 'skill_not_found', 'Private detail')
        yield

    monkeypatch.setattr(SoevClient, 'chat_stream', chat_stream)
    with pytest.raises(SoevApiError, match='Private detail'):
        _ = [event async for event in prepared_turn.events({'input': {}})]
    assert len(sends) == 1
    skills.upload.assert_not_awaited()


@pytest.mark.asyncio
async def test_forgetting_references_is_scoped_to_user_and_turn_skills(skills, assistant_folder):
    async def upload(path, body, **kwargs):
        return {**skills.upload.return_value, 'id': str(skills.upload.await_count)}

    skills.upload.side_effect = upload
    old, _ = await agent_v2._skills(metadata(), 'owui:user:fern')
    assistant_folder.params.system += ' changed'
    current, _ = await agent_v2._skills(metadata(), 'owui:user:fern')
    other_user, _ = await agent_v2._skills(metadata(user_id='reed'), 'owui:user:reed')
    assistant_folder.id = 'other-assistant'
    other_skill, _ = await agent_v2._skills(metadata(assistant_id=assistant_folder.id), 'owui:user:fern')
    assert len(agent_skill_uploads.REFERENCES) == 4
    assert agent_skill_uploads.forget_references('owui:user:fern', current, 'en') == [
        agent_skill_uploads._notice('Invented Helper', 'en')
    ]
    assert len(agent_skill_uploads.REFERENCES) == 2  # all versions of the turn's skill, only for this user
    remaining = [reference for reference, _ in agent_skill_uploads.REFERENCES.values()]
    assert [{'type': 'chat-skill', **reference} for reference in remaining] == other_user + other_skill
    assert old[0]['id'] not in {reference['id'] for reference in remaining}


# [Gradient] Assistant folders share transport/cache but never standalone-skill authorization.
@pytest.fixture(autouse=True)
def assistant_folder(skills, monkeypatch):
    model = SimpleNamespace(
        id='invented-assistant',
        user_id='invented-owner',
        base_model_id='invented-llm',
        name='Invented Helper',
        params=ModelParams(system='Invented private instructions'),
        meta=ModelMeta(description='Invented assistant description', capabilities={'workspace': True}),
    )
    skills.file.user_id = model.user_id
    monkeypatch.setattr(agent_assistant_skills.Models, 'get_model_by_id', AsyncMock(return_value=model))
    monkeypatch.setattr(agent_assistant_skills.AssistantSkillFiles, 'get_files', AsyncMock(return_value=skills.rows))
    monkeypatch.setattr(
        agent_assistant_skills.AssistantSkillFiles,
        'file_versions',
        AsyncMock(
            side_effect=lambda ids: {
                skills.file.id: [
                    skills.file.user_id,
                    skills.file.hash,
                    skills.file.updated_at,
                    skills.file.meta.get('size'),
                ]
            }
        ),
    )

    async def uploaded(path, body, **kwargs):
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            header = yaml.safe_load(archive.read('SKILL.md').decode().split('---\n')[1])
        digest = hashlib.sha256(body).hexdigest()
        return {
            'id': digest[:32],
            'sha256': digest,
            'size': len(body),
            'name': header['name'],
            'description': header['description'],
        }

    skills.upload.side_effect = uploaded
    return model


def assistant_metadata(model, **extra):
    return metadata(assistant_id=model.id, model={'info': {'meta': {'capabilities': {'workspace': True}}}}, **extra)


@pytest.mark.asyncio
async def test_only_assistant_owner_bytes_are_sent_without_portable_settings(skills, assistant_folder):
    references, notices = await agent_v2._skills(assistant_metadata(assistant_folder), 'owui:user:fern')
    assert [reference['name'] for reference in references] == ['invented-helper']
    assert references[0]['type'] == 'chat-skill'
    assert not notices
    call = skills.upload.call_args_list[0]
    assert call.kwargs['as_user'] == 'owui:user:fern'
    with zipfile.ZipFile(io.BytesIO(call.args[1])) as archive:
        assert archive.namelist() == ['SKILL.md', 'assets/template.bin']
        assert archive.read('assets/template.bin') == skills.path.read_bytes()
        assert archive.read('SKILL.md').endswith(b'Invented private instructions')
    # Bytes are delegated through the assistant, not fetched through a caller file route.
    agent_assistant_skills.Models.get_model_by_id.assert_awaited_once_with(assistant_folder.id)


@pytest.mark.asyncio
async def test_assistant_ignores_standalone_skill_feature_and_permission(skills, assistant_folder, monkeypatch):
    references, notices = await agent_v2._skills(assistant_metadata(assistant_folder), 'owui:user:fern')
    assert len(references) == 1 and references[0]['name'] == 'invented-helper'
    assert not notices


@pytest.mark.asyncio
@pytest.mark.parametrize('gate', ['turn', 'effective', 'stored', 'platform', 'assistant'])
async def test_assistant_is_not_sent_when_workspace_or_identity_is_off(skills, assistant_folder, monkeypatch, gate):
    data = assistant_metadata(assistant_folder)
    if gate == 'turn':
        data['features']['workspace'] = False
    elif gate == 'effective':
        data['model']['info']['meta']['capabilities']['workspace'] = False
    elif gate == 'stored':
        assistant_folder.meta.capabilities['workspace'] = False
    elif gate == 'platform':
        monkeypatch.setattr(capabilities, 'has', AsyncMock(return_value=False))
    else:
        data['assistant_id'] = None
    assert await agent_v2._skills(data, 'owui:user:fern') == ([], [])
    skills.upload.assert_not_awaited()


@pytest.mark.asyncio
async def test_assistant_without_files_still_sends_skill_md(skills, assistant_folder, monkeypatch):
    skills.rows.clear()
    references, notices = await agent_v2._skills(assistant_metadata(assistant_folder), 'owui:user:fern')
    assert len(references) == 1 and not notices
    with zipfile.ZipFile(io.BytesIO(skills.upload.call_args.args[1])) as archive:
        assert archive.namelist() == ['SKILL.md']


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['instructions', 'name', 'description', 'path', 'file', 'add', 'remove', 'user'])
async def test_assistant_cache_key_changes_with_every_bundle_source(skills, assistant_folder, monkeypatch, change):
    data = assistant_metadata(assistant_folder)
    await agent_v2._skills(data, 'owui:user:fern')
    skills.storage.reset_mock()
    await agent_v2._skills(data, 'owui:user:fern')
    assert skills.upload.await_count == 1
    skills.storage.assert_not_called()
    caller = 'owui:user:fern'
    if change == 'instructions':
        assistant_folder.params.system += ' Invented edit'
    elif change == 'name':
        assistant_folder.name += ' edited'
    elif change == 'description':
        assistant_folder.meta.description += ' edited'
    elif change == 'path':
        skills.rows[0].path = 'assets/renamed.bin'
    elif change == 'file':
        skills.path.write_bytes(b'changed invented bytes')
        skills.file.hash = hashlib.sha256(skills.path.read_bytes()).hexdigest()
    elif change == 'add':
        skills.rows.append(SimpleNamespace(path='references/added.bin', file_id=skills.file.id, updated_at=1))
    elif change == 'remove':
        skills.rows.clear()
    else:
        caller = 'owui:user:reed'
    await agent_v2._skills(data, caller)
    assert skills.upload.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('language', ['en', 'nl'])
async def test_assistant_upload_refusal_and_missing_owner_bytes_produce_visible_notice(
    skills, assistant_folder, monkeypatch, language, caplog
):
    skills.upload.side_effect = SoevApiError(422, 'invalid_field', 'Invented private detail')
    data = assistant_metadata(assistant_folder, user_language=language)
    references, notices = await agent_v2._skills(data, 'owui:user:fern')
    assert not references and notices == [agent_skill_uploads._notice(assistant_folder.name, language)]
    skills.upload.reset_mock()
    skills.file.user_id = 'invented-other-owner'
    references, notices = await agent_v2._skills(data, 'owui:user:fern')
    assert not references and notices == [agent_skill_uploads._notice(assistant_folder.name, language)]
    skills.upload.assert_not_awaited()
    for secret in (
        'Invented private detail',
        assistant_folder.params.system,
        assistant_folder.name,
        skills.rows[0].path,
    ):
        assert secret not in caplog.text


@pytest.mark.asyncio
async def test_assistant_server_upload_failure_propagates_the_existing_turn_error(
    skills, assistant_folder, monkeypatch
):
    error = SoevApiError(503, 'service_unavailable', 'Invented private detail')
    skills.upload.side_effect = error
    with pytest.raises(SoevApiError) as caught:
        await agent_v2._skills(assistant_metadata(assistant_folder), 'owui:user:fern')
    assert caught.value is error


@pytest.mark.asyncio
async def test_expired_assistant_reference_is_forgotten_in_the_shared_cache(skills, assistant_folder, monkeypatch):
    data = assistant_metadata(assistant_folder)
    references, _ = await agent_v2._skills(data, 'owui:user:fern')
    assert len(agent_skill_uploads.REFERENCES) == 1
    assert agent_skill_uploads.forget_references('owui:user:fern', references, 'en') == [
        agent_skill_uploads._notice(assistant_folder.name, 'en')
    ]
    assert not agent_skill_uploads.REFERENCES
    await agent_v2._skills(data, 'owui:user:fern')
    assert skills.upload.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('changed', ['assistant', 'file'])
async def test_owner_changes_never_reuse_a_previously_delegated_cached_bundle(
    skills, assistant_folder, monkeypatch, changed
):
    data = assistant_metadata(assistant_folder)
    references, _ = await agent_v2._skills(data, 'owui:user:fern')
    assert len(references) == 1
    if changed == 'assistant':
        assistant_folder.user_id = 'invented-new-owner'
    else:
        skills.file.user_id = 'invented-new-owner'
    references, notices = await agent_v2._skills(data, 'owui:user:fern')
    assert references == []
    assert notices == [agent_skill_uploads._notice(assistant_folder.name, None)]
    assert skills.upload.await_count == 1


@pytest.mark.asyncio
async def test_standalone_ids_never_load_or_reach_the_agent(skills, assistant_folder, monkeypatch):
    from open_webui.models.skills import Skills

    standalone = AsyncMock(side_effect=AssertionError('Standalone skills must never be queried'))
    monkeypatch.setattr(Skills, 'get_skills', standalone)
    data = assistant_metadata(assistant_folder)
    data['skill_ids'] = ['invented-standalone']
    data['skills'] = [{'id': 'invented-standalone', 'content': 'PRIVATE STANDALONE CONTENT'}]
    data['model']['info']['meta']['skillIds'] = ['invented-standalone']
    refs, notices = await agent_v2._skills(data, 'owui:user:fern')
    assert len(refs) == 1 and refs[0]['name'] == 'invented-helper'
    assert not notices
    standalone.assert_not_awaited()
    data['assistant_id'] = None
    assert await agent_v2._skills(data, 'owui:user:fern') == ([], [])
    assert skills.upload.await_count == 1
    standalone.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('source', ['backend', 'credential'])
async def test_assistant_reference_cache_is_scoped_to_platform_and_credential(
    skills, assistant_folder, monkeypatch, source
):
    from open_webui import config

    data = assistant_metadata(assistant_folder)
    await agent_v2._skills(data, 'owui:user:fern')
    if source == 'backend':
        monkeypatch.setattr(config, 'SOEV_API_URL', 'https://invented-other.invalid')
    else:
        monkeypatch.setattr(config, 'SOEV_API_KEY', 'invented-other-credential')
    await agent_v2._skills(data, 'owui:user:fern')
    assert skills.upload.await_count == 2
