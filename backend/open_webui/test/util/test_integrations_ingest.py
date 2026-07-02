"""Tests for the per-file (non-KB) dispatch on /api/v1/integrations/ingest.

Track 1 (chat attachments through warren, Fork a): warren POSTs chunked_text
back with ``collection.target='file'``; /ingest must embed into
``file-{file_id}`` and update the existing File row WITHOUT creating/looking-up
a KB, running the KB file-limit check, or linking the file to a KB. The default
``target='knowledge'`` path must stay byte-identical.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from open_webui.routers import integrations as integrations_router
from open_webui.routers.integrations import ChunkedTextDocument


def _chunked_doc(source_id: str = 'file-abc', filename: str = 'report.pdf') -> ChunkedTextDocument:
    return ChunkedTextDocument(source_id=source_id, filename=filename, chunks=['chunk one', 'chunk two'])


# --- IngestCollection.target discriminator ---------------------------------


def test_ingest_collection_target_defaults_to_knowledge():
    from open_webui.routers.integrations import IngestCollection

    col = IngestCollection(source_id='kb-1', name='KB')
    assert col.target == 'knowledge'


def test_ingest_collection_accepts_file_target():
    from open_webui.routers.integrations import IngestCollection

    col = IngestCollection(source_id='file-abc', name='report.pdf', target='file')
    assert col.target == 'file'


# --- _process_chunked_text_document: per-file embed target -----------------


def _patch_persistence(monkeypatch, *, existing_file=None, save_return=True):
    """Patch the DB + vector-DB seams. Returns (save_mock, files, knowledges)."""
    save_mock = MagicMock(return_value=save_return)
    monkeypatch.setattr(integrations_router, 'save_docs_to_vector_db', save_mock)

    files = MagicMock()
    files.get_file_by_id = AsyncMock(return_value=existing_file)
    files.insert_new_file = AsyncMock()
    files.update_file_metadata_by_id = AsyncMock()
    files.update_file_data_by_id = AsyncMock()
    files.update_file_path_by_id = AsyncMock()
    files.set_status = AsyncMock()
    monkeypatch.setattr(integrations_router, 'Files', files)

    knowledges = MagicMock()
    knowledges.add_file_to_knowledge_by_id = AsyncMock()
    knowledges.set_path_fields_by_file_id = AsyncMock()
    monkeypatch.setattr(integrations_router, 'Knowledges', knowledges)

    return save_mock, files, knowledges


@pytest.mark.asyncio
async def test_perfile_embeds_into_file_collection_add_false(monkeypatch):
    save_mock, files, knowledges = _patch_persistence(monkeypatch)

    result = await integrations_router._process_chunked_text_document(
        request=MagicMock(),
        knowledge_id=None,
        provider='owui_upload',
        doc=_chunked_doc(source_id='file-abc'),
        user_id='user-1',
        collection_name='file-file-abc',
        add=False,
    )

    assert result == {'source_id': 'file-abc', 'file_id': 'file-abc', 'status': 'created'}
    kwargs = save_mock.call_args.kwargs
    assert kwargs['collection_name'] == 'file-file-abc'  # per-file cache collection
    assert kwargs['add'] is False  # fresh collection, native chat parity
    assert kwargs['split'] is False  # already chunked by warren
    files.set_status.assert_awaited_with('file-abc', 'completed', error=None)


@pytest.mark.asyncio
async def test_perfile_never_links_to_kb(monkeypatch):
    save_mock, files, knowledges = _patch_persistence(monkeypatch)

    await integrations_router._process_chunked_text_document(
        request=MagicMock(),
        knowledge_id=None,
        provider='owui_upload',
        doc=_chunked_doc(source_id='file-abc'),
        user_id='user-1',
        collection_name='file-file-abc',
        add=False,
    )

    knowledges.add_file_to_knowledge_by_id.assert_not_called()
    knowledges.set_path_fields_by_file_id.assert_not_called()


@pytest.mark.asyncio
async def test_perfile_owui_upload_identity_updates_existing_row(monkeypatch):
    """owui_upload empty prefix ⇒ file_id == source_id ⇒ update, not duplicate."""
    existing = MagicMock(meta={}, path='')
    save_mock, files, knowledges = _patch_persistence(monkeypatch, existing_file=existing)

    result = await integrations_router._process_chunked_text_document(
        request=MagicMock(),
        knowledge_id=None,
        provider='owui_upload',
        doc=_chunked_doc(source_id='file-abc'),
        user_id='user-1',
        collection_name='file-file-abc',
        add=False,
    )

    assert result['status'] == 'updated'
    assert result['file_id'] == 'file-abc'
    files.insert_new_file.assert_not_called()  # no twin row created
    files.update_file_data_by_id.assert_awaited()  # content refreshed on the existing row
    knowledges.add_file_to_knowledge_by_id.assert_not_called()
    knowledges.set_path_fields_by_file_id.assert_not_called()


@pytest.mark.asyncio
async def test_perfile_bypass_skips_embed_but_stores_content(monkeypatch):
    save_mock, files, knowledges = _patch_persistence(monkeypatch)

    result = await integrations_router._process_chunked_text_document(
        request=MagicMock(),
        knowledge_id=None,
        provider='owui_upload',
        doc=_chunked_doc(source_id='file-abc'),
        user_id='user-1',
        collection_name='file-file-abc',
        add=False,
        skip_embed=True,
    )

    assert result['status'] == 'created'
    save_mock.assert_not_called()  # BYPASS: no vectors written
    files.insert_new_file.assert_awaited_once()  # content stored on the File row
    files.set_status.assert_awaited_with('file-abc', 'completed', error=None)


@pytest.mark.asyncio
async def test_kb_path_unchanged_regression(monkeypatch):
    """Default (KB) caller: embed into the KB collection, add=True, link to KB."""
    save_mock, files, knowledges = _patch_persistence(monkeypatch)

    await integrations_router._process_chunked_text_document(
        request=MagicMock(),
        knowledge_id='kb-1',
        provider='confluence',
        doc=_chunked_doc(source_id='doc-1'),
        user_id='user-1',
    )

    kwargs = save_mock.call_args.kwargs
    assert kwargs['collection_name'] == 'kb-1'  # embed into the KB collection
    assert kwargs['add'] is True
    knowledges.add_file_to_knowledge_by_id.assert_awaited_once_with('kb-1', 'confluence-doc-1', 'user-1')


@pytest.mark.asyncio
async def test_kb_path_chunked_success_clears_pipeline_bookkeeping(monkeypatch):
    """KB-path chunked ingest mirrors the per-file path: a completed row must
    drop out of the reconciler's candidate query, and stale pipeline job ids
    must not linger on completed rows."""
    save_mock, files, knowledges = _patch_persistence(monkeypatch)

    result = await integrations_router._process_chunked_text_document(
        request=MagicMock(),
        knowledge_id='kb-1',
        provider='onedrive',
        doc=_chunked_doc(source_id='item-1'),
        user_id='user-1',
    )

    assert result['status'] == 'created'
    files.set_status.assert_awaited_once_with('onedrive-item-1', 'completed', error=None)
    files.update_file_metadata_by_id.assert_any_await(
        'onedrive-item-1', {'pipeline_job_id': None, 'pipeline_submitted_at': None}
    )


@pytest.mark.asyncio
async def test_kb_path_chunked_failure_keeps_pipeline_bookkeeping(monkeypatch):
    """On embed failure the row goes 'error' and bookkeeping stays — only the
    success path clears it."""
    save_mock, files, knowledges = _patch_persistence(monkeypatch)
    save_mock.side_effect = RuntimeError('boom')

    result = await integrations_router._process_chunked_text_document(
        request=MagicMock(),
        knowledge_id='kb-1',
        provider='onedrive',
        doc=_chunked_doc(source_id='item-1'),
        user_id='user-1',
    )

    assert result['status'] == 'error'
    for call in files.update_file_metadata_by_id.await_args_list:
        assert call.args[1] != {'pipeline_job_id': None, 'pipeline_submitted_at': None}


# --- /ingest endpoint: target routing --------------------------------------


def _loader_principal_app(provider_slug: str, monkeypatch):
    """Mount the integrations router with a LoaderPrincipal (warren callback).

    Patches emit_file_status with an AsyncMock so tests stay hermetic (never
    touch the real Socket.IO server) and can assert the honest completion emit.
    """
    from open_webui.utils.service_auth import LoaderPrincipal, get_integration_principal

    monkeypatch.setattr(integrations_router, 'emit_file_status', AsyncMock())

    user = MagicMock(
        id='user-1',
        email='lex@gradient-ds.com',
        role='user',
        name='Lex',
        info={'integration_provider': provider_slug},
    )
    principal = LoaderPrincipal(user=user, provider_slug=provider_slug)

    app = FastAPI()
    app.include_router(integrations_router.router, prefix='/api/v1/integrations')
    app.dependency_overrides[get_integration_principal] = lambda: principal
    app.state.config = MagicMock(INTEGRATION_PROVIDERS={}, BYPASS_EMBEDDING_AND_RETRIEVAL=False)
    return app


@pytest.fixture
def perfile_ingest_app(monkeypatch):
    """owui_upload LoaderPrincipal — chat attachment + direct-KB warren callbacks."""
    return _loader_principal_app('owui_upload', monkeypatch)


def test_ingest_target_file_dispatches_perfile_and_skips_kb(perfile_ingest_app, monkeypatch):
    captured = {}

    async def fake_process(**kwargs):
        captured.update(kwargs)
        return {'source_id': kwargs['doc'].source_id, 'file_id': 'file-abc', 'status': 'created'}

    monkeypatch.setattr(integrations_router, '_process_chunked_text_document', fake_process)

    # KB resolution + limit must NOT be touched on the per-file path.
    create_kb = MagicMock()
    find_kb = MagicMock()
    get_kb = MagicMock()
    get_files = MagicMock()
    monkeypatch.setattr(integrations_router, '_create_kb_for_provider', create_kb)
    monkeypatch.setattr(integrations_router, '_find_kb_by_source_id', find_kb)
    monkeypatch.setattr(integrations_router.Knowledges, 'get_knowledge_by_id', get_kb)
    monkeypatch.setattr(integrations_router.Knowledges, 'get_files_by_id', get_files)
    monkeypatch.setattr(integrations_router.Files, 'update_file_metadata_by_id', AsyncMock())

    body = {
        'collection': {'source_id': 'file-abc', 'name': 'report.pdf', 'target': 'file', 'data_type': 'chunked_text'},
        'documents': [{'source_id': 'file-abc', 'filename': 'report.pdf', 'chunks': ['a', 'b']}],
    }
    res = TestClient(perfile_ingest_app).post(
        '/api/v1/integrations/ingest', files={'data': ('data.json', json.dumps(body).encode(), 'application/json')}
    )

    assert res.status_code == 200, res.text
    out = res.json()
    assert out['target'] == 'file'
    assert out['knowledge_id'] is None
    assert out['created'] == 1
    # Per-file dispatch parameters.
    assert captured['collection_name'] == 'file-file-abc'
    assert captured['add'] is False
    assert captured['knowledge_id'] is None
    # Whole KB machinery skipped.
    create_kb.assert_not_called()
    find_kb.assert_not_called()
    get_kb.assert_not_called()
    get_files.assert_not_called()


def test_ingest_target_file_clears_pipeline_bookkeeping(perfile_ingest_app, monkeypatch):
    async def fake_process(**kwargs):
        return {'source_id': kwargs['doc'].source_id, 'file_id': 'file-abc', 'status': 'completed'}

    update_meta = AsyncMock()
    monkeypatch.setattr(integrations_router, '_process_chunked_text_document', fake_process)
    monkeypatch.setattr(integrations_router.Files, 'update_file_metadata_by_id', update_meta)

    body = {
        'collection': {'source_id': 'file-abc', 'name': 'report.pdf', 'target': 'file', 'data_type': 'chunked_text'},
        'documents': [{'source_id': 'file-abc', 'filename': 'report.pdf', 'chunks': ['a']}],
    }
    res = TestClient(perfile_ingest_app).post(
        '/api/v1/integrations/ingest', files={'data': ('data.json', json.dumps(body).encode(), 'application/json')}
    )

    assert res.status_code == 200, res.text
    update_meta.assert_awaited_once_with('file-abc', {'pipeline_job_id': None, 'pipeline_submitted_at': None})


def test_ingest_target_file_rejects_non_chunked_data_type(perfile_ingest_app):
    body = {
        'collection': {'source_id': 'file-abc', 'name': 'report.pdf', 'target': 'file', 'data_type': 'parsed_text'},
        'documents': [{'source_id': 'file-abc', 'filename': 'report.pdf', 'text': 'x'}],
    }
    res = TestClient(perfile_ingest_app).post(
        '/api/v1/integrations/ingest', files={'data': ('data.json', json.dumps(body).encode(), 'application/json')}
    )

    assert res.status_code == 400
    assert "target='file'" in res.text


def test_ingest_default_target_uses_kb_path(perfile_ingest_app, monkeypatch):
    """target omitted ⇒ 'knowledge' ⇒ KB resolution + KB embed target (regression)."""
    captured = {}

    async def fake_process(**kwargs):
        captured.update(kwargs)
        return {'source_id': kwargs['doc'].source_id, 'file_id': 'doc-1', 'status': 'created'}

    monkeypatch.setattr(integrations_router, '_process_chunked_text_document', fake_process)
    kb = MagicMock(id='kb-1', name='KB', meta={})
    monkeypatch.setattr(integrations_router.Knowledges, 'get_knowledge_by_id', AsyncMock(return_value=kb))
    monkeypatch.setattr(integrations_router.Knowledges, 'get_files_by_id', AsyncMock(return_value=[]))

    body = {
        # target defaults to 'knowledge'
        'collection': {'source_id': 'kb-1', 'name': 'KB', 'data_type': 'chunked_text'},
        'documents': [{'source_id': 'doc-1', 'filename': 'r.pdf', 'chunks': ['a']}],
    }
    res = TestClient(perfile_ingest_app).post(
        '/api/v1/integrations/ingest', files={'data': ('data.json', json.dumps(body).encode(), 'application/json')}
    )

    assert res.status_code == 200, res.text
    out = res.json()
    assert out['knowledge_id'] == 'kb-1'
    assert captured['knowledge_id'] == 'kb-1'  # KB caller passes the real KB id
    assert captured.get('collection_name') is None  # and does NOT override the embed target
    assert captured.get('add', True) is True


# --- /ingest endpoint: honest file:status completion emit ------------------


def test_ingest_target_file_emits_completed(perfile_ingest_app, monkeypatch):
    """Per-file (chat) dispatch emits file:status='completed' with the per-file
    cache collection once the vectors land — the signal that ends the spinner."""

    async def fake_process(**kwargs):
        return {'source_id': kwargs['doc'].source_id, 'file_id': 'file-abc', 'status': 'created'}

    monkeypatch.setattr(integrations_router, '_process_chunked_text_document', fake_process)
    monkeypatch.setattr(integrations_router.Files, 'update_file_metadata_by_id', AsyncMock())

    body = {
        'collection': {'source_id': 'file-abc', 'name': 'report.pdf', 'target': 'file', 'data_type': 'chunked_text'},
        'documents': [{'source_id': 'file-abc', 'filename': 'report.pdf', 'chunks': ['a', 'b']}],
    }
    res = TestClient(perfile_ingest_app).post(
        '/api/v1/integrations/ingest', files={'data': ('data.json', json.dumps(body).encode(), 'application/json')}
    )

    assert res.status_code == 200, res.text
    integrations_router.emit_file_status.assert_awaited_once()
    assert integrations_router.emit_file_status.await_args.kwargs == {
        'user_id': 'user-1',
        'file_id': 'file-abc',
        'status': 'completed',
        'error': None,
        'collection_name': 'file-file-abc',  # per-file cache collection
    }


def test_ingest_target_file_emits_failed_on_error(perfile_ingest_app, monkeypatch):
    """A failed embed on the per-file path emits file:status='failed' so the
    frontend resolves to error (toast + removal), not a stuck spinner."""

    async def fake_process(**kwargs):
        return {'source_id': kwargs['doc'].source_id, 'file_id': 'file-abc', 'status': 'error', 'error': 'boom'}

    monkeypatch.setattr(integrations_router, '_process_chunked_text_document', fake_process)
    monkeypatch.setattr(integrations_router.Files, 'update_file_metadata_by_id', AsyncMock())

    body = {
        'collection': {'source_id': 'file-abc', 'name': 'report.pdf', 'target': 'file', 'data_type': 'chunked_text'},
        'documents': [{'source_id': 'file-abc', 'filename': 'report.pdf', 'chunks': ['a']}],
    }
    res = TestClient(perfile_ingest_app).post(
        '/api/v1/integrations/ingest', files={'data': ('data.json', json.dumps(body).encode(), 'application/json')}
    )

    assert res.status_code == 200, res.text
    kwargs = integrations_router.emit_file_status.await_args.kwargs
    assert kwargs['status'] == 'failed'
    assert kwargs['error'] == 'boom'
    assert kwargs['file_id'] == 'file-abc'


def test_ingest_direct_kb_owui_upload_emits_completed(perfile_ingest_app, monkeypatch):
    """Direct-KB upload routed through warren (provider owui_upload, default
    target) emits file:status='completed' with the KB id as collection_name."""

    async def fake_process(**kwargs):
        return {'source_id': kwargs['doc'].source_id, 'file_id': 'doc-1', 'status': 'created'}

    monkeypatch.setattr(integrations_router, '_process_chunked_text_document', fake_process)
    kb = MagicMock(id='kb-1', name='KB', meta={})
    monkeypatch.setattr(integrations_router.Knowledges, 'get_knowledge_by_id', AsyncMock(return_value=kb))
    monkeypatch.setattr(integrations_router.Knowledges, 'get_files_by_id', AsyncMock(return_value=[]))

    body = {
        'collection': {'source_id': 'kb-1', 'name': 'KB', 'data_type': 'chunked_text'},
        'documents': [{'source_id': 'doc-1', 'filename': 'r.pdf', 'chunks': ['a']}],
    }
    res = TestClient(perfile_ingest_app).post(
        '/api/v1/integrations/ingest', files={'data': ('data.json', json.dumps(body).encode(), 'application/json')}
    )

    assert res.status_code == 200, res.text
    integrations_router.emit_file_status.assert_awaited_once()
    assert integrations_router.emit_file_status.await_args.kwargs == {
        'user_id': 'user-1',
        'file_id': 'doc-1',
        'status': 'completed',
        'error': None,
        'collection_name': 'kb-1',
    }


def test_ingest_cloud_sync_provider_does_not_emit_file_status(monkeypatch):
    """Regression guard: a cloud-sync provider (onedrive) must NOT emit
    file:status — it emits its own honest {provider}:file:added, and a redundant
    file:status would double-count uploadBatch.added in KnowledgeBase.svelte."""
    app = _loader_principal_app('onedrive', monkeypatch)

    async def fake_process(**kwargs):
        return {'source_id': kwargs['doc'].source_id, 'file_id': 'onedrive-doc-1', 'status': 'created'}

    monkeypatch.setattr(integrations_router, '_process_chunked_text_document', fake_process)
    kb = MagicMock(id='kb-1', name='KB', meta={})
    monkeypatch.setattr(integrations_router.Knowledges, 'get_knowledge_by_id', AsyncMock(return_value=kb))
    monkeypatch.setattr(integrations_router.Knowledges, 'get_files_by_id', AsyncMock(return_value=[]))

    body = {
        'collection': {'source_id': 'kb-1', 'name': 'KB', 'data_type': 'chunked_text'},
        'documents': [{'source_id': 'doc-1', 'filename': 'r.pdf', 'chunks': ['a']}],
    }
    res = TestClient(app).post(
        '/api/v1/integrations/ingest', files={'data': ('data.json', json.dumps(body).encode(), 'application/json')}
    )

    assert res.status_code == 200, res.text
    integrations_router.emit_file_status.assert_not_awaited()


# --- /ingest transport: >1MB payload rides the uncapped file part ----------


def test_ingest_accepts_data_file_part_over_1mb(perfile_ingest_app, monkeypatch):
    """Regression for the large-KB sync failure: the JSON payload rides as a
    multipart *file* part, which Starlette does not size-cap (non-file form
    fields are capped at 1MB by formparsers.max_part_size). A >1MB ``data`` part
    must parse and return 200 — previously it 400'd at body-parse before any
    route code ran."""

    async def fake_process(**kwargs):
        return {'source_id': kwargs['doc'].source_id, 'file_id': 'file-abc', 'status': 'created'}

    monkeypatch.setattr(integrations_router, '_process_chunked_text_document', fake_process)
    monkeypatch.setattr(integrations_router.Files, 'update_file_metadata_by_id', AsyncMock())

    # ~1.2MB of chunk text — comfortably over the old 1MB form-field ceiling.
    big_chunk = 'x' * (1200 * 1024)
    body = {
        'collection': {'source_id': 'file-abc', 'name': 'report.pdf', 'target': 'file', 'data_type': 'chunked_text'},
        'documents': [{'source_id': 'file-abc', 'filename': 'report.pdf', 'chunks': [big_chunk]}],
    }
    payload = json.dumps(body).encode()
    assert len(payload) > 1024 * 1024  # the part genuinely exceeds the old cap

    res = TestClient(perfile_ingest_app).post(
        '/api/v1/integrations/ingest',
        files={'data': ('data.json', payload, 'application/json')},
    )

    assert res.status_code == 200, res.text
    assert res.json()['created'] == 1


# --- KB file-limit guard: updates at cap pass, net-new at cap 400 -----------


def _kb_loader_app(monkeypatch, *, max_files_per_kb: int):
    """LoaderPrincipal app for a managed-sync provider (onedrive) with a
    registered ``max_files_per_kb`` so the KB file-limit guard is exercised."""
    from open_webui.utils.service_auth import LoaderPrincipal, get_integration_principal

    monkeypatch.setattr(integrations_router, 'emit_file_status', AsyncMock())

    user = MagicMock(
        id='user-1',
        email='lex@gradient-ds.com',
        role='user',
        name='Lex',
        info={'integration_provider': 'onedrive'},
    )
    principal = LoaderPrincipal(user=user, provider_slug='onedrive')

    app = FastAPI()
    app.include_router(integrations_router.router, prefix='/api/v1/integrations')
    app.dependency_overrides[get_integration_principal] = lambda: principal
    app.state.config = MagicMock(
        INTEGRATION_PROVIDERS={
            'onedrive': {
                'max_files_per_kb': max_files_per_kb,
                'max_documents_per_request': 50,
                'custom_metadata_fields': [],
            }
        },
        BYPASS_EMBEDDING_AND_RETRIEVAL=False,
    )
    return app


def _patch_kb_at_cap(monkeypatch, *, existing_source_ids):
    """Patch KB resolution + a no-op chunked-text processor. Existing file ids
    carry the ``onedrive-`` prefix so net_new is computed against them."""
    kb = MagicMock(id='kb-1', name='KB', meta={})
    existing = [MagicMock(id=f'onedrive-{sid}') for sid in existing_source_ids]
    monkeypatch.setattr(integrations_router.Knowledges, 'get_knowledge_by_id', AsyncMock(return_value=kb))
    monkeypatch.setattr(integrations_router.Knowledges, 'get_files_by_id', AsyncMock(return_value=existing))

    async def fake_process(**kwargs):
        return {
            'source_id': kwargs['doc'].source_id,
            'file_id': f'onedrive-{kwargs["doc"].source_id}',
            'status': 'updated',
        }

    monkeypatch.setattr(integrations_router, '_process_chunked_text_document', fake_process)


def test_ingest_update_at_cap_is_allowed(monkeypatch):
    """KB already at its file cap: a pure update (net_new == 0) must pass."""
    app = _kb_loader_app(monkeypatch, max_files_per_kb=2)
    _patch_kb_at_cap(monkeypatch, existing_source_ids=['a', 'b'])

    body = {
        'collection': {'source_id': 'kb-1', 'name': 'KB', 'data_type': 'chunked_text'},
        'documents': [{'source_id': 'a', 'filename': 'a.pdf', 'chunks': ['x']}],
    }
    res = TestClient(app).post(
        '/api/v1/integrations/ingest',
        files={'data': ('data.json', json.dumps(body).encode(), 'application/json')},
    )

    assert res.status_code == 200, res.text
    assert res.json()['updated'] == 1


def test_ingest_net_new_at_cap_is_rejected(monkeypatch):
    """KB already at its file cap: adding a net-new file (net_new > 0) 400s."""
    app = _kb_loader_app(monkeypatch, max_files_per_kb=2)
    _patch_kb_at_cap(monkeypatch, existing_source_ids=['a', 'b'])

    body = {
        'collection': {'source_id': 'kb-1', 'name': 'KB', 'data_type': 'chunked_text'},
        'documents': [{'source_id': 'c', 'filename': 'c.pdf', 'chunks': ['x']}],
    }
    res = TestClient(app).post(
        '/api/v1/integrations/ingest',
        files={'data': ('data.json', json.dumps(body).encode(), 'application/json')},
    )

    assert res.status_code == 400
    assert 'file limit' in res.text
