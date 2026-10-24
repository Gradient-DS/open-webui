"""Re-embed every memory row into Open WebUI's vector store with the app's embedding function."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from types import SimpleNamespace

import sqlalchemy as sa


async def build_embedding_function() -> Callable[..., Awaitable]:
    from open_webui.soev.primitives import embed_memory

    async def embed(text, prefix=None, user=None):
        return await embed_memory(text, user)

    return embed


@dataclass
class MemoryState:
    # user id -> (memory rows, vectors stored)
    per_user: dict[str, tuple[int, int]] = field(default_factory=dict)
    reembedded: list[str] = field(default_factory=list)

    @property
    def missing(self) -> int:
        return sum(max(rows - vectors, 0) for rows, vectors in self.per_user.values())


async def _vector_ids(client, user_id: str) -> set[str]:
    collection = f'user-memory-{user_id}'
    if not await client.has_collection(collection):
        return set()
    result = await client.get(collection)
    return set(result.ids[0]) if result and result.ids else set()


async def reembed(*, dry_run: bool = False, db=None, embedding_function=None) -> MemoryState:
    """Reindex each user whose stored vectors do not match their memory rows; others are untouched."""
    from open_webui.internal.db import get_async_db_context
    from open_webui.models.memories import Memory, MemoryModel
    from open_webui.models.users import Users

    async with get_async_db_context(db) as session:
        result = await session.execute(sa.select(Memory).order_by(Memory.user_id, Memory.id))
        by_user: dict[str, list[MemoryModel]] = {}
        for row in result.scalars().all():
            by_user.setdefault(row.user_id, []).append(MemoryModel.model_validate(row))
    state = MemoryState()
    if dry_run:
        # A preview reads no vectors: the vector client connects on import.
        state.per_user = {user_id: (len(memories), 0) for user_id, memories in by_user.items()}
        return state
    from open_webui.routers import memories as router

    request = None
    for user_id, memories in sorted(by_user.items()):
        expected = {memory.id for memory in memories}
        if await _vector_ids(router.ASYNC_VECTOR_DB_CLIENT, user_id) == expected:
            state.per_user[user_id] = (len(memories), len(expected))
            continue
        if request is None:
            function = embedding_function or await build_embedding_function()
            request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(EMBEDDING_FUNCTION=function)))
        user = await Users.get_user_by_id(user_id)
        await router.reindex_memory_vectors_for_user(
            request, user_id, memories=memories, user=user, embedding_function=request.app.state.EMBEDDING_FUNCTION
        )
        state.reembedded.append(user_id)
        stored = await _vector_ids(router.ASYNC_VECTOR_DB_CLIENT, user_id)
        state.per_user[user_id] = (len(memories), len(stored & expected))
    return state
