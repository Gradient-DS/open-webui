"""Tests for the attachment-aware extension of /api/v1/integrations/ingest."""

from __future__ import annotations

import pytest


def test_ingest_attachment_manifest_pydantic_shape():
    from open_webui.routers.integrations import IngestAttachmentManifest

    m = IngestAttachmentManifest(
        kind='plan_png',
        part_name='doc-1__plan_png__Level_1__0',
    )
    assert m.content_type == 'image/png'
    assert m.storey is None
    assert m.caption == ''


def test_ingest_attachment_manifest_rejects_missing_kind():
    from open_webui.routers.integrations import IngestAttachmentManifest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        IngestAttachmentManifest(part_name='x')


def test_ingest_document_base_accepts_attachments_list():
    from open_webui.routers.integrations import IngestDocumentBase, IngestAttachmentManifest

    doc = IngestDocumentBase(
        source_id='doc-1',
        filename='S.ifc',
        attachments=[
            IngestAttachmentManifest(kind='plan_png', storey='L1', part_name='p1'),
            IngestAttachmentManifest(kind='axon_png', part_name='ax'),
        ],
    )
    assert len(doc.attachments) == 2


def test_ingest_document_base_attachments_default_empty():
    from open_webui.routers.integrations import IngestDocumentBase

    doc = IngestDocumentBase(source_id='doc-1', filename='S.ifc')
    assert doc.attachments == []
