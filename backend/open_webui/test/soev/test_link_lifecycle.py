"""Role transitions preserve the product link before and after local commits."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest


async def empty_cloud_pages(path, *, as_user):
    for row in ():
        yield row


@pytest.mark.asyncio
async def test_pending_user_cannot_link_even_if_cached(identity_config, monkeypatch):
    identity, _ = identity_config
    monkeypatch.setattr(identity, 'user_of', AsyncMock(return_value=SimpleNamespace(role='pending')))
    identity._linked_refs.add('owui:user:fern')
    client = SimpleNamespace(send=AsyncMock(), pages=empty_cloud_pages)
    with pytest.raises(identity.SoevApiError) as error:
        await identity.ensure_link('owui:user:fern', client)
    assert error.value.status == 403
    client.send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('method', ['role', 'update'])
async def test_approval_never_mints_a_legacy_link(role_store, monkeypatch, method):
    store = role_store
    signer = AsyncMock(side_effect=AssertionError('Approval must not mint a subject'))
    monkeypatch.setattr(store.identity, 'mint_assertion', signer)
    if method == 'role':
        await store.users.update_user_role_by_id('fern', 'user', db=store.db)
    else:
        await store.users.update_user_by_id('fern', {'role': 'user'}, db=store.db)
    assert (await store.users.get_user_by_id('fern', db=store.db)).role == 'user'
    signer.assert_not_called()


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
async def test_unlink_contract_and_cache_invalidation(identity_config, identity_http):
    identity, _ = identity_config
    requests, responses = identity_http
    responses.extend([httpx.Response(204), httpx.Response(204)])
    identity._linked_refs.add('owui:user:fern')
    for _ in range(2):
        await identity.unlink('owui:user:fern', identity.build_client())
    assert 'owui:user:fern' not in identity._linked_refs
    for request in requests:
        assert request.method == 'DELETE'
        assert request.url.path == '/v1/identity/links/' + identity.platform_user_id('owui:user:fern')
        assert request.url.params['source'] == 'owui'
        assert request.headers['Idempotency-Key']
        assert 'X-Soev-Subject' not in request.headers


@pytest.mark.asyncio
@pytest.mark.parametrize('role', ['user', 'admin'])
async def test_approval_never_links_and_suspension_never_unlinks(role_store, monkeypatch, role):
    store = role_store
    client = SimpleNamespace(send=AsyncMock(), pages=empty_cloud_pages)
    client.as_service = lambda: client
    monkeypatch.setattr(store.identity, 'build_client', lambda: client)
    from open_webui.soev import group_sync

    monkeypatch.setattr(group_sync, 'build_service_client', lambda: client)
    await store.users.update_user_role_by_id('fern', role, db=store.db)
    assert all('/v1/identity/' not in c.args[1] for c in client.send.await_args_list)
    client.send.reset_mock()
    with pytest.raises(store.identity.SoevApiError) as caught:
        await store.users.update_user_role_by_id('fern', 'pending', db=store.db)
    assert caught.value.constraint == 'suspension:console'
    client.send.assert_not_awaited()
    assert (await store.users.get_user_by_id('fern', db=store.db)).role == role
    assert 'owui:user:fern' not in store.identity._linked_refs


@pytest.mark.asyncio
async def test_group_reconcile_does_not_publish_unlink_or_retry_identities(
    role_store, monkeypatch, platform_capabilities
):
    from open_webui.soev import group_sync

    store = role_store
    client = SimpleNamespace(send=AsyncMock(), get=AsyncMock(return_value={'groups': [], 'next_cursor': None}))
    client.as_service = lambda: client
    monkeypatch.setattr(store.identity, 'build_client', lambda: client)
    monkeypatch.setattr(group_sync, 'build_service_client', lambda: client)
    store.identity._linked_refs.add('owui:user:gone')
    await store.users.insert_new_user('lark', 'Lark', 'lark@example.invalid', role='user', db=store.db)
    table = SimpleNamespace(get_all_groups=AsyncMock(return_value=[]))
    await group_sync.reconcile(table, db=store.db)
    assert all('/v1/identity/' not in call.args[1] for call in client.send.await_args_list)
    assert 'owui:user:gone' in store.identity._linked_refs
    await group_sync.retry_pending(table, now=float('inf'), db=store.db)


@pytest.mark.asyncio
async def test_relink_after_unlink_uses_a_new_operation(identity_config, identity_http):
    identity, _ = identity_config
    requests, responses = identity_http
    responses.extend([httpx.Response(204)] * 3)
    await identity.ensure_link('owui:user:fern', identity.build_client())
    await identity.unlink('owui:user:fern', identity.build_client())
    await identity.ensure_link('owui:user:fern', identity.build_client())
    assert requests[0].headers['Idempotency-Key'] != requests[2].headers['Idempotency-Key']
