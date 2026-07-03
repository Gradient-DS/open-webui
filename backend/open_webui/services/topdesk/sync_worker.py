"""TOPdesk sync worker — pulls in-scope knowledge items into a shared KB.

Mirrors ``services/confluence/sync_worker.py`` with the OAuth machinery removed:
TOPdesk has a single service-account credential read from global config (see
``services/topdesk/auth.py``), so there is no per-user token, no cloud_id
multiplexing and no per-user mode. The worker only ever drives the single,
admin-provisioned shared KB.

Source taxonomy (mirrors Confluence pages/spaces):
- ``folder`` — a knowledge item plus all of its descendants (an admin tree-picker
  subtree selection, ``include_descendants=True``); also the degenerate
  "whole KB" case where the root selection is flat (v1 Intermax).
- ``file``   — a single knowledge item (a leaf the admin selected without its
  subtree).

Change detection uses ``modificationDate`` as the cloud hash, carried per folder
source in ``source['item_map']: {item_id -> modificationDate}``. Deletion is by
set-difference against the fresh enumeration (base_worker's
``_handle_deleted_item``). Overlapping subtrees are de-duplicated via
``_seen_item_ids``.

The whole rendered document is built in-pod (``use_shared_loader`` is forced
False by the provider — there is no loader-worker TOPdesk source client): fetch
the HTML ``content``, convert to Markdown off-thread, prepend a front-matter
block, byte-cap, and emit a synthetic ``text/markdown`` document.
"""

import asyncio
import logging
import re
import time
from typing import Any, Dict, List, Optional

from open_webui.config import (
    TOPDESK_MAX_ITEMS_PER_SYNC,
    TOPDESK_MAX_ITEM_SIZE_MB,
    TOPDESK_SYNC_SCOPE,
)
from open_webui.models.files import Files
from open_webui.models.knowledge import Knowledges
from open_webui.models.users import Users
from open_webui.services.deletion import DeletionService
from open_webui.services.sync.base_worker import BaseSyncWorker
from open_webui.services.sync.html_renderer import html_to_markdown
from open_webui.services.topdesk import mapping
from open_webui.services.topdesk.auth import build_client, get_service_site, service_auth_configured
from open_webui.services.topdesk.topdesk_client import (
    TopdeskAuthError,
    TopdeskClient,
)

log = logging.getLogger(__name__)

_FILE_ID_PREFIX = 'topdesk-'
_META_KEY = 'topdesk_sync'


def _sync_scope() -> str:
    """Resolve the configured sync scope ('ssp' | 'public' | 'all').

    Read at use-time (not cached) so an admin scope change via the Cloud Sync tab
    takes effect on the next sync without a restart.
    """
    return getattr(TOPDESK_SYNC_SCOPE, 'value', None) or 'ssp'


def _sanitise_filename(title: str, item_id: str) -> str:
    """Turn a knowledge-item title into a safe display filename.

    No extension — a knowledge item is a synthetic document, not a real file, and
    a bare title reads better in citation pills. ``content_type`` is pinned to
    ``text/markdown`` in ``_get_provider_file_meta`` regardless of the filename.
    """
    cleaned = re.sub(r'[\\/:*?"<>|\r\n\t]', '_', (title or '').strip())
    return cleaned[:120].rstrip(' .') or f'item-{item_id}'


def _should_sync(item: Dict[str, Any]) -> bool:
    """Whether a REST KnowledgeItem should be ingested, per TOPDESK_SYNC_SCOPE.

    Delegates to ``mapping.should_sync`` (archived always excluded; ssp/public/all
    gate on ``visibility``). Replaces the old status-enum publish check — ``status``
    is a customer searchlist with no guaranteed "PUBLISHED" value, so visibility is
    the structured signal.
    """
    return mapping.should_sync(item, _sync_scope())


def _build_front_matter(file_info: Dict[str, Any]) -> str:
    """Compose the Markdown front-matter block prepended to a knowledge item.

    Reads enrichment fields stamped onto ``file_info`` during collection /
    download (title, number, keywords, language, status, web URL, dates).
    Format mirrors Confluence's ``_build_front_matter``.
    """
    info = file_info or {}
    title = info.get('title') or 'TOPdesk knowledge item'
    number = info.get('topdesk_number') or ''
    keywords = info.get('topdesk_keywords') or []
    language = info.get('topdesk_language') or ''
    status = info.get('topdesk_status') or ''
    web_url = info.get('web_url') or ''
    created_at = info.get('topdesk_created_at') or ''
    modified_at = info.get('topdesk_modified_at') or ''

    lines = [
        f'# {title}',
        '',
        f'_TOPdesk knowledge item · {number}_' if number else '_TOPdesk knowledge item_',
    ]
    if keywords:
        lines.append(f'_Keywords: {", ".join(keywords)}_')
    if language:
        lines.append(f'_Language: {language}_')
    if status:
        lines.append(f'_Status: {status}_')
    if web_url:
        lines.append(f'_Source: {web_url}_')
    if created_at:
        lines.append(f'_Created: {created_at}_')
    if modified_at:
        lines.append(f'_Last modified: {modified_at}_')
    lines.append('')
    lines.append('')
    return '\n'.join(lines)


class TopdeskSyncWorker(BaseSyncWorker):
    """Worker to sync TOPdesk knowledge items into a shared Knowledge base."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # A single service client for the configured tenant — TOPdesk has no
        # per-source cloud_id, so one client serves every source.
        self._service_client: Optional[TopdeskClient] = None
        # Track item ids emitted this run so overlapping subtree sources don't
        # queue the same item twice → avoids a UniqueViolation on the file_id
        # primary key when two pipeline tasks race to INSERT the same row.
        # Item ids are tenant-unique UUIDs, so a flat set suffices (no cloud_id).
        self._seen_item_ids: set[str] = set()
        # Shared full-content KB state — resolved lazily in sync() because the
        # lookups are async.
        self._is_shared_kb = False
        self._shared_kb_init_done = False

    # ------------------------------------------------------------------
    # Abstract properties
    # ------------------------------------------------------------------

    @property
    def meta_key(self) -> str:
        return _META_KEY

    @property
    def file_id_prefix(self) -> str:
        return _FILE_ID_PREFIX

    @property
    def event_prefix(self) -> str:
        return 'topdesk'

    @property
    def provider_slug(self) -> str:
        return 'topdesk'

    @property
    def internal_request_path(self) -> str:
        return '/internal/topdesk-sync'

    @property
    def max_files_config(self) -> Optional[int]:
        # TOPDESK_MAX_ITEMS_PER_SYNC is a PersistentConfig (admin-editable).
        # 0 = "no limit" → return None so base_worker falls back to the KB-wide
        # KNOWLEDGE_MAX_FILE_COUNT safety net alone.
        return TOPDESK_MAX_ITEMS_PER_SYNC.value or None

    @property
    def source_clear_delta_keys(self) -> list[str]:
        return ['item_map', 'last_synced_modified']

    # ------------------------------------------------------------------
    # Client lifecycle
    # ------------------------------------------------------------------

    def _create_client(self):
        """Build the single service client for the configured TOPdesk tenant.

        Returns the client so base_worker can stash it on ``self._client``; the
        worker uses ``_client_handle`` internally so the tree-walk helpers don't
        depend on base_worker's attribute.
        """
        self._service_client = build_client()
        return self._service_client

    def _client_handle(self) -> TopdeskClient:
        if self._service_client is None:
            self._service_client = build_client()
        return self._service_client

    async def _close_client(self):
        client = self._service_client
        if client is not None:
            try:
                await client.close()
            except Exception as e:  # pragma: no cover
                log.warning('Error closing TOPdesk client: %s', e)
        self._service_client = None

    # ------------------------------------------------------------------
    # Shared full-content KB — resolve all selected items at sync time
    # ------------------------------------------------------------------

    async def _init_shared_kb_state(self) -> None:
        """Resolve shared-KB flag + system-admin owner once at sync start."""
        if self._shared_kb_init_done:
            return
        kb = await Knowledges.get_knowledge_by_id(self.knowledge_id)
        sync_info = (kb.meta or {}).get(self.meta_key, {}) if kb else {}
        self._is_shared_kb = bool(sync_info.get('shared'))

        # A system-owned shared KB stores user_id='' on the KB row so it stays
        # decoupled from any deletable user. The sync mechanics (process_file
        # access control, file ownership, progress events) still need a real
        # user, so resolve this run to the instance super-admin when the KB has
        # no valid owner.
        if self._is_shared_kb and (not self.user_id or not await Users.get_user_by_id(self.user_id)):
            admin = await Users.get_super_admin_user()
            if admin:
                self.user_id = admin.id
            else:
                log.warning(
                    'Shared TOPdesk KB %s has no valid owner and no admin user could be resolved for the sync run',
                    self.knowledge_id,
                )
        self._shared_kb_init_done = True

    async def sync(self) -> Dict[str, Any]:
        """Run a sync, resolving the source list first for the shared KB.

        The shared KB has no fixed source list — its sources are rebuilt on every
        run from the items an admin explicitly opted in via the Cloud Sync tab.
        Deselecting an item drops its files via base_worker's revoked-source
        handling.
        """
        await self._init_shared_kb_state()
        if self._is_shared_kb:
            await self._resolve_shared_kb_sources()
        return await super().sync()

    async def _resolve_shared_kb_sources(self) -> None:
        """Rebuild ``self.sources`` from the admin-selected knowledge items.

        Each opted-in item — a single item or an item subtree — becomes one
        source. Delta state (``item_map`` / ``last_sync_at`` /
        ``last_synced_modified``) is carried over from the previously persisted
        entry with the same ``item_id`` so steady-state syncs only re-process
        changed items. An item removed from the selection keeps its old source
        for one more run so base_worker's ``_verify_source_access`` →
        ``_handle_revoked_source`` flow removes its files and then drops it from
        the persisted set.
        """
        kb = await Knowledges.get_knowledge_by_id(self.knowledge_id)
        sync_info = (kb.meta or {}).get(self.meta_key, {}) if kb else {}
        selected_items = sync_info.get('items') or []

        # Index previously persisted sources by item_id so their delta state
        # survives the rebuild.
        old_by_id: Dict[str, Dict[str, Any]] = {}
        for src in self.sources:
            src_item_id = src.get('item_id')
            if src_item_id:
                old_by_id[str(src_item_id)] = src

        site = get_service_site()
        site_url = site['url'] if site else ''

        resolved: List[Dict[str, Any]] = []
        seen_keys: set[str] = set()

        for item in selected_items:
            item_id = str(item.get('item_id') or item.get('id') or '')
            if not item_id or item_id in seen_keys:
                continue
            seen_keys.add(item_id)

            old = old_by_id.get(item_id, {})
            name = item.get('name') or item.get('title') or item.get('number') or item_id
            include_descendants = bool(item.get('include_descendants', True))

            # base_worker dispatches on source['type']: 'folder' enumerates the
            # item + descendants, 'file' fetches one item. A leaf selected
            # without its subtree is the only file-like case.
            source_type = 'folder' if include_descendants else 'file'

            source: Dict[str, Any] = {
                'type': source_type,
                'item_id': item_id,
                'name': name,
                'site_url': site_url,
                'include_descendants': include_descendants,
            }
            if old.get('item_map'):
                source['item_map'] = old['item_map']
            if old.get('last_synced_modified'):
                source['last_synced_modified'] = old['last_synced_modified']
            if old.get('last_sync_at'):
                source['last_sync_at'] = old['last_sync_at']
            resolved.append(source)

        # Items dropped from the selection: keep their old source one more run so
        # revoked-source handling removes the orphaned files.
        for item_id, old in old_by_id.items():
            if item_id not in seen_keys:
                resolved.append(old)

        self.sources = resolved
        log.info(
            'TOPdesk shared sync resolved %d item source(s) for KB %s',
            len(resolved),
            self.knowledge_id,
        )

    # ------------------------------------------------------------------
    # Support checks
    # ------------------------------------------------------------------

    def _is_supported_file(self, item: Dict[str, Any]) -> bool:
        """All in-scope knowledge items are supported in v1.

        The scope/visibility filter is applied at enumeration time
        (``_collect_*`` via ``_should_sync``); by the time an item reaches here it
        is already includable, so this returns True.
        """
        return True

    # ------------------------------------------------------------------
    # Source → items resolution
    # ------------------------------------------------------------------

    async def _enumerate_source_items(self, source: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Return the in-scope knowledge items covered by a source.

        For a flat selection (the whole KB) ``item_id`` may be the special
        ``__all__`` token written by the picker; that lists every item. For a
        subtree, walk the item + descendants. Items outside ``TOPDESK_SYNC_SCOPE``
        (or archived) are filtered out via ``_should_sync``.
        """
        client = self._client_handle()
        item_id = source.get('item_id')

        if item_id in (None, '', '__all__', '*'):
            # Whole-KB flat selection (v1 Intermax). Filter to the configured scope.
            items = await client.iter_all_knowledge_items()
            return [i for i in items if _should_sync(i)]

        root = await client.get_knowledge_item(item_id)
        if not root:
            return []

        include_descendants = bool(source.get('include_descendants', True))
        collected: List[Dict[str, Any]] = []
        if _should_sync(root):
            collected.append(root)

        if include_descendants:
            collected.extend(await self._walk_descendants(item_id))

        return collected

    async def _walk_descendants(self, root_id: str) -> List[Dict[str, Any]]:
        """Breadth-first walk of an item's descendant tree (in-scope only).

        Bounded by ``_seen_item_ids``-independent local visited set so a
        cyclic/duplicated tree cannot loop forever; the cross-source
        ``_seen_item_ids`` dedupe still applies later in ``_collect_folder_files``.

        FAIL-SAFE ENUMERATION (matches Confluence's ``_list_pages_for_source``):
        a child-fetch error is NOT swallowed. If it were, a transient TOPdesk
        hiccup mid-walk would silently truncate the freshly-enumerated set, and
        ``_collect_folder_files``'s set-difference deletion would treat the
        still-present-but-unenumerated subtree as deleted — yanking File rows +
        vectors out of the KB until the next clean sync re-adds them. Letting the
        error propagate aborts the cycle in ``BaseSyncWorker.sync`` BEFORE any
        deletion runs (``ConnectionError`` → transient skip path; everything else
        → status='failed' + re-raise), so a partial enumeration can never drive a
        deletion. A genuinely empty subtree is fine: ``list_item_children``
        returns ``[]`` for a leaf (a FIQL ``parent.id==`` query with no matches),
        so absence is data, not an error — only a real fetch failure aborts.
        """
        client = self._client_handle()
        out: List[Dict[str, Any]] = []
        visited: set[str] = {root_id}
        frontier: List[str] = [root_id]

        while frontier:
            parent_id = frontier.pop()
            # No try/except: any error here (TopdeskTransientError,
            # ConnectionError, TopdeskAuthError, TopdeskApiError, …) must
            # propagate so the base worker aborts the cycle without running the
            # set-difference deletion against a partial enumeration.
            children = await client.list_item_children(parent_id)
            for child in children:
                child_id = child.get('id')
                if not child_id or child_id in visited:
                    continue
                visited.add(child_id)
                # Always descend, regardless of the node's own visibility: a
                # not-visible intermediate node may still have in-scope descendants
                # we want to sync. Only in-scope nodes are emitted.
                frontier.append(child_id)
                if _should_sync(child):
                    out.append(child)
        return out

    def _file_info_for(self, source: Dict[str, Any], item: Dict[str, Any]) -> Dict[str, Any]:
        """Build a base_worker file_info dict from an enumerated REST item node.

        Reads the nested REST shape via ``mapping`` (translation.content.*,
        status.name, visibility.sspVisibility, urls.*). The web link comes straight
        from ``urls`` (relative → prefixed with the tenant URL); there is no SSP
        URL construction.
        """
        item_id = item['id']
        title = mapping.item_title(item) or f'item-{item_id}'
        site = get_service_site()
        base_url = site['url'] if site else ''
        return {
            'item': {
                'id': item_id,
                'name': title,
                'size': 0,
                'modificationDate': item.get('modificationDate') or '',
            },
            'item_id': item_id,
            'title': title,
            'web_url': mapping.item_web_url(item, base_url),
            'source_type': source['type'],
            'source_item_id': source['item_id'],
            'name': _sanitise_filename(title, item_id),
            'relative_path': (title or f'item-{item_id}').strip(),
            # Fields available on list/child/get nodes — carried for the
            # front-matter. Full hydration (content) happens in _download_file_content.
            'topdesk_number': item.get('number') or '',
            'topdesk_language': mapping.item_language(item),
            'topdesk_status': mapping.item_status_name(item),
            'topdesk_visibility': mapping.item_ssp_visibility(item),
            'topdesk_keywords': mapping.item_keywords(item),
            'topdesk_created_at': item.get('creationDate') or '',
            'topdesk_modified_at': item.get('modificationDate') or '',
            'topdesk_available_translations': item.get('availableTranslations') or [],
        }

    async def _collect_folder_files(self, source: Dict[str, Any]) -> tuple[List[Dict[str, Any]], int]:
        """Enumerate items for a subtree (or whole-KB) source.

        Uses ``modificationDate`` as the change indicator: if the stored item_map
        carries the same modificationDate for an item AND its File row is
        completed, it is skipped.
        """
        items = await self._enumerate_source_items(source)

        old_item_map: Dict[str, str] = source.get('item_map', {}) or {}
        current_ids = {i['id'] for i in items}

        # Detect deletions: previously tracked items no longer present.
        deleted_count = 0
        for old_id in list(old_item_map.keys()):
            if old_id not in current_ids:
                await self._handle_deleted_item({'id': old_id})
                deleted_count += 1

        files_to_process: List[Dict[str, Any]] = []
        new_item_map: Dict[str, str] = {}

        for item in items:
            item_id = item['id']
            current_modified = item.get('modificationDate') or ''
            # Track current modificationDate for next sync's deletion-detection +
            # skip check. Mirror of `last_synced_modified` in _collect_single_file.
            new_item_map[item_id] = current_modified

            # Dedupe across overlapping sources: each item only queued once per
            # sync, regardless of how many sources reference it.
            if item_id in self._seen_item_ids:
                continue
            self._seen_item_ids.add(item_id)

            stored_modified = old_item_map.get(item_id) or ''
            if current_modified and current_modified == stored_modified:
                file_id = f'{_FILE_ID_PREFIX}{item_id}'
                existing = await Files.get_file_by_id(file_id)
                if existing and (existing.data or {}).get('status') == 'completed':
                    continue
                log.info(
                    'TOPdesk item %s modificationDate unchanged but record missing/incomplete — re-syncing',
                    item_id,
                )

            files_to_process.append(self._file_info_for(source, item))

        source['item_map'] = new_item_map
        source['last_sync_at'] = int(time.time())

        return files_to_process, deleted_count

    async def _collect_single_file(self, source: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Single-item source without descendants. Uses modificationDate for change."""
        client = self._client_handle()
        item_id = source['item_id']

        # Skip if this item was already queued by a subtree source.
        if item_id in self._seen_item_ids:
            return None

        try:
            item = await client.get_knowledge_item(item_id)
        except Exception as e:
            log.error('Failed to fetch TOPdesk item %s: %s', source.get('name'), e)
            return None

        if not item:
            log.warning('TOPdesk item not found: %s', source.get('name'))
            return None

        # Out-of-scope / archived items never sync.
        if not _should_sync(item):
            log.info(
                'TOPdesk item %s is out of sync scope (visibility=%s, archived=%s) — skipping',
                item_id,
                mapping.item_ssp_visibility(item),
                bool(item.get('archived')),
            )
            return None

        self._seen_item_ids.add(item_id)

        current_modified = item.get('modificationDate') or ''
        stored_modified = source.get('last_synced_modified') or ''

        if current_modified and current_modified == stored_modified:
            file_id = f'{_FILE_ID_PREFIX}{item_id}'
            existing = await Files.get_file_by_id(file_id)
            if existing and (existing.data or {}).get('status') == 'completed':
                return None
            log.info('TOPdesk item %s modificationDate matches but record incomplete — re-syncing', source.get('name'))

        source['last_synced_modified'] = current_modified

        return self._file_info_for(source, item)

    def _get_cloud_hash(self, file_info: Dict[str, Any]) -> Optional[str]:
        """TOPdesk change indicator: the item's modificationDate string."""
        modified = (file_info.get('item') or {}).get('modificationDate')
        return str(modified) if modified else None

    async def _download_file_content(self, file_info: Dict[str, Any]) -> bytes:
        """Fetch the item content; render Markdown with a front-matter block.

        Side-effect: enriches ``file_info`` with structured TOPdesk metadata
        (keywords, language, status, dates) hydrated from the full item, so the
        subsequent ``_get_provider_file_meta`` call surfaces them on the File row
        and propagates them to vector-DB chunk metadata.
        """
        item_id = file_info['item_id']
        client = self._client_handle()

        item = await client.get_knowledge_item(item_id)
        if not item:
            raise RuntimeError(f'TOPdesk item {item_id} disappeared during sync')

        html = mapping.item_body_html(item)
        # html_to_markdown is sync + CPU-bound (BeautifulSoup parse); off-thread
        # to avoid stalling the event loop on long items.
        markdown_body = await asyncio.to_thread(html_to_markdown, html) if html else ''

        # Hydrate enrichment from the full node (nested REST shape), falling back to
        # the fields already on file_info.
        file_info['title'] = mapping.item_title(item) or file_info.get('title') or f'item-{item_id}'
        file_info['topdesk_number'] = item.get('number') or file_info.get('topdesk_number') or ''
        file_info['topdesk_keywords'] = mapping.item_keywords(item) or file_info.get('topdesk_keywords') or []
        file_info['topdesk_language'] = mapping.item_language(item) or file_info.get('topdesk_language') or ''
        file_info['topdesk_status'] = mapping.item_status_name(item) or file_info.get('topdesk_status') or ''
        file_info['topdesk_visibility'] = mapping.item_ssp_visibility(item) or file_info.get('topdesk_visibility') or ''
        file_info['topdesk_created_at'] = item.get('creationDate') or file_info.get('topdesk_created_at') or ''
        file_info['topdesk_modified_at'] = item.get('modificationDate') or file_info.get('topdesk_modified_at') or ''
        file_info['topdesk_available_translations'] = (
            item.get('availableTranslations') or file_info.get('topdesk_available_translations') or []
        )
        file_info['topdesk_parent_id'] = mapping.item_parent_id(item)

        rendered = _build_front_matter(file_info) + markdown_body + '\n'

        max_bytes = TOPDESK_MAX_ITEM_SIZE_MB * 1024 * 1024
        encoded = rendered.encode('utf-8')
        if len(encoded) > max_bytes:
            log.warning(
                'TOPdesk item %s exceeds max size (%d bytes > %d), truncating',
                item_id,
                len(encoded),
                max_bytes,
            )
            # Decode-then-reencode trims any incomplete UTF-8 sequence at the cut.
            encoded = encoded[:max_bytes].decode('utf-8', errors='ignore').encode('utf-8')

        return encoded

    def _get_provider_storage_headers(self, item_id: str) -> dict:
        return {
            'OpenWebUI-Source': 'topdesk',
            'OpenWebUI-Topdesk-Item-Id': item_id,
        }

    def _get_provider_file_meta(
        self,
        item_id: str,
        source_item_id: Optional[str],
        relative_path: str,
        name: str,
        content_type: str,
        size: int,
        file_info: Optional[Dict[str, Any]] = None,
    ) -> dict:
        info = file_info or {}
        return {
            'name': name,
            'content_type': 'text/markdown',
            'size': size,
            'source': 'topdesk',
            'topdesk_item_id': item_id,
            'topdesk_number': info.get('topdesk_number', ''),
            # Generic provenance URL (see Confluence worker) — one `source_url`
            # for every cloud-sync provider, consumed by the citation layer.
            'source_url': info.get('web_url', ''),
            'topdesk_title': info.get('title', ''),
            'topdesk_language': info.get('topdesk_language', ''),
            'topdesk_status': info.get('topdesk_status', ''),
            # Visibility is recorded as metadata only — the service account's
            # TOPdesk-side permission filter IS the visibility boundary in v1.
            'topdesk_visibility': info.get('topdesk_visibility', ''),
            # Keywords populated either at collection or download. Carried so they
            # propagate to vector-chunk metadata.
            'topdesk_keywords': info.get('topdesk_keywords') or [],
            # BCP-47 tags of every translation available for the item (v1 syncs the
            # tenant default language only; this records what else exists).
            'topdesk_available_translations': info.get('topdesk_available_translations') or [],
            'topdesk_parent_id': info.get('topdesk_parent_id', ''),
            'topdesk_created_at': info.get('topdesk_created_at', ''),
            'topdesk_modified_at': info.get('topdesk_modified_at', ''),
            'source_item_id': source_item_id,
            'relative_path': relative_path,
            'last_synced_at': int(time.time()),
        }

    # ------------------------------------------------------------------
    # Access / permissions (shared-KB rules only — no per-user mode)
    # ------------------------------------------------------------------

    async def _sync_permissions(self) -> None:
        """Verify the service credential still works; suspend the KB if not.

        TOPdesk has no per-user mode — the only "permission" is the service
        credential's validity. A 401 (TopdeskAuthError) is terminal: the
        credential is revoked/invalid, so the KB is suspended (30-day lifecycle).
        A transient/connection error leaves the suspension state untouched so a
        blip doesn't suspend a healthy KB.
        """
        if not service_auth_configured():
            # No usable credential configured — treat as access lost.
            await self._suspend_kb('service_credential_missing')
            return

        client = self._client_handle()
        owner_has_access = False
        definite_denial = False
        try:
            result = await client.probe()
            owner_has_access = bool(result.get('ok'))
            if not owner_has_access:
                # Probe ran but reported not-ok (e.g. a 4xx that wasn't 401) —
                # treat as a definite denial rather than transient.
                definite_denial = True
        except TopdeskAuthError:
            definite_denial = True
        except Exception as e:
            # Transient/connection error — don't change suspension state.
            log.warning('Transient error probing TOPdesk access: %s', e)
            return

        if owner_has_access:
            await self._unsuspend_kb_if_needed()
        elif definite_denial:
            await self._suspend_kb('service_credential_invalid')

    # Suspend only after this many *consecutive* cycles report a terminal auth
    # failure. A single bad cycle (e.g. a momentary 401 from an edge/proxy)
    # must not take the single shared KB offline. Mirrors the Confluence gate.
    _AUTH_FAIL_SUSPEND_THRESHOLD = 2

    async def _suspend_kb(self, reason: str) -> None:
        knowledge = await Knowledges.get_knowledge_by_id(self.knowledge_id)
        if not knowledge:
            return
        meta = knowledge.meta or {}
        sync_info = meta.get(self.meta_key, {})
        if sync_info.get('suspended_at'):
            return

        # Consecutive-failure gate: a single auth failure increments the
        # counter but does not suspend; only the threshold-th consecutive
        # failure stamps suspended_at. Persisted in meta so it spans cycles.
        fail_count = int(sync_info.get('auth_fail_count', 0)) + 1
        sync_info['auth_fail_count'] = fail_count
        if fail_count < self._AUTH_FAIL_SUSPEND_THRESHOLD:
            log.warning(
                'TOPdesk service credential unusable (%s) for KB %s — failure %d/%d, not suspending yet',
                reason,
                self.knowledge_id,
                fail_count,
                self._AUTH_FAIL_SUSPEND_THRESHOLD,
            )
            meta[self.meta_key] = sync_info
            await Knowledges.update_knowledge_meta_by_id(self.knowledge_id, meta)
            return

        log.warning('TOPdesk service credential unusable (%s), suspending KB %s', reason, self.knowledge_id)
        sync_info['suspended_at'] = int(time.time())
        sync_info['suspended_reason'] = reason
        meta[self.meta_key] = sync_info
        await Knowledges.update_knowledge_meta_by_id(self.knowledge_id, meta)
        await self._update_sync_status(
            'suspended',
            error='TOPdesk service credential is no longer valid. '
            'KB suspended — will be deleted after 30 days if access is not restored.',
        )

    async def _unsuspend_kb_if_needed(self) -> None:
        knowledge = await Knowledges.get_knowledge_by_id(self.knowledge_id)
        if not knowledge:
            return
        meta = knowledge.meta or {}
        sync_info = meta.get(self.meta_key, {})

        was_suspended = bool(sync_info.get('suspended_at'))
        had_failures = bool(sync_info.get('auth_fail_count'))
        if not was_suspended and not had_failures:
            return  # nothing to clear — avoid a needless meta write

        if was_suspended:
            log.info('TOPdesk service credential restored, unsuspending KB %s', self.knowledge_id)
        sync_info.pop('suspended_at', None)
        sync_info.pop('suspended_reason', None)
        # Any success resets the consecutive-failure streak.
        sync_info.pop('auth_fail_count', None)
        meta[self.meta_key] = sync_info
        await Knowledges.update_knowledge_meta_by_id(self.knowledge_id, meta)

    async def _verify_source_access(self, source: Dict[str, Any]) -> bool:
        """Verify a source still resolves under the service credential.

        For TOPdesk a source maps to a knowledge item (or the whole KB). A
        whole-KB / ``__all__`` source is always accessible if the credential
        works (already checked by ``_sync_permissions``). A specific item that
        404s/401s is treated as revoked so its files are cleaned up.
        """
        item_id = source.get('item_id')
        if item_id in (None, '', '__all__', '*'):
            return True

        client = self._client_handle()
        try:
            result = await client.get_knowledge_item(item_id)
            return result is not None
        except TopdeskAuthError:
            # Credential revoked — _sync_permissions already suspends the KB.
            # Returning True here avoids tearing down every source's files on a
            # transient-looking auth blip; suspension is the right lever.
            log.warning('TOPdesk auth error verifying source %s', source.get('name'))
            return True
        except Exception as e:
            log.warning('Error verifying TOPdesk source access: %s', e)
            return True

    async def _handle_revoked_source(self, source: Dict[str, Any]) -> int:
        """Remove all files associated with a revoked TOPdesk source."""
        source_name = source.get('name', 'unknown')
        source_item_id = source.get('item_id')
        removed_count = 0

        files = await Knowledges.get_files_by_id(self.knowledge_id)
        if not files:
            return 0

        for file in files:
            if not file.id.startswith(_FILE_ID_PREFIX):
                continue

            file_meta = file.meta or {}
            if file_meta.get('source_item_id') != source_item_id:
                continue

            await Knowledges.remove_file_from_knowledge_by_id(self.knowledge_id, file.id)
            try:
                from open_webui.retrieval.vector.async_client import ASYNC_VECTOR_DB_CLIENT

                await ASYNC_VECTOR_DB_CLIENT.delete(
                    collection_name=self.knowledge_id,
                    filter={'file_id': file.id},
                )
            except Exception as e:
                log.warning('Failed to remove vectors for %s: %s', file.id, e)

            remaining = await Knowledges.get_knowledge_files_by_file_id(file.id)
            if not remaining:
                await DeletionService.delete_file(file.id)

            removed_count += 1

        log.info(
            'Removed %d files from KB %s due to revoked access to TOPdesk source "%s"',
            removed_count,
            self.knowledge_id,
            source_name,
        )
        return removed_count
