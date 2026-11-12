"""Directory deletion preserves platform errors for the HTTP error handler."""

from unittest.mock import AsyncMock, Mock

import pytest

from open_webui.soev.client import SoevApiError
from open_webui.soev.knowledge_store import SoevKnowledgeTable


@pytest.mark.asyncio
@pytest.mark.parametrize('move_files', [True, False])
@pytest.mark.parametrize('stage', ['contents', 'delete'])
@pytest.mark.parametrize(
    'failure', [SoevApiError(403, 'scope_insufficient', 'private platform detail'), RuntimeError('delete failed')]
)
async def test_directory_deletion_propagates_failures(monkeypatch, move_files, stage, failure):
    projection = Mock()
    projection.directory_of.return_value = ('knowledge', ('directory',))
    monkeypatch.setattr(SoevKnowledgeTable, '_projection', property(lambda self: projection))
    store = SoevKnowledgeTable()
    store._folder_entries = AsyncMock(return_value=([], [{'source_id': 'file'}]))
    store.get_file_ids_in_directory_subtree = AsyncMock(return_value=['file'])
    store._move_document = AsyncMock()
    store.remove_file_from_knowledge_by_id = AsyncMock()
    store._delete_folder = AsyncMock()
    operation = (
        store._delete_folder
        if stage == 'delete'
        else store._move_document
        if move_files
        else store.remove_file_from_knowledge_by_id
    )
    operation.side_effect = failure
    with pytest.raises(type(failure)) as error:
        await store.delete_directory('directory-id', move_files_to_parent=move_files)
    assert error.value is failure
    if stage == 'contents':
        store._delete_folder.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure_status', [403, 503])
async def test_directory_route_uses_the_shared_platform_error_handler(monkeypatch, failure_status):
    from types import SimpleNamespace

    import httpx
    from fastapi import FastAPI

    from open_webui.internal.db import get_async_session
    from open_webui.main import soev_api_error_handler
    from open_webui.routers import knowledge
    from open_webui.services.deletion import service
    from open_webui.utils.auth import get_verified_user

    store = SoevKnowledgeTable()
    store.get_directory_by_id = AsyncMock(return_value=SimpleNamespace(knowledge_id='knowledge'))
    projection = Mock()
    projection.directory_of.return_value = ('knowledge', ('directory',))
    monkeypatch.setattr(SoevKnowledgeTable, '_projection', property(lambda self: projection))
    store._folder_entries = AsyncMock(return_value=([], []))
    store._delete_folder = AsyncMock(side_effect=SoevApiError(failure_status, 'scope_insufficient', 'private detail'))
    monkeypatch.setattr(knowledge, 'Knowledges', store)
    monkeypatch.setattr(service, 'Knowledges', store)
    monkeypatch.setattr(knowledge, '_verify_knowledge_write_access', AsyncMock())
    publish = AsyncMock()
    monkeypatch.setattr(knowledge, 'publish_event', publish)
    app = FastAPI()
    app.include_router(knowledge.router, prefix='/api/v1/knowledge')
    app.add_exception_handler(SoevApiError, soev_api_error_handler)
    for dependency in knowledge.router.dependencies:
        app.dependency_overrides[dependency.dependency] = lambda: None
    app.dependency_overrides[get_verified_user] = lambda: SimpleNamespace(id='owner', role='user')
    app.dependency_overrides[get_async_session] = lambda: None
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://owui.invalid') as client:
        response = await client.delete('/api/v1/knowledge/knowledge/dirs/directory/delete')
    assert response.status_code == failure_status
    assert response.json()['detail'] == {
        'code': 'scope_insufficient',
        'constraint': None,
        'detail': 'The platform could not complete this request.',
    }
    assert 'private detail' not in response.text
    publish.assert_not_awaited()
