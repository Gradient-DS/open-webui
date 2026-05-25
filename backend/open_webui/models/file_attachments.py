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
import uuid
from typing import Optional

from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, Column, Integer, String, Text
from sqlalchemy.orm import Session

from open_webui.internal.db import Base, get_db
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
# CRUD wrapper (stubs — implemented in Task 3)
####################


class FileAttachmentsTable:
    def insert_new_attachment(
        self,
        form: FileAttachmentForm,
        db: Optional[Session] = None,
    ) -> Optional[FileAttachmentModel]:
        raise NotImplementedError

    def get_attachments_by_file_id(
        self,
        file_id: str,
        db: Optional[Session] = None,
    ) -> list[FileAttachmentModel]:
        raise NotImplementedError

    def get_attachment_by_id(
        self,
        id: str,
        db: Optional[Session] = None,
    ) -> Optional[FileAttachmentModel]:
        raise NotImplementedError

    def delete_attachments_by_file_id(
        self,
        file_id: str,
        db: Optional[Session] = None,
    ) -> int:
        raise NotImplementedError

    def delete_attachment_by_id(
        self,
        id: str,
        db: Optional[Session] = None,
    ) -> bool:
        raise NotImplementedError


FileAttachments = FileAttachmentsTable()
