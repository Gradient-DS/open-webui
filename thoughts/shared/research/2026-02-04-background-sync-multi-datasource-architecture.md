\---
date: 2026-02-04T15:00:00+01:00
researcher: Claude
git_commit: c8a5a93e8636629452ea0a57448ddb707f9bacb9
branch: feat/data-control
repository: open-webui
topic: "Background Sync Architecture for Multiple Datasources"
tags: [research, codebase, onedrive, background-sync, token-management, multi-datasource, architecture, security]
status: complete
last_updated: 2026-02-04
last_updated_by: Claude
---

# Research: Background Sync Architecture for Multiple Datasources

**Date**: 2026-02-04T15:00:00+01:00
**Researcher**: Claude
**Git Commit**: c8a5a93e8636629452ea0a57448ddb707f9bacb9
**Branch**: feat/data-control
**Repository**: open-webui

## Research Question

What needs to be built to enable background sync for OneDrive (and future datasources like SharePoint, Google Drive, Slack), including secure token storage, token refresh, and a multi-datasource abstraction layer? What is Microsoft's documented approach for OneDrive sync?

## Summary

The codebase already has strong foundations: a working OneDrive sync worker, delta query support, encrypted OAuth token storage, and a Permission Provider abstraction pattern. The primary blocker for background sync is the **token refresh mechanism** — the current flow relies on short-lived delegated tokens from the frontend browser session.

To enable background sync with multi-datasource extensibility, three layers need to be built:

1. **Token Management Layer** — Store and refresh OAuth tokens per-user per-datasource, encrypted at rest
2. **Sync Provider Abstraction** — Generic interface for datasource sync operations (like the existing `PermissionProvider` pattern)
3. **Sync Orchestrator** — Upgraded scheduler that coordinates token refresh and sync execution across all providers

Microsoft's recommended approach combines **delta queries** (already partially implemented) with **webhook subscriptions** for near-real-time notifications. The existing `scheduler.py` is a placeholder that identifies due KBs but cannot execute syncs due to token limitations.

### Effort Breakdown

| Component | New/Modify | Complexity | Can Reuse |
|-----------|-----------|------------|-----------|
| Token refresh service | New file | Medium | OAuthSession model, Fernet encryption |
| `offline_access` scope + capture | Modify frontend | Low | MSAL integration |
| Store token endpoint | Modify router | Low | Existing router patterns |
| Upgrade scheduler | Modify existing | Medium | Existing scheduler structure |
| SyncProvider abstraction | New files | Medium | PermissionProvider pattern |
| Webhook subscriptions | New file | High | GraphClient |
| `410 Gone` handling | Modify GraphClient | Low | Existing retry logic |

---

## Current State Analysis

### What Works Today

1. **Manual sync via button** — User picks a OneDrive folder, frontend gets a delegated access token via MSAL, backend downloads and processes files into a knowledge base (`sync_worker.py`)
2. **Delta queries** — `GraphClient.get_drive_delta()` at `services/onedrive/graph_client.py:129-159` tracks changes efficiently using `@odata.deltaLink`
3. **Encrypted token storage** — `OAuthSession` model at `models/oauth_sessions.py:24-42` uses Fernet encryption with `OAUTH_SESSION_TOKEN_ENCRYPTION_KEY`
4. **Permission Provider pattern** — `PermissionProvider(ABC)` at `services/permissions/provider.py:48-135` with `PermissionProviderRegistry` at `services/permissions/registry.py:16-70` — designed for multi-source extension
5. **Scheduler infrastructure** — `services/onedrive/scheduler.py` exists but only logs which KBs are due, cannot execute syncs

### What's Missing

| Gap | File | Impact |
|-----|------|--------|
| No `offline_access` scope requested | `src/lib/utils/onedrive-file-picker.ts` | No refresh tokens returned by MSAL |
| No token refresh mechanism | New: `services/onedrive/token_refresh.py` | Scheduler cannot get fresh access tokens |
| Scheduler doesn't execute syncs | `services/onedrive/scheduler.py:87-92` | Background sync is non-functional |
| No `410 Gone` handling | `services/onedrive/graph_client.py:78-87` | Stale delta tokens cause unrecoverable errors |
| No webhook subscriptions | Not implemented | Only polling-based, no near-real-time sync |
| No `$select` optimization | `services/onedrive/graph_client.py:145` | Higher API cost and throttling risk |
| No multi-datasource abstraction | Not implemented | Each new datasource requires ad-hoc integration |

### Existing Plan: Refresh Token Storage

A detailed implementation plan already exists at `thoughts/shared/plans/2026-01-18-onedrive-refresh-token-storage.md`. It covers 5 phases:

1. Add `offline_access` scope and capture refresh token from MSAL
2. Backend token storage endpoints using OAuthSession
3. Token refresh service via direct HTTP to Microsoft's token endpoint
4. Frontend integration to store tokens after sync setup
5. Upgrade scheduler to execute syncs with refreshed tokens

**Assessment**: This plan is solid for the OneDrive-specific implementation. What it lacks is the multi-datasource abstraction layer that would make adding future datasources (Google Drive, SharePoint, Slack) systematic rather than ad-hoc.

---

## Microsoft's Documented Approach for OneDrive Sync

### Four-Phase Pattern (Official Recommendation)

Microsoft prescribes a four-phase sync pattern documented in their ["Best practices for discovering files and detecting changes at scale"](https://learn.microsoft.com/en-us/onedrive/developer/rest-api/concepts/scan-guidance):

```
Phase 1: DISCOVER  →  Phase 2: CRAWL  →  Phase 3: NOTIFY  →  Phase 4: PROCESS CHANGES
```

**Phase 1 — Discover**: Enumerate drives and sites. Subscribe to structure changes.

**Phase 2 — Crawl (Initial Sync)**: Call `/drives/{drive-id}/root/delta` without a token. Page through all results via `@odata.nextLink`. Store the final `@odata.deltaLink`.

**Phase 3 — Notify (Webhooks)**: Create subscription via `POST /subscriptions` for `drives/{drive-id}/root`. Include `lifecycleNotificationUrl` for reliability. Run one more delta query immediately after subscribing to close the gap.

**Phase 4 — Process Changes (Ongoing)**: On webhook notification or scheduled poll, run delta query with stored `deltaLink`. Download content only when `cTag` indicates change. Process deletions (items with `deleted` facet). Store new `deltaLink`.

### Delta Query API

```
GET /drives/{drive-id}/items/{folder-id}/delta
```

- **Initial crawl**: No token → returns all items + pagination via `@odata.nextLink`
- **Subsequent sync**: Use stored `@odata.deltaLink` → returns only changes (1 RU cost)
- **`410 Gone` response**: Delta token expired/invalid → must restart with full sync
- **`token=latest` shortcut**: Get a `deltaLink` without enumerating items (for future-only tracking)
- **`cTag` property**: Use to detect actual content changes before downloading

**Current implementation**: `GraphClient.get_drive_delta()` at `graph_client.py:129-159` correctly implements delta pagination. `OneDriveSyncWorker._collect_folder_files()` at `sync_worker.py:238-269` stores the delta link. Missing: `410 Gone` handling.

### Webhook Subscriptions

```http
POST https://graph.microsoft.com/v1.0/subscriptions
{
  "changeType": "updated",
  "notificationUrl": "https://your-app/api/webhooks/onedrive",
  "resource": "drives/{drive-id}/root",
  "expirationDateTime": "2026-03-05T00:00:00Z",
  "clientState": "secretClientValue",
  "lifecycleNotificationUrl": "https://your-app/api/webhooks/lifecycle"
}
```

| Constraint | Detail |
|---|---|
| Subscription scope | Root folder only (Business), any folder (Personal) |
| Maximum expiration | 42,300 minutes (~29.4 days) |
| Notification latency | Average < 1 min, max 60 min |
| changeType for root | Only `updated` supported |
| Validation | Microsoft POSTs `validationToken`, must echo back within 10s |

**Lifecycle notifications** (critical):
- `reauthorizationRequired` — Token expiring, must reauthorize
- `subscriptionRemoved` — Microsoft removed subscription, must recreate
- `missed` — Some notifications missed, must run delta query to catch up

### Token Refresh Strategy

**Option A — Delegated flow with refresh tokens** (recommended for per-user access):
- Request `offline_access` scope during initial auth
- Refresh tokens last 90 days (rolling — renewed on each use)
- Store encrypted, refresh before access token expiry
- Revocation triggers: password change, admin revocation, account disabled

**Option B — Client Credentials flow** (app-only, for organizational data):
- No user interaction, no refresh tokens
- Requires Application permissions + admin consent
- Access all files the app is permitted to see (not per-user)
- Simpler but changes the access model

**Recommendation**: Use Option A (delegated + refresh tokens) because it preserves per-user access semantics and aligns with the existing OneDrive sync design that operates on behalf of the user.

### Rate Limiting

| Category | Limit |
|---|---|
| Per-user requests | 3,000 per 5 minutes |
| Delta query with token | 1 RU |
| Multi-item query | 2 RU |
| Permission operations | 5 RU |
| File download | 1 RU |

Current `GraphClient._request_with_retry()` at `graph_client.py:30-76` correctly handles 429 and 5xx. Missing: proactive `RateLimit-Remaining` header monitoring.

---

## Multi-Datasource Abstraction Architecture

### Design Rationale

The codebase already has excellent abstraction patterns to follow:

| Pattern | Location | Approach |
|---------|----------|----------|
| **PermissionProvider** | `services/permissions/provider.py` | ABC + Registry + OneDrive impl |
| **StorageProvider** | `storage/provider.py` | ABC + Factory function |
| **VectorDBBase** | `retrieval/vector/main.py` | ABC + Factory class |

The sync system should follow the **PermissionProvider** pattern (ABC + Registry) since:
1. Providers are registered at startup (not selected by config like Storage/VectorDB)
2. Multiple providers can be active simultaneously
3. Lookup is by `source_type` string (matching file metadata)

### Proposed Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     Sync Orchestrator                            │
│  (Generic scheduler that coordinates across all providers)       │
│                                                                  │
│  1. Iterate registered SyncProviders                            │
│  2. Find KBs due for sync per provider                          │
│  3. Refresh tokens via TokenManager                             │
│  4. Execute syncs via SyncProvider                              │
│  5. Handle webhook notifications                                │
└────────┬──────────────────────────┬─────────────────────────────┘
         │                          │
    ┌────▼────┐              ┌──────▼──────┐
    │ Token   │              │   Sync      │
    │ Manager │              │  Provider   │
    │  (ABC)  │              │   (ABC)     │
    └────┬────┘              └──────┬──────┘
         │                          │
    ┌────▼────────────┐      ┌──────▼──────────────┐
    │ OneDrive        │      │ OneDrive             │
    │ TokenManager    │      │ SyncProvider         │
    ├─────────────────┤      ├──────────────────────┤
    │ Google Drive    │      │ Google Drive          │
    │ TokenManager    │      │ SyncProvider          │
    ├─────────────────┤      ├──────────────────────┤
    │ SharePoint      │      │ SharePoint            │
    │ TokenManager    │      │ SyncProvider          │
    └─────────────────┘      └──────────────────────┘
```

### SyncProvider Interface (Proposed)

```python
class SyncProvider(ABC):
    """Abstract interface for datasource sync operations."""

    source_type: str  # e.g., "onedrive", "google_drive", "sharepoint"

    @abstractmethod
    async def collect_changes(
        self,
        source_config: Dict[str, Any],
        access_token: str,
    ) -> SyncChangeset:
        """Discover what changed since last sync.

        Returns files to add/update/delete with a new sync cursor.
        """
        pass

    @abstractmethod
    async def download_file(
        self,
        source_config: Dict[str, Any],
        file_ref: FileReference,
        access_token: str,
    ) -> bytes:
        """Download file content from the source."""
        pass

    @abstractmethod
    async def setup_webhook(
        self,
        source_config: Dict[str, Any],
        callback_url: str,
        access_token: str,
    ) -> WebhookSubscription:
        """Create a change notification subscription (optional)."""
        pass

    @abstractmethod
    async def validate_connection(
        self,
        source_config: Dict[str, Any],
        access_token: str,
    ) -> ConnectionStatus:
        """Check if the connection to the source is still valid."""
        pass

    def get_supported_extensions(self) -> Set[str]:
        """File extensions this provider can handle."""
        return SUPPORTED_EXTENSIONS  # Default set
```

### TokenManager Interface (Proposed)

```python
class TokenManager(ABC):
    """Abstract interface for datasource token lifecycle management."""

    source_type: str

    @abstractmethod
    async def refresh_access_token(
        self,
        knowledge_id: str,
        user_id: str,
    ) -> TokenRefreshResult:
        """Refresh the access token using stored credentials.

        Must handle token rotation (e.g., Microsoft rotates refresh tokens).
        """
        pass

    @abstractmethod
    async def store_token(
        self,
        knowledge_id: str,
        user_id: str,
        token_data: Dict[str, Any],
    ) -> bool:
        """Store authentication credentials securely."""
        pass

    @abstractmethod
    async def revoke_token(
        self,
        knowledge_id: str,
        user_id: str,
    ) -> bool:
        """Revoke/delete stored credentials."""
        pass

    @abstractmethod
    async def get_valid_token(
        self,
        knowledge_id: str,
        user_id: str,
    ) -> Optional[str]:
        """Get a valid access token, refreshing if needed.

        Returns None if token cannot be obtained (needs re-auth).
        """
        pass
```

### SyncProviderRegistry (Proposed)

Following the PermissionProviderRegistry pattern:

```python
class SyncProviderRegistry:
    """Central registry for sync providers."""

    _providers: Dict[str, SyncProvider] = {}
    _token_managers: Dict[str, TokenManager] = {}

    @classmethod
    def register(cls, provider: SyncProvider, token_manager: TokenManager) -> None:
        cls._providers[provider.source_type] = provider
        cls._token_managers[provider.source_type] = token_manager

    @classmethod
    def get_provider(cls, source_type: str) -> Optional[SyncProvider]:
        return cls._providers.get(source_type)

    @classmethod
    def get_token_manager(cls, source_type: str) -> Optional[TokenManager]:
        return cls._token_managers.get(source_type)

    @classmethod
    def get_all_providers(cls) -> List[SyncProvider]:
        return list(cls._providers.values())
```

### How This Maps to Existing Code

| Existing Code | Becomes | Notes |
|---------------|---------|-------|
| `GraphClient` | Used internally by `OneDriveSyncProvider` | No change needed |
| `OneDriveSyncWorker.sync()` | `OneDriveSyncProvider.collect_changes()` + orchestrator | Refactor sync logic into provider |
| `OneDriveSyncWorker._collect_folder_files()` | `OneDriveSyncProvider.collect_changes()` | Delta query logic moves to provider |
| `OneDriveSyncWorker._process_file_info()` | Stays in orchestrator (generic file processing) | Same for all providers |
| `scheduler.py` | Generic `SyncOrchestrator` | Works with any registered provider |
| New: `token_refresh.py` | `OneDriveTokenManager` | Per-provider token refresh |

### Relationship with PermissionProvider

The `SyncProvider` and `PermissionProvider` are related but separate concerns:

```
SyncProvider    → "How do I get files from this source?"
PermissionProvider → "Who has access to files from this source?"
TokenManager    → "How do I maintain authentication with this source?"
```

A datasource integration registers all three:

```python
# In main.py startup
if ENABLE_ONEDRIVE_INTEGRATION:
    # Sync capability
    SyncProviderRegistry.register(
        OneDriveSyncProvider(),
        OneDriveTokenManager()
    )
    # Permission capability
    PermissionProviderRegistry.register(
        OneDrivePermissionProvider()
    )
```

---

## Security Considerations

### Token Storage Security

| Concern | Mitigation | Status |
|---------|-----------|--------|
| **Tokens at rest** | Fernet encryption via `OAuthSession` model | Exists |
| **Encryption key** | `OAUTH_SESSION_TOKEN_ENCRYPTION_KEY` (defaults to `WEBUI_SECRET_KEY`) | Exists |
| **Token isolation** | Provider = `onedrive:{knowledge_id}` per-KB tokens | Planned |
| **Minimal scopes** | `Files.Read.All` + `offline_access` only | Planned |
| **Token rotation** | Store new refresh token after each Microsoft refresh | Planned |
| **Revocation detection** | Handle `invalid_grant` → mark KB as `needs_reauth` | Planned |
| **Token cleanup** | Delete tokens when KB is deleted (DeletionService) | Needs implementation |
| **Audit logging** | Log all token store/refresh/revoke operations | Planned |

### Refresh Token Risks

1. **90-day rolling expiry**: Refresh tokens expire if unused for 90 days. A KB that hasn't synced in 90 days would need manual re-auth. The scheduler prevents this by running regularly.

2. **Revocation triggers**: User password change, admin revocation, or account deletion all invalidate refresh tokens. The system must detect `invalid_grant` and prompt re-auth.

3. **Rotating tokens**: Microsoft issues a new refresh token with each refresh. If the new token isn't stored (crash/error between receiving and storing), the old token becomes invalid. Use a transaction or write-before-use pattern.

4. **Cross-KB token sharing**: Each KB should have its own token to prevent a single revocation from breaking all KBs. The existing plan uses `provider=onedrive:{knowledge_id}`.

### Webhook Security

1. **Endpoint validation**: Microsoft sends a `validationToken` that must be echoed back. This prevents attackers from registering arbitrary URLs.

2. **`clientState` verification**: Include a secret in the subscription. Reject notifications that don't include the matching `clientState`.

3. **HTTPS requirement**: Notification URLs must use HTTPS (except localhost for development).

4. **Don't trust notification content**: Webhooks only signal "something changed". Always verify changes via delta query rather than trusting notification payloads.

### Client Credentials vs Delegated Flow

| Aspect | Delegated + Refresh Token | Client Credentials |
|--------|--------------------------|-------------------|
| **Scope** | User's files only | All files the app can access |
| **Admin consent** | Not required | Required |
| **Token lifetime** | 90 days (rolling) | ~1 hour (no refresh, just re-request) |
| **User interaction** | Once per KB setup | Never |
| **Risk if compromised** | One user's files | All organizational files |
| **Audit trail** | Actions attributed to user | Actions attributed to app |

**Recommendation**: Use **delegated flow** for the following reasons:
- Aligns with existing per-user access model
- Lower blast radius if tokens are compromised
- No admin consent required for deployment
- Better audit trail (actions attributed to specific users)
- Consistent with how the permission provider validates access

Client credentials could be added later as an admin-only option for organizational-wide sync scenarios.

---

## Implementation Approach

### Phase 1: Token Refresh for OneDrive (Enables Background Sync)

Follow the existing plan at `thoughts/shared/plans/2026-01-18-onedrive-refresh-token-storage.md`:

1. Add `offline_access` scope to MSAL config
2. Capture and send refresh token to backend after sync setup
3. Create `token_refresh.py` service for Microsoft token endpoint calls
4. Add store/revoke/status endpoints to `onedrive_sync.py` router
5. Upgrade `scheduler.py` to refresh tokens and execute syncs

**This phase gets background sync working for OneDrive without any abstraction layer.**

### Phase 2: Multi-Datasource Abstraction

Extract the generic patterns from Phase 1 into abstractions:

1. Create `services/sync/` directory with:
   - `provider.py` — `SyncProvider(ABC)` base class
   - `token_manager.py` — `TokenManager(ABC)` base class
   - `registry.py` — `SyncProviderRegistry`
   - `orchestrator.py` — Generic scheduler/orchestrator
2. Create `services/sync/providers/onedrive.py` — `OneDriveSyncProvider`
3. Create `services/sync/token_managers/onedrive.py` — `OneDriveTokenManager`
4. Refactor existing OneDrive code to implement the interfaces
5. Update `main.py` to register providers at startup

### Phase 3: Webhooks (Optional, for Near-Real-Time Sync)

1. Create webhook endpoint in backend (`/api/v1/webhooks/onedrive`)
2. Implement subscription creation/renewal in `OneDriveSyncProvider`
3. Handle lifecycle notifications (reauthorization, removal, missed)
4. Wire webhooks to trigger delta query + sync
5. Keep polling as fallback safety net

### Phase 4: Additional Datasources

With the abstraction layer in place, adding a new datasource requires:

1. Implement `SyncProvider` for the datasource
2. Implement `TokenManager` for the datasource's OAuth flow
3. Implement `PermissionProvider` for access control (if needed)
4. Register all three in `main.py`
5. Add frontend UI for connecting the datasource

---

## Trade-off: Abstract Now vs After OneDrive Works

| Approach | Pros | Cons |
|----------|------|------|
| **Build abstraction first, then OneDrive** | Clean architecture from day 1, easier to add datasources | Slower to get OneDrive background sync working, may over-design without second datasource to validate |
| **Build OneDrive first, extract abstraction later** | Faster to ship background sync, abstraction informed by real implementation | Risk of OneDrive-specific assumptions baked in, refactoring cost |
| **Build OneDrive with abstraction-ready boundaries** | Pragmatic middle ground, get value fast with clear extraction points | Requires discipline to keep boundaries clean |

**Recommendation**: **Option 3** — Build the OneDrive token refresh and scheduler upgrade first (following the existing plan), but structure the code with clear boundaries that make extraction into the abstraction layer straightforward. The existing `PermissionProvider` pattern was built this way (OneDrive first, designed for extension) and it worked well.

Concretely:
- Create `token_refresh.py` as a standalone module (easy to extract into `TokenManager`)
- Keep sync logic in `sync_worker.py` but separate "what to sync" from "how to process" (easy to extract `SyncProvider.collect_changes()`)
- Keep the generic scheduler separate from OneDrive-specific logic

---

## Code References

- `backend/open_webui/services/onedrive/scheduler.py:1-14` — Placeholder scheduler with documented token gap
- `backend/open_webui/services/onedrive/graph_client.py:129-159` — Delta query implementation
- `backend/open_webui/services/onedrive/sync_worker.py:238-269` — Delta link storage and change collection
- `backend/open_webui/services/onedrive/sync_worker.py:342-453` — Permission sync (email mapping)
- `backend/open_webui/models/oauth_sessions.py:69-105` — Fernet encryption for token storage
- `backend/open_webui/services/permissions/provider.py:48-135` — PermissionProvider ABC (pattern to follow)
- `backend/open_webui/services/permissions/registry.py:16-70` — Registry pattern (pattern to follow)
- `backend/open_webui/storage/provider.py` — StorageProvider ABC (alternative pattern)

## Historical Context (from thoughts/)

- `thoughts/shared/plans/2026-01-18-onedrive-refresh-token-storage.md` — Detailed 5-phase plan for OneDrive token refresh (ready to implement)
- `thoughts/shared/plans/2026-01-28-federated-source-access-control.md` — Permission Provider abstraction plan (partially implemented)
- `thoughts/shared/research/2026-01-28-federated-source-access-control-architecture.md` — Architecture research for permission system
- `thoughts/shared/research/2026-01-18-onedrive-sync-interval-not-working.md` — Analysis of why scheduled sync doesn't work (token gap)
- `thoughts/shared/research/2026-01-18-onedrive-implementation-best-practices-review.md` — Best practices review covering token flow chain

## Related Research

- `thoughts/shared/research/2026-01-28-federated-source-access-control-architecture.md` — Two-layer permission model (source + KB access)
- `thoughts/shared/research/2026-01-28-data-deletion-incomplete-cascade.md` — Token cleanup when deleting KBs/users

## Open Questions

1. **MSAL refresh token access**: The existing plan notes that MSAL browser SDK doesn't directly expose refresh tokens in the response. The workaround accesses internal MSAL cache storage which may break with MSAL updates. An alternative is implementing a backend auth code flow (redirect-based) where the backend exchanges the auth code for tokens directly. Which approach should we use?

2. **Webhook infrastructure**: Webhooks require a publicly accessible HTTPS endpoint. In self-hosted deployments behind firewalls, webhooks won't work. Should we make webhooks optional (polling-only fallback) or require external connectivity?

3. **Multi-tenant considerations**: If multiple users sync different OneDrive folders to separate KBs, each needs its own token. The per-KB token design handles this, but should we also support a shared organizational token (client credentials) for admin-managed sync?

4. **Sync conflict resolution**: When a file is modified both in OneDrive and manually in the KB (e.g., user re-uploads a corrected version), which version wins? Current behavior: OneDrive hash mismatch triggers re-download, overwriting the local version.

5. **Token migration**: Existing KBs with OneDrive sync need a manual re-sync to store refresh tokens. Should we show a migration banner/notification to affected users?

## Sources

- [Microsoft Graph Delta Query Overview](https://learn.microsoft.com/en-us/graph/delta-query-overview)
- [driveItem: delta API](https://learn.microsoft.com/en-us/graph/api/driveitem-delta?view=graph-rest-1.0)
- [Best practices for discovering files and detecting changes at scale](https://learn.microsoft.com/en-us/onedrive/developer/rest-api/concepts/scan-guidance)
- [Microsoft Graph Change Notifications (Webhooks)](https://learn.microsoft.com/en-us/graph/change-notifications-delivery-webhooks)
- [Subscription Resource Type](https://learn.microsoft.com/en-us/graph/api/resources/subscription?view=graph-rest-1.0)
- [Lifecycle Notifications](https://learn.microsoft.com/en-us/graph/change-notifications-lifecycle-events)
- [Microsoft Identity Platform Refresh Tokens](https://learn.microsoft.com/en-us/entra/identity-platform/refresh-tokens)
- [OAuth 2.0 Client Credentials Flow](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-client-creds-grant-flow)
- [Microsoft Graph Throttling Guidance](https://learn.microsoft.com/en-us/graph/throttling)
- [SharePoint Throttling Limits](https://learn.microsoft.com/en-us/sharepoint/dev/general-development/how-to-avoid-getting-throttled-or-blocked-in-sharepoint-online)
