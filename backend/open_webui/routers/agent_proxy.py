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

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.responses import StreamingResponse

from open_webui.env import AGENT_API_BASE_URL, AGENT_API_KEY
from open_webui.utils.auth import get_verified_user
from open_webui.utils.misc import stream_wrapper

log = logging.getLogger(__name__)

router = APIRouter()

AIOHTTP_CLIENT_TIMEOUT = aiohttp.ClientTimeout(total=300)


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
async def chat_completions(request: Request, user=Depends(get_verified_user)):
    """Proxy POST /v1/chat/completions to the agent service with SSE streaming.

    Injects the verified user's UUID into the body as ``user_id`` so the
    agent service can set its acting-user ContextVar. Without this, KB
    retrieval tools that gate on ``/accessible-kbs`` fail with
    "acting user is not set".
    """
    base_url = _get_base_url(request)

    raw = await request.body()
    try:
        body = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail='Invalid JSON body')
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail='Body must be a JSON object')
    body.setdefault('user_id', user.id)
    payload = json.dumps(body).encode()

    session = aiohttp.ClientSession(trust_env=True, timeout=AIOHTTP_CLIENT_TIMEOUT)
    try:
        response = await session.request(
            method='POST',
            url=f'{base_url}/v1/chat/completions',
            data=payload,
            headers=_auth_headers({'Content-Type': 'application/json'}),
        )

        if response.status >= 400:
            body = await response.text()
            await session.close()
            raise HTTPException(status_code=response.status, detail=body)

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
