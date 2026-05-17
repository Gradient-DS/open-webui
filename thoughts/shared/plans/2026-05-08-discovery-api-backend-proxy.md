# Discovery API Backend Proxy Implementation Plan

## Overview

Move the RAG filter panel's discovery API call out of a Caddy layer-7 reverse proxy and into the open-webui FastAPI backend. The frontend will call a same-origin authed route (`/api/v1/discovery/documents`); the backend proxies to the upstream search-api with a server-side `X-API-Key`. The `PUBLIC_RAG_API_KEY` is removed from the browser bundle, the unauth `/discovery/*` Caddy block becomes obsolete (deployment-side cleanup, not in this plan), and access is gated by the same session/JWT used by the rest of the app.

## Current State Analysis

**Frontend (broken/leaky):**
- `src/lib/apis/rag/index.ts:13-14` declares `PUBLIC_RAG_API_BASE_URL` and `PUBLIC_RAG_API_KEY`. Both ship to the browser bundle (SvelteKit's `PUBLIC_*` convention). The "key" is fake in production because Caddy server-side overrides the `X-API-Key` header, but the URL still points the browser at `https://neo.soev.ai/discovery/...`.
- `src/lib/apis/rag/index.ts:46-93` `getCollectionsAndDocuments()` is the only consumer used in production. Called from `src/lib/components/chat/RagFilter.svelte:103`.
- `src/lib/apis/rag/index.ts:100-115` `checkRagFilterAvailability()` is exported but unreferenced — dead code.
- These two `PUBLIC_*` vars appear nowhere else in the repo (`.env.example`, helm, vite config all clean).

**Caddy (deployment-side, out of repo):**
- `neo.soev.ai` Caddyfile has `handle /discovery/*` that reverse-proxies to `http://neo-db:3535` over Tailscale and injects `X-API-Key: $SEARCH_API_KEY`. No auth check — anyone resolving `neo.soev.ai/discovery/documents` reads the full document index. This Caddyfile lives in the neo-web ops repo, not here.

**Backend (no entry point yet):**
- No router currently proxies to the search-api. `backend/open_webui/routers/agent_proxy.py` is the close-fit template: server-side env-driven base URL + key, `Depends(get_verified_user)` on every route, mounted unconditionally with feature-flag enforcement at request time, typed 502s on upstream failures.
- Feature-flag exposure to the frontend already exists: `backend/open_webui/main.py:2609` exposes `enable_rag_filter_ui` from `app.state.config.ENABLE_RAG_FILTER_UI` (declared at `config.py:3181-3185`).

## Desired End State

A user opening the RAG filter panel hits `/api/v1/discovery/documents` (same origin, session cookie). The FastAPI handler validates the user, calls the upstream search-api at `${SEARCH_API_BASE_URL}/discovery/documents` with `X-API-Key: ${SEARCH_API_KEY}`, returns the JSON. The browser bundle contains no API key, no upstream URL. The Caddyfile patch on neo-web is obsolete (cleanup out of scope here).

**Verification:**
- `curl -i https://neo.soev.ai/discovery/documents` returns 404/302 (no longer matched by Caddy → falls through to open-webui's catch-all → 404 from the SPA route).
- `curl -i https://neo.soev.ai/api/v1/discovery/documents` without session cookie → 401.
- Same URL with a valid session cookie → 200 with `{collections: [...]}`.
- Browser DevTools → Network tab on the filter panel shows the request to `/api/v1/discovery/documents` with no `X-API-Key` header.
- `grep -r "PUBLIC_RAG_API" src/` returns nothing.

### Key Discoveries:

- **Pattern to mirror in full:** `backend/open_webui/routers/agent_proxy.py:142-238` (`_get_base_url`, `_auth_headers`, `_proxy_get_json`). Same upstream-error handling, same 503-on-misconfig, same route-level `Depends(get_verified_user)`.
- **Env-var location:** `backend/open_webui/env.py:787-802` is the `AGENT API` block — read-once module-level constants with `.strip().rstrip('/')` URL normalization. Mirror this for `SEARCH_API_BASE_URL` + `SEARCH_API_KEY`. No `PersistentConfig` — these are deployment-static.
- **Single-flag reuse:** `ENABLE_RAG_FILTER_UI` (`config.py:3181`) already gates the frontend panel; we reuse it as the backend gate too. No new flag, no `/api/config` change.
- **Mount style:** `main.py:1949` mounts `agent_proxy` unconditionally at `/api/v1/agent`; flag check is request-time inside `_get_base_url()`. Mirror with `/api/v1/discovery`.
- **Frontend consumer footprint is tiny:** only `RagFilter.svelte:103` calls `getCollectionsAndDocuments()`. Keeping the function's `Promise<RagDiscoveryResponse | null>` contract means zero changes to the consumer.
- **Helm wiring template is fully present:** `helm/open-webui-tenant/values.yaml:459-475` (config), `:773` (secrets), `:837` (externalSecrets), `templates/open-webui/configmap.yaml:448-461`, `templates/open-webui/deployment.yaml:153-164` (secretKeyRef), `templates/secrets.yaml:46-49`, `templates/external-secrets.yaml:73-77`. Mirror each line for `searchApi.baseUrl` + `searchApiKey`.

## What We're NOT Doing

- **Caddyfile / neo-web deployment changes.** The `handle /discovery/*` block can stay until the open-webui image rolls; once the new path is live, dropping it is a separate ops PR in the neo-web repo.
- **Citation `document_id` → external URL mapping.** The `/api/v1/files/{uuid}/content` issue from chat citations is a separate bug; tracked elsewhere.
- **Runtime admin toggle for the proxy.** No PersistentConfig for `SEARCH_API_*` — flipping requires pod restart, same as `AGENT_API_*`.
- **OpenAPI / collections-only / passthrough routes.** Single explicit `/documents` route, no wildcard. New routes are trivial to add later if the search-api grows.
- **Removing `enable_rag_filter_ui` from `/api/config`.** Frontend already reads it; backend gate piggybacks on the same field.

## Implementation Approach

Land the backend first so the frontend has somewhere to call. Frontend switch is one file. Helm wiring is mechanical, last. Each phase is independently mergeable and individually testable; all three together is what unblocks the deployment cleanup.

---

## Phase 1: Backend proxy router

### Overview
Add `backend/open_webui/routers/discovery.py` modelled on `agent_proxy.py`, declare `SEARCH_API_BASE_URL` + `SEARCH_API_KEY` in `env.py`, mount the router at `/api/v1/discovery`. Reuse `ENABLE_RAG_FILTER_UI` as the gate.

### Changes Required:

#### 1. Env-var declarations

**File:** `backend/open_webui/env.py`
**Changes:** Append a new block after the `AGENT API` block (current end at line 802). Module-level read-once, mirroring `AGENT_API_BASE_URL` / `AGENT_API_KEY`.

```python
####################################
# SEARCH API (RAG discovery proxy)
####################################

SEARCH_API_BASE_URL = os.environ.get('SEARCH_API_BASE_URL', '').strip().rstrip('/')
SEARCH_API_KEY = os.environ.get('SEARCH_API_KEY', '').strip()
```

#### 2. New router

**File:** `backend/open_webui/routers/discovery.py` (new)
**Changes:** Single GET route at `/documents`. Pattern lifted directly from `agent_proxy.py:_get_base_url / _auth_headers / _proxy_get_json`.

```python
"""Discovery proxy — reverse-proxy to the upstream search-api.

[Gradient] Exposes the search-api's /discovery/documents to OWUI users
authenticated by session cookie / JWT. The search-api lives behind a
Tailscale tunnel and uses an X-API-Key header that never leaves the
backend. Mirrors the agent_proxy pattern: route-level get_verified_user,
flag + URL check at request time, typed 502s on upstream failure.
"""

import asyncio
import json
import logging

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Request

from open_webui.env import SEARCH_API_BASE_URL, SEARCH_API_KEY
from open_webui.utils.auth import get_verified_user

log = logging.getLogger(__name__)

router = APIRouter()

AIOHTTP_CLIENT_TIMEOUT = aiohttp.ClientTimeout(total=30)


def _get_base_url(request: Request) -> str:
    """Return the configured search-api URL or raise 503.

    Reuses ENABLE_RAG_FILTER_UI — the same flag that controls panel
    visibility on the frontend — so a single Helm toggle gates the whole
    feature. SEARCH_API_BASE_URL must also be set; an empty string is a
    misconfig, not a runtime toggle.
    """
    if not request.app.state.config.ENABLE_RAG_FILTER_UI:
        raise HTTPException(
            status_code=503,
            detail=(
                'RAG filter UI is disabled. Set ENABLE_RAG_FILTER_UI=true '
                '(Helm: openWebui.config.enableRagFilterUi) and restart the OWUI pod.'
            ),
        )
    if not SEARCH_API_BASE_URL:
        raise HTTPException(
            status_code=503,
            detail=(
                'SEARCH_API_BASE_URL is not configured. Set it to the search-api URL '
                '(e.g. http://neo-db:3535) via Helm openWebui.config.searchApi.baseUrl '
                'and restart the OWUI pod.'
            ),
        )
    return SEARCH_API_BASE_URL


def _auth_headers() -> dict[str, str]:
    """Build outbound headers, including X-API-Key when configured."""
    headers: dict[str, str] = {}
    if SEARCH_API_KEY:
        headers['X-API-Key'] = SEARCH_API_KEY
    return headers


async def _proxy_get_json(base_url: str, path: str):
    """GET {base_url}{path} upstream and return parsed JSON.

    Same shape as agent_proxy._proxy_get_json — typed 502s for
    connection / timeout / decode failures, status-passthrough for
    upstream non-2xx so the operator sees the real error.
    """
    session = aiohttp.ClientSession(trust_env=True, timeout=AIOHTTP_CLIENT_TIMEOUT)
    try:
        try:
            response = await session.request(
                method='GET',
                url=f'{base_url}{path}',
                headers=_auth_headers(),
            )
        except aiohttp.ClientConnectorError as e:
            raise HTTPException(
                status_code=502,
                detail=(
                    f'Cannot reach the search-api at {base_url}{path}: {e}. '
                    'Check SEARCH_API_BASE_URL and that the upstream is reachable '
                    '(Tailscale tunnel up, target host listening).'
                ),
            )
        except asyncio.TimeoutError:
            raise HTTPException(
                status_code=504,
                detail=(
                    f'Timed out calling the search-api at {base_url}{path} '
                    f'(>{int(AIOHTTP_CLIENT_TIMEOUT.total or 0)}s).'
                ),
            )
        except aiohttp.ClientError as e:
            raise HTTPException(
                status_code=502,
                detail=f'Upstream search-api error on GET {path}: {type(e).__name__}: {e}',
            )

        if response.status == 401:
            raise HTTPException(
                status_code=502,
                detail=(
                    'search-api returned 401 — SEARCH_API_KEY is wrong or unset. '
                    'Check the secret value in the OWUI pod and on the search-api side.'
                ),
            )
        if response.status >= 400:
            body = await response.text()
            raise HTTPException(
                status_code=response.status,
                detail=(
                    f'search-api returned {response.status} on GET {path}. '
                    f'Upstream body: {body[:500] or "<empty>"}'
                ),
            )

        try:
            return await response.json()
        except (aiohttp.ContentTypeError, json.JSONDecodeError) as e:
            body_preview = (await response.text())[:500]
            raise HTTPException(
                status_code=502,
                detail=(
                    f'search-api at {path} returned non-JSON body. '
                    f'Body preview: {body_preview!r}. Original error: {e}'
                ),
            )
    finally:
        await session.close()


@router.get('/documents')
async def list_documents(request: Request, user=Depends(get_verified_user)):
    """Proxy GET /discovery/documents from the upstream search-api."""
    base_url = _get_base_url(request)
    return await _proxy_get_json(base_url, '/discovery/documents')
```

#### 3. Mount the router

**File:** `backend/open_webui/main.py`
**Changes:** Add `discovery` to the routers tuple (line 73 area, alongside `agent_proxy`), then `include_router` at `:1949` neighbourhood, mirroring agent_proxy.

```python
# in the routers tuple at the top of main.py (alongside agent_proxy)
from open_webui.routers import (
    ...,
    agent_proxy,
    discovery,
    ...,
)

# alongside the agent_proxy include_router
app.include_router(discovery.router, prefix='/api/v1/discovery', tags=['discovery'])
```

No `app.state.config.SEARCH_*` wiring (we reuse `ENABLE_RAG_FILTER_UI` which is already wired). No `/api/config` payload change.

### Success Criteria:

#### Automated Verification:

- [x] Backend lints: `npm run lint:backend` (pylint E/F clean on new files)
- [x] Backend formats: `npm run format:backend` (no diff)
- [x] Backend imports cleanly: `python -c "from open_webui.routers import discovery; from open_webui.env import SEARCH_API_BASE_URL, SEARCH_API_KEY"`
- [x] FastAPI app boots with router mounted: `python -c "from open_webui.main import app; assert any(getattr(r, 'path', '') == '/api/v1/discovery/documents' for r in app.routes)"`
- [x] New unit test passes: `pytest backend/open_webui/test/util/test_discovery_proxy.py` — 8/8 covering (a) 503 when `ENABLE_RAG_FILTER_UI` false, (b) 503 when `SEARCH_API_BASE_URL` empty, (c) `X-API-Key` injected on outbound call (and absent when key unset), (d) 401 from upstream remapped to 502 with operator-readable message, (e) connection errors mapped to 502, (f) non-JSON upstream mapped to 502, (g) `Depends(get_verified_user)` rejects unauthenticated requests.

#### Manual Verification:

- [ ] With `SEARCH_API_BASE_URL` + `SEARCH_API_KEY` + `ENABLE_RAG_FILTER_UI=true` set, run `open-webui dev` and `curl -H "Cookie: ..." http://localhost:8080/api/v1/discovery/documents` returns the search-api's payload.
- [ ] Same `curl` without the cookie returns 401 (FastAPI's standard auth error).
- [ ] With `ENABLE_RAG_FILTER_UI=false` (or env unset), authed `curl` returns 503 with the operator-readable detail.
- [ ] Server logs show no plaintext `SEARCH_API_KEY`.

**Implementation Note:** After Phase 1 the frontend still hits `/discovery/documents` (the old Caddy path). Both paths will work in parallel until Phase 2 lands; do not drop the Caddyfile block yet. After this phase passes automated and manual verification, pause for confirmation before starting Phase 2.

---

## Phase 2: Frontend client switch

### Overview
Rewrite `src/lib/apis/rag/index.ts` to call the new same-origin route. Drop `PUBLIC_RAG_*` env-var dependencies and `X-API-Key` headers. Delete unused `checkRagFilterAvailability`. `RagFilter.svelte` requires no changes — the function's `Promise<RagDiscoveryResponse | null>` contract is preserved.

### Changes Required:

#### 1. Rewrite the API client

**File:** `src/lib/apis/rag/index.ts`
**Changes:** Full rewrite. Drop `env` import, drop `PUBLIC_*` vars, drop `X-API-Key`, drop `checkRagFilterAvailability`, point at `/api/v1/discovery/documents`.

```typescript
/**
 * RAG Filter API — Collection and Document Discovery.
 *
 * Calls the open-webui backend's discovery proxy (/api/v1/discovery/*).
 * Same-origin, session-cookie authed; the upstream X-API-Key is injected
 * server-side and never leaves the backend.
 */

import { WEBUI_API_BASE_URL } from '$lib/constants';

export interface RagDocument {
	id: string;
	title: string;
	contentsubtype?: string;
}

export interface RagCollection {
	collection_key: string;
	collection_name: string;
	schema_name: string;
	document_count: number;
	documents: RagDocument[];
	error?: string;
}

export interface RagDiscoveryResponse {
	collections: RagCollection[];
	total_collections: number;
	database: {
		name: string;
		display_name: string;
	};
}

/**
 * Fetch all collections and their documents via the backend discovery proxy.
 * Returns null on any error (caller toasts).
 */
export const getCollectionsAndDocuments = async (
	token?: string
): Promise<RagDiscoveryResponse | null> => {
	const headers: Record<string, string> = {
		Accept: 'application/json',
		'Content-Type': 'application/json'
	};
	if (token) {
		headers.Authorization = `Bearer ${token}`;
	}

	try {
		const res = await fetch(`${WEBUI_API_BASE_URL}/discovery/documents`, {
			method: 'GET',
			credentials: 'include',
			headers
		});

		if (!res.ok) {
			const body = await res.json().catch(() => ({}));
			console.error('[RAG API]', res.status, body);
			return null;
		}

		const data = (await res.json()) as RagDiscoveryResponse;

		// Normalize doc_id → id, same as before.
		if (data?.collections && Array.isArray(data.collections)) {
			data.collections = data.collections.map((collection: RagCollection) => ({
				...collection,
				documents: (collection.documents ?? []).map((doc: any) => ({
					...doc,
					id: doc?.id ?? doc?.doc_id ?? doc?.original_doc_id ?? doc?.title
				}))
			}));
		}

		return data;
	} catch (err) {
		console.error('[RAG API]', err);
		return null;
	}
};
```

`WEBUI_API_BASE_URL` from `$lib/constants` is the standard same-origin prefix used by every other API client (e.g., `src/lib/apis/configs/index.ts`). It resolves to `/api/v1` so the final URL is `/api/v1/discovery/documents`.

#### 2. Caller untouched

**File:** `src/lib/components/chat/RagFilter.svelte`
**Changes:** None. The function still returns `Promise<RagDiscoveryResponse | null>`; the `data ? success : toast.error` branch at line 103-114 is unaffected. A future caller that needs to pass a token can do so; current callers don't.

#### 3. No new i18n strings needed

No user-facing text added — error toasts already exist in `RagFilter.svelte`. The `RAG Filters` button label was already added during the button-restoration work.

### Success Criteria:

#### Automated Verification:

- [x] Frontend lints: `npm run lint:frontend` (rag/ dir 3 → 1 errors, improved over baseline)
- [x] Frontend type-checks: `npm run check` (no new errors in rag/index.ts)
- [x] Frontend builds: `npm run build` (clean, no `your-dev-api-key-here` / `PUBLIC_RAG_API` in build/ or .svelte-kit/output/)
- [x] No remaining `PUBLIC_RAG_API` references: `grep -r "PUBLIC_RAG_API" src/` returns nothing
- [x] No remaining `/discovery/` absolute-URL references: `grep -rn "/discovery/" src/lib/apis/` returns only the new same-origin call

#### Manual Verification:

- [ ] With Phase 1 backend running locally + `ENABLE_RAG_FILTER_UI=true` + `SEARCH_API_BASE_URL` set: open chat → click RAG filter button → panel populates with collections.
- [ ] DevTools Network tab: the `documents` request goes to `http://localhost:5173/api/v1/discovery/documents` (or wherever frontend is served), with **no** `X-API-Key` header in the request, and the session cookie attached.
- [ ] DevTools Sources / built bundle: search the bundle for the placeholder `your-dev-api-key-here` — it's gone.
- [ ] With backend env vars unset: panel opens, toast shows "Failed to load RAG filter data" (the existing soft-failure path), no console errors thrown.

**Implementation Note:** Pause for confirmation after this phase. The browser bundle change is the security-relevant one — verify the key is gone before moving to Phase 3.

---

## Phase 3: Helm chart wiring

### Overview
Add `searchApi.baseUrl` (configmap, non-secret) and `searchApiKey` (secret, mounted via `secretKeyRef`, supported in both inline and 1Password external-secrets paths). Mirror the existing `agentApiBaseUrl` / `agentApiKey` wiring exactly.

### Changes Required:

#### 1. values.yaml

**File:** `helm/open-webui-tenant/values.yaml`
**Changes:** Three additions, mirroring agentApi locations.

```yaml
# Under openWebui.config (alongside agentApiBaseUrl at :459-461):
openWebui:
  config:
    # ...existing keys...
    searchApi:
      # Upstream search-api base URL (e.g. http://neo-db:3535). Empty disables the
      # discovery proxy. Backend reads this as SEARCH_API_BASE_URL.
      baseUrl: ""

# Under top-level secrets: (alongside agentApiKey at :773):
secrets:
  # ...existing keys...
  searchApiKey: ""

# Under externalSecrets.onepassword.fields (alongside agentApiKey at :837):
externalSecrets:
  onepassword:
    fields:
      # ...existing keys...
      searchApiKey: false
```

#### 2. configmap.yaml

**File:** `helm/open-webui-tenant/templates/open-webui/configmap.yaml`
**Changes:** Append a `SEARCH_API_BASE_URL` block alongside the agent block (~`:448-454`). Configmap, non-secret.

```yaml
{{- if .Values.openWebui.config.searchApi.baseUrl }}
# Note: SEARCH_API_KEY is set via secretKeyRef in deployment.yaml, not here.
SEARCH_API_BASE_URL: {{ .Values.openWebui.config.searchApi.baseUrl | quote }}
{{- end }}
```

No `ENABLE_DISCOVERY_PROXY` configmap entry — we reuse the existing `ENABLE_RAG_FILTER_UI` configmap line.

#### 3. deployment.yaml

**File:** `helm/open-webui-tenant/templates/open-webui/deployment.yaml`
**Changes:** Add a `SEARCH_API_KEY` env entry mirroring the `AGENT_API_KEY` block at `:153-164`.

```yaml
{{- if or .Values.secrets.searchApiKey .Values.externalSecrets.onepassword.fields.searchApiKey }}
- name: SEARCH_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "open-webui-tenant.fullname" . }}-secrets
      key: search-api-key
{{- end }}
```

#### 4. secrets.yaml

**File:** `helm/open-webui-tenant/templates/secrets.yaml`
**Changes:** Append `search-api-key:` entry alongside `agent-api-key:` (~`:46-49`).

```yaml
{{- if .Values.secrets.searchApiKey }}
search-api-key: {{ .Values.secrets.searchApiKey | b64enc | quote }}
{{- end }}
```

#### 5. external-secrets.yaml

**File:** `helm/open-webui-tenant/templates/external-secrets.yaml`
**Changes:** Add a 1Password remoteRef for `searchApiKey` mirroring `agentApiKey` at `:73-77`.

```yaml
{{- if .Values.externalSecrets.onepassword.fields.searchApiKey }}
- secretKey: search-api-key
  remoteRef:
    key: {{ .Values.externalSecrets.onepassword.itemName }}
    property: searchApiKey
{{- end }}
```

### Success Criteria:

#### Automated Verification:

- [x] Helm lints: `helm lint helm/open-webui-tenant`
- [x] Template renders with both paths empty: chart is inert (only one comment-only match for SEARCH_API_KEY, mirroring the same pattern used by AGENT_API_KEY).
- [x] Template renders with inline secret: shows `SEARCH_API_BASE_URL`, the `search-api-key` stringData entry, and the `SEARCH_API_KEY` secretKeyRef in deployment.
- [x] Template renders with 1Password path: external-secret remoteRef `tenant-secrets/searchApiKey`, deployment env block present, no inline stringData section (template gated by `if not .Values.externalSecrets.enabled`).
- [x] No mismatched key names: every `secretKeyRef.key: search-api-key` matches the same key in `secrets.yaml` / `external-secrets.yaml`.

#### Manual Verification:

- [ ] On the neo cluster (or a staging tenant): `helm upgrade ... --set openWebui.config.searchApi.baseUrl=http://neo-db:3535 --set externalSecrets.onepassword.fields.searchApiKey=true` rolls without errors.
- [ ] In the running pod: `kubectl exec ... -- env | grep SEARCH_API_BASE_URL` shows the configured URL; `... | grep SEARCH_API_KEY` shows a non-empty value.
- [ ] `kubectl exec ... -- printenv SEARCH_API_KEY` matches the 1Password value (or the inline value, depending on path).
- [ ] After rollout, the RAG filter panel populates in the running tenant.

**Implementation Note:** This phase is mechanical mirroring. If the agentApi pattern works for that tenant, the searchApi block should drop in cleanly — any deviation suggests a typo in the values path.

---

## Testing Strategy

### Unit Tests:
- New `backend/open_webui/test/util/test_discovery_proxy.py` covering: feature-flag gate (`ENABLE_RAG_FILTER_UI` false → 503), URL gate (`SEARCH_API_BASE_URL` empty → 503), `X-API-Key` injection on outbound calls (mock `aiohttp.ClientSession`), upstream-401 → 502 remapping, upstream-non-JSON → 502 remapping, auth dependency (no session → 401).
- Pattern reference: `backend/open_webui/test/util/test_pipeline_client.py` and similar files use the same aiohttp-mocking pattern.

### Integration Tests:
- The end-to-end loop is best validated manually against a real (or staged) search-api — mocking the upstream loses the real-world signal. Manual verification in each phase covers it.

### Manual Testing Steps:
1. Local: bring up `open-webui dev` with `ENABLE_RAG_FILTER_UI=true`, `SEARCH_API_BASE_URL` pointed at a reachable search-api (e.g., a port-forwarded staging instance or a docker-compose'd `genai-utils/api`).
2. Hit `/api/v1/discovery/documents` with no auth → 401.
3. Log in, hit it again → 200 with payload.
4. Open the chat UI, toggle the filter → panel populates.
5. Set `ENABLE_RAG_FILTER_UI=false`, restart, hit endpoint with auth → 503.
6. Set the flag back, unset `SEARCH_API_BASE_URL`, restart → 503 with the second-line detail message.
7. Inspect the production-built bundle: `grep -i "your-dev-api-key-here\|PUBLIC_RAG_API" build/` returns nothing.

## Performance Considerations

The proxy adds one network hop on top of the existing search-api call. The search-api responds in <500ms for the neo dataset (~1300 docs); the 30s aiohttp timeout has plenty of headroom. No streaming, no chunking — the discovery payload is small JSON.

If the panel is opened repeatedly, every open re-fetches. That's the same behaviour as today and is fine for the current use; if it becomes a hotspot, server-side caching at the FastAPI handler is a one-line `functools.lru_cache` away (out of scope).

## Migration Notes

No migration. The existing Caddy `/discovery/*` block can stay running in parallel until the open-webui image with this change is deployed; once it is, the block is unused and can be deleted in a follow-up neo-web ops PR. No DB schema changes, no PersistentConfig migration.

The `PUBLIC_RAG_API_BASE_URL` and `PUBLIC_RAG_API_KEY` env vars, if any deployment still sets them, become harmless (the new client ignores them). They can be cleaned out of any helm/env files as encountered.

## References

- Reference pattern: `backend/open_webui/routers/agent_proxy.py:142-238`
- Frontend caller (no change required): `src/lib/components/chat/RagFilter.svelte:99-121`
- Config exposure to FE: `backend/open_webui/main.py:2609`
- Feature flag declaration: `backend/open_webui/config.py:3181-3185`
- Helm secret/configmap mirror points: `helm/open-webui-tenant/values.yaml:459-475,773,837`, `templates/open-webui/configmap.yaml:448-461`, `templates/open-webui/deployment.yaml:153-164`, `templates/secrets.yaml:46-49`, `templates/external-secrets.yaml:73-77`
- Original ad-hoc Caddy patch (out-of-repo): neo-web ops repo, `Caddyfile` `handle /discovery/*` block — to be removed in a follow-up after this lands and rolls.
