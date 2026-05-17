"""Router-level test for the loader-bearer path on ``/api/v1/integrations/ingest``.

Verifies that when a call presents ``LOADER_INGEST_API_KEY`` plus the acting
headers, the ``user_id`` propagated to ``_process_*_document`` (and therefore
to ``Files.insert_new_file``) equals the ``X-Acting-User-Id`` value — not a
service account.

We mount the router on a throwaway app, override the auth dependency to inject
a real ``LoaderPrincipal``, and mock the heavy downstream collaborators so the
test exercises only the wiring this plan actually changed.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from open_webui.routers import integrations as integrations_router
from open_webui.utils.service_auth import LoaderPrincipal, get_integration_principal


@pytest.fixture
def acting_user_id() -> str:
    return 'lex-uuid-42'


@pytest.fixture
def loader_principal(acting_user_id):
    user = MagicMock()
    user.id = acting_user_id
    user.email = 'lex@gradient-ds.com'
    user.role = 'user'
    user.name = 'Lex'
    user.info = {'integration_provider': 'should-be-ignored'}
    return LoaderPrincipal(user=user, provider_slug='onedrive')


@pytest.fixture
def app(loader_principal):
    """FastAPI app with the integrations router and the auth dep overridden."""
    app = FastAPI()
    app.include_router(integrations_router.router, prefix='/api/v1/integrations')

    # Provide the minimum app.state.config that ingest_documents reads.
    app.state.config = SimpleNamespace(
        INTEGRATION_PROVIDERS={
            'onedrive': {
                'max_documents_per_request': 50,
                'max_files_per_kb': 1000,
                'custom_metadata_fields': [],
            }
        },
    )

    # Inject the LoaderPrincipal directly — bypass the bearer/header check
    # (covered by test_service_auth.py) so this test focuses on user_id flow.
    app.dependency_overrides[get_integration_principal] = lambda: loader_principal

    return app


def test_ingest_with_loader_bearer_attributes_files_to_acting_user(app, loader_principal, acting_user_id):
    fake_kb = MagicMock()
    fake_kb.id = 'kb-uuid-1'
    fake_kb.meta = {'integration': {'data_type': 'chunked_text'}}
    fake_kb.name = 'Test KB'
    fake_kb.description = ''
    fake_kb.type = 'onedrive'

    captured = {}

    def fake_process_chunked_text_document(*, request, knowledge_id, provider, doc, user_id, original_file=None):
        captured.setdefault('calls', []).append(
            {'knowledge_id': knowledge_id, 'provider': provider, 'user_id': user_id, 'source_id': doc.source_id}
        )
        return {'source_id': doc.source_id, 'file_id': f'{provider}-{doc.source_id}', 'status': 'created'}

    payload = {
        'collection': {
            'source_id': 'onedrive-drive-1',
            'name': 'Test KB',
            'data_type': 'chunked_text',
        },
        'documents': [
            {
                'source_id': 'doc-A',
                'filename': 'a.txt',
                'content_type': 'text/plain',
                'chunks': ['hello', 'world'],
            },
            {
                'source_id': 'doc-B',
                'filename': 'b.txt',
                'content_type': 'text/plain',
                'chunks': ['foo'],
            },
        ],
    }

    with (
        patch.object(integrations_router, '_find_kb_by_source_id', return_value=fake_kb),
        patch.object(
            integrations_router, '_process_chunked_text_document', side_effect=fake_process_chunked_text_document
        ),
        patch.object(integrations_router.Knowledges, 'get_files_by_id', return_value=[]),
        patch.object(integrations_router.Knowledges, 'update_knowledge_by_id', return_value=fake_kb),
    ):
        client = TestClient(app)
        resp = client.post(
            '/api/v1/integrations/ingest',
            data={'data': json.dumps(payload)},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body['knowledge_id'] == 'kb-uuid-1'
    assert body['provider'] == 'onedrive'
    assert body['data_type'] == 'chunked_text'
    assert body['total'] == 2
    assert body['created'] == 2
    assert body['errors'] == 0

    # The critical assertion: every call to the processor used the acting user's id.
    assert len(captured.get('calls', [])) == 2
    for call in captured['calls']:
        assert call['user_id'] == acting_user_id, (
            f'Expected user_id={acting_user_id} (X-Acting-User-Id), got {call["user_id"]}'
        )
        assert call['provider'] == 'onedrive'  # from LoaderPrincipal.provider_slug, not user.info


def test_ingest_routes_original_files_to_chunked_text_processor(app, loader_principal):
    """``original_files`` is matched to documents by ``source_id`` and the
    matched UploadFile is forwarded to ``_process_chunked_text_document``.
    Documents without bytes get ``original_file=None`` (back-compat).
    """
    fake_kb = MagicMock()
    fake_kb.id = 'kb-uuid-1'
    fake_kb.meta = {'integration': {'data_type': 'chunked_text'}}
    fake_kb.name = 'Test KB'
    fake_kb.description = ''
    fake_kb.type = 'onedrive'

    captured: dict = {}

    def fake_process(*, request, knowledge_id, provider, doc, user_id, original_file=None):
        captured.setdefault('calls', []).append(
            {
                'source_id': doc.source_id,
                'has_original_file': original_file is not None,
                'original_filename': getattr(original_file, 'filename', None),
            }
        )
        return {'source_id': doc.source_id, 'file_id': f'{provider}-{doc.source_id}', 'status': 'created'}

    payload = {
        'collection': {
            'source_id': 'onedrive-drive-1',
            'name': 'Test KB',
            'data_type': 'chunked_text',
        },
        'documents': [
            {'source_id': 'doc-A', 'filename': 'a.pdf', 'content_type': 'application/pdf', 'chunks': ['hi']},
            {'source_id': 'doc-B', 'filename': 'b.pdf', 'content_type': 'application/pdf', 'chunks': ['hi']},
        ],
    }

    with (
        patch.object(integrations_router, '_find_kb_by_source_id', return_value=fake_kb),
        patch.object(integrations_router, '_process_chunked_text_document', side_effect=fake_process),
        patch.object(integrations_router.Knowledges, 'get_files_by_id', return_value=[]),
        patch.object(integrations_router.Knowledges, 'update_knowledge_by_id', return_value=fake_kb),
    ):
        client = TestClient(app)
        # Multipart with one ``data`` field carrying JSON and one
        # ``original_files`` part for doc-A only. doc-B intentionally
        # has no companion blob — we expect original_file=None for it.
        resp = client.post(
            '/api/v1/integrations/ingest',
            data={'data': json.dumps(payload)},
            files=[
                ('original_files', ('doc-A', b'%PDF-1.7\nfake', 'application/pdf')),
            ],
        )

    assert resp.status_code == 200, resp.text
    by_source = {c['source_id']: c for c in captured['calls']}
    assert by_source['doc-A']['has_original_file'] is True
    assert by_source['doc-A']['original_filename'] == 'doc-A'
    assert by_source['doc-B']['has_original_file'] is False


def test_process_chunked_text_uploads_bytes_and_sets_path(loader_principal, acting_user_id):
    """``_process_chunked_text_document`` calls ``Storage.upload_file`` when
    ``original_file`` is provided and forwards the returned path to
    ``_create_or_update_file_record``.
    """
    from io import BytesIO

    from fastapi import UploadFile

    captured: dict = {}

    def fake_create_or_update(**kwargs):
        captured.update(kwargs)
        return 'created'

    def fake_upload_file(file_obj, filename, tags):
        return file_obj.read(), f's3://bucket/{filename}'

    fake_request = MagicMock()
    fake_request.app.state.config = SimpleNamespace()

    upload = UploadFile(filename='doc-A', file=BytesIO(b'%PDF-1.7\nfake'))
    doc = integrations_router.ChunkedTextDocument(
        source_id='doc-A', filename='a.pdf', content_type='application/pdf', chunks=['hi']
    )

    with (
        patch.object(integrations_router.Storage, 'upload_file', side_effect=fake_upload_file),
        patch.object(integrations_router, '_create_or_update_file_record', side_effect=fake_create_or_update),
        patch.object(integrations_router, '_delete_old_vectors'),
        patch.object(integrations_router, 'save_docs_to_vector_db'),
        patch.object(integrations_router.Files, 'update_file_data_by_id'),
    ):
        result = integrations_router._process_chunked_text_document(
            request=fake_request,
            knowledge_id='kb-uuid-1',
            provider='onedrive',
            doc=doc,
            user_id=acting_user_id,
            original_file=upload,
        )

    assert result['status'] == 'created'
    assert captured['file_path'] == 's3://bucket/onedrive-doc-A_a.pdf'
    assert captured['file_id'] == 'onedrive-doc-A'


def test_process_chunked_text_without_original_file_keeps_path_empty(acting_user_id):
    """Back-compat: callers (push integrations) that don't ship bytes still work."""
    captured: dict = {}

    def fake_create_or_update(**kwargs):
        captured.update(kwargs)
        return 'created'

    fake_request = MagicMock()
    fake_request.app.state.config = SimpleNamespace()
    doc = integrations_router.ChunkedTextDocument(
        source_id='doc-A', filename='a.txt', content_type='text/plain', chunks=['hi']
    )

    with (
        patch.object(integrations_router, '_create_or_update_file_record', side_effect=fake_create_or_update),
        patch.object(integrations_router, '_delete_old_vectors'),
        patch.object(integrations_router, 'save_docs_to_vector_db'),
        patch.object(integrations_router.Files, 'update_file_data_by_id'),
    ):
        integrations_router._process_chunked_text_document(
            request=fake_request,
            knowledge_id='kb-uuid-1',
            provider='onedrive',
            doc=doc,
            user_id=acting_user_id,
        )

    assert captured['file_path'] == ''


def test_create_or_update_file_record_updates_path_when_stub_row_gets_bytes(acting_user_id):
    """The stub File row created by ``services/sync/base_worker._create_stub_file_rows``
    has ``path=''``. When the loader-worker's /ingest callback arrives with
    bytes, ``_create_or_update_file_record`` must call
    ``Files.update_file_path_by_id`` so the citation-modal preview can serve
    them. The previous implementation only updated metadata in the existing-
    row branch, so previews stayed broken until manual re-sync.
    """
    stub_row = MagicMock()
    stub_row.path = ''  # pending stub from base_worker._create_stub_file_rows

    update_path_calls: list = []

    def fake_update_path(file_id, file_path):
        update_path_calls.append((file_id, file_path))

    doc = integrations_router.ChunkedTextDocument(
        source_id='doc-A', filename='a.pdf', content_type='application/pdf', chunks=['hi']
    )

    with (
        patch.object(integrations_router.Files, 'get_file_by_id', return_value=stub_row),
        patch.object(integrations_router.Files, 'update_file_metadata_by_id'),
        patch.object(integrations_router.Files, 'update_file_data_by_id'),
        patch.object(integrations_router.Files, 'update_file_path_by_id', side_effect=fake_update_path),
    ):
        status = integrations_router._create_or_update_file_record(
            file_id='onedrive-doc-A',
            doc=doc,
            content_text='hi',
            file_path='s3://bucket/onedrive-doc-A_a.pdf',
            provider='onedrive',
            knowledge_id='kb-uuid-1',
            user_id=acting_user_id,
        )

    assert status == 'updated'
    assert update_path_calls == [('onedrive-doc-A', 's3://bucket/onedrive-doc-A_a.pdf')]


def test_create_or_update_file_record_does_not_overwrite_path_with_empty(acting_user_id):
    """A re-sync that drops the bytes (e.g. kill-switch flipped off) must not
    silently break previews on rows that already had a valid path.
    """
    existing_row = MagicMock()
    existing_row.path = 's3://bucket/already-there'

    update_path_calls: list = []
    doc = integrations_router.ChunkedTextDocument(
        source_id='doc-A', filename='a.pdf', content_type='application/pdf', chunks=['hi']
    )

    with (
        patch.object(integrations_router.Files, 'get_file_by_id', return_value=existing_row),
        patch.object(integrations_router.Files, 'update_file_metadata_by_id'),
        patch.object(integrations_router.Files, 'update_file_data_by_id'),
        patch.object(
            integrations_router.Files,
            'update_file_path_by_id',
            side_effect=lambda fid, p: update_path_calls.append((fid, p)),
        ),
    ):
        integrations_router._create_or_update_file_record(
            file_id='onedrive-doc-A',
            doc=doc,
            content_text='hi',
            file_path='',
            provider='onedrive',
            knowledge_id='kb-uuid-1',
            user_id=acting_user_id,
        )

    assert update_path_calls == []
