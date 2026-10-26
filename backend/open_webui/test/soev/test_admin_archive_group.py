"""Local administrator roles do not author platform archive readership."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_admin_role_changes_never_publish_groups(role_store, monkeypatch):
    store = role_store
    client = SimpleNamespace(send=AsyncMock())
    monkeypatch.setattr(store.identity, 'build_client', lambda: client)
    await store.users.update_user_role_by_id('fern', 'admin', db=store.db)
    await store.users.update_user_role_by_id('fern', 'user', db=store.db)
    assert (await store.users.get_user_by_id('fern', db=store.db)).role == 'user'
    assert all('/groups' not in call.args[1] for call in client.send.await_args_list)


@pytest.mark.parametrize('ref', ['', 'owui:group:admins-archive', 'platform:group:'])
def test_archive_requires_seeded_platform_ref(identity_config, monkeypatch, ref):
    from open_webui.soev.archive import archive_group_ref

    identity, _ = identity_config
    monkeypatch.setattr(identity.config, 'SOEV_ARCHIVE_READERS_GROUP_REF', ref)
    with pytest.raises(ValueError):
        archive_group_ref()
