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


import json as _json
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def ingest_app(monkeypatch):
    """Mount only the integrations router with the dependency overrides
    needed to exercise the attachment-handling path.
    """
    from open_webui.routers import integrations as integrations_router
    from open_webui.utils.service_auth import LoaderPrincipal, get_integration_principal

    user = MagicMock(
        id='user-1', email='lex@gradient-ds.com', role='user', name='Lex', info={'integration_provider': 'onedrive'}
    )
    principal = LoaderPrincipal(user=user, provider_slug='onedrive')

    app = FastAPI()
    app.include_router(integrations_router.router, prefix='/api/v1/integrations')
    app.dependency_overrides[get_integration_principal] = lambda: principal

    # Provide a minimal app.state.config so the endpoint doesn't crash
    # when reading INTEGRATION_PROVIDERS (LoaderPrincipal branch).
    app.state.config = MagicMock(INTEGRATION_PROVIDERS={})

    # Knowledge layer + text-save layer are out of scope for these tests.
    monkeypatch.setattr(
        integrations_router,
        '_find_kb_by_source_id',
        lambda *_a, **_k: MagicMock(id='kb-1', name='kb', meta={}),
    )
    monkeypatch.setattr(
        integrations_router.Knowledges,
        'get_knowledge_by_id',
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        integrations_router.Knowledges,
        'get_files_by_id',
        lambda *_a, **_k: [],
    )
    # Stub the per-data-type document processors so they return a file_id
    # and status='completed' without touching the real KB / vector DB.
    for name in ('_process_parsed_text_document', '_process_chunked_text_document', '_process_full_document'):
        if hasattr(integrations_router, name):
            monkeypatch.setattr(
                integrations_router,
                name,
                lambda doc, *a, **k: {
                    'source_id': doc['source_id'] if isinstance(doc, dict) else doc.source_id,
                    'file_id': f'f-{doc["source_id"] if isinstance(doc, dict) else doc.source_id}',
                    'status': 'completed',
                },
            )

    return app


def test_ingest_endpoint_persists_attachments(ingest_app, monkeypatch):
    from open_webui.routers import integrations as integrations_router

    persisted = []

    def fake_persist(*, file_id, manifest, part_lookup):
        persisted.append((file_id, [m.part_name for m in manifest], sorted(part_lookup)))
        return integrations_router.PersistResult(saved=len(manifest), skipped=0)

    monkeypatch.setattr(integrations_router, '_persist_attachments', fake_persist)

    body = {
        'collection': {
            'source_id': 'col-1',
            'name': 'Test',
            'data_type': 'parsed_text',
        },
        'documents': [
            {
                'source_id': 'doc-1',
                'filename': 'S.ifc',
                'text': 'plain text',
                'attachments': [
                    {
                        'kind': 'plan_png',
                        'storey': 'L1',
                        'part_name': 'doc-1__plan_png__L1__0',
                        'content_type': 'image/png',
                        'caption': '',
                    },
                    {
                        'kind': 'axon_png',
                        'storey': None,
                        'part_name': 'doc-1__axon_png___1',
                        'content_type': 'image/png',
                        'caption': '',
                    },
                ],
            }
        ],
    }
    client = TestClient(ingest_app)
    res = client.post(
        '/api/v1/integrations/ingest',
        data={'data': _json.dumps(body)},
        files=[
            ('attachments', ('doc-1__plan_png__L1__0', b'fakePNG', 'image/png')),
            ('attachments', ('doc-1__axon_png___1', b'fakePNG2', 'image/png')),
        ],
    )

    assert res.status_code == 200, res.text
    assert persisted == [
        (
            'f-doc-1',
            ['doc-1__plan_png__L1__0', 'doc-1__axon_png___1'],
            sorted(['doc-1__plan_png__L1__0', 'doc-1__axon_png___1']),
        )
    ]
    body = res.json()
    doc_result = body['documents'][0] if 'documents' in body else body[0]
    assert doc_result['attachments_saved'] == 2
    assert doc_result['attachments_skipped'] == 0
