"""Router tests for the loader-authenticated warren cloud-sync endpoints:
``/integrations/stage``, ``/integrations/submit`` and
``/integrations/file-status/{file_id}`` (Task B-O4).

These three routes let the loader-worker, in ``warren`` pipeline mode, stage a
downloaded file into S3 via an OWUI-issued presigned PUT, submit one warren job
per file, and poll the File's processing status to terminal.

The tests mount the router on a throwaway app, inject a ``LoaderPrincipal`` via
the auth dependency override (bearer/header matching is covered by
``test_service_auth.py``), and mock the ``Storage`` + model collaborators so the
tests exercise only the wiring these routes add.

The canonical-key convention is the one wiring detail flagged during design: the
presigned PUT MUST target the exact S3 key ``File.path`` records and warren's
later presigned GET reads. That derivation is owned by
``S3StorageProvider.get_object_path`` and proven against ``upload_file`` in
``test_s3_get_object_path_matches_upload_file_key_convention`` below.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from open_webui.routers import integrations as integrations_router
from open_webui.services.sync.provider import file_id_prefix_for
from open_webui.utils.service_auth import LoaderPrincipal, get_integration_principal

PROVIDER = 'onedrive'
ACTING_USER_ID = 'lex-uuid-42'
BUCKET = 'test-bucket'
KEY_PREFIX = 'tenant/uploads'
PRESIGN_TTL = 3600


@pytest.fixture
def loader_principal():
    user = MagicMock()
    user.id = ACTING_USER_ID
    user.email = 'lex@gradient-ds.com'
    user.role = 'user'
    user.name = 'Lex'
    user.info = {'integration_provider': 'should-be-ignored'}
    return LoaderPrincipal(user=user, provider_slug=PROVIDER)


def _make_app(principal) -> FastAPI:
    app = FastAPI()
    app.include_router(integrations_router.router, prefix='/api/v1/integrations')
    app.state.config = SimpleNamespace(PIPELINE_PRESIGN_TTL_SECONDS=PRESIGN_TTL)
    app.dependency_overrides[get_integration_principal] = lambda: principal
    return app


@pytest.fixture
def app(loader_principal):
    return _make_app(loader_principal)


def _fake_get_object_path(object_name: str) -> str:
    """Mirror S3StorageProvider.get_object_path exactly (os.path.join key)."""
    return f's3://{BUCKET}/{os.path.join(KEY_PREFIX, object_name)}'


# --- stage ------------------------------------------------------------------


def test_stage_creates_file_at_canonical_key_and_returns_put_url(app):
    """stage on a brand-new file: create the File row at the canonical S3 key,
    link it to the KB, and return a presigned PUT for that exact key."""
    source_id = 'doc-A'
    filename = 'report.pdf'
    file_id = f'{file_id_prefix_for(PROVIDER)}{source_id}'  # onedrive-doc-A
    object_name = f'{file_id}_{filename}'
    canonical_path = _fake_get_object_path(object_name)

    presign_calls = []

    def fake_get_presigned_put_url(path, ttl, content_type):
        presign_calls.append((path, ttl, content_type))
        return 'https://s3/presigned-put?sig=abc'

    insert_calls = []
    link_calls = []

    async def fake_insert_new_file(user_id, form):
        insert_calls.append((user_id, form))
        return MagicMock()

    async def fake_add_file(kb_id, fid, uid):
        link_calls.append((kb_id, fid, uid))

    with (
        patch.object(integrations_router.Storage, 'get_object_path', side_effect=_fake_get_object_path),
        patch.object(integrations_router.Storage, 'get_presigned_put_url', side_effect=fake_get_presigned_put_url),
        patch.object(integrations_router.Files, 'get_file_by_id', new=AsyncMock(return_value=None)),
        patch.object(integrations_router.Files, 'insert_new_file', new=AsyncMock(side_effect=fake_insert_new_file)),
        patch.object(integrations_router.Files, 'update_file_path_by_id', new_callable=AsyncMock),
        patch.object(integrations_router.Files, 'update_file_metadata_by_id', new_callable=AsyncMock),
        patch.object(
            integrations_router.Knowledges, 'add_file_to_knowledge_by_id', new=AsyncMock(side_effect=fake_add_file)
        ),
    ):
        resp = TestClient(app).post(
            '/api/v1/integrations/stage',
            json={
                'knowledge_id': 'kb-uuid-1',
                'source_id': source_id,
                'filename': filename,
                'content_type': 'application/pdf',
            },
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body['file_id'] == file_id
    assert body['presigned_put_url'] == 'https://s3/presigned-put?sig=abc'

    # The one wiring detail: the presign PUT targets the canonical key that
    # File.path records and warren's later presigned GET reads.
    assert len(presign_calls) == 1
    put_path, put_ttl, put_ct = presign_calls[0]
    assert put_path == canonical_path
    assert put_path == f's3://{BUCKET}/{KEY_PREFIX}/{file_id}_{filename}'
    assert put_ttl == PRESIGN_TTL
    assert put_ct == 'application/pdf'

    # A File row was created at that path, owned by the acting user, and linked
    # to the KB.
    assert len(insert_calls) == 1
    created_user_id, created_form = insert_calls[0]
    assert created_user_id == ACTING_USER_ID
    assert created_form.id == file_id
    assert created_form.path == canonical_path
    assert created_form.filename == filename
    assert created_form.meta['content_type'] == 'application/pdf'
    assert link_calls == [('kb-uuid-1', file_id, ACTING_USER_ID)]


def test_stage_updates_stub_row_path_without_duplicating(app):
    """stage on an existing stub row (path='' from base_worker) UPDATEs the path
    to the canonical S3 key rather than inserting a twin row."""
    source_id = 'doc-B'
    filename = 'b.pdf'
    file_id = f'{file_id_prefix_for(PROVIDER)}{source_id}'
    object_name = f'{file_id}_{filename}'
    canonical_path = _fake_get_object_path(object_name)

    stub = MagicMock()
    stub.path = ''  # pending stub from base_worker._create_stub_file_rows

    update_path_calls = []

    async def fake_update_path(fid, path):
        update_path_calls.append((fid, path))

    with (
        patch.object(integrations_router.Storage, 'get_object_path', side_effect=_fake_get_object_path),
        patch.object(integrations_router.Storage, 'get_presigned_put_url', return_value='https://s3/put'),
        patch.object(integrations_router.Files, 'get_file_by_id', new=AsyncMock(return_value=stub)),
        patch.object(integrations_router.Files, 'insert_new_file', new_callable=AsyncMock) as insert_mock,
        patch.object(integrations_router.Files, 'update_file_path_by_id', new=AsyncMock(side_effect=fake_update_path)),
        patch.object(integrations_router.Files, 'update_file_metadata_by_id', new_callable=AsyncMock),
        patch.object(
            integrations_router.Knowledges, 'add_file_to_knowledge_by_id', new_callable=AsyncMock
        ) as link_mock,
    ):
        resp = TestClient(app).post(
            '/api/v1/integrations/stage',
            json={
                'knowledge_id': 'kb-uuid-1',
                'source_id': source_id,
                'filename': filename,
                'content_type': 'application/pdf',
            },
        )

    assert resp.status_code == 200, resp.text
    assert resp.json()['file_id'] == file_id
    insert_mock.assert_not_awaited()  # no twin row
    assert update_path_calls == [(file_id, canonical_path)]


def test_stage_does_not_overwrite_nonempty_path(app):
    """Idempotent re-stage: an existing row already at the canonical path must
    not trigger a redundant path write."""
    source_id = 'doc-C'
    filename = 'c.pdf'
    file_id = f'{file_id_prefix_for(PROVIDER)}{source_id}'
    canonical_path = _fake_get_object_path(f'{file_id}_{filename}')

    existing = MagicMock()
    existing.path = canonical_path  # already staged

    with (
        patch.object(integrations_router.Storage, 'get_object_path', side_effect=_fake_get_object_path),
        patch.object(integrations_router.Storage, 'get_presigned_put_url', return_value='https://s3/put'),
        patch.object(integrations_router.Files, 'get_file_by_id', new=AsyncMock(return_value=existing)),
        patch.object(integrations_router.Files, 'insert_new_file', new_callable=AsyncMock) as insert_mock,
        patch.object(integrations_router.Files, 'update_file_path_by_id', new_callable=AsyncMock) as update_path_mock,
        patch.object(integrations_router.Files, 'update_file_metadata_by_id', new_callable=AsyncMock),
        patch.object(integrations_router.Knowledges, 'add_file_to_knowledge_by_id', new_callable=AsyncMock),
    ):
        resp = TestClient(app).post(
            '/api/v1/integrations/stage',
            json={
                'knowledge_id': 'kb-uuid-1',
                'source_id': source_id,
                'filename': filename,
                'content_type': 'application/pdf',
            },
        )

    assert resp.status_code == 200, resp.text
    insert_mock.assert_not_awaited()
    update_path_mock.assert_not_awaited()


# --- submit -----------------------------------------------------------------


def test_submit_routes_to_warren_and_returns_job_id(app, loader_principal):
    """submit delegates to submit_existing_file_to_pipeline with the fetched
    File + the acting user, and returns its pipeline_job_id."""
    file_id = 'onedrive-doc-A'
    fake_file = SimpleNamespace(id=file_id, filename='report.pdf', path='s3://b/k/report.pdf', meta={})

    submit_calls = []

    async def fake_submit(request, file, knowledge_id, user):
        submit_calls.append({'file': file, 'knowledge_id': knowledge_id, 'user': user})
        return {'status': True, 'collection_name': knowledge_id, 'pipeline_job_id': 'job-99'}

    with (
        patch.object(integrations_router.Files, 'get_file_by_id', new=AsyncMock(return_value=fake_file)),
        patch.object(integrations_router, 'submit_existing_file_to_pipeline', side_effect=fake_submit),
    ):
        resp = TestClient(app).post(
            '/api/v1/integrations/submit',
            json={'file_id': file_id, 'knowledge_id': 'kb-uuid-1'},
        )

    assert resp.status_code == 200, resp.text
    assert resp.json() == {'pipeline_job_id': 'job-99'}
    assert len(submit_calls) == 1
    call = submit_calls[0]
    assert call['file'] is fake_file
    assert call['knowledge_id'] == 'kb-uuid-1'
    # A real user object (not a bare id) is forwarded — it exposes .id.
    assert call['user'].id == ACTING_USER_ID


def test_submit_404_when_file_missing(app):
    with (
        patch.object(integrations_router.Files, 'get_file_by_id', new=AsyncMock(return_value=None)),
        patch.object(integrations_router, 'submit_existing_file_to_pipeline', new=AsyncMock()) as submit_mock,
    ):
        resp = TestClient(app).post(
            '/api/v1/integrations/submit',
            json={'file_id': 'missing', 'knowledge_id': 'kb-uuid-1'},
        )

    assert resp.status_code == 404
    submit_mock.assert_not_awaited()


# --- file-status ------------------------------------------------------------


@pytest.mark.parametrize('status', ['processing', 'completed', 'error'])
def test_file_status_echoes_file_status(app, status):
    fake_file = MagicMock()
    fake_file.meta = {'status': status}
    fake_file.data = {'status': status}

    with patch.object(integrations_router.Files, 'get_file_by_id', new=AsyncMock(return_value=fake_file)):
        resp = TestClient(app).get('/api/v1/integrations/file-status/onedrive-doc-A')

    assert resp.status_code == 200, resp.text
    assert resp.json() == {'status': status}


def test_file_status_404_when_file_missing(app):
    with patch.object(integrations_router.Files, 'get_file_by_id', new=AsyncMock(return_value=None)):
        resp = TestClient(app).get('/api/v1/integrations/file-status/nope')

    assert resp.status_code == 404


# --- auth: all three reject a non-loader caller -----------------------------


@pytest.fixture
def non_loader_app():
    """Auth dep resolves to a regular user (not a LoaderPrincipal) — the machine
    routes must reject it with 403."""
    plain_user = MagicMock()
    plain_user.id = 'human-1'
    plain_user.role = 'user'
    return _make_app(plain_user)


def test_stage_rejects_non_loader(non_loader_app):
    resp = TestClient(non_loader_app).post(
        '/api/v1/integrations/stage',
        json={'knowledge_id': 'kb-1', 'source_id': 's', 'filename': 'f.pdf', 'content_type': 'application/pdf'},
    )
    assert resp.status_code == 403


def test_submit_rejects_non_loader(non_loader_app):
    resp = TestClient(non_loader_app).post(
        '/api/v1/integrations/submit',
        json={'file_id': 'onedrive-doc-A', 'knowledge_id': 'kb-1'},
    )
    assert resp.status_code == 403


def test_file_status_rejects_non_loader(non_loader_app):
    resp = TestClient(non_loader_app).get('/api/v1/integrations/file-status/onedrive-doc-A')
    assert resp.status_code == 403


# --- canonical-key convention proof (storage layer) -------------------------


def test_s3_get_object_path_matches_upload_file_key_convention():
    """get_object_path is the single source of truth for the canonical S3 path
    and MUST derive the exact same key upload_file writes and _extract_s3_key
    (used by both presign GET and PUT) inverts — otherwise the presigned PUT
    would land bytes at a key warren's presigned GET can't read."""
    from open_webui.storage.provider import S3StorageProvider

    provider = object.__new__(S3StorageProvider)  # skip boto3 client init
    provider.bucket_name = 'my-bucket'
    provider.key_prefix = 'tenant/uploads'

    object_name = 'onedrive-doc-A_report.pdf'
    path = provider.get_object_path(object_name)

    # Mirrors upload_file exactly: s3_key = os.path.join(key_prefix, filename)
    expected_key = os.path.join('tenant/uploads', object_name)
    assert path == f's3://my-bucket/{expected_key}'
    # Round-trips through the same inverter the presign methods use.
    assert provider._extract_s3_key(path) == expected_key


def test_s3_get_object_path_with_empty_prefix():
    from open_webui.storage.provider import S3StorageProvider

    provider = object.__new__(S3StorageProvider)
    provider.bucket_name = 'my-bucket'
    provider.key_prefix = ''

    path = provider.get_object_path('owui-doc_report.pdf')
    assert path == 's3://my-bucket/owui-doc_report.pdf'


def test_local_get_object_path_not_supported():
    from open_webui.storage.provider import LocalStorageProvider

    with pytest.raises(NotImplementedError):
        LocalStorageProvider.get_object_path('anything')
