"""OneDrive sync worker - Downloads and processes files from OneDrive folders."""

import httpx
import logging
import time
from typing import Optional, Dict, Any, List
from pathlib import Path

from open_webui.services.onedrive.graph_client import GraphClient
from open_webui.services.sync.base_worker import BaseSyncWorker
from open_webui.models.knowledge import Knowledges
from open_webui.models.files import Files
from open_webui.config import (
    ONEDRIVE_MAX_FILES_PER_SYNC,
    ONEDRIVE_MAX_FILE_SIZE_MB,
)
from open_webui.retrieval.vector.factory import VECTOR_DB_CLIENT
from open_webui.services.deletion import DeletionService
from open_webui.services.sync.constants import (
    SyncErrorType,
    FailedFile,
    SUPPORTED_EXTENSIONS,
)

log = logging.getLogger(__name__)

# Version tracking for folder_map schema. Bump this to force a full
# re-enumeration of all folder sources on next sync (clears delta_link).
FOLDER_MAP_VERSION = 1


class OneDriveSyncWorker(BaseSyncWorker):
    """Worker to sync OneDrive folder contents to a Knowledge base."""

    # ------------------------------------------------------------------
    # Abstract properties
    # ------------------------------------------------------------------

    @property
    def meta_key(self) -> str:
        return 'onedrive_sync'

    @property
    def file_id_prefix(self) -> str:
        return 'onedrive-'

    @property
    def event_prefix(self) -> str:
        return 'onedrive'

    @property
    def internal_request_path(self) -> str:
        return '/internal/onedrive-sync'

    @property
    def max_files_config(self) -> int:
        return ONEDRIVE_MAX_FILES_PER_SYNC

    @property
    def source_clear_delta_keys(self) -> list[str]:
        return ['delta_link', 'folder_map', 'folder_map_version']

    # ------------------------------------------------------------------
    # Abstract methods
    # ------------------------------------------------------------------

    def _create_client(self):
        return GraphClient(self.access_token, token_provider=self._token_provider)

    async def _close_client(self):
        if self._client:
            await self._client.close()

    def _is_supported_file(self, item: Dict[str, Any]) -> bool:
        """Check if file is supported for processing."""
        if 'folder' in item:
            return False

        name = item.get('name', '')
        ext = Path(name).suffix.lower()

        if ext not in SUPPORTED_EXTENSIONS:
            log.debug(f'Skipping unsupported file type: {name}')
            return False

        size = item.get('size', 0)
        max_size = ONEDRIVE_MAX_FILE_SIZE_MB * 1024 * 1024

        if size > max_size:
            log.warning(f'Skipping {name}: size {size} exceeds max {max_size}')
            return False

        return True

    async def _collect_folder_files(self, source: Dict[str, Any]) -> tuple[List[Dict[str, Any]], int]:
        """Collect files from a folder using delta query."""
        delta_link = source.get('delta_link')

        # Force full re-enumeration if folder_map is outdated or missing
        stored_version = source.get('folder_map_version', 0)
        if stored_version < FOLDER_MAP_VERSION:
            log.info(
                'Folder map version %d < %d for source %s, forcing full sync',
                stored_version,
                FOLDER_MAP_VERSION,
                source.get('name'),
            )
            delta_link = None
            source['folder_map'] = {}  # Clear stale map

        try:
            items, new_delta_link = await self._client.get_drive_delta(
                source['drive_id'], source['item_id'], delta_link
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 410:
                log.info(
                    'Delta token expired for source %s, performing full sync',
                    source['name'],
                )
                source['delta_link'] = None
                items, new_delta_link = await self._client.get_drive_delta(source['drive_id'], source['item_id'], None)
            else:
                raise

        # Update source with new delta link
        source['delta_link'] = new_delta_link

        # Build folder ID -> relative path map.
        # Load persisted map from previous syncs (incremental deltas may omit
        # unchanged folders, so we need the historical mapping).
        folder_map: Dict[str, str] = source.get('folder_map', {})
        # The source folder itself is always the root (empty relative path)
        folder_map[source['item_id']] = ''

        # First pass: update folder_map with any folder items from delta.
        # Delta items may arrive in any order, so we loop until no new folders
        # can be resolved (handles nested folders whose parent appears later).
        changed = True
        folder_items = [item for item in items if 'folder' in item and '@removed' not in item]
        while changed:
            changed = False
            for item in folder_items:
                parent_id = item.get('parentReference', {}).get('id', '')
                if parent_id not in folder_map:
                    continue
                parent_path = folder_map[parent_id]
                new_path = f'{parent_path}/{item["name"]}' if parent_path else item['name']
                if folder_map.get(item['id']) != new_path:
                    folder_map[item['id']] = new_path
                    changed = True

        # Handle deleted folders
        for item in items:
            if 'folder' in item and '@removed' in item:
                folder_map.pop(item.get('id', ''), None)

        # Persist updated folder_map and version back to source
        source['folder_map'] = folder_map
        source['folder_map_version'] = FOLDER_MAP_VERSION

        # Second pass: separate files and deleted items, compute relative paths
        files_to_process = []
        deleted_count = 0

        for item in items:
            if '@removed' in item:
                await self._handle_deleted_item(item)
                deleted_count += 1
            elif self._is_supported_file(item):
                parent_id = item.get('parentReference', {}).get('id', '')
                parent_path = folder_map.get(parent_id, '')
                item_name = item.get('name', 'unknown')
                relative_path = f'{parent_path}/{item_name}' if parent_path else item_name

                files_to_process.append(
                    {
                        'item': item,
                        'drive_id': source['drive_id'],
                        'source_type': 'folder',
                        'source_item_id': source['item_id'],
                        'name': item_name,
                        'relative_path': relative_path,
                    }
                )

        return files_to_process, deleted_count

    async def _collect_single_file(self, source: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Check if a single file needs syncing based on content hash."""
        try:
            # Get current file metadata from Graph API
            item = await self._client.get_item(source['drive_id'], source['item_id'])

            if not item:
                log.warning(f'File not found: {source["name"]}')
                return None

            # Check content hash for changes
            # OneDrive returns different hash types depending on the drive:
            # - sha256Hash: OneDrive for Business
            # - quickXorHash: OneDrive Personal
            hashes = item.get('file', {}).get('hashes', {})
            current_hash = hashes.get('sha256Hash') or hashes.get('quickXorHash')
            stored_hash = source.get('content_hash')

            if current_hash and current_hash == stored_hash:
                # Hash matches on OneDrive side, but verify the file was
                # actually processed successfully. If the file record was
                # deleted (orphan cleanup) or processing failed, re-sync it.
                file_id = f'onedrive-{source["item_id"]}'
                existing = await Files.get_file_by_id(file_id)
                if existing and (existing.data or {}).get('status') == 'completed':
                    log.info(f'File unchanged (hash match): {source["name"]}')
                    return None
                else:
                    reason = 'missing' if not existing else 'not processed'
                    log.info(f'File {source["name"]} hash matches but record {reason}, re-syncing')

            if not current_hash:
                log.warning(f'No hash available from OneDrive for: {source["name"]}')
            elif not stored_hash:
                log.info(f'First sync for file (no stored hash): {source["name"]}')
            elif current_hash != stored_hash:
                log.info(f'File changed (hash mismatch): {source["name"]}')

            # Store new hash for later save
            source['content_hash'] = current_hash

            return {
                'item': item,
                'drive_id': source['drive_id'],
                'source_type': 'file',
                'source_item_id': source['item_id'],
                'name': item.get('name', source['name']),
            }

        except Exception as e:
            log.error(f'Failed to check file {source["name"]}: {e}')
            return None

    def _get_cloud_hash(self, file_info: Dict[str, Any]) -> Optional[str]:
        """Extract OneDrive hash from item metadata.

        OneDrive for Business provides sha256Hash, Personal provides quickXorHash.
        """
        item = file_info['item']
        hashes = item.get('file', {}).get('hashes', {})
        return hashes.get('sha256Hash') or hashes.get('quickXorHash')

    async def _download_file_content(self, file_info: Dict[str, Any]) -> bytes:
        """Download file content from OneDrive."""
        drive_id = file_info['drive_id']
        item_id = file_info['item']['id']
        return await self._client.download_file(drive_id, item_id)

    def _get_provider_storage_headers(self, item_id: str) -> dict:
        return {
            'OpenWebUI-Source': 'onedrive',
            'OpenWebUI-OneDrive-Item-Id': item_id,
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
        drive_id = file_info.get('drive_id', '') if file_info else ''
        return {
            'name': name,
            'content_type': content_type,
            'size': size,
            'source': 'onedrive',
            'onedrive_item_id': item_id,
            'onedrive_drive_id': drive_id,
            'source_item_id': source_item_id,
            'relative_path': relative_path,
            'last_synced_at': int(time.time()),
        }

    async def _sync_permissions(self) -> None:
        """Verify the KB owner still has access to the cloud folder.

        If the owner lost access, suspend the KB by setting suspended_at in sync meta.
        If the owner has access and the KB was previously suspended, unsuspend it.
        Does NOT mirror cloud sharing permissions to Open WebUI access grants.
        """
        folder_source = next((s for s in self.sources if s.get('type') == 'folder'), None)
        if not folder_source:
            log.info('No folder sources, skipping owner access check')
            return

        try:
            # Check if the owner can still access the folder
            item = await self._client.get_item(folder_source['drive_id'], folder_source['item_id'])
            owner_has_access = item is not None
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (403, 404):
                owner_has_access = False
            else:
                # Transient error — don't change suspension state
                log.warning(f'Transient error checking owner access: {e}')
                return
        except Exception as e:
            log.warning(f'Error checking owner access: {e}')
            return

        knowledge = await Knowledges.get_knowledge_by_id(self.knowledge_id)
        if not knowledge:
            return

        meta = knowledge.meta or {}
        sync_info = meta.get(self.meta_key, {})

        if owner_has_access:
            if sync_info.get('suspended_at'):
                log.info(f'Owner regained access to folder, unsuspending KB {self.knowledge_id}')
                sync_info.pop('suspended_at', None)
                sync_info.pop('suspended_reason', None)
                meta[self.meta_key] = sync_info
                await Knowledges.update_knowledge_meta_by_id(self.knowledge_id, meta)
        else:
            if not sync_info.get('suspended_at'):
                log.warning(f'Owner {self.user_id} lost access to OneDrive folder, suspending KB {self.knowledge_id}')
                sync_info['suspended_at'] = int(time.time())
                sync_info['suspended_reason'] = 'owner_access_lost'
                meta[self.meta_key] = sync_info
                await Knowledges.update_knowledge_meta_by_id(self.knowledge_id, meta)

                await self._update_sync_status(
                    'suspended',
                    error='Owner no longer has access to the cloud folder. '
                    'KB suspended — will be deleted after 30 days if access is not restored.',
                )

    async def _verify_source_access(self, source: Dict[str, Any]) -> bool:
        """Verify the user can still access a OneDrive source."""
        drive_id = source.get('drive_id')
        item_id = source.get('item_id')
        source_type = source.get('type', 'folder')

        try:
            item = await self._client.get_item(drive_id, item_id)
            if item is None:
                return False
            return True
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (403, 404):
                log.warning(
                    f'User {self.user_id} lost access to {source_type} {drive_id}/{item_id}: {e.response.status_code}'
                )
                return False
            # For other errors (5xx, etc.), assume access is still valid
            # to avoid accidentally removing files
            log.warning(f'Error verifying access to {source_type} {drive_id}/{item_id}: {e}')
            return True
        except Exception as e:
            # For network errors, timeouts, etc., assume access is still valid
            log.warning(f'Error verifying access to {source_type} {drive_id}/{item_id}: {e}')
            return True

    async def _handle_revoked_source(self, source: Dict[str, Any]) -> int:
        """Remove all files associated with a revoked source from this KB."""
        source_name = source.get('name', 'unknown')
        source_drive_id = source.get('drive_id')
        removed_count = 0

        files = await Knowledges.get_files_by_id(self.knowledge_id)
        if not files:
            return 0

        for file in files:
            if not file.id.startswith('onedrive-'):
                continue

            file_meta = file.meta or {}
            file_drive_id = file_meta.get('onedrive_drive_id')
            file_source_item_id = file_meta.get('source_item_id')
            source_item_id = source.get('item_id')

            # Precise match by source_item_id if available
            if file_source_item_id:
                if file_source_item_id != source_item_id:
                    continue
            else:
                # Legacy fallback: match by drive_id (may over-match for same-drive sources)
                if not (file_drive_id and source_drive_id and file_drive_id == source_drive_id):
                    continue

            # Matched - proceed with removal
            await Knowledges.remove_file_from_knowledge_by_id(self.knowledge_id, file.id)
            # Remove vectors from KB collection
            try:
                VECTOR_DB_CLIENT.delete(
                    collection_name=self.knowledge_id,
                    filter={'file_id': file.id},
                )
            except Exception as e:
                log.warning(f'Failed to remove vectors for {file.id}: {e}')

            # Check for orphans - use DeletionService for full cleanup (vectors + storage + DB)
            remaining = await Knowledges.get_knowledge_files_by_file_id(file.id)
            if not remaining:
                await DeletionService.delete_file(file.id)

            removed_count += 1

        log.info(
            f"Removed {removed_count} files from KB {self.knowledge_id} due to revoked access to source '{source_name}'"
        )

        return removed_count
