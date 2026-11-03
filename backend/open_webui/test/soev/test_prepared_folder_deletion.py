"""Folder deletion uses owned prepared threads rather than local chat content."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException
from open_webui.routers import folders


@pytest.mark.asyncio
@pytest.mark.parametrize('delete_contents', [False, True])
async def test_folder_delete_uses_authorized_threads_and_never_local_content(monkeypatch, delete_contents):
    user = SimpleNamespace(id='fictional-fern', role='admin')
    folder = SimpleNamespace(id='fictional-folder', user_id=user.id)
    record = SimpleNamespace(id='fictional-chat', folder_id=folder.id)
    service = SimpleNamespace(records=AsyncMock(return_value=[record]), delete=AsyncMock(), organize=AsyncMock())
    monkeypatch.setattr(folders, 'thread_paths', lambda request: service)
    check = AsyncMock()
    monkeypatch.setattr(folders.folder_sharing, 'check_delete', check)

    @asynccontextmanager
    async def changing(*args):
        assert check.await_count == 1
        yield

    monkeypatch.setattr(folders.folder_sharing, 'changing', changing)
    monkeypatch.setattr(folders.Folders, 'delete_folder_by_id_and_user_id', AsyncMock(return_value=[folder.id]))
    monkeypatch.setattr(folders.AccessGrants, 'revoke_all_access', AsyncMock())
    monkeypatch.setattr(folders.Automations, 'clear_folder_ids', AsyncMock())
    monkeypatch.setattr(folders, 'publish_event', AsyncMock())
    local = Mock(side_effect=AssertionError('Local content was accessed'))
    monkeypatch.setattr(folders.Chats, 'count_chats_by_folder_ids_and_user_id', local)
    assert await folders.delete_prepared_folder(None, user, folder, [folder.id], delete_contents, None)
    if delete_contents:
        service.delete.assert_awaited_once_with(user.id, record.id)
        service.organize.assert_not_called()
    else:
        service.organize.assert_awaited_once_with(user.id, record.id, {'folder_id': None})
        service.delete.assert_not_called()
    local.assert_not_called()


@pytest.mark.asyncio
async def test_folder_delete_never_borrows_another_owners_threads(monkeypatch):
    read = Mock(side_effect=AssertionError('Foreign thread access'))
    monkeypatch.setattr(folders, 'thread_paths', read)
    with pytest.raises(HTTPException) as caught:
        await folders.delete_prepared_folder(
            None,
            SimpleNamespace(id='fictional-elm', role='admin'),
            SimpleNamespace(id='fictional-folder', user_id='fictional-fern'),
            ['fictional-folder'],
            True,
            None,
        )
    assert caught.value.status_code == 403
    read.assert_not_called()
