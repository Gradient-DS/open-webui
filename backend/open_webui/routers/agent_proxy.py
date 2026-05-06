"""
Agent Proxy — reverse-proxy to an external agent service.

[Gradient] Exposes the agent service's OpenAI-compatible API to users
authenticated with OWUI API keys. The agent service itself has no auth;
OWUI owns auth, the agent service owns inference.

Endpoints:
    GET  /models           → agent /v1/models
    POST /chat/completions → agent /v1/chat/completions (streaming SSE)
    GET  /openapi.json     → agent /openapi.json
"""

import json
import logging
from typing import Any, Optional

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import StreamingResponse

from open_webui.env import AGENT_API_BASE_URL, AGENT_API_KEY
from open_webui.models.knowledge import Knowledges
from open_webui.utils.auth import get_verified_user
from open_webui.utils.misc import stream_wrapper

log = logging.getLogger(__name__)

router = APIRouter()

AIOHTTP_CLIENT_TIMEOUT = aiohttp.ClientTimeout(total=300)


class AgentFileEntry(BaseModel):
    """Reference to a file or knowledge collection attached to a chat request.

    [Gradient] Callers may address a collection either by its OWUI ``id``
    (UUID) or by the integration ingest ``source_id`` plus ``provider``
    slug pair — whichever they have at hand. ``id`` wins when both forms
    are supplied; ``source_id`` is resolved via
    ``meta.integration.source_id`` on the matching provider's KBs and
    rewritten to ``id`` before the request is forwarded upstream.
    """

    model_config = ConfigDict(extra='allow')

    type: str = Field(description='``collection`` for a knowledge base, ``file`` for a single file.')
    id: Optional[str] = Field(
        default=None,
        description='OWUI knowledge-base UUID. Takes precedence when both ``id`` and ``source_id`` are supplied.',
    )
    source_id: Optional[str] = Field(
        default=None,
        description='Integration ingest dedup key (``meta.integration.source_id``). Requires ``provider`` to disambiguate across providers.',
    )
    provider: Optional[str] = Field(
        default=None,
        description='Integration provider slug. Required alongside ``source_id``; ignored when ``id`` is supplied.',
    )
    name: Optional[str] = None


class ChatCompletionsRequest(BaseModel):
    """Body for ``POST /api/v1/agent/chat/completions``.

    [Gradient] Documented schema for the agent proxy. Fields not declared
    here pass through untouched to the upstream agent service so this
    model never lags behind upstream additions.
    """

    model_config = ConfigDict(extra='allow')

    model: str
    messages: list[dict[str, Any]]
    files: Optional[list[AgentFileEntry]] = None
    agent: Optional[str] = None
    chat_id: Optional[str] = None
    stream: Optional[bool] = None


def _find_kb_by_integration_source_id(provider: str, source_id: str):
    """Return the KB whose ``meta.integration.source_id`` matches ``source_id``.

    Mirrors ``routers/integrations._find_kb_by_source_id`` but kept inline
    so the agent proxy stays self-contained — the integrations router
    holds the canonical writer; this is the read-side lookup the proxy
    needs to translate caller-supplied source ids to KB UUIDs.
    """
    for kb in Knowledges.get_knowledge_bases_by_type(provider):
        meta = kb.meta or {}
        if meta.get('integration', {}).get('source_id') == source_id:
            return kb
    return None


def _resolve_collection_refs(files: list[dict[str, Any]]) -> None:
    """Rewrite ``source_id``-addressed collection refs to UUIDs in place.

    UUID precedence: when ``id`` is set, ``source_id`` / ``provider`` are
    stripped without a lookup. Otherwise ``source_id`` + ``provider`` are
    resolved against ``meta.integration.source_id``.
    """
    for entry in files:
        if entry.get('type') != 'collection':
            continue
        if entry.get('id'):
            entry.pop('source_id', None)
            entry.pop('provider', None)
            continue
        source_id = entry.get('source_id')
        if not source_id:
            continue
        provider = entry.get('provider')
        if not provider:
            raise HTTPException(
                status_code=400,
                detail='files[].source_id requires files[].provider when files[].id is not set',
            )
        kb = _find_kb_by_integration_source_id(provider, source_id)
        if kb is None:
            raise HTTPException(
                status_code=404,
                detail=f"No knowledge base found for provider='{provider}' source_id='{source_id}'",
            )
        entry['id'] = kb.id
        entry.pop('source_id', None)
        entry.pop('provider', None)


def _get_base_url(request: Request) -> str:
    """Return the configured agent base URL or raise 503."""
    if not request.app.state.config.ENABLE_AGENT_PROXY:
        raise HTTPException(status_code=503, detail='Agent Proxy is disabled')

    if not AGENT_API_BASE_URL:
        raise HTTPException(status_code=503, detail='AGENT_API_BASE_URL is not configured')
    return AGENT_API_BASE_URL


def _auth_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Build outbound headers, including ``X-API-Key`` when configured.

    The agent service enforces the key on all ``/v1/*`` routes, so every
    proxied request carries it when ``AGENT_API_KEY`` is set.
    """
    headers: dict[str, str] = dict(extra or {})
    if AGENT_API_KEY:
        headers['X-API-Key'] = AGENT_API_KEY
    return headers


@router.get('/models')
async def list_models(request: Request, user=Depends(get_verified_user)):
    """Proxy GET /v1/models from the agent service."""
    base_url = _get_base_url(request)

    session = aiohttp.ClientSession(trust_env=True, timeout=AIOHTTP_CLIENT_TIMEOUT)
    try:
        response = await session.request(
            method='GET',
            url=f'{base_url}/v1/models',
            headers=_auth_headers(),
        )
        if response.status >= 400:
            body = await response.text()
            raise HTTPException(status_code=response.status, detail=body)
        data = await response.json()
        return data
    finally:
        await session.close()


@router.post('/chat/completions')
async def chat_completions(
    request: Request,
    body: ChatCompletionsRequest,
    user=Depends(get_verified_user),
):
    """Proxy POST /v1/chat/completions to the agent service with SSE streaming.

    Injects the verified user's UUID into the body as ``user_id`` so the
    agent service can set its acting-user ContextVar. Without this, KB
    retrieval tools that gate on ``/accessible-kbs`` fail with
    "acting user is not set".

    Each ``files[]`` entry of type ``collection`` may be addressed by KB
    UUID (``id``) or by the integration ingest pair (``source_id`` +
    ``provider``). The pair is resolved to a UUID here so the upstream
    agent only ever sees ``id``. ``id`` takes precedence when both are
    provided.
    """
    base_url = _get_base_url(request)

    payload_dict = body.model_dump(exclude_none=True)
    if payload_dict.get('files'):
        _resolve_collection_refs(payload_dict['files'])
    payload_dict.setdefault('user_id', user.id)
    payload = json.dumps(payload_dict).encode()

    session = aiohttp.ClientSession(trust_env=True, timeout=AIOHTTP_CLIENT_TIMEOUT)
    try:
        response = await session.request(
            method='POST',
            url=f'{base_url}/v1/chat/completions',
            data=payload,
            headers=_auth_headers({'Content-Type': 'application/json'}),
        )

        if response.status >= 400:
            error_body = await response.text()
            await session.close()
            raise HTTPException(status_code=response.status, detail=error_body)

        content_type = response.headers.get('Content-Type', '')

        if 'text/event-stream' in content_type:
            return StreamingResponse(
                stream_wrapper(response, session),
                media_type='text/event-stream',
            )
        else:
            data = await response.json()
            await session.close()
            return data

    except HTTPException:
        raise
    except Exception as e:
        await session.close()
        log.error(f'Agent proxy error: {e}')
        raise HTTPException(status_code=502, detail=str(e))


@router.get('/gradient_agent_meta')
async def gradient_agent_meta(request: Request, user=Depends(get_verified_user)):
    """Proxy GET /v1/gradient_agent_meta from the agent service.

    Returns the default agent's metadata (description, welcome_message, ...)
    so the chat UI can render it without exposing ``AGENT_API_KEY``.
    """
    base_url = _get_base_url(request)

    session = aiohttp.ClientSession(trust_env=True, timeout=AIOHTTP_CLIENT_TIMEOUT)
    try:
        response = await session.request(
            method='GET',
            url=f'{base_url}/v1/gradient_agent_meta',
            headers=_auth_headers(),
        )
        if response.status >= 400:
            body = await response.text()
            raise HTTPException(status_code=response.status, detail=body)
        return await response.json()
    finally:
        await session.close()


@router.get('/openapi.json')
async def openapi_spec(request: Request, user=Depends(get_verified_user)):
    """Proxy the agent service's OpenAPI spec."""
    base_url = _get_base_url(request)

    session = aiohttp.ClientSession(trust_env=True, timeout=AIOHTTP_CLIENT_TIMEOUT)
    try:
        response = await session.request(
            method='GET',
            url=f'{base_url}/openapi.json',
            headers=_auth_headers(),
        )
        if response.status >= 400:
            body = await response.text()
            raise HTTPException(status_code=response.status, detail=body)
        data = await response.json()
        return data
    finally:
        await session.close()
