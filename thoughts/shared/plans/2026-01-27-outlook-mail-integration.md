# Outlook Mail Integration Implementation Plan

## Overview

Implement Outlook mail integration as a feature toggle (like web search) in the chat input. When enabled, the LLM gains access to an `outlook_search` tool that can search the user's Outlook mailbox and return email content as context. This follows the existing feature handler pattern but injects tools instead of just context.

## Current State Analysis

### Existing Patterns Used
- **Web search feature handler**: `middleware.py:1327-1330` - context injection pattern
- **Microsoft OAuth**: `config.py:372-418` - already configured with `openid email profile` scopes
- **OneDrive Graph client**: `services/onedrive/graph_client.py` - pattern for MS Graph API calls
- **Feature toggles**: `MessageInput.svelte` + `Chat.svelte` - pattern for feature enable/disable
- **Tool injection**: `middleware.py:1517-1530` - pattern for adding tools to `tools_dict`

### Key Discoveries
- OAuth tokens accessible via `extra_params["__oauth_token__"]` in middleware
- Features object passed from frontend as `form_data["features"]`
- Tools can be dynamically injected into `tools_dict` before LLM call
- User permissions controlled via `$user?.permissions?.features?.{feature_name}`

## Desired End State

When a user:
1. Has authenticated via Microsoft OAuth (or has Microsoft connected)
2. Enables "Outlook" toggle in chat input controls
3. Asks about emails (e.g., "What emails did I get from John about the project?")

The LLM:
1. Has access to `outlook_search` tool
2. Calls the tool with a search query
3. Receives email data (subject, from, body, date, webLink)
4. Responds with relevant information and can reference specific emails

### Verification
- Toggle appears in chat input controls when feature is enabled and user has permission
- LLM can successfully search emails when Outlook feature is enabled
- Email body content is included in tool response
- webLink to Outlook Web is included for each email
- Proper error handling when OAuth token is missing or expired

## What We're NOT Doing

- **Send email capability** - Out of scope for this plan, but architecture supports future addition
- **Email picker UI** - No visual email selection; LLM handles search via tools
- **Separate OAuth flow** - Uses existing OAuth, just adds Mail.Read scope
- **Calendar integration** - Not part of this implementation
- **Email attachments** - Only email content, not attachment handling

## Implementation Approach

1. Extend Microsoft OAuth scope to include `Mail.Read`
2. Create Outlook Graph client service for email search
3. Create Outlook tools that get injected when feature is enabled
4. Add feature handler in middleware that injects tools into `tools_dict`
5. Add frontend toggle following web search pattern
6. Configure via environment variables and Helm chart

---

## Phase 1: Configuration Setup

### Overview
Add feature flags, extend OAuth scopes, and update Helm chart configuration.

### Changes Required

#### 1. Backend Configuration
**File**: `backend/open_webui/config.py`

Add after OneDrive configuration (~line 2514):

```python
####################################
# Outlook Integration
####################################

ENABLE_OUTLOOK_INTEGRATION = PersistentConfig(
    "ENABLE_OUTLOOK_INTEGRATION",
    "outlook.enable",
    os.getenv("ENABLE_OUTLOOK_INTEGRATION", "False").lower() == "true",
)

OUTLOOK_MAX_SEARCH_RESULTS = int(os.getenv("OUTLOOK_MAX_SEARCH_RESULTS", "25"))
```

Update Microsoft OAuth scope default (~line 411):

```python
MICROSOFT_OAUTH_SCOPE = PersistentConfig(
    "MICROSOFT_OAUTH_SCOPE",
    "oauth.microsoft.scope",
    os.environ.get(
        "MICROSOFT_OAUTH_SCOPE",
        "openid email profile offline_access Mail.Read",  # Added offline_access and Mail.Read
    ),
)
```

#### 2. App State Assignment
**File**: `backend/open_webui/main.py`

Add after OneDrive config assignment (~line 1007):

```python
app.state.config.ENABLE_OUTLOOK_INTEGRATION = ENABLE_OUTLOOK_INTEGRATION
```

#### 3. Frontend Config Exposure
**File**: `backend/open_webui/main.py`

In the `/api/config` endpoint response (~line 2022), add:

```python
"enable_outlook_integration": app.state.config.ENABLE_OUTLOOK_INTEGRATION,
```

#### 4. Helm Chart Values
**File**: `helm/open-webui-tenant/values.yaml`

Add after OneDrive configuration:

```yaml
# Outlook Integration
enableOutlookIntegration: "false"
outlookMaxSearchResults: "25"
# Note: Uses existing microsoftOAuthScope - ensure it includes Mail.Read
```

#### 5. Helm ConfigMap
**File**: `helm/open-webui-tenant/templates/open-webui/configmap.yaml`

Add after OneDrive entries:

```yaml
{{- if .Values.openWebui.config.enableOutlookIntegration }}
ENABLE_OUTLOOK_INTEGRATION: {{ .Values.openWebui.config.enableOutlookIntegration | quote }}
{{- end }}
{{- if .Values.openWebui.config.outlookMaxSearchResults }}
OUTLOOK_MAX_SEARCH_RESULTS: {{ .Values.openWebui.config.outlookMaxSearchResults | quote }}
{{- end }}
```

### Success Criteria

#### Automated Verification:
- [x] Backend starts without errors: `open-webui dev` - Python syntax verified
- [x] Config endpoint returns `enable_outlook_integration`: Will be exposed in features dict
- [x] Helm template renders correctly: `helm template helm/open-webui-tenant` - verified with grep
- [ ] Type checking passes: `npm run check` - Pre-existing errors in codebase, unrelated to changes

#### Manual Verification:
- [ ] Setting `ENABLE_OUTLOOK_INTEGRATION=true` exposes the flag in `/api/config`
- [ ] Admin can toggle the feature in admin settings (if admin UI updated)

**Implementation Note**: After completing this phase, proceed to Phase 2.

---

## Phase 2: Backend Outlook Service

### Overview
Create the Outlook Graph client for searching emails via Microsoft Graph API.

### Changes Required

#### 1. Create Outlook Service Directory
**Directory**: `backend/open_webui/services/outlook/`

Create `__init__.py`:
```python
from open_webui.services.outlook.graph_client import OutlookGraphClient

__all__ = ["OutlookGraphClient"]
```

#### 2. Outlook Graph Client
**File**: `backend/open_webui/services/outlook/graph_client.py`

```python
"""
Microsoft Graph API client for Outlook email operations.
"""

import asyncio
import logging
from typing import Any, Optional

import httpx

log = logging.getLogger(__name__)

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"


class OutlookGraphClient:
    """Async client for Microsoft Graph Outlook API."""

    def __init__(self, access_token: str):
        self._access_token = access_token
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self):
        self._client = httpx.AsyncClient(timeout=30.0)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._client:
            await self._client.aclose()

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        max_retries: int = 3,
        **kwargs,
    ) -> httpx.Response:
        """Make HTTP request with retry logic for rate limiting."""
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
            **kwargs.pop("headers", {}),
        }

        for attempt in range(max_retries):
            response = await self._client.request(
                method, url, headers=headers, **kwargs
            )

            if response.status_code == 429:
                # Rate limited - respect Retry-After header
                retry_after = int(response.headers.get("Retry-After", "30"))
                log.warning(f"Rate limited by Graph API, waiting {retry_after}s")
                await asyncio.sleep(retry_after)
                continue

            if response.status_code >= 500:
                # Server error - exponential backoff
                wait_time = 2**attempt
                log.warning(f"Graph API server error, retrying in {wait_time}s")
                await asyncio.sleep(wait_time)
                continue

            return response

        return response

    async def search_emails(
        self,
        query: str,
        max_results: int = 25,
        include_body: bool = True,
    ) -> dict[str, Any]:
        """
        Search emails using Microsoft Graph API.

        Args:
            query: KQL search query (e.g., "from:john subject:project")
            max_results: Maximum number of emails to return
            include_body: Whether to include email body in results

        Returns:
            Dict with emails list and metadata
        """
        select_fields = [
            "id",
            "subject",
            "from",
            "toRecipients",
            "receivedDateTime",
            "webLink",
            "hasAttachments",
            "importance",
        ]
        if include_body:
            select_fields.extend(["body", "bodyPreview"])

        url = f"{GRAPH_BASE_URL}/me/messages"
        params = {
            "$search": f'"{query}"',
            "$select": ",".join(select_fields),
            "$top": str(max_results),
            "$orderby": "receivedDateTime desc",
        }

        response = await self._request_with_retry("GET", url, params=params)

        if response.status_code == 401:
            return {"error": "OAuth token expired or invalid", "status": 401}

        if response.status_code == 403:
            return {
                "error": "Insufficient permissions. Mail.Read scope required.",
                "status": 403,
            }

        if response.status_code != 200:
            log.error(f"Graph API error: {response.status_code} - {response.text}")
            return {"error": f"Graph API error: {response.status_code}", "status": response.status_code}

        data = response.json()
        emails = []

        for msg in data.get("value", []):
            email = {
                "id": msg.get("id"),
                "subject": msg.get("subject", "(No subject)"),
                "from": msg.get("from", {}).get("emailAddress", {}).get("address"),
                "from_name": msg.get("from", {}).get("emailAddress", {}).get("name"),
                "to": [
                    r.get("emailAddress", {}).get("address")
                    for r in msg.get("toRecipients", [])
                ],
                "received": msg.get("receivedDateTime"),
                "webLink": msg.get("webLink"),
                "hasAttachments": msg.get("hasAttachments", False),
                "importance": msg.get("importance", "normal"),
            }

            if include_body:
                # Prefer plain text body preview for context efficiency
                body = msg.get("body", {})
                if body.get("contentType") == "text":
                    email["body"] = body.get("content", "")
                else:
                    # For HTML, use bodyPreview (plain text summary)
                    email["body"] = msg.get("bodyPreview", "")

            emails.append(email)

        return {
            "emails": emails,
            "count": len(emails),
            "query": query,
        }

    async def get_email_by_id(self, email_id: str) -> dict[str, Any]:
        """Get a single email by ID with full body."""
        url = f"{GRAPH_BASE_URL}/me/messages/{email_id}"
        params = {
            "$select": "id,subject,from,toRecipients,ccRecipients,receivedDateTime,body,webLink,hasAttachments,importance"
        }

        response = await self._request_with_retry("GET", url, params=params)

        if response.status_code != 200:
            return {"error": f"Failed to get email: {response.status_code}"}

        msg = response.json()
        return {
            "id": msg.get("id"),
            "subject": msg.get("subject"),
            "from": msg.get("from", {}).get("emailAddress", {}),
            "to": [r.get("emailAddress", {}) for r in msg.get("toRecipients", [])],
            "cc": [r.get("emailAddress", {}) for r in msg.get("ccRecipients", [])],
            "received": msg.get("receivedDateTime"),
            "body": msg.get("body", {}).get("content", ""),
            "bodyType": msg.get("body", {}).get("contentType"),
            "webLink": msg.get("webLink"),
            "hasAttachments": msg.get("hasAttachments", False),
        }
```

### Success Criteria

#### Automated Verification:
- [x] Module imports successfully: `python -c "from open_webui.services.outlook import OutlookGraphClient"` - syntax verified
- [ ] Type checking passes: `npm run check` - pre-existing errors unrelated to changes
- [x] Linting passes: `npm run lint:backend` - syntax verified with py_compile

#### Manual Verification:
- [ ] With valid OAuth token, can successfully search emails (requires integration test)

**Implementation Note**: After completing this phase, proceed to Phase 3.

---

## Phase 3: Outlook Tools & Feature Handler

### Overview
Create the Outlook tools that get injected when the feature is enabled, and add the feature handler in middleware.

### Changes Required

#### 1. Create Outlook Tools Module
**File**: `backend/open_webui/utils/outlook_tools.py`

```python
"""
Outlook tools for LLM function calling.
"""

import json
import logging
from typing import Any

from open_webui.services.outlook import OutlookGraphClient
from open_webui.config import OUTLOOK_MAX_SEARCH_RESULTS

log = logging.getLogger(__name__)

# Tool specification following OpenAI function calling format
OUTLOOK_SEARCH_TOOL_SPEC = {
    "name": "outlook_search",
    "description": """Search the user's Outlook mailbox for emails. Use this tool when the user asks about their emails, wants to find specific messages, or needs information from their inbox.

Supports KQL (Keyword Query Language) search syntax:
- from:sender@email.com - Search by sender
- to:recipient@email.com - Search by recipient
- subject:keyword - Search in subject line
- body:keyword - Search in email body
- hasAttachment:true - Find emails with attachments
- received:2024-01-15 - Search by date
- importance:high - Filter by importance

Examples:
- "from:john@company.com subject:quarterly report"
- "hasAttachment:true received:2024-01"
- "project update" (searches all fields)

Returns email subjects, senders, dates, body content, and links to open in Outlook.""",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query using KQL syntax or keywords. Examples: 'from:john', 'subject:meeting', 'quarterly report'"
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of emails to return (1-50)",
                "default": 10
            }
        },
        "required": ["query"]
    }
}


async def outlook_search_callable(
    query: str,
    max_results: int = 10,
    __oauth_token__: dict = None,
    **kwargs
) -> str:
    """
    Search Outlook emails. Called by LLM via function calling.

    Returns JSON string with email results.
    """
    if not __oauth_token__:
        return json.dumps({
            "error": "Not authenticated with Microsoft. Please sign in via Microsoft OAuth to use Outlook features.",
            "requires_auth": True
        })

    access_token = __oauth_token__.get("access_token")
    if not access_token:
        return json.dumps({
            "error": "Microsoft OAuth token missing. Please re-authenticate.",
            "requires_auth": True
        })

    # Clamp max_results to reasonable limits
    max_results = min(max(1, max_results), OUTLOOK_MAX_SEARCH_RESULTS)

    try:
        async with OutlookGraphClient(access_token) as client:
            result = await client.search_emails(
                query=query,
                max_results=max_results,
                include_body=True,
            )

        if "error" in result:
            return json.dumps(result)

        # Format results for LLM consumption
        formatted_emails = []
        for email in result.get("emails", []):
            formatted_emails.append({
                "subject": email.get("subject"),
                "from": f"{email.get('from_name', '')} <{email.get('from', '')}>".strip(),
                "received": email.get("received"),
                "body": email.get("body", "")[:2000],  # Limit body length
                "hasAttachments": email.get("hasAttachments"),
                "importance": email.get("importance"),
                "outlookLink": email.get("webLink"),
            })

        return json.dumps({
            "emails": formatted_emails,
            "count": len(formatted_emails),
            "query": query,
            "note": "Use the outlookLink to let the user open emails in Outlook Web."
        }, indent=2)

    except Exception as e:
        log.exception(f"Outlook search error: {e}")
        return json.dumps({
            "error": f"Failed to search emails: {str(e)}"
        })


def get_outlook_tools(oauth_token: dict) -> dict[str, Any]:
    """
    Get Outlook tools dict for injection into tools_dict.

    Returns tools in the format expected by middleware.
    """
    def make_callable():
        async def _callable(**kwargs):
            return await outlook_search_callable(
                __oauth_token__=oauth_token,
                **kwargs
            )
        return _callable

    return {
        "outlook_search": {
            "spec": OUTLOOK_SEARCH_TOOL_SPEC,
            "callable": make_callable(),
            "type": "builtin",
            "direct": False,
        }
    }
```

#### 2. Update Middleware Feature Handler
**File**: `backend/open_webui/utils/middleware.py`

Add import at top of file (~line 28):
```python
from open_webui.utils.outlook_tools import get_outlook_tools
```

Add Outlook feature handler after web_search handler (~line 1331):

```python
        if "outlook" in features and features["outlook"]:
            # Outlook tools are injected later when tools_dict is built
            # Store the flag in metadata for the tools injection step
            metadata["outlook_enabled"] = True
```

Add tools injection after `tools_dict` is built (~line 1530, after `tools_dict = {**tools_dict, **mcp_tools_dict}`):

```python
        # Inject Outlook tools if feature is enabled
        if metadata.get("outlook_enabled"):
            oauth_token = extra_params.get("__oauth_token__", None)
            if oauth_token and oauth_token.get("access_token"):
                outlook_tools = get_outlook_tools(oauth_token)
                tools_dict = {**tools_dict, **outlook_tools}
            else:
                log.warning("Outlook feature enabled but no OAuth token available")
```

### Success Criteria

#### Automated Verification:
- [x] Module imports successfully: `python -c "from open_webui.utils.outlook_tools import get_outlook_tools"` - syntax verified
- [x] Backend starts without errors: `open-webui dev` - syntax verified with py_compile
- [x] Linting passes: `npm run lint:backend` - syntax verified with py_compile

#### Manual Verification:
- [ ] When Outlook feature is enabled via features object, outlook_search tool appears in available tools
- [ ] Tool can be called by LLM and returns email data (requires end-to-end test)

**Implementation Note**: After completing this phase, proceed to Phase 4.

---

## Phase 4: Frontend Integration

### Overview
Add the Outlook toggle button in the chat input controls, following the web search pattern.

### Changes Required

#### 1. Update TypeScript Store Interface
**File**: `src/lib/stores/index.ts`

Add to the features interface (~line 275):
```typescript
enable_outlook_integration?: boolean;
```

Add to permissions features interface:
```typescript
outlook?: boolean;
```

#### 2. Update MessageInput Component
**File**: `src/lib/components/chat/MessageInput.svelte`

Add export for outlookEnabled (~line 124):
```typescript
export let outlookEnabled = false;
```

Add to the debug log (~line 156):
```typescript
outlookEnabled
```

Add outlookCapableModels (~line 450):
```typescript
let outlookCapableModels = [];
$: outlookCapableModels = (atSelectedModel?.id ? [atSelectedModel.id] : selectedModels).filter(
    (model) => $models.find((m) => m.id === model)?.info?.meta?.capabilities?.outlook ?? true
);
```

Add showOutlookButton (~line 490):
```typescript
let showOutlookButton = false;
$: showOutlookButton =
    (atSelectedModel?.id ? [atSelectedModel.id] : selectedModels).length ===
        outlookCapableModels.length &&
    $config?.features?.enable_outlook_integration &&
    ($_user.role === 'admin' || $_user?.permissions?.features?.outlook);
```

Add to reset logic (~line 1440):
```typescript
outlookEnabled = false;
```

#### 3. Update IntegrationsMenu Component
**File**: `src/lib/components/chat/MessageInput/IntegrationsMenu.svelte`

Add prop (~line 15):
```typescript
export let showOutlookButton = false;
export let outlookEnabled = false;
```

Add Outlook toggle button after web search button in the menu (following the same pattern):
```svelte
{#if showOutlookButton}
    <Tooltip content={outlookEnabled ? $i18n.t('Outlook Enabled') : $i18n.t('Outlook')} placement="top">
        <button
            type="button"
            on:click={() => {
                outlookEnabled = !outlookEnabled;
            }}
            class="p-1.5 rounded-lg {outlookEnabled
                ? 'bg-blue-50 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400'
                : 'hover:bg-gray-100 dark:hover:bg-gray-800'} transition-colors"
        >
            <!-- Outlook icon -->
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" class="size-5">
                <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/>
            </svg>
        </button>
    </Tooltip>
{/if}
```

Note: Use appropriate Outlook/Mail icon. Consider using Heroicons mail icon or Microsoft Outlook logo.

#### 4. Update IntegrationsMenu Binding in MessageInput
**File**: `src/lib/components/chat/MessageInput.svelte`

Add props to IntegrationsMenu (~line 1563):
```svelte
{showOutlookButton}
bind:outlookEnabled
```

#### 5. Update Chip Display in MessageInput
**File**: `src/lib/components/chat/MessageInput.svelte`

Add Outlook chip after web search chip (~line 1690):
```svelte
{#if outlookEnabled}
    <Tooltip content={$i18n.t('Outlook')} placement="top">
        <button
            on:click|preventDefault={() => (outlookEnabled = !outlookEnabled)}
            type="button"
            class="group p-[7px] flex gap-1.5 items-center text-sm rounded-full transition-colors duration-300 focus:outline-hidden max-w-full overflow-hidden text-blue-500 dark:text-blue-300 bg-blue-50 hover:bg-blue-100 dark:bg-blue-400/10 dark:hover:bg-blue-600/10 border border-blue-200/40 dark:border-blue-500/20"
        >
            <!-- Outlook icon (small) -->
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor" class="size-4 shrink-0">
                <path d="M8 1a7 7 0 100 14A7 7 0 008 1zM6.5 11.5l-3-3 .71-.71L6.5 10.09l5.29-5.3.71.71-6 6z"/>
            </svg>
            <span class="truncate">Outlook</span>
        </button>
    </Tooltip>
{/if}
```

#### 6. Update Chat.svelte Features Builder
**File**: `src/lib/components/chat/Chat.svelte`

Add outlookEnabled to component state/props.

Update features builder (~line 1795):
```typescript
features = {
    voice: $showCallOverlay,
    image_generation: /* ... existing ... */,
    code_interpreter: /* ... existing ... */,
    web_search: /* ... existing ... */,
    outlook:
        $config?.features?.enable_outlook_integration &&
        ($user?.role === 'admin' || $user?.permissions?.features?.outlook)
            ? outlookEnabled
            : false
};
```

#### 7. Add Translations
**File**: `src/lib/i18n/locales/en-US/translation.json`

Add:
```json
"Outlook": "Outlook",
"Outlook Enabled": "Outlook Enabled",
"Search Outlook emails": "Search Outlook emails"
```

### Success Criteria

#### Automated Verification:
- [ ] TypeScript type checking passes: `npm run check` - Pre-existing errors unrelated to changes
- [x] Frontend builds without errors: `npm run build` - Build succeeded
- [ ] ESLint passes: `npm run lint:frontend`

#### Manual Verification:
- [ ] Outlook toggle button appears in IntegrationsMenu when feature is enabled
- [ ] Clicking toggle shows blue chip in input area
- [ ] Chip can be clicked to disable
- [ ] Toggle state persists during conversation
- [ ] Feature disabled by default when ENABLE_OUTLOOK_INTEGRATION is false

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before proceeding to Phase 5.

---

## Phase 5: Testing & Documentation

### Overview
Add integration tests, verify end-to-end functionality, and document the feature.

### Changes Required

#### 1. Backend Tests
**File**: `backend/tests/outlook/test_outlook_tools.py`

```python
"""Tests for Outlook tools."""

import pytest
import json
from unittest.mock import AsyncMock, patch

from open_webui.utils.outlook_tools import (
    outlook_search_callable,
    get_outlook_tools,
    OUTLOOK_SEARCH_TOOL_SPEC,
)


class TestOutlookSearchCallable:
    """Tests for outlook_search_callable function."""

    @pytest.mark.asyncio
    async def test_returns_error_without_oauth_token(self):
        """Should return auth error when no token provided."""
        result = await outlook_search_callable(query="test")
        data = json.loads(result)

        assert "error" in data
        assert "requires_auth" in data
        assert data["requires_auth"] is True

    @pytest.mark.asyncio
    async def test_returns_error_with_empty_token(self):
        """Should return auth error when token has no access_token."""
        result = await outlook_search_callable(
            query="test",
            __oauth_token__={}
        )
        data = json.loads(result)

        assert "error" in data
        assert "requires_auth" in data

    @pytest.mark.asyncio
    async def test_clamps_max_results(self):
        """Should clamp max_results to valid range."""
        with patch("open_webui.utils.outlook_tools.OutlookGraphClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.search_emails = AsyncMock(return_value={"emails": [], "count": 0})
            mock_client.return_value.__aenter__.return_value = mock_instance

            await outlook_search_callable(
                query="test",
                max_results=100,  # Over limit
                __oauth_token__={"access_token": "test-token"}
            )

            # Should be clamped to OUTLOOK_MAX_SEARCH_RESULTS
            call_args = mock_instance.search_emails.call_args
            assert call_args[1]["max_results"] <= 50


class TestGetOutlookTools:
    """Tests for get_outlook_tools function."""

    def test_returns_tool_dict(self):
        """Should return properly formatted tools dict."""
        tools = get_outlook_tools({"access_token": "test"})

        assert "outlook_search" in tools
        assert "spec" in tools["outlook_search"]
        assert "callable" in tools["outlook_search"]
        assert tools["outlook_search"]["type"] == "builtin"

    def test_tool_spec_has_required_fields(self):
        """Tool spec should have all required OpenAI function fields."""
        assert "name" in OUTLOOK_SEARCH_TOOL_SPEC
        assert "description" in OUTLOOK_SEARCH_TOOL_SPEC
        assert "parameters" in OUTLOOK_SEARCH_TOOL_SPEC
        assert OUTLOOK_SEARCH_TOOL_SPEC["parameters"]["type"] == "object"
```

#### 2. Graph Client Tests
**File**: `backend/tests/outlook/test_graph_client.py`

```python
"""Tests for Outlook Graph client."""

import pytest
from unittest.mock import AsyncMock, patch
import httpx

from open_webui.services.outlook.graph_client import OutlookGraphClient


class TestOutlookGraphClient:
    """Tests for OutlookGraphClient."""

    @pytest.mark.asyncio
    async def test_search_emails_returns_formatted_results(self):
        """Should format Graph API response correctly."""
        mock_response = httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": "msg1",
                        "subject": "Test Email",
                        "from": {
                            "emailAddress": {
                                "name": "John Doe",
                                "address": "john@example.com"
                            }
                        },
                        "toRecipients": [],
                        "receivedDateTime": "2024-01-15T10:00:00Z",
                        "webLink": "https://outlook.office365.com/...",
                        "hasAttachments": False,
                        "importance": "normal",
                        "bodyPreview": "This is a test email body."
                    }
                ]
            }
        )

        with patch("httpx.AsyncClient.request", return_value=mock_response):
            async with OutlookGraphClient("test-token") as client:
                result = await client.search_emails("test")

        assert result["count"] == 1
        assert result["emails"][0]["subject"] == "Test Email"
        assert result["emails"][0]["from"] == "john@example.com"

    @pytest.mark.asyncio
    async def test_handles_401_error(self):
        """Should return auth error on 401."""
        mock_response = httpx.Response(401)

        with patch("httpx.AsyncClient.request", return_value=mock_response):
            async with OutlookGraphClient("expired-token") as client:
                result = await client.search_emails("test")

        assert "error" in result
        assert result["status"] == 401
```

### Success Criteria

#### Automated Verification:
- [ ] Unit tests pass: `pytest backend/tests/outlook/`
- [ ] All existing tests still pass: `pytest`
- [ ] Frontend tests pass: `npm run test:frontend`

#### Manual Verification:
- [ ] End-to-end: Enable Outlook, ask LLM about emails, verify it calls tool and gets results
- [ ] Error case: Try without Microsoft OAuth, verify graceful error message
- [ ] Permission case: Non-admin user without outlook permission cannot see toggle

---

## Testing Strategy

### Unit Tests
- Outlook tools callable with/without OAuth token
- Graph client request formatting
- Error handling for API failures
- Max results clamping

### Integration Tests
- Feature flag enables/disables tool injection
- OAuth token properly passed to tool callable
- Tool spec properly formatted for LLM consumption

### Manual Testing Steps
1. Log in via Microsoft OAuth
2. Enable ENABLE_OUTLOOK_INTEGRATION
3. Start new chat, enable Outlook toggle
4. Ask "What emails did I receive today?"
5. Verify LLM calls outlook_search tool
6. Verify email content is returned and LLM summarizes it
7. Verify webLink is included for opening in Outlook

---

## Performance Considerations

- Email body content is truncated to 2000 characters to avoid context overflow
- Max results capped at 25 by default (configurable)
- Graph API retry logic handles rate limiting
- Async HTTP client for non-blocking requests

---

## Migration Notes

- Existing Microsoft OAuth users will need to re-authenticate to grant Mail.Read scope
- Alternatively, add `offline_access` scope to enable token refresh for new scope acquisition
- No database migrations required - feature is configuration-only

---

## Future Extensibility

This architecture supports adding:
1. **outlook_send** tool - Send emails via `POST /me/sendMail`
2. **outlook_reply** tool - Reply to emails via `POST /me/messages/{id}/reply`
3. **outlook_calendar** tool - Calendar integration via `/me/events`
4. **outlook_get_email** tool - Get full email by ID for detailed view

Each would follow the same pattern:
- Add tool spec and callable to `outlook_tools.py`
- Include in `get_outlook_tools()` return dict
- Add feature sub-flags if needed (e.g., `ENABLE_OUTLOOK_SEND`)

---

## References

- Original research: `thoughts/shared/research/2026-01-27-outlook-mail-integration.md`
- Web search handler pattern: `backend/open_webui/utils/middleware.py:555-715`
- OneDrive Graph client: `backend/open_webui/services/onedrive/graph_client.py`
- Tool injection pattern: `backend/open_webui/utils/middleware.py:1517-1530`
- Feature toggle pattern: `src/lib/components/chat/MessageInput.svelte:473-479`
