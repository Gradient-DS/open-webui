"""
Cross-replica OAuth pending-flow store for the sync providers.

Backed by `request.app.state.redis` when configured, with an in-process dict
fallback so single-replica dev setups (no Redis) keep working. Used by the
OneDrive, Google Drive, and Confluence auth modules to share auth-code-flow
handshake state (PKCE verifier, redirect URI, knowledge_id) between the pod
that handled `/auth/initiate` and the pod that handles the OAuth callback.
"""

import asyncio
import json
import time
from typing import Any, Optional

from fastapi import Request

from open_webui.env import REDIS_KEY_PREFIX

_DEFAULT_TTL_SECONDS = 600

_FALLBACK: dict[str, dict[str, Any]] = {}
_FALLBACK_LOCK = asyncio.Lock()


def _key(provider: str, state: str) -> str:
    return f'{REDIS_KEY_PREFIX}:oauth:pending:{provider}:{state}'


def _redis(request: Request):
    return getattr(request.app.state, 'redis', None)


async def store_pending_flow(
    request: Request,
    provider: str,
    state: str,
    payload: dict[str, Any],
    ttl: int = _DEFAULT_TTL_SECONDS,
) -> None:
    redis = _redis(request)
    if redis is not None:
        await redis.set(_key(provider, state), json.dumps(payload), ex=ttl)
        return

    async with _FALLBACK_LOCK:
        _FALLBACK[_key(provider, state)] = {**payload, '_expires_at': time.time() + ttl}


async def get_pending_flow(
    request: Request,
    provider: str,
    state: str,
) -> Optional[dict[str, Any]]:
    redis = _redis(request)
    if redis is not None:
        raw = await redis.get(_key(provider, state))
        return json.loads(raw) if raw else None

    async with _FALLBACK_LOCK:
        item = _FALLBACK.get(_key(provider, state))
        if not item:
            return None
        if item['_expires_at'] < time.time():
            _FALLBACK.pop(_key(provider, state), None)
            return None
        return {k: v for k, v in item.items() if k != '_expires_at'}


async def pop_pending_flow(
    request: Request,
    provider: str,
    state: str,
) -> Optional[dict[str, Any]]:
    redis = _redis(request)
    if redis is not None:
        # GETDEL is atomic and available since Redis 6.2; our chart image is 7-alpine.
        raw = await redis.execute_command('GETDEL', _key(provider, state))
        return json.loads(raw) if raw else None

    async with _FALLBACK_LOCK:
        item = _FALLBACK.pop(_key(provider, state), None)
        if not item or item['_expires_at'] < time.time():
            return None
        return {k: v for k, v in item.items() if k != '_expires_at'}


async def remove_pending_flow(request: Request, provider: str, state: str) -> None:
    redis = _redis(request)
    if redis is not None:
        await redis.delete(_key(provider, state))
        return

    async with _FALLBACK_LOCK:
        _FALLBACK.pop(_key(provider, state), None)


async def has_pending_flow(request: Request, provider: str, state: str) -> bool:
    redis = _redis(request)
    if redis is not None:
        return bool(await redis.exists(_key(provider, state)))

    async with _FALLBACK_LOCK:
        item = _FALLBACK.get(_key(provider, state))
        return item is not None and item['_expires_at'] >= time.time()
