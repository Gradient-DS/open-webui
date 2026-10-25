"""Optional soev configuration must leave local operations and ACLs intact."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from open_webui import config
from open_webui.soev.availability import SoevNotConfigured, configured, require_soev


@pytest.fixture(params=[False, True], ids=['unconfigured', 'configured'])
def soev_state(request, monkeypatch):
    monkeypatch.setattr(config, 'SOEV_API_URL', 'https://soev.invalid' if request.param else '')
    return request.param


@pytest.mark.asyncio
async def test_dependency_and_registered_global_handler(soev_state, monkeypatch):
    from open_webui.main import app as main_app
    from open_webui.soev import identity
    from open_webui.utils.auth import get_current_user

    app = FastAPI(exception_handlers={SoevNotConfigured: main_app.exception_handlers[SoevNotConfigured]})
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id='reader', role='user')

    @app.get('/dependency', dependencies=[Depends(require_soev)])
    async def dependency():
        return {'ok': True}

    @app.get('/backstop')
    async def backstop():
        identity.build_client()
        return {'ok': True}

    assert configured() is soev_state
    async with AsyncClient(transport=ASGITransport(app), base_url='http://test') as client:
        for path in ['/dependency', '/backstop']:
            response = await client.get(path)
            assert response.status_code == (200 if soev_state else 503)
            assert response.json() == (
                {'ok': True}
                if soev_state
                else {'detail': {'code': 'soev_not_configured', 'detail': 'Soev API is not configured.'}}
            )


@pytest.mark.asyncio
async def test_unconfigured_soev_refuses_callers_by_auth_first(monkeypatch):
    from fastapi import HTTPException, Request
    from open_webui.main import app as main_app
    from open_webui.routers import cloud_sync, knowledge, live_documents
    from open_webui.utils.auth import get_current_user

    def current_user(request: Request):
        role = request.headers.get('x-role')
        if role is None:
            raise HTTPException(status_code=401, detail='Not authenticated')
        return SimpleNamespace(id=role, role=role)

    monkeypatch.setattr(config, 'SOEV_API_URL', '')
    app = FastAPI(exception_handlers={SoevNotConfigured: main_app.exception_handlers[SoevNotConfigured]})
    app.dependency_overrides[get_current_user] = current_user
    for prefix, router in [
        ('/knowledge', knowledge.router),
        ('/live', live_documents.router),
        ('/cloud', cloud_sync.router),
    ]:
        app.include_router(router, prefix=prefix)
    unavailable = {'detail': {'code': 'soev_not_configured', 'detail': 'Soev API is not configured.'}}
    async with AsyncClient(transport=ASGITransport(app), base_url='http://test') as client:
        for role, method, path, status in [
            (None, 'GET', '/knowledge/', 401),
            (None, 'POST', '/live/onedrive/attach', 401),
            (None, 'GET', '/cloud/knowledge/kb/sync', 401),
            (None, 'GET', '/knowledge/kb/export', 401),
            ('user', 'GET', '/knowledge/', 503),
            ('user', 'POST', '/live/onedrive/attach', 503),
            ('user', 'GET', '/cloud/knowledge/kb/sync', 503),
            ('user', 'GET', '/knowledge/kb/export', 401),
            ('user', 'POST', '/knowledge/metadata/reindex', 401),
            ('admin', 'GET', '/knowledge/kb/export', 503),
        ]:
            headers = {} if role is None else {'x-role': role}
            response = await client.request(method, path, headers=headers)
            assert response.status_code == status, (role, path, response.text)
            if status == 503:
                assert response.json() == unavailable


def test_every_soev_gated_route_resolves_availability_last():
    from open_webui.main import app as main_app
    from open_webui.soev.availability import SoevRoute

    gated = [
        route
        for route in main_app.routes
        if any(
            dependency.call is require_soev
            for dependency in getattr(route, 'dependant', SimpleNamespace(dependencies=[])).dependencies
        )
    ]
    assert gated
    for route in gated:
        assert isinstance(route, SoevRoute), route.path
        assert route.dependant.dependencies[-1].call is require_soev, route.path


def test_all_knowledge_routes_are_soev_gated():
    from open_webui.routers import knowledge

    for route in knowledge.router.routes:
        assert any(dependency.call is require_soev for dependency in route.dependant.dependencies), route.path


@pytest.fixture
def access(monkeypatch):
    from open_webui.utils.access_control import files

    file = SimpleNamespace(id='file', user_id='owner', meta={'collection_name': 'kb'})
    kb = SimpleNamespace(id='kb', user_id='reader')
    monkeypatch.setattr(files.Files, 'get_file_by_id', AsyncMock(return_value=file))
    monkeypatch.setattr(files.Knowledges, 'get_knowledges_by_file_id', AsyncMock(return_value=[kb]))
    monkeypatch.setattr(files.Knowledges, 'get_knowledge_by_id', AsyncMock(return_value=kb))
    monkeypatch.setattr(files.Knowledges, 'check_access_by_user_id', AsyncMock(return_value=True))
    monkeypatch.setattr(files.Groups, 'get_groups_by_member_id', AsyncMock(return_value=[]))
    monkeypatch.setattr(files.Chats, 'get_shared_chat_ids_by_file_id', AsyncMock(return_value=[]))
    monkeypatch.setattr(files.Models, 'get_model_owners_attaching_file', AsyncMock(return_value={}))
    monkeypatch.setattr(files.AccessGrants, 'get_accessible_resource_ids', AsyncMock(return_value=set()))
    return files


@pytest.mark.asyncio
@pytest.mark.parametrize('permission', ['read', 'write'])
async def test_file_access_no_kb_grants_when_unconfigured(soev_state, access, permission):
    assert await access.has_access_to_file('file', permission, SimpleNamespace(id='owner', role='user'))
    access.Knowledges.get_knowledges_by_file_id.assert_not_awaited()
    allowed = await access.has_access_to_file('file', permission, SimpleNamespace(id='reader', role='user'))
    assert allowed is (soev_state and permission == 'read')
    assert access.Knowledges.get_knowledges_by_file_id.await_count == int(soev_state)
    if not soev_state:
        access.Knowledges.get_knowledge_by_id.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('grant', ['shared_chat', 'model'])
async def test_local_grants_still_work_without_soev(access, monkeypatch, grant):
    monkeypatch.setattr(config, 'SOEV_API_URL', '')
    if grant == 'shared_chat':
        access.Chats.get_shared_chat_ids_by_file_id.return_value = ['chat']
        access.AccessGrants.get_accessible_resource_ids.return_value = {'chat'}
    else:
        access.Models.get_model_owners_attaching_file.return_value = {'model': 'owner'}
        access.AccessGrants.get_accessible_resource_ids.return_value = {'model'}
    assert await access.has_access_to_file('file', 'read', SimpleNamespace(id='reader', role='user'))
    access.Knowledges.get_knowledges_by_file_id.assert_not_awaited()
    access.Knowledges.get_knowledge_by_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_folder_kb_access(soev_state, access):
    entry = {'id': 'kb', 'type': 'collection'}
    result = await access.get_accessible_folder_files([entry], SimpleNamespace(id='reader', role='user'))
    assert result == ([entry] if soev_state else [])
    assert access.Knowledges.check_access_by_user_id.await_count == int(soev_state)


@pytest.mark.asyncio
async def test_file_delete_skips_kb_cleanup(soev_state, monkeypatch):
    from open_webui.routers import files

    file = SimpleNamespace(id='file', user_id='owner', meta={'soev_job': {'job_id': 'job'}}, path=None, filename='f')
    monkeypatch.setattr(files.Files, 'get_file_by_id', AsyncMock(return_value=file))
    monkeypatch.setattr(files.Files, 'delete_file_by_id', AsyncMock(return_value=True))
    monkeypatch.setattr(
        files.Knowledges, 'get_knowledges_by_file_id', AsyncMock(return_value=[SimpleNamespace(id='kb')])
    )
    monkeypatch.setattr(files.Knowledges, 'remove_file_from_knowledge_by_id', AsyncMock())
    monkeypatch.setattr(files.ingest, 'cancel', AsyncMock())
    monkeypatch.setattr(files, 'publish_event', AsyncMock())
    result = await files.delete_file_by_id(None, 'file', user=SimpleNamespace(id='owner'), db=None)
    assert result == {'message': 'File deleted successfully'}
    files.Files.delete_file_by_id.assert_awaited_once_with('file', db=None)
    assert files.Knowledges.get_knowledges_by_file_id.await_count == int(soev_state)
    assert files.Knowledges.remove_file_from_knowledge_by_id.await_count == int(soev_state)
    assert files.ingest.cancel.await_count == int(soev_state)


@pytest.mark.asyncio
async def test_content_update_skips_only_kb_propagation(soev_state, monkeypatch):
    from open_webui.routers import files

    file = SimpleNamespace(id='file', user_id='owner', data={'content': 'updated'})
    monkeypatch.setattr(files.Files, 'get_file_by_id', AsyncMock(return_value=file))
    monkeypatch.setattr(files.Config, 'get', AsyncMock(return_value=None))
    monkeypatch.setattr(files, 'process_file', AsyncMock())
    monkeypatch.setattr(files, 'publish_event', AsyncMock())
    monkeypatch.setattr(
        files.Knowledges, 'get_knowledges_by_file_id', AsyncMock(return_value=[SimpleNamespace(id='kb')])
    )
    result = await files.update_file_data_content_by_id(
        None, 'file', files.ContentForm(content='updated'), user=SimpleNamespace(id='owner'), db=None
    )
    assert result == {'content': 'updated'}
    assert files.process_file.await_count == 1 + int(soev_state)
    assert files.process_file.await_args_list[0].args[1].content == 'updated'
    assert files.Knowledges.get_knowledges_by_file_id.await_count == int(soev_state)
    if soev_state:
        assert files.process_file.await_args_list[1].args[1].collection_name == 'kb'


@pytest.mark.asyncio
async def test_reset_always_resets_vectors(soev_state, monkeypatch):
    from open_webui.routers import retrieval

    monkeypatch.setattr(retrieval.ASYNC_VECTOR_DB_CLIENT, 'reset', AsyncMock())
    monkeypatch.setattr(retrieval.Knowledges, 'delete_all_knowledge', AsyncMock())
    monkeypatch.setattr(config, 'BYPASS_ADMIN_ACCESS_CONTROL', True)
    await retrieval.reset_vector_db(user=SimpleNamespace(role='admin'), db=None)
    retrieval.ASYNC_VECTOR_DB_CLIENT.reset.assert_awaited_once()
    assert retrieval.Knowledges.delete_all_knowledge.await_count == int(soev_state)


@pytest.mark.asyncio
@pytest.mark.parametrize('preview', ['user', 'group'])
async def test_previews(soev_state, monkeypatch, preview):
    from open_webui.routers import groups, users

    module = users if preview == 'user' else groups
    target = SimpleNamespace(id='target', name='Target', permissions={})
    kb = SimpleNamespace(id='kb', name='Knowledge', user_id='target')
    monkeypatch.setattr(users.Users, 'get_user_by_id', AsyncMock(return_value=target))
    monkeypatch.setattr(module.Groups, 'get_groups_by_member_id', AsyncMock(return_value=[]))
    monkeypatch.setattr(module.Groups, 'get_group_by_id', AsyncMock(return_value=target))
    monkeypatch.setattr(module.Models, 'get_all_models', AsyncMock(return_value=[]))
    monkeypatch.setattr(module.Tools, 'get_tools', AsyncMock(return_value=[]))
    monkeypatch.setattr(module.AccessGrants, 'get_accessible_resource_ids', AsyncMock(return_value={'kb'}))
    monkeypatch.setattr(module.Knowledges, 'get_knowledge_bases', AsyncMock(return_value=[kb]))
    handler = users.get_user_preview if preview == 'user' else groups.preview_group_access
    result = await handler('target', user=None, db=None)
    assert result['knowledge'] == {
        'items': [{'id': 'kb', 'name': 'Knowledge'}] if soev_state else [],
        'total': int(soev_state),
    }
    assert module.Knowledges.get_knowledge_bases.await_count == int(soev_state)


@pytest.mark.asyncio
async def test_export_includes_local_data_without_soev(soev_state, monkeypatch):
    from open_webui.services.export import service

    monkeypatch.setattr(service.Users, 'get_user_by_id', AsyncMock(return_value=None))
    for table, method in [
        (service.Chats, 'get_chats_by_user_id'),
        (service.Memories, 'get_memories_by_user_id'),
        (service.Notes, 'get_notes_by_user_id'),
        (service.Prompts, 'get_prompts_by_user_id'),
        (service.Tools, 'get_tools_by_user_id'),
        (service.Models, 'get_models'),
        (service.Feedbacks, 'get_feedbacks_by_user_id'),
        (service.Tags, 'get_tags_by_user_id'),
        (service.Folders, 'get_folders_by_user_id'),
    ]:
        monkeypatch.setattr(table, method, AsyncMock(return_value=[]))
    monkeypatch.setattr(service.Files, 'get_files_by_user_id', AsyncMock(return_value=[{'id': 'local'}]))
    monkeypatch.setattr(service.Knowledges, 'get_knowledge_bases_by_user_id', AsyncMock(return_value=[{'id': 'kb'}]))
    result = await service.ExportService.collect_user_data('owner')
    assert result['files'] == [{'id': 'local'}]
    assert result['knowledge_bases'] == ([{'id': 'kb'}] if soev_state else [])
    assert service.Knowledges.get_knowledge_bases_by_user_id.await_count == int(soev_state)


@pytest.mark.asyncio
async def test_retrieval_access_does_not_allow_unscoped_collections_without_soev(soev_state, monkeypatch):
    from open_webui.retrieval import utils

    monkeypatch.setattr(utils, 'ENABLE_RETRIEVAL_UNSCOPED_COLLECTIONS', True)
    monkeypatch.setattr(utils.Knowledges, 'check_access_by_user_id', AsyncMock(return_value=True))
    monkeypatch.setattr(utils.Knowledges, 'get_knowledge_by_id', AsyncMock())
    monkeypatch.setattr(utils, 'has_access_to_file', AsyncMock(return_value=True))
    result = await utils.filter_accessible_collections(
        {'kb', 'file-local', 'user-memory-reader', 'user-memory-other'}, SimpleNamespace(id='reader', role='user')
    )
    assert result == {'file-local', 'user-memory-reader'} | ({'kb'} if soev_state else set())
    assert utils.Knowledges.check_access_by_user_id.await_count == int(soev_state)
    utils.Knowledges.get_knowledge_by_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_chat_thread_cleanup_skips_soev(soev_state, monkeypatch):
    from open_webui.soev import agent_threads

    client = SimpleNamespace(chat_delete=AsyncMock())
    build = Mock(return_value=client)
    monkeypatch.setattr(agent_threads.identity, 'build_client', build)
    assert await agent_threads.delete_threads('owner', ['thread']) == []
    assert build.call_count == int(soev_state)
    assert client.chat_delete.await_count == int(soev_state)


@pytest.mark.asyncio
async def test_knowledge_store_never_swallows_not_configured(monkeypatch):
    from open_webui.models.files import Files
    from open_webui.soev.knowledge_store import SoevKnowledgeTable

    monkeypatch.setattr(config, 'SOEV_API_URL', '')
    store = SoevKnowledgeTable()
    monkeypatch.setattr(Files, 'get_unlanded_files_for_collection', AsyncMock(return_value=[]))
    with pytest.raises(SoevNotConfigured):
        await store.get_knowledge_bases()
    with pytest.raises(SoevNotConfigured):
        await store.reset_knowledge_by_id('kb')
    monkeypatch.setattr(store, '_move_document', AsyncMock(side_effect=SoevNotConfigured()))
    with pytest.raises(SoevNotConfigured):
        await store.move_file_to_directory('kb', 'file')


@pytest.mark.asyncio
async def test_catalog_backstop_keeps_error_code(monkeypatch):
    from open_webui.models import knowledge
    from open_webui.soev import catalog_content
    from open_webui.soev.knowledge_store import SoevKnowledgeTable

    monkeypatch.setattr(config, 'SOEV_API_URL', '')
    monkeypatch.setattr(knowledge, 'Knowledges', SoevKnowledgeTable())
    for operation in [
        catalog_content.catalog_file('remote', SimpleNamespace(id='reader')),
        catalog_content.stream_catalog_content('remote', SimpleNamespace(id='reader'), attachment=False),
    ]:
        with pytest.raises(SoevNotConfigured):
            await operation


@pytest.mark.asyncio
async def test_deletion_service_cleans_local_file(soev_state, monkeypatch):
    from open_webui.services.deletion import service

    file = SimpleNamespace(id='file', path=None, hash=None)
    monkeypatch.setattr(service.Files, 'get_file_by_id', AsyncMock(return_value=file))
    monkeypatch.setattr(service.Files, 'delete_file_by_id', AsyncMock(return_value=True))
    monkeypatch.setattr(service.Knowledges, 'get_knowledge_files_by_file_id', AsyncMock(return_value=[]))
    monkeypatch.setattr(service.ASYNC_VECTOR_DB_CLIENT, 'has_collection', AsyncMock(return_value=True))
    monkeypatch.setattr(service.ASYNC_VECTOR_DB_CLIENT, 'delete_collection', AsyncMock())
    report = await service.DeletionService.delete_file('file')
    assert not report.errors
    assert report.db_records == {'file': 1}
    assert report.vector_collections == 1
    assert service.Knowledges.get_knowledge_files_by_file_id.await_count == int(soev_state)


@pytest.mark.asyncio
async def test_orphan_cleanup_retains_chat_references(soev_state, monkeypatch):
    from open_webui.models.chats import Chats
    from open_webui.services.deletion import service

    monkeypatch.setattr(service.Knowledges, 'get_referenced_file_ids', AsyncMock(return_value={'kb-file'}))
    monkeypatch.setattr(Chats, 'get_referenced_file_ids', AsyncMock(return_value={'chat-file'}))
    monkeypatch.setattr(service.Files, 'get_files_by_ids', AsyncMock(return_value=[]))
    monkeypatch.setattr(service.Files, 'delete_files_by_ids', AsyncMock())
    monkeypatch.setattr(service.ASYNC_VECTOR_DB_CLIENT, 'delete_collection', AsyncMock())
    report = await service.DeletionService.delete_orphaned_files_batch(['kb-file', 'chat-file', 'orphan'])
    assert not report.errors
    service.Files.delete_files_by_ids.assert_awaited_once_with(['orphan'] if soev_state else ['kb-file', 'orphan'])
    assert service.Knowledges.get_referenced_file_ids.await_count == int(soev_state)
