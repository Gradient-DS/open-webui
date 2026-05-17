---
date: 2026-04-29T17:31:44+02:00
researcher: Lex Lubbers (via Claude)
git_commit: 759f0b630f22764e4686fbb308fa8c4f6a19f1c8
branch: feat/sync-redis
repository: Gradient-DS/open-webui (primary), Gradient-DS/soev-gitops (deployment cross-reference)
topic: "Redis-backed `_pending_flows` to fix the cross-replica OAuth race for OneDrive / Google Drive / Confluence"
tags: [research, oauth, onedrive, google-drive, confluence, redis, ha, multi-replica, sync-abstraction]
status: complete
last_updated: 2026-04-29
last_updated_by: Lex Lubbers (via Claude)
---

# Research: Redis-backed pending OAuth flows (option 2)

**Date**: 2026-04-29 17:31 +02:00
**Researcher**: Lex Lubbers (via Claude)
**Git commit**: `759f0b630f22764e4686fbb308fa8c4f6a19f1c8`
**Branch**: `feat/sync-redis`
**Repository**: `Gradient-DS/open-webui` (chart and app); cross-checked against `Gradient-DS/soev-gitops`

## Research Question

> Resolve the multi-replica OAuth race for OneDrive / Google Drive / Confluence and confirm whether Redis is the right fix. If yes, take option 2 from my own list:
>
> 1. Quick stop-gap: `sessionAffinity: ClientIP` on the gradient open-webui Service.
> 2. Proper fix: persist `_pending_flows` in Redis. One module change shared by all three providers.
> 3. Workaround: scale gradient to 1 replica during onboarding.
>
> Want option 2 — also for upstream PR. Is Redis the right call, and what does it actually take?

## Summary (TL;DR)

- **Yes, option 2 is the right fix.** All three OAuth flows currently store the auth-code-flow handshake state in a per-process module-global dict (`_pending_flows`) at `services/{onedrive,google_drive,confluence}/auth.py:25-26`. With `replicaCount: 2` and no sticky sessions, the callback can hit a different pod from the one that initiated the flow, and the state lookup returns "Invalid or expired authorization flow."
- **The plumbing is already there.** Gradient's HelmRelease has `redis.enabled: true` (`tenants/previder-prod/gradient/helmrelease.yaml:195-215`). The chart wires `REDIS_URL=redis://...:6379/0` and `WEBSOCKET_MANAGER=redis` whenever `redis.enabled` is true (`templates/open-webui/configmap.yaml:402-406`). The app already constructs `app.state.redis` in lifespan and uses it for JWT revocation, distributed task tracking, the rate limiter, the Socket.IO manager, AppConfig, YDoc, and starsessions. So **no infrastructure change is needed for gradient** — just the code change, and on tenants without `redis.enabled`, the same in-memory fallback pattern every other Redis-using subsystem already uses.
- **Scope of code change is small and bounded.** ~1 new helper module (`services/sync/pending_flows.py`), ~3 surgical edits in the per-provider auth modules, ~3 trivial edits in routers, and ~1 edit in `main.py`'s OAuth callback dispatcher. No DB migration, no new env vars, no new infra.
- **One trap to respect.** The codebase has a known "redis-ha-loop-bug": constructing async Redis clients at module import time crashes under sentinel HA because the client gets bound to the wrong asyncio loop. The fix that's already in `utils/redis.py:33-46` and `utils/lazy_resource.py` is to construct lazily (in lifespan or via `lazy(...)` proxy). The new helper must follow that pattern — read `request.app.state.redis` rather than building its own client at import. See [thoughts/shared/research/2026-04-20-redis-ha-loop-bug-and-kind-repro.md](../../thoughts/shared/research/2026-04-20-redis-ha-loop-bug-and-kind-repro.md).
- **A `sessionAffinity: ClientIP` stop-gap is reasonable but skip-able.** It buys partial coverage (egress-IP changes during the flow still break it), and the chart's Service template currently has no affinity field anywhere. Once option 2 ships, sticky sessions are not needed for *this* problem — but the 2026-04-15 HA readiness research already recommended sticky sessions for unrelated reasons (initial WebSocket 403s, [#5109](https://github.com/open-webui/open-webui/discussions/5109)). Treat that as a separate decision.

## Detailed Findings

### 1. The race, in code

All three providers follow an identical, copy-pasted shape. References below are to the OneDrive file; the Google Drive and Confluence files are functionally the same modulo provider-specific OAuth params.

| Step | Where | What |
|---|---|---|
| Initiate | `services/onedrive/auth.py:61-98` (`get_authorization_url`) | Generates a 32-byte URL-safe `state`, generates a PKCE `code_verifier`, stores `{user_id, knowledge_id, code_verifier, redirect_uri, created_at}` in `_pending_flows[state]`, returns the consent URL. |
| Consent | Microsoft / Google / Atlassian | User approves, browser redirects to `redirect_uri?code=...&state=...`. |
| Dispatch | `main.py:3083-3134` | Single OAuth callback route checks `state` against each provider's `_pending_flows` to decide whether this is a sync flow or normal SSO. **Direct module-global access** — uses `if state in _pending_flows:` not a public API. |
| Provider callback | `routers/{provider}_sync.py:handle_*_auth_callback` | Calls `get_pending_flow(state)` (read, no remove), then `complete_auth_callback(...)` which calls `exchange_code_for_tokens(code, state, user_id)`. |
| Exchange | `services/onedrive/auth.py:101-196` | Pops the flow from `_pending_flows`, validates `user_id` matches, validates TTL, posts to the token endpoint with `code_verifier`, persists the resulting refresh token in the `OAuthSessions` table. |

The race: `_pending_flows` is a Python dict at module scope. Each replica has its own. Whichever pod handled `/auth/initiate` is the only pod that knows about that `state`. With `replicaCount: 2` and no sessionAffinity, Cilium's 5-tuple hashing usually keeps the browser on one pod within a window, but any of the following breaks it:

- The "lucky" pod restarts mid-flow (deploy / OOM / eviction / rolling update — and the chart explicitly uses `RollingUpdate` with `maxUnavailable: 0`, so the new pod comes up *during* the user's flow).
- Egress IP/port changes (mobile networks, NAT rebinding, VPN reconnect — typical when consent lasts more than ~60 s).
- Browser opens the consent in a different network context than the `/auth/initiate` (corporate VPN handoff).

When the callback lands on the other pod, `_pending_flows[state]` is missing, the router returns "Invalid or expired authorization flow", and the integration silently fails. There is no telemetry on this today.

### 2. Gradient already has Redis — but only for sessions/sockets

The chart at `helm/open-webui-tenant/`:

- `templates/redis/statefulset.yaml:1-66` — single-replica StatefulSet running `redis:7-alpine` with `--save "" --appendonly no` (no AOF, no RDB, ephemeral by design), `emptyDir` volume.
- `templates/redis/service.yaml` — ClusterIP Service in the same namespace.
- `templates/open-webui/configmap.yaml:402-406`:

  ```yaml
  {{- if .Values.redis.enabled }}
  ENABLE_WEBSOCKET_SUPPORT: "true"
  WEBSOCKET_MANAGER: "redis"
  REDIS_URL: "redis://{{ include "open-webui-tenant.redis.fullname" . }}:6379/0"
  {{- end }}
  ```

Gradient's HelmRelease enables it (`tenants/previder-prod/gradient/helmrelease.yaml:195-215`). Confirmed via `kubectl` paths in the gitops CLAUDE.md guidance — Redis was rolled out to every active previder-prod tenant on 2026-04-19 ([thoughts/shared/plans/2026-04-15-openwebui-ha-redis-migration.md](../../thoughts/shared/plans/2026-04-15-openwebui-ha-redis-migration.md)).

**Implication:** the gradient pod *already* has `REDIS_URL` set in env. `app.state.redis` is already a working async Redis client. We aren't adding infrastructure — we're using infrastructure that's been sitting there since 2026-04-19.

The Redis instance is ephemeral on purpose. That's fine for `_pending_flows` — the data is short-lived (10 min TTL), losing it on a Redis restart just means in-flight users have to retry consent once. Same trade-off as today, just no longer dependent on which *open-webui* pod they hit.

### 3. The codebase has a clean Redis-with-fallback pattern

Every Redis-touching subsystem in the backend follows the same shape: "use Redis if `app.state.redis` (or `REDIS_URL`) is set, otherwise fall back to in-process state." This is documented and exercised:

| Subsystem | Redis path | Fallback path |
|---|---|---|
| JWT revocation (`utils/auth.py:223-257`) | `await app.state.redis.set/get` | Skip — token cannot be revoked but verifies normally |
| Distributed task registry (`tasks.py:25-211`) | `redis.pipeline().hset/sadd/...` + pubsub | `tasks: Dict[str, asyncio.Task]` module dict |
| `AppConfig` (`config.py:220-279`) | per-key `f'{prefix}:config:{key}'` | DB only, no cross-pod refresh |
| Socket.IO manager (`socket/main.py:90-191`) | `socketio.AsyncRedisManager(...)` | In-memory manager |
| `MODELS` / `SESSION_POOL` / `USAGE_POOL` (`socket/main.py:142-160`) | `RedisDict` over `HSET`/`HGET` | Plain `dict()` |
| YDoc collaboration (`socket/utils.py:124-263`) | per-doc lists + sets | Per-process dicts |
| Rate limiter (`utils/rate_limit.py:38-135`) | `INCR` + `EXPIRE` per bucket | Class-level `_memory_store` |
| StarSessions (`main.py:2911-2941`) | `RedisStore` from `starsessions` | Signed-cookie `SessionMiddleware` |

The new pending-flows helper should follow the same convention. That keeps it consistent with the rest of the codebase, keeps single-replica dev setups working with no Redis, and is upstream-mergeable (Open WebUI's official scaling docs assume Redis is optional).

### 4. Surface area of the change

#### 4a. New helper module: `backend/open_webui/services/sync/pending_flows.py`

The user already has `services/sync/` (the abstraction layer that OneDrive / Google Drive / Confluence share — `base_worker.py`, `provider.py`, `scheduler.py`, `token_refresh.py`, etc.). The pending-flows helper belongs here. Public async API:

- `async def store_pending_flow(request, provider: str, state: str, payload: dict, ttl: int = 600) -> None`
- `async def get_pending_flow(request, provider: str, state: str) -> Optional[dict]`
- `async def pop_pending_flow(request, provider: str, state: str) -> Optional[dict]`
- `async def has_pending_flow(request, provider: str, state: str) -> bool`

Implementation notes:

- Read the client lazily from `request.app.state.redis`. Do **not** call `get_redis_connection()` at module load — that's the `redis-ha-loop-bug` trap.
- Key format: `f'{REDIS_KEY_PREFIX}:oauth:pending:{provider}:{state}'`. `state` is `secrets.token_urlsafe(32)` so it's already URL-/colon-safe.
- TTL via `SET ... EX ttl`. Redis evicts on expiry; the `_cleanup_expired_flows()` cleanup function disappears entirely.
- Fallback: when `request.app.state.redis is None`, use a module-level dict + the same `created_at`-based TTL check the current code does. Keeps single-replica dev working.
- `pop_*` should use a Lua `GETDEL` (Redis 6.2+, our `redis:7-alpine` chart image supports it) or a `pipeline().get().delete().execute()` to make pop atomic. Single-shot pop is the security-relevant case (replay protection).

Sketch (illustrative, not final):

```python
# backend/open_webui/services/sync/pending_flows.py
import json, time, asyncio
from typing import Optional
from fastapi import Request
from open_webui.env import REDIS_KEY_PREFIX

_FALLBACK: dict[str, dict] = {}
_FALLBACK_LOCK = asyncio.Lock()
_DEFAULT_TTL = 600

def _key(provider: str, state: str) -> str:
    return f'{REDIS_KEY_PREFIX}:oauth:pending:{provider}:{state}'

async def store_pending_flow(request: Request, provider: str, state: str, payload: dict, ttl: int = _DEFAULT_TTL) -> None:
    redis = getattr(request.app.state, 'redis', None)
    if redis is not None:
        await redis.set(_key(provider, state), json.dumps(payload), ex=ttl)
        return
    async with _FALLBACK_LOCK:
        _FALLBACK[_key(provider, state)] = {**payload, '_expires_at': time.time() + ttl}

async def get_pending_flow(request: Request, provider: str, state: str) -> Optional[dict]:
    redis = getattr(request.app.state, 'redis', None)
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

async def pop_pending_flow(request: Request, provider: str, state: str) -> Optional[dict]:
    redis = getattr(request.app.state, 'redis', None)
    if redis is not None:
        # Atomic pop: requires Redis 6.2+
        raw = await redis.execute_command('GETDEL', _key(provider, state))
        return json.loads(raw) if raw else None
    async with _FALLBACK_LOCK:
        item = _FALLBACK.pop(_key(provider, state), None)
        if not item or item['_expires_at'] < time.time():
            return None
        return {k: v for k, v in item.items() if k != '_expires_at'}

async def has_pending_flow(request: Request, provider: str, state: str) -> bool:
    redis = getattr(request.app.state, 'redis', None)
    if redis is not None:
        return bool(await redis.exists(_key(provider, state)))
    async with _FALLBACK_LOCK:
        item = _FALLBACK.get(_key(provider, state))
        return item is not None and item['_expires_at'] >= time.time()
```

The four async signatures take `request` so callers can use the live `app.state.redis` constructed in lifespan — the codebase pattern documented at `utils/auth.py:223-257`.

#### 4b. Per-provider edits

Each of the three `services/{provider}/auth.py` modules drops:

- `_pending_flows: Dict[str, Dict[str, Any]] = {}`
- `_FLOW_TTL_SECONDS = 600` (passed as `ttl` arg now)
- `_cleanup_expired_flows()` (Redis TTL handles it)

And the public functions become async and take `request`:

- `async def get_authorization_url(request, user_id, knowledge_id, redirect_uri)` — `await store_pending_flow(request, '<provider>', state, {...})`
- `async def exchange_code_for_tokens(request, code, state, user_id)` — `flow = await pop_pending_flow(request, '<provider>', state)`
- `async def get_pending_flow(request, state)` and `async def remove_pending_flow(request, state)` — keep names so the routers don't need their imports rewritten; both delegate to the helper.

Check for one subtlety in `services/confluence/auth.py:175-249`: after popping the flow, it does an extra `httpx` call to `accessible-resources`. With Redis pop being atomic, the order is fine — pop first, then HTTP, then DB write. No change to the structure.

#### 4c. Router edits (3 files, all the same)

Each router's `handle_*_auth_callback` already calls `get_pending_flow(state)`; just thread `request` through. References:

- `routers/onedrive_sync.py:216-264` (`handle_onedrive_auth_callback`)
- `routers/google_drive_sync.py:247-295` (`handle_google_drive_auth_callback`)
- `routers/confluence_sync.py:255-305` (`handle_confluence_auth_callback` — line numbers approximate from the grep, full file not read)

The shared `complete_auth_callback` (in `services/sync/router_helpers` based on the import patterns; not read here) takes the `exchange_code_fn` as a callable, so awaiting that already works.

#### 4d. Dispatcher edit in `main.py:3083-3134`

The current dispatcher reaches *into* each provider's module to inspect `_pending_flows` directly:

```python
from open_webui.services.onedrive.auth import _pending_flows
if state in _pending_flows:
    return await handle_onedrive_auth_callback(request)
```

Replace with the public helper:

```python
from open_webui.services.sync.pending_flows import has_pending_flow
if await has_pending_flow(request, 'onedrive', state):
    return await handle_onedrive_auth_callback(request)
```

Three identical edits (microsoft → onedrive, google → google_drive, atlassian → confluence). This is the cleanest improvement of the whole change — the dispatcher stops poking at module privates.

### 5. Why option 1 (`sessionAffinity: ClientIP`) doesn't fully fix it

- The chart's `templates/open-webui/service.yaml` has no `sessionAffinity` field today (full file fits in one screen — quoted in the research session). Setting `service.spec.sessionAffinity: ClientIP` on a Kubernetes Service hashes by client IP at the kube-proxy / Cilium layer. That works for direct ClusterIP traffic.
- Gradient's traffic flows: Internet → Cilium Gateway (Envoy) → HTTPRoute → Service → Pod. Cilium Gateway terminates the connection, which means the *Service-level* affinity sees the Envoy pod's IP, not the user's. So `sessionAffinity: ClientIP` on the Service is a no-op end-to-end here.
- The right place would be a `BackendTrafficPolicy` (Envoy Gateway) or a Cilium-specific `consistent_hash` config on the route. The 2026-04-15 HA readiness research already flagged this as TODO and never resolved it ([thoughts/shared/research/2026-04-15-openwebui-ha-multi-replica-readiness.md](../../../thoughts/shared/research/2026-04-15-openwebui-ha-multi-replica-readiness.md), §5/§9).
- Even if we did get cookie-based affinity working at the gateway, it doesn't survive: pod restart mid-flow (RollingUpdate with `maxUnavailable: 0` deliberately *replaces* a pod under load), browser cookie flush, third-party cookie blocking, or the user opening the consent in a different browser context (e.g. incognito).

Verdict: option 1 is at best partial mitigation, at worst a no-op behind the gateway. Skip and go directly to option 2.

### 6. What to ship, in what order

1. **Code change in open-webui** (`feat/sync-redis` branch — already exists for this work).
   - Add `services/sync/pending_flows.py`.
   - Edit three `auth.py` modules + three routers + `main.py` dispatcher.
   - Tests:
     - Unit: `pending_flows` round-trip with and without `app.state.redis`, TTL expiry, atomic pop.
     - Integration: spin up `replicaCount: 2` in the kind harness ([thoughts/shared/plans/2026-04-20-redis-ha-loop-fix-and-kind-harness.md](../../thoughts/shared/plans/2026-04-20-redis-ha-loop-fix-and-kind-harness.md) describes the harness), force the callback to a different pod (delete-the-pod-mid-flow style), verify token persists.
   - PR description: include the cross-pod failure mode, the three-providers-share-one-bug framing, and a note that no env var or chart change is needed when `redis.enabled: true` (which the chart already wires).

2. **Validate on gradient.soev.ai** (no infra change needed — `redis.enabled: true`, `REDIS_URL` already in env). Just bump the image tag through the existing image-policy automation.

3. **Optional: file an upstream issue/PR.** The fix is generic — any Open WebUI deployment running multi-replica with these integrations hits the same race. The PR will be accepted more easily if framed as "make the existing Redis-with-fallback pattern apply to the OAuth handshake state too" — same shape as `tasks.py`, `utils/rate_limit.py`, `socket/main.py`.

4. **Skip option 1** unless we find another reason to want sticky sessions (e.g. WebSocket initial-connection 403s — separate issue, separate decision).

5. **Skip option 3** (scale to 1 replica). Not needed once option 2 is shipped, and reverts HA hardening that was just rolled out.

### 7. Open questions

- **Should we move OAuth flows to `app.state.redis` or to a per-feature client?** `app.state.redis` is built once in lifespan with `decode_responses=True`. The pending-flow values are JSON strings — fine with `decode_responses=True`. Reusing the existing client is the cleaner choice and matches the JWT revocation pattern. Recommend: reuse `app.state.redis`.
- **`GETDEL` availability.** Our chart image is `redis:7-alpine`, which has `GETDEL` (added 6.2). For deployments on older Redis the helper should fall back to `pipeline().get().delete().execute()`. Not strictly atomic but acceptable for OAuth state — at worst, a parallel callback for the same `state` (which already requires a leaked `state`, which already means the user's browser session is compromised) sees a duplicate exchange attempt and one of them fails.
- **Dispatcher provider precedence.** The current dispatcher checks `microsoft`/`google`/`atlassian` separately based on the OAuth route param, so there's no cross-provider state-collision concern. Keeping the per-provider key prefix preserves this even if state-token uniqueness across providers somehow degraded.
- **Does the `feat/sync-redis` branch already contain partial work?** The branch name suggests it might. Worth checking the diff before starting fresh — `git diff main..HEAD --stat -- backend/open_webui/services/` will show whether any of the three auth modules already have changes pending.

## Code References

- `backend/open_webui/services/onedrive/auth.py:25-27` — module-global `_pending_flows`, `_FLOW_TTL_SECONDS = 600`. Same pattern at `services/google_drive/auth.py:24-26` and `services/confluence/auth.py:30-32`.
- `backend/open_webui/services/onedrive/auth.py:78-84` — flow stored on initiate. Same shape at `google_drive/auth.py:77-83` and `confluence/auth.py:102-108`.
- `backend/open_webui/services/onedrive/auth.py:101-196` — `exchange_code_for_tokens`, the single consumer of `_pending_flows.pop`. Same at `google_drive/auth.py:100-191` and `confluence/auth.py:164-273`.
- `backend/open_webui/main.py:3083-3134` — OAuth callback dispatcher, three direct `from ... import _pending_flows` reaches into module privates.
- `backend/open_webui/routers/onedrive_sync.py:216-264` — `handle_onedrive_auth_callback`. Same pattern in `google_drive_sync.py:247-295` and `confluence_sync.py:255-305`.
- `backend/open_webui/utils/redis.py:182-275` — `get_redis_client`, `get_redis_connection`, `SentinelRedisProxy`. Single-source-of-truth for Redis client construction.
- `backend/open_webui/utils/lazy_resource.py` — `lazy(factory)` proxy, the documented antidote to module-import-time client construction.
- `backend/open_webui/main.py:852-857` — `app.state.redis = get_redis_connection(..., async_mode=True)` in lifespan. The client to reuse.
- `backend/open_webui/utils/auth.py:223-257` — JWT revocation. The closest existing pattern to copy: short-lived keys, TTL on write, async client, optional Redis with skip-if-absent.
- `helm/open-webui-tenant/templates/open-webui/configmap.yaml:402-406` — chart wires `REDIS_URL` and `WEBSOCKET_MANAGER` when `redis.enabled: true`.
- `helm/open-webui-tenant/templates/redis/statefulset.yaml:1-66` — bundled Redis (single-replica, `emptyDir`, no AOF/RDB).
- `helm/open-webui-tenant/templates/open-webui/service.yaml:1-17` — Service has no `sessionAffinity` field.
- `tenants/previder-prod/gradient/helmrelease.yaml:56-83` — `replicaCount: 2`, `RollingUpdate maxUnavailable: 0`, `pdb.minAvailable: 1`.
- `tenants/previder-prod/gradient/helmrelease.yaml:195-215` — `redis.enabled: true` for gradient.

## Architecture Insights

- **The three-provider duplication is real but not load-bearing.** Each `auth.py` is ~270 lines of near-identical code (PKCE, state, TTL cleanup, store/pop). This was flagged in [thoughts/shared/research/2026-03-24-cloud-sync-abstraction-audit-merge-strategy.md](../../thoughts/shared/research/2026-03-24-cloud-sync-abstraction-audit-merge-strategy.md). The pending-flows helper is a small step toward consolidating the *state-handling* portion of those modules without forcing a full extract-base-class rewrite. Keep the per-provider files for the OAuth provider knobs (URLs, scopes, audience, post-token discovery like Confluence's accessible-resources call); centralize only the state machine.
- **The "module global + cleanup function" pattern is repeated elsewhere in the backend.** `tasks.py` did the same thing (in-memory `tasks: Dict[str, asyncio.Task]`) and ended up with a Redis adapter; same with `utils/rate_limit.py`. The pending-flows fix is the third application of the same conversion. Worth a one-liner in the world model on this idiom.
- **Module-import-time async client construction is dangerous in this codebase.** The `redis-ha-loop-bug` work (April 2026) added `clear_connection_cache()` to lifespan and the `lazy()` proxy specifically to defend against this. The new helper avoids it entirely by reading `request.app.state.redis` per call — no client at all in the helper module.
- **`sessionAffinity: ClientIP` does not survive the Cilium Gateway hop.** Worth recording in the world model — it's a footgun anyone might reach for. The right level for sticky routing here is gateway-level config (HTTPRoute hash-on-cookie or Envoy `BackendTrafficPolicy`), not Service-level affinity.

## Historical Context (from thoughts/)

- [thoughts/shared/research/2026-04-15-openwebui-ha-multi-replica-readiness.md](../../../thoughts/shared/research/2026-04-15-openwebui-ha-multi-replica-readiness.md) — full HA-readiness audit. Identified Redis, websocket env wiring, migration race, sticky sessions, and PDB as prerequisites. Did not catch the OAuth `_pending_flows` issue (the audit focused on Socket.IO, MODELS/SESSION_POOL/USAGE_POOL, and Alembic). This research is the missing piece.
- [thoughts/shared/plans/2026-04-15-openwebui-ha-redis-migration.md](../../../thoughts/shared/plans/2026-04-15-openwebui-ha-redis-migration.md) — the rollout plan that landed `redis.enabled: true` on every active previder-prod tenant on 2026-04-19.
- [thoughts/shared/research/2026-04-20-redis-ha-loop-bug-and-kind-repro.md](../../thoughts/shared/research/2026-04-20-redis-ha-loop-bug-and-kind-repro.md) and [thoughts/shared/plans/2026-04-20-redis-ha-loop-fix-and-kind-harness.md](../../thoughts/shared/plans/2026-04-20-redis-ha-loop-fix-and-kind-harness.md) — the asyncio-loop-binding bug and the kind-based test harness. The new helper must follow the post-fix patterns (`lazy()`, lifespan-built client, never `redis.from_url()` at import time). The kind harness is the right tool to integration-test the cross-pod path.
- [thoughts/shared/research/2026-03-24-cloud-sync-abstraction-audit-merge-strategy.md](../../thoughts/shared/research/2026-03-24-cloud-sync-abstraction-audit-merge-strategy.md) — sync-abstraction audit. The pending-flows module is a logical extension of `services/sync/`.
- `collab/notes.md` (open-webui collab memory) — Google Drive integration note (26-03-2026) explicitly mentions "OAuth Callback Multiplexing: Reused the existing `/oauth/google/callback` URL — checks `state` against pending Drive flows before falling through to SSO login handler." The dispatcher we're editing is exactly that mechanism. The note doesn't flag the multi-replica issue — it was written when there was 1 pod.

## Related Research

- [thoughts/shared/research/2026-04-15-openwebui-ha-multi-replica-readiness.md](../../../thoughts/shared/research/2026-04-15-openwebui-ha-multi-replica-readiness.md)
- [thoughts/shared/research/2026-04-20-redis-ha-loop-bug-and-kind-repro.md](../../thoughts/shared/research/2026-04-20-redis-ha-loop-bug-and-kind-repro.md)
- [thoughts/shared/research/2026-03-24-cloud-sync-abstraction-audit-merge-strategy.md](../../thoughts/shared/research/2026-03-24-cloud-sync-abstraction-audit-merge-strategy.md)
