"""Archive readership follows the current admin role."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_admin_demotion_narrows_before_role_commit(role_store, monkeypatch):
    from open_webui.soev import group_sync

    store = role_store
    monkeypatch.setattr(store.identity, 'build_client', lambda: SimpleNamespace(send=AsyncMock()))
    await store.users.update_user_role_by_id('fern', 'admin', db=store.db)
    narrow = AsyncMock(side_effect=store.identity.SoevApiError(503, 'offline', 'Offline'))
    monkeypatch.setattr(group_sync, 'narrow', narrow)
    with pytest.raises(store.identity.SoevApiError):
        await store.users.update_user_role_by_id('fern', 'user', db=store.db)
    assert (await store.users.get_user_by_id('fern', db=store.db)).role == 'admin'
    narrow.assert_awaited_once_with('admins-archive', [])


@pytest.mark.asyncio
async def test_admin_promotion_retries_archive_group_from_current_roles(role_store, monkeypatch):
    from open_webui.soev import group_sync

    store = role_store
    monkeypatch.setattr(store.identity, 'build_client', lambda: SimpleNamespace(send=AsyncMock()))
    monkeypatch.setattr(group_sync, '_pending', {})
    push = AsyncMock(side_effect=[store.identity.SoevApiError(503, 'offline', 'Offline'), None])
    monkeypatch.setattr(group_sync, 'push', push)
    await store.users.update_user_role_by_id('fern', 'admin', db=store.db)
    assert 'admins-archive' in group_sync.pending_groups()
    table = SimpleNamespace()
    await group_sync.retry_pending(table, now=float('inf'), db=store.db)
    assert push.await_args.args == ('admins-archive', ['fern'])
    assert not group_sync.pending_groups()


@pytest.mark.asyncio
async def test_reconcile_always_publishes_admin_archive_group(role_store, monkeypatch):
    from open_webui.soev import group_sync

    store = role_store
    client = SimpleNamespace(send=AsyncMock(), get=AsyncMock(return_value={'groups': [], 'next_cursor': None}))
    monkeypatch.setattr(store.identity, 'build_client', lambda: client)
    await store.users.update_user_role_by_id('fern', 'admin', db=store.db)
    client.send.reset_mock()
    await group_sync.reconcile(SimpleNamespace(get_all_groups=AsyncMock(return_value=[])), db=store.db)
    assert any(
        'owui%3Agroup%3Aadmins-archive' in call.args[1] and call.args[2] == {'members': ['owui:user:fern']}
        for call in client.send.await_args_list
    )
