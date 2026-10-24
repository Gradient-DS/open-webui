"""Optional platform features advertised by the configured soev backend."""

import asyncio
import logging
from time import monotonic

import httpx
from fastapi import HTTPException

from open_webui import config
from open_webui.constants import ERROR_MESSAGES
from open_webui.soev.availability import ensure_configured
from open_webui.soev.client import SoevApiError

LIVE_DOCUMENTS = 'live_documents'
LIVE_MAIL = 'live_mail'
CLOUD_SYNC = 'cloud_sync'
MEETINGS = 'meetings'
GROUPS_PUSH = 'groups_push'
IMAGES = 'images'

TTL_SECONDS = 60
RETRY_SECONDS = 10
log = logging.getLogger(__name__)
_features: frozenset[str] = frozenset()
_expires_at = 0.0
_backend: tuple[str, str] | None = None
_lock: tuple[asyncio.AbstractEventLoop, asyncio.Lock] | None = None
_warned = False
_known: bool = False


async def refresh() -> frozenset[str]:
    global _features, _expires_at, _backend, _lock, _warned, _known
    backend = (config.SOEV_API_URL, config.SOEV_API_KEY)
    if backend != _backend:
        _backend, _features, _expires_at, _warned, _known = backend, frozenset(), 0.0, False, False
    if not backend[0]:
        return frozenset()
    loop = asyncio.get_running_loop()
    if _lock is None or _lock[0] is not loop:
        _lock = (loop, asyncio.Lock())
    async with _lock[1]:
        if monotonic() < _expires_at:
            return _features
        ttl = TTL_SECONDS
        try:
            from open_webui.soev.client import build_service_client

            payload = await build_service_client().get('/v1/capabilities')
            if not isinstance(payload, dict):
                raise ValueError('Invalid capabilities response')
            features = payload.get('features')
            if not isinstance(payload.get('contract'), str) or not isinstance(features, list):
                raise ValueError('Invalid capabilities response')
            if not all(isinstance(feature, str) for feature in features):
                raise ValueError('Invalid capability names')
            _features = frozenset(features)
            _known = True
            _warned = False
        except (httpx.HTTPError, SoevApiError, ValueError):
            ttl = RETRY_SECONDS
            if not _warned:
                log.warning('soev capabilities unavailable; retaining cached optional features')
                _warned = True
        _expires_at = monotonic() + ttl
        return _features


def known() -> bool:
    """Whether a capability read succeeded for the configured backend."""
    return _known


async def has(feature: str) -> bool:
    return feature in await refresh()


async def require(feature: str) -> None:
    ensure_configured()
    if not await has(feature):
        raise HTTPException(
            status_code=503,
            detail={'code': 'feature_unavailable', 'detail': ERROR_MESSAGES.DEFAULT()},
        )
