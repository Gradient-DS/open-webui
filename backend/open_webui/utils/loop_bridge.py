"""Bridge from sync/thread-pool code to uvicorn's running event loop.

Use `run_on_main_loop(coro)` instead of `asyncio.run(coro)` in sync
handlers that touch shared async resources (e.g. `sio.emit`, which
goes through the python-socketio `AsyncRedisManager`'s connection
pool). `asyncio.run` creates a brand-new loop for each call and
closes it on return; any async object whose internal Future belongs
to that loop — including a pooled `redis.asyncio.Connection` stream —
becomes unusable from the main loop afterwards, producing:

    RuntimeError: got Future attached to a different loop
    RuntimeError: Event loop is closed

See thoughts/shared/research/2026-04-20-redis-ha-loop-bug-and-kind-repro.md.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Coroutine, Optional

log = logging.getLogger(__name__)

_MAIN_LOOP: Optional[asyncio.AbstractEventLoop] = None


def set_main_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Register uvicorn's serving loop. Call once from the FastAPI lifespan."""
    global _MAIN_LOOP
    _MAIN_LOOP = loop


def run_on_main_loop(coro: Coroutine[Any, Any, Any]) -> Any:
    """Run a coroutine on uvicorn's serving loop and return its result.

    Safe to call from a sync function running in an anyio thread-pool
    worker or a starlette BackgroundTasks thread. The coroutine executes
    on the main loop, so any shared async resources it touches (pooled
    redis.asyncio connections, the Socket.IO AsyncRedisManager, etc.)
    stay bound to one loop across the process lifetime.

    Falls back to `asyncio.run(coro)` only when no main loop has been
    registered (e.g. a CLI entry point invoked before the FastAPI
    lifespan runs). That fallback preserves current behaviour for
    those paths; it does NOT mask the loop-binding bug for in-process
    request handlers — those always go through the main loop.
    """
    if _MAIN_LOOP is None or _MAIN_LOOP.is_closed():
        log.warning(
            'run_on_main_loop: no registered main loop — falling back to '
            'asyncio.run(). Expect loop-binding issues if the coroutine '
            'touches shared async resources (Socket.IO / redis.asyncio).'
        )
        return asyncio.run(coro)
    return asyncio.run_coroutine_threadsafe(coro, _MAIN_LOOP).result()
