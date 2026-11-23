"""Soev ingest routing and the native fallback for single files and batches."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from fastapi import HTTPException
from open_webui import config as soev_config
from open_webui.models.files import FileModel
from open_webui.routers import retrieval as retrieval_router
from open_webui.routers.retrieval import BatchProcessFilesForm, ProcessFileForm
from open_webui.soev.client import SoevApiError


def _setup(monkeypatch, *, api_url='https://soev.invalid', bypass=False):
    file = FileModel(
        id='file-1',
        filename='report.pdf',
        path='s3://bucket/report.pdf',
        meta={'content_type': 'application/pdf'},
        data={'content': ''},
        user_id='user-1',
        created_at=0,
        updated_at=0,
    )
    monkeypatch.setattr(retrieval_router.Files, 'get_file_by_id', AsyncMock(return_value=file))
    monkeypatch.setattr(retrieval_router.Files, 'get_file_by_id_and_user_id', AsyncMock(return_value=file))
    for method in (
        'update_file_data_by_id',
        'update_file_metadata_by_id',
        'update_file_hash_by_id',
        'update_file_by_id',
        'set_status',
    ):
        monkeypatch.setattr(retrieval_router.Files, method, AsyncMock())
    monkeypatch.setattr(retrieval_router, '_validate_collection_access', AsyncMock())
    monkeypatch.setattr(retrieval_router, 'publish_event', AsyncMock())
    monkeypatch.setattr(soev_config, 'SOEV_API_URL', api_url)
    submit = AsyncMock(return_value='job-1')
    ensure = AsyncMock(return_value='owui-attachments-user-1')
    client = MagicMock()
    monkeypatch.setattr(retrieval_router.ingest, 'submit', submit)
    monkeypatch.setattr(retrieval_router.ingest, 'ensure_attachments_collection', ensure)
    monkeypatch.setattr(retrieval_router.identity, 'build_client', MagicMock(return_value=client))

    session = MagicMock()

    @asynccontextmanager
    async def fresh_session():
        yield session

    monkeypatch.setattr(retrieval_router, 'get_async_db', fresh_session)
    vector_client = MagicMock()
    vector_client.delete_collection = AsyncMock()
    monkeypatch.setattr(retrieval_router, 'ASYNC_VECTOR_DB_CLIENT', vector_client)
    config = SimpleNamespace(
        BYPASS_EMBEDDING_AND_RETRIEVAL=bypass,
    )
    monkeypatch.setattr(retrieval_router, 'get_rag_config_state', AsyncMock(return_value=config))
    return SimpleNamespace(
        file=file,
        request=SimpleNamespace(headers={'accept-language': 'en-US'}),
        user=SimpleNamespace(id='user-1', role='user'),
        db=SimpleNamespace(commit=AsyncMock()),
        session=session,
        submit=submit,
        ensure=ensure,
        client=client,
        vector=vector_client,
    )


async def _invoke(env, *, collection_name=None, content=None):
    return await retrieval_router.process_file(
        env.request,
        ProcessFileForm(file_id=env.file.id, collection_name=collection_name, content=content),
        user=env.user,
        db=env.db,
    )


@pytest.mark.asyncio
async def test_a_kb_file_submits_into_the_knowledge_base(monkeypatch):
    env = _setup(monkeypatch)
    result = await _invoke(env, collection_name='kb-1')
    assert result == {
        'status': True,
        'collection_name': 'kb-1',
        'filename': 'report.pdf',
        'content': '',
        'job_id': 'job-1',
    }
    retrieval_router._validate_collection_access.assert_awaited_once_with(['kb-1'], env.user, access_type='write')
    env.submit.assert_awaited_once_with(env.file, collection_key='kb-1', user_id=env.user.id, text=None, source=None)
    env.ensure.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_chat_file_submits_into_the_users_attachments_collection(monkeypatch):
    env = _setup(monkeypatch)
    result = await _invoke(env)
    assert result['collection_name'] is None
    assert result['job_id'] == 'job-1'
    env.ensure.assert_awaited_once_with(env.user.id, env.client)
    env.submit.assert_awaited_once_with(
        env.file, collection_key='owui-attachments-user-1', user_id=env.user.id, text=None, source=None
    )


@pytest.mark.asyncio
@pytest.mark.parametrize('content', ['transcribed text', ''])
@pytest.mark.parametrize('collection_name', [None, 'kb-1'])
async def test_inline_content_uses_the_inline_arm(monkeypatch, content, collection_name):
    env = _setup(monkeypatch)
    result = await _invoke(env, collection_name=collection_name, content=content)
    assert result['job_id'] == 'job-1'
    env.submit.assert_awaited_once_with(
        env.file,
        collection_key=collection_name or 'owui-attachments-user-1',
        user_id=env.user.id,
        text=content,
        source=None,
    )


@pytest.mark.asyncio
async def test_a_refused_submit_marks_the_file_failed_with_the_code(monkeypatch):
    env = _setup(monkeypatch)
    env.submit.side_effect = SoevApiError(403, 'scope_insufficient', 'Writer membership required')
    detail = 'The platform could not complete this request.'
    with pytest.raises(HTTPException) as error:
        await _invoke(env, collection_name='kb-1')
    assert error.value.status_code == 400
    assert error.value.detail == detail
    retrieval_router.Files.set_status.assert_awaited_once_with(env.file.id, 'failed', error=detail, db=env.session)
    retrieval_router.Files.update_file_hash_by_id.assert_not_awaited()
    retrieval_router.publish_event.assert_awaited_once_with(
        env.request,
        retrieval_router.EVENTS.RETRIEVAL_CONTENT_PROCESS_FAILED,
        actor=env.user,
        subject_id=env.file.id,
        subject_type='file',
        data={'collection_name': 'kb-1', 'filename': env.file.filename, 'message': f'{env.file.filename}: {detail}'},
    )


@pytest.mark.asyncio
async def test_a_busy_file_is_409(monkeypatch):
    env = _setup(monkeypatch)
    env.submit.side_effect = retrieval_router.ingest.IngestBusy(env.file.id)
    with pytest.raises(HTTPException) as error:
        await _invoke(env)
    assert error.value.status_code == 409
    assert error.value.detail == 'file is being processed'
    retrieval_router.Files.set_status.assert_not_awaited()
    retrieval_router.Files.update_file_hash_by_id.assert_not_awaited()
    retrieval_router.publish_event.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [None, 'refused', 'busy'])
async def test_process_files_batch_submits_each_file_as_processing(monkeypatch, failure):
    env = _setup(monkeypatch)
    second = env.file.model_copy(update={'id': 'file-2'})
    retrieval_router.Files.get_file_by_id.side_effect = [env.file, second]
    first_result = {
        None: 'job-1',
        'refused': SoevApiError(403, 'scope_insufficient', 'Writer membership required'),
        'busy': retrieval_router.ingest.IngestBusy(env.file.id),
    }[failure]
    env.submit.side_effect = [first_result, 'job-2']
    result = await retrieval_router.process_files_batch(
        env.request,
        BatchProcessFilesForm(files=[env.file, second], collection_name='kb-1'),
        user=env.user,
        db=env.db,
    )
    assert env.submit.await_args_list == [
        call(env.file, collection_key='kb-1', user_id=env.user.id),
        call(second, collection_key='kb-1', user_id=env.user.id),
    ]
    expected_ids = ['file-2'] if failure else ['file-1', 'file-2']
    assert [(item.file_id, item.status) for item in result.results] == [(id, 'processing') for id in expected_ids]
    if failure:
        detail = 'The platform could not complete this request.' if failure == 'refused' else 'file is being processed'
        assert [item.model_dump() for item in result.errors] == [
            {'file_id': 'file-1', 'status': 'failed', 'error': detail}
        ]
    else:
        assert not result.errors
    env.ensure.assert_not_awaited()
    retrieval_router.Files.update_file_by_id.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('role', ['user', 'admin'])
async def test_batch_foreign_and_missing_are_indistinguishable(monkeypatch, role):
    env = _setup(monkeypatch)
    env.user.role = role
    foreign = env.file.model_copy(update={'user_id': 'other'})
    retrieval_router.Files.get_file_by_id.side_effect = [foreign, None]
    result = await retrieval_router.process_files_batch(
        env.request, BatchProcessFilesForm(files=[env.file, env.file], collection_name='kb-1'), user=env.user, db=env.db
    )
    assert len(result.errors) == 2
    assert result.errors[0].model_dump() == result.errors[1].model_dump()
    assert result.errors[0].error == 'File not found'
    env.submit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'language,expected',
    [
        ('en-US', 'The platform could not complete this request.'),
        ('nl-NL', 'Het platform kon dit verzoek niet uitvoeren.'),
    ],
)
async def test_batch_never_returns_private_upstream_text(monkeypatch, language, expected):
    env = _setup(monkeypatch)
    env.request.headers['accept-language'] = language
    env.submit.side_effect = SoevApiError(503, 'upstream_error', 'fictional-private-document-name')
    result = await retrieval_router.process_files_batch(
        env.request, BatchProcessFilesForm(files=[env.file], collection_name='kb-1'), user=env.user, db=env.db
    )
    assert result.errors[0].error == expected
    assert 'fictional-private' not in result.model_dump_json()


@pytest.mark.asyncio
async def test_process_failure_updates_inside_session_and_preserves_original_fingerprint(monkeypatch):
    env = _setup(monkeypatch)
    env.file.meta.update(file_hash='a' * 64, size=12, soev_read_collection='kb-1')
    active = []

    @asynccontextmanager
    async def session():
        active.append(True)
        try:
            yield env.session
        finally:
            active.clear()

    async def failed(*args, **kwargs):
        assert active and kwargs['db'] is env.session

    monkeypatch.setattr(retrieval_router, 'get_async_db', session)
    monkeypatch.setattr(retrieval_router.Files, 'set_status', AsyncMock(side_effect=failed))
    env.submit.side_effect = SoevApiError(503, 'upstream_error', 'fictional-private-detail')
    with pytest.raises(HTTPException):
        await _invoke(env, content='replacement', collection_name='kb-1')
    assert active == []
    retrieval_router.Files.update_file_hash_by_id.assert_not_awaited()
    assert env.file.meta['file_hash'] == 'a' * 64
