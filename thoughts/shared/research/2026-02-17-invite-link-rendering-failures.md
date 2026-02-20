---
date: 2026-02-17T15:30:00+01:00
researcher: claude
git_commit: 5d18f2c4 (open-webui), 73a85aa5 (soev-gitops)
branch: feat/sync-improvements (open-webui), main (soev-gitops)
repository: open-webui, soev-gitops
topic: "Why invite links sometimes don't render and CSS/JS fails to load"
tags: [research, codebase, rendering, invite-links, static-assets, deployment, cilium, sveltekit]
status: complete
last_updated: 2026-02-17
last_updated_by: claude
---

# Research: Invite Link Rendering Failures and CSS/JS Loading Issues

**Date**: 2026-02-17T15:30:00+01:00
**Researcher**: claude
**Git Commit**: 5d18f2c4 (open-webui), 73a85aa5 (soev-gitops)
**Branch**: feat/sync-improvements (open-webui), main (soev-gitops)
**Repository**: open-webui, soev-gitops

## Research Question

Sometimes when people open invite links to gradient.soev.ai or demo.soev.ai (or any other deployment), the page does not render the first time. Also sometimes the CSS/JS does not all seem to load and users see static HTML. What could cause these issues in the app or the deployment logic?

## Summary

There are **multiple contributing factors** across both the application and deployment layers that can cause first-load rendering failures and CSS/JS loading issues. The most impactful are:

1. **Recreate deployment strategy with 1 replica** -- causes complete downtime during every deploy
2. **`crossorigin="use-credentials"` on static assets** -- can cause CORS-related loading failures
3. **Sequential async initialization gates** -- slow backends cause prolonged blank screens
4. **No Cache-Control headers on static assets** -- hashed immutable assets aren't cached
5. **`pullPolicy: Always`** -- slows pod startup by always pulling the image

## Detailed Findings

### Finding 1: Deployment Downtime (Recreate Strategy + 1 Replica)

**Severity: HIGH -- Most likely cause of intermittent failures**

The Helm chart uses `strategy.type: Recreate` with `replicaCount: 1` (`helm/open-webui-tenant/values.yaml:62-67`). During every deployment:

1. The running pod is **terminated first**
2. The new pod starts and must pass the startup probe
3. The startup probe allows up to **300 seconds** (30 retries x 10s) before declaring failure
4. The readiness probe has `initialDelaySeconds: 30`

This means there is a **30-300 second window** during every deployment where the service is completely unavailable. Any user opening an invite link during this window would get a connection error or a gateway timeout from Cilium.

**Evidence:**
- `helm/open-webui-tenant/values.yaml:62-67` -- Recreate strategy, 1 replica
- `helm/open-webui-tenant/templates/open-webui/deployment.yaml:125-141` -- probe timings
- No HPA or PDB configured anywhere

### Finding 2: `crossorigin="use-credentials"` on Asset References

**Severity: HIGH -- Can cause CSS/JS to fail silently**

The HTML template (`src/app.html`) uses `crossorigin="use-credentials"` on multiple asset references:

- Line 5-25: All favicon `<link>` tags
- Line 26: `<link rel="manifest" href="/manifest.json" crossorigin="use-credentials">`
- Line 34: `<script src="/static/loader.js" defer crossorigin="use-credentials">`
- Line 35: `<link rel="stylesheet" href="/static/custom.css" crossorigin="use-credentials">`

The `crossorigin="use-credentials"` attribute tells the browser to include cookies with the request AND requires the server to respond with `Access-Control-Allow-Credentials: true` and a specific (non-wildcard) `Access-Control-Allow-Origin` header. However:

- The FastAPI CORS middleware (`main.py:1496-1502`) is configured with `allow_origins=["*"]` by default
- Per the CORS spec, when `Access-Control-Allow-Credentials: true`, the `Access-Control-Allow-Origin` CANNOT be `*` -- it must be a specific origin
- The CORS_ALLOW_ORIGIN is set per-tenant (e.g., `https://gradient.soev.ai` in `values-patch.yaml:21`), which should work -- but if this env var is misconfigured or missing, the wildcard default would cause credential-bearing requests to fail

Even when CORS is configured correctly, `use-credentials` on same-origin requests adds unnecessary complexity. If the cookie/session state is in a bad state (e.g., an expired `owui-session` cookie), browsers may handle the credentialed request differently than a simple request.

### Finding 3: Missing Static Assets (`loader.js`, `custom.css`)

**Severity: MEDIUM -- Causes 404 errors on every page load**

The HTML template references two files that **do not exist** in the source tree:

- `/static/loader.js` (referenced at `src/app.html:34`)
- `/static/custom.css` (referenced at `src/app.html:35`)

The backend copies files from `FRONTEND_BUILD_DIR/static/` into `STATIC_DIR` at startup (`config.py:827-849`). If these files aren't in the build output, every page load generates 404 requests for them. While the `defer` attribute on the script tag means a 404 won't block rendering, it does:

- Generate console errors
- Waste network resources
- Could confuse browser loading priorities

The `custom.css` 404 is potentially more impactful -- if a browser treats the failed CSS fetch as blocking (depending on implementation), it could delay rendering.

### Finding 4: Sequential Loading Gates and Backend Dependency

**Severity: MEDIUM -- Causes blank page if backend is slow**

The app has a **double loading gate** pattern:

1. **Root layout** (`+layout.svelte:844`): Nothing renders until `loaded = true` (set at line 814)
2. **`(app)` layout** (`(app)/+layout.svelte:385`): Content gated behind its own `loaded` flag

The root layout's `loaded` depends on:
- `GET /api/config` succeeding (line 724-729)
- Socket.IO connection establishing (line 752)
- Session user fetch if token exists (line 757-771)

If the backend is slow to respond (cold start, high load, database connection issues), users see only the splash screen indefinitely. If the backend config fetch **fails entirely**, the user is redirected to `/error` (line 782).

For invite links specifically, the sequence is:
1. Root layout fetches backend config (network roundtrip 1)
2. Root layout sets `loaded = true`, invite page mounts
3. Invite page fetches `GET /api/v1/invites/{token}/validate` (network roundtrip 2)
4. Invite form finally renders

That's a **minimum of 2 sequential API calls** before the invite form is visible.

### Finding 5: No Cache-Control Headers on Hashed Assets

**Severity: MEDIUM -- Causes unnecessary re-downloads**

The `SecurityHeadersMiddleware` (`utils/security_headers.py`) only sets `Cache-Control` if the `CACHE_CONTROL` env var is set -- and it applies the **same header to all responses** (API and static alike). No tenant sets this env var.

SvelteKit's build output produces content-hashed filenames like `/_app/immutable/entry/start.D7f3U2kP.js`. These files are immutable and should be cached aggressively (`Cache-Control: public, max-age=31536000, immutable`). Without this:

- Every page load re-validates or re-downloads all JS/CSS bundles
- On slow connections, this significantly increases load time
- Browser cache may still work via heuristic caching, but it's unreliable

### Finding 6: `pullPolicy: Always` Slows Pod Startup

**Severity: LOW-MEDIUM**

All tenants use `imagePullPolicy: Always` (`values.yaml:60`). The Open WebUI Docker image is large (includes Python, PyTorch, embedding models, etc.). On every pod restart, Kubernetes re-pulls the image manifest and potentially layers. Combined with the Recreate strategy, this extends the downtime window.

### Finding 7: Cilium Gateway and HTTP-to-HTTPS Redirect

**Severity: LOW -- Edge case**

The HTTP-to-HTTPS redirect (`infrastructure/previder-prod/gateway/http-redirect.yaml`) has **no hostnames field** due to a Cilium bug (#37994). This means the redirect applies to all port 80 traffic. While functionally correct, if a user's browser opens an invite link via `http://` (e.g., copied from a non-HTTPS email client), the 301 redirect adds an extra roundtrip.

The HSTS header (`max-age=31536000; includeSubDomains`) set in HTTPRoutes ensures subsequent visits use HTTPS directly, but the **first visit** to a new domain may still require the redirect.

### Finding 8: CORS Wildcard with Credentials

**Severity: LOW-MEDIUM -- Could cause silent failures**

The CORS middleware at `main.py:1496-1502`:
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOW_ORIGIN,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

With `allow_credentials=True`, if `CORS_ALLOW_ORIGIN` resolves to `["*"]` (the default from `env.py`), the CORS spec says the browser should reject the response. The tenant deployments set `CORS_ALLOW_ORIGIN` to their specific domain, but if this is ever misconfigured:
- All `crossorigin="use-credentials"` requests would fail
- The SvelteKit-injected script tags (without `crossorigin`) would still work
- Result: partial load -- main app JS works, but some assets fail

## Code References

### Application Layer (open-webui)
- `svelte.config.js:1-19` -- Static adapter with SPA fallback
- `src/routes/+layout.js:10` -- `ssr = false` globally
- `src/app.html:26,34,35` -- `crossorigin="use-credentials"` on asset refs
- `src/routes/+layout.svelte:603-820` -- Root layout onMount with loading gate
- `src/routes/+layout.svelte:844-856` -- `{#if loaded}` template gate
- `src/routes/auth/invite/[token]/+page.svelte:33-53` -- Invite validation onMount
- `backend/open_webui/main.py:589-601` -- SPAStaticFiles class
- `backend/open_webui/main.py:1496-1502` -- CORS middleware config
- `backend/open_webui/main.py:2531-2566` -- Static file mounts
- `backend/open_webui/config.py:827-849` -- Static directory cleanup/copy on startup
- `backend/open_webui/utils/security_headers.py:16-59` -- Security headers (all opt-in)
- `backend/open_webui/env.py:480-482` -- Compression middleware enabled by default

### Deployment Layer (soev-gitops)
- `infrastructure/previder-prod/gateway/gateway.yaml` -- Cilium Gateway definition
- `infrastructure/previder-prod/gateway/httproute-gradient.yaml` -- HSTS header only
- `infrastructure/previder-prod/gateway/http-redirect.yaml` -- HTTP->HTTPS redirect
- `tenants/previder-prod/gradient/values-patch.yaml:21` -- CORS_ALLOW_ORIGIN per tenant
- `helm/open-webui-tenant/values.yaml:60` -- pullPolicy: Always
- `helm/open-webui-tenant/values.yaml:62-67` -- Recreate strategy, 1 replica
- `helm/open-webui-tenant/templates/open-webui/deployment.yaml:116-141` -- Probes

## Architecture Insights

### Why the SPA Architecture Makes This Worse

Open WebUI is a **pure client-side SPA** (SSR disabled). This means:
- The initial HTML response is always the same empty shell regardless of URL
- **ALL rendering** depends on JavaScript executing successfully
- If any JS bundle fails to load, the user sees only the splash screen (or raw HTML if the splash is also broken)
- There is no graceful degradation -- it's all-or-nothing

With SSR enabled, the server would render meaningful HTML that works even if JS fails. With `ssr = false`, any JS loading failure results in a completely broken page.

### The "Static HTML" Symptom Explained

When users report seeing "static HTML," this is likely the `app.html` shell with the splash screen -- or the splash screen not being removed because:
1. The JS bundle failed to load (network error, 404 from stale deployment)
2. The JS loaded but `getBackendConfig()` failed (backend not ready)
3. The JS loaded but hit an unhandled error during initialization

### The "Doesn't Render First Time" Symptom Explained

For invite links specifically, "doesn't render first time" could mean:
1. **During deployment**: Recreate strategy causes 30-300s downtime
2. **Cold start**: Backend needs time to initialize (load models, connect to DB)
3. **Race condition**: If the backend isn't fully ready when the first request arrives after pod startup, `GET /api/config` fails, and the user is redirected to `/error`
4. **On refresh**: The page works because the backend has warmed up

## Recommendations

### Quick Wins
1. **Switch to RollingUpdate strategy** with `maxUnavailable: 0, maxSurge: 1` -- eliminates deployment downtime
2. **Add `Cache-Control` headers** for `/_app/immutable/*` assets (`max-age=31536000, immutable`)
3. **Remove `crossorigin="use-credentials"`** from static asset links in `app.html` (they're same-origin)
4. **Change `pullPolicy: Always` to `IfNotPresent`** and use specific image tags instead of mutable branch tags

### Longer-term Improvements
5. **Increase replica count to 2** for production tenants (gradient at minimum)
6. **Add a startup readiness signal** -- don't mark the pod as ready until the backend can serve `/api/config` and all static files
7. **Consider adding asset-specific Cache-Control** in the `SecurityHeadersMiddleware` or via a custom middleware that distinguishes between API and static responses
8. **Audit the missing `/static/loader.js` and `/static/custom.css`** references -- either add these files or remove the references from `app.html`

## Open Questions

1. Is the `CORS_ALLOW_ORIGIN` env var correctly set for all tenants? If any tenant is missing it, the default `*` + credentials would cause failures.
2. How long does the Open WebUI backend take to start up in production? The readiness probe's 30s initial delay suggests it's expected to be slow.
3. Are there any Cilium/Envoy timeout settings that could cause the gateway to give up before the backend responds?
4. Could the ONNX WASM files (`vite-plugin-static-copy` output) cause loading issues if they fail to load?
