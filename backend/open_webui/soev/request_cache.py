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


def remember(key, value):
    cache = _cache.get()
    if cache is not None and key not in cache:
        future = asyncio.get_running_loop().create_future()
        future.set_result(value)
        cache[key] = future


_batch_hook = ContextVar('soev_batch_hook', default=None)


@contextmanager
def batch_hook(callback):
    token = _batch_hook.set(callback)
    try:
        yield
    finally:
        _batch_hook.reset(token)


def has_batch_hook():
    return _batch_hook.get() is not None


async def fetch_batch(reads, fetch):
    hook = _batch_hook.get()
    return await fetch(reads) if hook is None else await hook(reads, fetch)
