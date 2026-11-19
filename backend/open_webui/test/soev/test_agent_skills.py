"""[Gradient] Workspace skills use caller-owned uploads without contacting the platform."""

import hashlib
import io
import re
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import yaml
from open_webui.soev import capabilities
from open_webui.soev.client import SoevApiError, SoevClient
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
    rows = [SimpleNamespace(path='assets/template.bin', file_id='template')]
    monkeypatch.setattr(agent_skills.Skills, 'get_skills', AsyncMock(return_value=[skill]))
    monkeypatch.setattr(agent_skills.SkillFiles, 'get_files_by_skill_id', AsyncMock(return_value=rows))
    monkeypatch.setattr(agent_skills.Files, 'get_file_by_id', AsyncMock(return_value=SimpleNamespace(path=str(path))))
    monkeypatch.setattr(agent_skills.Storage, 'get_file', lambda path: path)
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
    return SimpleNamespace(skill=skill, rows=rows, path=path, upload=upload)


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
        assert frontmatter['name'] == 'invented-outline'
        assert frontmatter['description'] == 'Draft an outline'
        assert frontmatter['license'] == 'MIT'
        assert text.endswith('Original body\n')
        assert archive.read('assets/template.bin') == skills.path.read_bytes()
    agent_skills.Skills.get_skills.assert_awaited_once_with(user_id='fern')


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'change', ['content', 'name', 'description', 'bytes', 'path', 'add', 'remove', 'user', 'backend']
)
async def test_cache_rechecks_access_and_all_sources_even_with_unchanged_timestamps(skills, monkeypatch, change):
    await agent_v2._skills(metadata(), 'owui:user:fern')
    await agent_v2._skills(metadata(), 'owui:user:fern')
    assert skills.upload.await_count == 1
    caller = 'owui:user:fern'
    if change in ('content', 'name', 'description'):
        setattr(skills.skill, change, getattr(skills.skill, change) + ' changed')
    elif change == 'bytes':
        skills.path.write_bytes(b'changed')
    elif change == 'path':
        skills.rows[0].path = 'assets/renamed.bin'
    elif change == 'add':
        skills.rows.append(SimpleNamespace(path='notes.txt', file_id='notes'))
    elif change == 'remove':
        skills.rows.clear()
    elif change == 'user':
        caller = 'owui:user:reed'
    else:
        monkeypatch.setattr(agent_skills.config, 'SOEV_API_URL', 'https://other.invalid')
    await agent_v2._skills(metadata(user_id='reed' if change == 'user' else 'fern'), caller)
    assert skills.upload.await_count == 2
    assert agent_skills.Skills.get_skills.await_count == 3
    if change == 'user':
        await agent_v2._skills(metadata(), 'owui:user:fern')
        assert skills.upload.await_count == 2  # several users retain independent references
    agent_skills.Skills.get_skills.return_value = []
    assert await agent_v2._skills(metadata(), caller) == ([], [])


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
    assert [call.args[0] for call in agent_skills.SkillFiles.get_files_by_skill_id.await_args_list] == (
        ['skill-0'] + [f'skill-{i}' for i in range(21, 2, -1)]
    )


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
    agent_skills.Files.get_file_by_id.return_value = None
    refs, notices = await agent_v2._skills(metadata(), 'owui:user:fern')
    assert not refs and len(notices) == 1
    skills.upload.assert_not_awaited()


@pytest.mark.asyncio
async def test_plain_content_and_empty_description(skills):
    skills.skill.content = 'Plain body'
    skills.skill.description = ''
    body = await agent_skills._bundle(skills.skill, 'invented-outline')
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
        SimpleNamespace(**{**vars(skills.skill), 'id': skill_id, 'updated_at': len(ids) - i})
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


def test_collision_with_an_existing_suffixed_slug():
    used = {'outline'}
    first = agent_skills._skill_name('_outline', used)
    used.remove('outline')
    assert agent_skills._skill_name(first, used) != first


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
