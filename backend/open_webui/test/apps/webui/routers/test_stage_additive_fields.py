"""Tests for the sync-daemon additive fields on ``POST /integrations/stage``:
``file_hash`` (staged as ``meta.pending_cloud_hash``, R4 staged-promote) and
``directory_id`` (KnowledgeFile placement in an upstream knowledge_directory,
D-8). Both are optional — when absent the request must behave byte-identically
to the pre-daemon contract (R12), proven by the no-field regression tests.

Mirrors ``test_integrations_stage_submit.py``: router on a throwaway app,
``LoaderPrincipal`` injected via dependency override, Storage/model seams
patched.
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
KB_ID = 'kb-uuid-1'
BUCKET = 'test-bucket'
KEY_PREFIX = 'tenant/uploads'


@pytest.fixture
def loader_principal():
    user = MagicMock()
    user.id = ACTING_USER_ID
    user.role = 'user'
    return LoaderPrincipal(user=user, provider_slug=PROVIDER)


@pytest.fixture(autouse=True)
def config_values(monkeypatch):
    values = {
        'rag.distributed_doc_pipeline_sync_enabled': True,
        'doc_pipeline.presign_ttl_seconds': 3600,
    }

    async def fake_get(key, default=None):
        return values.get(key, default)

    monkeypatch.setattr(integrations_router.Config, 'get', staticmethod(fake_get))
    return values


@pytest.fixture
def app(loader_principal):
    app = FastAPI()
    app.include_router(integrations_router.router, prefix='/api/v1/integrations')
    app.dependency_overrides[get_integration_principal] = lambda: loader_principal
    return app


def _fake_get_object_path(object_name: str) -> str:
    return f's3://{BUCKET}/{os.path.join(KEY_PREFIX, object_name)}'


class _Seams:
    """Container for the patched Files/Knowledges/Storage collaborators."""

    def __init__(self, stack, existing_file, directory, has_file):
        self.get_file_by_id = stack.enter_context(
            patch.object(integrations_router.Files, 'get_file_by_id', new=AsyncMock(return_value=existing_file))
        )
        self.insert_new_file = stack.enter_context(
            patch.object(integrations_router.Files, 'insert_new_file', new_callable=AsyncMock)
        )
        self.update_file_path = stack.enter_context(
            patch.object(integrations_router.Files, 'update_file_path_by_id', new_callable=AsyncMock)
        )
        self.update_file_metadata = stack.enter_context(
            patch.object(integrations_router.Files, 'update_file_metadata_by_id', new_callable=AsyncMock)
        )
        self.update_file_name = stack.enter_context(
            patch.object(integrations_router.Files, 'update_file_name_by_id', new_callable=AsyncMock)
        )
        self.add_file = stack.enter_context(
            patch.object(integrations_router.Knowledges, 'add_file_to_knowledge_by_id', new_callable=AsyncMock)
        )
        self.get_directory_by_id = stack.enter_context(
            patch.object(integrations_router.Knowledges, 'get_directory_by_id', new=AsyncMock(return_value=directory))
        )
        self.has_file = stack.enter_context(
            patch.object(integrations_router.Knowledges, 'has_file', new=AsyncMock(return_value=has_file))
        )
        self.move_file = stack.enter_context(
            patch.object(integrations_router.Knowledges, 'move_file_to_directory', new=AsyncMock(return_value=True))
        )
        stack.enter_context(
            patch.object(integrations_router.Storage, 'get_object_path', side_effect=_fake_get_object_path)
        )
        stack.enter_context(
            patch.object(integrations_router.Storage, 'get_presigned_put_url', return_value='https://s3/put')
        )


@pytest.fixture
def seams_factory():
    from contextlib import ExitStack

    def _make(stack, *, existing_file=None, directory=None, has_file=False):
        return _Seams(stack, existing_file, directory, has_file)

    def factory(**kwargs):
        stack = ExitStack()
        seams = _make(stack, **kwargs)
        return stack, seams

    return factory


def _stage(app, **fields):
    payload = {
        'knowledge_id': KB_ID,
        'source_id': 'doc-A',
        'filename': 'report.pdf',
        'content_type': 'application/pdf',
    }
    payload.update(fields)
    return TestClient(app).post('/api/v1/integrations/stage', json=payload)


FILE_ID = f'{file_id_prefix_for(PROVIDER)}doc-A'


# --- file_hash → pending_cloud_hash ------------------------------------------


def test_file_hash_staged_on_new_row_branch(app, seams_factory):
    stack, seams = seams_factory(existing_file=None)
    with stack:
        resp = _stage(app, file_hash='provider-hash-1')

    assert resp.status_code == 200, resp.text
    seams.insert_new_file.assert_awaited_once()
    _, form = seams.insert_new_file.await_args.args
    assert form.meta['pending_cloud_hash'] == 'provider-hash-1'
    # cloud_hash is never written at stage time — only /ingest promotes (R4).
    assert 'cloud_hash' not in form.meta


def test_file_hash_staged_on_existing_row_branch(app, seams_factory):
    existing = MagicMock()
    existing.path = _fake_get_object_path(f'{FILE_ID}_report.pdf')
    existing.filename = 'report.pdf'
    stack, seams = seams_factory(existing_file=existing)
    with stack:
        resp = _stage(app, file_hash='provider-hash-2')

    assert resp.status_code == 200, resp.text
    seams.insert_new_file.assert_not_awaited()
    seams.update_file_metadata.assert_awaited_once_with(
        FILE_ID,
        {
            'content_type': 'application/pdf',
            'collection_name': KB_ID,
            'pending_cloud_hash': 'provider-hash-2',
        },
    )


# --- directory_id placement ---------------------------------------------------


def test_directory_id_places_file_after_link(app, seams_factory):
    directory = SimpleNamespace(id='dir-1', knowledge_id=KB_ID)
    stack, seams = seams_factory(existing_file=None, directory=directory, has_file=True)
    with stack:
        resp = _stage(app, directory_id='dir-1')

    assert resp.status_code == 200, resp.text
    seams.move_file.assert_awaited_once_with(KB_ID, FILE_ID, 'dir-1')
    # New-row branch already linked the KB — no duplicate link call.
    seams.add_file.assert_awaited_once_with(KB_ID, FILE_ID, ACTING_USER_ID)


def test_directory_id_links_shared_row_not_yet_in_kb(app, seams_factory):
    """Existing shared File row without a KnowledgeFile join in this KB: the
    directory placement needs the link, so stage creates it (idempotent)."""
    existing = MagicMock()
    existing.path = _fake_get_object_path(f'{FILE_ID}_report.pdf')
    directory = SimpleNamespace(id='dir-1', knowledge_id=KB_ID)
    stack, seams = seams_factory(existing_file=existing, directory=directory, has_file=False)
    with stack:
        resp = _stage(app, directory_id='dir-1')

    assert resp.status_code == 200, resp.text
    seams.add_file.assert_awaited_once_with(KB_ID, FILE_ID, ACTING_USER_ID)
    seams.move_file.assert_awaited_once_with(KB_ID, FILE_ID, 'dir-1')


def test_directory_from_another_kb_rejected_before_any_write(app, seams_factory):
    directory = SimpleNamespace(id='dir-x', knowledge_id='some-other-kb')
    stack, seams = seams_factory(existing_file=None, directory=directory)
    with stack:
        resp = _stage(app, directory_id='dir-x')

    assert resp.status_code == 400
    assert 'dir-x' in resp.json()['detail']
    seams.insert_new_file.assert_not_awaited()
    seams.update_file_metadata.assert_not_awaited()
    seams.move_file.assert_not_awaited()


def test_unknown_directory_rejected(app, seams_factory):
    stack, seams = seams_factory(existing_file=None, directory=None)
    with stack:
        resp = _stage(app, directory_id='dir-missing')

    assert resp.status_code == 400
    seams.insert_new_file.assert_not_awaited()


# --- both absent → pre-daemon behavior byte-identical --------------------------


def test_no_fields_new_row_meta_has_no_pending_cloud_hash(app, seams_factory):
    stack, seams = seams_factory(existing_file=None)
    with stack:
        resp = _stage(app)

    assert resp.status_code == 200, resp.text
    _, form = seams.insert_new_file.await_args.args
    assert form.meta == {
        'name': 'report.pdf',
        'content_type': 'application/pdf',
        'collection_name': KB_ID,
        'source': PROVIDER,
        'source_id': 'doc-A',
    }
    seams.get_directory_by_id.assert_not_awaited()
    seams.has_file.assert_not_awaited()
    seams.move_file.assert_not_awaited()


def test_no_fields_existing_row_meta_update_unchanged(app, seams_factory):
    existing = MagicMock()
    existing.path = _fake_get_object_path(f'{FILE_ID}_report.pdf')
    existing.filename = 'report.pdf'
    stack, seams = seams_factory(existing_file=existing)
    with stack:
        resp = _stage(app)

    assert resp.status_code == 200, resp.text
    seams.update_file_metadata.assert_awaited_once_with(
        FILE_ID,
        {'content_type': 'application/pdf', 'collection_name': KB_ID},
    )
    seams.move_file.assert_not_awaited()
    # No relative_path sent → no join-row refresh, no rename touch (R12).
    seams.add_file.assert_not_awaited()
    seams.update_file_name.assert_not_awaited()


# --- relative_path (D-8 bridge) + rename refresh -------------------------------


def test_relative_path_staged_on_new_row(app, seams_factory):
    stack, seams = seams_factory(existing_file=None)
    with stack:
        resp = _stage(app, relative_path='docs/api/report.pdf')

    assert resp.status_code == 200, resp.text
    _, form = seams.insert_new_file.await_args.args
    assert form.meta['relative_path'] == 'docs/api/report.pdf'


def test_relative_path_updates_meta_and_refreshes_join_row(app, seams_factory):
    existing = MagicMock()
    existing.path = _fake_get_object_path(f'{FILE_ID}_report.pdf')
    existing.filename = 'report.pdf'
    stack, seams = seams_factory(existing_file=existing)
    with stack:
        resp = _stage(app, relative_path='moved/report.pdf')

    assert resp.status_code == 200, resp.text
    meta_update = seams.update_file_metadata.await_args.args[1]
    assert meta_update['relative_path'] == 'moved/report.pdf'
    # The upsert refreshes knowledge_file.relative_path from the meta above.
    seams.add_file.assert_awaited_once_with(KB_ID, FILE_ID, ACTING_USER_ID)


def test_provider_rename_refreshes_filename(app, seams_factory):
    existing = MagicMock()
    existing.path = _fake_get_object_path(f'{FILE_ID}_report.pdf')
    existing.filename = 'Old title'
    stack, seams = seams_factory(existing_file=existing)
    with stack:
        resp = _stage(app)

    assert resp.status_code == 200, resp.text
    seams.update_file_name.assert_awaited_once_with(FILE_ID, 'report.pdf')
