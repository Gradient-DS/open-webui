"""Provider-agnostic shared-KB lifecycle helpers (services.sync.shared_kb).

These pin the contract extracted out of the Confluence implementation so a
TOPdesk (or any future) provider can reuse it without re-deriving the
data-corruption-critical bits: the public-read grant is set directly (bypassing
the user knowledge router's non-local-type guards), an empty owner yields a
system-owned KB (``user_id=''``), and ``is_managed_shared_kb`` recognises a
shared KB under *any* provider's meta key — which is what the knowledge-router
deletion guard and the cleanup worker's hard-delete skip now rely on.

``Knowledges`` / ``AccessGrants`` are patched on the ``shared_kb`` module, so
these run without a database. They use ``asyncio.run`` (no pytest-asyncio),
matching the existing confluence test style.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest import mock

from open_webui.services.sync import shared_kb


def _kb(kb_id='kb-1', user_id='owner-1', meta=None, deleted_at=None):
    return SimpleNamespace(id=kb_id, user_id=user_id, meta=meta or {}, deleted_at=deleted_at)


# ── find_shared_kb ──────────────────────────────────────────────────────────


def test_find_shared_kb_returns_match_by_type_and_shared_flag():
    live = _kb('kb-live', meta={'confluence_sync': {'shared': True}})
    with mock.patch.object(
        shared_kb.Knowledges,
        'get_knowledge_bases_by_type',
        new=mock.AsyncMock(return_value=[live]),
    ) as get_by_type:
        result = asyncio.run(shared_kb.find_shared_kb('confluence', 'confluence_sync'))

    get_by_type.assert_awaited_once_with('confluence')
    assert result is live


def test_find_shared_kb_skips_soft_deleted():
    deleted = _kb('kb-del', meta={'confluence_sync': {'shared': True}}, deleted_at=123)
    with mock.patch.object(
        shared_kb.Knowledges,
        'get_knowledge_bases_by_type',
        new=mock.AsyncMock(return_value=[deleted]),
    ):
        result = asyncio.run(shared_kb.find_shared_kb('confluence', 'confluence_sync'))
    assert result is None


def test_find_shared_kb_returns_none_when_no_shared_flag():
    # A KB of the right type but not flagged shared (e.g. a per-user synced KB).
    plain = _kb('kb-plain', meta={'confluence_sync': {'sources': []}})
    with mock.patch.object(
        shared_kb.Knowledges,
        'get_knowledge_bases_by_type',
        new=mock.AsyncMock(return_value=[plain]),
    ):
        result = asyncio.run(shared_kb.find_shared_kb('confluence', 'confluence_sync'))
    assert result is None


# ── provision_shared_kb ─────────────────────────────────────────────────────


def test_provision_creates_kb_with_empty_grants_then_public_read_grant():
    """A brand-new shared KB is created with access_grants=[] (bypassing the
    user router) and then granted user:*:read directly via AccessGrants, with
    the shared flag + selection written under the given meta key."""
    created = _kb('kb-new', user_id='admin-1', meta=None)
    insert = mock.AsyncMock(return_value=created)
    update_meta = mock.AsyncMock()
    set_grants = mock.AsyncMock()

    with (
        mock.patch.object(
            shared_kb.Knowledges,
            'get_knowledge_bases_by_type',
            new=mock.AsyncMock(return_value=[]),
        ),
        mock.patch.object(shared_kb.Knowledges, 'insert_new_knowledge', new=insert),
        mock.patch.object(shared_kb.Knowledges, 'update_knowledge_meta_by_id', new=update_meta),
        mock.patch.object(shared_kb.AccessGrants, 'set_access_grants', new=set_grants),
    ):
        result = asyncio.run(
            shared_kb.provision_shared_kb(
                provider_type='topdesk',
                meta_key='topdesk_sync',
                name='TOPdesk',
                description='desc',
                owner_id='admin-1',
                selected_items=[{'item_id': 'KI-1'}],
                extra_meta={'auth_mode': 'oauth'},
            )
        )

    assert result is created

    # Created via the model, bypassing the user router, with NO grants inline.
    insert.assert_awaited_once()
    owner_arg, form_arg = insert.await_args.args
    assert owner_arg == 'admin-1'
    assert form_arg.type == 'topdesk'
    assert form_arg.access_grants == []

    # Meta written under the provider's meta key with the shared flag + items.
    update_meta.assert_awaited_once()
    kb_id_arg, meta_arg = update_meta.await_args.args
    assert kb_id_arg == 'kb-new'
    sync_info = meta_arg['topdesk_sync']
    assert sync_info['shared'] is True
    assert sync_info['auth_mode'] == 'oauth'
    assert sync_info['items'] == [{'item_id': 'KI-1'}]
    assert sync_info['sources'] == []
    assert sync_info['status'] == 'idle'

    # Public read grant set directly on the model (not via the user router).
    set_grants.assert_awaited_once_with(
        'knowledge',
        'kb-new',
        [{'principal_type': 'user', 'principal_id': '*', 'permission': 'read'}],
    )


def test_provision_empty_owner_is_system_owned():
    """An empty owner_id is passed straight through to insert_new_knowledge so
    the KB is system-owned (user_id='')."""
    created = _kb('kb-sys', user_id='', meta=None)
    insert = mock.AsyncMock(return_value=created)

    with (
        mock.patch.object(
            shared_kb.Knowledges,
            'get_knowledge_bases_by_type',
            new=mock.AsyncMock(return_value=[]),
        ),
        mock.patch.object(shared_kb.Knowledges, 'insert_new_knowledge', new=insert),
        mock.patch.object(shared_kb.Knowledges, 'update_knowledge_meta_by_id', new=mock.AsyncMock()),
        mock.patch.object(shared_kb.AccessGrants, 'set_access_grants', new=mock.AsyncMock()),
    ):
        asyncio.run(
            shared_kb.provision_shared_kb(
                provider_type='topdesk',
                meta_key='topdesk_sync',
                name='TOPdesk',
                description='desc',
                owner_id='',
                selected_items=[],
                extra_meta=None,
            )
        )

    owner_arg, _form = insert.await_args.args
    assert owner_arg == ''


def test_provision_honours_items_key_override_and_drops_internal_key():
    """``extra_meta['_items_key']`` selects the meta key the selection is stored
    under (Confluence uses 'spaces') and must not leak into the persisted meta."""
    created = _kb('kb-c', user_id='admin-1', meta=None)

    update_meta = mock.AsyncMock()
    with (
        mock.patch.object(
            shared_kb.Knowledges,
            'get_knowledge_bases_by_type',
            new=mock.AsyncMock(return_value=[]),
        ),
        mock.patch.object(shared_kb.Knowledges, 'insert_new_knowledge', new=mock.AsyncMock(return_value=created)),
        mock.patch.object(shared_kb.Knowledges, 'update_knowledge_meta_by_id', new=update_meta),
        mock.patch.object(shared_kb.AccessGrants, 'set_access_grants', new=mock.AsyncMock()),
    ):
        asyncio.run(
            shared_kb.provision_shared_kb(
                provider_type='confluence',
                meta_key='confluence_sync',
                name='Confluence',
                description='desc',
                owner_id='admin-1',
                selected_items=[{'id': 'S1'}],
                extra_meta={'auth_mode': 'basic', '_items_key': 'spaces'},
            )
        )

    sync_info = update_meta.await_args.args[1]['confluence_sync']
    assert sync_info['spaces'] == [{'id': 'S1'}]
    assert 'items' not in sync_info
    assert '_items_key' not in sync_info
    assert sync_info['auth_mode'] == 'basic'


def test_provision_updates_existing_kb_in_place_and_reassigns_owner():
    """When a shared KB already exists, provision updates its meta (shared flag,
    selection, extra_meta) and reassigns the owner if it changed — without
    re-inserting."""
    existing = _kb('kb-old', user_id='old-owner', meta={'confluence_sync': {'shared': True, 'sync_all_spaces': True}})

    update_user = mock.AsyncMock()
    update_meta = mock.AsyncMock()
    insert = mock.AsyncMock()

    with (
        mock.patch.object(
            shared_kb.Knowledges,
            'get_knowledge_bases_by_type',
            new=mock.AsyncMock(return_value=[existing]),
        ),
        mock.patch.object(shared_kb.Knowledges, 'insert_new_knowledge', new=insert),
        mock.patch.object(shared_kb.Knowledges, 'update_knowledge_user_id_by_id', new=update_user),
        mock.patch.object(shared_kb.Knowledges, 'update_knowledge_meta_by_id', new=update_meta),
        mock.patch.object(shared_kb.AccessGrants, 'set_access_grants', new=mock.AsyncMock()),
    ):
        asyncio.run(
            shared_kb.provision_shared_kb(
                provider_type='confluence',
                meta_key='confluence_sync',
                name='Confluence',
                description='desc',
                owner_id='new-owner',
                selected_items=[{'id': 'S2'}],
                extra_meta={'auth_mode': 'oauth', '_items_key': 'spaces'},
            )
        )

    insert.assert_not_awaited()
    update_user.assert_awaited_once_with('kb-old', 'new-owner')
    sync_info = update_meta.await_args.args[1]['confluence_sync']
    assert sync_info['shared'] is True
    assert sync_info['auth_mode'] == 'oauth'
    assert sync_info['spaces'] == [{'id': 'S2'}]
    # Legacy flag is dropped.
    assert 'sync_all_spaces' not in sync_info


# ── shared_kb_status ────────────────────────────────────────────────────────


def test_status_unprovisioned():
    with mock.patch.object(
        shared_kb.Knowledges,
        'get_knowledge_bases_by_type',
        new=mock.AsyncMock(return_value=[]),
    ):
        status = asyncio.run(shared_kb.shared_kb_status('topdesk', 'topdesk_sync'))
    assert status == {'provisioned': False, 'knowledge_id': None}


def test_status_provisioned_reports_meta_and_file_count():
    kb = _kb(
        'kb-1',
        user_id='owner-1',
        meta={
            'topdesk_sync': {
                'shared': True,
                'status': 'syncing',
                'last_sync_at': 1000,
                'last_result': 'ok',
                'suspended_at': None,
                'progress_current': 3,
                'progress_total': 10,
                'items': [{'item_id': 'KI-1'}],
            }
        },
    )
    with (
        mock.patch.object(
            shared_kb.Knowledges,
            'get_knowledge_bases_by_type',
            new=mock.AsyncMock(return_value=[kb]),
        ),
        mock.patch.object(
            shared_kb.Knowledges, 'get_files_by_id', new=mock.AsyncMock(return_value=[object(), object()])
        ),
    ):
        status = asyncio.run(shared_kb.shared_kb_status('topdesk', 'topdesk_sync'))

    assert status['provisioned'] is True
    assert status['knowledge_id'] == 'kb-1'
    assert status['owner_id'] == 'owner-1'
    assert status['status'] == 'syncing'
    assert status['last_sync_at'] == 1000
    assert status['last_result'] == 'ok'
    assert status['suspended_at'] is None
    assert status['file_count'] == 2
    assert status['progress_current'] == 3
    assert status['progress_total'] == 10
    assert status['items'] == [{'item_id': 'KI-1'}]


def test_status_items_key_override():
    kb = _kb('kb-1', meta={'confluence_sync': {'shared': True, 'spaces': [{'id': 'S1'}]}})
    with (
        mock.patch.object(
            shared_kb.Knowledges,
            'get_knowledge_bases_by_type',
            new=mock.AsyncMock(return_value=[kb]),
        ),
        mock.patch.object(shared_kb.Knowledges, 'get_files_by_id', new=mock.AsyncMock(return_value=[])),
    ):
        status = asyncio.run(shared_kb.shared_kb_status('confluence', 'confluence_sync', items_key='spaces'))
    assert status['spaces'] == [{'id': 'S1'}]
    assert 'items' not in status


# ── delete_shared_kb ────────────────────────────────────────────────────────


def test_delete_soft_deletes_and_returns_id():
    kb = _kb('kb-del', meta={'confluence_sync': {'shared': True}})
    soft_delete = mock.AsyncMock(return_value=True)
    with (
        mock.patch.object(
            shared_kb.Knowledges,
            'get_knowledge_bases_by_type',
            new=mock.AsyncMock(return_value=[kb]),
        ),
        mock.patch.object(shared_kb.Knowledges, 'soft_delete_by_id', new=soft_delete),
    ):
        result = asyncio.run(shared_kb.delete_shared_kb('confluence', 'confluence_sync'))

    soft_delete.assert_awaited_once_with('kb-del')
    assert result == 'kb-del'


def test_delete_returns_none_when_no_shared_kb():
    soft_delete = mock.AsyncMock()
    with (
        mock.patch.object(
            shared_kb.Knowledges,
            'get_knowledge_bases_by_type',
            new=mock.AsyncMock(return_value=[]),
        ),
        mock.patch.object(shared_kb.Knowledges, 'soft_delete_by_id', new=soft_delete),
    ):
        result = asyncio.run(shared_kb.delete_shared_kb('confluence', 'confluence_sync'))

    soft_delete.assert_not_awaited()
    assert result is None


# ── is_managed_shared_kb ────────────────────────────────────────────────────


def test_is_managed_true_for_confluence_shaped_shared_kb():
    kb = _kb(meta={'confluence_sync': {'shared': True}})
    assert shared_kb.is_managed_shared_kb(kb) is True


def test_is_managed_true_for_topdesk_shaped_shared_kb():
    kb = _kb(meta={'topdesk_sync': {'shared': True}})
    assert shared_kb.is_managed_shared_kb(kb) is True


def test_is_managed_false_for_plain_kb():
    assert shared_kb.is_managed_shared_kb(_kb(meta={})) is False
    assert shared_kb.is_managed_shared_kb(_kb(meta=None)) is False


def test_is_managed_false_for_per_user_synced_kb():
    # A per-user synced KB carries the meta key but is NOT flagged shared.
    kb = _kb(meta={'confluence_sync': {'sources': [{'item_id': 'x'}]}})
    assert shared_kb.is_managed_shared_kb(kb) is False
    kb2 = _kb(meta={'topdesk_sync': {'sources': [{'item_id': 'y'}]}})
    assert shared_kb.is_managed_shared_kb(kb2) is False


def test_is_managed_covers_all_meta_keys():
    # Regression guard: the set of recognised keys must include both providers
    # the knowledge.py guard + cleanup_worker skip rely on.
    assert set(shared_kb.SHARED_SYNC_META_KEYS) >= {'confluence_sync', 'topdesk_sync'}
