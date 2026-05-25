"""Machine-auth retrieval endpoint for external agents.

Mounted at ``/api/v1/internal/retrieval``. Authenticated by an allow-listed
agent bearer (see :class:`open_webui.utils.service_auth.AgentPrincipal`); the
acting user is carried in the ``X-Acting-User-Id`` header. The handler is a
thin shim over :func:`run_agent_search` so the same pipeline serves the
endpoint and any future caller.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from open_webui.models.files import Files
from open_webui.services.retrieval.agent_search import (
    resolve_accessible_kbs,
    run_agent_search,
)
from open_webui.utils.access_control.files import has_access_to_file
from open_webui.utils.service_auth import AgentPrincipal, get_agent_principal

router = APIRouter()
log = logging.getLogger(__name__)


class AgentSearchBody(BaseModel):
    query: str = Field(..., min_length=1)
    top_k: int = Field(10, ge=1, le=100)
    kb_ids: Optional[list[str]] = None


class AgentSearchResult(BaseModel):
    kb_id: str
    file_id: str
    chunk: str
    score: float
    metadata: dict


class AgentSearchResponse(BaseModel):
    results: list[AgentSearchResult]


class AccessibleKB(BaseModel):
    id: str
    collection_name: str
    name: str
    description: str = ''
    type: Optional[str] = None
    owner_id: Optional[str] = None


class AccessibleKBsResponse(BaseModel):
    user_id: str
    kbs: list[AccessibleKB]
    kb_index_collection_name: str


class AccessibleFilesResponse(BaseModel):
    user_id: str
    file_ids: list[str]


@router.get('/accessible-kbs', response_model=AccessibleKBsResponse)
async def list_accessible_kbs(
    request: Request,
    principal: AgentPrincipal = Depends(get_agent_principal),
    kb_ids: Optional[str] = None,
) -> AccessibleKBsResponse:
    """List the KBs the acting user may read, plus the meta-collection name.

    Shaped for agents that prefer to query the per-tenant Weaviate directly:
    ``collection_name`` is the sanitised class name as it lives in the vector
    DB; ``kb_index_collection_name`` points at the meta-collection where each
    KB has a ``(name, description)`` embedding indexed by
    ``metadata.knowledge_base_id``. Open-webui owns the ACL pass; the agent
    owns the query construction.

    :param kb_ids: Optional comma-separated subset filter. KBs not in the
        subset are dropped before the suspended-KB filter runs.
    """

    if not getattr(request.app.state.config, 'AGENT_SEARCH_ENABLED', False):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='agent search not enabled',
        )

    parsed_kb_ids: Optional[list[str]] = None
    if kb_ids:
        parsed_kb_ids = [kb_id.strip() for kb_id in kb_ids.split(',') if kb_id.strip()]

    log.info(
        'agent_accessible_kbs: agent=%s acting_user=%s kb_ids=%s',
        principal.agent_id,
        principal.user.id,
        'all' if parsed_kb_ids is None else f'{len(parsed_kb_ids)} kbs',
    )

    payload = await resolve_accessible_kbs(principal.user, kb_ids=parsed_kb_ids)
    return AccessibleKBsResponse(
        user_id=payload['user_id'],
        kbs=[AccessibleKB(**kb) for kb in payload['kbs']],
        kb_index_collection_name=payload['kb_index_collection_name'],
    )


@router.get('/accessible-files', response_model=AccessibleFilesResponse)
async def list_accessible_files(
    request: Request,
    principal: AgentPrincipal = Depends(get_agent_principal),
    file_ids: str = Query(..., description='Comma-separated file UUIDs to check'),
) -> AccessibleFilesResponse:
    """Return the subset of ``file_ids`` the acting user may read.

    Mirrors :func:`list_accessible_kbs` but at file granularity. Used by
    agents-api to validate per-file ad-hoc collections (``file-{uuid}``)
    before stashing them in the validated-collections ContextVar.

    No admin role shortcut: ``has_access_to_file`` already covers owner /
    KB-membership / direct grant — the only paths a tenant-isolated agent
    should respect.
    """

    if not getattr(request.app.state.config, 'AGENT_SEARCH_ENABLED', False):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='agent search not enabled',
        )

    requested = [fid.strip() for fid in file_ids.split(',') if fid.strip()]
    if not requested:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='file_ids query parameter must contain at least one id',
        )

    accessible = [fid for fid in requested if has_access_to_file(file_id=fid, access_type='read', user=principal.user)]

    log.info(
        'agent_accessible_files: agent=%s acting_user=%s requested=%d accessible=%d',
        principal.agent_id,
        principal.user.id,
        len(requested),
        len(accessible),
    )

    return AccessibleFilesResponse(
        user_id=principal.user.id,
        file_ids=accessible,
    )


class FileContentResponse(BaseModel):
    doc_id: str
    title: str
    content: str


@router.get('/files/{file_id}/content', response_model=FileContentResponse)
async def file_content(
    file_id: str,
    request: Request,
    principal: AgentPrincipal = Depends(get_agent_principal),
) -> FileContentResponse:
    """Return the extracted text + filename for a file the acting user can read.

    Same shape as :data:`OpenWebUIRetrievalProvider`'s legacy file-content
    fetch but gated by the agent bearer + ``X-Acting-User-Id`` instead of
    a per-user API key. ACL: ``has_access_to_file(read)`` (KB ownership /
    access-grants / shared-workspace / chat / channel paths — same logic
    chat retrieval uses).
    """

    if not getattr(request.app.state.config, 'AGENT_SEARCH_ENABLED', False):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='agent search not enabled',
        )

    file = await Files.get_file_by_id(file_id)
    if not file:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"file '{file_id}' not found",
        )

    user = principal.user
    # No admin shortcut here — the agent retrieval path is tenant-isolated by
    # construction; admin's UI-level cross-user reads do not extend to it.
    if file.user_id != user.id and not has_access_to_file(file_id=file_id, access_type='read', user=user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"user '{user.id}' has no read access to file '{file_id}'",
        )

    log.info(
        'agent_file_content: agent=%s acting_user=%s file=%s',
        principal.agent_id,
        user.id,
        file_id,
    )

    content = (file.data or {}).get('content', '') if isinstance(file.data, dict) else ''
    return FileContentResponse(
        doc_id=file_id,
        title=file.filename or '',
        content=content,
    )


@router.post(
    '/query',
    response_model=AgentSearchResponse,
    deprecated=True,
    summary='[Deprecated] Run a search via open-webui (legacy single-call path)',
)
async def agent_query(
    body: AgentSearchBody,
    request: Request,
    principal: AgentPrincipal = Depends(get_agent_principal),
) -> AgentSearchResponse:
    """**Deprecated** — kept for compatibility with the rev-2 simple-default path.

    The canonical agent retrieval flow is ``GET /accessible-kbs`` followed by
    direct Weaviate access from the agent (rev-3 amendment in
    `2026-04-25-shared-services-loader-worker.md`). All current Gradient agents
    use that path; nothing in the soev monorepo calls ``/query`` today. The
    endpoint stays on the wire so an external simple-agent integration that
    prefers a single open-webui call doesn't have to be rewritten, but new
    agents should not target it.
    """
    if not getattr(request.app.state.config, 'AGENT_SEARCH_ENABLED', False):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='agent search not enabled',
        )

    log.info(
        'agent_search: agent=%s acting_user=%s top_k=%d kb_ids=%s',
        principal.agent_id,
        principal.user.id,
        body.top_k,
        'all' if body.kb_ids is None else f'{len(body.kb_ids)} kbs',
    )

    results = await run_agent_search(
        request=request,
        user=principal.user,
        query=body.query,
        top_k=body.top_k,
        kb_ids=body.kb_ids,
    )
    return AgentSearchResponse(results=[AgentSearchResult(**r) for r in results])
