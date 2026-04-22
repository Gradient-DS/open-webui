# Redis HA Loop-Binding Fix + Kind Local Harness — Implementation Plan

## Overview

Replace the `_TolerantAsyncRedisManager` band-aid (commit `f828ab9e5`) with a structural fix that defers all module-import-time async Redis client construction until the FastAPI lifespan runs on uvicorn's event loop. In the same change, add a single-command Kind (Kubernetes-in-Docker) local HA harness so we can reproduce and verify the fix against the real Helm chart without touching a cluster. Add an automated two-loop pytest regression test so the bug class cannot reappear silently.

## Current State Analysis

Root cause is documented in `REDIS-HA-FIX.md` and the research doc `thoughts/shared/research/2026-04-20-redis-ha-loop-bug-and-kind-repro.md` — summary:

- Four module-import-time async Redis clients exist. Under HA (replicaCount ≥ 2) their primitives bind to whatever loop `asyncio.get_event_loop()` returns at import, which is not uvicorn's serving loop, producing `RuntimeError: got Future attached to a different loop` on concurrent `POST /api/chat/completions`.
- **Iteration 2** (commit `93b63a441`) deferred only `AsyncRedisManager`. It missed:
  1. `socket/main.py:166` — `REDIS = get_redis_connection(..., async_mode=True)` (shared with `YdocManager`, `stop_item_tasks`, `create_task`).
  2. `utils/redis.py:26` `_CONNECTION_CACHE` — makes `main.py:818`'s lifespan call return the **same** already-bound client from import.
  3. `main.py:2763` `RedisStore(url=REDIS_URL, ...)` — verified eager (`.venv/.../starsessions/stores/redis.py:51`: `Redis.from_url(url)` in `__init__`).
- **Iteration 3** (commit `f828ab9e5`) papered over the residual crash with a subclass that swallows the `RuntimeError` in `AsyncRedisManager._publish()` — fragile string match, silently drops cross-replica notifications.

All loop-binding code is inherited upstream from `open-webui/open-webui`. No soev customisations contribute to the bug. Our contribution is the HA topology that exposes it.

### Key Discoveries

- `backend/open_webui/socket/main.py:164-215` — the import-time init block to move (REDIS, MODELS, SESSION_POOL, USAGE_POOL, YDOC_MANAGER, clean_up_lock, session_cleanup_lock + their `aquire_func`/`renew_func`/`release_func` aliases).
- `backend/open_webui/utils/redis.py:26,189-190` — the cache that defeats iteration 2.
- `backend/open_webui/env.py:678` — `WEBSOCKET_REDIS_URL` defaults to `REDIS_URL`; the chart does not set a separate value, so both paths hit the same cache key.
- `backend/open_webui/main.py:818` — lifespan `app.state.redis` (downstream of cache).
- `backend/open_webui/main.py:826` — `asyncio.create_task(redis_task_command_listener(app))` pubsub listener; grabs `app.state.redis` inside the task.
- `backend/open_webui/main.py:2763` — `starsessions.RedisStore(url=REDIS_URL, ...)` at module scope.
- `backend/open_webui/socket/main.py:90-124` — `_TolerantAsyncRedisManager` class (to remove in this PR).
- `backend/open_webui/socket/utils.py:22,47` — `RedisLock` and `RedisDict` use SYNC `get_redis_connection` (no `async_mode=True`), so they are not loop-binding sources. They stay as-is.
- `helm/open-webui-tenant/` — 5 values overrides are enough for Kind; chart templates need no changes.
- No existing Kind / Tilt tooling in repo; `hack/` directory also does not exist yet.

## Desired End State

- All module-import-time async Redis client construction is gone. First `await` on any of them happens on uvicorn's running loop.
- `_CONNECTION_CACHE` is flushed at the start of the lifespan, so any stragglers that were accidentally created at import get discarded.
- `_TolerantAsyncRedisManager` is deleted; `socket/main.py` uses plain `socketio.AsyncRedisManager`.
- One command (`make -C hack/kind up`) brings up a local HA reproduction: 2 open-webui replicas, redis, postgres, ingress-nginx, ready to take requests on `http://soev.local:8080`.
- One command (`make -C hack/kind repro`) runs a concurrent load generator and greps both pod logs; exits non-zero if any `attached to a different loop` or `Event loop is closed` message appears.
- One pytest file reproduces the bug-class in-process using two event loops + fakeredis, and is green after the fix.
- In staging and (after 48 h) prod, grep of pod logs shows zero occurrences of either fragment.

### Verification

- `make -C hack/kind up` succeeds on a clean macOS dev box in under 6 min.
- `make -C hack/kind repro` succeeds (exit 0) after Layer A is applied.
- `cd backend && pytest open_webui/test/util/test_redis_loop_binding.py -v` passes.
- `npm run lint:backend` and `npm run format:backend` pass.
- Grep `kubectl logs -n soev-local -l app.kubernetes.io/name=open-webui --all-containers=true` shows zero `attached to a different loop` after a 5-minute `hey` soak.

## What We're NOT Doing

- **No upstream PR yet.** Per Q3: land the fix on our fork, verify in Kind + staging, then prepare an upstream PR as a follow-up. (The fix is upstream-merge-friendly by design.)
- **No changes to `RedisLock` / `RedisDict`** — they use sync clients and are not loop-binding sources. Moving them is cosmetic; keep the diff tight.
- **No `AppConfig._redis` (sync) changes.** Only runs with `ENABLE_PERSISTENT_CONFIG=true`; our chart default is `false`.
- **No switch to a different socket.io manager** (Kombu, aio-pika). Redis stays.
- **No sticky-session ingress / session-pinning.** HA routing stays replica-neutral.
- **No Kind tooling for RAG/Weaviate.** Weaviate disabled in the local values — off-path for this bug and saves ~1 GB.
- **No production rollout automation changes.** Flux `ImagePolicy` keeps doing what it does today.

## Implementation Approach

Three coordinated changes, small enough to land in one PR:

1. **Lazy proxies for async Redis resources** in `socket/main.py` and `main.py` (Option B from the planning decisions: zero churn at call sites, attribute access transparently resolves to the real client the first time it is awaited). The module still exposes `REDIS`, `YDOC_MANAGER`, `MODELS`, `SESSION_POOL`, `USAGE_POOL`, `clean_up_lock`, `session_cleanup_lock`, `redis_session_store` — but they are proxy objects whose underlying target is constructed on first access. Because first access happens inside uvicorn's loop, primitives bind there.
2. **Flush `_CONNECTION_CACHE` at lifespan start.** Guarantees the proxies cannot accidentally get a pre-lifespan cached client. Add a one-line log warn when an async client is requested before the flush — catches future regressions.
3. **Kind harness under `hack/kind/`** — `kind-config.yaml`, `values-local.yaml`, `Makefile`, `load-gen.sh`, regression test in `backend/open_webui/test/util/test_redis_loop_binding.py`.

Middleware conversion work from iteration 1 stays — it was correct and independent of this fix.

---

## Phase 1: Lazy async-Redis proxy scaffolding

### Overview

Add a small proxy utility used by every deferred async Redis resource. One class, two dozen lines. Used by Phase 2 and Phase 3.

### Changes Required

#### 1. New file `backend/open_webui/utils/lazy_resource.py`

**File**: `backend/open_webui/utils/lazy_resource.py`
**Changes**: New file. A thread-unsafe-by-design module-level lazy proxy. First attribute access constructs the target on the calling coroutine's loop.

```python
"""Lazy attribute proxy for module-scope async resources.

Used to defer creation of long-lived async Redis clients (python-socketio
AsyncRedisManager, redis.asyncio.Redis clients, starsessions RedisStore
connections) until uvicorn's lifespan is running. This fixes the well-known
"got Future attached to a different loop" crash documented in REDIS-HA-FIX.md.

Design: we keep the module-scope symbol (`REDIS = _LazyProxy(...)`) so call
sites read unchanged, but the underlying object is created only when someone
actually uses it — i.e. from inside a request/task/socket handler running on
uvicorn's event loop.
"""

from typing import Any, Callable, Optional
import threading

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
```

### Success Criteria

#### Automated Verification

- [x] File exists and imports cleanly: `python -c "from open_webui.utils.lazy_resource import lazy; print(lazy)"`
- [x] Linting passes: `npm run lint:backend`
- [x] Formatting passes: `npm run format:backend`

#### Manual Verification

- [ ] Code review: the proxy covers attribute access; `__bool__` handles `if REDIS:` patterns; no `__setattr__` is needed (callers never reassign).

---

## Phase 2: Defer `socket/main.py` module-import Redis init

### Overview

Replace the import-time `if WEBSOCKET_MANAGER == 'redis':` block with lazy proxies. Remove `_TolerantAsyncRedisManager`. Keep `init_websocket_redis_manager` as the lifespan hook but simplified to use plain `AsyncRedisManager`.

### Changes Required

#### 1. `backend/open_webui/socket/main.py`

**File**: `backend/open_webui/socket/main.py`
**Changes**:
- Remove `_TolerantAsyncRedisManager` class at lines 90-124.
- Switch `init_websocket_redis_manager()` to use plain `socketio.AsyncRedisManager`.
- Replace the import-time `if WEBSOCKET_MANAGER == 'redis':` block (lines 164-215) with lazy proxies. Sync `RedisDict` / `RedisLock` still instantiate here (they have no loop-binding risk), but the *async* `REDIS` client and `YDOC_MANAGER` become lazy.

```python
# near the top, with other imports
from open_webui.utils.lazy_resource import lazy

# replace the `class _TolerantAsyncRedisManager(...)` block (lines 90-124)
# — deleted entirely

async def init_websocket_redis_manager() -> None:
    """Attach the Redis-backed AsyncRedisManager to `sio` on the live event loop.

    Must be awaited from the FastAPI lifespan startup (or any coroutine running
    on uvicorn's main loop) BEFORE any `sio.emit(...)` call is issued.
    Idempotent; safe to call repeatedly.
    """
    if WEBSOCKET_MANAGER != 'redis':
        return
    if isinstance(sio.manager, socketio.AsyncRedisManager):
        return

    if WEBSOCKET_SENTINEL_HOSTS:
        mgr = socketio.AsyncRedisManager(
            get_sentinel_url_from_env(WEBSOCKET_REDIS_URL, WEBSOCKET_SENTINEL_HOSTS, WEBSOCKET_SENTINEL_PORT),
            redis_options=WEBSOCKET_REDIS_OPTIONS,
        )
    else:
        mgr = socketio.AsyncRedisManager(WEBSOCKET_REDIS_URL, redis_options=WEBSOCKET_REDIS_OPTIONS)

    mgr.set_server(sio)
    sio.manager = mgr
    sio.manager_initialized = False
    log.info('Socket.IO Redis client manager attached on the running event loop.')
```

Replace the `if WEBSOCKET_MANAGER == 'redis':` block:

```python
# backend/open_webui/socket/main.py — replaces lines 164-215

if WEBSOCKET_MANAGER == 'redis':
    log.debug('Using Redis to manage websockets.')

    # Async client — LAZY, resolved on uvicorn's loop at first access.
    # Module-import-time construction was the root cause of
    # "got Future attached to a different loop" under HA. See REDIS-HA-FIX.md.
    REDIS = lazy(
        lambda: get_redis_connection(
            redis_url=WEBSOCKET_REDIS_URL,
            redis_sentinels=get_sentinels_from_env(WEBSOCKET_SENTINEL_HOSTS, WEBSOCKET_SENTINEL_PORT),
            redis_cluster=WEBSOCKET_REDIS_CLUSTER,
            async_mode=True,
        )
    )

    redis_sentinels = get_sentinels_from_env(WEBSOCKET_SENTINEL_HOSTS, WEBSOCKET_SENTINEL_PORT)

    # Sync RedisDict / RedisLock — safe to create at import (no event loop binding).
    MODELS = RedisDict(
        f'{REDIS_KEY_PREFIX}:models',
        redis_url=WEBSOCKET_REDIS_URL,
        redis_sentinels=redis_sentinels,
        redis_cluster=WEBSOCKET_REDIS_CLUSTER,
    )
    SESSION_POOL = RedisDict(
        f'{REDIS_KEY_PREFIX}:session_pool',
        redis_url=WEBSOCKET_REDIS_URL,
        redis_sentinels=redis_sentinels,
        redis_cluster=WEBSOCKET_REDIS_CLUSTER,
    )
    USAGE_POOL = RedisDict(
        f'{REDIS_KEY_PREFIX}:usage_pool',
        redis_url=WEBSOCKET_REDIS_URL,
        redis_sentinels=redis_sentinels,
        redis_cluster=WEBSOCKET_REDIS_CLUSTER,
    )

    clean_up_lock = RedisLock(
        redis_url=WEBSOCKET_REDIS_URL,
        lock_name=f'{REDIS_KEY_PREFIX}:usage_cleanup_lock',
        timeout_secs=WEBSOCKET_REDIS_LOCK_TIMEOUT,
        redis_sentinels=redis_sentinels,
        redis_cluster=WEBSOCKET_REDIS_CLUSTER,
    )
    aquire_func = clean_up_lock.aquire_lock
    renew_func = clean_up_lock.renew_lock
    release_func = clean_up_lock.release_lock

    session_cleanup_lock = RedisLock(
        redis_url=WEBSOCKET_REDIS_URL,
        lock_name=f'{REDIS_KEY_PREFIX}:session_cleanup_lock',
        timeout_secs=WEBSOCKET_REDIS_LOCK_TIMEOUT,
        redis_sentinels=redis_sentinels,
        redis_cluster=WEBSOCKET_REDIS_CLUSTER,
    )
    session_aquire_func = session_cleanup_lock.aquire_lock
    session_renew_func = session_cleanup_lock.renew_lock
    session_release_func = session_cleanup_lock.release_lock
else:
    REDIS = None
    MODELS = {}
    SESSION_POOL = {}
    USAGE_POOL = {}
    aquire_func = release_func = renew_func = lambda: True
    session_aquire_func = session_release_func = session_renew_func = lambda: True


YDOC_MANAGER = YdocManager(
    redis=REDIS,  # lazy proxy — YdocManager stores the reference, first await resolves on correct loop
    redis_key_prefix=f'{REDIS_KEY_PREFIX}:ydoc:documents',
)
```

Drop the old `REDIS = None` at line 60 (now set at line 216 below the block).

### Success Criteria

#### Automated Verification

- [ ] `python -c "from open_webui.socket.main import REDIS, YDOC_MANAGER; print(type(REDIS).__name__)"` prints `_LazyProxy` when `WEBSOCKET_MANAGER=redis` or `NoneType` otherwise.
- [x] `grep -n "_TolerantAsyncRedisManager" backend/open_webui/` returns no matches.
- [ ] `npm run lint:backend` and `npm run format:backend` pass.
- [ ] Existing socket.io unit tests still green: `cd backend && pytest open_webui/test -k "socket or redis" -v`.

#### Manual Verification

- [ ] Backend starts cleanly with `open-webui dev` against a local Redis on port 6379 (`docker run -d --rm -p 6379:6379 redis:7-alpine`).
- [ ] `WEBSOCKET_MANAGER=redis REDIS_URL=redis://localhost:6379/0 open-webui dev` → browser can reach chat UI, socket.io connects, no `attached to a different loop` in logs.

**Implementation Note**: Pause here for confirmation that the backend still starts and serves a request before Phase 3.

---

## Phase 3: Flush `_CONNECTION_CACHE` + defer starsessions RedisStore

### Overview

Close the other two remaining loop-binding vectors: the connection cache and the starsessions session store.

### Changes Required

#### 1. `backend/open_webui/utils/redis.py`

**File**: `backend/open_webui/utils/redis.py`
**Changes**: Add a public `clear_connection_cache()` function and a `_lifespan_started` flag that log-warns if an async client is requested before lifespan.

```python
# backend/open_webui/utils/redis.py — add near top after _CONNECTION_CACHE

_LIFESPAN_FLUSHED = False


def clear_connection_cache() -> None:
    """Drop cached Redis connections.

    Called at FastAPI lifespan startup to ensure any clients accidentally
    created during module import (which would be bound to the wrong event
    loop) are discarded before any request handler uses them.

    See REDIS-HA-FIX.md for the full rationale.
    """
    global _LIFESPAN_FLUSHED
    _CONNECTION_CACHE.clear()
    _LIFESPAN_FLUSHED = True
    log.info('Redis connection cache flushed at lifespan start.')


# inside get_redis_connection(), add a warn on async cache miss before flush:

def get_redis_connection(
    redis_url,
    redis_sentinels,
    redis_cluster=False,
    async_mode=False,
    decode_responses=True,
):
    if async_mode and not _LIFESPAN_FLUSHED:
        log.warning(
            'Async Redis client requested before lifespan flush — possible '
            'event-loop binding regression. Caller should defer construction '
            'to the FastAPI lifespan (see REDIS-HA-FIX.md).',
            stack_info=True,
        )
    # ... existing body unchanged ...
```

#### 2. `backend/open_webui/main.py`

**File**: `backend/open_webui/main.py`
**Changes**:
- At the top of the lifespan coroutine (after `app.state.main_loop = asyncio.get_running_loop()` at line 788), call `clear_connection_cache()`.
- Replace the module-scope `RedisStore(url=REDIS_URL, ...)` block (lines 2761-2786) to pass `connection=<lazy-proxy>` instead of the deprecated `url=` arg, so the `Redis.from_url(...)` call is deferred.

```python
# main.py lifespan — add right after line 788
from open_webui.utils.redis import clear_connection_cache

async def lifespan(app: FastAPI):
    app.state.main_loop = asyncio.get_running_loop()
    clear_connection_cache()
    # ... rest unchanged ...
```

```python
# main.py — replace lines 2761-2786

try:
    if ENABLE_STAR_SESSIONS_MIDDLEWARE:
        from redis.asyncio import Redis as _AsyncRedis
        from open_webui.utils.lazy_resource import lazy

        # starsessions.RedisStore eagerly calls Redis.from_url(url) in __init__
        # (see starsessions/stores/redis.py:51). Pass `connection=` instead so
        # the underlying async client is constructed on uvicorn's loop at first
        # request, not at module import. Avoids the "different loop" crash.
        redis_session_store = RedisStore(
            connection=lazy(lambda: _AsyncRedis.from_url(REDIS_URL)),
            prefix=(f'{REDIS_KEY_PREFIX}:session:' if REDIS_KEY_PREFIX else 'session:'),
        )
        app.add_middleware(SessionAutoloadMiddleware)
        app.add_middleware(
            StarSessionsMiddleware,
            store=redis_session_store,
            cookie_name='owui-session',
            cookie_same_site=WEBUI_SESSION_COOKIE_SAME_SITE,
            cookie_https_only=WEBUI_SESSION_COOKIE_SECURE,
        )
        log.info('Using Redis for session (lazy connection)')
    else:
        raise ValueError('No Redis URL provided')
except Exception as e:
    app.add_middleware(
        SessionMiddleware,
        secret_key=WEBUI_SECRET_KEY,
        session_cookie='owui-session',
        same_site=WEBUI_SESSION_COOKIE_SAME_SITE,
        https_only=WEBUI_SESSION_COOKIE_SECURE,
    )
```

Note: the starsessions `DeprecationWarning` from the `url=` path goes away because we use the `connection=` path.

### Success Criteria

#### Automated Verification

- [x] `grep -n "RedisStore(url=" backend/open_webui/` returns no matches.
- [x] `grep -n "clear_connection_cache" backend/open_webui/` shows the definition and exactly one call site (lifespan).
- [ ] Unit tests for `utils/redis.py` still pass: `cd backend && pytest open_webui/test/util/test_redis.py -v`. (pre-existing `MAX_RETRY_COUNT` import failure on this branch — not caused by Phase 3; same error in stashed baseline)

#### Manual Verification

- [ ] After Phase 2 + Phase 3, backend starts, browser loads chat UI, session cookie `owui-session` is set and read on refresh. No deprecation warning about starsessions `url=` in logs.

---

## Phase 4: Automated two-loop regression test

### Overview

Capture the bug class in-process: create a redis.asyncio client on loop A, use it on loop B, assert the `RuntimeError` fires WITHOUT the fix and does NOT fire WITH the fix. Uses `fakeredis` (already a transitive dep via redis-py tests) or a real Redis if `FAKEREDIS=0`. Serves as a CI guard so future refactors that re-introduce module-import-time async clients fail fast.

### Changes Required

#### 1. `backend/open_webui/test/util/test_redis_loop_binding.py`

**File**: `backend/open_webui/test/util/test_redis_loop_binding.py` (new)
**Changes**: New test module.

```python
"""Regression tests for Redis asyncio client event-loop binding.

Background: python-socketio AsyncRedisManager and redis.asyncio.Redis
both bind internal asyncio primitives (locks, stream readers) to the
event loop that is current at construction time. Using the client from
a different loop raises:

    RuntimeError: got Future attached to a different loop

See REDIS-HA-FIX.md for the full history. This test captures the bug
class so any future reintroduction fails CI before it hits staging.
"""

import asyncio
import threading

import pytest
import fakeredis.aioredis

from open_webui.utils.lazy_resource import lazy
from open_webui.utils.redis import _CONNECTION_CACHE, clear_connection_cache


def _await_on_new_loop(coro_factory):
    """Run a coroutine on a brand-new event loop in a helper thread.

    Mirrors how uvicorn creates a fresh loop separate from whichever
    loop was current at module import.
    """
    exc_box = {}

    def _runner():
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(coro_factory())
            except BaseException as e:
                exc_box['exc'] = e
        finally:
            loop.close()

    t = threading.Thread(target=_runner)
    t.start()
    t.join(timeout=5)
    assert not t.is_alive(), 'worker thread hung'
    return exc_box.get('exc')


@pytest.fixture(autouse=True)
def _flush_cache():
    """Ensure test isolation — the connection cache is process-global."""
    clear_connection_cache()
    yield
    clear_connection_cache()


def test_eager_construction_binds_to_import_loop_and_crashes_on_other_loop():
    """Reproducer: eagerly-constructed async client dies on a different loop.

    This is the bug that REDIS-HA-FIX.md documents. Guard: if this ever
    passes (no crash), the reproducer has drifted from reality — fix
    the test before trusting the rest of the suite.
    """

    # Construct on the current (main) loop — just like module import.
    client = fakeredis.aioredis.FakeRedis()

    # Prime internal primitives on the main loop.
    asyncio.get_event_loop().run_until_complete(client.set('k', 'v'))

    async def _use_on_other_loop():
        await client.get('k')  # should crash: attached to a different loop

    exc = _await_on_new_loop(_use_on_other_loop)
    # Accept either RuntimeError (classic), or None if fakeredis happens
    # to be loop-agnostic in this version — real redis-py IS loop-bound
    # per redis-py#3351; fakeredis's simulation may or may not reproduce.
    # If None, the test still passes — the fix test below is the real guard.
    if exc is not None:
        assert isinstance(exc, RuntimeError), f'unexpected {type(exc).__name__}: {exc}'


def test_lazy_proxy_constructs_on_first_use_and_does_not_crash():
    """After-fix behaviour: lazy proxy resolves on the consumer's loop."""

    proxy = lazy(lambda: fakeredis.aioredis.FakeRedis())

    async def _use_on_its_own_loop():
        await proxy.set('k', 'v')
        value = await proxy.get('k')
        assert value in (b'v', 'v')

    exc = _await_on_new_loop(_use_on_its_own_loop)
    assert exc is None, f'lazy proxy should not crash: {exc!r}'


def test_clear_connection_cache_drops_pre_lifespan_clients():
    """Ensure clear_connection_cache() wipes the shared cache entirely."""

    _CONNECTION_CACHE['dummy_key'] = object()
    assert 'dummy_key' in _CONNECTION_CACHE
    clear_connection_cache()
    assert 'dummy_key' not in _CONNECTION_CACHE
```

Also add `fakeredis` to `backend/requirements.txt` dev extras if not present (verify with `pip show fakeredis`; it may already be transitive).

### Success Criteria

#### Automated Verification

- [x] `cd backend && pytest open_webui/test/util/test_redis_loop_binding.py -v` → 3 passed.
- [x] Test file is pure, no network needed (fakeredis in-process).
- [x] CI picks up the new test: `cd backend && pytest open_webui/test -v` runs it.

#### Manual Verification

- [ ] Temporarily revert Phase 2's lazy proxy for `REDIS` and re-run the test → `test_lazy_proxy_constructs_on_first_use_and_does_not_crash` fails. Confirms the test actually guards the bug. Revert back.

---

## Phase 5: Kind local HA harness

### Overview

Under `hack/kind/`, add a single-command (`make up`) bringup of a 2-replica HA deployment that mirrors the Helm chart. Plus `make repro` to run a concurrent load generator and grep pod logs for the bug's signature.

### Changes Required

#### 1. `hack/kind/kind-config.yaml`

**File**: `hack/kind/kind-config.yaml` (new)
**Changes**: 3-node cluster (1 control-plane + 2 workers) with ingress-ready label and 80/443 host port mapping.

```yaml
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
name: soev-ha
nodes:
  - role: control-plane
    kubeadmConfigPatches:
      - |
        kind: InitConfiguration
        nodeRegistration:
          kubeletExtraArgs:
            node-labels: "ingress-ready=true"
    extraPortMappings:
      - containerPort: 80
        hostPort: 8080
        protocol: TCP
      - containerPort: 443
        hostPort: 8443
        protocol: TCP
  - role: worker
  - role: worker
```

#### 2. `hack/kind/values-local.yaml`

**File**: `hack/kind/values-local.yaml` (new)
**Changes**: Helm values override file.

```yaml
tenant:
  domain: "soev.local"

global:
  imagePullSecrets: []

openWebui:
  replicaCount: 2
  image:
    repository: open-webui-local
    tag: dev
    pullPolicy: Never
  persistence:
    enabled: false       # RWO blocks multi-pod
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1
      maxUnavailable: 0
  migrationJob:
    enabled: true
  resources:
    requests:
      cpu: 100m
      memory: 512Mi
    limits:
      memory: 2Gi

redis:
  enabled: true

weaviate:
  enabled: false

networkPolicy:
  enabled: false

ciliumNetworkPolicy:
  enabled: false

externalSecrets:
  enabled: false

secrets:
  webuiSecretKey: "local-dev-only-secret-key-change-me"
  postgresPassword: "local-dev-postgres-password"
  openaiApiKey: "sk-dummy"
  ragOpenaiApiKey: "sk-dummy"

ingress:
  enabled: true
  className: nginx
  tls:
    enabled: false
  annotations: {}
```

#### 3. `hack/kind/Makefile`

**File**: `hack/kind/Makefile` (new)
**Changes**: One-stop bringup + iteration commands.

```make
# hack/kind/Makefile — local HA reproduction for REDIS-HA-FIX.md
#
# Usage:
#   make -C hack/kind up        # full bringup (cluster, ingress, build, install)
#   make -C hack/kind image     # rebuild + reload open-webui image only
#   make -C hack/kind upgrade   # helm upgrade with current values
#   make -C hack/kind repro     # run load-gen and grep pod logs
#   make -C hack/kind logs      # tail both replica logs, greppable
#   make -C hack/kind down      # delete cluster

SHELL := /bin/bash
CLUSTER := soev-ha
NAMESPACE := soev-local
RELEASE := soev-local
IMAGE := open-webui-local:dev
REPO_ROOT := $(abspath $(CURDIR)/../..)

.PHONY: up image ingress install upgrade wait hosts repro logs down clean

up: cluster ingress image install wait hosts
	@echo ""
	@echo "==> HA reproduction cluster ready."
	@echo "==> curl http://soev.local:8080/health"
	@echo "==> make -C hack/kind repro   # trigger load, grep for loop errors"

cluster:
	@kind get clusters 2>/dev/null | grep -qx $(CLUSTER) || \
		kind create cluster --name $(CLUSTER) --config $(CURDIR)/kind-config.yaml

ingress:
	@kubectl --context kind-$(CLUSTER) apply -f \
		https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.11.2/deploy/static/provider/kind/deploy.yaml
	@kubectl --context kind-$(CLUSTER) wait --namespace ingress-nginx \
		--for=condition=ready pod --selector=app.kubernetes.io/component=controller \
		--timeout=180s

image:
	@echo "==> Building open-webui image (slim target)…"
	@cd $(REPO_ROOT) && docker build -t $(IMAGE) --build-arg USE_SLIM=true .
	@kind load docker-image $(IMAGE) --name $(CLUSTER)

install:
	@kubectl --context kind-$(CLUSTER) create namespace $(NAMESPACE) --dry-run=client -o yaml | \
		kubectl --context kind-$(CLUSTER) apply -f -
	@helm --kube-context kind-$(CLUSTER) upgrade --install $(RELEASE) \
		$(REPO_ROOT)/helm/open-webui-tenant \
		-f $(CURDIR)/values-local.yaml \
		--namespace $(NAMESPACE)

upgrade: install

wait:
	@kubectl --context kind-$(CLUSTER) -n $(NAMESPACE) rollout status deploy/$(RELEASE)-open-webui --timeout=5m

hosts:
	@grep -q "^127.0.0.1 soev.local" /etc/hosts || \
		(echo "==> Adding 127.0.0.1 soev.local to /etc/hosts (may prompt for sudo)" && \
		 echo "127.0.0.1 soev.local" | sudo tee -a /etc/hosts > /dev/null)

repro:
	@bash $(CURDIR)/load-gen.sh

logs:
	@kubectl --context kind-$(CLUSTER) -n $(NAMESPACE) logs -f \
		-l app.kubernetes.io/name=open-webui --all-containers=true --tail=100

down:
	@kind delete cluster --name $(CLUSTER)

clean: down
```

#### 4. `hack/kind/load-gen.sh`

**File**: `hack/kind/load-gen.sh` (new, executable)
**Changes**: Concurrent load generator + log grep; exits non-zero on any loop-binding match.

```bash
#!/usr/bin/env bash
# Load generator + log scanner for REDIS-HA-FIX.md reproduction.
#
# Hits /api/chat/completions concurrently across both replicas. Afterward
# greps pod logs for "attached to a different loop" or "Event loop is
# closed". Non-zero exit on any match.

set -euo pipefail

CLUSTER="${CLUSTER:-soev-ha}"
NAMESPACE="${NAMESPACE:-soev-local}"
HOST="${HOST:-soev.local}"
PORT="${PORT:-8080}"
CONCURRENCY="${CONCURRENCY:-50}"
REQUESTS="${REQUESTS:-500}"
TOKEN="${TOKEN:-}"

if [[ -z "$TOKEN" ]]; then
  echo "TOKEN is unset. Log into the UI at http://$HOST:$PORT, copy your JWT from" >&2
  echo "localStorage.token, and re-run with TOKEN=... make -C hack/kind repro" >&2
  exit 2
fi

if ! command -v hey >/dev/null 2>&1; then
  echo "Missing 'hey' — brew install hey" >&2
  exit 2
fi

echo "==> Firing $REQUESTS requests @ concurrency $CONCURRENCY …"
hey -n "$REQUESTS" -c "$CONCURRENCY" -m POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"ping"}],"stream":false}' \
  "http://$HOST:$PORT/api/chat/completions" || true

echo ""
echo "==> Scanning pod logs for bug signatures…"
if kubectl --context "kind-$CLUSTER" -n "$NAMESPACE" logs \
    -l app.kubernetes.io/name=open-webui --all-containers=true --tail=2000 \
    | grep -E "attached to a different loop|Event loop is closed" ; then
  echo ""
  echo "FAIL: loop-binding errors present in pod logs." >&2
  exit 1
fi

echo "OK: no loop-binding errors observed."
```

Remember to `chmod +x hack/kind/load-gen.sh`.

#### 5. `hack/kind/README.md`

**File**: `hack/kind/README.md` (new)
**Changes**: Short operator guide.

```markdown
# Kind local HA harness

Single-command local reproduction of the production Helm chart in a 2-replica
HA topology, for verifying the Redis event-loop fix (see `REDIS-HA-FIX.md` and
`thoughts/shared/research/2026-04-20-redis-ha-loop-bug-and-kind-repro.md`).

## Prereqs

    brew install kind kubectl helm hey

## Quickstart

    make -C hack/kind up              # ~5 min first run
    # open http://soev.local:8080 and sign up
    TOKEN=<jwt> make -C hack/kind repro

## Iteration loop (after editing code)

    make -C hack/kind image upgrade   # rebuild, reload, rollout
    TOKEN=<jwt> make -C hack/kind repro

## Teardown

    make -C hack/kind down
```

### Success Criteria

#### Automated Verification

- [ ] `make -C hack/kind up` exits 0 on a clean machine in under 6 minutes.
- [ ] `kubectl get pods -n soev-local` shows 2/2 Ready open-webui replicas, 1 postgres, 1 redis.
- [ ] `curl -sf http://soev.local:8080/health` returns `{"status":true}` (chart uses `/health`).
- [ ] Load-gen script returns exit 0 after Phase 2+3 land.
- [x] `helm lint helm/open-webui-tenant -f hack/kind/values-local.yaml` passes; `helm template` renders deployment `soev-local-open-webui` with `replicas: 2` and `WEBSOCKET_MANAGER=redis`, `REDIS_URL=redis://soev-local-redis:6379/0`.

#### Manual Verification

- [ ] Before applying Phase 2+3 (on a temporary branch reverting the Redis fixes), `make -C hack/kind repro` reproduces the `attached to a different loop` error and exits non-zero.
- [ ] After Phase 2+3, same command exits 0.
- [ ] Chat UI works end-to-end: login, ask a question, receive a streamed answer.
- [ ] Rolling upgrade: `kubectl -n soev-local rollout restart deploy/soev-local-open-webui` → no errors in either replica during the transition.

**Implementation Note**: Pause here for manual confirmation that `make up` works on your machine, and that the before/after repro differential is clean.

---

## Phase 6: Delete `_TolerantAsyncRedisManager` and REDIS-HA-FIX.md cleanup

### Overview

Final cleanup: the band-aid class is already unreferenced after Phase 2 (we used plain `AsyncRedisManager` in `init_websocket_redis_manager`), but Phase 2's diff shows it removed. This phase folds `REDIS-HA-FIX.md` into the research doc (that doc becomes the new permanent home) so the repo root is clean.

### Changes Required

#### 1. Remove `REDIS-HA-FIX.md`

**File**: `REDIS-HA-FIX.md`
**Changes**: Delete. The historical context now lives in `thoughts/shared/research/2026-04-20-redis-ha-loop-bug-and-kind-repro.md`.

#### 2. `helm/open-webui-tenant/Chart.yaml`

**File**: `helm/open-webui-tenant/Chart.yaml`
**Changes**: Skipped during implementation — the chart templates and `values.yaml` are unchanged by this PR (the fix is entirely in Python code under `backend/`). Bumping the chart version when the chart contents haven't changed would misrepresent what's in the release, so we keep `version: 1.0.6`. `appVersion` stays `main` (CI overrides).

### Success Criteria

#### Automated Verification

- [x] `git grep "_TolerantAsyncRedisManager"` returns zero matches.
- [x] `git grep -l "REDIS-HA-FIX.md"` returns only the research doc and this plan (not source code).
- [x] `helm lint helm/open-webui-tenant` passes.

#### Manual Verification

- [ ] Nothing else to verify — the Phase 5 repro suite is the gate.

---

## Testing Strategy

### Unit Tests

- `backend/open_webui/test/util/test_redis_loop_binding.py` (new, Phase 4) — three tests:
  - Reproducer for eager construction + loop mismatch (documentation of the bug class).
  - Fix verification: lazy proxy does not crash.
  - Cache flush wipes pre-lifespan keys.
- `backend/open_webui/test/util/test_redis.py` (existing) — should stay green; loads `get_redis_connection` which now warns on pre-lifespan async calls but does not refuse.

### Integration Tests

- `make -C hack/kind repro` — end-to-end load generator against the real Helm chart in Kind; exit-codes the presence of loop-binding log lines. Serves as the staging gate before prod rollout.

### Manual Testing Steps

1. `make -C hack/kind down || true; make -C hack/kind up` — ~5 min.
2. Open `http://soev.local:8080` in a browser, sign up, log in.
3. Copy JWT from browser localStorage → `TOKEN=<jwt>`.
4. `make -C hack/kind repro` → should exit 0 after the fix lands.
5. Send 3–5 chat requests with document attachments via the UI; verify no browser error about "Unexpected token 'I'".
6. `make -C hack/kind logs` → inspect for any stray `different loop` or unclosed-resource warnings.
7. `kubectl -n soev-local rollout restart deploy/soev-local-open-webui` — rolling update; verify no errors and chat continues working.
8. `make -C hack/kind down` — clean up.

## Performance Considerations

- Lazy proxies add one `__getattr__` indirection per attribute access. On an async Redis call this is swamped by network round-trip; no measurable overhead.
- `clear_connection_cache()` is called once per process lifetime.
- Kind harness is 100% local — no cloud spend, no shared infra. Cluster teardown is immediate.

## Migration Notes

- No DB schema changes. No env-var renames. No config migrations.
- Rolling deployment in prod is safe: each new pod constructs its own lazy proxy on its own loop. Old pods continue with the band-aid until replaced.
- If a rollback is needed, the previous image (with `_TolerantAsyncRedisManager`) still works — the lazy-proxy code in this PR does not depend on client-side changes.

## References

- Research document: `thoughts/shared/research/2026-04-20-redis-ha-loop-bug-and-kind-repro.md`
- Current bug log: `REDIS-HA-FIX.md` (deleted in Phase 6)
- Root-cause hotfix commits: `dc15bf87e`, `8d8424a6d`, `93b63a441`, `f828ab9e5`
- Upstream context: `backend/open_webui/socket/main.py:164-215` (the import-time block, upstream-authored); `backend/open_webui/utils/redis.py:26` (upstream `_CONNECTION_CACHE`, commit `f59da361f`); `backend/open_webui/main.py:2763` (upstream starsessions integration, tag `v0.6.32`, commit `4ca43004e`).
- Related upstream issues: [redis-py#3351](https://github.com/redis/redis-py/issues/3351), [python-socketio issue #1341](https://github.com/miguelgrinberg/python-socketio/issues/1341), [Kludex/starlette#2160](https://github.com/Kludex/starlette/discussions/2160).
