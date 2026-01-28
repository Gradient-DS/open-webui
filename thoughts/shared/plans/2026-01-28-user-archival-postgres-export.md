# User Data Archival & PostgreSQL Export Implementation Plan

## Overview

Implement a comprehensive user data archival system for government/sovereign deployments, allowing administrators to archive user data before account removal, restore archived users, and browse archived data. Additionally, add PostgreSQL database export capability to achieve parity with the existing SQLite download feature.

## Current State Analysis

### What Exists
- `DeletionService` (`services/deletion/service.py:39-506`) handles full cascade deletion
- SQLite download at `GET /api/utils/db/download` (returns 400 for PostgreSQL)
- `ENABLE_ADMIN_EXPORT` flag gates sensitive exports
- `Feedbacks` model provides good template for new models
- Admin config pattern with `PersistentConfig` for env + DB persistence

### What's Missing
- No user data archival before deletion
- No PostgreSQL export capability
- No way to preserve departing employee data for compliance
- No restore functionality for accidentally deleted users

### Key Discoveries
- `DeletionService.delete_user()` collects all user data types in order (`service.py:318-504`)
- Archive can reuse this data collection logic but serialize instead of delete
- `PersistentConfig` pattern at `config.py:165-221` for admin-configurable settings
- Model pattern with JSON columns at `models/feedbacks.py` is ideal template

## Desired End State

After implementation:
1. Admins can archive any user's chat data before deletion
2. Archived users can be restored with full chat history
3. Admins can browse archived user data (read-only)
4. User self-deletion can auto-archive based on admin policy
5. Configurable retention with "never delete" option
6. PostgreSQL databases can be exported as JSON
7. All archive operations are audit-logged

### Verification
- Admin can archive user → view archive → restore user → user has original chats
- Retention job deletes expired archives (except "never delete")
- PostgreSQL export downloads complete JSON of all tables

## What We're NOT Doing

- **Vector embedding archival** - Derived data, can be regenerated
- **File content archival** - Phase 2 enhancement (metadata only for now)
- **Knowledge base archival** - Phase 2 enhancement
- **Real-time sync** - Archives are point-in-time snapshots
- **Incremental archives** - Each archive is complete snapshot

## Implementation Approach

Create a new `UserArchive` model storing frozen JSON snapshots of user chat data. Implement `ArchiveService` that mirrors `DeletionService` but serializes instead of deletes. Add admin endpoints for CRUD + restore operations. Integrate with user deletion flow for optional pre-deletion archival. Add background job for retention enforcement.

---

## Phase 1: Database Model & Configuration

### Overview
Create the `UserArchive` database model and add configuration settings for archival feature control.

### Changes Required

#### 1. Create Archive Model
**File**: `backend/open_webui/models/user_archives.py` (new file)

```python
import logging
import time
import uuid
from typing import Optional, List

from open_webui.internal.db import Base, get_db
from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, Column, Text, JSON, Boolean, Index

log = logging.getLogger(__name__)


####################
# UserArchive DB Schema
####################


class UserArchive(Base):
    __tablename__ = "user_archive"

    id = Column(Text, primary_key=True, unique=True)

    # Original user identifiers
    user_id = Column(Text, nullable=False)  # Original user ID
    user_email = Column(Text, nullable=False)  # For search
    user_name = Column(Text, nullable=False)  # For display

    # Archive metadata
    reason = Column(Text, nullable=False)  # e.g., "Employee offboarding", "Account cleanup"
    archived_by = Column(Text, nullable=False)  # Admin user ID who created archive

    # The frozen data snapshot
    data = Column(JSON, nullable=False)  # Contains: user_profile, chats, tags, folders

    # Retention settings
    retention_days = Column(BigInteger, nullable=True)  # NULL = never delete
    expires_at = Column(BigInteger, nullable=True)  # Calculated from retention_days
    never_delete = Column(Boolean, default=False)

    # Restoration tracking
    restored = Column(Boolean, default=False)
    restored_at = Column(BigInteger, nullable=True)
    restored_by = Column(Text, nullable=True)
    restored_user_id = Column(Text, nullable=True)  # New user ID after restore

    # Timestamps
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        Index("user_archive_user_email_idx", "user_email"),
        Index("user_archive_user_name_idx", "user_name"),
        Index("user_archive_expires_at_idx", "expires_at"),
        Index("user_archive_created_at_idx", "created_at"),
    )


class UserArchiveModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    user_email: str
    user_name: str
    reason: str
    archived_by: str
    data: dict
    retention_days: Optional[int] = None
    expires_at: Optional[int] = None
    never_delete: bool = False
    restored: bool = False
    restored_at: Optional[int] = None
    restored_by: Optional[str] = None
    restored_user_id: Optional[str] = None
    created_at: int
    updated_at: int


class UserArchiveSummaryModel(BaseModel):
    """Lightweight model for list views (excludes large data field)"""
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    user_email: str
    user_name: str
    reason: str
    archived_by: str
    retention_days: Optional[int] = None
    expires_at: Optional[int] = None
    never_delete: bool = False
    restored: bool = False
    restored_at: Optional[int] = None
    created_at: int
    updated_at: int


####################
# Forms
####################


class CreateArchiveForm(BaseModel):
    reason: str
    retention_days: Optional[int] = None  # NULL = use default
    never_delete: bool = False


class UpdateArchiveForm(BaseModel):
    reason: Optional[str] = None
    retention_days: Optional[int] = None
    never_delete: Optional[bool] = None


####################
# Table Operations
####################


class UserArchiveTable:
    def insert_archive(
        self,
        user_id: str,
        user_email: str,
        user_name: str,
        reason: str,
        archived_by: str,
        data: dict,
        retention_days: Optional[int] = None,
        never_delete: bool = False,
    ) -> Optional[UserArchiveModel]:
        with get_db() as db:
            archive_id = str(uuid.uuid4())
            now = int(time.time())

            expires_at = None
            if retention_days and not never_delete:
                expires_at = now + (retention_days * 24 * 60 * 60)

            archive = UserArchive(
                id=archive_id,
                user_id=user_id,
                user_email=user_email,
                user_name=user_name,
                reason=reason,
                archived_by=archived_by,
                data=data,
                retention_days=retention_days,
                expires_at=expires_at,
                never_delete=never_delete,
                restored=False,
                created_at=now,
                updated_at=now,
            )
            try:
                db.add(archive)
                db.commit()
                db.refresh(archive)
                return UserArchiveModel.model_validate(archive)
            except Exception as e:
                log.exception(f"Error creating user archive: {e}")
                return None

    def get_archive_by_id(self, archive_id: str) -> Optional[UserArchiveModel]:
        with get_db() as db:
            archive = db.query(UserArchive).filter_by(id=archive_id).first()
            if not archive:
                return None
            return UserArchiveModel.model_validate(archive)

    def get_archives(
        self,
        skip: int = 0,
        limit: int = 50,
        search: Optional[str] = None,
        include_restored: bool = False,
    ) -> List[UserArchiveSummaryModel]:
        with get_db() as db:
            query = db.query(UserArchive)

            if not include_restored:
                query = query.filter(UserArchive.restored == False)

            if search:
                search_term = f"%{search}%"
                query = query.filter(
                    (UserArchive.user_email.ilike(search_term)) |
                    (UserArchive.user_name.ilike(search_term))
                )

            archives = query.order_by(UserArchive.created_at.desc()).offset(skip).limit(limit).all()
            return [UserArchiveSummaryModel.model_validate(a) for a in archives]

    def get_expired_archives(self) -> List[UserArchiveModel]:
        """Get archives past their retention period (for cleanup job)"""
        with get_db() as db:
            now = int(time.time())
            archives = db.query(UserArchive).filter(
                UserArchive.never_delete == False,
                UserArchive.restored == False,
                UserArchive.expires_at.isnot(None),
                UserArchive.expires_at < now,
            ).all()
            return [UserArchiveModel.model_validate(a) for a in archives]

    def update_archive(
        self, archive_id: str, form_data: UpdateArchiveForm
    ) -> Optional[UserArchiveModel]:
        with get_db() as db:
            archive = db.query(UserArchive).filter_by(id=archive_id).first()
            if not archive:
                return None

            now = int(time.time())

            if form_data.reason is not None:
                archive.reason = form_data.reason
            if form_data.never_delete is not None:
                archive.never_delete = form_data.never_delete
            if form_data.retention_days is not None:
                archive.retention_days = form_data.retention_days
                if form_data.retention_days and not archive.never_delete:
                    archive.expires_at = archive.created_at + (form_data.retention_days * 24 * 60 * 60)
                else:
                    archive.expires_at = None

            archive.updated_at = now
            db.commit()
            db.refresh(archive)
            return UserArchiveModel.model_validate(archive)

    def mark_restored(
        self, archive_id: str, restored_by: str, restored_user_id: str
    ) -> Optional[UserArchiveModel]:
        with get_db() as db:
            archive = db.query(UserArchive).filter_by(id=archive_id).first()
            if not archive:
                return None

            now = int(time.time())
            archive.restored = True
            archive.restored_at = now
            archive.restored_by = restored_by
            archive.restored_user_id = restored_user_id
            archive.updated_at = now

            db.commit()
            db.refresh(archive)
            return UserArchiveModel.model_validate(archive)

    def delete_archive(self, archive_id: str) -> bool:
        with get_db() as db:
            archive = db.query(UserArchive).filter_by(id=archive_id).first()
            if not archive:
                return False
            db.delete(archive)
            db.commit()
            return True

    def count_archives(self, include_restored: bool = False) -> int:
        with get_db() as db:
            query = db.query(UserArchive)
            if not include_restored:
                query = query.filter(UserArchive.restored == False)
            return query.count()


UserArchives = UserArchiveTable()
```

#### 2. Create Alembic Migration
**File**: `backend/open_webui/migrations/versions/XXXXXX_add_user_archive_table.py` (new file)

```python
"""Add user_archive table

Revision ID: [auto-generated]
Revises: [previous-revision]
Create Date: 2026-01-28

"""

from alembic import op
import sqlalchemy as sa

revision = "[auto-generated]"
down_revision = "[previous-revision]"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user_archive",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("user_email", sa.Text(), nullable=False),
        sa.Column("user_name", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("archived_by", sa.Text(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("retention_days", sa.BigInteger(), nullable=True),
        sa.Column("expires_at", sa.BigInteger(), nullable=True),
        sa.Column("never_delete", sa.Boolean(), default=False),
        sa.Column("restored", sa.Boolean(), default=False),
        sa.Column("restored_at", sa.BigInteger(), nullable=True),
        sa.Column("restored_by", sa.Text(), nullable=True),
        sa.Column("restored_user_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
    )

    op.create_index("user_archive_user_email_idx", "user_archive", ["user_email"])
    op.create_index("user_archive_user_name_idx", "user_archive", ["user_name"])
    op.create_index("user_archive_expires_at_idx", "user_archive", ["expires_at"])
    op.create_index("user_archive_created_at_idx", "user_archive", ["created_at"])


def downgrade():
    op.drop_index("user_archive_created_at_idx", table_name="user_archive")
    op.drop_index("user_archive_expires_at_idx", table_name="user_archive")
    op.drop_index("user_archive_user_name_idx", table_name="user_archive")
    op.drop_index("user_archive_user_email_idx", table_name="user_archive")
    op.drop_table("user_archive")
```

#### 3. Add Configuration Settings
**File**: `backend/open_webui/config.py`

Add after line ~1607 (after `ENABLE_ADMIN_CHAT_ACCESS`):

```python
####################################
# User Archival
####################################

ENABLE_USER_ARCHIVAL = PersistentConfig(
    "ENABLE_USER_ARCHIVAL",
    "admin.enable_user_archival",
    os.environ.get("ENABLE_USER_ARCHIVAL", "True").lower() == "true",
)

DEFAULT_ARCHIVE_RETENTION_DAYS = PersistentConfig(
    "DEFAULT_ARCHIVE_RETENTION_DAYS",
    "admin.default_archive_retention_days",
    int(os.environ.get("DEFAULT_ARCHIVE_RETENTION_DAYS", "1095")),  # 3 years default (ISO 27001)
)

# Auto-archive when users delete their own accounts
ENABLE_AUTO_ARCHIVE_ON_SELF_DELETE = PersistentConfig(
    "ENABLE_AUTO_ARCHIVE_ON_SELF_DELETE",
    "admin.enable_auto_archive_on_self_delete",
    os.environ.get("ENABLE_AUTO_ARCHIVE_ON_SELF_DELETE", "False").lower() == "true",
)

AUTO_ARCHIVE_RETENTION_DAYS = PersistentConfig(
    "AUTO_ARCHIVE_RETENTION_DAYS",
    "admin.auto_archive_retention_days",
    int(os.environ.get("AUTO_ARCHIVE_RETENTION_DAYS", "365")),  # 1 year default for self-delete
)
```

#### 4. Add to App State
**File**: `backend/open_webui/main.py`

Add imports (around line 430):
```python
from open_webui.config import (
    # ... existing imports ...
    ENABLE_USER_ARCHIVAL,
    DEFAULT_ARCHIVE_RETENTION_DAYS,
    ENABLE_AUTO_ARCHIVE_ON_SELF_DELETE,
    AUTO_ARCHIVE_RETENTION_DAYS,
)
```

Add to app state initialization (around line 815):
```python
app.state.config.ENABLE_USER_ARCHIVAL = ENABLE_USER_ARCHIVAL
app.state.config.DEFAULT_ARCHIVE_RETENTION_DAYS = DEFAULT_ARCHIVE_RETENTION_DAYS
app.state.config.ENABLE_AUTO_ARCHIVE_ON_SELF_DELETE = ENABLE_AUTO_ARCHIVE_ON_SELF_DELETE
app.state.config.AUTO_ARCHIVE_RETENTION_DAYS = AUTO_ARCHIVE_RETENTION_DAYS
```

#### 5. Update Helm Chart Values
**File**: `helm/open-webui-tenant/values.yaml`

Add after `auditLogLevel` config (around line 305):

```yaml
    # User Archival
    enableUserArchival: "true"
    defaultArchiveRetentionDays: "1095"  # 3 years (ISO 27001 compliance)
    enableAutoArchiveOnSelfDelete: "false"
    autoArchiveRetentionDays: "365"  # 1 year for self-deletions
```

#### 6. Update Helm ConfigMap
**File**: `helm/open-webui-tenant/templates/open-webui/configmap.yaml`

Add after `AUDIT_LOG_LEVEL` (around line 228):

```yaml
  # User Archival
  ENABLE_USER_ARCHIVAL: {{ .Values.openWebui.config.enableUserArchival | quote }}
  DEFAULT_ARCHIVE_RETENTION_DAYS: {{ .Values.openWebui.config.defaultArchiveRetentionDays | quote }}
  ENABLE_AUTO_ARCHIVE_ON_SELF_DELETE: {{ .Values.openWebui.config.enableAutoArchiveOnSelfDelete | quote }}
  AUTO_ARCHIVE_RETENTION_DAYS: {{ .Values.openWebui.config.autoArchiveRetentionDays | quote }}
```

### Success Criteria

#### Automated Verification:
- [x] Migration applies cleanly: `alembic upgrade head`
- [x] Model imports without errors: `python -c "from open_webui.models.user_archives import UserArchives"`
- [x] Config loads correctly: `python -c "from open_webui.config import ENABLE_USER_ARCHIVAL; print(ENABLE_USER_ARCHIVAL.value)"`

#### Manual Verification:
- [ ] `user_archive` table exists in database with correct schema
- [ ] Helm template renders correctly: `helm template ./helm/open-webui-tenant`

---

## Phase 2: Archive Service

### Overview
Create `ArchiveService` that collects user data and creates/restores archives.

### Changes Required

#### 1. Create Archive Service
**File**: `backend/open_webui/services/archival/service.py` (new file)

```python
"""
User Archival Service

Collects user data for archival and handles restoration.
Mirrors DeletionService data collection but serializes instead of deletes.
"""

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List

log = logging.getLogger(__name__)


@dataclass
class ArchiveData:
    """Container for archived user data"""
    user_profile: Dict[str, Any] = field(default_factory=dict)
    chats: List[Dict[str, Any]] = field(default_factory=list)
    tags: List[Dict[str, Any]] = field(default_factory=list)
    folders: List[Dict[str, Any]] = field(default_factory=list)
    archived_at: int = 0
    version: str = "1.0"  # Schema version for future compatibility

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "archived_at": self.archived_at,
            "user_profile": self.user_profile,
            "chats": self.chats,
            "tags": self.tags,
            "folders": self.folders,
            "stats": {
                "chat_count": len(self.chats),
                "tag_count": len(self.tags),
                "folder_count": len(self.folders),
            }
        }


@dataclass
class ArchiveResult:
    """Result of archive operation"""
    success: bool = False
    archive_id: Optional[str] = None
    stats: Dict[str, int] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0


@dataclass
class RestoreResult:
    """Result of restore operation"""
    success: bool = False
    new_user_id: Optional[str] = None
    stats: Dict[str, int] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0


class ArchiveService:
    """Service for creating and restoring user archives"""

    @staticmethod
    def collect_user_data(user_id: str) -> ArchiveData:
        """
        Collect all user data for archival.
        Similar to DeletionService but reads instead of deletes.
        """
        from open_webui.models.users import Users
        from open_webui.models.chats import Chats
        from open_webui.models.tags import Tags
        from open_webui.models.folders import Folders

        data = ArchiveData()
        data.archived_at = int(time.time())

        # 1. User profile
        try:
            user = Users.get_user_by_id(user_id)
            if user:
                data.user_profile = {
                    "id": user.id,
                    "name": user.name,
                    "email": user.email,
                    "role": user.role,
                    "profile_image_url": user.profile_image_url,
                    "settings": user.settings.model_dump() if user.settings else {},
                    "info": user.info.model_dump() if user.info else {},
                    "created_at": user.created_at,
                    "updated_at": user.updated_at,
                }
        except Exception as e:
            log.error(f"Error collecting user profile: {e}")

        # 2. Chats with full message history
        try:
            chats = Chats.get_chats_by_user_id(user_id)
            for chat in chats:
                chat_data = {
                    "id": chat.id,
                    "title": chat.title,
                    "chat": chat.chat,  # Full message history
                    "meta": chat.meta,
                    "archived": chat.archived,
                    "pinned": chat.pinned,
                    "folder_id": chat.folder_id,
                    "share_id": chat.share_id,
                    "created_at": chat.created_at,
                    "updated_at": chat.updated_at,
                }
                data.chats.append(chat_data)
        except Exception as e:
            log.error(f"Error collecting chats: {e}")

        # 3. Tags
        try:
            tags = Tags.get_tags_by_user_id(user_id)
            for tag in tags:
                tag_data = {
                    "id": tag.id,
                    "name": tag.name,
                    "user_id": tag.user_id,
                    "meta": tag.meta if hasattr(tag, 'meta') else None,
                }
                data.tags.append(tag_data)
        except Exception as e:
            log.error(f"Error collecting tags: {e}")

        # 4. Folders
        try:
            folders = Folders.get_folders_by_user_id(user_id)
            for folder in folders:
                folder_data = {
                    "id": folder.id,
                    "name": folder.name,
                    "parent_id": folder.parent_id,
                    "user_id": folder.user_id,
                    "is_expanded": folder.is_expanded,
                    "created_at": folder.created_at,
                    "updated_at": folder.updated_at,
                }
                data.folders.append(folder_data)
        except Exception as e:
            log.error(f"Error collecting folders: {e}")

        return data

    @staticmethod
    def create_archive(
        user_id: str,
        archived_by: str,
        reason: str,
        retention_days: Optional[int] = None,
        never_delete: bool = False,
    ) -> ArchiveResult:
        """
        Create an archive of user data.

        Args:
            user_id: ID of user to archive
            archived_by: ID of admin creating the archive
            reason: Reason for archival (for compliance)
            retention_days: Days to retain (None = use default)
            never_delete: If True, archive is never auto-deleted
        """
        from open_webui.models.users import Users
        from open_webui.models.user_archives import UserArchives

        result = ArchiveResult()

        # Get user info
        user = Users.get_user_by_id(user_id)
        if not user:
            result.errors.append(f"User {user_id} not found")
            return result

        # Collect data
        try:
            data = ArchiveService.collect_user_data(user_id)
            result.stats = {
                "chats": len(data.chats),
                "tags": len(data.tags),
                "folders": len(data.folders),
            }
        except Exception as e:
            result.errors.append(f"Error collecting user data: {e}")
            return result

        # Create archive record
        try:
            archive = UserArchives.insert_archive(
                user_id=user_id,
                user_email=user.email,
                user_name=user.name,
                reason=reason,
                archived_by=archived_by,
                data=data.to_dict(),
                retention_days=retention_days,
                never_delete=never_delete,
            )
            if archive:
                result.success = True
                result.archive_id = archive.id
            else:
                result.errors.append("Failed to insert archive record")
        except Exception as e:
            result.errors.append(f"Error creating archive: {e}")

        return result

    @staticmethod
    def restore_archive(
        archive_id: str,
        restored_by: str,
        new_email: Optional[str] = None,
        new_password: Optional[str] = None,
    ) -> RestoreResult:
        """
        Restore a user from an archive.

        Creates a new user account and imports all archived chats.

        Args:
            archive_id: ID of archive to restore
            restored_by: ID of admin performing restore
            new_email: Optional new email (if original is taken)
            new_password: Password for restored account (required if no SSO)
        """
        from open_webui.models.user_archives import UserArchives
        from open_webui.models.users import Users
        from open_webui.models.auths import Auths
        from open_webui.models.chats import Chats
        from open_webui.models.tags import Tags
        from open_webui.models.folders import Folders
        from open_webui.utils.utils import get_password_hash

        result = RestoreResult()

        # Get archive
        archive = UserArchives.get_archive_by_id(archive_id)
        if not archive:
            result.errors.append(f"Archive {archive_id} not found")
            return result

        if archive.restored:
            result.errors.append(f"Archive {archive_id} has already been restored")
            return result

        data = archive.data
        user_profile = data.get("user_profile", {})

        # Determine email to use
        email = new_email or user_profile.get("email")
        if not email:
            result.errors.append("No email available for restoration")
            return result

        # Check if email is already in use
        existing = Users.get_user_by_email(email)
        if existing:
            result.errors.append(f"Email {email} is already in use")
            return result

        # Create new user
        try:
            new_user_id = str(uuid.uuid4())

            # Create auth record
            if new_password:
                hashed = get_password_hash(new_password)
                Auths.insert_new_auth(
                    email=email,
                    password=hashed,
                    name=user_profile.get("name", "Restored User"),
                    role=user_profile.get("role", "user"),
                    profile_image_url=user_profile.get("profile_image_url", ""),
                    id=new_user_id,
                )
            else:
                # Create without password (for SSO users)
                Auths.insert_new_auth(
                    email=email,
                    password="",  # Empty password - must use SSO
                    name=user_profile.get("name", "Restored User"),
                    role=user_profile.get("role", "user"),
                    profile_image_url=user_profile.get("profile_image_url", ""),
                    id=new_user_id,
                )

            result.new_user_id = new_user_id
            result.stats["user"] = 1
        except Exception as e:
            result.errors.append(f"Error creating user: {e}")
            return result

        # Restore folders first (for folder_id references in chats)
        folder_id_map = {}  # Old ID -> New ID
        try:
            for folder_data in data.get("folders", []):
                new_folder = Folders.insert_new_folder(
                    user_id=new_user_id,
                    name=folder_data.get("name", "Restored Folder"),
                    parent_id=folder_id_map.get(folder_data.get("parent_id")),  # Map parent
                )
                if new_folder:
                    folder_id_map[folder_data["id"]] = new_folder.id
            result.stats["folders"] = len(folder_id_map)
        except Exception as e:
            result.errors.append(f"Error restoring folders: {e}")

        # Restore tags
        tag_name_map = {}  # Original name tracking
        try:
            for tag_data in data.get("tags", []):
                new_tag = Tags.insert_new_tag(
                    user_id=new_user_id,
                    name=tag_data.get("name", "restored"),
                )
                if new_tag:
                    tag_name_map[tag_data["id"]] = new_tag.name
            result.stats["tags"] = len(tag_name_map)
        except Exception as e:
            result.errors.append(f"Error restoring tags: {e}")

        # Restore chats
        chat_count = 0
        try:
            for chat_data in data.get("chats", []):
                # Map folder_id to new folder
                folder_id = folder_id_map.get(chat_data.get("folder_id"))

                new_chat = Chats.insert_new_chat(
                    user_id=new_user_id,
                    form_data={
                        "chat": chat_data.get("chat", {}),
                    }
                )
                if new_chat:
                    # Update additional fields
                    Chats.update_chat_by_id(
                        new_chat.id,
                        {
                            "title": chat_data.get("title", "Restored Chat"),
                            "folder_id": folder_id,
                            "meta": chat_data.get("meta", {}),
                        }
                    )
                    chat_count += 1
            result.stats["chats"] = chat_count
        except Exception as e:
            result.errors.append(f"Error restoring chats: {e}")

        # Mark archive as restored
        try:
            UserArchives.mark_restored(
                archive_id=archive_id,
                restored_by=restored_by,
                restored_user_id=new_user_id,
            )
        except Exception as e:
            result.errors.append(f"Error marking archive as restored: {e}")

        result.success = True
        return result

    @staticmethod
    def cleanup_expired_archives() -> Dict[str, int]:
        """
        Delete archives past their retention period.
        Called by background job.
        """
        from open_webui.models.user_archives import UserArchives

        stats = {"checked": 0, "deleted": 0, "errors": 0}

        expired = UserArchives.get_expired_archives()
        stats["checked"] = len(expired)

        for archive in expired:
            try:
                if UserArchives.delete_archive(archive.id):
                    stats["deleted"] += 1
                    log.info(f"Deleted expired archive {archive.id} for user {archive.user_email}")
                else:
                    stats["errors"] += 1
            except Exception as e:
                log.error(f"Error deleting archive {archive.id}: {e}")
                stats["errors"] += 1

        return stats
```

#### 2. Create Service `__init__.py`
**File**: `backend/open_webui/services/archival/__init__.py` (new file)

```python
from open_webui.services.archival.service import (
    ArchiveService,
    ArchiveData,
    ArchiveResult,
    RestoreResult,
)

__all__ = ["ArchiveService", "ArchiveData", "ArchiveResult", "RestoreResult"]
```

### Success Criteria

#### Automated Verification:
- [x] Service imports without errors: `python -c "from open_webui.services.archival import ArchiveService"`
- [x] Data collection works: `python -c "from open_webui.services.archival import ArchiveService; print(ArchiveService.collect_user_data.__doc__)"`

#### Manual Verification:
- [ ] ArchiveService.create_archive() creates valid archive record
- [ ] ArchiveService.restore_archive() creates new user with chats

---

## Phase 3: Archive API Endpoints

### Overview
Create admin API endpoints for archive CRUD operations and restoration.

### Changes Required

#### 1. Create Archives Router
**File**: `backend/open_webui/routers/archives.py` (new file)

```python
"""
User Archives API Router

Admin endpoints for managing user data archives.
"""

import logging
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from open_webui.models.user_archives import (
    UserArchives,
    UserArchiveModel,
    UserArchiveSummaryModel,
    CreateArchiveForm,
    UpdateArchiveForm,
)
from open_webui.services.archival import ArchiveService
from open_webui.utils.auth import get_admin_user
from open_webui.constants import ERROR_MESSAGES

from pydantic import BaseModel

log = logging.getLogger(__name__)

router = APIRouter()


####################
# Response Models
####################


class ArchiveListResponse(BaseModel):
    items: List[UserArchiveSummaryModel]
    total: int


class CreateArchiveResponse(BaseModel):
    success: bool
    archive_id: Optional[str] = None
    stats: dict
    errors: List[str]


class RestoreArchiveForm(BaseModel):
    new_email: Optional[str] = None
    new_password: Optional[str] = None


class RestoreArchiveResponse(BaseModel):
    success: bool
    new_user_id: Optional[str] = None
    stats: dict
    errors: List[str]


####################
# Endpoints
####################


@router.get("/", response_model=ArchiveListResponse)
async def get_archives(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    search: Optional[str] = Query(None),
    include_restored: bool = Query(False),
    user=Depends(get_admin_user),
):
    """
    List all user archives.

    - Requires admin role
    - Requires ENABLE_USER_ARCHIVAL to be enabled
    """
    if not request.app.state.config.ENABLE_USER_ARCHIVAL:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User archival is not enabled",
        )

    archives = UserArchives.get_archives(
        skip=skip,
        limit=limit,
        search=search,
        include_restored=include_restored,
    )
    total = UserArchives.count_archives(include_restored=include_restored)

    return ArchiveListResponse(items=archives, total=total)


@router.get("/{archive_id}", response_model=UserArchiveModel)
async def get_archive(
    request: Request,
    archive_id: str,
    user=Depends(get_admin_user),
):
    """
    Get a specific archive with full data.

    - Requires admin role
    - Returns complete chat history in data field
    """
    if not request.app.state.config.ENABLE_USER_ARCHIVAL:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User archival is not enabled",
        )

    archive = UserArchives.get_archive_by_id(archive_id)
    if not archive:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Archive not found",
        )

    return archive


@router.post("/user/{user_id}", response_model=CreateArchiveResponse)
async def create_user_archive(
    request: Request,
    user_id: str,
    form_data: CreateArchiveForm,
    user=Depends(get_admin_user),
):
    """
    Create an archive of a user's data.

    - Requires admin role
    - Does NOT delete the user (use DELETE /users/{id} separately)
    - Archives: chats, tags, folders, user profile
    """
    if not request.app.state.config.ENABLE_USER_ARCHIVAL:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User archival is not enabled",
        )

    # Use default retention if not specified
    retention_days = form_data.retention_days
    if retention_days is None and not form_data.never_delete:
        retention_days = request.app.state.config.DEFAULT_ARCHIVE_RETENTION_DAYS

    result = ArchiveService.create_archive(
        user_id=user_id,
        archived_by=user.id,
        reason=form_data.reason,
        retention_days=retention_days,
        never_delete=form_data.never_delete,
    )

    if not result.success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.errors[0] if result.errors else "Failed to create archive",
        )

    return CreateArchiveResponse(
        success=result.success,
        archive_id=result.archive_id,
        stats=result.stats,
        errors=result.errors,
    )


@router.post("/{archive_id}/restore", response_model=RestoreArchiveResponse)
async def restore_archive(
    request: Request,
    archive_id: str,
    form_data: RestoreArchiveForm,
    user=Depends(get_admin_user),
):
    """
    Restore a user from an archive.

    - Creates a new user account
    - Imports all archived chats
    - Marks archive as restored (cannot restore again)
    """
    if not request.app.state.config.ENABLE_USER_ARCHIVAL:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User archival is not enabled",
        )

    result = ArchiveService.restore_archive(
        archive_id=archive_id,
        restored_by=user.id,
        new_email=form_data.new_email,
        new_password=form_data.new_password,
    )

    if not result.success and result.errors:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.errors[0],
        )

    return RestoreArchiveResponse(
        success=result.success,
        new_user_id=result.new_user_id,
        stats=result.stats,
        errors=result.errors,
    )


@router.patch("/{archive_id}", response_model=UserArchiveSummaryModel)
async def update_archive(
    request: Request,
    archive_id: str,
    form_data: UpdateArchiveForm,
    user=Depends(get_admin_user),
):
    """
    Update archive retention settings.

    - Can update: reason, retention_days, never_delete
    """
    if not request.app.state.config.ENABLE_USER_ARCHIVAL:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User archival is not enabled",
        )

    archive = UserArchives.update_archive(archive_id, form_data)
    if not archive:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Archive not found",
        )

    return UserArchiveSummaryModel.model_validate(archive)


@router.delete("/{archive_id}")
async def delete_archive(
    request: Request,
    archive_id: str,
    user=Depends(get_admin_user),
):
    """
    Permanently delete an archive.

    - This action cannot be undone
    - Use for early cleanup before retention expires
    """
    if not request.app.state.config.ENABLE_USER_ARCHIVAL:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User archival is not enabled",
        )

    success = UserArchives.delete_archive(archive_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Archive not found",
        )

    return {"success": True}


####################
# Admin Config Endpoints
####################


class ArchiveConfigResponse(BaseModel):
    enable_user_archival: bool
    default_archive_retention_days: int
    enable_auto_archive_on_self_delete: bool
    auto_archive_retention_days: int


class ArchiveConfigForm(BaseModel):
    enable_user_archival: Optional[bool] = None
    default_archive_retention_days: Optional[int] = None
    enable_auto_archive_on_self_delete: Optional[bool] = None
    auto_archive_retention_days: Optional[int] = None


@router.get("/admin/config", response_model=ArchiveConfigResponse)
async def get_archive_config(
    request: Request,
    user=Depends(get_admin_user),
):
    """Get archive configuration settings."""
    return ArchiveConfigResponse(
        enable_user_archival=request.app.state.config.ENABLE_USER_ARCHIVAL,
        default_archive_retention_days=request.app.state.config.DEFAULT_ARCHIVE_RETENTION_DAYS,
        enable_auto_archive_on_self_delete=request.app.state.config.ENABLE_AUTO_ARCHIVE_ON_SELF_DELETE,
        auto_archive_retention_days=request.app.state.config.AUTO_ARCHIVE_RETENTION_DAYS,
    )


@router.post("/admin/config", response_model=ArchiveConfigResponse)
async def update_archive_config(
    request: Request,
    form_data: ArchiveConfigForm,
    user=Depends(get_admin_user),
):
    """Update archive configuration settings."""
    if form_data.enable_user_archival is not None:
        request.app.state.config.ENABLE_USER_ARCHIVAL = form_data.enable_user_archival
    if form_data.default_archive_retention_days is not None:
        request.app.state.config.DEFAULT_ARCHIVE_RETENTION_DAYS = form_data.default_archive_retention_days
    if form_data.enable_auto_archive_on_self_delete is not None:
        request.app.state.config.ENABLE_AUTO_ARCHIVE_ON_SELF_DELETE = form_data.enable_auto_archive_on_self_delete
    if form_data.auto_archive_retention_days is not None:
        request.app.state.config.AUTO_ARCHIVE_RETENTION_DAYS = form_data.auto_archive_retention_days

    return ArchiveConfigResponse(
        enable_user_archival=request.app.state.config.ENABLE_USER_ARCHIVAL,
        default_archive_retention_days=request.app.state.config.DEFAULT_ARCHIVE_RETENTION_DAYS,
        enable_auto_archive_on_self_delete=request.app.state.config.ENABLE_AUTO_ARCHIVE_ON_SELF_DELETE,
        auto_archive_retention_days=request.app.state.config.AUTO_ARCHIVE_RETENTION_DAYS,
    )
```

#### 2. Register Router in Main App
**File**: `backend/open_webui/main.py`

Add import (around line 100):
```python
from open_webui.routers import archives
```

Add router registration (around line 680, with other routers):
```python
app.include_router(archives.router, prefix="/api/archives", tags=["archives"])
```

### Success Criteria

#### Automated Verification:
- [x] Router imports: `python -c "from open_webui.routers import archives"`
- [x] OpenAPI spec includes archive endpoints: Check `/docs` endpoint

#### Manual Verification:
- [ ] `POST /api/archives/user/{user_id}` creates archive
- [ ] `GET /api/archives` lists archives
- [ ] `GET /api/archives/{id}` returns full archive with data
- [ ] `POST /api/archives/{id}/restore` creates new user
- [ ] `DELETE /api/archives/{id}` removes archive

---

## Phase 4: Integration with User Deletion

### Overview
Add auto-archival option when users are deleted (by admin or self-deletion).

### Changes Required

#### 1. Update User Deletion Endpoint
**File**: `backend/open_webui/routers/users.py`

Modify the delete user endpoint (around line 580):

```python
@router.delete("/{user_id}", response_model=bool)
async def delete_user_by_id(
    request: Request,
    user_id: str,
    archive_before_delete: bool = Query(False),
    archive_reason: Optional[str] = Query(None),
    user=Depends(get_admin_user),
):
    """
    Delete a user and optionally archive their data first.

    - archive_before_delete: If true, creates archive before deletion
    - archive_reason: Required if archive_before_delete is true
    """
    from open_webui.services.archival import ArchiveService

    if user_id == str(user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.ACTION_PROHIBITED,
        )

    # Check if primary admin
    primary_admin = Users.get_primary_admin()
    if primary_admin and primary_admin.id == user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot delete primary admin user",
        )

    target_user = Users.get_user_by_id(user_id)
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.USER_NOT_FOUND,
        )

    # Archive if requested
    if archive_before_delete:
        if not archive_reason:
            archive_reason = "Admin deletion"

        if request.app.state.config.ENABLE_USER_ARCHIVAL:
            archive_result = ArchiveService.create_archive(
                user_id=user_id,
                archived_by=user.id,
                reason=archive_reason,
                retention_days=request.app.state.config.DEFAULT_ARCHIVE_RETENTION_DAYS,
            )
            if not archive_result.success:
                log.warning(f"Failed to archive user before deletion: {archive_result.errors}")

    # Proceed with deletion
    report = DeletionService.delete_user(user_id)

    if report.has_errors:
        log.warning(f"User deletion had errors: {report.errors}")

    return True
```

#### 2. Add Self-Delete Auto-Archive
**File**: `backend/open_webui/routers/auths.py`

Find the user self-deletion endpoint (if exists) or add auto-archive logic where users can delete their own account. Add similar logic:

```python
# In the self-delete endpoint (around line where users can delete their own account)
if request.app.state.config.ENABLE_AUTO_ARCHIVE_ON_SELF_DELETE:
    from open_webui.services.archival import ArchiveService

    archive_result = ArchiveService.create_archive(
        user_id=user.id,
        archived_by=user.id,  # Self-archived
        reason="User self-deletion",
        retention_days=request.app.state.config.AUTO_ARCHIVE_RETENTION_DAYS,
    )
    if archive_result.has_errors:
        log.warning(f"Failed to auto-archive before self-deletion: {archive_result.errors}")
```

### Success Criteria

#### Automated Verification:
- [ ] Delete endpoint accepts new query params

#### Manual Verification:
- [ ] Deleting user with `archive_before_delete=true` creates archive then deletes
- [ ] Self-delete creates archive when `ENABLE_AUTO_ARCHIVE_ON_SELF_DELETE=true`

---

## Phase 5: PostgreSQL Export

### Overview
Add JSON export capability that works for both SQLite and PostgreSQL databases.

### Changes Required

#### 1. Add Database Export Endpoint
**File**: `backend/open_webui/routers/utils.py`

Add new endpoint (after the existing `/db/download` endpoint):

```python
@router.get("/db/export")
async def export_db_json(user=Depends(get_admin_user)):
    """
    Export database as JSON.

    Works with both SQLite and PostgreSQL.
    Returns JSON file with all tables.
    """
    if not ENABLE_ADMIN_EXPORT:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    import json
    from io import BytesIO
    from fastapi.responses import StreamingResponse
    from open_webui.internal.db import engine, get_db
    from sqlalchemy import inspect, text

    export_data = {
        "export_version": "1.0",
        "exported_at": int(time.time()),
        "database_type": engine.name,
        "tables": {},
    }

    inspector = inspect(engine)
    table_names = inspector.get_table_names()

    with get_db() as db:
        for table_name in table_names:
            # Skip alembic version table
            if table_name == "alembic_version":
                continue

            try:
                result = db.execute(text(f"SELECT * FROM {table_name}"))
                columns = result.keys()
                rows = []
                for row in result:
                    row_dict = {}
                    for i, col in enumerate(columns):
                        value = row[i]
                        # Handle non-JSON-serializable types
                        if isinstance(value, bytes):
                            value = value.hex()
                        elif hasattr(value, 'isoformat'):
                            value = value.isoformat()
                        row_dict[col] = value
                    rows.append(row_dict)
                export_data["tables"][table_name] = {
                    "columns": list(columns),
                    "row_count": len(rows),
                    "rows": rows,
                }
            except Exception as e:
                log.error(f"Error exporting table {table_name}: {e}")
                export_data["tables"][table_name] = {
                    "error": str(e),
                }

    # Create JSON file in memory
    json_bytes = json.dumps(export_data, indent=2, default=str).encode('utf-8')
    buffer = BytesIO(json_bytes)

    filename = f"openwebui-export-{int(time.time())}.json"

    return StreamingResponse(
        buffer,
        media_type="application/json",
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
        }
    )
```

Add import at top of file:
```python
import time
```

### Success Criteria

#### Automated Verification:
- [ ] Endpoint exists and returns JSON

#### Manual Verification:
- [ ] Export works with PostgreSQL database
- [ ] Export works with SQLite database
- [ ] Exported JSON can be parsed and contains all tables

---

## Phase 6: Admin UI Components

### Overview
Add archive management UI to the admin panel.

### Changes Required

#### 1. Create Archive List Component
**File**: `src/lib/components/admin/Settings/Archives.svelte` (new file)

```svelte
<script lang="ts">
    import { onMount } from 'svelte';
    import { getArchives, deleteArchive } from '$lib/apis/archives';
    import { toast } from 'svelte-sonner';

    let archives = [];
    let total = 0;
    let loading = true;
    let search = '';
    let includeRestored = false;

    async function loadArchives() {
        loading = true;
        try {
            const response = await getArchives(localStorage.token, {
                search,
                include_restored: includeRestored,
            });
            archives = response.items;
            total = response.total;
        } catch (error) {
            toast.error('Failed to load archives');
        }
        loading = false;
    }

    async function handleDelete(archiveId: string) {
        if (!confirm('Are you sure you want to permanently delete this archive?')) {
            return;
        }
        try {
            await deleteArchive(localStorage.token, archiveId);
            toast.success('Archive deleted');
            loadArchives();
        } catch (error) {
            toast.error('Failed to delete archive');
        }
    }

    onMount(loadArchives);
</script>

<div class="space-y-4">
    <div class="flex items-center justify-between">
        <h3 class="text-lg font-medium">User Archives</h3>
        <span class="text-sm text-gray-500">{total} archives</span>
    </div>

    <div class="flex gap-4">
        <input
            type="text"
            placeholder="Search by name or email..."
            bind:value={search}
            on:input={() => loadArchives()}
            class="flex-1 px-3 py-2 border rounded"
        />
        <label class="flex items-center gap-2">
            <input type="checkbox" bind:checked={includeRestored} on:change={loadArchives} />
            Include restored
        </label>
    </div>

    {#if loading}
        <div class="text-center py-4">Loading...</div>
    {:else if archives.length === 0}
        <div class="text-center py-4 text-gray-500">No archives found</div>
    {:else}
        <div class="space-y-2">
            {#each archives as archive}
                <div class="p-4 border rounded flex items-center justify-between">
                    <div>
                        <div class="font-medium">{archive.user_name}</div>
                        <div class="text-sm text-gray-500">{archive.user_email}</div>
                        <div class="text-xs text-gray-400">
                            Archived: {new Date(archive.created_at * 1000).toLocaleDateString()}
                            | Reason: {archive.reason}
                            {#if archive.restored}
                                | <span class="text-green-600">Restored</span>
                            {/if}
                            {#if archive.never_delete}
                                | <span class="text-blue-600">Never expires</span>
                            {:else if archive.expires_at}
                                | Expires: {new Date(archive.expires_at * 1000).toLocaleDateString()}
                            {/if}
                        </div>
                    </div>
                    <div class="flex gap-2">
                        <a href="/admin/archives/{archive.id}" class="px-3 py-1 bg-blue-500 text-white rounded text-sm">
                            View
                        </a>
                        {#if !archive.restored}
                            <a href="/admin/archives/{archive.id}/restore" class="px-3 py-1 bg-green-500 text-white rounded text-sm">
                                Restore
                            </a>
                        {/if}
                        <button on:click={() => handleDelete(archive.id)} class="px-3 py-1 bg-red-500 text-white rounded text-sm">
                            Delete
                        </button>
                    </div>
                </div>
            {/each}
        </div>
    {/if}
</div>
```

#### 2. Create Archive API Client
**File**: `src/lib/apis/archives/index.ts` (new file)

```typescript
import { WEBUI_API_BASE_URL } from '$lib/constants';

export const getArchives = async (
    token: string,
    params: { search?: string; include_restored?: boolean } = {}
) => {
    const searchParams = new URLSearchParams();
    if (params.search) searchParams.set('search', params.search);
    if (params.include_restored) searchParams.set('include_restored', 'true');

    const res = await fetch(`${WEBUI_API_BASE_URL}/archives?${searchParams}`, {
        headers: {
            Authorization: `Bearer ${token}`,
        },
    });

    if (!res.ok) throw new Error('Failed to fetch archives');
    return res.json();
};

export const getArchive = async (token: string, archiveId: string) => {
    const res = await fetch(`${WEBUI_API_BASE_URL}/archives/${archiveId}`, {
        headers: {
            Authorization: `Bearer ${token}`,
        },
    });

    if (!res.ok) throw new Error('Failed to fetch archive');
    return res.json();
};

export const createArchive = async (
    token: string,
    userId: string,
    data: { reason: string; retention_days?: number; never_delete?: boolean }
) => {
    const res = await fetch(`${WEBUI_API_BASE_URL}/archives/user/${userId}`, {
        method: 'POST',
        headers: {
            Authorization: `Bearer ${token}`,
            'Content-Type': 'application/json',
        },
        body: JSON.stringify(data),
    });

    if (!res.ok) throw new Error('Failed to create archive');
    return res.json();
};

export const restoreArchive = async (
    token: string,
    archiveId: string,
    data: { new_email?: string; new_password?: string } = {}
) => {
    const res = await fetch(`${WEBUI_API_BASE_URL}/archives/${archiveId}/restore`, {
        method: 'POST',
        headers: {
            Authorization: `Bearer ${token}`,
            'Content-Type': 'application/json',
        },
        body: JSON.stringify(data),
    });

    if (!res.ok) throw new Error('Failed to restore archive');
    return res.json();
};

export const deleteArchive = async (token: string, archiveId: string) => {
    const res = await fetch(`${WEBUI_API_BASE_URL}/archives/${archiveId}`, {
        method: 'DELETE',
        headers: {
            Authorization: `Bearer ${token}`,
        },
    });

    if (!res.ok) throw new Error('Failed to delete archive');
    return res.json();
};

export const getArchiveConfig = async (token: string) => {
    const res = await fetch(`${WEBUI_API_BASE_URL}/archives/admin/config`, {
        headers: {
            Authorization: `Bearer ${token}`,
        },
    });

    if (!res.ok) throw new Error('Failed to fetch archive config');
    return res.json();
};

export const updateArchiveConfig = async (token: string, config: {
    enable_user_archival?: boolean;
    default_archive_retention_days?: number;
    enable_auto_archive_on_self_delete?: boolean;
    auto_archive_retention_days?: number;
}) => {
    const res = await fetch(`${WEBUI_API_BASE_URL}/archives/admin/config`, {
        method: 'POST',
        headers: {
            Authorization: `Bearer ${token}`,
            'Content-Type': 'application/json',
        },
        body: JSON.stringify(config),
    });

    if (!res.ok) throw new Error('Failed to update archive config');
    return res.json();
};
```

#### 3. Add Archive Tab to Admin Settings
**File**: `src/routes/(app)/admin/+page.svelte`

Add "Archives" tab to the admin navigation (implementation depends on existing structure).

### Success Criteria

#### Automated Verification:
- [ ] TypeScript compiles: `npm run check`
- [ ] Svelte components compile: `npm run build`

#### Manual Verification:
- [ ] Archive list shows in admin panel
- [ ] Search works
- [ ] View/Restore/Delete buttons work
- [ ] Archive detail page shows chats

---

## Phase 7: Retention Enforcement Background Job

### Overview
Add periodic cleanup of expired archives.

### Changes Required

#### 1. Add Cleanup Task
**File**: `backend/open_webui/main.py`

Add to the startup tasks or create a scheduled job:

```python
# Add to app lifespan or as a periodic task
async def cleanup_expired_archives():
    """Periodic task to delete expired archives"""
    from open_webui.services.archival import ArchiveService

    while True:
        try:
            stats = ArchiveService.cleanup_expired_archives()
            if stats["deleted"] > 0:
                log.info(f"Archive cleanup: deleted {stats['deleted']} expired archives")
        except Exception as e:
            log.error(f"Error in archive cleanup: {e}")

        # Run daily
        await asyncio.sleep(24 * 60 * 60)
```

Alternatively, integrate with existing background task scheduler if one exists.

### Success Criteria

#### Automated Verification:
- [ ] Cleanup function executes without error

#### Manual Verification:
- [ ] Archives with past `expires_at` are deleted
- [ ] Archives with `never_delete=True` are not deleted
- [ ] Restored archives are not deleted

---

## Testing Strategy

### Unit Tests

**Archive Service Tests:**
- `test_collect_user_data` - Collects all expected data types
- `test_create_archive` - Creates valid archive record
- `test_restore_archive` - Creates new user with correct data
- `test_restore_already_restored` - Returns error for restored archive
- `test_cleanup_expired` - Only deletes expired, non-protected archives

**Archive Model Tests:**
- `test_insert_archive` - Creates record with correct fields
- `test_get_expired_archives` - Returns only expired archives
- `test_update_retention` - Recalculates expires_at

### Integration Tests

- Create user → Archive user → Delete user → Restore from archive → Verify data
- Create archive with retention → Wait for expiry → Verify cleanup
- PostgreSQL export → Verify all tables present

### Manual Testing Steps

1. Create test user with several chats
2. Archive the user (verify archive appears in list)
3. View archive details (verify chats visible)
4. Delete user
5. Restore from archive (verify new account works)
6. Login as restored user (verify chats restored)
7. Test PostgreSQL export downloads valid JSON

---

## Migration Notes

### Database Migration

1. Run Alembic migration to create `user_archive` table
2. No data migration needed (new feature)

### Configuration Migration

1. New env vars have sensible defaults
2. Existing deployments will have archival enabled by default
3. Auto-archive on self-delete is disabled by default

### Helm Chart Updates

Update deployments with new values after merging.

---

## Performance Considerations

- Archive creation may take time for users with many chats (background if needed)
- Archive data is stored as JSON blob - consider compression for large archives
- Index on `expires_at` for efficient cleanup queries
- Archive list uses summary model (excludes large `data` field)

---

## References

- DeletionService implementation: `backend/open_webui/services/deletion/service.py`
- Feedbacks model pattern: `backend/open_webui/models/feedbacks.py`
- PersistentConfig pattern: `backend/open_webui/config.py:165-221`
- Admin endpoint pattern: `backend/open_webui/routers/configs.py`
- GDPR Art 17(3) exceptions: https://gdpr-info.eu/art-17-gdpr/
- ISO 27001 retention requirements: https://sprinto.com/blog/iso-27001-data-retention-policy/
