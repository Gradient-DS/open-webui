"""[Gradient] Agent metadata proxy.

Thin pass-through to the external agent service so the chat UI can read
the active agent's metadata (description, welcome_message, ...) without
exposing ``AGENT_API_KEY`` to the browser. Mirrors how the proxy's
``/v1/chat/completions`` falls back to the agent service's configured
``default_agent`` when no agent is specified.
"""

import logging

import aiohttp
from fastapi import APIRouter, Depends, HTTPException

from open_webui.env import AGENT_API_BASE_URL, AGENT_API_KEY
from open_webui.utils.auth import get_verified_user

log = logging.getLogger(__name__)

router = APIRouter()

AIOHTTP_CLIENT_TIMEOUT = aiohttp.ClientTimeout(total=10)


@router.get('')
async def get_default_agent(user=Depends(get_verified_user)):
    if not AGENT_API_BASE_URL:
        raise HTTPException(status_code=503, detail='AGENT_API_BASE_URL is not configured')

    headers: dict[str, str] = {}
    if AGENT_API_KEY:
        headers['X-API-Key'] = AGENT_API_KEY

    session = aiohttp.ClientSession(trust_env=True, timeout=AIOHTTP_CLIENT_TIMEOUT)
    try:
        response = await session.request(
            method='GET',
            url=f'{AGENT_API_BASE_URL}/v1/gradient_agent_meta',
            headers=headers,
        )
        if response.status == 404:
            raise HTTPException(status_code=404, detail='Agent not found')
        if response.status >= 400:
            body = await response.text()
            log.warning('Agent service returned %s: %s', response.status, body)
            raise HTTPException(status_code=502, detail='Upstream agent service error')
        return await response.json()
    except aiohttp.ClientError as exc:
        log.warning('Agent service unreachable: %s', exc)
        raise HTTPException(status_code=502, detail='Upstream agent service unreachable')
    finally:
        await session.close()
