"""Discovery proxy — reverse-proxy to the agents-api document catalog.

[Gradient] Exposes the upstream ``GET /v1/discovery/documents`` to OWUI
users authenticated by session cookie / JWT, with an X-API-Key header
that never leaves the backend. Mirrors the agent_proxy pattern:
route-level get_verified_user, flag + URL check at request time, typed
502s on upstream failure.

Upstream is soev-agents
(``adapters/openwebui/service/routes/discovery.py``), which hosts the
catalog on the agents-api now that the standalone search-api is gone.
SEARCH_API_BASE_URL / SEARCH_API_KEY therefore point at the agents-api —
same values as AGENT_API_BASE_URL / AGENT_API_KEY; the names are kept
because the Helm chart is wired for them.
"""

import asyncio
import json
import logging

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Request

from open_webui.env import SEARCH_API_BASE_URL, SEARCH_API_KEY
from open_webui.models.config import Config
from open_webui.utils.auth import get_verified_user

log = logging.getLogger(__name__)

router = APIRouter()

AIOHTTP_CLIENT_TIMEOUT = aiohttp.ClientTimeout(total=30)


async def _get_base_url(request: Request) -> str:
    """Return the configured agents-api URL or raise 503.

    Reuses ENABLE_RAG_FILTER_UI — the same flag that controls panel
    visibility on the frontend — so a single Helm toggle gates the whole
    feature. SEARCH_API_BASE_URL must also be set; an empty string is a
    misconfig, not a runtime toggle.
    """
    if not await Config.get('rag.enable_filter_ui'):
        raise HTTPException(
            status_code=503,
            detail=(
                'RAG filter UI is disabled. Set ENABLE_RAG_FILTER_UI=true '
                '(Helm: openWebui.config.enableRagFilterUi) and restart the OWUI pod.'
            ),
        )
    if not SEARCH_API_BASE_URL:
        raise HTTPException(
            status_code=503,
            detail=(
                'SEARCH_API_BASE_URL is not configured. Set it to the agents-api URL '
                '(e.g. http://soev-agents:8181 — the same value as AGENT_API_BASE_URL) '
                'via Helm openWebui.config.searchApi.baseUrl and restart the OWUI pod.'
            ),
        )
    return SEARCH_API_BASE_URL


def _auth_headers() -> dict[str, str]:
    """Build outbound headers, including X-API-Key when configured."""
    headers: dict[str, str] = {}
    if SEARCH_API_KEY:
        headers['X-API-Key'] = SEARCH_API_KEY
    return headers


async def _proxy_get_json(base_url: str, path: str):
    """GET {base_url}{path} upstream and return parsed JSON.

    Same shape as agent_proxy._proxy_get_json — typed 502s for
    connection / timeout / decode failures, status-passthrough for
    upstream non-2xx so the operator sees the real error.
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
                    f'Cannot reach the agents-api at {base_url}{path}: {e}. '
                    'Check SEARCH_API_BASE_URL and that the upstream is reachable '
                    '(Tailscale tunnel up, target host listening).'
                ),
            )
        except asyncio.TimeoutError:
            raise HTTPException(
                status_code=504,
                detail=(
                    f'Timed out calling the agents-api at {base_url}{path} '
                    f'(>{int(AIOHTTP_CLIENT_TIMEOUT.total or 0)}s).'
                ),
            )
        except aiohttp.ClientError as e:
            raise HTTPException(
                status_code=502,
                detail=f'Upstream agents-api error on GET {path}: {type(e).__name__}: {e}',
            )

        if response.status == 401:
            raise HTTPException(
                status_code=502,
                detail=(
                    'agents-api returned 401 — SEARCH_API_KEY is wrong or unset. '
                    'It must match the agents-api key (AGENT_API_KEY / agents SOEV_AGENTS_API_KEY).'
                ),
            )
        if response.status >= 400:
            body = await response.text()
            raise HTTPException(
                status_code=response.status,
                detail=(
                    f'agents-api returned {response.status} on GET {path}. Upstream body: {body[:500] or "<empty>"}'
                ),
            )

        try:
            return await response.json()
        except (aiohttp.ContentTypeError, json.JSONDecodeError) as e:
            body_preview = (await response.text())[:500]
            raise HTTPException(
                status_code=502,
                detail=(
                    f'agents-api at {path} returned non-JSON body. Body preview: {body_preview!r}. Original error: {e}'
                ),
            )
    finally:
        await session.close()


@router.get('/documents')
async def list_documents(request: Request, user=Depends(get_verified_user)):
    """Proxy GET /v1/discovery/documents from the upstream agents-api.

    Returns the upstream payload verbatim: ``{collections[], total_collections,
    database{}}``. The frontend consumes that shape directly.
    """
    base_url = await _get_base_url(request)
    return await _proxy_get_json(base_url, '/v1/discovery/documents')
