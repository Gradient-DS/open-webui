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
import os
from typing import Any, Coroutine, Optional

log = logging.getLogger(__name__)

_MAIN_LOOP: Optional[asyncio.AbstractEventLoop] = None


def set_main_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Register uvicorn's serving loop. Call once from the FastAPI lifespan."""
    global _MAIN_LOOP
    _MAIN_LOOP = loop
    log.info('loop_bridge: main loop registered (id=%s)', id(loop))


def run_on_main_loop(coro: Coroutine[Any, Any, Any]) -> Any:
    """Run a coroutine on uvicorn's serving loop and return its result.

    Safe to call from a sync function running in an anyio thread-pool
    worker or a starlette BackgroundTasks thread. The coroutine executes
    on the main loop, so any shared async resources it touches (pooled
    redis.asyncio connections, the Socket.IO AsyncRedisManager, etc.)
    stay bound to one loop across the process lifetime.

    Returns ``None`` (and closes the coroutine) when no main loop is
    registered. The previous ``asyncio.run`` fallback masked a real bug —
    a missed ``set_main_loop`` call from the lifespan poisoned the
    Socket.IO Redis pool the first time a sync handler tried to emit, and
    the user-visible symptom (file:status emits dropped, infinite upload
    spinner — see thoughts/2026-04-30) was traceable only via tracebacks.
    Failing fast plus the front-end polling fallback in ``_processFileStatus``
    converts a silent corruption into a logged ERROR + a ~30s spinner.
    """
    if _MAIN_LOOP is None or _MAIN_LOOP.is_closed():
        log.error(
            'run_on_main_loop: no registered main loop. Lifespan ordering bug — '
            'Socket.IO emits are being dropped. The frontend polling fallback '
            'in KnowledgeBase._processFileStatus will recover.',
            extra={'event': 'loop_bridge.no_main_loop'},
        )
        # Test path: pure-CLI invocations (no FastAPI lifespan) still need
        # an executable fallback so unit tests can exercise sync helpers
        # that call run_on_main_loop without a serving uvicorn.
        if os.environ.get('OPEN_WEBUI_TESTING'):
            return asyncio.run(coro)
        coro.close()
        return None
    return asyncio.run_coroutine_threadsafe(coro, _MAIN_LOOP).result()
