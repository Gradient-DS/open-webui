"""Tests for the process_file distributed-pipeline routing gate.

Track 1 (chat attachments through warren): the gate must send a chat attachment
(collection_name is None) to route_chat_file_to_pipeline behind
DISTRIBUTED_DOC_PIPELINE_CHAT_ENABLED, keep KB uploads on route_file_to_pipeline,
and fall through to the native path when a flag is off, the format is
unsupported, or inline content is supplied (STT / manual content update).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.documents import Document
from open_webui.routers import retrieval as retrieval_router
from open_webui.routers.retrieval import ProcessFileForm


@asynccontextmanager
async def _fake_db_cm():
    yield MagicMock()


async def _invoke(
    monkeypatch,
    *,
    collection_name,
    content,
    chat_flag,
    kb_flag,
    filename='report.pdf',
    file_path='s3://bucket/report.pdf',
    bypass=False,
):
    """Drive process_file with the routing seams mocked. Returns
    (result, route_file_mock, route_chat_mock, file, request, user)."""
    file = MagicMock(
        id='file-1',
        filename=filename,
        path=file_path,
        meta={'content_type': 'application/pdf'},
        data={'content': 'hello world'},
        user_id='user-1',
    )

    monkeypatch.setattr(retrieval_router.Files, 'get_file_by_id', AsyncMock(return_value=file))
    monkeypatch.setattr(retrieval_router.Files, 'get_file_by_id_and_user_id', AsyncMock(return_value=file))
    monkeypatch.setattr(retrieval_router.Files, 'update_file_data_by_id', AsyncMock())
    monkeypatch.setattr(retrieval_router.Files, 'update_file_metadata_by_id', AsyncMock())
    monkeypatch.setattr(retrieval_router.Files, 'update_file_hash_by_id', AsyncMock())
    monkeypatch.setattr(retrieval_router.Files, 'set_status', AsyncMock())
    monkeypatch.setattr(retrieval_router, '_validate_collection_access', AsyncMock())

    route_file = AsyncMock(return_value={'routed': 'kb'})
    route_chat = AsyncMock(return_value={'routed': 'chat'})
    monkeypatch.setattr(retrieval_router, 'route_file_to_pipeline', route_file)
    monkeypatch.setattr(retrieval_router, 'route_chat_file_to_pipeline', route_chat)

    # Native seams — kept no-op so a fallthrough completes without real I/O.
    loader = MagicMock()
    loader.aload = AsyncMock(return_value=[Document(page_content='hello world', metadata={})])
    monkeypatch.setattr(retrieval_router, 'build_loader_from_config', lambda request: loader)
    monkeypatch.setattr(retrieval_router.Storage, 'get_file', lambda p: '/tmp/x')
    monkeypatch.setattr(retrieval_router, 'save_docs_to_vector_db', MagicMock(return_value=True))
    monkeypatch.setattr(retrieval_router, 'get_async_db', _fake_db_cm)
    vector_client = MagicMock()
    vector_client.delete_collection = AsyncMock()
    monkeypatch.setattr(retrieval_router, 'ASYNC_VECTOR_DB_CLIENT', vector_client)

    request = MagicMock()
    request.app.state.config = MagicMock(
        DISTRIBUTED_DOC_PIPELINE_ENABLED=kb_flag,
        DISTRIBUTED_DOC_PIPELINE_CHAT_ENABLED=chat_flag,
        BYPASS_EMBEDDING_AND_RETRIEVAL=bypass,
    )
    user = MagicMock(id='user-1', role='user')
    db = MagicMock()
    db.commit = AsyncMock()

    form = ProcessFileForm(file_id='file-1', content=content, collection_name=collection_name)
    result = await retrieval_router.process_file(request, form, user=user, db=db)
    return result, route_file, route_chat, file, request, user


@pytest.mark.asyncio
async def test_chat_file_routes_to_chat_pipeline(monkeypatch):
    result, route_file, route_chat, file, request, user = await _invoke(
        monkeypatch, collection_name=None, content=None, chat_flag=True, kb_flag=False
    )
    assert result == {'routed': 'chat'}
    route_chat.assert_awaited_once_with(request, file, user)
    route_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_kb_file_routes_to_kb_pipeline(monkeypatch):
    result, route_file, route_chat, file, request, user = await _invoke(
        monkeypatch, collection_name='kb-1', content=None, chat_flag=False, kb_flag=True
    )
    assert result == {'routed': 'kb'}
    route_file.assert_awaited_once_with(request, file, 'kb-1', user)
    route_chat.assert_not_awaited()


@pytest.mark.asyncio
async def test_chat_flag_off_falls_through_to_native(monkeypatch):
    result, route_file, route_chat, *_ = await _invoke(
        monkeypatch, collection_name=None, content=None, chat_flag=False, kb_flag=True, bypass=True
    )
    route_chat.assert_not_awaited()
    route_file.assert_not_awaited()  # KB flag on but this is a chat file → not the KB router either
    assert result['status'] is True
    assert result['collection_name'] is None


@pytest.mark.asyncio
async def test_unsupported_format_falls_through_to_native(monkeypatch):
    result, route_file, route_chat, *_ = await _invoke(
        monkeypatch,
        collection_name=None,
        content=None,
        chat_flag=True,
        kb_flag=False,
        filename='photo.png',
        bypass=True,
    )
    route_chat.assert_not_awaited()
    route_file.assert_not_awaited()
    assert result['status'] is True


@pytest.mark.asyncio
async def test_missing_path_falls_through_to_native(monkeypatch):
    """No storage path to presign → native even with the flag on and a good format."""
    result, route_file, route_chat, *_ = await _invoke(
        monkeypatch, collection_name=None, content=None, chat_flag=True, kb_flag=False, file_path='', bypass=True
    )
    route_chat.assert_not_awaited()
    assert result['status'] is True


@pytest.mark.asyncio
async def test_inline_content_skips_routing(monkeypatch):
    """Inline content (STT transcript / manual update) never routes to warren."""
    result, route_file, route_chat, *_ = await _invoke(
        monkeypatch, collection_name=None, content='transcribed text', chat_flag=True, kb_flag=True, bypass=True
    )
    route_chat.assert_not_awaited()
    route_file.assert_not_awaited()
    assert result['status'] is True
