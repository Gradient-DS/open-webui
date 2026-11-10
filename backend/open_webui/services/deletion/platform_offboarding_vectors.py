"""Retire derived vectors or move memory ownership without generating embeddings."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from open_webui.retrieval.vector.dbs.pgvector import PgvectorClient


async def remove_file_vectors(file: str) -> None:
    await _mutate('file-' + file, None)


async def memory_vectors(source: str, target: str | None) -> None:
    await _mutate('user-memory-' + source, 'user-memory-' + target if target is not None else None)


async def _mutate(source: str, target: str | None) -> None:
    from open_webui.retrieval.vector.async_client import ASYNC_VECTOR_DB_CLIENT
    from open_webui.retrieval.vector.dbs.pgvector import PgvectorClient

    client = ASYNC_VECTOR_DB_CLIENT.sync
    if not isinstance(client, PgvectorClient):
        message = 'Offboarding vector disposition requires PostgreSQL'
        raise ValueError(message)
    await asyncio.to_thread(_move, client, source, target)


def _move(client: PgvectorClient, source: str, target: str | None) -> None:
    from open_webui.retrieval.vector.dbs.pgvector import DocumentChunk

    with client.session() as session:
        rows = session.query(DocumentChunk).filter(DocumentChunk.collection_name == source)
        if target is None:
            rows.delete(synchronize_session=False)
        else:
            rows.update({'collection_name': target}, synchronize_session=False)
        session.commit()
