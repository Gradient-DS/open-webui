---
date: 2026-01-27T09:30:00+01:00
researcher: Claude
git_commit: 7ee9dcefcaef467484bdd79c18b8b3b95db5f2b5
branch: main
repository: open-webui
topic: "Outlook Mail Integration Options for Open WebUI"
tags: [research, codebase, outlook, microsoft-graph, integration, tools, oauth]
status: complete
last_updated: 2026-01-27
last_updated_by: Claude
---

# Research: Outlook Mail Integration Options for Open WebUI

**Date**: 2026-01-27T09:30:00+01:00
**Researcher**: Claude
**Git Commit**: 7ee9dcefcaef467484bdd79c18b8b3b95db5f2b5
**Branch**: main
**Repository**: open-webui

## Research Question

How to integrate Outlook mail into Open WebUI similar to the existing OneDrive integration, with capabilities to:
1. Search emails with links to open them in Outlook
2. Send emails from Open WebUI
3. Configure via environment variables and Helm chart values
4. Maintain upstream merge compatibility

## Summary

There are **three viable approaches** for Outlook integration, ordered by upstream merge compatibility:

| Approach | Upstream Safe | Complexity | User Experience | OAuth Reuse |
|----------|---------------|------------|-----------------|-------------|
| **1. Tool (recommended)** | Full | Medium | Good | Yes (`__oauth_token__`) |
| **2. MCP Server** | Full | Low | Good | Yes (Tool Server OAuth) |
| **3. Core Integration** | Minimal | High | Best | Yes (existing Microsoft OAuth) |

**Recommendation**: Start with **Approach 1 (Tool)** for immediate value with zero upstream conflicts, then optionally build a dedicated integration if deeper UX is needed.

## Detailed Findings

### Approach 1: Custom Tool (Highest Upstream Compatibility)

Create an Outlook Tool that users can install via the Tools workspace. This leverages Open WebUI's existing plugin system.

#### Implementation Files Needed

```
# No core modifications needed - Tool is stored in database
# Tool code (stored in tool.content via admin UI):
backend/open_webui/tools/outlook_tool.py  # Reference implementation
```

#### Tool Structure

```python
"""
title: Outlook Mail Tool
author: Your Name
version: 0.1.0
requirements: msgraph-sdk>=1.0.0, azure-identity>=1.15.0
"""

from pydantic import BaseModel, Field
from typing import Optional
import json

class Valves(BaseModel):
    """Admin-configurable settings (stored in DB, editable via Admin UI)"""
    TENANT_ID: str = Field(default="", description="Azure AD Tenant ID")
    CLIENT_ID: str = Field(default="", description="Azure AD App Client ID")
    # Note: Client secret should NOT be stored in Valves for user-facing tools
    # Instead, use system_oauth auth type with Tool Servers

class UserValves(BaseModel):
    """Per-user settings"""
    MAX_RESULTS: int = Field(default=25, description="Maximum emails to return")
    INCLUDE_BODY: bool = Field(default=False, description="Include email body in results")

class Tools:
    def __init__(self):
        self.valves = Valves()

    async def search_emails(
        self,
        query: str = Field(..., description="Search query (KQL syntax: from:john subject:project)"),
        __user__: dict = {},
        __oauth_token__: dict = None,
    ) -> str:
        """
        Search Outlook emails. Returns email subjects with links to open in Outlook.

        Example queries:
        - "from:john subject:report"
        - "hasAttachment:true received:2026-01-01"
        - "body:budget importance:high"
        """
        if not __oauth_token__:
            return json.dumps({"error": "Not authenticated with Microsoft. Please sign in via OAuth."})

        import aiohttp

        access_token = __oauth_token__.get("access_token")
        user_valves = __user__.get("valves", {})
        max_results = user_valves.get("MAX_RESULTS", 25)
        include_body = user_valves.get("INCLUDE_BODY", False)

        select_fields = ["id", "subject", "from", "receivedDateTime", "webLink", "hasAttachments"]
        if include_body:
            select_fields.append("bodyPreview")

        url = f"https://graph.microsoft.com/v1.0/me/messages"
        params = {
            "$search": f'"{query}"',
            "$select": ",".join(select_fields),
            "$top": str(max_results),
            "$orderby": "receivedDateTime desc"
        }

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, headers=headers) as resp:
                if resp.status == 401:
                    return json.dumps({"error": "OAuth token expired. Please re-authenticate."})
                if resp.status == 429:
                    return json.dumps({"error": "Rate limited by Microsoft Graph. Please wait and try again."})
                if resp.status != 200:
                    return json.dumps({"error": f"API error: {resp.status}"})

                data = await resp.json()

        results = []
        for msg in data.get("value", []):
            result = {
                "subject": msg.get("subject"),
                "from": msg.get("from", {}).get("emailAddress", {}).get("address"),
                "received": msg.get("receivedDateTime"),
                "link": msg.get("webLink"),  # Deep link to Outlook Web
                "hasAttachments": msg.get("hasAttachments", False)
            }
            if include_body:
                result["preview"] = msg.get("bodyPreview", "")[:200]
            results.append(result)

        return json.dumps({"emails": results, "count": len(results)})

    async def send_email(
        self,
        to: str = Field(..., description="Recipient email address"),
        subject: str = Field(..., description="Email subject"),
        body: str = Field(..., description="Email body (plain text)"),
        __oauth_token__: dict = None,
    ) -> str:
        """
        Send an email via Outlook. Requires Mail.Send permission.
        """
        if not __oauth_token__:
            return json.dumps({"error": "Not authenticated with Microsoft. Please sign in via OAuth."})

        import aiohttp

        access_token = __oauth_token__.get("access_token")
        url = "https://graph.microsoft.com/v1.0/me/sendMail"

        payload = {
            "message": {
                "subject": subject,
                "body": {
                    "contentType": "Text",
                    "content": body
                },
                "toRecipients": [
                    {"emailAddress": {"address": to}}
                ]
            },
            "saveToSentItems": True
        }

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers) as resp:
                if resp.status == 202:
                    return json.dumps({"success": True, "message": f"Email sent to {to}"})
                elif resp.status == 401:
                    return json.dumps({"error": "OAuth token expired. Please re-authenticate."})
                elif resp.status == 403:
                    return json.dumps({"error": "Insufficient permissions. Mail.Send scope required."})
                else:
                    error_body = await resp.text()
                    return json.dumps({"error": f"Failed to send: {resp.status}", "details": error_body})
```

#### OAuth Configuration for Tool

The tool requires the Microsoft OAuth provider to include mail scopes. Update existing Microsoft OAuth config:

```python
# backend/open_webui/config.py (existing, modify scope)
MICROSOFT_OAUTH_SCOPE = PersistentConfig(
    "MICROSOFT_OAUTH_SCOPE",
    "oauth.microsoft.scope",
    os.environ.get(
        "MICROSOFT_OAUTH_SCOPE",
        "openid email profile Mail.Read Mail.Send"  # Add mail scopes
    ),
)
```

#### Helm Configuration

```yaml
# helm/open-webui-tenant/values.yaml
openWebui:
  config:
    # Existing Microsoft OAuth config
    microsoftClientId: ""
    microsoftClientTenantId: ""
    microsoftOAuthScope: "openid email profile Mail.Read Mail.Send"  # Add mail scopes
```

```yaml
# helm/open-webui-tenant/templates/open-webui/configmap.yaml
{{- if .Values.openWebui.config.microsoftOAuthScope }}
MICROSOFT_OAUTH_SCOPE: {{ .Values.openWebui.config.microsoftOAuthScope | quote }}
{{- end }}
```

---

### Approach 2: MCP Tool Server (External Service)

Create an external MCP server that provides Outlook tools. This keeps all code external to Open WebUI.

#### Architecture

```
┌──────────────┐     MCP Protocol      ┌────────────────────┐
│  Open WebUI  │ ◄─────────────────────► │  Outlook MCP Server │
│              │     OAuth 2.1 Auth      │  (Python/FastAPI)   │
└──────────────┘                        └────────────────────┘
                                                  │
                                                  ▼
                                        ┌─────────────────┐
                                        │ Microsoft Graph │
                                        └─────────────────┘
```

#### MCP Server Implementation

```python
# outlook_mcp_server/server.py
from mcp.server import FastMCP
from mcp.types import Tool, TextContent
import httpx

mcp = FastMCP("outlook-mail")

@mcp.tool()
async def search_emails(query: str, max_results: int = 25) -> str:
    """Search Outlook emails with KQL query syntax."""
    # Token passed via MCP context from Open WebUI OAuth
    token = mcp.context.get("oauth_token")

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://graph.microsoft.com/v1.0/me/messages",
            params={"$search": f'"{query}"', "$top": max_results},
            headers={"Authorization": f"Bearer {token}"}
        )
        return resp.json()

@mcp.tool()
async def send_email(to: str, subject: str, body: str) -> str:
    """Send an email via Outlook."""
    token = mcp.context.get("oauth_token")
    # ... implementation
```

#### Open WebUI Tool Server Configuration

```python
# Add via Admin Settings > Tool Servers
{
    "url": "http://outlook-mcp:3000",
    "type": "mcp",
    "auth_type": "oauth_2.1",  # Uses Open WebUI's OAuth flow
    "oauth_config": {
        "authorization_endpoint": "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize",
        "token_endpoint": "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
        "client_id": "...",
        "scope": "Mail.Read Mail.Send"
    }
}
```

---

### Approach 3: Core Integration (OneDrive Pattern)

Full integration following the OneDrive pattern. **Lowest upstream compatibility** but best UX.

#### Files to Create/Modify

**Backend:**
```
backend/open_webui/
├── services/outlook/
│   ├── __init__.py
│   ├── graph_client.py      # MS Graph API client (like OneDrive's)
│   ├── search_worker.py     # Background email search
│   └── send_worker.py       # Background email sending
├── routers/
│   └── outlook.py           # FastAPI router
└── config.py                # Add ENABLE_OUTLOOK_INTEGRATION etc.
```

**Frontend:**
```
src/lib/
├── apis/outlook/
│   └── index.ts             # API client
├── utils/
│   └── outlook-picker.ts    # Email picker utility
└── components/
    ├── icons/Outlook.svelte
    └── chat/MessageInput/
        └── OutlookMenu.svelte  # Email attachment menu
```

#### Configuration Pattern (from OneDrive)

```python
# backend/open_webui/config.py

# Master enable flag
ENABLE_OUTLOOK_INTEGRATION = PersistentConfig(
    "ENABLE_OUTLOOK_INTEGRATION",
    "outlook.enable",
    os.getenv("ENABLE_OUTLOOK_INTEGRATION", "False").lower() == "true",
)

# Feature sub-flags
ENABLE_OUTLOOK_SEARCH = PersistentConfig(
    "ENABLE_OUTLOOK_SEARCH",
    "outlook.enable_search",
    os.getenv("ENABLE_OUTLOOK_SEARCH", "True").lower() == "true",
)

ENABLE_OUTLOOK_SEND = PersistentConfig(
    "ENABLE_OUTLOOK_SEND",
    "outlook.enable_send",
    os.getenv("ENABLE_OUTLOOK_SEND", "False").lower() == "true",  # Off by default
)

# Rate limiting
OUTLOOK_MAX_SEARCH_RESULTS = int(os.getenv("OUTLOOK_MAX_SEARCH_RESULTS", "100"))
OUTLOOK_RATE_LIMIT_PER_MINUTE = int(os.getenv("OUTLOOK_RATE_LIMIT_PER_MINUTE", "60"))
```

#### Helm Chart Values

```yaml
# helm/open-webui-tenant/values.yaml

openWebui:
  config:
    # Outlook Integration
    enableOutlookIntegration: "false"
    enableOutlookSearch: "true"
    enableOutlookSend: "false"
    outlookMaxSearchResults: "100"
    outlookRateLimitPerMinute: "60"

    # Reuses existing Microsoft OAuth
    # microsoftClientId: ""
    # microsoftClientTenantId: ""
    # microsoftOAuthScope: "openid email profile Mail.Read Mail.Send"
```

```yaml
# helm/open-webui-tenant/templates/open-webui/configmap.yaml

# Outlook Integration
ENABLE_OUTLOOK_INTEGRATION: {{ .Values.openWebui.config.enableOutlookIntegration | quote }}
ENABLE_OUTLOOK_SEARCH: {{ .Values.openWebui.config.enableOutlookSearch | quote }}
ENABLE_OUTLOOK_SEND: {{ .Values.openWebui.config.enableOutlookSend | quote }}
OUTLOOK_MAX_SEARCH_RESULTS: {{ .Values.openWebui.config.outlookMaxSearchResults | quote }}
```

#### Router Conditional Loading

```python
# backend/open_webui/main.py

if app.state.config.ENABLE_OUTLOOK_INTEGRATION:
    from open_webui.routers import outlook
    app.include_router(
        outlook.router, prefix="/api/v1/outlook", tags=["outlook"]
    )
```

#### Frontend Config Exposure

```python
# backend/open_webui/main.py (in /api/config endpoint)

{
    "enable_outlook_integration": app.state.config.ENABLE_OUTLOOK_INTEGRATION,
    **(
        {
            "enable_outlook_search": app.state.config.ENABLE_OUTLOOK_SEARCH,
            "enable_outlook_send": app.state.config.ENABLE_OUTLOOK_SEND,
        }
        if app.state.config.ENABLE_OUTLOOK_INTEGRATION
        else {}
    ),
}
```

---

### Microsoft Graph API Requirements

#### Permissions Needed

| Feature | Permission | Type | Admin Consent |
|---------|------------|------|---------------|
| Search emails | `Mail.Read` | Delegated | No |
| Search emails (metadata only) | `Mail.ReadBasic` | Delegated | No |
| Send emails | `Mail.Send` | Delegated | No |

**Recommendation**: Use `Mail.Read` (not `Mail.ReadBasic`) to enable body search and preview.

#### Search API Usage

```http
GET https://graph.microsoft.com/v1.0/me/messages
  ?$search="from:john subject:quarterly"
  &$select=id,subject,from,receivedDateTime,webLink,hasAttachments
  &$top=25
  &$orderby=receivedDateTime desc
```

**KQL Search Operators:**
- `from:`, `to:`, `cc:`, `bcc:`, `participants:`
- `subject:`, `body:`, `attachment:`
- `hasAttachment:true/false`
- `received:`, `sent:` (date filters)
- `importance:high/normal/low`

#### Deep Links

The `webLink` property returns URLs like:
```
https://outlook.office365.com/owa/?ItemID=AAMkAD...&viewmodel=ReadMessageItem
```

**Limitations:**
- Only works for Outlook on the web (not desktop app)
- Cannot be embedded in iframes (X-Frame-Options)
- User must be signed into Microsoft 365

#### Rate Limits

| Limit | Value |
|-------|-------|
| Requests per 10 min per mailbox | 10,000 |
| Recommended sustained rate | 4-10 req/sec |
| Max recipients per email | 500 |

**Best Practice**: Implement Retry-After header handling:

```python
async def handle_rate_limit(response):
    if response.status == 429:
        retry_after = int(response.headers.get("Retry-After", 30))
        await asyncio.sleep(retry_after)
        # Retry request
```

---

### Existing Infrastructure to Leverage

#### Microsoft OAuth (Already Configured)

Open WebUI already has Microsoft OAuth configured at `config.py:372-418`:

```python
MICROSOFT_CLIENT_ID = PersistentConfig(...)
MICROSOFT_CLIENT_SECRET = PersistentConfig(...)
MICROSOFT_CLIENT_TENANT_ID = PersistentConfig(...)
MICROSOFT_OAUTH_SCOPE = PersistentConfig(...)  # Add Mail.Read Mail.Send
```

#### OAuth Token Access in Tools

Tools receive `__oauth_token__` parameter when `system_oauth` auth type is configured:

```python
# backend/open_webui/utils/middleware.py:1181-1189
oauth_token = await request.app.state.oauth_manager.get_oauth_token(
    user.id,
    request.cookies.get("oauth_session_id", None),
)
```

#### Token Storage and Refresh

`OAuthSessions` model handles encrypted token storage with automatic refresh:
- `backend/open_webui/models/oauth_sessions.py`
- `backend/open_webui/utils/oauth.py:601-645`

---

## Code References

### OneDrive Integration (Reference Pattern)
- `backend/open_webui/services/onedrive/graph_client.py` - MS Graph API client
- `backend/open_webui/services/onedrive/sync_worker.py` - Background worker
- `backend/open_webui/routers/onedrive_sync.py` - FastAPI router
- `src/lib/utils/onedrive-file-picker.ts` - Frontend picker utility
- `src/lib/components/chat/MessageInput/InputMenu.svelte:347-557` - UI integration

### Configuration Pattern
- `backend/open_webui/config.py:2466-2514` - OneDrive config as reference
- `backend/open_webui/main.py:1482-1487` - Conditional router loading
- `backend/open_webui/main.py:2011-2021` - Frontend config exposure

### Tools System
- `backend/open_webui/utils/tools.py:107-320` - Tool loading and execution
- `backend/open_webui/utils/tools.py:256-262` - Valves injection
- `backend/open_webui/utils/middleware.py:1181-1198` - OAuth token injection

### Helm Chart
- `helm/open-webui-tenant/values.yaml:286-298` - OneDrive values pattern
- `helm/open-webui-tenant/templates/open-webui/configmap.yaml:202-220` - ConfigMap mapping

---

## Architecture Insights

### Upstream Merge Strategy

1. **Tool Approach**: Zero merge conflicts - tool stored in database, not codebase
2. **MCP Server Approach**: Zero merge conflicts - external service
3. **Core Integration**:
   - New files (no conflicts): `services/outlook/`, `routers/outlook.py`, frontend components
   - Modified files (potential conflicts): `config.py`, `main.py`, `InputMenu.svelte`
   - **Mitigation**: Use feature flag to completely isolate code paths

### Authentication Flow

```
User clicks "Connect Outlook"
         │
         ▼
Microsoft OAuth popup (login.microsoftonline.com)
         │
         ▼
Authorization code returned
         │
         ▼
Token exchange (backend)
         │
         ▼
Tokens stored encrypted in oauth_session table
         │
         ▼
Tool receives __oauth_token__ on invocation
         │
         ▼
Microsoft Graph API calls with Bearer token
```

### Feature Flag Pattern

```python
# Completely isolate feature behind flags
if ENABLE_OUTLOOK_INTEGRATION:
    # Import and register router
    # Load frontend components
    # Enable menu items
```

---

## Open Questions

1. **Email Threading**: Should search return full email threads or individual messages?
2. **Attachment Handling**: Should we support attaching email attachments to chat, or just link to emails?
3. **Reply/Forward**: Is there a need for reply-in-context from chat?
4. **Calendar Integration**: Should calendar events be included in the same integration?
5. **Shared Mailboxes**: Should support delegated mailbox access?

---

## Recommendations

### Phase 1: Tool Implementation (1-2 days)
1. Add `Mail.Read Mail.Send` to `MICROSOFT_OAUTH_SCOPE` in config
2. Update helm chart with new scope default
3. Create Outlook Tool via Admin UI (can be shared as JSON export)
4. Document tool installation process

### Phase 2: MCP Server (optional, 2-3 days)
1. Create standalone MCP server with Outlook tools
2. Package as Docker container
3. Add to helm chart as optional sidecar
4. Document Tool Server OAuth configuration

### Phase 3: Core Integration (optional, 1-2 weeks)
1. Create `services/outlook/` following OneDrive pattern
2. Add frontend components with feature flag
3. Maintain as separate branch until merged upstream

---

## Related Research

- `thoughts/shared/research/2026-01-18-onedrive-implementation-best-practices-review.md` - OneDrive patterns
- `thoughts/shared/plans/2026-01-18-onedrive-refresh-token-storage.md` - Token storage approach
