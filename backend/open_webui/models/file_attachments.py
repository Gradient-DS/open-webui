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

from open_webui.internal.db import Base, get_db_context
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
            for r in rows:
                try:
                    Storage.delete_file(r.path)
                except Exception:
                    log.exception('failed to delete Storage path %s for attachment %s', r.path, r.id)
                # Storage failure is logged but not fatal: prefer an orphan
                # storage object over a DB row that can never be cleaned up.
                session.delete(r)
            session.commit()
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
            try:
                Storage.delete_file(row.path)
            except Exception:
                log.exception('failed to delete Storage path %s for attachment %s', row.path, row.id)
            # Storage failure is logged but not fatal: prefer an orphan
            # storage object over a DB row that can never be cleaned up.
            session.delete(row)
            session.commit()
            return True


FileAttachments = FileAttachmentsTable()
