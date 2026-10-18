"""Deduplicate reads only within the current authenticated HTTP request."""

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar

_cache: ContextVar[dict | None] = ContextVar('soev_request_cache', default=None)


@contextmanager
def request_cache():
    existing = _cache.get()
    if existing is not None:
        yield
        return
    cache = {}
    token = _cache.set(cache)
    try:
        yield
    finally:
        cache.clear()
        _cache.reset(token)


async def memoized(key, fetch):
    cache = _cache.get()
    if cache is None:
        return await fetch()
    if key not in cache:
        cache[key] = asyncio.create_task(fetch())
    return await cache[key]


def invalidate():
    cache = _cache.get()
    if cache is not None:
        cache.clear()
