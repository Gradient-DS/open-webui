from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from open_webui.models.models import ModelMeta, ModelModel, ModelParams
from open_webui.soev import acting, knowledge_sharing
from open_webui.soev.client import SoevApiError, SoevClient
from open_webui.utils.auth import get_verified_user


def saved_model():
    return ModelModel(
        id='fable/helper',
        user_id='fern',
        name='Fable helper',
        base_model_id='llm',
        meta=ModelMeta(knowledge=[{'type': 'collection', 'id': 'local'}]),
        params=ModelParams(),
        is_active=True,
        created_at=1,
        updated_at=1,
    )


@pytest.fixture
def api(monkeypatch):
    from open_webui.routers import models

    state = SimpleNamespace(user=SimpleNamespace(id='lark', role='admin'), model=saved_model())
    monkeypatch.setattr(models, 'configured', lambda: True)
    monkeypatch.setattr(models.assistant_sharing, 'configured', lambda: True)
    monkeypatch.setattr(models, 'can_bypass_access_control', lambda _: True)
    monkeypatch.setattr(models.Models, 'get_model_by_id', AsyncMock(side_effect=lambda *a, **k: state.model))
    monkeypatch.setattr(models.Models, 'get_models_by_ids', AsyncMock(return_value=[state.model]))
    monkeypatch.setattr(models.AccessGrants, 'has_access', AsyncMock(return_value=True))
    monkeypatch.setattr(models.Models, 'update_model_by_id', AsyncMock(return_value=state.model))
    monkeypatch.setattr(models.Models, 'delete_model_by_id', AsyncMock(return_value=True))
    monkeypatch.setattr(models, 'publish_event', AsyncMock())

    app = FastAPI()

    @app.exception_handler(SoevApiError)
    async def refusal(request, error):
        return JSONResponse(status_code=error.status, content={'detail': 'Refused'})

    async def requester():
        ref = f'owui:user:{state.user.id}'
        request_token = acting._requester_ref.set(ref)
        acting_token = acting._acting_ref.set(ref)
        try:
            yield
        finally:
            acting._acting_ref.reset(acting_token)
            acting._requester_ref.reset(request_token)

    app.include_router(models.router, prefix='/models', dependencies=[Depends(requester)])
    app.dependency_overrides[get_verified_user] = lambda: state.user
    state.client = TestClient(app, raise_server_exceptions=False)
    state.routes = models
    return state


@pytest.mark.parametrize('operation', ['access', 'knowledge', 'import'])
def test_another_user_cannot_change_assistant_sharing_or_knowledge_before_commit(api, operation):
    if operation == 'access':
        response = api.client.post('/models/model/access/update', json={'id': api.model.id, 'access_grants': []})
    else:
        payload = {**api.model.model_dump(), 'meta': {'knowledge': []}}
        if operation == 'import':
            response = api.client.post('/models/import', json={'models': [payload]})
        else:
            response = api.client.post('/models/model/update', json=payload)
    assert response.status_code == 403, response.text
    api.routes.Models.update_model_by_id.assert_not_awaited()


def test_an_assistant_writer_can_change_other_fields_without_republishing_grants(api):
    payload = {**api.model.model_dump(), 'name': 'Fable guide'}
    response = api.client.post('/models/model/update', json=payload)
    assert response.status_code == 200, response.text
    saved = api.routes.Models.update_model_by_id.await_args.args[1]
    assert saved.name == 'Fable guide' and saved.access_grants is None


def test_sharing_view_lists_owner_exclusions_and_hides_them_from_members(api, monkeypatch):
    left_out = [
        {'type': 'collection', 'id': 'cloud', 'name': 'Fable projects', 'reason': 'cloud'},
        {'type': 'collection', 'id': 'foreign', 'name': 'Lark projects', 'reason': 'owner'},
    ]
    inspect = AsyncMock(return_value=left_out)
    monkeypatch.setattr(knowledge_sharing, 'left_out', inspect)
    response = api.client.get('/models/model/sharing', params={'id': api.model.id})
    assert response.json() == {'shares_knowledge': True, 'may_share': False, 'left_out': []}
    inspect.assert_not_awaited()
    api.user.id = 'fern'
    response = api.client.get('/models/model/sharing', params={'id': api.model.id})
    assert response.json() == {'shares_knowledge': True, 'may_share': True, 'left_out': left_out}


@pytest.mark.parametrize('has_remote', [True, False])
def test_admin_assistant_delete_is_refused_only_when_remote_cleanup_is_needed(api, monkeypatch, has_remote):
    monkeypatch.setattr(api.routes.assistant_sharing, 'remote_cleanup_needed', AsyncMock(return_value=has_remote))
    response = api.client.post('/models/model/delete', json={'id': api.model.id})
    assert response.status_code == (403 if has_remote else 200)
    assert api.routes.Models.delete_model_by_id.await_count == (0 if has_remote else 1)


def test_file_delete_uses_requester_authority_and_refuses_before_local_commit(api, monkeypatch):
    from open_webui.routers import files
    from open_webui.soev import identity

    file = SimpleNamespace(id='upload', user_id='fern', meta={'soev_job': {'job_id': 'fictional-job'}}, path='')
    monkeypatch.setattr(files.Files, 'get_file_by_id', AsyncMock(return_value=file))
    monkeypatch.setattr(files.Models, 'get_model_owners_attaching_file', AsyncMock(return_value={api.model.id: 'fern'}))
    monkeypatch.setattr(files.Files, 'delete_file_by_id', AsyncMock())
    monkeypatch.setattr(files.Models, 'remove_file_from_models', AsyncMock())
    monkeypatch.setattr(files.ingest, 'cancel', AsyncMock())
    monkeypatch.setattr(files, 'configured', lambda: True)
    monkeypatch.setattr(files, 'can_bypass_access_control', lambda _: True)
    monkeypatch.setattr(identity, 'build_client', lambda: SoevClient('https://soev.invalid', 'fictional-key'))
    calls = []

    async def guarded(self, method, path, **kwargs):
        calls.append((method, path, kwargs.get('as_user')))
        assert kwargs.get('as_user') == 'owui:user:lark'
        raise SoevApiError(403, 'forbidden', 'fictional refusal')

    monkeypatch.setattr(SoevClient, '_request', guarded)
    app = api.client.app
    app.include_router(files.router, prefix='/files')

    # The model router dependency establishes a requester; do the same for the file route.
    @app.middleware('http')
    async def requester(request, call_next):
        token = acting._requester_ref.set('owui:user:lark')
        try:
            return await call_next(request)
        finally:
            acting._requester_ref.reset(token)

    response = api.client.delete('/files/upload')
    assert response.status_code == 403
    assert calls == [('DELETE', '/v1/collections/owui-assistant-fable%2Fhelper/documents/upload', 'owui:user:lark')]
    files.ingest.cancel.assert_not_awaited()
    files.Files.delete_file_by_id.assert_not_awaited()
    files.Models.remove_file_from_models.assert_not_awaited()


@pytest.mark.parametrize('remote', ['grant', 'files', 'empty'])
def test_nonowner_folder_delete_checks_subtree_without_acting_as_owner(api, monkeypatch, remote):
    import httpx
    from open_webui.routers import folders
    from open_webui.soev import folder_sharing, identity

    monkeypatch.setattr(folder_sharing, 'configured', lambda: True)

    folder = SimpleNamespace(id='project', user_id='fern', name='Fable project')
    monkeypatch.setattr(folders, 'check_folders_permission', AsyncMock())
    monkeypatch.setattr(folders, 'configured', lambda: True)
    monkeypatch.setattr(folders, 'can_bypass_access_control', lambda _: True)
    monkeypatch.setattr(folders.Folders, 'get_folder_by_id_and_user_id', AsyncMock(return_value=None))
    monkeypatch.setattr(folders.Folders, 'get_folder_by_id', AsyncMock(return_value=folder))
    monkeypatch.setattr(
        folders.Folders, 'get_folder_ids_by_id_and_user_id_in_subtree', AsyncMock(return_value=['project'])
    )
    monkeypatch.setattr(folders.Folders, 'get_folders_by_parent_id_and_user_id', AsyncMock(return_value=[]))
    delete = AsyncMock(return_value=['project'])
    monkeypatch.setattr(folders.Folders, 'delete_folder_by_id_and_user_id', delete)
    monkeypatch.setattr(folders.Chats, 'count_chats_by_folder_ids_and_user_id', AsyncMock(return_value=0))
    monkeypatch.setattr(folders.Chats, 'soft_delete_by_user_id_and_folder_id', AsyncMock())
    monkeypatch.setattr(folders.Automations, 'clear_folder_ids', AsyncMock())
    monkeypatch.setattr(folders.AccessGrants, 'revoke_all_access', AsyncMock())
    monkeypatch.setattr(folders, 'publish_event', AsyncMock())
    monkeypatch.setattr(
        folder_sharing,
        'view',
        AsyncMock(
            return_value=folder_sharing.View(
                parents={'project': None}, files={'project': [{'type': 'note', 'id': 'memo'}]}, grants={}
            )
        ),
    )
    from open_webui.models.folder_share_grants import FolderShareGrants

    monkeypatch.setattr(
        FolderShareGrants,
        'get_by_owner',
        AsyncMock(
            return_value=[SimpleNamespace(resource_type='note', resource_id='memo')] if remote == 'grant' else []
        ),
    )
    client = SoevClient('https://soev.invalid', 'fictional-key', subject_minter=lambda _: 'fictional-subject')
    monkeypatch.setattr(identity, 'build_client', lambda: client)
    monkeypatch.setattr(identity, 'ensure_link', AsyncMock())

    async def guarded(self, method, path, **kwargs):
        assert kwargs.get('as_user') in (None, 'owui:user:lark')
        if remote == 'empty':
            raise SoevApiError(404, 'collection_not_found', 'missing')
        if path.endswith('/documents'):
            return httpx.Response(200, json={'data': [{'source_id': 'upload'}]})
        return httpx.Response(200, json={'key': 'owui-folder-project', 'created_by': 'owui:user:fern'})

    monkeypatch.setattr(SoevClient, '_request', guarded)
    app = FastAPI()
    app.include_router(folders.router, prefix='/folders')
    app.dependency_overrides[get_verified_user] = lambda: api.user

    @app.middleware('http')
    async def requester(request, call_next):
        token = acting._requester_ref.set('owui:user:lark')
        try:
            return await call_next(request)
        finally:
            acting._requester_ref.reset(token)

    response = TestClient(app).delete('/folders/project')
    assert response.status_code == (200 if remote == 'empty' else 403), response.text
    assert delete.await_count == (1 if remote == 'empty' else 0)


@pytest.mark.asyncio
@pytest.mark.parametrize('requester', ['fern', 'lark'])
@pytest.mark.parametrize('busy', [False, True])
async def test_assistant_turn_marks_pending_only_for_its_owner(monkeypatch, requester, busy):
    from open_webui.models.soev_pending_sync import PendingSyncs
    from open_webui.utils import agent_v2

    monkeypatch.setattr(acting, 'requester_ref', lambda: f'owui:user:{requester}')
    monkeypatch.setattr(agent_v2.Models, 'get_model_by_id', AsyncMock(return_value=saved_model()))
    mark = AsyncMock()
    monkeypatch.setattr(PendingSyncs, 'mark', mark)
    file = SimpleNamespace(
        meta={'status': 'processing', **({'soev_job': {'collection_key': 'chat-files'}} if busy else {})}
    )
    assert (
        await agent_v2._assistant_file_reason('owui-assistant-fable/helper', file, 'fable/helper', None) == 'processing'
    )
    if requester == 'fern':
        mark.assert_awaited_once_with('fern', 'assistant_files', 'fable/helper')
    else:
        mark.assert_not_awaited()
