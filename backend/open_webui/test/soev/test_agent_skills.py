"""[Gradient] Workspace skills use caller-owned uploads without contacting the platform."""

import hashlib
import io
import re
import zipfile
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
import yaml
from open_webui.soev import capabilities
from open_webui.soev.client import ChatEvent, SoevApiError, SoevClient
from open_webui.utils import agent_skills, agent_v2


@pytest.fixture
def skills(monkeypatch, tmp_path, identity_config):
    skill = SimpleNamespace(
        id='invented-outline',
        name='Outline',
        description='Draft an outline',
        content='---\nname: old\ndescription: old\nlicense: MIT\n---\nOriginal body\n',
        is_active=True,
        updated_at=1,
    )
    path = tmp_path / 'template.bin'
    path.write_bytes(b'\x00\xffraw bytes')
    rows = [SimpleNamespace(path='assets/template.bin', file_id='template', updated_at=1)]
    file = SimpleNamespace(
        id='template', path=str(path), hash='original-hash', updated_at=1, meta={'size': path.stat().st_size}
    )
    monkeypatch.setattr(agent_skills.Skills, 'get_skills', AsyncMock(return_value=[skill]))
    monkeypatch.setattr(
        agent_skills.SkillFiles,
        'get_files_by_skill_ids',
        AsyncMock(side_effect=lambda ids: {skill_id: rows for skill_id in ids}),
    )
    monkeypatch.setattr(agent_skills.Files, 'get_files_by_ids', AsyncMock(return_value=[file]))
    monkeypatch.setattr(agent_skills.Files, 'get_file_metadatas_by_ids', AsyncMock(return_value=[file]))
    storage = Mock(side_effect=lambda path: path)
    monkeypatch.setattr(agent_skills.Storage, 'get_file', storage)
    monkeypatch.setattr(agent_skills.identity, 'ensure_link', AsyncMock())
    upload = AsyncMock(
        return_value={
            'id': 'a' * 32,
            'sha256': 'b' * 64,
            'size': 123,
            'name': 'platform-name',
            'description': 'Platform description',
        }
    )
    monkeypatch.setattr(SoevClient, 'post_bytes', upload)
    monkeypatch.setattr(agent_skills, 'REFERENCES', agent_skills.OrderedDict())
    monkeypatch.setattr(agent_v2, 'is_feature_enabled', lambda feature: True)
    monkeypatch.setattr(capabilities, 'has', AsyncMock(return_value=True))
    return SimpleNamespace(skill=skill, rows=rows, path=path, file=file, upload=upload, storage=storage)


def metadata(**extra):
    return {'user_id': 'fern', 'features': {'workspace': True}, **extra}


@pytest.mark.asyncio
async def test_bundles_override_identity_preserve_body_and_include_raw_bytes(skills):
    references, notices = await agent_v2._skills(metadata(), 'owui:user:fern')
    assert references == [skills.upload.return_value]
    assert not notices
    args, kwargs = skills.upload.call_args
    assert args[0] == '/v1/chat/skills'
    assert kwargs == {'as_user': 'owui:user:fern', 'content_type': 'application/zip'}
    with zipfile.ZipFile(io.BytesIO(args[1])) as archive:
        assert archive.namelist() == ['SKILL.md', 'assets/template.bin']
        text = archive.read('SKILL.md').decode()
        frontmatter = yaml.safe_load(text.split('---\n')[1])
        assert re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', frontmatter['name'])
        assert frontmatter['name'] == 'outline'
        assert frontmatter['description'] == 'Draft an outline'
        assert frontmatter['license'] == 'MIT'
        assert text.endswith('Original body\n')
        assert archive.read('assets/template.bin') == skills.path.read_bytes()
    agent_skills.Skills.get_skills.assert_awaited_once_with(user_id='fern')


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'change',
    ['content', 'name', 'description', 'bytes', 'path', 'add', 'remove', 'user', 'backend', 'credential'],
)
async def test_cache_rechecks_access_and_metadata_sources(skills, monkeypatch, change):
    await agent_v2._skills(metadata(), 'owui:user:fern')
    skills.storage.reset_mock()
    agent_skills.Files.get_files_by_ids.reset_mock()
    await agent_v2._skills(metadata(), 'owui:user:fern')
    assert skills.upload.await_count == 1
    skills.storage.assert_not_called()
    agent_skills.Files.get_files_by_ids.assert_not_awaited()
    caller = 'owui:user:fern'
    if change in ('content', 'name', 'description'):
        setattr(skills.skill, change, getattr(skills.skill, change) + ' changed')
    elif change == 'bytes':
        skills.path.write_bytes(b'changed')
        skills.file.hash = hashlib.sha256(b'changed').hexdigest()
    elif change == 'path':
        skills.rows[0].path = 'assets/renamed.bin'
    elif change == 'add':
        skills.rows.append(SimpleNamespace(path='notes.txt', file_id='template', updated_at=1))
    elif change == 'remove':
        skills.rows.clear()
    elif change == 'user':
        caller = 'owui:user:reed'
    elif change == 'backend':
        monkeypatch.setattr(agent_skills.config, 'SOEV_API_URL', 'https://other.invalid')
    else:
        monkeypatch.setattr(agent_skills.config, 'SOEV_API_KEY', 'different-test-credential')
    await agent_v2._skills(metadata(user_id='reed' if change == 'user' else 'fern'), caller)
    assert skills.upload.await_count == 2
    assert agent_skills.Skills.get_skills.await_count == 3
    if change == 'user':
        await agent_v2._skills(metadata(), 'owui:user:fern')
        assert skills.upload.await_count == 2  # several users retain independent references
    agent_skills.Skills.get_skills.return_value = []
    assert await agent_v2._skills(metadata(), caller) == ([], [])


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
    monkeypatch.setattr(agent_skills, '_bundle', bundle)
    skills.storage.reset_mock()
    await agent_v2._skills(metadata(), 'owui:user:fern')
    bundle.assert_not_awaited()
    skills.storage.assert_not_called()
    assert skills.upload.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('gate', ['workspace', 'feature', 'capability'])
async def test_disabled_gates_do_not_read_skills_or_upload(skills, monkeypatch, gate):
    data = metadata()
    if gate == 'workspace':
        data['features']['workspace'] = False
    elif gate == 'feature':
        monkeypatch.setattr(agent_v2, 'is_feature_enabled', lambda feature: False)
    else:
        monkeypatch.setattr(capabilities, 'has', AsyncMock(return_value=False))
    assert await agent_v2._skills(data, 'owui:user:fern') == ([], [])
    agent_skills.Skills.get_skills.assert_not_awaited()
    skills.upload.assert_not_awaited()


@pytest.mark.asyncio
async def test_attached_inactive_skills_first_then_recent_active_and_no_unreadable_ids(skills, caplog):
    candidates = [SimpleNamespace(**{**vars(skills.skill), 'id': f'skill-{i}', 'updated_at': i}) for i in range(23)]
    candidates[0].is_active = False
    candidates[22].is_active = False
    agent_skills.Skills.get_skills.return_value = candidates
    data = metadata(model={'info': {'meta': {'skillIds': ['skill-0', 'not-readable', 'skill-0']}}})
    with caplog.at_level('WARNING'):
        refs, notices = await agent_v2._skills(data, 'owui:user:fern')
    assert len(refs) == 20 and not notices
    assert 'left out 2 skills' in caplog.text
    agent_skills.SkillFiles.get_files_by_skill_ids.assert_awaited_once_with(
        ['skill-0'] + [f'skill-{i}' for i in range(21, 2, -1)]
    )
    agent_skills.Files.get_file_metadatas_by_ids.assert_awaited_once_with(['template'])


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
        assert notices == [agent_skills._notice('Outline', 'nl')]
        assert not agent_skills.REFERENCES
        assert 'PRIVATE DETAIL' not in caplog.text


@pytest.mark.asyncio
async def test_missing_bundle_file_is_not_silently_dropped(skills):
    agent_skills.Files.get_file_metadatas_by_ids.return_value = []
    agent_skills.Files.get_files_by_ids.return_value = []
    refs, notices = await agent_v2._skills(metadata(), 'owui:user:fern')
    assert not refs and len(notices) == 1
    skills.upload.assert_not_awaited()


@pytest.mark.asyncio
async def test_plain_content_and_empty_description(skills):
    skills.skill.content = 'Plain body'
    skills.skill.description = ''
    body = await agent_skills._bundle(skills.skill, 'invented-outline', skills.rows)
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        assert 'description: Outline\n' in archive.read('SKILL.md').decode()
        assert archive.read('SKILL.md').decode().endswith('---\nPlain body')


@pytest.mark.asyncio
async def test_reference_cache_is_bounded(skills, monkeypatch):
    monkeypatch.setattr(agent_skills, 'CACHE_LIMIT', 2)
    for user in ('fern', 'reed', 'moss'):
        await agent_v2._skills(metadata(user_id=user), f'owui:user:{user}')
    assert len(agent_skills.REFERENCES) == 2
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
async def test_readable_unique_capped_names_with_deterministic_collision_suffix(skills):
    ids = ['__Draft-Outline', 'draft-outline', 'a' * 65, 'a' * 64 + 'b', '___']
    agent_skills.Skills.get_skills.return_value = [
        SimpleNamespace(**{**vars(skills.skill), 'id': skill_id, 'name': '', 'updated_at': len(ids) - i})
        for i, skill_id in enumerate(ids)
    ]
    await agent_v2._skills(metadata(), 'owui:user:fern')
    names = []
    for call in skills.upload.await_args_list:
        with zipfile.ZipFile(io.BytesIO(call.args[1])) as archive:
            names.append(yaml.safe_load(archive.read('SKILL.md').decode().split('---\n')[1])['name'])
    assert names == [
        'draft-outline',
        'draft-outline-' + hashlib.sha256(ids[1].encode()).hexdigest()[:8],
        'a' * 64,
        'a' * 55 + '-' + hashlib.sha256(ids[3].encode()).hexdigest()[:8],
        'skill-' + hashlib.sha256(ids[4].encode()).hexdigest()[:8],
    ]
    assert len(set(names)) == len(ids)
    assert all(re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', name) for name in names)
    assert [agent_skills._skill_name(skill_id, set(names[:i])) for i, skill_id in enumerate(ids)] == names


def test_name_comes_from_the_skill_name_not_its_suffixed_id():
    used: set[str] = set()
    assert agent_skills._skill_name('house-style-784e24', used, 'House Style (Docx)') == 'house-style-docx'
    again = agent_skills._skill_name('house-style', used, 'House Style (Docx)')
    assert again == 'house-style-docx-' + hashlib.sha256(b'house-style').hexdigest()[:8]
    assert agent_skills._skill_name('fallback-id', used, '¿?') == 'fallback-id'


def test_collision_with_an_existing_suffixed_slug():
    used = {'outline'}
    first = agent_skills._skill_name('_outline', used)
    used.remove('outline')
    assert agent_skills._skill_name(first, used) != first


@pytest.mark.asyncio
async def test_cache_tracks_stored_create_edit_move_and_remove_in_one_second(skills, monkeypatch, tmp_path):
    from contextlib import asynccontextmanager

    from open_webui.internal.db import Base
    from open_webui.models import files as files_module
    from open_webui.models import skill_files as skill_files_module
    from open_webui.models.skills import Skill
    from open_webui.models.users import User
    from open_webui.routers import skill_files as router
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda db: Base.metadata.create_all(
                db,
                tables=[
                    User.__table__,
                    Skill.__table__,
                    files_module.File.__table__,
                    skill_files_module.SkillFile.__table__,
                ],
            )
        )
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def context(db=None):
        if db is not None:
            yield db
        else:
            async with sessions() as session:
                yield session

    monkeypatch.setattr(files_module, 'get_async_db_context', context)
    monkeypatch.setattr(skill_files_module, 'get_async_db_context', context)
    monkeypatch.setattr(files_module.time, 'time', lambda: 100)
    monkeypatch.setattr(
        agent_skills.SkillFiles, 'get_files_by_skill_ids', skill_files_module.SkillFilesTable().get_files_by_skill_ids
    )
    for method in ('get_files_by_ids', 'get_file_metadatas_by_ids'):
        monkeypatch.setattr(agent_skills.Files, method, getattr(files_module.FilesTable(), method))
    skills.skill.user_id = 'fern'
    monkeypatch.setattr(router.Skills, 'get_skill_by_id', AsyncMock(return_value=skills.skill))
    user = SimpleNamespace(id='fern', role='user', email='fern@example.invalid', name='Fern')

    def upload(file, name, tags):
        content = file.read()
        path = tmp_path / name
        path.write_bytes(content)
        return content, str(path)

    monkeypatch.setattr(agent_skills.Storage, 'upload_file', upload)

    async def check_upload(count):
        await agent_v2._skills(metadata(), 'owui:user:fern')
        assert skills.upload.await_count == count
        skills.storage.reset_mock()
        await agent_v2._skills(metadata(), 'owui:user:fern')
        assert skills.upload.await_count == count
        skills.storage.assert_not_called()

    try:
        async with sessions() as db:
            created = await router.create_skill_file(
                skills.skill.id, router.SkillFileCreateForm(path='docs/guide.md', content='first'), user, db
            )
            await check_upload(1)
            for count, content in enumerate(('other', 'third'), start=2):
                await router.edit_skill_file(
                    skills.skill.id, router.SkillFileEditForm(path='docs/guide.md', content=content), user, db
                )
                file = await files_module.Files.get_file_by_id(created.file_id, db=db)
                assert file.updated_at == created.updated_at == 100
                assert file.meta['size'] == 5
                assert file.hash == hashlib.sha256(content.encode()).hexdigest()
                await check_upload(count)
            await router.create_skill_file(
                skills.skill.id, router.SkillFileCreateForm(path='docs/extra.md', content='extra'), user, db
            )
            await check_upload(4)
            await router.move_skill_file(
                skills.skill.id, router.SkillFileMoveForm(from_path='docs/guide.md', to_path='guide.md'), user, db
            )
            await check_upload(5)
            await router.move_skill_file(
                skills.skill.id, router.SkillFileMoveForm(from_path='docs', to_path='references'), user, db
            )
            await check_upload(6)
            await router.remove_skill_file(skills.skill.id, router.SkillFilePathForm(path='guide.md'), user, db)
            await check_upload(7)
            await router.remove_skill_file(skills.skill.id, router.SkillFilePathForm(path='references'), user, db)
            await check_upload(8)
    finally:
        await engine.dispose()


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
    turn = agent_v2.AgentTurn(agent_skills.identity.build_client(), data, 'owui:user:fern')
    run = Mock()
    monkeypatch.setattr(turn, 'run', run)
    if refused:
        skills.upload.side_effect = SoevApiError(422, 'invalid_field', 'Private detail')
    await agent_v2._sent(turn, 'Draft an outline', data, agent='example-agent', model='example-model')
    sent = run.call_args.args[0]['input']
    assert sent['tools']['workspace_task'] == 'auto'
    assert sent.get('skills') == (None if refused else [skills.upload.return_value])
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
    turn = agent_v2.AgentTurn(agent_skills.identity.build_client(), data, 'owui:user:fern')
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
    candidates = [skills.skill, SimpleNamespace(**{**vars(skills.skill), 'id': 'summary', 'name': 'Summary'})]
    agent_skills.Skills.get_skills.return_value = candidates
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
    assert skills.upload.await_count == 4  # two original uploads, two fresh uploads
    assert skills.storage.call_count == 2  # cache hit first, then rebuild both bundles
    assert sends[0][1]['input']['skills'] == original
    assert sends[1][1]['input']['skills'] == uploaded[2:]
    expected_path = '/v1/chat/threads/invented-thread/inputs' if existing_thread else '/v1/chat/threads'
    assert all(path == expected_path for path, _, _ in sends)
    assert all(kwargs['as_user'] == turn.as_user for _, _, kwargs in sends)
    for _, body, _ in sends:
        assert body['input']['text'] == 'Draft an outline'
        assert body['model'] == 'example-model'
    if give_up:
        assert 'skills' not in sends[2][1]['input']
        assert turn.notices == [agent_skills._notice(skill.name, language) for skill in candidates]
        assert not agent_skills.REFERENCES
        agent_v2.Chats.upsert_message_to_chat_by_id_and_message_id.assert_awaited_once_with(
            'invented-chat', 'invented-answer', {'notices': turn.notices}
        )
        turn.emit.assert_any_await('chat:message:notices', {'notices': turn.notices, 'removed': []})
    else:
        assert not turn.notices
        assert len(agent_skills.REFERENCES) == 2
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
    assert len(agent_skills.REFERENCES) == 1
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
async def test_forgetting_references_is_scoped_to_user_and_turn_skills(skills):
    async def upload(path, body, **kwargs):
        return {**skills.upload.return_value, 'id': str(skills.upload.await_count)}

    skills.upload.side_effect = upload
    old, _ = await agent_v2._skills(metadata(), 'owui:user:fern')
    skills.skill.content += ' changed'
    current, _ = await agent_v2._skills(metadata(), 'owui:user:fern')
    other_user, _ = await agent_v2._skills(metadata(user_id='reed'), 'owui:user:reed')
    skills.skill.id = 'other-skill'
    other_skill, _ = await agent_v2._skills(metadata(), 'owui:user:fern')
    assert len(agent_skills.REFERENCES) == 4
    assert agent_skills.forget_references('owui:user:fern', current, 'en') == [agent_skills._notice('Outline', 'en')]
    assert len(agent_skills.REFERENCES) == 2  # all versions of the turn's skill, only for this user
    remaining = [reference for reference, _ in agent_skills.REFERENCES.values()]
    assert remaining == other_user + other_skill
    assert old[0] not in remaining
