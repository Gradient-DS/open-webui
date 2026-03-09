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
    ) -> List[UserArchiveSummaryModel]:
        with get_db() as db:
            query = db.query(UserArchive)

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

    def delete_archive(self, archive_id: str) -> bool:
        with get_db() as db:
            archive = db.query(UserArchive).filter_by(id=archive_id).first()
            if not archive:
                return False
            db.delete(archive)
            db.commit()
            return True

    def count_archives(self) -> int:
        with get_db() as db:
            return db.query(UserArchive).count()


UserArchives = UserArchiveTable()
