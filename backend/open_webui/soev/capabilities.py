"""Optional platform features advertised by the configured soev backend."""

import asyncio
import logging
from time import monotonic

from fastapi import HTTPException

from open_webui import config
from open_webui.constants import ERROR_MESSAGES
from open_webui.soev.availability import ensure_configured

LIVE_DOCUMENTS = 'live_documents'
LIVE_MAIL = 'live_mail'
CLOUD_SYNC = 'cloud_sync'
MEETINGS = 'meetings'
GROUPS_PUSH = 'groups_push'
IMAGES = 'images'

TTL_SECONDS = 60
log = logging.getLogger(__name__)
_features: frozenset[str] = frozenset()
_expires_at = 0.0
_backend: tuple[str, str] | None = None
_lock: tuple[asyncio.AbstractEventLoop, asyncio.Lock] | None = None
_warned = False


async def refresh() -> frozenset[str]:
    global _features, _expires_at, _backend, _lock, _warned
    backend = (config.SOEV_API_URL, config.SOEV_API_KEY)
    if backend != _backend:
        _backend, _features, _expires_at, _warned = backend, frozenset(), 0.0, False
    if not backend[0]:
        return frozenset()
    loop = asyncio.get_running_loop()
    if _lock is None or _lock[0] is not loop:
        _lock = (loop, asyncio.Lock())
    async with _lock[1]:
        if monotonic() < _expires_at:
            return _features
        try:
            # Identity imports availability during model/config initialization.
            from open_webui.soev.identity import build_client

            payload = await build_client().get('/v1/capabilities')
            features = payload.get('features')
            if not isinstance(payload.get('contract'), str) or not isinstance(features, list):
                raise ValueError('Invalid capabilities response')
            if not all(isinstance(feature, str) for feature in features):
                raise ValueError('Invalid capability names')
            _features = frozenset(features)
            _warned = False
        except Exception:
            _features = frozenset()
            if not _warned:
                log.warning('soev capabilities unavailable; optional features are disabled')
                _warned = True
        _expires_at = monotonic() + TTL_SECONDS
        return _features


async def has(feature: str) -> bool:
    return feature in await refresh()


async def require(feature: str) -> None:
    ensure_configured()
    if not await has(feature):
        raise HTTPException(
            status_code=503,
            detail={'code': 'feature_unavailable', 'detail': ERROR_MESSAGES.DEFAULT()},
        )
