from dataclasses import dataclass, field
from typing import Dict, List, Set, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging

from open_webui.models.files import Files
from open_webui.models.knowledge import Knowledges
from open_webui.storage.provider import Storage
from open_webui.retrieval.vector.factory import VECTOR_DB_CLIENT

log = logging.getLogger(__name__)

# Shared pool for vector DB cleanup operations.
# Bounded to 10 workers total across ALL concurrent deletions to avoid
# overwhelming Weaviate when multiple users delete KBs simultaneously.
_vector_cleanup_pool = ThreadPoolExecutor(max_workers=10, thread_name_prefix="vec-cleanup")


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
        log.warning(f"Deletion error: {error}")

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
    def delete_file(
        file_id: str, deleted_file_ids: Optional[Set[str]] = None
    ) -> DeletionReport:
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
        file = Files.get_file_by_id(file_id)
        if not file:
            report.add_error(f"File {file_id} not found in database")
            return report

        # 1. Find all knowledge bases containing this file and remove vectors
        knowledge_files = Knowledges.get_knowledge_files_by_file_id(file_id)
        for kf in knowledge_files:
            try:
                # Delete vectors by file_id from the knowledge collection
                VECTOR_DB_CLIENT.delete(
                    collection_name=kf.knowledge_id, filter={"file_id": file_id}
                )
                report.vector_documents += 1

                # Delete by hash as well (duplicates may exist)
                if file.hash:
                    VECTOR_DB_CLIENT.delete(
                        collection_name=kf.knowledge_id, filter={"hash": file.hash}
                    )
            except Exception as e:
                report.add_error(
                    f"Failed to remove vectors from knowledge {kf.knowledge_id}: {e}"
                )

        # 2. Delete the file's own vector collection
        try:
            file_collection = f"file-{file_id}"
            if VECTOR_DB_CLIENT.has_collection(collection_name=file_collection):
                VECTOR_DB_CLIENT.delete_collection(collection_name=file_collection)
                report.vector_collections += 1
        except Exception as e:
            report.add_error(f"Failed to delete vector collection file-{file_id}: {e}")

        # 3. Delete from storage
        if file.path:
            try:
                Storage.delete_file(file.path)
                report.storage_files += 1
            except Exception as e:
                report.add_error(f"Failed to delete storage file {file.path}: {e}")

        # 4. Delete from database (FK cascades handle junction tables)
        try:
            result = Files.delete_file_by_id(file_id)
            if result:
                report.add_db("file")
                # Track successful deletion to prevent duplicate attempts
                if deleted_file_ids is not None:
                    deleted_file_ids.add(file_id)
            else:
                report.add_error(f"Failed to delete file {file_id} from database")
        except Exception as e:
            report.add_error(f"Database error deleting file {file_id}: {e}")

        return report

    @staticmethod
    def delete_orphaned_files_batch(
        file_ids: list[str], force: bool = False
    ) -> DeletionReport:
        """
        Batch-delete files that are no longer referenced by any KB.

        1. Find which file_ids still have KB references (1 SQL query)
        2. Load orphaned file records (1 SQL query)
        3. Delete per-file vector collections (N calls)
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

            kb_referenced = Knowledges.get_referenced_file_ids(file_ids)
            chat_referenced = Chats.get_referenced_file_ids(file_ids)
            referenced_ids = kb_referenced | chat_referenced
            orphaned_ids = [fid for fid in file_ids if fid not in referenced_ids]

        if not orphaned_ids:
            return report

        log.info(
            f"Batch cleanup: {len(orphaned_ids)} orphaned files "
            f"out of {len(file_ids)} total"
        )

        # 2. Load file records for orphaned files (single query).
        #    We need paths BEFORE deleting DB records so we can find storage files.
        orphaned_files = Files.get_files_by_ids(orphaned_ids)
        file_paths = [f.path for f in orphaned_files if f.path]

        # Deletion order: derived data first, source-of-truth (DB) last.
        # This ensures retryability: if we crash mid-deletion, the DB
        # records still exist so a cleanup worker can find what's left.

        # 3. Delete per-file vector collections (derived, slowest step)
        def _delete_collection(file_id: str) -> tuple[str, Optional[str]]:
            try:
                VECTOR_DB_CLIENT.delete_collection(
                    collection_name=f"file-{file_id}"
                )
                return file_id, None
            except Exception as e:
                return file_id, str(e)

        futures = {
            _vector_cleanup_pool.submit(_delete_collection, fid): fid
            for fid in orphaned_ids
        }
        for future in as_completed(futures):
            fid, error = future.result()
            if error:
                report.add_error(
                    f"Failed to delete vector collection file-{fid}: {error}"
                )
            else:
                report.vector_collections += 1

        # 4. Delete from storage (raw files)
        if file_paths:
            try:
                deleted = Storage.delete_files(file_paths)
                report.storage_files += deleted
            except Exception as e:
                report.add_error(f"Storage batch delete failed: {e}")

        # 5. Delete DB records last (source of truth for retry)
        if orphaned_ids:
            try:
                Files.delete_files_by_ids(orphaned_ids)
                report.add_db("file", len(orphaned_ids))
            except Exception as e:
                report.add_error(f"DB batch delete failed: {e}")

        return report

    @staticmethod
    def delete_chat(chat_id: str, user_id: str) -> DeletionReport:
        """
        Delete a chat and all associated files/vectors.

        1. Get files associated with chat (via ChatFile junction)
        2. For each file: delete vectors, storage, and DB record
        3. Delete chat record (ChatFile junction cascades via FK)
        """
        from open_webui.models.chats import Chats
        from open_webui.models.tags import Tags

        report = DeletionReport()

        # Get chat to check it exists and get metadata (includes soft-deleted)
        chat = Chats.get_chat_by_id_unfiltered(chat_id)
        if not chat:
            report.add_error(f"Chat {chat_id} not found")
            return report

        # 1. Get files associated with this chat
        chat_files = Chats.get_files_by_chat_id(chat_id)

        # 2. Delete each file (this handles vectors and storage)
        for chat_file in chat_files:
            file_report = DeletionService.delete_file(chat_file.file_id)
            # Merge reports
            for table, count in file_report.db_records.items():
                report.add_db(table, count)
            report.storage_files += file_report.storage_files
            report.vector_collections += file_report.vector_collections
            report.vector_documents += file_report.vector_documents
            report.errors.extend(file_report.errors)

        # 3. Clean up orphaned tags (same logic as router)
        # Tags are deleted if this was the only chat using them
        if chat.meta and chat.meta.get("tags"):
            for tag_name in chat.meta.get("tags", []):
                try:
                    # Use actual count query, not meta.count which may be stale
                    if Chats.count_chats_by_tag_name_and_user_id(tag_name, user_id) == 1:
                        Tags.delete_tag_by_name_and_user_id(tag_name, user_id)
                        report.add_db("tag")
                except Exception as e:
                    report.add_error(f"Failed to cleanup tag {tag_name}: {e}")

        # 4. Delete chat (ChatFile junction cascades via FK)
        try:
            result = Chats.delete_chat_by_id(chat_id)
            if result:
                report.add_db("chat")
            else:
                report.add_error(f"Failed to delete chat {chat_id}")
        except Exception as e:
            report.add_error(f"Database error deleting chat {chat_id}: {e}")

        return report

    @staticmethod
    def delete_knowledge(
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

        knowledge = Knowledges.get_knowledge_by_id_unfiltered(knowledge_id)
        if not knowledge:
            report.add_error(f"Knowledge {knowledge_id} not found")
            return report

        # 1. Delete the knowledge vector collection
        try:
            if VECTOR_DB_CLIENT.has_collection(collection_name=knowledge_id):
                VECTOR_DB_CLIENT.delete_collection(collection_name=knowledge_id)
                report.vector_collections += 1
        except Exception as e:
            report.add_error(f"Failed to delete knowledge vector collection: {e}")

        # 2. Optionally delete associated files
        if delete_files:
            knowledge_files = Knowledges.get_files_by_id(knowledge_id)
            for file in knowledge_files:
                file_report = DeletionService.delete_file(file.id, deleted_file_ids)
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

            models = Models.get_all_models()
            for model in models:
                if model.meta and hasattr(model.meta, "knowledge"):
                    knowledge_list = model.meta.knowledge or []
                    # Knowledge items are objects with 'id' field
                    updated_knowledge = [k for k in knowledge_list if k.get("id") != knowledge_id]

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
                        Models.update_model_by_id(model.id, model_form)
                        log.info(f"Removed knowledge {knowledge_id} from model {model.id}")
        except Exception as e:
            report.add_error(f"Failed to update models referencing knowledge: {e}")

        # 4. Delete knowledge record (knowledge_file junction cascades via FK)
        try:
            result = Knowledges.delete_knowledge_by_id(knowledge_id)
            if result:
                report.add_db("knowledge")
            else:
                report.add_error(f"Failed to delete knowledge {knowledge_id}")
        except Exception as e:
            report.add_error(f"Database error deleting knowledge {knowledge_id}: {e}")

        return report

    @staticmethod
    def delete_memories(user_id: str) -> DeletionReport:
        """
        Delete all memories for a user.

        1. Delete the user-memory-{user_id} vector collection
        2. Delete all memory records for the user
        """
        from open_webui.models.memories import Memories

        report = DeletionReport()

        # 1. Delete the user memory vector collection
        try:
            collection_name = f"user-memory-{user_id}"
            if VECTOR_DB_CLIENT.has_collection(collection_name=collection_name):
                VECTOR_DB_CLIENT.delete_collection(collection_name=collection_name)
                report.vector_collections += 1
        except Exception as e:
            report.add_error(f"Failed to delete memory vector collection: {e}")

        # 2. Delete all memory records
        try:
            result = Memories.delete_memories_by_user_id(user_id)
            if result:
                report.add_db("memory")
        except Exception as e:
            report.add_error(f"Failed to delete memories from database: {e}")

        return report

    @staticmethod
    def delete_user(user_id: str) -> DeletionReport:
        """
        Delete a user and ALL associated data.

        Fast path (synchronous):
        1. Delete memories (single vector collection + DB)
        2. Soft-delete all user's knowledge bases (instant)
        3. Soft-delete all user's chats (instant)
        4. Delete simple DB tables (tags, folders, prompts, etc.)
        5. Delete auth and user records

        The cleanup worker handles the expensive parts:
        - KB vector collections, model reference updates, file cleanup
        - Chat file cleanup (vectors, storage, DB)
        """
        from open_webui.models.auths import Auths
        from open_webui.models.users import Users
        from open_webui.models.chats import Chats
        from open_webui.models.groups import Groups
        from open_webui.models.tags import Tags
        from open_webui.models.folders import Folders
        from open_webui.models.prompts import Prompts
        from open_webui.models.tools import Tools
        from open_webui.models.functions import Functions
        from open_webui.models.models import Models
        from open_webui.models.feedbacks import Feedbacks
        from open_webui.models.notes import Notes
        from open_webui.models.channels import Channels
        from open_webui.models.messages import Messages
        from open_webui.models.oauth_sessions import OAuthSessions

        report = DeletionReport()

        # Verify user exists
        user = Users.get_user_by_id(user_id)
        if not user:
            report.add_error(f"User {user_id} not found")
            return report

        # 1. Delete memories (vectors + DB) — fast, single collection
        memory_report = DeletionService.delete_memories(user_id)
        report.vector_collections += memory_report.vector_collections
        for table, count in memory_report.db_records.items():
            report.add_db(table, count)
        report.errors.extend(memory_report.errors)

        # 2. Soft-delete all knowledge bases (instant — cleanup worker handles the rest)
        try:
            kb_count = Knowledges.soft_delete_by_user_id(user_id)
            report.add_db("knowledge_soft_deleted", kb_count)
        except Exception as e:
            report.add_error(f"Failed to soft-delete knowledge bases: {e}")

        # 3. Soft-delete all chats (instant — cleanup worker handles the rest)
        try:
            chat_count = Chats.soft_delete_by_user_id(user_id)
            report.add_db("chat_soft_deleted", chat_count)
        except Exception as e:
            report.add_error(f"Failed to soft-delete chats: {e}")

        # 4. Delete remaining tables with user_id
        # Order matters: delete dependents before parents

        # Messages and reactions (channel content)
        try:
            Messages.delete_messages_by_user_id(user_id)
            report.add_db("message")
        except Exception as e:
            report.add_error(f"Failed to delete messages: {e}")

        # Channel memberships
        try:
            Channels.delete_member_by_user_id(user_id)
            report.add_db("channel_member")
        except Exception as e:
            report.add_error(f"Failed to delete channel memberships: {e}")

        # Channels owned by user
        try:
            Channels.delete_channels_by_user_id(user_id)
            report.add_db("channel")
        except Exception as e:
            report.add_error(f"Failed to delete channels: {e}")

        # Tags
        try:
            Tags.delete_tags_by_user_id(user_id)
            report.add_db("tag")
        except Exception as e:
            report.add_error(f"Failed to delete tags: {e}")

        # Folders
        try:
            Folders.delete_folders_by_user_id(user_id)
            report.add_db("folder")
        except Exception as e:
            report.add_error(f"Failed to delete folders: {e}")

        # Prompts
        try:
            Prompts.delete_prompts_by_user_id(user_id)
            report.add_db("prompt")
        except Exception as e:
            report.add_error(f"Failed to delete prompts: {e}")

        # Tools
        try:
            Tools.delete_tools_by_user_id(user_id)
            report.add_db("tool")
        except Exception as e:
            report.add_error(f"Failed to delete tools: {e}")

        # Functions
        try:
            Functions.delete_functions_by_user_id(user_id)
            report.add_db("function")
        except Exception as e:
            report.add_error(f"Failed to delete functions: {e}")

        # Models (user's custom models)
        try:
            Models.delete_models_by_user_id(user_id)
            report.add_db("model")
        except Exception as e:
            report.add_error(f"Failed to delete models: {e}")

        # Feedbacks
        try:
            Feedbacks.delete_feedbacks_by_user_id(user_id)
            report.add_db("feedback")
        except Exception as e:
            report.add_error(f"Failed to delete feedbacks: {e}")

        # Notes
        try:
            Notes.delete_notes_by_user_id(user_id)
            report.add_db("note")
        except Exception as e:
            report.add_error(f"Failed to delete notes: {e}")

        # OAuth sessions
        try:
            OAuthSessions.delete_sessions_by_user_id(user_id)
            report.add_db("oauth_session")
        except Exception as e:
            report.add_error(f"Failed to delete OAuth sessions: {e}")

        # Groups - remove from all groups
        try:
            Groups.remove_user_from_all_groups(user_id)
            report.add_db("group_member")
        except Exception as e:
            report.add_error(f"Failed to remove from groups: {e}")

        # Groups owned by user
        try:
            Groups.delete_groups_by_user_id(user_id)
            report.add_db("group")
        except Exception as e:
            report.add_error(f"Failed to delete owned groups: {e}")

        # API keys
        try:
            Users.delete_user_api_key_by_id(user_id)
            report.add_db("api_key")
        except Exception as e:
            report.add_error(f"Failed to delete API keys: {e}")

        # 5. Finally delete auth and user records
        try:
            Auths.delete_auth_by_id(user_id)
            report.add_db("auth")
            report.add_db("user")
        except Exception as e:
            report.add_error(f"Failed to delete auth/user record: {e}")

        return report
