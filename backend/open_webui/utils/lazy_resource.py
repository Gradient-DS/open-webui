"""Lazy attribute proxy for module-scope async resources.

Used to defer creation of long-lived async Redis clients (python-socketio
AsyncRedisManager, redis.asyncio.Redis clients, starsessions RedisStore
connections) until uvicorn's lifespan is running. This fixes the well-known
"got Future attached to a different loop" crash documented in thoughts/shared/research/2026-04-20-redis-ha-loop-bug-and-kind-repro.md.

Design: we keep the module-scope symbol (`REDIS = _LazyProxy(...)`) so call
sites read unchanged, but the underlying object is created only when someone
actually uses it — i.e. from inside a request/task/socket handler running on
uvicorn's event loop.
"""

import threading
from typing import Any, Callable

_UNSET = object()


class _LazyProxy:
    __slots__ = ('_factory', '_target', '_lock')

    def __init__(self, factory: Callable[[], Any]) -> None:
        self._factory = factory
        self._target: Any = _UNSET
        self._lock = threading.Lock()

    def _resolve(self) -> Any:
        if self._target is _UNSET:
            with self._lock:
                if self._target is _UNSET:
                    self._target = self._factory()
        return self._target

    def __getattr__(self, name: str) -> Any:
        return getattr(self._resolve(), name)

    def __bool__(self) -> bool:
        return self._target is not _UNSET or self._factory is not None

    def __repr__(self) -> str:
        if self._target is _UNSET:
            return f'<_LazyProxy unresolved factory={self._factory!r}>'
        return f'<_LazyProxy resolved target={self._target!r}>'


def lazy(factory: Callable[[], Any]) -> Any:
    """Return a lazy proxy that constructs the target on first attribute access."""
    return _LazyProxy(factory)
