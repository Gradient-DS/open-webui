"""Agent retrieval pipeline.

Wraps the existing chat-retrieval primitives in a single function that takes
``(user, query, top_k, kb_ids?)`` and returns a flat list of ranked chunks
with ``kb_id`` provenance attached. The KB-level ACL resolution and per-KB
Weaviate iteration both reuse what chat retrieval does today —
:meth:`Knowledges.get_knowledge_bases_by_user_id` for the ACL pass,
:func:`query_collection` for the per-collection vector / hybrid search.

This is the single seam through which external agents reach KB content; the
chat-retrieval router and the new ``/api/v1/internal/retrieval/query``
endpoint should both call into this so a future ACL refinement (e.g.
file-level visibility) lands in one place.

The sibling :func:`resolve_accessible_kbs` returns the same KB set in
metadata-only form (id, name, description, sanitized collection_name) for
agents that prefer to query the per-tenant Weaviate directly. Both functions
share the same ACL + suspension filter so they cannot drift.
"""

from __future__ import annotations

import logging
from typing import Optional

from open_webui.models.knowledge import KnowledgeUserModel, Knowledges
from open_webui.models.users import UserModel
from open_webui.retrieval.utils import query_collection
from open_webui.retrieval.vector.factory import VECTOR_DB_CLIENT
from open_webui.routers.knowledge import KNOWLEDGE_BASES_COLLECTION

log = logging.getLogger(__name__)


def _sanitize_collection_name(raw: str) -> str:
    """Return the collection name as the active vector-DB client would use it.

    For Weaviate this maps ``"abc-123"`` → ``"Abc_123"`` (capitalised, underscores).
    Other backends (Chroma, pgvector) typically pass through unchanged. Calling
    the client's own sanitiser keeps callers from re-implementing the mapping.
    """

    sanitiser = getattr(VECTOR_DB_CLIENT, '_sanitize_collection_name', None)
    if sanitiser is None:
        return raw
    try:
        return sanitiser(raw)
    except Exception as e:
        log.debug('collection name sanitiser failed for %s: %s', raw, e)
        return raw


async def _filter_to_accessible_kbs(
    user: UserModel,
    *,
    kb_ids: Optional[list[str]] = None,
) -> list[KnowledgeUserModel]:
    """Resolve KBs the user may read; drop suspended ones and apply optional subset.

    Includes both the access-grant pass and explicit ownership. Ownership
    is included defensively — the grant pass already covers it, but we
    have observed the upstream check returning empty for KB owners
    (research doc 2026-05-06). Owners must always see their own KBs.
    """

    accessible = await Knowledges.get_knowledge_bases_by_user_id(user.id, permission='read')
    owned = await Knowledges.get_knowledge_items_by_user_id(user.id)

    seen = {kb.id for kb in accessible}
    for kb in owned:
        if kb.id not in seen:
            accessible.append(kb)
            seen.add(kb.id)

    if kb_ids is not None:
        kb_id_set = set(kb_ids)
        accessible = [kb for kb in accessible if kb.id in kb_id_set]

    filtered = []
    for kb in accessible:
        if not await Knowledges.is_suspended(kb.id):
            filtered.append(kb)
    return filtered


async def resolve_accessible_kbs(
    user: UserModel,
    *,
    kb_ids: Optional[list[str]] = None,
) -> dict:
    """Return KBs the user may read, plus the meta-collection for KB selection.

    Shaped for agents that query Weaviate directly: ``collection_name`` is the
    sanitised class name as it lives in the vector DB (so the agent doesn't
    have to re-implement the sanitiser). ``kb_index_collection_name`` points
    at the meta-collection where each KB has a ``(name, description)``
    embedding under ``metadata.knowledge_base_id`` — the agent can run a
    semantic / keyword search against it (filtered by ``id`` from the kbs
    list) to pick which KBs are worth querying.
    """

    accessible = await _filter_to_accessible_kbs(user, kb_ids=kb_ids)
    return {
        'user_id': user.id,
        'kbs': [
            {
                'id': kb.id,
                'collection_name': _sanitize_collection_name(kb.id),
                'name': kb.name,
                'description': kb.description or '',
                'type': getattr(kb, 'type', None),
                'owner_id': kb.user_id,
            }
            for kb in accessible
        ],
        'kb_index_collection_name': _sanitize_collection_name(KNOWLEDGE_BASES_COLLECTION),
    }


async def resolve_accessible_kb(
    user: UserModel,
    *,
    kb_id: str,
) -> Optional[KnowledgeUserModel]:
    """Single-KB form of :func:`resolve_accessible_kbs`; shares the same ACL.

    Returns ``None`` if the KB does not exist, the user has no read access,
    or the KB is suspended. Reusing ``_filter_to_accessible_kbs`` keeps the
    file-listing endpoint's ACL from drifting away from the KB-listing one.
    """

    accessible = await _filter_to_accessible_kbs(user, kb_ids=[kb_id])
    return accessible[0] if accessible else None


async def run_agent_search(
    *,
    request,
    user: UserModel,
    query: str,
    top_k: int,
    kb_ids: Optional[list[str]] = None,
) -> list[dict]:
    """Resolve accessible KBs, run vector / hybrid search per KB, merge by score.

    Returns a list of ``{kb_id, file_id, chunk, score, metadata}`` dicts,
    sorted by descending score and truncated to ``top_k``. Per-KB failures
    are logged and skipped — a single broken collection never sinks the
    whole query.
    """

    accessible_kbs = await _filter_to_accessible_kbs(user, kb_ids=kb_ids)

    async def embedding_function(query_text, prefix):
        return await request.app.state.EMBEDDING_FUNCTION(query_text, prefix=prefix, user=user)

    results: list[dict] = []
    for kb in accessible_kbs:
        try:
            qr = await query_collection(
                request,
                collection_names=[kb.id],
                queries=[query],
                embedding_function=embedding_function,
                k=top_k,
            )
        except Exception as e:
            log.exception(f'agent_search: kb {kb.id} query failed: {e}')
            continue

        distances = (qr.get('distances') or [[]])[0]
        documents = (qr.get('documents') or [[]])[0]
        metadatas = (qr.get('metadatas') or [[]])[0]

        for distance, document, metadata in zip(distances, documents, metadatas):
            md = metadata or {}
            results.append(
                {
                    'kb_id': kb.id,
                    'file_id': md.get('file_id', ''),
                    'chunk': document,
                    'score': float(distance),
                    'metadata': md,
                }
            )

    results.sort(key=lambda r: r['score'], reverse=True)
    return results[:top_k]
