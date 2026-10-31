from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import Request


@pytest.mark.asyncio
async def test_platform_knowledge_metadata_skips_local_embedding(monkeypatch):
    from open_webui import config
    from open_webui.routers import knowledge

    monkeypatch.setattr(config, 'SOEV_API_URL', 'https://platform.invalid')
    embedding = AsyncMock(side_effect=AssertionError('No local embeddings on platform'))
    vectors = AsyncMock()
    monkeypatch.setattr(knowledge, 'ASYNC_VECTOR_DB_CLIENT', vectors)
    request = Request({'type': 'http', 'app': SimpleNamespace(state=SimpleNamespace(EMBEDDING_FUNCTION=embedding))})
    assert await knowledge.embed_knowledge_base_metadata(request, 'kb', 'Name', 'Description') is False
    embedding.assert_not_awaited()
    vectors.upsert.assert_not_awaited()


@pytest.mark.asyncio
async def test_thread_runtime_never_reads_pending_local_chats(monkeypatch):
    from open_webui.models.chats import Chats
    from open_webui.services.deletion import cleanup_worker
    from open_webui.soev import threads

    monkeypatch.setattr(threads, 'enabled', lambda: True)
    pending = AsyncMock(side_effect=AssertionError('Local chat content is forbidden'))
    monkeypatch.setattr(Chats, 'get_pending_deletions', pending)
    await cleanup_worker._process_pending_deletions()
    pending.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_knowledge_metadata_still_embeds(monkeypatch):
    from open_webui import config
    from open_webui.routers import knowledge

    monkeypatch.setattr(config, 'SOEV_API_URL', '')
    embedding = AsyncMock(return_value=[0.25])
    vectors = AsyncMock()
    monkeypatch.setattr(knowledge, 'ASYNC_VECTOR_DB_CLIENT', vectors)
    request = Request({'type': 'http', 'app': SimpleNamespace(state=SimpleNamespace(EMBEDDING_FUNCTION=embedding))})
    assert await knowledge.embed_knowledge_base_metadata(request, 'kb', 'Name', '') is True
    embedding.assert_awaited_once()
    vectors.upsert.assert_awaited_once()


@pytest.mark.asyncio
async def test_legacy_pending_chat_cleanup_still_runs(monkeypatch):
    from open_webui.models.chats import Chats
    from open_webui.services.deletion import cleanup_worker
    from open_webui.soev import threads

    monkeypatch.setattr(threads, 'enabled', lambda: False)
    pending = AsyncMock(return_value=[])
    monkeypatch.setattr(Chats, 'get_pending_deletions', pending)
    await cleanup_worker._process_pending_deletions()
    pending.assert_awaited_once_with(limit=100)
