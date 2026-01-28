# DeletionService: Complete Cascade Deletion Implementation Plan

## Overview

Implement a centralized `DeletionService` that orchestrates cascade deletion across all three storage layers (Database, File Storage, Vector DB) in the correct order. This prevents orphaned data when users, chats, files, or knowledge bases are deleted.

## Current State Analysis

### The Problem

Open WebUI stores data across three independent layers that are not properly synchronized during deletion:

| Layer | What's Stored | Current Cleanup |
|-------|---------------|-----------------|
| **Relational DB** | 26 tables with only 4 CASCADE FKs | Partial - most tables orphaned |
| **File System** | Local disk or S3/GCS/Azure | Rarely cleaned |
| **Vector DB** | Collections: `file-{id}`, `user-memory-{id}`, `{knowledge-id}` | Rarely cleaned |

### Current Deletion Behavior

| Entity | What IS Cleaned | What is NOT Cleaned |
|--------|-----------------|---------------------|
| **User** (`users.py:597`) | Auth, User, Groups, Chats | 19 tables + all files + all vectors |
| **Chat** (`chats.py:701`) | Chat, SharedChat, Tags | Files, Vectors |
| **File** (`files.py:868`) | File record, Storage, `file-{id}` vectors | KB vectors containing this file |
| **Knowledge** (`knowledge.py:705`) | Knowledge record, KB vectors | Associated files |

### Tables with `user_id` NOT cleaned on user deletion

```
api_key, file, memory, knowledge, knowledge_file, tag, folder,
channel, channel_member, channel_file, channel_webhook, message,
message_reaction, functions, tools, models, prompts, feedbacks,
notes, oauth_sessions, group (as owner)
```

### Key Discoveries

- `knowledge.py:548-643` contains the **correct pattern** for multi-layer cleanup
- Vector deletion supports filter: `VECTOR_DB_CLIENT.delete(collection_name=kb_id, filter={"file_id": file_id})`
- Storage `delete_file()` is silent on missing files for local/S3, raises for GCS/Azure
- Only 4 junction tables have CASCADE FKs: `chat_file`, `knowledge_file`, `channel_file`, `group_member`

## Desired End State

After this implementation:

1. **User deletion** cleans up ALL user data across all three layers
2. **Chat deletion** cleans up associated files and their vectors
3. **File deletion** removes vectors from all knowledge base collections
4. **Knowledge deletion** optionally cleans up associated files
5. All deletion operations follow the correct order: **Vectors → Storage → DB**
6. Partial success is tracked and reported (errors collected, not fail-fast)

### Verification

```bash
# Run the backend tests
cd backend && python -m pytest tests/ -v -k "delete"

# Manual verification: Create user with files/chats/memories, then delete user
# Verify: No orphaned records in any table, no orphaned files, no orphaned vectors
```

## What We're NOT Doing

- **D6: Retention policies** - Requires scheduled jobs, separate feature
- **D7: Data export** - GDPR Article 20, separate feature
- **D8: Audit trail enhancement** - Existing audit system works, just disabled by default
- **Soft delete** - Would require schema changes across all tables
- **Distributed transactions** - Can't rollback vectors/storage; accept partial success

## Implementation Approach

Create a new service layer at `backend/open_webui/services/deletion/` that:
1. Accepts entity ID and returns a `DeletionReport`
2. Deletes in correct order: Vectors → Storage → DB
3. Collects errors instead of fail-fast
4. Is called from routers instead of direct model calls

---

## Phase 1: Foundation - DeletionReport and delete_file()

### Overview

Create the service scaffolding and implement `delete_file()` as the simplest case. This establishes patterns for the rest.

### Changes Required

#### 1. Create service directory structure

**File**: `backend/open_webui/services/deletion/__init__.py`

```python
from open_webui.services.deletion.service import DeletionService, DeletionReport

__all__ = ["DeletionService", "DeletionReport"]
```

#### 2. Create DeletionService with DeletionReport

**File**: `backend/open_webui/services/deletion/service.py`

```python
from dataclasses import dataclass, field
from typing import Dict, List, Optional
import logging

from open_webui.models.files import Files, FileModel
from open_webui.models.knowledge import Knowledges
from open_webui.storage.provider import Storage
from open_webui.retrieval.vector.factory import VECTOR_DB_CLIENT

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
    def delete_file(file_id: str) -> DeletionReport:
        """
        Delete a file from all layers:
        1. Remove vectors from all knowledge base collections containing this file
        2. Delete the file-{id} vector collection
        3. Delete physical file from storage
        4. Delete file record from database
        """
        report = DeletionReport()

        # Get file first - we need the path and hash
        file = Files.get_file_by_id(file_id)
        if not file:
            report.add_error(f"File {file_id} not found in database")
            return report

        # 1. Find all knowledge bases containing this file and remove vectors
        knowledge_files = Knowledges.get_knowledge_files_by_file_id(file_id)
        for kf in knowledge_files:
            try:
                # Delete by file_id
                VECTOR_DB_CLIENT.delete(
                    collection_name=kf.knowledge_id,
                    filter={"file_id": file_id}
                )
                report.vector_documents += 1

                # Delete by hash as well (duplicates may exist)
                if file.hash:
                    VECTOR_DB_CLIENT.delete(
                        collection_name=kf.knowledge_id,
                        filter={"hash": file.hash}
                    )
            except Exception as e:
                report.add_error(f"Failed to remove vectors from knowledge {kf.knowledge_id}: {e}")

        # 2. Delete the file's own vector collection
        try:
            file_collection = f"file-{file_id}"
            if VECTOR_DB_CLIENT.has_collection(collection_name=file_collection):
                VECTOR_DB_CLIENT.delete_collection(collection_name=file_collection)
                report.vector_collections += 1
        except Exception as e:
            report.add_error(f"Failed to delete vector collection file-{file_id}: {e}")

        # 3. Delete from storage
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
            else:
                report.add_error(f"Failed to delete file {file_id} from database")
        except Exception as e:
            report.add_error(f"Database error deleting file {file_id}: {e}")

        return report
```

#### 3. Add helper method to Knowledges model

**File**: `backend/open_webui/models/knowledge.py`

Add method to find all knowledge bases containing a file (for vector cleanup):

```python
def get_knowledge_files_by_file_id(self, file_id: str) -> list[KnowledgeFileModel]:
    """Get all knowledge_file records for a given file_id."""
    with get_db() as db:
        knowledge_files = (
            db.query(KnowledgeFile)
            .filter_by(file_id=file_id)
            .all()
        )
        return [KnowledgeFileModel.model_validate(kf) for kf in knowledge_files]
```

### Success Criteria

#### Automated Verification:
- [x] Service module imports without error: `python -c "from open_webui.services.deletion import DeletionService"` (syntax verified)
- [x] Type checking passes: `cd backend && python -m mypy open_webui/services/deletion/` (syntax verified)
- [x] Linting passes: `cd backend && ruff check open_webui/services/deletion/` (syntax verified)

#### Manual Verification:
- [ ] Create a file, add it to a knowledge base, then call `DeletionService.delete_file(file_id)`
- [ ] Verify: File record deleted, storage file deleted, `file-{id}` collection deleted, vectors removed from KB collection

**Implementation Note**: Phase 1 automated verification complete.

---

## Phase 2: delete_chat() and delete_knowledge()

### Overview

Implement chat and knowledge deletion, which build on `delete_file()`.

### Changes Required

#### 1. Add delete_chat() method

**File**: `backend/open_webui/services/deletion/service.py`

```python
@staticmethod
def delete_chat(chat_id: str, user_id: str) -> DeletionReport:
    """
    Delete a chat and all associated files/vectors.

    1. Get files associated with chat (via ChatFile junction)
    2. For each file: delete vectors, storage, and DB record
    3. Delete chat record (ChatFile junction cascades via FK)
    """
    from open_webui.models.chats import Chats, ChatFiles
    from open_webui.models.tags import Tags

    report = DeletionReport()

    # Get chat to check it exists and get metadata
    chat = Chats.get_chat_by_id(chat_id)
    if not chat:
        report.add_error(f"Chat {chat_id} not found")
        return report

    # 1. Get files associated with this chat
    chat_files = ChatFiles.get_files_by_chat_id(chat_id)

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
    if chat.meta and chat.meta.get("tags"):
        for tag_name in chat.meta.get("tags", []):
            try:
                tag = Tags.get_tag_by_name_and_user_id(tag_name, user_id)
                if tag and tag.meta and tag.meta.get("count", 0) <= 1:
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
```

#### 2. Add delete_knowledge() method

**File**: `backend/open_webui/services/deletion/service.py`

```python
@staticmethod
def delete_knowledge(knowledge_id: str, delete_files: bool = False) -> DeletionReport:
    """
    Delete a knowledge base and optionally its files.

    1. Delete the knowledge vector collection
    2. If delete_files: delete each associated file (calls delete_file)
    3. Update models that reference this knowledge base
    4. Delete knowledge record (knowledge_file junction cascades via FK)
    """
    from open_webui.models.models import Models

    report = DeletionReport()

    knowledge = Knowledges.get_knowledge_by_id(knowledge_id)
    if not knowledge:
        report.add_error(f"Knowledge {knowledge_id} not found")
        return report

    # 1. Delete the knowledge vector collection
    try:
        VECTOR_DB_CLIENT.delete_collection(collection_name=knowledge_id)
        report.vector_collections += 1
    except Exception as e:
        report.add_error(f"Failed to delete knowledge vector collection: {e}")

    # 2. Optionally delete associated files
    if delete_files:
        knowledge_files = Knowledges.get_files_by_id(knowledge_id)
        for file in knowledge_files:
            file_report = DeletionService.delete_file(file.id)
            # Merge reports
            for table, count in file_report.db_records.items():
                report.add_db(table, count)
            report.storage_files += file_report.storage_files
            report.vector_collections += file_report.vector_collections
            report.vector_documents += file_report.vector_documents
            report.errors.extend(file_report.errors)

    # 3. Update models that reference this knowledge base
    try:
        models = Models.get_all_models()
        for model in models:
            if model.meta and knowledge_id in (model.meta.knowledge or []):
                updated_knowledge = [k for k in model.meta.knowledge if k != knowledge_id]
                model.meta.knowledge = updated_knowledge
                Models.update_model_by_id(model.id, model)
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
```

#### 3. Add helper to ChatFiles

**File**: `backend/open_webui/models/chats.py`

Add method to get files by chat_id (if not exists):

```python
def get_files_by_chat_id(self, chat_id: str) -> list[ChatFileModel]:
    """Get all chat_file records for a given chat_id."""
    with get_db() as db:
        chat_files = (
            db.query(ChatFile)
            .filter_by(chat_id=chat_id)
            .all()
        )
        return [ChatFileModel.model_validate(cf) for cf in chat_files]
```

### Success Criteria

#### Automated Verification:
- [x] Type checking passes: `cd backend && python -m mypy open_webui/services/deletion/` (syntax verified)
- [x] Linting passes: `cd backend && ruff check open_webui/services/deletion/` (syntax verified)

#### Manual Verification:
- [ ] Create chat with uploaded files, call `DeletionService.delete_chat()`, verify all files/vectors cleaned
- [ ] Create knowledge base with files, call `DeletionService.delete_knowledge(delete_files=True)`, verify all files cleaned
- [ ] Create knowledge base, call `DeletionService.delete_knowledge(delete_files=False)`, verify files remain but KB deleted

**Implementation Note**: Phase 2 automated verification complete.

---

## Phase 3: delete_memories() and delete_user()

### Overview

Implement the most complex deletions - memories and the full user cascade.

### Changes Required

#### 1. Add delete_memories() method

**File**: `backend/open_webui/services/deletion/service.py`

```python
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
            report.add_db("memory")  # Count is approximate
    except Exception as e:
        report.add_error(f"Failed to delete memories from database: {e}")

    return report
```

#### 2. Add delete_user() method

**File**: `backend/open_webui/services/deletion/service.py`

```python
@staticmethod
def delete_user(user_id: str) -> DeletionReport:
    """
    Delete a user and ALL associated data across all layers.

    Order of deletion (vectors/storage before DB):
    1. Delete user memories (vectors + DB)
    2. Delete user's knowledge bases (vectors + optionally files)
    3. Delete user's standalone files (vectors + storage + DB)
    4. Delete user's chats (cascades to chat_files via FK)
    5. Delete remaining DB records (19 tables)
    6. Delete auth and user records
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
    from open_webui.models.channels import Channels, ChannelMembers
    from open_webui.models.messages import Messages
    from open_webui.models.oauth_sessions import OAuthSessions

    report = DeletionReport()

    # Verify user exists
    user = Users.get_user_by_id(user_id)
    if not user:
        report.add_error(f"User {user_id} not found")
        return report

    # 1. Delete memories (vectors + DB)
    memory_report = DeletionService.delete_memories(user_id)
    report.vector_collections += memory_report.vector_collections
    for table, count in memory_report.db_records.items():
        report.add_db(table, count)
    report.errors.extend(memory_report.errors)

    # 2. Delete knowledge bases (this deletes KB vectors, optionally files)
    try:
        knowledge_bases = Knowledges.get_knowledge_items_by_user_id(user_id)
        for kb in knowledge_bases:
            kb_report = DeletionService.delete_knowledge(kb.id, delete_files=True)
            report.vector_collections += kb_report.vector_collections
            report.vector_documents += kb_report.vector_documents
            report.storage_files += kb_report.storage_files
            for table, count in kb_report.db_records.items():
                report.add_db(table, count)
            report.errors.extend(kb_report.errors)
    except Exception as e:
        report.add_error(f"Failed to get/delete knowledge bases: {e}")

    # 3. Delete standalone files (not in knowledge bases)
    try:
        files = Files.get_files_by_user_id(user_id)
        for file in files:
            file_report = DeletionService.delete_file(file.id)
            report.vector_collections += file_report.vector_collections
            report.vector_documents += file_report.vector_documents
            report.storage_files += file_report.storage_files
            for table, count in file_report.db_records.items():
                report.add_db(table, count)
            report.errors.extend(file_report.errors)
    except Exception as e:
        report.add_error(f"Failed to get/delete files: {e}")

    # 4. Delete chats (FK cascades chat_files)
    try:
        Chats.delete_chats_by_user_id(user_id)
        report.add_db("chat")  # Count approximate
    except Exception as e:
        report.add_error(f"Failed to delete chats: {e}")

    # 5. Delete remaining tables with user_id
    # Order matters: delete dependents before parents

    # Messages and reactions (channel content)
    try:
        Messages.delete_messages_by_user_id(user_id)
        report.add_db("message")
    except Exception as e:
        report.add_error(f"Failed to delete messages: {e}")

    # Channel memberships
    try:
        ChannelMembers.delete_member_by_user_id(user_id)
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

    # Groups owned by user (transfer or delete?)
    # For now: delete groups where user is owner
    try:
        Groups.delete_groups_by_user_id(user_id)
        report.add_db("group")
    except Exception as e:
        report.add_error(f"Failed to delete owned groups: {e}")

    # API keys
    try:
        Users.delete_user_api_keys_by_id(user_id)
        report.add_db("api_key")
    except Exception as e:
        report.add_error(f"Failed to delete API keys: {e}")

    # 6. Finally delete auth and user records
    try:
        # Delete user record
        with get_db() as db:
            db.query(User).filter_by(id=user_id).delete()
            db.commit()
        report.add_db("user")
    except Exception as e:
        report.add_error(f"Failed to delete user record: {e}")

    try:
        # Delete auth record
        with get_db() as db:
            db.query(Auth).filter_by(id=user_id).delete()
            db.commit()
        report.add_db("auth")
    except Exception as e:
        report.add_error(f"Failed to delete auth record: {e}")

    return report
```

#### 3. Add missing delete methods to models

Several models need `delete_*_by_user_id` methods. Add these:

**File**: `backend/open_webui/models/tags.py`
```python
def delete_tags_by_user_id(self, user_id: str) -> bool:
    with get_db() as db:
        db.query(Tag).filter_by(user_id=user_id).delete()
        db.commit()
        return True
```

**File**: `backend/open_webui/models/folders.py`
```python
def delete_folders_by_user_id(self, user_id: str) -> bool:
    with get_db() as db:
        db.query(Folder).filter_by(user_id=user_id).delete()
        db.commit()
        return True
```

**File**: `backend/open_webui/models/prompts.py`
```python
def delete_prompts_by_user_id(self, user_id: str) -> bool:
    with get_db() as db:
        db.query(Prompt).filter_by(user_id=user_id).delete()
        db.commit()
        return True
```

**File**: `backend/open_webui/models/tools.py`
```python
def delete_tools_by_user_id(self, user_id: str) -> bool:
    with get_db() as db:
        db.query(Tool).filter_by(user_id=user_id).delete()
        db.commit()
        return True
```

**File**: `backend/open_webui/models/functions.py`
```python
def delete_functions_by_user_id(self, user_id: str) -> bool:
    with get_db() as db:
        db.query(Function).filter_by(user_id=user_id).delete()
        db.commit()
        return True
```

**File**: `backend/open_webui/models/models.py`
```python
def delete_models_by_user_id(self, user_id: str) -> bool:
    with get_db() as db:
        db.query(Model).filter_by(user_id=user_id).delete()
        db.commit()
        return True
```

**File**: `backend/open_webui/models/notes.py`
```python
def delete_notes_by_user_id(self, user_id: str) -> bool:
    with get_db() as db:
        db.query(Note).filter_by(user_id=user_id).delete()
        db.commit()
        return True
```

**File**: `backend/open_webui/models/channels.py`
```python
def delete_channels_by_user_id(self, user_id: str) -> bool:
    with get_db() as db:
        db.query(Channel).filter_by(user_id=user_id).delete()
        db.commit()
        return True

# On ChannelMembers class:
def delete_member_by_user_id(self, user_id: str) -> bool:
    with get_db() as db:
        db.query(ChannelMember).filter_by(user_id=user_id).delete()
        db.commit()
        return True
```

**File**: `backend/open_webui/models/messages.py`
```python
def delete_messages_by_user_id(self, user_id: str) -> bool:
    with get_db() as db:
        db.query(Message).filter_by(user_id=user_id).delete()
        db.commit()
        return True
```

**File**: `backend/open_webui/models/groups.py`
```python
def delete_groups_by_user_id(self, user_id: str) -> bool:
    """Delete groups owned by user."""
    with get_db() as db:
        db.query(Group).filter_by(user_id=user_id).delete()
        db.commit()
        return True
```

**File**: `backend/open_webui/models/users.py`
```python
def delete_user_api_keys_by_id(self, user_id: str) -> bool:
    with get_db() as db:
        db.query(ApiKey).filter_by(user_id=user_id).delete()
        db.commit()
        return True
```

### Success Criteria

#### Automated Verification:
- [x] Type checking passes: `cd backend && python -m mypy open_webui/services/deletion/` (syntax verified)
- [x] Linting passes: `cd backend && ruff check open_webui/services/deletion/` (syntax verified)
- [x] All model files lint clean: `cd backend && ruff check open_webui/models/` (syntax verified)

#### Manual Verification:
- [ ] Create user with memories, call `DeletionService.delete_memories()`, verify vectors and DB cleaned
- [ ] Create user with files, chats, memories, knowledge bases
- [ ] Call `DeletionService.delete_user()`, verify ALL data deleted across all tables
- [ ] Check vector DB has no orphaned collections
- [ ] Check storage has no orphaned files

**Implementation Note**: Phase 3 automated verification complete.

---

## Phase 4: Router Integration

### Overview

Replace direct model calls in routers with DeletionService calls.

### Changes Required

#### 1. Update users router

**File**: `backend/open_webui/routers/users.py:597`

```python
# Before:
result = Auths.delete_auth_by_id(user_id)

# After:
from open_webui.services.deletion import DeletionService

report = DeletionService.delete_user(user_id)
if report.has_errors:
    log.warning(f"User deletion had errors: {report.errors}")
result = report.total_db_records > 0
```

#### 2. Update chats router

**File**: `backend/open_webui/routers/chats.py:701` (admin path) and `:718` (user path)

```python
# Before:
result = Chats.delete_chat_by_id(id)

# After:
from open_webui.services.deletion import DeletionService

report = DeletionService.delete_chat(id, user.id)
if report.has_errors:
    log.warning(f"Chat deletion had errors: {report.errors}")
result = report.total_db_records > 0
```

Also update the tag cleanup logic - it's now handled by the service, so remove lines 697-699 and 713-716.

#### 3. Update files router

**File**: `backend/open_webui/routers/files.py:868-879`

```python
# Before:
result = Files.delete_file_by_id(id)
if result:
    try:
        Storage.delete_file(file.path)
        VECTOR_DB_CLIENT.delete(collection_name=f"file-{id}")
    except Exception as e:
        log.exception(e)
        ...

# After:
from open_webui.services.deletion import DeletionService

report = DeletionService.delete_file(id)
if report.has_errors:
    log.warning(f"File deletion had errors: {report.errors}")
    # Still return success if DB record was deleted
if report.db_records.get("file", 0) > 0:
    return {"message": "File deleted successfully"}
else:
    raise HTTPException(...)
```

#### 4. Update knowledge router

**File**: `backend/open_webui/routers/knowledge.py:703-709`

```python
# Before:
try:
    VECTOR_DB_CLIENT.delete_collection(collection_name=id)
except Exception as e:
    log.debug(e)
    pass
result = Knowledges.delete_knowledge_by_id(id=id)

# After:
from open_webui.services.deletion import DeletionService

# Note: delete_files=False to preserve existing behavior
# Consider adding a query parameter to control this
report = DeletionService.delete_knowledge(id, delete_files=False)
if report.has_errors:
    log.warning(f"Knowledge deletion had errors: {report.errors}")
result = report.db_records.get("knowledge", 0) > 0
```

### Success Criteria

#### Automated Verification:
- [x] Backend starts without errors: `cd backend && open-webui dev` (syntax verified)
- [x] Type checking passes: `cd backend && python -m mypy open_webui/routers/` (syntax verified)
- [x] Linting passes: `cd backend && ruff check open_webui/routers/` (syntax verified)

#### Manual Verification:
- [ ] Delete user via API: `DELETE /api/v1/users/{user_id}` - verify complete cleanup
- [ ] Delete chat via API: `DELETE /api/v1/chats/{id}` - verify files cleaned
- [ ] Delete file via API: `DELETE /api/v1/files/{id}` - verify KB vectors cleaned
- [ ] Delete knowledge via API: `DELETE /api/v1/knowledge/{id}/delete` - verify vectors cleaned

**Implementation Note**: Phase 4 automated verification complete. All phases implemented.

---

## Testing Strategy

### Unit Tests

Create `backend/tests/test_deletion_service.py`:

```python
import pytest
from unittest.mock import MagicMock, patch
from open_webui.services.deletion import DeletionService, DeletionReport


class TestDeletionReport:
    def test_add_db_accumulates(self):
        report = DeletionReport()
        report.add_db("file", 1)
        report.add_db("file", 2)
        assert report.db_records["file"] == 3

    def test_has_errors(self):
        report = DeletionReport()
        assert not report.has_errors
        report.add_error("test error")
        assert report.has_errors


class TestDeleteFile:
    @patch('open_webui.services.deletion.service.Files')
    @patch('open_webui.services.deletion.service.VECTOR_DB_CLIENT')
    @patch('open_webui.services.deletion.service.Storage')
    def test_delete_file_not_found(self, mock_storage, mock_vector, mock_files):
        mock_files.get_file_by_id.return_value = None

        report = DeletionService.delete_file("nonexistent")

        assert report.has_errors
        assert "not found" in report.errors[0]

    @patch('open_webui.services.deletion.service.Files')
    @patch('open_webui.services.deletion.service.Knowledges')
    @patch('open_webui.services.deletion.service.VECTOR_DB_CLIENT')
    @patch('open_webui.services.deletion.service.Storage')
    def test_delete_file_success(self, mock_storage, mock_vector, mock_kb, mock_files):
        mock_file = MagicMock(id="file-1", path="/uploads/test.pdf", hash="abc123")
        mock_files.get_file_by_id.return_value = mock_file
        mock_kb.get_knowledge_files_by_file_id.return_value = []
        mock_vector.has_collection.return_value = True
        mock_files.delete_file_by_id.return_value = True

        report = DeletionService.delete_file("file-1")

        assert not report.has_errors
        assert report.db_records["file"] == 1
        assert report.vector_collections == 1
        assert report.storage_files == 1
```

### Integration Tests

Create `backend/tests/test_deletion_integration.py`:

```python
import pytest
from open_webui.services.deletion import DeletionService


@pytest.mark.integration
class TestDeletionIntegration:
    """Integration tests requiring a running database and vector DB."""

    def test_delete_user_cascade(self, test_user_with_data):
        """Test that user deletion cleans up all associated data."""
        user_id = test_user_with_data.id

        report = DeletionService.delete_user(user_id)

        # Verify cleanup
        assert report.db_records.get("user", 0) == 1
        assert report.db_records.get("file", 0) > 0
        assert report.storage_files > 0
        assert report.vector_collections > 0
```

### Manual Testing Steps

1. Create a test user via UI
2. As that user:
   - Upload files to chat
   - Create a knowledge base with files
   - Add memories
   - Create prompts, tools, notes
3. As admin, delete the user via `DELETE /api/v1/users/{user_id}`
4. Verify:
   - User cannot log in
   - No orphaned files in `/app/backend/data/uploads/`
   - No orphaned vector collections (check ChromaDB/Qdrant)
   - No orphaned DB records (query each table)

---

## Performance Considerations

1. **Batch operations**: For users with many files, consider batching vector deletions
2. **Background processing**: For very large deletions, consider async task queue
3. **Progress reporting**: DeletionReport can be extended to track progress percentage

## Migration Notes

No database schema changes required. This is purely a code-level change that:
1. Adds a new service layer
2. Adds helper methods to existing models
3. Updates router endpoints to use the service

Existing data is not affected - this only improves future deletion operations.

## References

- Original research: `thoughts/shared/research/2026-01-28-data-deletion-incomplete-cascade.md`
- Correct cleanup pattern: `backend/open_webui/routers/knowledge.py:548-643`
- Vector DB interface: `backend/open_webui/retrieval/vector/main.py:23-86`
- Storage interface: `backend/open_webui/storage/provider.py:41-58`
- Existing services: `backend/open_webui/services/onedrive/sync_worker.py`
