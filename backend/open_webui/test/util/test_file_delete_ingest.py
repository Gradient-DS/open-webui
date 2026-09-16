"""File deletion cancels ingest before removing collection membership and bytes."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from open_webui.routers import files as router


@pytest.mark.asyncio
@pytest.mark.parametrize('job', [None, {'job_id': 'job-1'}])
async def test_delete_cancels_ingest_before_removing_memberships_row_and_bytes(monkeypatch, job):
    """Delete preserves cleanup order and never deletes vectors directly."""
    file = SimpleNamespace(
        id='file-1', user_id='alice', filename='report.pdf', path='/stored/file', meta={'soev_job': job}
    )
    events = []

    async def cancel(row):
        assert row is file
        events.append('cancel')

    async def memberships(file_id, *, db):
        events.append('memberships')
        return [SimpleNamespace(id='kb-1'), SimpleNamespace(id='kb-2')]

    async def remove(knowledge_id, file_id, *, db):
        events.append(knowledge_id)

    async def delete(file_id, *, db):
        events.append('row')
        return True

    monkeypatch.setattr(router.Files, 'get_file_by_id', AsyncMock(return_value=file))
    monkeypatch.setattr(router.ingest, 'cancel', AsyncMock(side_effect=cancel))
    monkeypatch.setattr(router.Knowledges, 'get_knowledges_by_file_id', memberships)
    monkeypatch.setattr(router.Knowledges, 'remove_file_from_knowledge_by_id', remove)
    monkeypatch.setattr(router.Files, 'delete_file_by_id', delete)
    storage_delete = Mock(side_effect=lambda path: events.append('bytes'))
    monkeypatch.setattr(router.Storage, 'delete_file', storage_delete)
    vectors = AsyncMock()
    monkeypatch.setattr(router.ASYNC_VECTOR_DB_CLIENT, 'delete', vectors)
    monkeypatch.setattr(router, 'publish_event', AsyncMock())

    result = await router.delete_file_by_id(Mock(), file.id, user=SimpleNamespace(id='alice'), db=Mock())

    assert result == {'message': 'File deleted successfully'}
    assert events == (['cancel'] if job else []) + ['memberships', 'kb-1', 'kb-2', 'row', 'bytes']
    storage_delete.assert_called_once_with(file.path)
    vectors.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_stops_before_cleanup_when_cancellation_fails(monkeypatch):
    """A cancellation failure retains the file and its collection memberships."""
    file = SimpleNamespace(id='file-1', user_id='alice', meta={'soev_job': {'job_id': 'job-1'}})
    monkeypatch.setattr(router.Files, 'get_file_by_id', AsyncMock(return_value=file))
    monkeypatch.setattr(router.ingest, 'cancel', AsyncMock(side_effect=RuntimeError('cancel failed')))
    memberships = AsyncMock()
    delete = AsyncMock()
    monkeypatch.setattr(router.Knowledges, 'get_knowledges_by_file_id', memberships)
    monkeypatch.setattr(router.Files, 'delete_file_by_id', delete)

    with pytest.raises(RuntimeError, match='cancel failed'):
        await router.delete_file_by_id(Mock(), file.id, user=SimpleNamespace(id='alice'), db=Mock())

    memberships.assert_not_awaited()
    delete.assert_not_awaited()
