"""TopdeskSyncWorker — classification, subtree dedup, document build, metadata.

Runs against the committed fixtures with a mocked TopdeskClient (zero network)
and the model layer (Files/Knowledges) patched. Uses ``asyncio.run`` (no
pytest-asyncio dependency), matching ``test_topdesk_client.py``.

The worker is built with ``__new__`` + manual attribute assignment so the
heavyweight ``BaseSyncWorker.__init__`` (which constructs a PipelineClient and
wants an ``app``) is skipped — only the attributes the methods under test touch
are set.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from open_webui.services.topdesk.sync_worker import TopdeskSyncWorker

_FIXTURES = Path(__file__).parent / 'fixtures'


def _fixture(name: str) -> dict:
    return json.loads((_FIXTURES / name).read_text())


def _knowledge_item(name: str) -> dict:
    """Return the ``knowledgeItem`` node from a single-item fixture."""
    return _fixture(name)['data']['knowledgeItem']


def _run(coro):
    return asyncio.run(coro)


def _make_worker(client=None) -> TopdeskSyncWorker:
    """Build a worker without running BaseSyncWorker.__init__."""
    worker = TopdeskSyncWorker.__new__(TopdeskSyncWorker)
    worker.knowledge_id = 'kb-test'
    worker.user_id = 'user-test'
    worker.sources = []
    worker._service_client = client
    worker._seen_item_ids = set()
    worker._is_shared_kb = True
    worker._shared_kb_init_done = True
    return worker


def _stub_client(**methods) -> SimpleNamespace:
    """A SimpleNamespace mock client with AsyncMock methods."""
    return SimpleNamespace(
        **{k: AsyncMock(side_effect=v) if callable(v) else AsyncMock(return_value=v) for k, v in methods.items()}
    )


# ---------------------------------------------------------------------
# Abstract-member surface
# ---------------------------------------------------------------------


def test_worker_properties():
    worker = _make_worker()
    assert worker.meta_key == 'topdesk_sync'
    assert worker.file_id_prefix == 'topdesk-'
    assert worker.event_prefix == 'topdesk'
    assert worker.provider_slug == 'topdesk'
    assert worker.internal_request_path == '/internal/topdesk-sync'
    assert worker.source_clear_delta_keys == ['item_map', 'last_synced_modified']


def test_max_files_config_reads_persistent_config():
    worker = _make_worker()
    with patch('open_webui.services.topdesk.sync_worker.TOPDESK_MAX_ITEMS_PER_SYNC', SimpleNamespace(value=500)):
        assert worker.max_files_config == 500
    with patch('open_webui.services.topdesk.sync_worker.TOPDESK_MAX_ITEMS_PER_SYNC', SimpleNamespace(value=0)):
        # 0 → None (no per-sync cap; KB-wide safety net applies).
        assert worker.max_files_config is None


# ---------------------------------------------------------------------
# _get_cloud_hash — modificationDate
# ---------------------------------------------------------------------


def test_get_cloud_hash_returns_modification_date():
    worker = _make_worker()
    fi = {'item': {'id': 'x', 'modificationDate': '2026-05-21T14:30:00Z'}}
    assert worker._get_cloud_hash(fi) == '2026-05-21T14:30:00Z'


def test_get_cloud_hash_none_when_missing():
    worker = _make_worker()
    assert worker._get_cloud_hash({'item': {'id': 'x'}}) is None


# ---------------------------------------------------------------------
# Classification via _collect_folder_files (changed / unchanged / deleted)
# ---------------------------------------------------------------------


def test_collect_folder_changed_modification_date_queues_update():
    """An item whose modificationDate differs from the stored item_map is queued."""
    item = _knowledge_item('item_with_content.json')  # modificationDate 2026-05-21T14:30:00Z
    client = _stub_client()
    worker = _make_worker(client)
    # Whole-item subtree source: enumerate returns just this published item.
    source = {
        'type': 'folder',
        'item_id': item['id'],
        'include_descendants': False,
        'item_map': {item['id']: '2026-01-01T00:00:00Z'},  # stale → changed
    }
    with (
        patch.object(worker, '_enumerate_source_items', new=AsyncMock(return_value=[item])),
        patch('open_webui.services.topdesk.sync_worker.Files.get_file_by_id', new=AsyncMock(return_value=None)),
    ):
        files, deleted = _run(worker._collect_folder_files(source))

    assert deleted == 0
    assert [f['item_id'] for f in files] == [item['id']]
    # item_map updated to the fresh modificationDate.
    assert source['item_map'][item['id']] == '2026-05-21T14:30:00Z'


def test_collect_folder_unchanged_completed_skips():
    """Same modificationDate + a completed File row → skipped (not re-queued)."""
    item = _knowledge_item('item_with_content.json')
    worker = _make_worker(_stub_client())
    source = {
        'type': 'folder',
        'item_id': item['id'],
        'include_descendants': False,
        'item_map': {item['id']: item['modificationDate']},  # matches → unchanged
    }
    completed = SimpleNamespace(data={'status': 'completed'}, meta={})
    with (
        patch.object(worker, '_enumerate_source_items', new=AsyncMock(return_value=[item])),
        patch('open_webui.services.topdesk.sync_worker.Files.get_file_by_id', new=AsyncMock(return_value=completed)),
    ):
        files, deleted = _run(worker._collect_folder_files(source))

    assert files == []
    assert deleted == 0


def test_collect_folder_unchanged_but_incomplete_re_syncs():
    """Same modificationDate but the File row is missing/incomplete → re-queued."""
    item = _knowledge_item('item_with_content.json')
    worker = _make_worker(_stub_client())
    source = {
        'type': 'folder',
        'item_id': item['id'],
        'include_descendants': False,
        'item_map': {item['id']: item['modificationDate']},
    }
    with (
        patch.object(worker, '_enumerate_source_items', new=AsyncMock(return_value=[item])),
        patch('open_webui.services.topdesk.sync_worker.Files.get_file_by_id', new=AsyncMock(return_value=None)),
    ):
        files, _deleted = _run(worker._collect_folder_files(source))

    assert [f['item_id'] for f in files] == [item['id']]


def test_collect_folder_missing_item_is_deleted_by_set_difference():
    """An item in the old item_map but absent from the fresh enumeration is deleted."""
    item = _knowledge_item('item_with_content.json')
    worker = _make_worker(_stub_client())
    source = {
        'type': 'folder',
        'item_id': '__all__',
        'include_descendants': True,
        # 'gone' was tracked last run; this run only 'item' is enumerated.
        'item_map': {item['id']: item['modificationDate'], 'gone-id': '2026-01-01T00:00:00Z'},
    }
    handled: list = []

    async def _fake_delete(it):
        handled.append(it['id'])

    completed = SimpleNamespace(data={'status': 'completed'}, meta={})
    with (
        patch.object(worker, '_enumerate_source_items', new=AsyncMock(return_value=[item])),
        patch.object(worker, '_handle_deleted_item', new=AsyncMock(side_effect=_fake_delete)),
        patch('open_webui.services.topdesk.sync_worker.Files.get_file_by_id', new=AsyncMock(return_value=completed)),
    ):
        _files, deleted = _run(worker._collect_folder_files(source))

    assert deleted == 1
    assert handled == ['gone-id']
    assert 'gone-id' not in source['item_map']


# ---------------------------------------------------------------------
# Subtree dedup across overlapping sources
# ---------------------------------------------------------------------


def test_subtree_dedup_processes_item_once_across_two_sources():
    """An item present under two selected subtrees is queued only once."""
    item = _knowledge_item('item_with_content.json')
    worker = _make_worker(_stub_client())
    src_a = {'type': 'folder', 'item_id': 'A', 'include_descendants': True, 'item_map': {}}
    src_b = {'type': 'folder', 'item_id': 'B', 'include_descendants': True, 'item_map': {}}

    with (
        patch.object(worker, '_enumerate_source_items', new=AsyncMock(return_value=[item])),
        patch('open_webui.services.topdesk.sync_worker.Files.get_file_by_id', new=AsyncMock(return_value=None)),
    ):
        files_a, _ = _run(worker._collect_folder_files(src_a))
        files_b, _ = _run(worker._collect_folder_files(src_b))

    assert [f['item_id'] for f in files_a] == [item['id']]
    # Second source sees the item already in _seen_item_ids → not re-queued.
    assert files_b == []
    # But its item_map still records the modificationDate for deletion tracking.
    assert src_b['item_map'][item['id']] == item['modificationDate']


def test_collect_single_file_skips_when_already_seen():
    item = _knowledge_item('item_with_content.json')
    client = _stub_client(get_knowledge_item=item)
    worker = _make_worker(client)
    worker._seen_item_ids.add(item['id'])
    source = {'type': 'file', 'item_id': item['id'], 'include_descendants': False}
    result = _run(worker._collect_single_file(source))
    assert result is None
    client.get_knowledge_item.assert_not_awaited()


# ---------------------------------------------------------------------
# Published-only filter
# ---------------------------------------------------------------------


def test_enumerate_subtree_filters_unpublished_root():
    """A non-published root item is excluded from enumeration."""
    draft = {**_knowledge_item('item_with_content.json'), 'status': 'DRAFT'}
    client = _stub_client(get_knowledge_item=draft, list_item_children=[])
    worker = _make_worker(client)
    source = {'type': 'folder', 'item_id': draft['id'], 'include_descendants': False}
    items = _run(worker._enumerate_source_items(source))
    assert items == []


def test_collect_single_file_skips_draft():
    draft = {**_knowledge_item('item_with_content.json'), 'status': 'DRAFT'}
    client = _stub_client(get_knowledge_item=draft)
    worker = _make_worker(client)
    source = {'type': 'file', 'item_id': draft['id'], 'include_descendants': False}
    assert _run(worker._collect_single_file(source)) is None


def test_walk_descendants_filters_unpublished_children():
    """Children walk includes only published children (item_children.json all PUBLISHED)."""
    children_node = _fixture('item_children.json')['data']['knowledgeItem']
    # First call returns the 3 published children; deeper calls return none.
    calls = {'n': 0}

    async def _children(_item_id):
        calls['n'] += 1
        if calls['n'] == 1:
            return children_node['children']
        return []

    client = _stub_client()
    client.list_item_children = AsyncMock(side_effect=_children)
    worker = _make_worker(client)
    out = _run(worker._walk_descendants('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'))
    assert sorted(c['number'] for c in out) == ['KI 0001', 'KI 0006', 'KI 0007']


# ---------------------------------------------------------------------
# Document build — front-matter + markdown + byte cap
# ---------------------------------------------------------------------


def test_download_file_content_builds_front_matter_and_markdown():
    item = _knowledge_item('item_with_content.json')
    client = _stub_client(get_knowledge_item=item)
    worker = _make_worker(client)
    file_info = {
        'item_id': item['id'],
        'title': item['title'],
        'web_url': 'https://t.topdesk.net/tas/public/ssp/content/detail/knowledgeitem?unid=' + item['id'],
    }
    content = _run(worker._download_file_content(file_info))
    text = content.decode('utf-8')

    # Front-matter fields.
    assert text.startswith('# How to reset your password')
    assert 'KI 0001' in text
    assert '_Keywords: password, reset, login, account_' in text
    assert '_Language: en_' in text
    assert '_Status: PUBLISHED_' in text
    assert 'knowledgeitem?unid=' + item['id'] in text
    assert '_Created: 2026-01-04T09:12:00Z_' in text
    assert '_Last modified: 2026-05-21T14:30:00Z_' in text
    # Markdown body rendered from the HTML content (ordered list + link + bold).
    assert '1. Go to the [login page](https://example-portal.invalid/login).' in text
    assert '**Forgot password**' in text
    # file_info enriched for _get_provider_file_meta.
    assert file_info['topdesk_keywords'] == ['password', 'reset', 'login', 'account']
    assert file_info['topdesk_parent_id'] == 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'


def test_download_file_content_byte_cap_enforced():
    item = _knowledge_item('item_with_content.json')
    # Content larger than a 1 MB cap → output must be truncated to <= max_bytes.
    big = {**item, 'content': '<p>' + ('A' * (2 * 1024 * 1024)) + '</p>'}
    client = _stub_client(get_knowledge_item=big)
    worker = _make_worker(client)
    file_info = {'item_id': item['id'], 'title': item['title'], 'web_url': ''}
    with patch('open_webui.services.topdesk.sync_worker.TOPDESK_MAX_ITEM_SIZE_MB', 1):
        content = _run(worker._download_file_content(file_info))
    assert len(content) <= 1 * 1024 * 1024


def test_download_file_content_raises_when_item_disappears():
    client = _stub_client(get_knowledge_item=None)
    worker = _make_worker(client)
    try:
        _run(worker._download_file_content({'item_id': 'gone', 'title': 'x', 'web_url': ''}))
        raised = False
    except RuntimeError:
        raised = True
    assert raised


# ---------------------------------------------------------------------
# _get_provider_file_meta — carries id/number/web_url/language/keywords
# ---------------------------------------------------------------------


def test_provider_file_meta_carries_topdesk_fields():
    worker = _make_worker()
    file_info = {
        'web_url': 'https://t.topdesk.net/tas/public/ssp/content/detail/knowledgeitem?unid=KI1',
        'title': 'How to reset your password',
        'topdesk_number': 'KI 0001',
        'topdesk_language': 'en',
        'topdesk_status': 'PUBLISHED',
        'topdesk_visibility': 'SELF_SERVICE_PORTAL',
        'topdesk_keywords': ['password', 'reset'],
        'topdesk_created_at': '2026-01-04T09:12:00Z',
        'topdesk_modified_at': '2026-05-21T14:30:00Z',
    }
    meta = worker._get_provider_file_meta(
        item_id='item-uuid-1',
        source_item_id='src-1',
        relative_path='How to reset your password',
        name='How to reset your password',
        content_type='text/markdown',
        size=0,
        file_info=file_info,
    )
    assert meta['content_type'] == 'text/markdown'
    assert meta['source'] == 'topdesk'
    assert meta['topdesk_item_id'] == 'item-uuid-1'
    assert meta['topdesk_number'] == 'KI 0001'
    assert meta['topdesk_url'].endswith('unid=KI1')
    assert meta['topdesk_language'] == 'en'
    assert meta['topdesk_keywords'] == ['password', 'reset']
    assert meta['topdesk_visibility'] == 'SELF_SERVICE_PORTAL'
    assert meta['source_item_id'] == 'src-1'


def test_build_item_url_ssp_format():
    worker = _make_worker()
    with patch(
        'open_webui.services.topdesk.sync_worker.get_service_site',
        return_value={'url': 'https://tenant.topdesk.net', 'cloud_id': 'tenant.topdesk.net', 'name': 'x'},
    ):
        url = worker._build_item_url('e2a64a28-c0b8-4df2-9029-241fbecfbf72')
    assert url == (
        'https://tenant.topdesk.net/tas/public/ssp/content/detail/knowledgeitem'
        '?unid=e2a64a28-c0b8-4df2-9029-241fbecfbf72'
    )


# ---------------------------------------------------------------------
# _sync_permissions — suspension lifecycle on credential validity
# ---------------------------------------------------------------------


def test_sync_permissions_suspends_on_auth_error():
    from open_webui.services.topdesk.topdesk_client import TopdeskAuthError

    client = SimpleNamespace(probe=AsyncMock(side_effect=TopdeskAuthError('401')))
    worker = _make_worker(client)
    kb = SimpleNamespace(meta={'topdesk_sync': {}})
    update_meta = AsyncMock()
    with (
        patch('open_webui.services.topdesk.sync_worker.service_auth_configured', return_value=True),
        patch('open_webui.services.topdesk.sync_worker.Knowledges.get_knowledge_by_id', new=AsyncMock(return_value=kb)),
        patch('open_webui.services.topdesk.sync_worker.Knowledges.update_knowledge_meta_by_id', new=update_meta),
        patch.object(worker, '_update_sync_status', new=AsyncMock()),
    ):
        _run(worker._sync_permissions())

    update_meta.assert_awaited()
    _kb_id, meta_arg = update_meta.await_args.args
    assert meta_arg['topdesk_sync']['suspended_at'] is not None
    assert meta_arg['topdesk_sync']['suspended_reason'] == 'service_credential_invalid'


def test_sync_permissions_unsuspends_on_recovered_access():
    client = SimpleNamespace(probe=AsyncMock(return_value={'ok': True}))
    worker = _make_worker(client)
    kb = SimpleNamespace(meta={'topdesk_sync': {'suspended_at': 123, 'suspended_reason': 'x'}})
    update_meta = AsyncMock()
    with (
        patch('open_webui.services.topdesk.sync_worker.service_auth_configured', return_value=True),
        patch('open_webui.services.topdesk.sync_worker.Knowledges.get_knowledge_by_id', new=AsyncMock(return_value=kb)),
        patch('open_webui.services.topdesk.sync_worker.Knowledges.update_knowledge_meta_by_id', new=update_meta),
    ):
        _run(worker._sync_permissions())

    update_meta.assert_awaited()
    _kb_id, meta_arg = update_meta.await_args.args
    assert 'suspended_at' not in meta_arg['topdesk_sync']


def test_sync_permissions_transient_error_leaves_state_untouched():
    client = SimpleNamespace(probe=AsyncMock(side_effect=ConnectionError('down')))
    worker = _make_worker(client)
    update_meta = AsyncMock()
    with (
        patch('open_webui.services.topdesk.sync_worker.service_auth_configured', return_value=True),
        patch('open_webui.services.topdesk.sync_worker.Knowledges.update_knowledge_meta_by_id', new=update_meta),
    ):
        _run(worker._sync_permissions())
    # Transient error → no suspension state change.
    update_meta.assert_not_awaited()


def test_sync_permissions_suspends_when_credential_missing():
    worker = _make_worker()
    kb = SimpleNamespace(meta={'topdesk_sync': {}})
    update_meta = AsyncMock()
    with (
        patch('open_webui.services.topdesk.sync_worker.service_auth_configured', return_value=False),
        patch('open_webui.services.topdesk.sync_worker.Knowledges.get_knowledge_by_id', new=AsyncMock(return_value=kb)),
        patch('open_webui.services.topdesk.sync_worker.Knowledges.update_knowledge_meta_by_id', new=update_meta),
        patch.object(worker, '_update_sync_status', new=AsyncMock()),
    ):
        _run(worker._sync_permissions())
    update_meta.assert_awaited()
    _kb_id, meta_arg = update_meta.await_args.args
    assert meta_arg['topdesk_sync']['suspended_reason'] == 'service_credential_missing'


# ---------------------------------------------------------------------
# _resolve_shared_kb_sources — selection → sources, delta carry-over, drop-keep
# ---------------------------------------------------------------------


def test_resolve_shared_kb_sources_builds_folder_and_file_sources():
    worker = _make_worker()
    worker.sources = []
    kb = SimpleNamespace(
        meta={
            'topdesk_sync': {
                'shared': True,
                'items': [
                    {'item_id': 'sub-1', 'name': 'Subtree', 'include_descendants': True},
                    {'item_id': 'leaf-1', 'name': 'Leaf', 'include_descendants': False},
                ],
            }
        }
    )
    with (
        patch('open_webui.services.topdesk.sync_worker.Knowledges.get_knowledge_by_id', new=AsyncMock(return_value=kb)),
        patch(
            'open_webui.services.topdesk.sync_worker.get_service_site',
            return_value={'url': 'https://t.topdesk.net', 'cloud_id': 't', 'name': 't'},
        ),
    ):
        _run(worker._resolve_shared_kb_sources())

    by_id = {s['item_id']: s for s in worker.sources}
    assert by_id['sub-1']['type'] == 'folder'
    assert by_id['leaf-1']['type'] == 'file'
    assert by_id['sub-1']['site_url'] == 'https://t.topdesk.net'


def test_resolve_shared_kb_sources_carries_delta_and_keeps_dropped():
    worker = _make_worker()
    # Previously-persisted sources carry delta state; 'old-leaf' is no longer selected.
    worker.sources = [
        {'type': 'folder', 'item_id': 'sub-1', 'item_map': {'x': 't1'}, 'last_synced_modified': 't1'},
        {'type': 'file', 'item_id': 'old-leaf', 'item_map': {}},
    ]
    kb = SimpleNamespace(meta={'topdesk_sync': {'shared': True, 'items': [{'item_id': 'sub-1'}]}})
    with (
        patch('open_webui.services.topdesk.sync_worker.Knowledges.get_knowledge_by_id', new=AsyncMock(return_value=kb)),
        patch('open_webui.services.topdesk.sync_worker.get_service_site', return_value=None),
    ):
        _run(worker._resolve_shared_kb_sources())

    by_id = {s['item_id']: s for s in worker.sources}
    # Delta state carried over for the still-selected source.
    assert by_id['sub-1']['item_map'] == {'x': 't1'}
    # Dropped source kept one extra run for revoked-source cleanup.
    assert 'old-leaf' in by_id


# ---------------------------------------------------------------------
# _verify_source_access / _handle_revoked_source
# ---------------------------------------------------------------------


def test_verify_source_access_whole_kb_always_true():
    worker = _make_worker(_stub_client())
    assert _run(worker._verify_source_access({'item_id': '__all__'})) is True


def test_verify_source_access_missing_item_is_revoked():
    client = _stub_client(get_knowledge_item=None)
    worker = _make_worker(client)
    assert _run(worker._verify_source_access({'item_id': 'gone', 'name': 'g'})) is False


def test_handle_revoked_source_removes_matching_files():
    worker = _make_worker()
    f1 = SimpleNamespace(id='topdesk-1', meta={'source_item_id': 'src-1'})
    f2 = SimpleNamespace(id='topdesk-2', meta={'source_item_id': 'other'})
    with (
        patch(
            'open_webui.services.topdesk.sync_worker.Knowledges.get_files_by_id',
            new=AsyncMock(return_value=[f1, f2]),
        ),
        patch(
            'open_webui.services.topdesk.sync_worker.Knowledges.remove_file_from_knowledge_by_id',
            new=AsyncMock(),
        ),
        patch(
            'open_webui.services.topdesk.sync_worker.Knowledges.get_knowledge_files_by_file_id',
            new=AsyncMock(return_value=[]),
        ),
        patch('open_webui.services.topdesk.sync_worker.DeletionService.delete_file', new=AsyncMock()),
        patch('open_webui.retrieval.vector.async_client.ASYNC_VECTOR_DB_CLIENT.delete', new=AsyncMock()),
    ):
        removed = _run(worker._handle_revoked_source({'item_id': 'src-1', 'name': 's'}))
    # Only the file matching source_item_id='src-1' is removed.
    assert removed == 1
