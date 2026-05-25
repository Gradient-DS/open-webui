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
    assert m.part_name == 'doc-1__plan_png__Level_1__0'


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
    assert isinstance(doc.attachments[0], IngestAttachmentManifest)
    assert doc.attachments[0].kind == 'plan_png'
    assert doc.attachments[0].storey == 'L1'
    assert doc.attachments[1].kind == 'axon_png'
    assert doc.attachments[1].part_name == 'ax'


def test_ingest_attachment_manifest_rejects_missing_part_name():
    from open_webui.routers.integrations import IngestAttachmentManifest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        IngestAttachmentManifest(kind='plan_png')


def test_ingest_document_base_attachments_default_empty():
    from open_webui.routers.integrations import IngestDocumentBase

    doc = IngestDocumentBase(source_id='doc-1', filename='S.ifc')
    assert doc.attachments == []


from io import BytesIO
from unittest.mock import MagicMock, call, patch
from fastapi import UploadFile


def _upload_file(filename: str, content: bytes = b'\x89PNG\r\n\x1a\n') -> UploadFile:
    uf = UploadFile(filename=filename, file=BytesIO(content))
    return uf


def _manifest(kind: str, storey, part_name: str):
    from open_webui.routers.integrations import IngestAttachmentManifest

    return IngestAttachmentManifest(kind=kind, storey=storey, part_name=part_name)


@patch('open_webui.routers.integrations.FileAttachments')
@patch('open_webui.routers.integrations.Storage')
def test_persist_attachments_happy_path(mock_storage, mock_attachments):
    from open_webui.routers.integrations import _persist_attachments

    mock_storage.upload_file.side_effect = [
        (b'X', 'uploads/0.png'),
        (b'X', 'uploads/1.png'),
        (b'X', 'uploads/2.png'),
    ]
    parts = {f'doc1__plan_png__L{i}__{i}': _upload_file(f'doc1__plan_png__L{i}__{i}') for i in range(3)}
    manifest = [_manifest('plan_png', f'L{i}', f'doc1__plan_png__L{i}__{i}') for i in range(3)]

    saved, skipped = _persist_attachments(
        file_id='file-1',
        manifest=manifest,
        part_lookup=parts,
    )

    assert (saved, skipped) == (3, 0)
    mock_attachments.delete_attachments_by_file_id.assert_called_once_with('file-1')
    assert mock_storage.upload_file.call_count == 3
    assert mock_attachments.insert_new_attachment.call_count == 3


@patch('open_webui.routers.integrations.FileAttachments')
@patch('open_webui.routers.integrations.Storage')
def test_persist_attachments_skips_missing_part(mock_storage, mock_attachments, caplog):
    import logging
    from open_webui.routers.integrations import _persist_attachments

    parts = {}
    manifest = [_manifest('plan_png', 'L1', 'doc1__plan_png__L1__0')]

    with caplog.at_level(logging.WARNING, logger='open_webui.routers.integrations'):
        saved, skipped = _persist_attachments(
            file_id='file-1',
            manifest=manifest,
            part_lookup=parts,
        )

    assert (saved, skipped) == (0, 1)
    assert any('missing part' in r.message for r in caplog.records)
    mock_storage.upload_file.assert_not_called()
    mock_attachments.insert_new_attachment.assert_not_called()


@patch('open_webui.routers.integrations.FileAttachments')
@patch('open_webui.routers.integrations.Storage')
def test_persist_attachments_skips_storage_failure(mock_storage, mock_attachments, caplog):
    import logging
    from open_webui.routers.integrations import _persist_attachments

    mock_storage.upload_file.side_effect = RuntimeError('disk full')
    parts = {'p1': _upload_file('p1')}
    manifest = [_manifest('plan_png', 'L1', 'p1')]

    with caplog.at_level(logging.ERROR, logger='open_webui.routers.integrations'):
        saved, skipped = _persist_attachments(
            file_id='file-1',
            manifest=manifest,
            part_lookup=parts,
        )

    assert (saved, skipped) == (0, 1)
    assert any('storage upload failed' in r.message for r in caplog.records)
    mock_attachments.insert_new_attachment.assert_not_called()


@patch('open_webui.routers.integrations.FileAttachments')
@patch('open_webui.routers.integrations.Storage')
def test_persist_attachments_replaces_on_reupload(mock_storage, mock_attachments):
    from open_webui.routers.integrations import _persist_attachments

    mock_storage.upload_file.return_value = (b'X', 'uploads/new.png')
    parts = {'p1': _upload_file('p1')}
    manifest = [_manifest('plan_png', 'L1', 'p1')]

    _persist_attachments(file_id='file-1', manifest=manifest, part_lookup=parts)

    # The prior-batch deletion must fire BEFORE any new insert so a
    # re-upload never leaves both old and new rows.
    assert mock_attachments.method_calls[0] == call.delete_attachments_by_file_id('file-1')
