"""File-attachment rows alongside the ``file`` table.

One row per render artefact derived from a file at ingest time (e.g. an
IFC's per-storey plan PNG or its isometric axon). Bytes live in the
configured ``StorageProvider``; the row stores the path string and the
manifest metadata the UI needs to display the artefact.

Cascade is enforced application-side, matching the OWUI convention of
not declaring DB-level foreign keys. Callers must call
``FileAttachments.delete_attachments_by_file_id`` when a parent
``file`` row is removed; the helper also calls ``Storage.delete_file``
for every path it drops.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, Column, Integer, String, Text
from sqlalchemy.orm import Session

from contextlib import contextmanager

from open_webui.internal.db import Base, get_db


@contextmanager
def get_db_context(_db: Optional[Session] = None):
    """Compat shim. Dev migrated ``get_db_context(db)`` to a zero-arg
    ``get_db()``; this FileAttachments module is sync-only and always
    runs via ``asyncio.to_thread`` from async callers, so the historical
    ``db=`` pass-through is now a noop.
    """
    with get_db() as session:
        yield session


from open_webui.storage.provider import Storage


log = logging.getLogger(__name__)


####################
# Schema
####################


class FileAttachment(Base):
    __tablename__ = 'file_attachment'

    id = Column(String, primary_key=True, unique=True)
    file_id = Column(String, nullable=False, index=True)
    kind = Column(String, nullable=False)
    storey = Column(String, nullable=True)
    index = Column(Integer, nullable=False)
    content_type = Column(String, nullable=False)
    caption = Column(Text, nullable=False)
    path = Column(Text, nullable=False)
    created_at = Column(BigInteger, nullable=False)


####################
# Pydantic
####################


class FileAttachmentModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    file_id: str
    kind: str
    storey: Optional[str] = None
    index: int = 0
    content_type: str = 'image/png'
    caption: str = ''
    path: str
    created_at: int


class FileAttachmentForm(BaseModel):
    id: str
    file_id: str
    kind: str
    storey: Optional[str] = None
    index: int = 0
    content_type: str = 'image/png'
    caption: str = ''
    path: str


####################
# CRUD wrapper
####################


class FileAttachmentsTable:
    def insert_new_attachment(
        self,
        form: FileAttachmentForm,
        db: Optional[Session] = None,
    ) -> Optional[FileAttachmentModel]:
        with get_db_context(db) as session:
            row = FileAttachment(
                id=form.id,
                file_id=form.file_id,
                kind=form.kind,
                storey=form.storey,
                index=form.index,
                content_type=form.content_type,
                caption=form.caption,
                path=form.path,
                created_at=int(time.time()),
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return FileAttachmentModel.model_validate(row)

    def get_attachments_by_file_id(
        self,
        file_id: str,
        db: Optional[Session] = None,
    ) -> list[FileAttachmentModel]:
        with get_db_context(db) as session:
            rows = (
                session.query(FileAttachment)
                .filter(FileAttachment.file_id == file_id)
                .order_by(FileAttachment.kind, FileAttachment.storey, FileAttachment.index)
                .all()
            )
            return [FileAttachmentModel.model_validate(r) for r in rows]

    def get_attachment_by_id(
        self,
        id: str,
        db: Optional[Session] = None,
    ) -> Optional[FileAttachmentModel]:
        with get_db_context(db) as session:
            row = session.query(FileAttachment).filter(FileAttachment.id == id).first()
            return FileAttachmentModel.model_validate(row) if row else None

    def delete_attachments_by_file_id(
        self,
        file_id: str,
        db: Optional[Session] = None,
    ) -> int:
        with get_db_context(db) as session:
            rows = session.query(FileAttachment).filter(FileAttachment.file_id == file_id).all()
            paths = [r.path for r in rows]
            for r in rows:
                session.delete(r)
            session.commit()
            # Storage cleanup is best-effort and post-commit: if it
            # fails, we leak storage objects (cheap, gc-able later) but
            # never DB rows (which nothing else cleans up).
            for path in paths:
                try:
                    Storage.delete_file(path)
                except Exception:
                    log.exception(
                        'failed to delete Storage path %s after attachment row deletion',
                        path,
                    )
            return len(rows)

    def delete_attachments_by_file_ids(
        self,
        file_ids: list[str],
        db: Optional[Session] = None,
    ) -> int:
        """Bulk cascade for the multi-file delete path.

        One DB round-trip via .in_(file_ids), one commit. Storage
        cleanup is best-effort and post-commit, same policy as the
        single-file variant.
        """
        if not file_ids:
            return 0
        with get_db_context(db) as session:
            rows = session.query(FileAttachment).filter(FileAttachment.file_id.in_(file_ids)).all()
            paths = [r.path for r in rows]
            session.query(FileAttachment).filter(FileAttachment.file_id.in_(file_ids)).delete(synchronize_session=False)
            session.commit()
            for path in paths:
                try:
                    Storage.delete_file(path)
                except Exception:
                    log.exception(
                        'failed to delete Storage path %s after attachment row deletion',
                        path,
                    )
            return len(rows)

    def delete_attachment_by_id(
        self,
        id: str,
        db: Optional[Session] = None,
    ) -> bool:
        with get_db_context(db) as session:
            row = session.query(FileAttachment).filter(FileAttachment.id == id).first()
            if row is None:
                return False
            path = row.path
            session.delete(row)
            session.commit()
            # Storage cleanup is best-effort and post-commit: if it
            # fails, we leak storage objects (cheap, gc-able later) but
            # never DB rows (which nothing else cleans up).
            try:
                Storage.delete_file(path)
            except Exception:
                log.exception(
                    'failed to delete Storage path %s after attachment row deletion',
                    path,
                )
            return True

    def delete_all_attachments(self, db: Optional[Session] = None) -> int:
        """Wipe all attachments and their Storage paths.

        Best-effort per row on Storage; the DB rows are always dropped.
        Returns the count of rows that were on the way out.
        """
        with get_db_context(db) as session:
            paths = [r.path for r in session.query(FileAttachment).all()]
            count = len(paths)
            session.query(FileAttachment).delete()
            session.commit()
            for path in paths:
                try:
                    Storage.delete_file(path)
                except Exception:
                    # Storage failures are logged but not fatal: prefer orphan
                    # storage objects over DB rows that can never be cleaned up.
                    log.exception(
                        'failed to delete Storage path %s after attachment row deletion',
                        path,
                    )
            return count


FileAttachments = FileAttachmentsTable()
