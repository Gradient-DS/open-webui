"""Confluence sync worker — downloads pages, converts HTML→Markdown, embeds, stores."""

import asyncio
import logging
import re
import time
from typing import Optional, Dict, Any, List

import httpx

from open_webui.services.confluence.confluence_client import ConfluenceClient
from open_webui.services.confluence.html_renderer import html_to_markdown
from open_webui.services.sync.base_worker import BaseSyncWorker
from open_webui.models.knowledge import Knowledges
from open_webui.models.files import Files
from open_webui.config import (
    CONFLUENCE_MAX_PAGES_PER_SYNC,
    CONFLUENCE_MAX_PAGE_SIZE_MB,
)
from open_webui.retrieval.vector.factory import VECTOR_DB_CLIENT
from open_webui.services.deletion import DeletionService

log = logging.getLogger(__name__)

_FILE_ID_PREFIX = 'confluence-'

# Heuristic: map a page's Confluence labels to a coarse page_type bucket so
# RAG callers can filter by document kind (decision, meeting, how-to, ...)
# without having to know every label your team uses. First match wins.
_PAGE_TYPE_RULES: list[tuple[str, set[str]]] = [
    ('decision', {'decision', 'adr', 'architecture-decision'}),
    ('meeting', {'meeting-notes', 'meeting', 'minutes', 'standup', '1on1'}),
    ('how-to', {'how-to', 'howto', 'guide', 'tutorial', 'runbook', 'playbook'}),
    ('reference', {'reference', 'spec', 'config'}),
]


def _sanitise_filename(title: str, page_id: str) -> str:
    """Turn a Confluence page title into a safe `.md` filename."""
    cleaned = re.sub(r'[\\/:*?"<>|\r\n\t]', '_', (title or '').strip())
    cleaned = cleaned[:120].rstrip(' .') or f'page-{page_id}'
    return f'{cleaned}.md'


def _derive_page_type(labels: List[str]) -> Optional[str]:
    label_set = {l.lower() for l in labels}
    for type_, keywords in _PAGE_TYPE_RULES:
        if label_set & keywords:
            return type_
    return None


class ConfluenceSyncWorker(BaseSyncWorker):
    """Worker to sync Confluence space/page contents to a Knowledge base."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Migrate legacy-format sources (saved before the type/confluence_type
        # split). base_worker dispatches on source['type'] == 'folder', so
        # space / page-subtree sources must be normalized here.
        for source in self.sources:
            raw_type = source.get('type')
            if 'confluence_type' not in source and raw_type in ('space', 'page'):
                source['confluence_type'] = raw_type
            if raw_type == 'space':
                source['type'] = 'folder'
            elif raw_type == 'page':
                if source.get('include_descendants', True):
                    source['type'] = 'folder'
                else:
                    source['type'] = 'file'
        # Track (cloud_id, page_id) pairs emitted so overlapping sources (e.g. a
        # space + one of its subtrees) don't queue the same page twice → avoids
        # UniqueViolation on the file_id primary key when two pipeline tasks
        # race to INSERT the same row. Keyed by cloud_id+page_id since page IDs
        # are not globally unique across Atlassian sites.
        self._seen_page_ids: set[tuple[str, str]] = set()

    # ------------------------------------------------------------------
    # Abstract properties
    # ------------------------------------------------------------------

    @property
    def meta_key(self) -> str:
        return 'confluence_sync'

    @property
    def file_id_prefix(self) -> str:
        return _FILE_ID_PREFIX

    @property
    def event_prefix(self) -> str:
        return 'confluence'

    @property
    def provider_slug(self) -> str:
        return 'confluence'

    @property
    def internal_request_path(self) -> str:
        return '/internal/confluence-sync'

    @property
    def max_files_config(self) -> int:
        return CONFLUENCE_MAX_PAGES_PER_SYNC

    @property
    def source_clear_delta_keys(self) -> list[str]:
        return ['page_map', 'last_synced_version']

    # ------------------------------------------------------------------
    # Client lifecycle
    # ------------------------------------------------------------------

    def _create_client(self):
        """Return a placeholder client; real per-source clients are built on
        demand inside the collection methods because cloud_id can vary per source.
        """
        # We keep a dict of clients keyed by cloud_id so close() can clean
        # them all up after sync() finishes.
        self._clients_by_cloud_id: Dict[str, ConfluenceClient] = {}
        return self._clients_by_cloud_id

    def _client_for(self, cloud_id: str) -> ConfluenceClient:
        cached = self._clients_by_cloud_id.get(cloud_id)
        if cached is None:
            cached = ConfluenceClient(
                access_token=self.access_token,
                cloud_id=cloud_id,
                token_provider=self._token_provider,
            )
            self._clients_by_cloud_id[cloud_id] = cached
        return cached

    async def _close_client(self):
        clients = getattr(self, '_clients_by_cloud_id', {}) or {}
        for client in clients.values():
            try:
                await client.close()
            except Exception as e:  # pragma: no cover
                log.warning('Error closing Confluence client: %s', e)
        self._clients_by_cloud_id = {}

    # ------------------------------------------------------------------
    # Support checks
    # ------------------------------------------------------------------

    def _is_supported_file(self, item: Dict[str, Any]) -> bool:
        """All Confluence pages are supported in v1."""
        return True

    # ------------------------------------------------------------------
    # Source → pages resolution
    # ------------------------------------------------------------------

    async def _collect_folder_files(self, source: Dict[str, Any]) -> tuple[List[Dict[str, Any]], int]:
        """Enumerate pages for a space or page-subtree source.

        Uses page.version.number as the change indicator: if the stored
        page_map has the same version for a page, it is skipped.
        """
        cloud_id = source['cloud_id']
        client = self._client_for(cloud_id)

        pages = await self._list_pages_for_source(source, client)

        old_page_map: Dict[str, int] = source.get('page_map', {}) or {}
        current_ids = {p['id'] for p in pages}

        # Detect deletions: previously tracked pages no longer present.
        deleted_count = 0
        for old_id in list(old_page_map.keys()):
            if old_id not in current_ids:
                await self._handle_deleted_item({'id': old_id})
                deleted_count += 1

        files_to_process: List[Dict[str, Any]] = []
        new_page_map: Dict[str, int] = {}
        source_item_id = source['item_id']

        for page in pages:
            page_id = page['id']
            current_version = int((page.get('version') or {}).get('number') or 0)
            # Track current version for next sync's deletion-detection + skip
            # check. Mirror of `last_synced_version` in _collect_single_file.
            new_page_map[page_id] = current_version

            # Dedupe across overlapping sources: each page only queued once
            # per sync, regardless of how many sources reference it.
            seen_key = (cloud_id, page_id)
            if seen_key in self._seen_page_ids:
                continue
            self._seen_page_ids.add(seen_key)

            stored_version = int(old_page_map.get(page_id) or 0)
            relative_path = (page.get('title') or f'page-{page_id}').strip()

            if current_version and current_version == stored_version:
                file_id = f'{_FILE_ID_PREFIX}{page_id}'
                existing = Files.get_file_by_id(file_id)
                if existing and (existing.data or {}).get('status') == 'completed':
                    continue
                log.info(
                    'Confluence page %s version unchanged but record missing/incomplete — re-syncing',
                    page_id,
                )

            files_to_process.append(
                {
                    'item': {
                        'id': page_id,
                        'name': page.get('title') or f'page-{page_id}',
                        'size': 0,
                        'version': page.get('version') or {},
                    },
                    'cloud_id': cloud_id,
                    'space_id': source.get('space_id'),
                    'space_key': source.get('space_key'),
                    'page_id': page_id,
                    'title': page.get('title'),
                    'web_url': self._build_page_url(source, page),
                    'source_type': source['type'],
                    'source_item_id': source_item_id,
                    'name': _sanitise_filename(page.get('title') or '', page_id),
                    'relative_path': relative_path,
                }
            )

        source['page_map'] = new_page_map
        source['last_sync_at'] = int(time.time())

        return files_to_process, deleted_count

    async def _collect_single_file(self, source: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Single-page source without descendants. Uses version.number for change."""
        cloud_id = source['cloud_id']
        client = self._client_for(cloud_id)

        # Skip if this page was already queued by a space / subtree source.
        seen_key = (cloud_id, source['item_id'])
        if seen_key in self._seen_page_ids:
            return None

        try:
            page = await client.get_page(source['item_id'], include_body=False)
        except Exception as e:
            log.error('Failed to fetch Confluence page %s: %s', source.get('name'), e)
            return None

        if not page:
            log.warning('Confluence page not found: %s', source.get('name'))
            return None

        self._seen_page_ids.add(seen_key)

        current_version = int((page.get('version') or {}).get('number') or 0)
        stored_version = int(source.get('last_synced_version') or 0)

        if current_version and current_version == stored_version:
            file_id = f'{_FILE_ID_PREFIX}{source["item_id"]}'
            existing = Files.get_file_by_id(file_id)
            if existing and (existing.data or {}).get('status') == 'completed':
                return None
            log.info('Confluence page %s version matches but record incomplete — re-syncing', source.get('name'))

        source['last_synced_version'] = current_version

        return {
            'item': {
                'id': page['id'],
                'name': page.get('title') or source.get('name'),
                'size': 0,
                'version': page.get('version') or {},
            },
            'cloud_id': cloud_id,
            'space_id': (page.get('spaceId') or source.get('space_id')),
            'space_key': source.get('space_key'),
            'page_id': page['id'],
            'title': page.get('title'),
            'web_url': self._build_page_url(source, page),
            'source_type': 'page',
            'source_item_id': source['item_id'],
            'name': _sanitise_filename(page.get('title') or '', page['id']),
        }

    def _get_cloud_hash(self, file_info: Dict[str, Any]) -> Optional[str]:
        """Confluence change indicator: page version.number (stringified)."""
        version = (file_info.get('item') or {}).get('version') or {}
        number = version.get('number')
        return str(number) if number is not None else None

    async def _download_file_content(self, file_info: Dict[str, Any]) -> bytes:
        """Fetch the page + labels + ancestors; render Markdown with front-matter.

        Side-effect: enriches ``file_info`` with structured Confluence metadata
        (labels, breadcrumb, page_type, author, last_modified, created_at,
        ancestor_ids). base_worker calls ``_get_provider_file_meta`` AFTER this
        method, passing the same ``file_info``, so the enrichment surfaces on
        ``file.meta`` and propagates to vector-DB chunk metadata.
        """
        cloud_id = file_info['cloud_id']
        page_id = file_info['page_id']
        client = self._client_for(cloud_id)

        # Fetch page body, labels, and ancestor chain in parallel — the labels
        # and ancestor calls are cheap, so they hide behind the body fetch.
        page_result, labels_result, ancestors_result = await asyncio.gather(
            client.get_page(page_id, include_body=True),
            client.list_all_page_labels(page_id),
            client.list_all_page_ancestors(page_id),
            return_exceptions=True,
        )

        if isinstance(page_result, BaseException) or not page_result:
            if isinstance(page_result, BaseException):
                raise page_result
            raise RuntimeError(f'Confluence page {page_id} disappeared during sync')
        page = page_result

        labels = [
            label.get('name')
            for label in (labels_result if isinstance(labels_result, list) else [])
            if label.get('name')
        ]
        ancestors = ancestors_result if isinstance(ancestors_result, list) else []

        body = (page.get('body') or {}).get('view') or {}
        html = body.get('value') or ''
        # html_to_markdown is sync + CPU-bound (BeautifulSoup parse); off-thread
        # to avoid stalling the event loop on long pages.
        markdown_body = await asyncio.to_thread(html_to_markdown, html) if html else ''

        title = page.get('title') or file_info.get('title') or f'page-{page_id}'
        version_number = (page.get('version') or {}).get('number')
        version_when = (page.get('version') or {}).get('createdAt') or ''
        author_id = (page.get('version') or {}).get('authorId') or ''
        created_at = page.get('createdAt') or ''
        space_id = page.get('spaceId') or file_info.get('space_id') or ''
        web_url = file_info.get('web_url') or ''

        ancestor_titles = [a.get('title') for a in ancestors if a.get('title')]
        breadcrumb = ' > '.join([*ancestor_titles, title])
        page_type = _derive_page_type(labels)

        front_matter = [
            f'# {title}',
            '',
            f'_Confluence page · space {space_id} · version {version_number}_',
        ]
        if breadcrumb:
            front_matter.append(f'_Path: {breadcrumb}_')
        if labels:
            front_matter.append(f'_Labels: {", ".join(labels)}_')
        if page_type:
            front_matter.append(f'_Type: {page_type}_')
        if web_url:
            front_matter.append(f'_Source: {web_url}_')
        if created_at:
            front_matter.append(f'_Created: {created_at}_')
        if version_when:
            front_matter.append(f'_Last modified: {version_when}_')
        if author_id:
            front_matter.append(f'_Author: {author_id}_')
        front_matter.append('')
        front_matter.append('')

        # Enrich file_info so _get_provider_file_meta can promote these onto
        # the File row's meta (and from there onto every chunk's metadata via
        # retrieval.py's `**file.meta` spread).
        file_info['confluence_labels'] = labels
        file_info['confluence_breadcrumb'] = breadcrumb
        file_info['confluence_page_type'] = page_type
        file_info['confluence_ancestor_ids'] = [a.get('id') for a in ancestors if a.get('id')]
        file_info['confluence_author_id'] = author_id
        file_info['confluence_last_modified'] = version_when
        file_info['confluence_created_at'] = created_at

        rendered = '\n'.join(front_matter) + markdown_body + '\n'

        max_bytes = CONFLUENCE_MAX_PAGE_SIZE_MB * 1024 * 1024
        encoded = rendered.encode('utf-8')
        if len(encoded) > max_bytes:
            log.warning(
                'Confluence page %s exceeds max size (%d bytes > %d), truncating',
                page_id,
                len(encoded),
                max_bytes,
            )
            # Decode-then-reencode trims any incomplete UTF-8 sequence at the cut.
            encoded = encoded[:max_bytes].decode('utf-8', errors='ignore').encode('utf-8')

        return encoded

    def _get_provider_storage_headers(self, item_id: str) -> dict:
        return {
            'OpenWebUI-Source': 'confluence',
            'OpenWebUI-Confluence-Page-Id': item_id,
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
            'source': 'confluence',
            'confluence_page_id': item_id,
            'confluence_cloud_id': info.get('cloud_id', ''),
            'confluence_space_id': info.get('space_id', ''),
            'confluence_space_key': info.get('space_key', ''),
            'confluence_url': info.get('web_url', ''),
            'confluence_title': info.get('title', ''),
            # Enrichment fields populated by _download_file_content. Empty when
            # this method is called before download (e.g. cloud-hash skip path
            # or shared-loader job-creation path).
            'confluence_labels': info.get('confluence_labels') or [],
            'confluence_page_type': info.get('confluence_page_type'),
            'confluence_breadcrumb': info.get('confluence_breadcrumb', ''),
            'confluence_ancestor_ids': info.get('confluence_ancestor_ids') or [],
            'confluence_author_id': info.get('confluence_author_id', ''),
            'confluence_last_modified': info.get('confluence_last_modified', ''),
            'confluence_created_at': info.get('confluence_created_at', ''),
            'source_item_id': source_item_id,
            'relative_path': relative_path,
            'last_synced_at': int(time.time()),
        }

    # ------------------------------------------------------------------
    # Access / permissions
    # ------------------------------------------------------------------

    async def _sync_permissions(self) -> None:
        """Verify the KB owner still has access to at least one source.

        Probes every source: only suspend the KB if every probe reports a
        permanent access denial (401/403/404). A single revoked source among
        many should not suspend the whole KB.
        """
        if not self.sources:
            return

        any_access = False
        any_definite_denial = False

        for source in self.sources:
            cloud_id = source.get('cloud_id')
            if not cloud_id:
                continue

            client = self._client_for(cloud_id)
            try:
                if self._is_space_source(source) and source.get('space_id'):
                    result = await client.get_space(source['space_id'])
                else:
                    result = await client.get_page(source['item_id'], include_body=False)
                if result is not None:
                    any_access = True
                    break
                any_definite_denial = True  # 404 returned as None
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (401, 403, 404):
                    any_definite_denial = True
                else:
                    log.warning('Transient error checking Confluence access: %s', e)
                    return  # Don't change suspension state on transient failures
            except Exception as e:
                log.warning('Error checking Confluence access: %s', e)
                return

        if not any_access and not any_definite_denial:
            # No probes ran (e.g. all sources missing cloud_id) — treat as transient.
            return

        owner_has_access = any_access

        knowledge = Knowledges.get_knowledge_by_id(self.knowledge_id)
        if not knowledge:
            return

        meta = knowledge.meta or {}
        sync_info = meta.get(self.meta_key, {})

        if owner_has_access:
            if sync_info.get('suspended_at'):
                log.info('Owner regained Confluence access, unsuspending KB %s', self.knowledge_id)
                sync_info.pop('suspended_at', None)
                sync_info.pop('suspended_reason', None)
                meta[self.meta_key] = sync_info
                Knowledges.update_knowledge_meta_by_id(self.knowledge_id, meta)
        else:
            if not sync_info.get('suspended_at'):
                log.warning(
                    'Owner %s lost Confluence access, suspending KB %s',
                    self.user_id,
                    self.knowledge_id,
                )
                sync_info['suspended_at'] = int(time.time())
                sync_info['suspended_reason'] = 'owner_access_lost'
                meta[self.meta_key] = sync_info
                Knowledges.update_knowledge_meta_by_id(self.knowledge_id, meta)

                await self._update_sync_status(
                    'suspended',
                    error='Owner no longer has access to the Confluence source. '
                    'KB suspended — will be deleted after 30 days if access is not restored.',
                )

    async def _verify_source_access(self, source: Dict[str, Any]) -> bool:
        cloud_id = source.get('cloud_id')
        if not cloud_id:
            return False

        client = self._client_for(cloud_id)
        try:
            if self._is_space_source(source) and source.get('space_id'):
                result = await client.get_space(source['space_id'])
            else:
                result = await client.get_page(source['item_id'], include_body=False)
            return result is not None
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (401, 403, 404):
                log.warning(
                    'User %s lost access to Confluence source %s: %s',
                    self.user_id,
                    source.get('name'),
                    e.response.status_code,
                )
                return False
            log.warning('Error verifying Confluence access: %s', e)
            return True
        except Exception as e:
            log.warning('Error verifying Confluence access: %s', e)
            return True

    async def _handle_revoked_source(self, source: Dict[str, Any]) -> int:
        """Remove all files associated with a revoked Confluence source."""
        source_name = source.get('name', 'unknown')
        source_item_id = source.get('item_id')
        removed_count = 0

        files = Knowledges.get_files_by_id(self.knowledge_id)
        if not files:
            return 0

        for file in files:
            if not file.id.startswith(_FILE_ID_PREFIX):
                continue

            file_meta = file.meta or {}
            if file_meta.get('source_item_id') != source_item_id:
                continue

            Knowledges.remove_file_from_knowledge_by_id(self.knowledge_id, file.id)
            try:
                VECTOR_DB_CLIENT.delete(
                    collection_name=self.knowledge_id,
                    filter={'file_id': file.id},
                )
            except Exception as e:
                log.warning('Failed to remove vectors for %s: %s', file.id, e)

            remaining = Knowledges.get_knowledge_files_by_file_id(file.id)
            if not remaining:
                await asyncio.to_thread(DeletionService.delete_file, file.id)

            removed_count += 1

        log.info(
            'Removed %d files from KB %s due to revoked access to Confluence source "%s"',
            removed_count,
            self.knowledge_id,
            source_name,
        )
        return removed_count

    # ------------------------------------------------------------------
    # Confluence-specific helpers
    # ------------------------------------------------------------------

    async def _list_pages_for_source(
        self,
        source: Dict[str, Any],
        client: ConfluenceClient,
    ) -> List[Dict[str, Any]]:
        """Return all pages within a space or page-subtree source."""
        if self._is_space_source(source):
            space_id = source.get('space_id') or source['item_id']
            return await client.list_all_pages_in_space(space_id)

        # page-subtree source — item_id is a page id.
        include_descendants = bool(source.get('include_descendants', True))
        root_page = await client.get_page(source['item_id'], include_body=False)
        if not root_page:
            return []

        if not include_descendants:
            return [root_page]

        descendants = await client.list_all_page_descendants(source['item_id'])
        return [root_page] + descendants

    @staticmethod
    def _is_space_source(source: Dict[str, Any]) -> bool:
        """True if this source represents a whole Confluence space.

        Uses the new `confluence_type` field; falls back to the legacy `type`
        value in case older sources are still stored with type='space'.
        """
        return source.get('confluence_type') == 'space' or source.get('type') == 'space'

    def _build_page_url(self, source: Dict[str, Any], page: Dict[str, Any]) -> str:
        """Compose a direct-viewable URL to the page on the Confluence site.

        source['site_url'] (e.g. https://mycompany.atlassian.net) is stored when
        the source is first created by the picker router; pages surface a
        _links.webui path (relative) from v2.
        """
        site_url = (source.get('site_url') or '').rstrip('/')
        webui = (page.get('_links') or {}).get('webui') or ''
        if site_url and webui:
            if webui.startswith('/'):
                return f'{site_url}/wiki{webui}'
            return f'{site_url}/wiki/{webui}'
        return site_url
