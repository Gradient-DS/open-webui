# Agent Proxy Integration — Implementation Plan

## Overview

Expose the genai-utils agent API (OpenAI-compatible) through Open WebUI as a reverse proxy at `/api/v1/agent/...`. Users authenticate with their existing `sk-` API keys. An admin toggle + inline documentation lives in the Integrations settings tab.

This follows the same proxy pattern used by the Ollama and OpenAI routers — `aiohttp` session with `stream_wrapper` for SSE passthrough.

## Current State Analysis

- **Agent service** (`genai-utils/agents_updated`): FastAPI app with `GET /v1/models`, `POST /v1/chat/completions`, `GET /healthz`, `GET /openapi.json`. No auth, no CORS — designed as an internal backend service.
- **Existing Agent API** (`AGENT_API_ENABLED` in `env.py:873`): Environment-only config that routes OWUI's own chat middleware to the agent. **Not admin-configurable**, no frontend UI. This is a separate feature — the middleware bypass for OWUI's chat UI.
- **The proxy** we're building is different: it exposes the agent's OpenAI-compatible API directly to external callers (curl, SDKs, other services) who authenticate with `sk-` keys.
- **Admin Integrations tab** (`Integrations.svelte`): Has Tool Servers, Terminal Servers, and Integration Providers sections. Uses `isFeatureEnabled()` gates and `PersistentConfig` for DB persistence.

### Key Discoveries:
- Ollama/OpenAI proxies use `aiohttp` + `stream_wrapper()` from `utils/misc.py:875` for streaming — we follow this pattern
- `PersistentConfig` (config.py:171) handles env-var-default + DB-persistence + Redis sync
- Config GET/POST endpoints in `configs.py` use `get_admin_user` dependency
- Feature flags exposed to frontend via `/api/config` features dict in `main.py:2440+`
- `get_verified_user` (auth.py:415) validates JWT or `sk-` API key — handles both browser sessions and external API callers

## Desired End State

1. Admin can toggle "Agent Proxy" on/off in Settings > Integrations
2. Admin can configure the internal agent service URL
3. When enabled, authenticated users can call:
   - `GET /api/v1/agent/models` — list available agents
   - `POST /api/v1/agent/chat/completions` — chat with streaming SSE
   - `GET /api/v1/agent/openapi.json` — fetch agent's OpenAPI spec
4. Inline documentation in the admin panel shows curl examples
5. When disabled, all proxy endpoints return 503

### Verification:
```bash
# With proxy enabled and agent stack running:
curl -H "Authorization: Bearer sk-..." https://octobox.soev.ai/api/v1/agent/models
curl -H "Authorization: Bearer sk-..." \
  -H "Content-Type: application/json" \
  -d '{"model":"open-webui-clone","messages":[{"role":"user","content":"Hello"}],"stream":true}' \
  https://octobox.soev.ai/api/v1/agent/chat/completions
```

## What We're NOT Doing

- Not adding auth to the agent service itself (stays auth-free, called by trusted infra)
- Not modifying the existing `AGENT_API_ENABLED` middleware bypass — that's a separate feature
- Not adding per-user rate limiting (can be added later)
- Not adding model filtering per-tenant (can be added later)
- Not adding gateway/HTTPRoute changes (proxy routes through existing OWUI domain)

## Implementation Approach

Follow the Ollama/OpenAI proxy pattern: `aiohttp` session → `stream_wrapper` → `StreamingResponse`. Config via `PersistentConfig` with admin API in `configs.py`. Frontend section in `Integrations.svelte` with Switch toggle.

---

## Phase 1: Backend — Config & Proxy Router

### Overview
Add `PersistentConfig` variables, create the proxy router, add config endpoints, mount in `main.py`.

### Changes Required:

#### 1. Config definitions
**File**: `backend/open_webui/config.py`
**Changes**: Add `ENABLE_AGENT_PROXY` and `AGENT_PROXY_BASE_URL` after the `INTEGRATION_PROVIDERS` block (~line 3488).

```python
####################################
# Agent Proxy
####################################

ENABLE_AGENT_PROXY = PersistentConfig(
    "ENABLE_AGENT_PROXY",
    "agent_proxy.enable",
    os.environ.get("ENABLE_AGENT_PROXY", "False").lower() == "true",
)

AGENT_PROXY_BASE_URL = PersistentConfig(
    "AGENT_PROXY_BASE_URL",
    "agent_proxy.base_url",
    os.environ.get("AGENT_PROXY_BASE_URL", ""),
)
```

#### 2. Wire config to app state
**File**: `backend/open_webui/main.py`
**Changes**: Import the new configs and assign to `app.state.config` (after `INTEGRATION_PROVIDERS` at line 1270). Also expose `enable_agent_proxy` in the features dict (~line 2480).

```python
# After line 1270:
from open_webui.config import ENABLE_AGENT_PROXY, AGENT_PROXY_BASE_URL

app.state.config.ENABLE_AGENT_PROXY = ENABLE_AGENT_PROXY
app.state.config.AGENT_PROXY_BASE_URL = AGENT_PROXY_BASE_URL
```

In the features dict (after `enable_email_invites` at line 2480):
```python
"enable_agent_proxy": app.state.config.ENABLE_AGENT_PROXY,
```

Mount the router (after integrations at line 1771):
```python
from open_webui.routers import agent_proxy

app.include_router(
    agent_proxy.router, prefix="/api/v1/agent", tags=["agent-proxy"]
)
```

#### 3. Config API endpoints
**File**: `backend/open_webui/routers/configs.py`
**Changes**: Add GET/POST endpoints for agent proxy config (after the integrations endpoints at line 789).

```python
class AgentProxyConfigForm(BaseModel):
    ENABLE_AGENT_PROXY: bool
    AGENT_PROXY_BASE_URL: str


@router.get("/agent_proxy")
async def get_agent_proxy_config(request: Request, user=Depends(get_admin_user)):
    return {
        "ENABLE_AGENT_PROXY": request.app.state.config.ENABLE_AGENT_PROXY,
        "AGENT_PROXY_BASE_URL": request.app.state.config.AGENT_PROXY_BASE_URL,
    }


@router.post("/agent_proxy")
async def set_agent_proxy_config(
    request: Request,
    form_data: AgentProxyConfigForm,
    user=Depends(get_admin_user),
):
    request.app.state.config.ENABLE_AGENT_PROXY = form_data.ENABLE_AGENT_PROXY
    request.app.state.config.AGENT_PROXY_BASE_URL = form_data.AGENT_PROXY_BASE_URL
    return {
        "ENABLE_AGENT_PROXY": request.app.state.config.ENABLE_AGENT_PROXY,
        "AGENT_PROXY_BASE_URL": request.app.state.config.AGENT_PROXY_BASE_URL,
    }
```

#### 4. Proxy router
**File**: `backend/open_webui/routers/agent_proxy.py` (new file)
**Changes**: Create the proxy router with three endpoints. Uses `aiohttp` + `stream_wrapper` matching the Ollama/OpenAI pattern.

```python
"""
Agent Proxy — reverse-proxy to an external agent service.

[Gradient] Exposes the agent service's OpenAI-compatible API to users
authenticated with OWUI API keys. The agent service itself has no auth;
OWUI owns auth, the agent service owns inference.

Endpoints:
    GET  /models           → agent /v1/models
    POST /chat/completions → agent /v1/chat/completions (streaming SSE)
    GET  /openapi.json     → agent /openapi.json
"""

import logging

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.responses import StreamingResponse

from open_webui.utils.auth import get_verified_user
from open_webui.utils.misc import stream_wrapper

log = logging.getLogger(__name__)

router = APIRouter()

AIOHTTP_CLIENT_TIMEOUT = aiohttp.ClientTimeout(total=300)


def _get_base_url(request: Request) -> str:
    """Return the configured agent proxy base URL or raise 503."""
    if not request.app.state.config.ENABLE_AGENT_PROXY:
        raise HTTPException(status_code=503, detail="Agent Proxy is disabled")

    base_url = request.app.state.config.AGENT_PROXY_BASE_URL
    if not base_url:
        raise HTTPException(
            status_code=503, detail="Agent Proxy base URL is not configured"
        )
    return base_url.rstrip("/")


@router.get("/models")
async def list_models(request: Request, user=Depends(get_verified_user)):
    """Proxy GET /v1/models from the agent service."""
    base_url = _get_base_url(request)

    session = aiohttp.ClientSession(
        trust_env=True, timeout=AIOHTTP_CLIENT_TIMEOUT
    )
    try:
        response = await session.request(
            method="GET",
            url=f"{base_url}/v1/models",
        )
        if response.status >= 400:
            body = await response.text()
            raise HTTPException(status_code=response.status, detail=body)
        data = await response.json()
        return data
    finally:
        await session.close()


@router.post("/chat/completions")
async def chat_completions(request: Request, user=Depends(get_verified_user)):
    """Proxy POST /v1/chat/completions to the agent service with SSE streaming."""
    base_url = _get_base_url(request)

    payload = await request.body()

    session = aiohttp.ClientSession(
        trust_env=True, timeout=AIOHTTP_CLIENT_TIMEOUT
    )
    try:
        response = await session.request(
            method="POST",
            url=f"{base_url}/v1/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json"},
        )

        if response.status >= 400:
            body = await response.text()
            await session.close()
            raise HTTPException(status_code=response.status, detail=body)

        content_type = response.headers.get("Content-Type", "")

        if "text/event-stream" in content_type:
            return StreamingResponse(
                stream_wrapper(response, session),
                media_type="text/event-stream",
            )
        else:
            data = await response.json()
            await session.close()
            return data

    except HTTPException:
        raise
    except Exception as e:
        await session.close()
        log.error(f"Agent proxy error: {e}")
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/openapi.json")
async def openapi_spec(request: Request, user=Depends(get_verified_user)):
    """Proxy the agent service's OpenAPI spec."""
    base_url = _get_base_url(request)

    session = aiohttp.ClientSession(
        trust_env=True, timeout=AIOHTTP_CLIENT_TIMEOUT
    )
    try:
        response = await session.request(
            method="GET",
            url=f"{base_url}/openapi.json",
        )
        if response.status >= 400:
            body = await response.text()
            raise HTTPException(status_code=response.status, detail=body)
        data = await response.json()
        return data
    finally:
        await session.close()
```

### Success Criteria:

#### Automated Verification:
- [ ] Backend starts without errors: `open-webui dev`
- [x] Black formatting passes: `npm run format:backend`
- [ ] New config endpoints respond: `curl localhost:8080/api/v1/configs/agent_proxy` (requires admin token)
- [ ] Proxy returns 503 when disabled: `curl localhost:8080/api/v1/agent/models`
- [x] Build succeeds: `npm run build`

#### Manual Verification:
- [ ] With `ENABLE_AGENT_PROXY=true` and a running agent service, `GET /api/v1/agent/models` returns the model list
- [ ] `POST /api/v1/agent/chat/completions` streams SSE responses correctly
- [ ] `GET /api/v1/agent/openapi.json` returns the spec
- [ ] Authentication rejects requests without a valid `sk-` key or JWT

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation that the proxy works end-to-end before proceeding to Phase 2.

---

## Phase 2: Frontend — Admin UI in Integrations Tab

### Overview
Add the Agent Proxy section to the Integrations admin settings with an enable/disable toggle, URL input, and inline documentation with curl examples.

### Changes Required:

#### 1. Frontend API client functions
**File**: `src/lib/apis/configs/index.ts`
**Changes**: Add `getAgentProxyConfig` and `setAgentProxyConfig` after the integrations functions (after line 760).

```typescript
export const getAgentProxyConfig = async (token: string) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/agent_proxy`, {
		method: 'GET',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const setAgentProxyConfig = async (token: string, config: object) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/agent_proxy`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify(config)
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};
```

#### 2. Integrations tab — Agent Proxy section
**File**: `src/lib/components/admin/Settings/Integrations.svelte`
**Changes**: Add Agent Proxy section between the Terminal Servers and Integration Providers sections. Import the new API functions, add state variables, load config on mount, save on toggle/input change.

Add to the script block imports:
```typescript
import {
    getToolServerConnections,
    setToolServerConnections,
    getTerminalServerConnections,
    setTerminalServerConnections,
    getAgentProxyConfig,
    setAgentProxyConfig
} from '$lib/apis/configs';

import { config } from '$lib/stores';
```

Add state variables:
```typescript
let ENABLE_AGENT_PROXY = false;
let AGENT_PROXY_BASE_URL = '';
let showAgentDocs = false;
```

Add to `onMount`:
```typescript
// Load agent proxy config
try {
    const agentRes = await getAgentProxyConfig(localStorage.token);
    if (agentRes) {
        ENABLE_AGENT_PROXY = agentRes.ENABLE_AGENT_PROXY ?? false;
        AGENT_PROXY_BASE_URL = agentRes.AGENT_PROXY_BASE_URL ?? '';
    }
} catch {
    // Not configured yet
}
```

Add save handler:
```typescript
const saveAgentProxyConfig = async () => {
    const res = await setAgentProxyConfig(localStorage.token, {
        ENABLE_AGENT_PROXY,
        AGENT_PROXY_BASE_URL
    }).catch((err) => {
        toast.error($i18n.t('Failed to save Agent Proxy settings'));
        return null;
    });

    if (res) {
        toast.success($i18n.t('Agent Proxy settings saved'));
    }
};
```

Add template section (after the Terminal Servers `{/if}` at line 332, before the `<hr>` at line 335):

```svelte
{#if $user?.role === 'admin'}
<div class="mb-2.5 flex flex-col w-full">
    <div class="flex justify-between items-center mb-1">
        <div class="flex items-center gap-2">
            <div class="font-medium">{$i18n.t('Agent Proxy')}</div>
        </div>

        <Tooltip
            content={ENABLE_AGENT_PROXY
                ? $i18n.t('Enabled')
                : $i18n.t('Disabled')}
        >
            <Switch
                bind:state={ENABLE_AGENT_PROXY}
                on:change={() => {
                    saveAgentProxyConfig();
                }}
            />
        </Tooltip>
    </div>

    {#if ENABLE_AGENT_PROXY}
        <div class="flex flex-col gap-2 mt-1">
            <div>
                <div class="text-xs font-medium mb-1">{$i18n.t('Agent Service URL')}</div>
                <input
                    class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
                    type="text"
                    placeholder="http://agent-service:8080"
                    bind:value={AGENT_PROXY_BASE_URL}
                    on:change={() => {
                        saveAgentProxyConfig();
                    }}
                />
            </div>

            <div class="text-xs text-gray-500">
                {$i18n.t('Proxy requests to an external OpenAI-compatible agent service. Users authenticate with their API keys.')}
            </div>

            <div class="mt-1">
                <button
                    class="text-xs underline text-gray-600 dark:text-gray-300"
                    type="button"
                    on:click={() => { showAgentDocs = !showAgentDocs; }}
                >
                    {showAgentDocs ? $i18n.t('Hide API documentation') : $i18n.t('Show API documentation')}
                </button>

                {#if showAgentDocs}
                    <div class="mt-2 p-3 bg-gray-50 dark:bg-gray-850 rounded-lg text-xs font-mono space-y-3">
                        <div>
                            <div class="font-semibold text-gray-700 dark:text-gray-300 mb-1">{$i18n.t('List available models')}</div>
                            <pre class="whitespace-pre-wrap text-gray-600 dark:text-gray-400">curl -H "Authorization: Bearer sk-..." \
  {window.location.origin}/api/v1/agent/models</pre>
                        </div>

                        <div>
                            <div class="font-semibold text-gray-700 dark:text-gray-300 mb-1">{$i18n.t('Chat completions (streaming)')}</div>
                            <pre class="whitespace-pre-wrap text-gray-600 dark:text-gray-400">curl -H "Authorization: Bearer sk-..." \
  -H "Content-Type: application/json" \
  -d '{JSON.stringify({model: "agent-name", messages: [{role: "user", content: "Hello"}], stream: true})}' \
  {window.location.origin}/api/v1/agent/chat/completions</pre>
                        </div>

                        <div>
                            <div class="font-semibold text-gray-700 dark:text-gray-300 mb-1">{$i18n.t('OpenAPI specification')}</div>
                            <pre class="whitespace-pre-wrap text-gray-600 dark:text-gray-400">curl -H "Authorization: Bearer sk-..." \
  {window.location.origin}/api/v1/agent/openapi.json</pre>
                        </div>
                    </div>
                {/if}
            </div>
        </div>
    {/if}
</div>

<hr class=" border-gray-100/30 dark:border-gray-850/30 my-4" />
{/if}
```

#### 3. i18n keys
**File**: `src/lib/i18n/locales/en-US/translation.json`
**Changes**: Add translation keys (alphabetically sorted):

```json
"Agent Proxy": "",
"Agent Service URL": "",
"Chat completions (streaming)": "",
"Hide API documentation": "",
"List available models": "",
"OpenAPI specification": "",
"Proxy requests to an external OpenAI-compatible agent service. Users authenticate with their API keys.": "",
"Show API documentation": "",
"Failed to save Agent Proxy settings": "",
"Agent Proxy settings saved": ""
```

### Success Criteria:

#### Automated Verification:
- [x] Frontend builds without errors: `npm run build`
- [x] Backend formatting passes: `npm run format:backend`

#### Manual Verification:
- [ ] Agent Proxy section appears in Admin > Settings > Integrations
- [ ] Toggle turns the proxy on/off and persists across page reloads
- [ ] URL input saves and persists
- [ ] "Show API documentation" expands with correct curl examples using the current domain
- [ ] When disabled, the toggle hides the URL input and documentation
- [ ] Non-admin users do not see the Agent Proxy section
- [ ] End-to-end: toggle on, enter agent URL, use curl with `sk-` key to hit `/api/v1/agent/models`

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation that the admin UI and proxy work end-to-end.

---

## Testing Strategy

### Manual Testing Steps:
1. Start backend with `open-webui dev`, frontend with `npm run dev`
2. Log in as admin, go to Settings > Integrations
3. Verify Agent Proxy section with toggle (default: off)
4. Toggle on, enter agent service URL (e.g., `http://localhost:8080` for a local agent)
5. Verify config persists after page reload
6. Generate an API key in Settings > Account
7. Test `curl -H "Authorization: Bearer sk-..." localhost:5173/api/v1/agent/models`
8. Test streaming chat completion with curl
9. Toggle off, verify curl returns 503
10. Verify non-admin user cannot see the Agent Proxy section

### Edge Cases:
- Agent service is down → proxy should return 502/503 with clear error
- Invalid URL configured → should fail gracefully
- Empty URL with toggle on → returns 503 "base URL not configured"
- Unauthenticated request → returns 401
- Non-streaming response from agent → returned as JSON

## Performance Considerations

- The proxy adds one extra network hop (negligible for SSE streaming)
- `aiohttp` session is created per-request (matching Ollama/OpenAI pattern) — no connection pooling. Acceptable for the expected request volume.
- No request body parsing for `chat/completions` — raw bytes are forwarded directly

## References

- Research document: provided as input to this plan
- Ollama proxy pattern: `backend/open_webui/routers/ollama.py:108-185` (`send_post_request` + `stream_wrapper`)
- OpenAI proxy pattern: `backend/open_webui/routers/openai.py:937-1136`
- `stream_wrapper`: `backend/open_webui/utils/misc.py:875-887`
- Existing Agent API client: `backend/open_webui/utils/agent.py` (separate feature — middleware bypass)
- Config pattern: `backend/open_webui/config.py:171-228` (`PersistentConfig`)
- Admin config API pattern: `backend/open_webui/routers/configs.py:755-789` (integrations)
- Integrations tab: `src/lib/components/admin/Settings/Integrations.svelte`
- Connections toggle pattern: `src/lib/components/admin/Settings/Connections.svelte:43-107`
- Frontend config API: `src/lib/apis/configs/index.ts:707-760`
- Push ingest integration plan: `thoughts/shared/plans/2026-03-15-push-ingest-integration.md`
