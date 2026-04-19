# Hotfix: Redis-async event-loop crash under HA (BaseHTTPMiddleware incompatibility)

## Symptom

On tenants running in HA mode (`replicaCount: 2`, socket.io Redis manager, Redis-backed session store), authenticated `POST /api/chat/completions` requests return HTTP **500** with a plain-text body `"Internal Server Error"` (21 bytes). The streamed chat response itself still renders in the browser via the socket.io channel, but the parallel HTTP POST response causes a frontend JSON parse error:

```
Unexpected token 'I', "Internal S"... is not valid JSON
```

With `replicaCount: 1` the error disappears or becomes very rare. Errors only occur with a document attached (high concurrency path — many auth-validated requests packed together).

## Root cause

`starlette.middleware.base.BaseHTTPMiddleware` wraps the downstream ASGI call in its own internal task group, which means `call_next(request)` runs the rest of the chain in a **sub-task** whose anyio task group can end up associated with a different asyncio event-loop accessor than the one on which long-lived async resources were created.

Our long-lived async resource is the **Redis client** built at app startup and stored on `app.state.redis`:

```python
# backend/open_webui/main.py:819
app.state.redis = get_redis_connection(
    redis_url=REDIS_URL,
    redis_sentinels=get_sentinels_from_env(...),
    redis_cluster=REDIS_CLUSTER,
    async_mode=True,
)
```

This client holds a connection pool bound to the asyncio event loop that was running at module import / lifespan-startup time.

On every authenticated request, FastAPI's `get_current_user` dependency calls `is_valid_token`, which reads the revocation flag from Redis:

```python
# backend/open_webui/utils/auth.py:229
revoked = await request.app.state.redis.get(
    f'{REDIS_KEY_PREFIX}:auth:token:{jti}:revoked'
)
```

Under HA load, Redis has lots of concurrent traffic (socket.io pubsub from the Redis manager, session reads/writes, auth-revocation checks). The probability of the Redis async I/O hitting a `read_response` while the enclosing request lives in a `BaseHTTPMiddleware` sub-task rises sharply, and we get:

```
RuntimeError: Task <Task pending name='starlette.middleware.base.BaseHTTPMiddleware.__call__.<locals>.call_next.<locals>.coro' ...>
got Future <Future pending> attached to a different loop
```

Starlette's `ServerErrorMiddleware` catches the unhandled exception and returns the default `"Internal Server Error"` text/plain 500 — which the browser's JSON parser then chokes on.

The real-world exception chain (from production logs):

```
File ".../redis/asyncio/connection.py", line 734, in read_response
  → response = await self._parser.read_response(...)
File ".../asyncio/streams.py", line 543, in _wait_for_data
  → await self._waiter
RuntimeError: ...got Future ... attached to a different loop
  (caused in Task: BaseHTTPMiddleware.__call__.<locals>.call_next.<locals>.coro)

The above exception was the direct cause of the following exception:
File ".../uvicorn/protocols/http/httptools_impl.py", line 416, in run_asgi
File ".../starlette/middleware/errors.py", line 186, in __call__
File ".../starlette/middleware/sessions.py", line 88, in __call__
File ".../open_webui/utils/audit.py", line 156, in __call__
File ".../starlette/middleware/cors.py", line 96, in __call__
... (inside the BaseHTTPMiddleware wrappers)
```

Audit middleware is not at fault — it's already a pure ASGI-3 implementation. The culprits are the BaseHTTPMiddleware wrappers below it in the stack.

## Why this is a well-known FastAPI pitfall

FastAPI's own documentation warns that `BaseHTTPMiddleware` has limitations with async resources: https://www.starlette.io/middleware/#basehttpmiddleware

Community reports in `encode/starlette` issue #1438 and `fastapi/fastapi` #1640 describe the same loop-crossing crash when long-lived async clients (SQLAlchemy asyncpg, Redis asyncio, gRPC async channels) are used from within a request that passes through `BaseHTTPMiddleware`. The upstream guidance is: **for anything non-trivial, write pure ASGI middleware**.

## BaseHTTPMiddleware call sites in this codebase

All in `backend/open_webui/main.py` or files it imports:

| # | Location | Kind | Purpose |
|---|----------|------|---------|
| 1 | `main.py:1650` | `class RedirectMiddleware(BaseHTTPMiddleware)` | Rewrite `?v=` YouTube + PWA share-target URLs |
| 2 | `utils/security_headers.py:9` | `class SecurityHeadersMiddleware(BaseHTTPMiddleware)` | Inject HSTS / CSP / X-Frame-Options headers |
| 3 | `main.py:1739` | `@app.middleware('http')` `commit_session_after_request` | SQLAlchemy scoped-session commit + remove |
| 4 | `main.py:1753` | `@app.middleware('http')` `check_url` | Normalise `request.state.token` from Authorization header / cookie / x-api-key; stamp `X-Process-Time` |
| 5 | `main.py:1780` | `@app.middleware('http')` `inspect_websocket` | Reject malformed WebSocket upgrade requests early |

`@app.middleware('http')` is a convenience wrapper around `BaseHTTPMiddleware` — same bug class.

`AuditLoggingMiddleware` (`utils/audit.py`) and `APIKeyRestrictionMiddleware` (`main.py:1694`) are already pure ASGI — keep them as-is.

## Fix: convert all five to pure ASGI (ASGI-3) middleware

Same public behaviour, no functional changes. Each middleware becomes:

```python
class MyMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        # ... logic here using direct scope/receive/send ...
        await self.app(scope, receive, send)
```

Because pure ASGI middlewares don't spawn per-request sub-tasks, the Redis client (and any async resource on `app.state`) stays on the same event loop the request runs on. The loop-mismatch crash goes away entirely.

### Preservation notes per middleware

- **`commit_session_after_request`**: wrap `await self.app(scope, receive, send)` in `try/finally` calling `ScopedSession.commit()` and `ScopedSession.remove()` — same semantic as today. The commit happens after the full response is sent, identical ordering.
- **`check_url`**: mutate `scope['state']` (or attach to the `Request(scope)` early) so downstream routes see `request.state.token` / `request.state.enable_api_keys`. Header injection (`X-Process-Time`) done via a `send` wrapper that adds to the headers list on the `http.response.start` message.
- **`inspect_websocket`**: the original code only inspects `request.query_params` and `request.headers`, then either returns a `JSONResponse(400)` or passes through. In pure ASGI, we inspect the `scope` headers, send a manual 400 response if invalid, or pass through.
- **`RedirectMiddleware`**: same pattern — inspect scope, if it matches the YouTube / shared path, send a 302 redirect via `send`, otherwise pass through.
- **`SecurityHeadersMiddleware`**: wrap `send` to mutate the `http.response.start` headers.

## Rollout

1. Apply the code changes (this PR).
2. Run existing unit tests — no public-API changes, should all pass.
3. Tag `v1.0.2`, push to `ghcr.io/gradient-ds/open-webui`.
4. Flux ImagePolicy `open-webui-semver` (>= 1.0.0) auto-rolls tenants to `v1.0.2` within ~1 min.
5. Verify on demo: send several doc-attached chats concurrently; grep tenant pod logs for `got Future ... attached to a different loop` — should be zero occurrences. The 500 on `POST /api/chat/completions` goes away; browser stops throwing the JSON parse error.

## Why not alternatives

- **Disable audit log** (`ENABLE_AUDIT_LOG=false`): user needs it, non-starter.
- **Disable Redis** (`ENABLE_WEBSOCKET_SUPPORT=false` or swap to in-memory sessions): breaks HA coordination, socket.io across replicas would require sticky sessions instead.
- **Per-request Redis connection in `auth.py`**: papers over the real problem; every `BaseHTTPMiddleware` + async-resource combo will be prone to the same class of failure (future additions, SQLAlchemy async, etc.).
- **Skip HA, go single-replica**: regression on reliability, no rolling updates during pod restarts.

Pure ASGI is the right layer to fix this at.
