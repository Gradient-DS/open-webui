"""Soev ingest routing and the native fallback for single files and batches."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from fastapi import HTTPException
from langchain_core.documents import Document
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
    monkeypatch.setattr(retrieval_router.soev_config, 'SOEV_API_URL', api_url)
    submit = AsyncMock(return_value='job-1')
    ensure = AsyncMock(return_value='owui-attachments-user-1')
    client = MagicMock()
    monkeypatch.setattr(retrieval_router.ingest, 'submit', submit)
    monkeypatch.setattr(retrieval_router.ingest, 'ensure_attachments_collection', ensure)
    monkeypatch.setattr(retrieval_router.identity, 'build_client', MagicMock(return_value=client))

    loader = MagicMock()
    loader.aload = AsyncMock(return_value=[Document(page_content='hello world', metadata={})])
    monkeypatch.setattr(retrieval_router, 'build_loader_from_config', MagicMock(return_value=loader))
    monkeypatch.setattr(retrieval_router, 'get_loader_config', AsyncMock(return_value={}))
    monkeypatch.setattr(retrieval_router.Storage, 'get_file', lambda path: '/tmp/x')  # nosec B108
    save = MagicMock(return_value=True)
    monkeypatch.setattr(retrieval_router, 'save_docs_to_vector_db', save)
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
        DISTRIBUTED_DOC_PIPELINE_ENABLED=True,
        DISTRIBUTED_DOC_PIPELINE_CHAT_ENABLED=True,
    )
    monkeypatch.setattr(retrieval_router, 'get_rag_config_state', AsyncMock(return_value=config))
    return SimpleNamespace(
        file=file,
        request=MagicMock(),
        user=SimpleNamespace(id='user-1', role='user'),
        db=SimpleNamespace(commit=AsyncMock()),
        session=session,
        submit=submit,
        ensure=ensure,
        client=client,
        loader=loader,
        save=save,
        vector=vector_client,
    )


async def _invoke(env, *, collection_name=None, content=None):
    return await retrieval_router.process_file(
        env.request,
        ProcessFileForm(file_id=env.file.id, collection_name=collection_name, content=content),
        user=env.user,
        db=env.db,
    )


def _assert_native_untouched(env):
    retrieval_router.build_loader_from_config.assert_not_called()
    env.loader.aload.assert_not_awaited()
    env.save.assert_not_called()
    assert not env.vector.mock_calls
    retrieval_router.Files.update_file_data_by_id.assert_not_awaited()


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
    env.submit.assert_awaited_once_with(env.file, collection_key='kb-1', user_id=env.user.id, text=None)
    env.ensure.assert_not_awaited()
    _assert_native_untouched(env)


@pytest.mark.asyncio
async def test_a_chat_file_submits_into_the_users_attachments_collection(monkeypatch):
    env = _setup(monkeypatch)
    result = await _invoke(env)
    assert result['collection_name'] is None
    assert result['job_id'] == 'job-1'
    env.ensure.assert_awaited_once_with(env.user.id, env.client)
    env.submit.assert_awaited_once_with(
        env.file, collection_key='owui-attachments-user-1', user_id=env.user.id, text=None
    )
    _assert_native_untouched(env)


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
    )
    _assert_native_untouched(env)


@pytest.mark.asyncio
@pytest.mark.parametrize('bypass', [False, True])
async def test_without_soev_api_the_upstream_body_runs(monkeypatch, bypass):
    env = _setup(monkeypatch, api_url='', bypass=bypass)
    result = await _invoke(env)
    assert result['status'] is True
    assert result['content'] == 'hello world'
    env.submit.assert_not_awaited()
    env.ensure.assert_not_awaited()
    env.loader.aload.assert_awaited_once()
    retrieval_router.Files.update_file_data_by_id.assert_awaited_once_with(
        env.file.id, {'content': 'hello world'}, db=env.db
    )
    if bypass:
        assert result['collection_name'] is None
        env.save.assert_not_called()
    else:
        env.save.assert_called_once()
    env.save.reset_mock()
    result = await retrieval_router.process_files_batch(
        env.request, BatchProcessFilesForm(files=[env.file], collection_name='kb-1'), user=env.user, db=env.db
    )
    assert [(item.file_id, item.status) for item in result.results] == [('file-1', 'completed')]
    assert not result.errors
    env.save.assert_called_once()
    retrieval_router.Files.update_file_by_id.assert_awaited_once()
    env.submit.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_refused_submit_marks_the_file_failed_with_the_code(monkeypatch):
    env = _setup(monkeypatch)
    env.submit.side_effect = SoevApiError(403, 'scope_insufficient', 'Writer membership required')
    detail = 'scope_insufficient: Writer membership required'
    with pytest.raises(HTTPException) as error:
        await _invoke(env, collection_name='kb-1')
    assert error.value.status_code == 400
    assert error.value.detail == detail
    retrieval_router.Files.set_status.assert_awaited_once_with(env.file.id, 'failed', error=detail, db=env.session)
    retrieval_router.Files.update_file_hash_by_id.assert_awaited_once_with(env.file.id, None, db=env.session)
    retrieval_router.publish_event.assert_awaited_once_with(
        env.request,
        retrieval_router.EVENTS.RETRIEVAL_CONTENT_PROCESS_FAILED,
        actor=env.user,
        subject_id=env.file.id,
        subject_type='file',
        data={'collection_name': 'kb-1', 'filename': env.file.filename, 'message': f'{env.file.filename}: {detail}'},
    )
    _assert_native_untouched(env)


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
    _assert_native_untouched(env)


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
        detail = 'scope_insufficient: Writer membership required' if failure == 'refused' else 'file is being processed'
        assert [item.model_dump() for item in result.errors] == [
            {'file_id': 'file-1', 'status': 'failed', 'error': detail}
        ]
    else:
        assert not result.errors
    env.ensure.assert_not_awaited()
    retrieval_router.Files.update_file_by_id.assert_not_awaited()
    _assert_native_untouched(env)
