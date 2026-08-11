"""Per-chunk citation geometry on /api/v1/integrations/ingest (chunked_text).

warren's owui_ingest worker now sends chunks as
``{"content": str, "metadata": {"page": int, "bboxes": [...]}}`` objects;
old senders still ship bare strings. The union must accept both, and
``bboxes`` must reach the vector store as a JSON-parseable string (never
the Python ``str(list)`` repr that ``process_metadata`` would otherwise
produce).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from open_webui.retrieval.vector.utils import process_metadata
from open_webui.routers import integrations as integrations_router
from open_webui.routers.integrations import ChunkedTextDocument, IngestChunk, _chunk_meta

_BBOXES = [
    {'page': 0, 'x0': 10.0, 'y0': 20.0, 'x1': 300.0, 'y1': 40.0},
    {'page': 1, 'x0': 10.0, 'y0': 700.0, 'x1': 300.0, 'y1': 720.0},
]


# --- chunk union compat -----------------------------------------------------


def test_chunked_document_accepts_bare_strings():
    doc = ChunkedTextDocument(source_id='f1', filename='a.pdf', chunks=['one', 'two'])
    assert [c.content for c in doc.chunks] == ['one', 'two']
    assert all(isinstance(c, IngestChunk) and c.metadata == {} for c in doc.chunks)


def test_chunked_document_accepts_chunk_objects():
    doc = ChunkedTextDocument(
        source_id='f1',
        filename='a.pdf',
        chunks=[{'content': 'one', 'metadata': {'page': 0, 'bboxes': _BBOXES}}],
    )
    (chunk,) = doc.chunks
    assert chunk.content == 'one'
    assert chunk.metadata == {'page': 0, 'bboxes': _BBOXES}


def test_chunked_document_accepts_mixed_chunks():
    doc = ChunkedTextDocument(
        source_id='f1',
        filename='a.pdf',
        chunks=['bare', {'content': 'obj', 'metadata': {'page': 2}}],
    )
    assert [(c.content, c.metadata) for c in doc.chunks] == [('bare', {}), ('obj', {'page': 2})]


# --- _chunk_meta ------------------------------------------------------------


def test_chunk_meta_passes_page_and_serializes_bboxes():
    meta = _chunk_meta(IngestChunk(content='c', metadata={'page': 3, 'bboxes': _BBOXES}))
    assert meta['page'] == 3
    assert isinstance(meta['bboxes'], str)
    assert json.loads(meta['bboxes']) == _BBOXES


def test_chunk_meta_tolerates_pre_serialized_bboxes():
    meta = _chunk_meta(IngestChunk(content='c', metadata={'bboxes': json.dumps(_BBOXES)}))
    assert json.loads(meta['bboxes']) == _BBOXES


def test_chunk_meta_empty_for_metadata_less_chunk():
    assert _chunk_meta(IngestChunk(content='c')) == {}


def test_chunk_meta_drops_non_geometry_keys_and_bad_page():
    meta = _chunk_meta(IngestChunk(content='c', metadata={'page': True, 'pages': [1, 2], 'foo': 'bar'}))
    assert meta == {}


def test_chunk_meta_bboxes_survive_process_metadata():
    """The JSON string reaches the vector store unmangled — process_metadata's
    str() coercion only fires on raw lists/dicts, never on the string."""
    meta = _chunk_meta(IngestChunk(content='c', metadata={'page': 0, 'bboxes': _BBOXES}))
    processed = process_metadata({'file_id': 'f1', **meta})
    assert json.loads(processed['bboxes']) == _BBOXES
    assert processed['page'] == 0


# --- _process_chunked_text_document wiring ---------------------------------


def _patch_persistence(monkeypatch):
    save_mock = MagicMock(return_value=True)
    monkeypatch.setattr(integrations_router, 'save_docs_to_vector_db', save_mock)

    files = MagicMock()
    files.get_file_by_id = AsyncMock(return_value=None)
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

    return save_mock


@pytest.mark.asyncio
async def test_process_chunked_builds_per_chunk_metadata(monkeypatch):
    save_mock = _patch_persistence(monkeypatch)

    doc = ChunkedTextDocument(
        source_id='f1',
        filename='a.pdf',
        chunks=[
            {'content': 'geo chunk', 'metadata': {'page': 1, 'bboxes': _BBOXES}},
            'plain chunk',
        ],
    )
    await integrations_router._process_chunked_text_document(
        request=MagicMock(),
        knowledge_id='kb-1',
        provider='owui_upload',
        doc=doc,
        user_id='user-1',
    )

    docs = save_mock.call_args.kwargs['docs']
    assert [d.page_content for d in docs] == ['geo chunk', 'plain chunk']
    assert docs[0].metadata['page'] == 1
    assert json.loads(docs[0].metadata['bboxes']) == _BBOXES
    # Metadata-less chunk carries base metadata only — no geometry keys.
    assert 'page' not in docs[1].metadata
    assert 'bboxes' not in docs[1].metadata
    # Shared base metadata still present on both.
    assert all(d.metadata['file_id'] == 'f1' for d in docs)
