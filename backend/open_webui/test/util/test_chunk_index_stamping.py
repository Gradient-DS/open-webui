"""`save_docs_to_vector_db` stamps each chunk with its list position.

List position in the final (post-split) doc list is the authoritative document
order; `start_index` is not, because the markdown header splitter resets it per
section. soev-agents reconstructs full documents by sorting chunks on
`chunk_index`, so the key must reach the inserted items on both the split and
the non-split path.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from langchain_core.documents import Document

import open_webui.routers.retrieval as retrieval_router


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        ENABLE_MARKDOWN_HEADER_TEXT_SPLITTER=True,
        CHUNK_MIN_SIZE_TARGET=0,
        TEXT_SPLITTER='character',
        CHUNK_SIZE=40,
        CHUNK_OVERLAP=0,
        RAG_EMBEDDING_ENGINE='',
        RAG_EMBEDDING_MODEL='test-model',
        RAG_OPENAI_API_BASE_URL='',
        RAG_OPENAI_API_KEY='',
        RAG_OLLAMA_BASE_URL='',
        RAG_OLLAMA_API_KEY='',
        RAG_AZURE_OPENAI_BASE_URL='',
        RAG_AZURE_OPENAI_API_KEY='',
        RAG_AZURE_OPENAI_API_VERSION=None,
        RAG_EMBEDDING_BATCH_SIZE=1,
        ENABLE_ASYNC_EMBEDDING=False,
        RAG_EMBEDDING_CONCURRENT_REQUESTS=1,
    )


@pytest.fixture
def inserted_items(monkeypatch):
    """Run save_docs_to_vector_db with the embed/insert seams faked.

    Returns a one-element list that the fake vector client fills with the items
    handed to `insert`.
    """
    captured: list = []

    vector_db = MagicMock()
    vector_db.has_collection.return_value = False
    vector_db.insert.side_effect = lambda collection_name, items: captured.extend(items)
    monkeypatch.setattr(retrieval_router, 'VECTOR_DB_CLIENT', vector_db)

    monkeypatch.setattr(retrieval_router, 'get_embedding_function', lambda *a, **k: MagicMock())
    monkeypatch.setattr(
        retrieval_router.asyncio,
        'run_coroutine_threadsafe',
        lambda coro, loop: SimpleNamespace(result=lambda timeout=None: [[0.0]] * 100),
    )
    return captured


def test_chunk_index_is_list_position_after_splitting(monkeypatch, inserted_items) -> None:
    # Two markdown sections, each long enough for the character splitter to cut
    # further — so the section boundary is not the only source of ordering.
    text = '# A\n' + ('alpha ' * 40) + '\n# B\n' + ('beta ' * 40)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ef=None, main_loop=None)))

    result = retrieval_router.save_docs_to_vector_db(
        request,
        [Document(page_content=text, metadata={'file_id': 'file-1'})],
        'file-file-1',
        config=_config(),
    )

    assert result is True
    assert len(inserted_items) > 2, 'expected the document to split into several chunks'
    assert [item['metadata']['chunk_index'] for item in inserted_items] == list(range(len(inserted_items)))


def test_chunk_index_stamped_on_the_non_split_path(monkeypatch, inserted_items) -> None:
    docs = [Document(page_content=f'chunk {i}', metadata={'file_id': 'file-1'}) for i in range(3)]
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ef=None, main_loop=None)))

    retrieval_router.save_docs_to_vector_db(
        request,
        docs,
        'file-file-1',
        split=False,
        config=_config(),
    )

    assert [item['metadata']['chunk_index'] for item in inserted_items] == [0, 1, 2]
