---
date: 2026-04-20T20:40:00+02:00
researcher: Lex Lubbers
git_commit: f828ab9e552c402c30361ce50624edeb0478e894
branch: fix/ha-error-local-k8s
repository: Gradient-DS/open-webui
topic: 'Redis HA "Future attached to a different loop" — root-cause follow-up and Kind reproduction harness'
tags: [research, codebase, redis, websocket, socket.io, asyncio, helm, kind, HA, middleware]
status: complete
last_updated: 2026-04-20
last_updated_by: Lex Lubbers
---

# Research: Redis HA "Future attached to a different loop" — root-cause follow-up and Kind reproduction harness

**Date**: 2026-04-20T20:40:00+02:00
**Researcher**: Lex Lubbers
**Git Commit**: f828ab9e552c402c30361ce50624edeb0478e894
**Branch**: fix/ha-error-local-k8s
**Repository**: Gradient-DS/open-webui

## Research Question

On `main` we landed four hotfixes for an HA-mode crash producing `RuntimeError: got Future attached to a different loop` / `Event loop is closed`, culminating in a `_TolerantAsyncRedisManager` that swallows the error. The final fix is a band-aid — it does not address why the error is still raised. We want to:

1. Understand why the error still fires after the lifespan-deferred `AsyncRedisManager` fix, and identify the remaining loop-binding sources.
2. Design a local Kubernetes reproduction (using Kind) that mirrors the Helm chart topology so we can iterate on a real fix without deploying to a cluster.

## Summary

**The band-aid is necessary today because iteration 2 deferred only one of three (probably four) module-import-time async Redis clients.** The missed ones are:

1. `backend/open_webui/socket/main.py:166` — `REDIS = get_redis_connection(..., async_mode=True)` at module import, shared by `YdocManager`, `stop_item_tasks`, `create_task`.
2. The **`_CONNECTION_CACHE`** at `backend/open_webui/utils/redis.py:26` causes the lifespan call at `main.py:818` to return the **same already-bound client** as the import-time call, because `WEBSOCKET_REDIS_URL` defaults to `REDIS_URL` (`env.py:678`). So "lifespan-deferred `app.state.redis`" is a no-op in practice.
3. `backend/open_webui/main.py:2763` — `starsessions.stores.redis.RedisStore(url=REDIS_URL, ...)` constructed at module import. Less conclusive (starsessions lazy-initializes, but still a candidate).

**The real fix is structural, not a version bump.** Upstream python-socketio 5.16.1 (latest, 2026-02-06) and any redis-py 5.x/6.x still bind async primitives to the event loop at construction time — confirmed by [redis-py #3351](https://github.com/redis/redis-py/issues/3351), [#3431](https://github.com/redis/redis-py/issues/3431), [#3492](https://github.com/redis/redis-py/issues/3492). Upstream Open WebUI has **not** fixed this; their `main` still does the same import-time wiring we did before iteration 2.

**The HA-only manifestation** is explained by traffic shape: at `replicaCount=1` the socket.io pubsub subscribe task barely fires; at `replicaCount=2` the listener is continuously reading, so concurrent publish/subscribe on a loop-mismatched pool exposes the race reliably. The same client, single-replica, usually survives.

**Kind harness is feasible and small.** The chart deploys cleanly on Kind with five values overrides (disable RWO PVC, `weaviate.enabled=false`, `networkPolicy.enabled=false`, `externalSecrets.enabled=false`, `ingress` with `className: nginx`). Weaviate, network policies, cert-manager, and External Secrets are all off-path for this bug. Four-step repro harness described in §4.

## Detailed Findings

### 1. What the four hotfixes actually did

In order (oldest → newest):

| Commit | Scope | What changed |
|---|---|---|
| `dc15bf87e` "hotfix: fixed the redis awsgi topology" | Middleware architecture | Converted every `BaseHTTPMiddleware` / `@app.middleware('http')` in the app to pure ASGI-3 classes: `RedirectMiddleware`, `SecurityHeadersMiddleware`, `CommitSessionMiddleware`, `AuthTokenMiddleware`, `WebSocketGuardMiddleware`. This removes the sub-task-group layer that forced async Redis I/O onto a different anyio-owned loop accessor. |
| `8d8424a6d` "hotfix: final redis HA fix" | Telemetry middleware | Converted the `_metrics_middleware` (OTel) in `utils/telemetry/metrics.py` to a pure ASGI-3 `MetricsMiddleware` class. This was the last `@app.middleware('http')` decorator. |
| `93b63a441` "hotfix: redis async event loop" | Socket.IO manager | Deferred `AsyncRedisManager` construction to a lifespan hook (`init_websocket_redis_manager()`), and hot-swaps `sio.manager` with `sio.manager_initialized=False` so the subscribe task re-spawns on uvicorn's loop. |
| `f828ab9e5` "hotfix: swallowed the error temp" | Band-aid | Subclassed `AsyncRedisManager` → `_TolerantAsyncRedisManager` that swallows `RuntimeError` when its message contains `"attached to a different loop"` or `"Event loop is closed"`, in `_publish` only. |

All four are in the current tree (`REDIS-HA-FIX.md` captures them with rationale).

### 2. Current middleware inventory (verified)

Zero remaining `BaseHTTPMiddleware` subclasses and zero remaining `@app.middleware('http')` decorators — confirmed by grep. Every middleware is either pure ASGI-3 (ours) or a third-party already-pure middleware (Starlette CORS/Compress, starsessions).

Call order (outer → inner, LIFO of `add_middleware`):

| # | Name | File:line |
|---|---|---|
| 1 | `AuditLoggingMiddleware` | `utils/audit.py:115` (pre-existing pure ASGI, confirmed) |
| 2 | `SessionAutoloadMiddleware` + `StarSessionsMiddleware` (or fallback `SessionMiddleware`) | `main.py:2768-2786` |
| 3 | `CORSMiddleware` | `main.py:1848-1854` |
| 4 | `CommitSessionMiddleware` | `main.py:1751, added 1846` |
| 5 | `AuthTokenMiddleware` | `main.py:1776, added 1845` |
| 6 | `WebSocketGuardMiddleware` | `main.py:1818, added 1844` |
| 7 | `APIKeyRestrictionMiddleware` | `main.py:1706, added 1748` |
| 8 | `SecurityHeadersMiddleware` | `utils/security_headers.py:7, added main.py:1703` |
| 9 | `RedirectMiddleware` | `main.py:1661, added 1702` |
| 10 | `CompressMiddleware` (conditional) | `main.py:1658` |
| 11 | `MetricsMiddleware` (conditional on OTel) | `utils/telemetry/metrics.py:179` |

**Iteration-1 work is clean and permanent.** Even if the loop-binding root cause is fixed elsewhere, this migration is correct: BaseHTTPMiddleware is still not deprecated (see [Kludex/starlette#2160](https://github.com/Kludex/starlette/discussions/2160)) but the upstream recommendation for anything touching async resources remains "write pure ASGI".

### 3. Why the band-aid still fires — remaining loop-binding sources

All three candidates below are constructed at **module-import time** — before uvicorn creates its serving loop — and hold long-lived async state. Every one of them is a potential `RuntimeError: got Future attached to a different loop`.

#### 3a. `socket/main.py:166-171` — module-import async Redis client

```python
# backend/open_webui/socket/main.py:166
if WEBSOCKET_MANAGER == 'redis':
    REDIS = get_redis_connection(
        redis_url=WEBSOCKET_REDIS_URL,
        redis_sentinels=get_sentinels_from_env(...),
        redis_cluster=WEBSOCKET_REDIS_CLUSTER,
        async_mode=True,   # ← async client built at import
    )
```

- This client is shared by `YDOC_MANAGER` (`socket/main.py:227`), `stop_item_tasks(REDIS, ...)` (`:729`), `create_task(REDIS, ...)` (`:764`).
- `YdocManager._redis.*` is awaited from `socket/utils.py:141-263` — called from every `ydoc:*` socket event.
- This was not deferred by iteration 2 (only `AsyncRedisManager` was).

#### 3b. `_CONNECTION_CACHE` shares the client across import and lifespan

```python
# backend/open_webui/utils/redis.py:26, 189-190
_CONNECTION_CACHE = {}
...
if cache_key in _CONNECTION_CACHE:
    return _CONNECTION_CACHE[cache_key]
```

The cache key is `(redis_url, tuple(redis_sentinels), async_mode, decode_responses)`.

- `env.py:678`: `WEBSOCKET_REDIS_URL = os.environ.get('WEBSOCKET_REDIS_URL', REDIS_URL)` — defaults to the app's main Redis URL.
- In the Helm chart, `configmap.yaml:371-375` sets only `REDIS_URL` (no separate `WEBSOCKET_REDIS_URL`). So the two URLs are identical.
- `socket/main.py:166` runs at import and caches the client with key `(REDIS_URL, (), True, True)`.
- `main.py:818` lifespan calls `get_redis_connection(..., async_mode=True)` with the same args → **cache hit → returns the same already-loop-bound object as `app.state.redis`**.

This is the critical finding. The lifespan-deferred `app.state.redis` is **not actually fresh** — it's the stale import-time client wearing a different name. Every `await request.app.state.redis.get(...)` in `utils/auth.py:229`, `utils/tools.py:807-953`, `tasks.py:26`, and `main.py:3046-3049` awaits a Future owned by the wrong loop.

#### 3c. `starsessions.RedisStore` at `main.py:2763`

```python
# backend/open_webui/main.py:2763
if ENABLE_STAR_SESSIONS_MIDDLEWARE:
    redis_session_store = RedisStore(
        url=REDIS_URL,
        prefix=...,
    )
    app.add_middleware(StarSessionsMiddleware, store=redis_session_store, ...)
```

- Built at module import, not in lifespan.
- `starsessions[redis]==2.2.1` internally uses `redis.asyncio`. Per source inspection, `RedisStore.__init__` lazily constructs the client on first use (first request) rather than at `__init__`. This *probably* means it binds to uvicorn's loop because the first session read happens after startup. Less certain than 3a/3b — worth verifying empirically during Kind reproduction.

#### 3d. `AppConfig._redis` (sync) at `config.py:232`

- Sync client, not async — cannot cause "Future attached to a different loop".
- But it does synchronous Redis I/O inside async request handlers on every `app.state.config.X` access when `ENABLE_PERSISTENT_CONFIG=true` (our chart default: `false`, `values.yaml:167` → `configmap.yaml:11`). So not in scope for the HA tenants we run. Flag for future consideration.

### 4. Why HA (replicaCount>=2) makes it deterministic

`AsyncRedisManager._publish()` is the Redis pubsub `PUBLISH` — cross-replica broadcast of socket.io events. At `replicaCount=1`:

- The server still publishes, but there are no other subscribers consuming the channel from a different pod.
- The subscribe listener (`AsyncPubSubManager._thread()`) is always running in some pod, but with no cross-pod traffic its read volume is low.
- Concurrent publish + read collisions on a loop-mismatched client happen rarely; most hit a benign path.

At `replicaCount=2`:

- Every pod's subscriber is continuously reading the pubsub from the OTHER pod's publishes (auth events, socket.io broadcasts).
- Under chat load, publishes and reads both fire from request handlers on the serving loop, racing on the same loop-bound primitives — the mismatch surfaces almost immediately.
- Document uploads and chat reconnections pile many calls through the Redis pool at once (auth revocation check + session read + websocket reconnect + sio.emit status), multiplying collisions.

Matches `REDIS-HA-FIX.md:11-12` ("With `replicaCount: 1` the error disappears or becomes very rare. Errors only occur with a document attached").

### 5. Pinned dependency versions

From `backend/requirements.txt`, `backend/requirements-slim.txt`, `pyproject.toml`:

| Package | Version |
|---|---|
| `fastapi` | 0.135.1 |
| `uvicorn[standard]` | 0.41.0 |
| `starlette` | transitive (from fastapi) |
| `python-socketio` | 5.16.1 (latest as of 2026-02-06) |
| `redis` | 7.4.0 (stable, has asyncio submodule) |
| `aioredis` | — (not used; `redis.asyncio` replaces it) |
| `starlette-compress` | 1.7.0 |
| `starsessions[redis]` | 2.2.1 |
| `asgiref` | 3.11.1 |

Per [python-socketio CHANGES.md](https://github.com/miguelgrinberg/python-socketio/blob/main/CHANGES.md), no 5.11–5.16 release fixes loop-binding in `AsyncRedisManager`. Per [redis-py #3351](https://github.com/redis/redis-py/issues/3351) (opened 2024, still open in 2026), no redis-py version fixes module-level async client loop binding — it is a fundamental behavior, not a bug.

### 6. Upstream Open WebUI state

- [`open-webui/open-webui`'s `backend/open_webui/socket/main.py`](https://github.com/open-webui/open-webui/blob/main/backend/open_webui/socket/main.py) on `main` (as of 2026-04) **still constructs `AsyncRedisManager` at module import** and **still calls `get_redis_connection(..., async_mode=True)` at import**. No lifespan deferral, no tolerant subclass.
- No open PR addresses the same bug class. Closest related: [PR #7780](https://github.com/open-webui/open-webui/pull/7780) (HA polling transport + cleanup lock — symptom-adjacent).
- Symptom-adjacent issues: [#8134](https://github.com/open-webui/open-webui/issues/8134), [#15162](https://github.com/open-webui/open-webui/issues/15162), [#16157](https://github.com/open-webui/open-webui/issues/16157). None maps cleanly to our traceback.
- **Our iteration-1 (pure-ASGI) and iteration-2 (deferred manager) work is ahead of upstream.** A targeted upstream PR for both is feasible and aligns with our "additive over modifying" posture.

### 7. Proposed real fix

Layered, minimal-surface. Each layer is independently testable in Kind.

#### Layer A — Defer **all** module-import async Redis client construction to lifespan

Move every `get_redis_connection(..., async_mode=True)` call out of module scope. Concretely:

1. **`socket/main.py`** — remove the top-level `if WEBSOCKET_MANAGER == 'redis':` block at `:164-215`. Convert `REDIS`, `MODELS`, `SESSION_POOL`, `USAGE_POOL`, `clean_up_lock`, `session_cleanup_lock`, `YDOC_MANAGER` to lazy singletons initialized inside `init_websocket_redis_manager()` (rename it to `init_websocket_state()`). The singletons should be accessed through getter functions so call sites don't break.
   - Sync `RedisDict` / `RedisLock` (no `async_mode=True`) don't need deferral for the loop-binding reason — but co-locating them keeps the wiring coherent.
2. **`utils/redis.py`** — clear `_CONNECTION_CACHE` at the start of lifespan, OR change the cache key to include a "generation" counter bumped on startup, OR remove the cache entirely and let callers hold their own reference. Removing is safest — the cache exists to dedupe `get_redis_connection` calls, but in practice we call it once per use-case with distinct args.
3. **`main.py:818`** — call `init_websocket_state()` before `app.state.redis = get_redis_connection(...)`. Both go inside the lifespan.
4. **`main.py:2763`** — if `starsessions.RedisStore` is confirmed to eagerly construct the client, move this block into lifespan too (store on `app.state.redis_session_store` and read it inside a `StarSessionsMiddleware` wrapper). If it's lazy, leave it.

#### Layer B — Remove `_TolerantAsyncRedisManager`

Only delete after Layer A is verified in Kind (grep pod logs for 10-minute load run showing zero "different loop" / "Event loop is closed" messages). Until then it stays as a safety net.

#### Layer C — Upstream the fix

Prepare a PR against `open-webui/open-webui` with Layer A + the iteration-1 pure-ASGI middleware migration. This reduces our local drift, benefits the community, and — importantly — means future upstream merges are less painful.

#### Why not alternatives

- **Bump python-socketio / redis-py**: verified not helpful, see §5–6.
- **Swap `AsyncRedisManager` for Kombu / aio-pika**: would work but is a much larger behavior change for a non-Redis pubsub, requires a second broker in ops.
- **Sticky-session the ingress so replicas don't cross-publish**: defeats the point of HA; users pin to dead pods on rolling updates.
- **Disable the websocket redis manager**: breaks real-time updates across replicas — real-time is a core UX promise for chat. Already ruled out in iteration-1 rationale.

## Kind local reproduction harness

The chart deploys cleanly to Kind with five values overrides. No modifications to the chart templates needed.

### 8. Prerequisites (Homebrew)

```bash
brew install kind kubectl helm
# optional load testing:
brew install hey    # or vegeta
```

### 9. Kind cluster config

`hack/kind/kind-config.yaml` (suggested new path):

```yaml
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
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

Two workers let `topologySpreadConstraints` place the two open-webui replicas on different nodes — closer to prod. Host port `8080:80` avoids sudo.

### 10. Local values override

`hack/kind/values-local.yaml`:

```yaml
tenant:
  domain: "soev.local"

global:
  imagePullSecrets: []   # local image, no registry auth

openWebui:
  replicaCount: 2
  image:
    repository: open-webui-local
    tag: dev
    pullPolicy: Never    # use kind-loaded local image
  persistence:
    enabled: false       # RWO PVC blocks multi-pod
  strategy:
    type: RollingUpdate
  migrationJob:
    enabled: true

redis:
  enabled: true

weaviate:
  enabled: false         # off-path for this bug

networkPolicy:
  enabled: false         # kindnet doesn't enforce anyway

ciliumNetworkPolicy:
  enabled: false

externalSecrets:
  enabled: false

secrets:
  webuiSecretKey: "local-dev-only-secret"
  postgresPassword: "local-dev-postgres-pw"
  openaiApiKey: "sk-dummy"
  ragOpenaiApiKey: "sk-dummy"

ingress:
  enabled: true
  className: nginx
  tls:
    enabled: false
  annotations: {}        # strip cert-manager annotation

# Ensure REDIS_URL is set (chart derives from redis.enabled)
```

### 11. Reproduction workflow

```bash
# 1. Create cluster
kind create cluster --name soev-ha --config hack/kind/kind-config.yaml

# 2. Install ingress-nginx (port-mapped 80/443 from cluster config)
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.11.2/deploy/static/provider/kind/deploy.yaml
kubectl wait --namespace ingress-nginx \
  --for=condition=ready pod --selector=app.kubernetes.io/component=controller \
  --timeout=180s

# 3. Build & load image (use slim target to match prod)
docker build -f Dockerfile -t open-webui-local:dev --build-arg USE_SLIM=true .
kind load docker-image open-webui-local:dev --name soev-ha

# 4. Install chart
helm install soev-local helm/open-webui-tenant \
  -f hack/kind/values-local.yaml \
  --namespace soev-local --create-namespace

# 5. Add host entry so ingress routes work
echo "127.0.0.1 soev.local" | sudo tee -a /etc/hosts

# 6. Wait for pods
kubectl -n soev-local wait --for=condition=ready pod \
  -l app.kubernetes.io/name=open-webui --timeout=5m
```

### 12. Reproducing the bug

The symptom requires concurrent authenticated `POST /api/chat/completions` with a document attached. Minimal steps:

```bash
# Get a JWT by logging in once (UI or curl) — save it to $TOKEN
TOKEN="..."

# Hammer the endpoint with 50 concurrent requests
hey -n 500 -c 50 -m POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"hi"}],"stream":false}' \
  http://soev.local:8080/api/chat/completions

# Tail both replicas' logs; grep for the signature
kubectl -n soev-local logs -f -l app.kubernetes.io/name=open-webui --all-containers=true \
  | grep -E "different loop|Event loop is closed|Internal Server Error"
```

A document-attachment variant (more faithful to the prod trigger) needs a multipart upload first to get a `file_id`, then a chat request referencing it. Can be scripted as a Python `asyncio.gather` load-gen once we have a test user.

### 13. Iterating on Layer A

With `pullPolicy: Never` and local image loading, the fix loop is:

```bash
# Edit socket/main.py / main.py / utils/redis.py
docker build -f Dockerfile -t open-webui-local:dev --build-arg USE_SLIM=true .
kind load docker-image open-webui-local:dev --name soev-ha
kubectl -n soev-local rollout restart deploy/soev-local-open-webui
kubectl -n soev-local rollout status deploy/soev-local-open-webui --timeout=3m

# Re-run the load generator, grep logs
```

~2-3 minute feedback cycle per iteration, no cluster access needed.

## Code References

- `REDIS-HA-FIX.md` — three-iteration bug log (§ Symptom / Root cause / Pure ASGI / Second root cause / Third iteration).
- `backend/open_webui/socket/main.py:77-87` — `sio` with default in-memory AsyncManager at import.
- `backend/open_webui/socket/main.py:90-124` — `_TolerantAsyncRedisManager` (the band-aid).
- `backend/open_webui/socket/main.py:127-155` — `init_websocket_redis_manager()` lifespan hook.
- `backend/open_webui/socket/main.py:164-215` — **module-import-time `REDIS`, `MODELS`, `SESSION_POOL`, `USAGE_POOL`, cleanup locks** (the missed deferral).
- `backend/open_webui/socket/main.py:226-229` — `YDOC_MANAGER` wired to import-time `REDIS`.
- `backend/open_webui/utils/redis.py:26` — `_CONNECTION_CACHE` dict.
- `backend/open_webui/utils/redis.py:189-190` — cache hit returns stale client.
- `backend/open_webui/env.py:678` — `WEBSOCKET_REDIS_URL` defaults to `REDIS_URL` (causes cache collision in practice).
- `backend/open_webui/main.py:788` — `app.state.main_loop = asyncio.get_running_loop()` (the codebase already knows loops differ).
- `backend/open_webui/main.py:818-823` — lifespan `app.state.redis = get_redis_connection(...)` — defeated by the cache.
- `backend/open_webui/main.py:826` — `redis_task_command_listener(app)` long-running pubsub on `app.state.redis`.
- `backend/open_webui/main.py:833-838` — `await init_websocket_redis_manager()`.
- `backend/open_webui/main.py:2763-2766` — `starsessions.RedisStore(url=REDIS_URL, ...)` (candidate 3c).
- `backend/open_webui/utils/audit.py:115-180` — pure ASGI-3 `AuditLoggingMiddleware` (pre-existing, correct).
- `backend/open_webui/utils/security_headers.py:7-37` — converted pure ASGI-3.
- `backend/open_webui/utils/telemetry/metrics.py:179-217` — converted pure ASGI-3 (conditional on OTel).
- `backend/open_webui/main.py:1661-1702` — `RedirectMiddleware` (converted).
- `backend/open_webui/main.py:1751` — `CommitSessionMiddleware` (converted).
- `backend/open_webui/main.py:1776` — `AuthTokenMiddleware` (converted).
- `backend/open_webui/main.py:1818` — `WebSocketGuardMiddleware` (converted).
- `helm/open-webui-tenant/values.yaml:62` — `openWebui.replicaCount` (defaults 1; set 2 for HA repro).
- `helm/open-webui-tenant/values.yaml:614-619` — `redis.enabled` (default `false`, must be `true`).
- `helm/open-webui-tenant/templates/open-webui/configmap.yaml:371-375` — `REDIS_URL`, `WEBSOCKET_MANAGER=redis`, `ENABLE_WEBSOCKET_SUPPORT=true` (gated on `redis.enabled`).
- `helm/open-webui-tenant/templates/open-webui/deployment.yaml:34-54` — init containers waiting for weaviate / redis.
- `helm/open-webui-tenant/templates/open-webui/migration-job.yaml:7-11` — pre-upgrade migration hook (required when `replicaCount>1`).
- `helm/open-webui-tenant/templates/ingress.yaml:1-33` — ingress; no websocket-specific annotations, no sticky sessions.

## Architecture Insights

- **"Lifespan-deferred" is a leaky abstraction when a connection cache exists.** Iteration 2 deferred construction, but `_CONNECTION_CACHE` undid the effect because the cache spans module-import and lifespan. Any lifespan deferral pattern should pair with a cache flush or a generation-tagged key.
- **There is no safe module-import-time async Redis client in this codebase.** Any such client will be bound to whatever loop `asyncio.get_event_loop()` returns at import — usually not uvicorn's. This is a property of `redis.asyncio` 5.x/6.x (per redis-py #3351) and python-socketio 5.x (no fix in 5.11–5.16), not something we can paper over with version pins.
- **HA exposes concurrency bugs that single-replica hides.** The same code is buggy in both modes; it just doesn't fire reliably without cross-replica pubsub traffic. This argues for having HA repro in CI or locally from day one — which the Kind harness enables.
- **The chart is Kind-friendly out of the box.** Only truly external-to-Kind dependencies (cert-manager, External Secrets, CiliumNetworkPolicy) are already gated behind flags off-by-default. The one friction is the private GHCR image — solved by `kind load docker-image` + `pullPolicy: Never`.
- **Weaviate is off-path for this bug.** The chat endpoint's loop crash happens before RAG retrieval runs. Disabling Weaviate cuts ~1 GB of memory and ~60 s of startup time without weakening the repro.

## Historical Context (from thoughts/)

No prior research notes in `thoughts/shared/research/` on this bug — this is the first write-up. The four hotfixes reference only `REDIS-HA-FIX.md` (at the repo root) which was the single co-located bug log. Related adjacent work: `thoughts/shared/research/2026-02-04-gke-to-previder-migration.md` may contain relevant infra context (not verified).

## Related Research

- `thoughts/shared/research/2026-02-04-gke-to-previder-migration.md` — possibly relevant for tenant HA posture decisions.
- `REDIS-HA-FIX.md` (repo root) — the hotfix log; keep alongside this doc until Layer A lands, then fold into this research.

## Open Questions

1. **Does `starsessions.RedisStore.__init__` eagerly construct the redis.asyncio client, or lazily on first use?** Needs source inspection of `starsessions==2.2.1` or empirical test in Kind. If eager, add to Layer A. If lazy, skip.
2. **Does `_CONNECTION_CACHE` also capture `SentinelRedisProxy` across loops?** Our chart doesn't use sentinel (default `redis.enabled: true` with direct URL) but some tenants might. The proxy creates its sentinel client at `__init__` (same loop-binding risk).
3. **Should we keep `_CONNECTION_CACHE` at all?** Its de-dup benefit is tiny (maybe 3-5 cache keys across the app); its correctness cost (sharing loop-bound objects across import/lifespan) is the exact bug we're debugging. Recommend removal in Layer A.
4. **Can we write a pytest fixture that reproduces the bug against fakeredis/miniredis with two asyncio loops**, as a CI regression test? Would be faster than Kind for rapid iteration on Layer A.
5. **Is `pullPolicy: Always` forced anywhere we'd need to override it for Kind?** Chart defaults `openWebui.image.pullPolicy: Always` (`values.yaml:60`). Our override `Never` in `values-local.yaml` fixes this, but verify the migration-job inherits it via `_helpers.tpl`.
