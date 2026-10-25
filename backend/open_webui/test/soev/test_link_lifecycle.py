"""Role transitions require no product links and refuse console-only suspension."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest


async def empty_cloud_pages(path, *, as_user):
    for row in ():
        yield row


@pytest.mark.asyncio
@pytest.mark.parametrize('method', ['role', 'update'])
async def test_approval_never_mints_a_legacy_link(role_store, monkeypatch, method):
    store = role_store
    if method == 'role':
        await store.users.update_user_role_by_id('fern', 'user', db=store.db)
    else:
        await store.users.update_user_by_id('fern', {'role': 'user'}, db=store.db)
    assert (await store.users.get_user_by_id('fern', db=store.db)).role == 'user'


@pytest.mark.asyncio
@pytest.mark.parametrize('method', ['role', 'update'])
async def test_suspension_refuses_before_remote_or_local_role_write(role_store, monkeypatch, method):
    store = role_store
    await store.db.execute(__import__('sqlalchemy').text("UPDATE user SET role='user'"))
    await store.db.commit()
    client = SimpleNamespace(send=AsyncMock(side_effect=httpx.ConnectError('offline')), pages=empty_cloud_pages)
    client.as_service = lambda: client
    monkeypatch.setattr(store.identity, 'build_client', lambda: client)
    with pytest.raises(store.identity.SoevApiError) as caught:
        if method == 'role':
            await store.users.update_user_role_by_id('fern', 'pending', db=store.db)
        else:
            await store.users.update_user_by_id('fern', {'role': 'pending'}, db=store.db)
    assert caught.value.constraint == 'suspension:console'
    client.send.assert_not_awaited()
    assert (await store.users.get_user_by_id('fern', db=store.db)).role == 'user'


@pytest.mark.asyncio
@pytest.mark.parametrize('before', ['user', 'admin', 'pending'])
@pytest.mark.parametrize('role', ['', 'disabled'])
@pytest.mark.parametrize('method', ['role', 'update'])
@pytest.mark.parametrize('configured', [False, True])
async def test_invalid_role_refuses_before_remote_or_local_write(
    role_store, monkeypatch, before, role, method, configured
):
    from open_webui.models.users import User

    store = role_store
    if not configured:
        monkeypatch.setattr(store.identity.config, 'SOEV_API_URL', '')
    target = await store.db.get(User, 'fern')
    target.role = before
    await store.db.commit()
    client = SimpleNamespace(send=AsyncMock(), pages=empty_cloud_pages)
    client.as_service = lambda: client
    monkeypatch.setattr(store.identity, 'build_client', lambda: client)
    with pytest.raises(store.identity.SoevApiError) as caught:
        if method == 'role':
            await store.users.update_user_role_by_id('fern', role, db=store.db)
        else:
            await store.users.update_user_by_id('fern', {'role': role, 'name': 'Changed'}, db=store.db)
    assert caught.value.status == (403 if configured and before in {'user', 'admin'} else 400)
    await store.db.refresh(target)
    assert (target.role, target.name) == (before, 'Fern')
    client.send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('role', ['user', 'admin'])
async def test_approval_never_links_and_suspension_never_unlinks(role_store, monkeypatch, role):
    store = role_store
    client = SimpleNamespace(send=AsyncMock(), pages=empty_cloud_pages)
    client.as_service = lambda: client
    monkeypatch.setattr(store.identity, 'build_client', lambda: client)
    await store.users.update_user_role_by_id('fern', role, db=store.db)
    assert all('/v1/identity/' not in c.args[1] for c in client.send.await_args_list)
    client.send.reset_mock()
    with pytest.raises(store.identity.SoevApiError) as caught:
        await store.users.update_user_role_by_id('fern', 'pending', db=store.db)
    assert caught.value.constraint == 'suspension:console'
    client.send.assert_not_awaited()
    assert (await store.users.get_user_by_id('fern', db=store.db)).role == role


@pytest.mark.asyncio
async def test_group_reconcile_does_not_publish_unlink_or_retry_identities(group_db, monkeypatch):
    from open_webui.soev import group_mirror

    client = SimpleNamespace(send=AsyncMock(), get=AsyncMock(return_value={'data': [], 'next_cursor': None}))
    monkeypatch.setattr(group_mirror, 'build_service_client', lambda: client)
    await group_mirror.reconcile(db=group_db)
    client.get.assert_awaited_once_with('/v1/groups', params={'limit': 200})
    client.send.assert_not_awaited()
