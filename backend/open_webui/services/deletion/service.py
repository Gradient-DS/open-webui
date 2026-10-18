import asyncio
from dataclasses import dataclass, field
from typing import Dict, List, Set, Optional
import logging

from open_webui.models.files import Files
from open_webui.models.knowledge import Knowledges
from open_webui.soev.availability import SoevNotConfigured, configured
from open_webui.storage.provider import Storage
from open_webui.retrieval.vector.async_client import ASYNC_VECTOR_DB_CLIENT

log = logging.getLogger(__name__)


@dataclass
class DeletionReport:
    """Tracks what was deleted across all layers."""

    db_records: Dict[str, int] = field(default_factory=dict)
    storage_files: int = 0
    vector_collections: int = 0
    vector_documents: int = 0  # Vectors deleted by filter
    errors: List[str] = field(default_factory=list)

    def add_db(self, table: str, count: int = 1):
        self.db_records[table] = self.db_records.get(table, 0) + count

    def add_error(self, error: str):
        self.errors.append(error)
        log.warning(f'Deletion error: {error}')

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0

    @property
    def total_db_records(self) -> int:
        return sum(self.db_records.values())


class DeletionService:
    """
    Centralized service for cascade deletion across DB, Storage, and Vector DB.

    Deletion order is always: Vectors -> Storage -> DB
    This ensures we can retry cleanup if storage/vector fails (DB still has references).
    """

    @staticmethod
    async def delete_file(file_id: str, deleted_file_ids: Optional[Set[str]] = None) -> DeletionReport:
        """
        Delete a file from all layers:
        1. Remove vectors from all knowledge base collections containing this file
        2. Delete the file-{id} vector collection
        3. Delete physical file from storage
        4. Delete file record from database

        Args:
            file_id: The file ID to delete
            deleted_file_ids: Optional set to track already-deleted files (prevents
                              duplicate deletion attempts during cascade operations)
        """
        report = DeletionReport()

        # Skip if already deleted in this cascade operation
        if deleted_file_ids is not None and file_id in deleted_file_ids:
            return report

        # Get file first - we need the path and hash
        file = await Files.get_file_by_id(file_id)
        if not file:
            report.add_error(f'File {file_id} not found in database')
            return report

        # 1. Find all knowledge bases containing this file and remove vectors
        knowledge_files = await Knowledges.get_knowledge_files_by_file_id(file_id) if configured() else []
        for kf in knowledge_files:
            try:
                # Delete vectors by file_id from the knowledge collection
                await ASYNC_VECTOR_DB_CLIENT.delete(collection_name=kf.knowledge_id, filter={'file_id': file_id})
                report.vector_documents += 1

                # Delete by hash as well (duplicates may exist)
                if file.hash:
                    await ASYNC_VECTOR_DB_CLIENT.delete(collection_name=kf.knowledge_id, filter={'hash': file.hash})
            except Exception as e:
                report.add_error(f'Failed to remove vectors from knowledge {kf.knowledge_id}: {e}')

        # 2. Delete the file's own vector collection
        try:
            file_collection = f'file-{file_id}'
            if await ASYNC_VECTOR_DB_CLIENT.has_collection(collection_name=file_collection):
                await ASYNC_VECTOR_DB_CLIENT.delete_collection(collection_name=file_collection)
                report.vector_collections += 1
        except Exception as e:
            report.add_error(f'Failed to delete vector collection file-{file_id}: {e}')

        # 3. Delete from storage
        if file.path:
            try:
                Storage.delete_file(file.path)
                report.storage_files += 1
            except Exception as e:
                report.add_error(f'Failed to delete storage file {file.path}: {e}')

        # 4. Delete from database (FK cascades handle junction tables)
        try:
            result = await Files.delete_file_by_id(file_id)
            if result:
                report.add_db('file')
                # Track successful deletion to prevent duplicate attempts
                if deleted_file_ids is not None:
                    deleted_file_ids.add(file_id)
            else:
                report.add_error(f'Failed to delete file {file_id} from database')
        except Exception as e:
            report.add_error(f'Database error deleting file {file_id}: {e}')

        return report

    @staticmethod
    async def delete_orphaned_files_batch(file_ids: list[str], force: bool = False) -> DeletionReport:
        """
        Batch-delete files that are no longer referenced by any KB.

        1. Find which file_ids still have KB references (1 SQL query)
        2. Load orphaned file records (1 SQL query)
        3. Delete per-file vector collections (concurrent via asyncio.gather)
        4. Batch-delete from storage (1 call per 1000 files)
        5. Batch-delete DB records (1 SQL query)

        Args:
            file_ids: List of file IDs to check and potentially delete.
            force: If True, skip the KB reference check and delete all files.
                   Use when deleting an entire user (all their KBs are already gone).
        """
        report = DeletionReport()
        if not file_ids:
            return report

        # 1. Find orphaned files (single query instead of N queries)
        if force:
            orphaned_ids = file_ids
        else:
            from open_webui.models.chats import Chats

            kb_referenced = await Knowledges.get_referenced_file_ids(file_ids) if configured() else set()
            chat_referenced = await Chats.get_referenced_file_ids(file_ids)
            referenced_ids = kb_referenced | chat_referenced
            orphaned_ids = [fid for fid in file_ids if fid not in referenced_ids]

        if not orphaned_ids:
            return report

        log.info(f'Batch cleanup: {len(orphaned_ids)} orphaned files out of {len(file_ids)} total')

        # 2. Load file records for orphaned files (single query).
        #    We need paths BEFORE deleting DB records so we can find storage files.
        orphaned_files = await Files.get_files_by_ids(orphaned_ids)
        file_paths = [f.path for f in orphaned_files if f.path]

        # Deletion order: derived data first, source-of-truth (DB) last.
        # This ensures retryability: if we crash mid-deletion, the DB
        # records still exist so a cleanup worker can find what's left.

        # 3. Delete per-file vector collections concurrently.
        #    ASYNC_VECTOR_DB_CLIENT bridges to the sync client via asyncio.to_thread,
        #    so asyncio.gather + the default executor gives us parallelism.
        async def _delete_collection(file_id: str) -> tuple[str, Optional[str]]:
            try:
                await ASYNC_VECTOR_DB_CLIENT.delete_collection(collection_name=f'file-{file_id}')
                return file_id, None
            except Exception as e:
                return file_id, str(e)

        results = await asyncio.gather(*(_delete_collection(fid) for fid in orphaned_ids))
        for fid, error in results:
            if error:
                report.add_error(f'Failed to delete vector collection file-{fid}: {error}')
            else:
                report.vector_collections += 1

        # 4. Delete from storage (raw files)
        if file_paths:
            try:
                deleted = Storage.delete_files(file_paths)
                report.storage_files += deleted
            except Exception as e:
                report.add_error(f'Storage batch delete failed: {e}')

        # 5. Delete DB records last (source of truth for retry)
        if orphaned_ids:
            try:
                await Files.delete_files_by_ids(orphaned_ids)
                report.add_db('file', len(orphaned_ids))
            except Exception as e:
                report.add_error(f'DB batch delete failed: {e}')

        return report

    @staticmethod
    async def delete_directory(
        knowledge_id: str,
        directory_id: str,
        move_files_to_parent: bool = True,
    ) -> DeletionReport:
        """
        Delete a KB directory with full fork-cascade parity (P2-8 ledger #4).

        - move_files_to_parent=True: contained files re-home to the parent
          directory (or root); only the directory rows go.
        - move_files_to_parent=False ("delete directory + contents"): every
          file in the subtree is deleted exactly like a single-file delete —
          vectors by file_id + hash in this KB's collection, then the orphan
          batch (file-{id} collections, storage objects, DB rows). Files still
          referenced by another KB or chat survive with only this KB's link
          and vectors removed.

        The directory must belong to knowledge_id — the KB-scope guard for the
        /sync/cleanup dir branch lives here so every caller gets it.
        """
        report = DeletionReport()

        directory = await Knowledges.get_directory_by_id(directory_id)
        if not directory:
            report.add_error(f'Directory {directory_id} not found')
            return report
        if directory.knowledge_id != knowledge_id:
            report.add_error(f'Directory {directory_id} does not belong to knowledge {knowledge_id}')
            return report

        subtree_file_ids: list[str] = []
        if not move_files_to_parent:
            subtree_file_ids = await Knowledges.get_file_ids_in_directory_subtree(knowledge_id, directory_id)
            # Vectors first (deletion order: Vectors -> Storage -> DB). Remove
            # this KB's vectors by file_id and hash — the same double delete
            # the single-file path does.
            files = await Files.get_files_by_ids(subtree_file_ids) if subtree_file_ids else []
            for file in files:
                try:
                    await ASYNC_VECTOR_DB_CLIENT.delete(collection_name=knowledge_id, filter={'file_id': file.id})
                    report.vector_documents += 1
                    if file.hash:
                        await ASYNC_VECTOR_DB_CLIENT.delete(collection_name=knowledge_id, filter={'hash': file.hash})
                except Exception as e:
                    report.add_error(f'Failed to remove vectors for file {file.id} from knowledge {knowledge_id}: {e}')

        # Directory + knowledge_file join rows (and, for the delete-contents
        # mode, the subtree link rows) — the reference check below needs the
        # links gone first to see which files became orphans.
        if not await Knowledges.delete_directory(directory_id, move_files_to_parent=move_files_to_parent):
            report.add_error(f'Failed to delete directory {directory_id}')
            return report
        report.add_db('knowledge_directory')

        if subtree_file_ids:
            batch_report = await DeletionService.delete_orphaned_files_batch(subtree_file_ids)
            for table, count in batch_report.db_records.items():
                report.add_db(table, count)
            report.storage_files += batch_report.storage_files
            report.vector_collections += batch_report.vector_collections
            report.vector_documents += batch_report.vector_documents
            report.errors.extend(batch_report.errors)

        return report

    @staticmethod
    async def delete_chat(chat_id: str, user_id: str) -> DeletionReport:
        """
        Delete a chat and all associated files/vectors.

        1. Get files associated with chat (via ChatFile junction)
        2. For each file: delete vectors, storage, and DB record
        3. Delete chat record (ChatFile junction cascades via FK)
        """
        from open_webui.models.chats import Chats
        from open_webui.models.tags import Tags
        from open_webui.soev.agent_threads import delete_chat_threads

        report = DeletionReport()

        # Get chat to check it exists and get metadata (includes soft-deleted)
        chat = await Chats.get_chat_by_id_unfiltered(chat_id)
        if not chat:
            report.add_error(f'Chat {chat_id} not found')
            return report

        # 1. Get files associated with this chat
        chat_files = await Chats.get_files_by_chat_id(chat_id)

        # 2. Delete each file (this handles vectors and storage)
        for chat_file in chat_files:
            file_report = await DeletionService.delete_file(chat_file.file_id)
            # Merge reports
            for table, count in file_report.db_records.items():
                report.add_db(table, count)
            report.storage_files += file_report.storage_files
            report.vector_collections += file_report.vector_collections
            report.vector_documents += file_report.vector_documents
            report.errors.extend(file_report.errors)

        # 3. Clean up orphaned tags (same logic as router)
        # Tags are deleted if this was the only chat using them
        if chat.meta and chat.meta.get('tags'):
            for tag_name in chat.meta.get('tags', []):
                try:
                    # Use actual count query, not meta.count which may be stale
                    if await Chats.count_chats_by_tag_name_and_user_id(tag_name, user_id) == 1:
                        await Tags.delete_tag_by_name_and_user_id(tag_name, user_id)
                        report.add_db('tag')
                except Exception as e:
                    report.add_error(f'Failed to cleanup tag {tag_name}: {e}')

        # [Gradient] Its agent threads in soev-api, unless another chat of the user still bookmarks them
        try:
            if kept := await delete_chat_threads(chat.user_id, chat_id):
                report.add_error(f'Agent threads not deleted: {len(kept)}')
        except Exception as e:
            report.add_error(f'Failed to delete agent threads of chat {chat_id}: {e}')

        # 4. Delete chat (ChatFile junction cascades via FK)
        try:
            result = await Chats.delete_chat_by_id(chat_id)
            if result:
                report.add_db('chat')
            else:
                report.add_error(f'Failed to delete chat {chat_id}')
        except Exception as e:
            report.add_error(f'Database error deleting chat {chat_id}: {e}')

        return report

    @staticmethod
    async def delete_knowledge(
        knowledge_id: str,
        delete_files: bool = False,
        deleted_file_ids: Optional[Set[str]] = None,
    ) -> DeletionReport:
        """
        Delete a knowledge base and optionally its files.

        1. Delete the knowledge vector collection
        2. If delete_files: delete each associated file (calls delete_file)
        3. Update models that reference this knowledge base
        4. Delete knowledge record (knowledge_file junction cascades via FK)

        Args:
            knowledge_id: The knowledge base ID to delete
            delete_files: Whether to also delete associated files
            deleted_file_ids: Optional set to track already-deleted files
        """
        from open_webui.models.models import Models

        report = DeletionReport()

        knowledge = await Knowledges.get_knowledge_by_id_unfiltered(knowledge_id)
        if not knowledge:
            report.add_error(f'Knowledge {knowledge_id} not found')
            return report

        # 1. Delete the knowledge vector collection
        try:
            if await ASYNC_VECTOR_DB_CLIENT.has_collection(collection_name=knowledge_id):
                await ASYNC_VECTOR_DB_CLIENT.delete_collection(collection_name=knowledge_id)
                report.vector_collections += 1
        except Exception as e:
            report.add_error(f'Failed to delete knowledge vector collection: {e}')

        # 2. Optionally delete associated files
        if delete_files:
            knowledge_files = await Knowledges.get_files_by_id(knowledge_id)
            for file in knowledge_files:
                file_report = await DeletionService.delete_file(file.id, deleted_file_ids)
                # Merge reports
                for table, count in file_report.db_records.items():
                    report.add_db(table, count)
                report.storage_files += file_report.storage_files
                report.vector_collections += file_report.vector_collections
                report.vector_documents += file_report.vector_documents
                report.errors.extend(file_report.errors)

        # 3. Update models that reference this knowledge base
        try:
            from open_webui.models.models import ModelForm

            models = await Models.get_all_models()
            for model in models:
                if model.meta and hasattr(model.meta, 'knowledge'):
                    knowledge_list = model.meta.knowledge or []
                    # Knowledge items are objects with 'id' field
                    updated_knowledge = [k for k in knowledge_list if k.get('id') != knowledge_id]

                    if len(updated_knowledge) != len(knowledge_list):
                        model.meta.knowledge = updated_knowledge
                        # Create ModelForm for update
                        model_form = ModelForm(
                            id=model.id,
                            name=model.name,
                            base_model_id=model.base_model_id,
                            meta=model.meta,
                            params=model.params,
                            access_control=model.access_control,
                            is_active=model.is_active,
                        )
                        await Models.update_model_by_id(model.id, model_form)
                        log.info(f'Removed knowledge {knowledge_id} from model {model.id}')
        except Exception as e:
            report.add_error(f'Failed to update models referencing knowledge: {e}')

        # 4. Delete knowledge record (knowledge_file junction cascades via FK)
        try:
            result = await Knowledges.delete_knowledge_by_id(knowledge_id)
            if result:
                report.add_db('knowledge')
            else:
                report.add_error(f'Failed to delete knowledge {knowledge_id}')
        except SoevNotConfigured:
            raise
        except Exception as e:
            report.add_error(f'Database error deleting knowledge {knowledge_id}: {e}')

        return report

    @staticmethod
    async def delete_memories(user_id: str) -> DeletionReport:
        """
        Delete all memories for a user.

        1. Delete the user-memory-{user_id} vector collection
        2. Delete all memory records for the user
        """
        from open_webui.models.memories import Memories

        report = DeletionReport()

        # 1. Delete the user memory vector collection
        try:
            collection_name = f'user-memory-{user_id}'
            if await ASYNC_VECTOR_DB_CLIENT.has_collection(collection_name=collection_name):
                await ASYNC_VECTOR_DB_CLIENT.delete_collection(collection_name=collection_name)
                report.vector_collections += 1
        except Exception as e:
            report.add_error(f'Failed to delete memory vector collection: {e}')

        # 2. Delete all memory records
        try:
            result = await Memories.delete_memories_by_user_id(user_id)
            if result:
                report.add_db('memory')
        except Exception as e:
            report.add_error(f'Failed to delete memories from database: {e}')

        return report

    @staticmethod
    async def delete_user(user_id: str) -> DeletionReport:
        """Offboard a user without swallowing revocation or group failures."""
        from open_webui.services.deletion.offboarding import OffboardingOptions, offboard_user

        count = await offboard_user(user_id, actor_id='system:deletion', options=OffboardingOptions())
        report = DeletionReport()
        report.add_db('user', count)
        return report
