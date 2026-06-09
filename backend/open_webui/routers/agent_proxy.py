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

import asyncio
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

    model: Optional[str] = None
    messages: list[dict[str, Any]]
    files: Optional[list[AgentFileEntry]] = None
    agent: Optional[str] = None
    chat_id: Optional[str] = None
    stream: Optional[bool] = None


async def _find_kb_by_integration_source_id(provider: str, source_id: str):
    """Return the KB whose ``meta.integration.source_id`` matches ``source_id``.

    Mirrors ``routers/integrations._find_kb_by_source_id`` but kept inline
    so the agent proxy stays self-contained — the integrations router
    holds the canonical writer; this is the read-side lookup the proxy
    needs to translate caller-supplied source ids to KB UUIDs.
    """
    for kb in await Knowledges.get_knowledge_bases_by_type(provider):
        meta = kb.meta or {}
        if meta.get('integration', {}).get('source_id') == source_id:
            return kb
    return None


async def _resolve_collection_refs(files: list[dict[str, Any]]) -> None:
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
                detail=(
                    'files[].source_id requires files[].provider when files[].id is not set. '
                    'Either set files[].id to the KB UUID, or supply both files[].source_id and '
                    'files[].provider (the integration provider slug from /api/v1/configs).'
                ),
            )
        kb = await _find_kb_by_integration_source_id(provider, source_id)
        if kb is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"No knowledge base found for provider='{provider}' source_id='{source_id}'. "
                    'The pair is matched against meta.integration.source_id on KBs whose type '
                    'equals the provider slug. Check that the integration has actually ingested '
                    'this collection (POST /api/v1/integrations/ingest) and that the provider '
                    'slug matches the one configured in OWUI admin → Integraties.'
                ),
            )
        entry['id'] = kb.id
        entry.pop('source_id', None)
        entry.pop('provider', None)


def _get_base_url(request: Request) -> str:
    """Return the configured agent base URL or raise 503."""
    if not request.app.state.config.ENABLE_AGENT_PROXY:
        raise HTTPException(
            status_code=503,
            detail=(
                'Agent Proxy is disabled. Set ENABLE_AGENT_PROXY=true (Helm: '
                'openWebui.config.enableAgentProxy) and restart the OWUI pod.'
            ),
        )

    if not AGENT_API_BASE_URL:
        raise HTTPException(
            status_code=503,
            detail=(
                'AGENT_API_BASE_URL is not configured. Set it to the agent service URL '
                '(e.g. http://<release>-agent-agents-api:8080) via Helm '
                'openWebui.config.agentApiBaseUrl and restart the OWUI pod.'
            ),
        )
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


async def _proxy_get_json(base_url: str, path: str) -> Any:
    """GET ``{base_url}{path}`` upstream and return parsed JSON.

    Surfaces upstream non-2xx as a same-status HTTPException with the
    upstream body inline. Wraps connection / timeout / decode errors as
    a 502 with a typed message — bare ``str(e)`` on aiohttp errors can
    be empty and a stack trace from FastAPI is unhelpful for an
    operator deciding whether to look at the agent pod or this proxy.
    """
    session = aiohttp.ClientSession(trust_env=True, timeout=AIOHTTP_CLIENT_TIMEOUT)
    try:
        try:
            response = await session.request(
                method='GET',
                url=f'{base_url}{path}',
                headers=_auth_headers(),
            )
        except aiohttp.ClientConnectorError as e:
            raise HTTPException(
                status_code=502,
                detail=(
                    f'Cannot reach the agent service at {base_url}{path}: {e}. '
                    'Check AGENT_API_BASE_URL and that the agents-api pod is Running.'
                ),
            )
        except asyncio.TimeoutError:
            raise HTTPException(
                status_code=502,
                detail=(
                    f'Timed out calling the agent service at {base_url}{path} '
                    f'(>{int(AIOHTTP_CLIENT_TIMEOUT.total or 0)}s). The agent pod may be '
                    'overloaded or unresponsive — check `kubectl logs` and pod readiness.'
                ),
            )
        except aiohttp.ClientError as e:
            raise HTTPException(
                status_code=502,
                detail=(f'Upstream agent service error on GET {path}: {type(e).__name__}: {e}'),
            )

        if response.status >= 400:
            body = await response.text()
            raise HTTPException(
                status_code=response.status,
                detail=(
                    f'Agent service returned {response.status} on GET {path}. Upstream body: {body[:500] or "<empty>"}'
                ),
            )

        try:
            return await response.json()
        except (aiohttp.ContentTypeError, json.JSONDecodeError) as e:
            body_preview = (await response.text())[:500]
            raise HTTPException(
                status_code=502,
                detail=(
                    f'Agent service at {path} returned non-JSON body. '
                    f'Body preview: {body_preview!r}. Original error: {e}'
                ),
            )
    finally:
        await session.close()


async def _proxy_post_sse(
    base_url: str,
    upstream_path: str,
    payload: bytes,
) -> StreamingResponse:
    """POST ``payload`` to ``{base_url}{upstream_path}`` and stream the SSE body.

    Common error envelopes (502 on connect/timeout/client error, upstream
    status pass-through on >=400, JSON fallback when upstream is non-SSE).

    Ownership: on the SSE path the open ``session`` and ``response`` are
    handed to ``stream_wrapper``, which closes them when the client
    disconnects. On every other exit path this helper closes the session
    itself.
    """
    session = aiohttp.ClientSession(trust_env=True, timeout=AIOHTTP_CLIENT_TIMEOUT)
    try:
        try:
            response = await session.request(
                method='POST',
                url=f'{base_url}{upstream_path}',
                data=payload,
                headers=_auth_headers({'Content-Type': 'application/json'}),
            )
        except aiohttp.ClientConnectorError as e:
            await session.close()
            raise HTTPException(
                status_code=502,
                detail=(
                    f'Cannot reach the agent service at {base_url}{upstream_path}: {e}. '
                    'Check AGENT_API_BASE_URL and that the agents-api pod is Running.'
                ),
            )
        except asyncio.TimeoutError:
            await session.close()
            raise HTTPException(
                status_code=502,
                detail=(
                    f'Timed out calling the agent service at {base_url}{upstream_path} '
                    f'(>{int(AIOHTTP_CLIENT_TIMEOUT.total or 0)}s). The agent pod may be '
                    'overloaded or unresponsive — check `kubectl logs` and pod readiness.'
                ),
            )
        except aiohttp.ClientError as e:
            await session.close()
            raise HTTPException(
                status_code=502,
                detail=(f'Upstream agent service error on POST {upstream_path}: {type(e).__name__}: {e}'),
            )

        if response.status >= 400:
            error_body = await response.text()
            await session.close()
            raise HTTPException(
                status_code=response.status,
                detail=(
                    f'Agent service returned {response.status} on {upstream_path}. '
                    f'Upstream body: {error_body[:1000] or "<empty>"}'
                ),
            )

        content_type = response.headers.get('Content-Type', '')

        if 'text/event-stream' in content_type:
            return StreamingResponse(
                stream_wrapper(response, session),
                media_type='text/event-stream',
            )

        data = await response.json()
        await session.close()
        return data

    except HTTPException:
        raise
    except Exception as e:
        await session.close()
        log.exception('Agent proxy unexpected error on POST %s', upstream_path)
        raise HTTPException(
            status_code=502,
            detail=f'Agent proxy unexpected error: {type(e).__name__}: {e}',
        )


@router.get('/models')
async def list_models(request: Request, user=Depends(get_verified_user)):
    """Proxy GET /v1/models from the agent service."""
    base_url = _get_base_url(request)
    return await _proxy_get_json(base_url, '/v1/models')


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
        await _resolve_collection_refs(payload_dict['files'])
    payload_dict.setdefault('user_id', user.id)
    payload = json.dumps(payload_dict).encode()

    return await _proxy_post_sse(base_url, '/v1/chat/completions', payload)


@router.get('/gradient_agent_meta')
async def gradient_agent_meta(request: Request, user=Depends(get_verified_user)):
    """Proxy GET /v1/gradient_agent_meta from the agent service.

    Returns the default agent's metadata (description, welcome_message, ...)
    so the chat UI can render it without exposing ``AGENT_API_KEY``.
    """
    base_url = _get_base_url(request)
    return await _proxy_get_json(base_url, '/v1/gradient_agent_meta')


@router.get('/openapi.json')
async def openapi_spec(request: Request, user=Depends(get_verified_user)):
    """Proxy the agent service's OpenAPI spec."""
    base_url = _get_base_url(request)
    return await _proxy_get_json(base_url, '/openapi.json')
