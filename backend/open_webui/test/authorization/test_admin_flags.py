"""Administrator content access requires the flag for that content domain."""

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import update
from sqlalchemy.orm import Session

from .test_matrix import seed_resource


@pytest.fixture(params=[False, True])
def bypass(request, seeded, monkeypatch):
    from open_webui import config

    for flag in ('BYPASS_ADMIN_ACCESS_CONTROL', 'ENABLE_ADMIN_CHAT_ACCESS', 'ENABLE_ADMIN_EXPORT'):
        monkeypatch.setattr(config, flag, request.param)
    return request.param


@pytest.mark.parametrize(
    'suffix',
    [
        '/content',
        '/content/original.txt',
        '/content/html',
        '/data/content',
        '/process/status',
        '/attachments',
        '/attachments/attachment',
    ],
)
def test_file_content(seeded, monkeypatch, tmp_path, bypass, suffix):
    from open_webui.models.file_attachments import FileAttachment
    from open_webui.models.files import File
    from open_webui.models.users import User
    from open_webui.routers import files

    seeded.run(seed_resource, seeded, 'file')
    path = tmp_path / 'original.txt'
    path.write_text('private file')
    with Session(seeded.engine) as db:
        db.execute(update(File).where(File.id == 'target').values(path=str(path), data={'status': 'completed'}))
        db.execute(update(User).where(User.id == 'owner').values(role='admin'))
        db.add(
            FileAttachment(
                id='attachment',
                file_id='target',
                kind='image',
                index=0,
                content_type='text/plain',
                caption='',
                path=str(path),
                created_at=1,
            )
        )
        db.commit()
    monkeypatch.setattr(files.Storage, 'get_file', lambda value: value)
    monkeypatch.setattr(files.ingest, 'rendition_of', AsyncMock(return_value='private file'))
    response = seeded.client.get('/api/v1/files/target' + suffix, headers=seeded.headers('admin'))
    assert response.status_code == 200 if bypass else response.status_code in (401, 403, 404), response.text


@pytest.mark.parametrize('suffix', ['/files', '/files/pending', '/export'])
def test_knowledge_files(seeded, bypass, suffix):
    seeded.run(seed_resource, seeded, 'knowledge')
    response = seeded.client.get('/api/v1/knowledge/target' + suffix, headers=seeded.headers('admin'))
    assert response.status_code == 200 if bypass else response.status_code in (400, 403), response.text


@pytest.mark.parametrize(
    'method,suffix,body',
    [
        ('GET', '/messages', None),
        ('GET', '/messages/pinned', None),
        ('GET', '/messages/message', None),
        ('GET', '/messages/message/data', None),
        ('GET', '/messages/message/thread', None),
        ('POST', '/messages/message/update', {'content': 'changed'}),
        ('POST', '/messages/message/pin', {'is_pinned': True}),
        ('DELETE', '/messages/message/delete', None),
        ('GET', '/webhooks', None),
    ],
)
def test_channel_messages_without_membership(seeded, bypass, method, suffix, body):
    from open_webui.models.messages import Message

    seeded.run(seed_resource, seeded, 'channel')
    with Session(seeded.engine) as db:
        db.add(
            Message(
                id='message',
                user_id='owner',
                channel_id='target',
                content='private',
                data={},
                created_at=1,
                updated_at=1,
            )
        )
        db.commit()
    response = seeded.client.request(
        method, '/api/v1/channels/target' + suffix, json=body, headers=seeded.headers('admin')
    )
    assert response.status_code == (200 if bypass else 403), response.text
    if not bypass:
        with Session(seeded.engine) as db:
            assert db.get(Message, 'message').content == 'private'


@pytest.mark.parametrize('chat_access', [False, True])
@pytest.mark.parametrize('delete_contents', [False, True])
def test_folder_chat_cascade_requires_both_flags(seeded, monkeypatch, bypass, chat_access, delete_contents):
    from open_webui import config
    from open_webui.models.chats import Chat
    from open_webui.models.folders import Folder

    monkeypatch.setattr(config, 'ENABLE_ADMIN_CHAT_ACCESS', chat_access)
    seeded.run(seed_resource, seeded, 'folder')
    seeded.run(seed_resource, seeded, 'chat')
    with Session(seeded.engine) as db:
        db.execute(update(Chat).where(Chat.id == 'target').values(folder_id='target'))
        db.commit()
    response = seeded.client.delete(
        f'/api/v1/folders/target?delete_contents={str(delete_contents).lower()}', headers=seeded.headers('admin')
    )
    allowed = bypass and chat_access
    assert response.status_code == (200 if allowed else 403), response.text
    with Session(seeded.engine) as db:
        chat = db.get(Chat, 'target')
        if allowed:
            assert db.get(Folder, 'target') is None
            assert (chat.deleted_at is not None) if delete_contents else chat.folder_id is None
        else:
            assert db.get(Folder, 'target') is not None
            assert chat.deleted_at is None and chat.folder_id == 'target'


@pytest.mark.parametrize(
    'method,path,body',
    [
        ('GET', '/feedback/feedback', None),
        ('POST', '/feedback/feedback', {'type': 'rating', 'data': {'rating': 1}}),
        ('DELETE', '/feedback/feedback', None),
        ('GET', '/feedbacks/list', None),
        ('GET', '/feedbacks/models', None),
        ('GET', '/feedbacks/all/ids', None),
        ('GET', '/feedbacks/all/export', None),
        ('DELETE', '/feedbacks/all', None),
    ],
)
def test_feedback_content(seeded, bypass, monkeypatch, method, path, body):
    from open_webui import config
    from open_webui.models.feedbacks import Feedback

    # Export is independent of permission to browse individual chats.
    if path.endswith('/export'):
        monkeypatch.setattr(config, 'ENABLE_ADMIN_CHAT_ACCESS', not bypass)
    else:
        monkeypatch.setattr(config, 'ENABLE_ADMIN_EXPORT', not bypass)
    with Session(seeded.engine) as db:
        db.add(
            Feedback(
                id='feedback',
                user_id='owner',
                type='rating',
                data={'model_id': 'model'},
                snapshot={'chat': {'title': 'private'}},
                created_at=1,
                updated_at=1,
            )
        )
        db.commit()
    response = seeded.client.request(method, '/api/v1/evaluations' + path, json=body, headers=seeded.headers('admin'))
    assert response.status_code == 200 if bypass else response.status_code in (403, 404), response.text
    if not bypass:
        with Session(seeded.engine) as db:
            assert db.get(Feedback, 'feedback').snapshot['chat']['title'] == 'private'


@pytest.mark.parametrize('path', ['/chats/list/user/owner', '/chats/all/db', '/utils/db/download', '/utils/db/export'])
def test_admin_listing_and_exports(seeded, bypass, monkeypatch, path):
    from open_webui import config
    from open_webui.internal import db as database

    seeded.run(seed_resource, seeded, 'chat')
    if path.startswith('/chats/list'):
        monkeypatch.setattr(config, 'ENABLE_ADMIN_EXPORT', not bypass)
    else:
        monkeypatch.setattr(config, 'ENABLE_ADMIN_CHAT_ACCESS', not bypass)
    monkeypatch.setattr(database, 'engine', seeded.engine)
    response = seeded.client.get('/api/v1' + path, headers=seeded.headers('admin'))
    assert response.status_code == (200 if bypass else 401), response.text


@pytest.mark.parametrize('kind', ['note', 'model', 'prompt', 'tool', 'skill', 'knowledge', 'channel'])
def test_admin_reader_has_no_write_access(seeded, bypass, kind):
    from open_webui.models.access_grants import AccessGrants

    from .test_matrix import request_for

    seeded.run(seed_resource, seeded, kind)
    seeded.run(
        AccessGrants.set_access_grants,
        kind,
        'target',
        [{'principal_type': 'user', 'principal_id': 'admin', 'permission': 'read'}],
    )
    method, path, _ = request_for(kind, 'read')
    response = seeded.client.request(method, path, headers=seeded.headers('admin'))
    assert response.status_code == 200, response.text
    assert response.json()['write_access'] is bypass


@pytest.mark.parametrize('internal', [False, True])
def test_internal_chats_require_chat_flag(seeded, bypass, monkeypatch, internal):
    from open_webui import config
    from open_webui.models.chats import Chat

    monkeypatch.setattr(config, 'BYPASS_ADMIN_ACCESS_CONTROL', not bypass)
    seeded.run(seed_resource, seeded, 'chat')
    if internal:
        with Session(seeded.engine) as db:
            db.execute(update(Chat).where(Chat.id == 'target').values(meta={'internal': True, 'type': 'note'}))
            db.commit()
    response = seeded.client.get('/api/v1/chats/target', headers=seeded.headers('admin'))
    assert response.status_code == (200 if bypass else 401), response.text


@pytest.mark.parametrize(
    'module_name,function_name',
    [
        ('files', 'delete_all_files'),
        ('models', 'delete_all_models'),
        ('models', 'sync_models'),
        ('knowledge', 'reindex_knowledge_files'),
        ('knowledge', 'reindex_knowledge_base_metadata_embeddings'),
        ('retrieval', 'reset_vector_db'),
        ('retrieval', 'reset_upload_dir'),
        ('memories', 'reindex_memories_from_vector_db'),
    ],
)
def test_bulk_workspace_mutation(seeded, bypass, monkeypatch, tmp_path, module_name, function_name):
    import importlib

    from fastapi import HTTPException
    from open_webui.models.users import Users

    module = importlib.import_module(f'open_webui.routers.{module_name}')
    function = getattr(module, function_name)
    # Keep external storage/vector I/O local; authorization and SQL remain real.
    if hasattr(module, 'ASYNC_VECTOR_DB_CLIENT'):
        monkeypatch.setattr(
            module, 'ASYNC_VECTOR_DB_CLIENT', SimpleNamespace(reset=AsyncMock(), delete_collection=AsyncMock())
        )
    if hasattr(module, 'Storage'):
        monkeypatch.setattr(module.Storage, 'delete_all_files', lambda: None)
    if hasattr(module, 'UPLOAD_DIR'):
        monkeypatch.setattr(module, 'UPLOAD_DIR', str(tmp_path))
    if module_name == 'memories':
        monkeypatch.setattr(module, 'reindex_memory_vectors_for_user', AsyncMock(return_value=0))
    monkeypatch.setattr(module, 'publish_event', AsyncMock(), raising=False)

    async def check():
        async with seeded.sessions() as db:
            kwargs = {'user': await Users.get_user_by_id('admin', db=db)}
            parameters = inspect.signature(function).parameters
            if 'db' in parameters:
                kwargs['db'] = db
            if 'request' in parameters:
                kwargs['request'] = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=None)))
            if function_name == 'sync_models':
                kwargs['form_data'] = module.SyncModelsForm(models=[])
            if bypass:
                await function(**kwargs)
            else:
                with pytest.raises(HTTPException) as denied:
                    await function(**kwargs)
                assert denied.value.status_code == 403

    seeded.run(check)


@pytest.mark.parametrize('kind', ['file', 'note', 'skill', 'knowledge', 'folder'])
def test_runtime_content_helpers(seeded, bypass, kind):
    from open_webui.models.users import Users
    from open_webui.tools import builtin, knowledge_fs
    from open_webui.utils.access_control.files import get_accessible_folder_files
    from open_webui.utils.json_codec import JSONCodec

    seeded.run(seed_resource, seeded, kind)

    async def check():
        user = await Users.get_user_by_id('admin')
        if kind == 'file':
            from open_webui.models.files import Files

            return await builtin._has_read_access_to_file(await Files.get_file_by_id('target'), user.model_dump())
        if kind == 'knowledge':
            return bool(await knowledge_fs._get_accessible_kb_ids(user.model_dump(), None, 'target'))
        if kind == 'folder':
            await seed_resource(seeded, 'note')
            return bool(await get_accessible_folder_files([{'type': 'note', 'id': 'target'}], user))
        function = builtin.view_note if kind == 'note' else builtin.view_skill
        kwargs = {'note_id' if kind == 'note' else 'id': 'target'}
        result = JSONCodec.loads(await function(**kwargs, __request__=SimpleNamespace(), __user__=user.model_dump()))
        return 'error' not in result

    assert seeded.run(check) is bypass


@pytest.mark.parametrize('export_enabled', [False, True])
@pytest.mark.parametrize('kind', ['model', 'tool', 'skill'])
def test_workspace_bulk_exports_need_export_flag(seeded, bypass, monkeypatch, export_enabled, kind):
    from open_webui import config

    monkeypatch.setattr(config, 'ENABLE_ADMIN_EXPORT', export_enabled)
    seeded.run(seed_resource, seeded, kind)
    if kind == 'model':
        from open_webui.models.models import Model

        with Session(seeded.engine) as db:
            db.execute(update(Model).where(Model.id == 'target').values(base_model_id='base'))
            db.commit()
    response = seeded.client.get(f'/api/v1/{kind}s/export', headers=seeded.headers('admin'))
    assert response.status_code == 200, response.text
    assert bool(response.json()) is (bypass and export_enabled)


def test_shared_folder_chats_use_chat_flag(seeded, bypass, monkeypatch):
    from open_webui import config
    from open_webui.models.chats import Chat

    monkeypatch.setattr(config, 'BYPASS_ADMIN_ACCESS_CONTROL', not bypass)
    seeded.run(seed_resource, seeded, 'folder')
    seeded.run(seed_resource, seeded, 'chat')
    with Session(seeded.engine) as db:
        db.execute(update(Chat).where(Chat.id == 'target').values(folder_id='target'))
        db.commit()
    response = seeded.client.get('/api/v1/folders/target/shared/chats', headers=seeded.headers('admin'))
    assert response.status_code == (200 if bypass else 403), response.text


@pytest.mark.parametrize('function_name', ['stop_task_endpoint', 'list_tasks_endpoint', 'verify_chat_ownership'])
def test_chat_runtime_gates(seeded, bypass, monkeypatch, function_name):
    from fastapi import HTTPException
    from open_webui import config, main
    from open_webui.models.users import Users

    monkeypatch.setattr(config, 'BYPASS_ADMIN_ACCESS_CONTROL', not bypass)
    monkeypatch.setattr(main, 'stop_task', AsyncMock(return_value=True))
    monkeypatch.setattr(main, 'list_tasks', AsyncMock(return_value=[]))
    seeded.run(seed_resource, seeded, 'chat')

    async def check():
        user = await Users.get_user_by_id('admin')
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=None)))
        if function_name == 'verify_chat_ownership':
            kwargs = {'chat_id': 'target', 'user': user}
        else:
            kwargs = {'request': request, 'user': user}
            if function_name == 'stop_task_endpoint':
                kwargs['task_id'] = 'private-task'
        if bypass:
            await getattr(main, function_name)(**kwargs)
        else:
            with pytest.raises(HTTPException) as denied:
                await getattr(main, function_name)(**kwargs)
            assert denied.value.status_code in (403, 404)
            main.stop_task.assert_not_awaited()

    seeded.run(check)


@pytest.mark.parametrize(
    'kind,suffix', [('prompt', '/history'), ('skill', '/files'), ('tool', '/valves'), ('tool', '/valves/spec')]
)
def test_workspace_nested_content(seeded, bypass, kind, suffix):
    seeded.run(seed_resource, seeded, kind)
    response = seeded.client.get(f'/api/v1/{kind}s/id/target{suffix}', headers=seeded.headers('admin'))
    assert response.status_code == 200 if bypass else response.status_code in (401, 403), response.text
