<!-- Append-only episodic memory. See methodology.md Section 3 for the note template and rules. -->

---

### [20-03-2026] Gradient-DS Custom Features Overview

**Dev:** @lexlubbers: Lex Lubbers

**Context:** During the upstream merge from v0.6.43 to v0.8.9 (1126 upstream commits), we documented all Gradient-DS custom features to track what we've built on top of Open WebUI and ensure nothing was lost during the merge.

**What We Built:**

1. **Typed Knowledge Bases** — Added `type` column to knowledge table (values: `local`, `onedrive`, custom integration types). Type validation on create, immutable after creation. Frontend filter dropdown. Guards preventing file operations on non-local KBs.
   - Backend: `models/knowledge.py` (type column + filter), `routers/knowledge.py` (validation + guards)
   - Frontend: `workspace/Knowledge.svelte` (type filter dropdown)
   - Migration: `2c5f92a9fd66_add_knowledge_type_column.py`

2. **OneDrive Integration** — Full Microsoft OneDrive file picker and background sync. OAuth flow for personal and business accounts. SharePoint support. Configurable sync interval, max files, max file size.
   - Backend: `services/onedrive/` (auth, graph client, sync worker, scheduler, token refresh)
   - Router: `routers/onedrive_sync.py` mounted at `/api/v1/onedrive`
   - Frontend: `apis/onedrive/`, `utils/onedrive-file-picker.ts`, InputMenu entry
   - Config: `ENABLE_ONEDRIVE_INTEGRATION`, `ENABLE_ONEDRIVE_PERSONAL`, `ENABLE_ONEDRIVE_BUSINESS`, `ONEDRIVE_CLIENT_ID_*`, `ONEDRIVE_SHAREPOINT_*`, `ONEDRIVE_SYNC_*`

3. **Email Invites** — Invite users via email using Microsoft Graph mail API. Configurable expiry, subject, heading. Resend support.
   - Backend: `routers/invites.py`, `models/invites.py`, `services/email/` (auth, graph mail client)
   - Migration: `eaa33ce2752e_create_invite_table.py`
   - Config: `ENABLE_EMAIL_INVITES`, `INVITE_EXPIRY_HOURS`, `EMAIL_INVITE_SUBJECT`, `EMAIL_INVITE_HEADING`

4. **GDPR Archival** — Archive user data before deletion for compliance. Configurable retention period. Auto-archive on self-delete. Periodic expired archive cleanup.
   - Backend: `routers/archives.py` mounted at `/api/v1/archives`, `services/archival/`
   - Integration: `routers/users.py` archive-before-delete (`archive_before_delete` query param)
   - Migration: `f8e1a9c2d3b4_add_user_archive_table.py`
   - Config: `enable_user_archival`, `default_archive_retention_days`, `enable_auto_archive_on_self_delete`

5. **Acceptance Modal** — Configurable modal that users must accept before using the platform. Admin-configurable title, content, and button text.
   - Frontend: `layout/Overlay/AcceptanceModal.svelte`, `admin/Settings/Acceptance.svelte`
   - Integration: `(app)/+layout.svelte` (checkAcceptanceModal on init)
   - Config: `ui.enable_acceptance_modal`, `ui.acceptance_modal_title`, `ui.acceptance_modal_content`, `ui.acceptance_modal_button_text`

6. **Feature Flags** — 15+ environment variable flags to enable/disable UI features. Frontend utility `isFeatureEnabled()` consumed by 40+ files. Allows granular control over chat controls, capture, artifacts, playground, notes, voice, changelog, models, knowledge, prompts, tools, input menu, temporary chat, admin sections.
   - Backend: `config.py` (`FEATURE_*` env vars), `utils/features.py`
   - Frontend: `utils/features.ts` (`isFeatureEnabled`, `hasFeatureAccess`)
   - Pattern: `FEATURE_CHAT_CONTROLS=True`, `FEATURE_KNOWLEDGE=True`, etc.

7. **Feedback Configuration** — Customizable feedback layers: layer 2 (positive/negative tags), layer 3 (free-text prompt), category tags, conversation-level feedback with configurable scale, header, and placeholder.
   - Backend: `config.py` (`ENABLE_FEEDBACK_LAYER2`, `FEEDBACK_LAYER2_*_TAGS`, `ENABLE_FEEDBACK_LAYER3`, etc.)
   - Frontend: `chat/Messages/RateComment.svelte`, `chat/ConversationFeedback.svelte`, `admin/Settings/Evaluations.svelte`

8. **External Pipeline / Integration Providers** — Route RAG file processing to external pipeline service. Registry of integration providers with admin UI for managing provider configs (slug, name, badge type, max files, service accounts).
   - Backend: `routers/external_retrieval.py`, `routers/retrieval.py` (conditional routing)
   - Frontend: `admin/Settings/IntegrationProviders.svelte`
   - Config: `EXTERNAL_PIPELINE_URL`, `EXTERNAL_PIPELINE_API_KEY`, `EXTERNAL_PIPELINE_TIMEOUT`, `INTEGRATION_PROVIDERS`

9. **Agent API** — Routes chat completions to an external agent service, bypassing Open WebUI's built-in RAG, web search, and tool orchestration. Custom SSE protocol for status and source events. External agent loader that auto-installs agent packages from git repos at startup.
   - Backend: `utils/agent.py` (client, payload builder, SSE parser), `utils/external_agents.py` (auto-loader)
   - Integration: `main.py` (routing at lines 2051-2059), `utils/middleware.py` (3 bypass points for KB, web search, tools)
   - Config: `AGENT_API_ENABLED`, `AGENT_API_BASE_URL`, `AGENT_API_AGENT`, `EXTERNAL_AGENTS_REPO`, `EXTERNAL_AGENTS_PACKAGE`, `EXTERNAL_AGENTS_LIST`
   - Docs: `docs/agent-api-deployment.md`, `SOEV.md`

**Key Learnings:**
- All 9 features survived the v0.6.43 → v0.8.9 upstream merge (127 conflict files resolved)
- Migration ID collision (`a1b2c3d4e5f6`) between our soft-delete migration and upstream's skill table migration was resolved pre-merge by renaming ours
- Upstream added their own `Integrations.svelte` — our `IntegrationProviders.svelte` coexists within it
- Upstream redesigned the attach menu (split into 2 dropdowns) and workspace navigation (5-tab layout) — accepted as upstream design decisions

**Related:** `thoughts/shared/research/2026-03-20-upstream-merge-strategy.md`, `thoughts/shared/plans/2026-03-20-upstream-merge-v0.8.9.md`

---

### [26-03-2026] Google Drive Integration — Implementation & Sync Abstraction Refactor

**Dev:** @lexlubbers: Lex Lubbers

**Context:** After OneDrive sync was already working, we added Google Drive as a second cloud provider. This involved building the Google Drive-specific backend, refactoring the OneDrive code into a shared sync abstraction layer, and creating the frontend picker. Work spanned from initial backend (c0898b96b) through folder upload, token refresh, and file type fixes (88d7d82cd).

**What We Did:**

- **Sync Abstraction Layer** (`services/sync/`): Extracted shared base classes from OneDrive code — `BaseSyncWorker` (11 abstract methods, ~1029 lines), `SyncProvider`/`TokenManager` interfaces, `SyncScheduler`, shared router helpers, Socket.IO event emitters, and a frontend API factory (`apis/sync/index.ts`). Both OneDrive and Google Drive now plug into this.

- **Google Drive Backend** (`services/google_drive/`):
  - OAuth 2.0 Authorization Code + PKCE flow with `access_type=offline` to get refresh tokens
  - `GoogleDriveClient` — async httpx wrapper for Drive API v3 with 401 retry (token refresh), 429 respect, 5xx backoff
  - `GoogleDriveSyncWorker` — BFS folder listing, incremental sync via Changes API (captures `startPageToken` before first full listing), permission sync mapping Drive emails to Open WebUI users
  - Token refresh preserves old refresh_token when Google doesn't return a new one (common Google behavior)
  - Router at `/api/v1/google-drive` with 9 endpoints (sync CRUD, auth flow, token status)

- **Google Workspace File Handling**: Native Google Docs/Sheets/Slides can't be downloaded directly. Export map: Docs→DOCX, Sheets→XLSX, Slides→PPTX for KB sync; Docs→text/plain, Sheets→CSV for chat picker. Change detection uses `modifiedTime` (no `md5Checksum` available for Workspace files).

- **OAuth Callback Multiplexing**: Reused the existing `/oauth/google/callback` URL — checks `state` against pending Drive flows before falling through to SSO login handler. Avoids needing a separate redirect URI in Google Cloud Console.

- **Frontend Picker** (`utils/google-drive-picker.ts`): Two picker modes — `createKnowledgePicker()` for KB sync (multi-select, folders enabled, `NAV_HIDDEN`) and `createPicker()` for chat attachment (single file, downloads content). Auth flow: tries backend stored token first, falls back to OAuth popup.

- **Shared Frontend Pattern**: `KnowledgeBase.svelte` uses a `CLOUD_PROVIDERS` config map keyed by type (`onedrive`, `google_drive`). Each entry specifies meta keys, event prefixes, file ID prefixes, and API instances — adding a new provider is mostly config.

- **Config**: 6 env vars — `ENABLE_GOOGLE_DRIVE_INTEGRATION`, `GOOGLE_DRIVE_CLIENT_ID`, `GOOGLE_DRIVE_API_KEY`, `ENABLE_GOOGLE_DRIVE_SYNC`, `GOOGLE_DRIVE_SYNC_INTERVAL_MINUTES`, `GOOGLE_DRIVE_MAX_FILES_PER_SYNC`, `GOOGLE_DRIVE_MAX_FILE_SIZE_MB`. Shares `GOOGLE_CLIENT_SECRET` with Google SSO.

- **Scrollable Pickers** (801300612, d0e7d6155): Added scrollable containers for KBs with many files and for file lists in the picker.

- **Hotfixes**: Google file type guessing (88d7d82cd), Helm secrets reference for API key (0ac933e8b), `access_grants` format fix when porting from dev branch (b82f5dcf4).

**Key Learnings:**
- Google's Changes API is the equivalent of OneDrive's delta links but works differently — returns changed items globally, requiring parent-chain filtering to scope to the synced folder tree
- Google Workspace files have no `md5Checksum`, so change detection falls back to `modifiedTime` comparison
- Google doesn't always return a new refresh_token on refresh — must preserve the old one
- Sharing the OAuth callback URL between SSO and Drive auth avoids Google Cloud Console redirect URI management but requires careful state-based routing
- The sync abstraction cleanly separated provider-specific logic (11 abstract methods) from shared orchestration (~1000 lines of base worker)
- Cleaning up the abstraction layer (ccc3d65ac) removed fragile patterns: error-string matching replaced with status code checks, private `_pending_flows` replaced with public API, eliminated the `_current_drive_id` stash pattern

**Related:** `collab/docs/external-integration-cookbook.md`, [20-03-2026] Gradient-DS Custom Features Overview

---

### [26-03-2026] Agent Proxy — External API for soev.ai Agents

**Dev:** @lexlubbers

**Context:** External callers (curl, SDKs, other services) needed a way to call soev.ai agents through an OpenAI-compatible API, authenticated with OWUI API keys (`sk-` tokens). The existing `AGENT_API_ENABLED` feature only routes OWUI's own chat UI through the agent service — it doesn't expose the agent to external callers.

**What We Did:**
- Created a reverse proxy at `/api/v1/agent/` with three endpoints: `GET /models`, `POST /chat/completions` (SSE streaming), `GET /openapi.json`
- Follows the existing Ollama/OpenAI proxy pattern: `aiohttp` session + `stream_wrapper()` for SSE passthrough
- Admin toggle in Settings > Integrations (PersistentConfig `ENABLE_AGENT_PROXY`, default off)
- Reuses existing `AGENT_API_BASE_URL` from `env.py` — no separate URL config needed
- Auth via `get_verified_user` (supports both JWT and `sk-` API keys)
- Inline API documentation in admin panel with download link for OpenAPI spec and curl examples (including collection/document attachment via `files` field)
- Added Vite dev server proxy config for `/api`, `/ollama`, `/openai`, `/oauth`, `/static` so API routes work on port 5173 during development
- Helm chart: `enableAgentProxy: "false"` in values.yaml, `ENABLE_AGENT_PROXY` in configmap

**Key Learnings:**
- Two distinct features share the agent service but serve different purposes: `AGENT_API_ENABLED` (internal, routes OWUI chat middleware) vs `ENABLE_AGENT_PROXY` (external, exposes OpenAI-compatible proxy). Naming clarity matters — "proxy" implies external access
- The proxy is a raw passthrough — it forwards the request body as-is, so callers can pass `files` (with `type: "collection"` or `type: "file"` and an `id`), `rag_filter`, and any other fields the agent service accepts
- Vite dev server had no proxy config, causing API routes to 404 on port 5173 when accessed directly in the browser. Adding `server.proxy` in `vite.config.ts` fixed this for all API prefixes
- The IntegrationProviders OpenAPI download uses authenticated fetch + blob download (not a plain link), because the endpoint requires auth. Agent proxy uses the same pattern for consistency

**Related:** `thoughts/shared/plans/2026-03-26-agent-proxy-integration.md`, `backend/open_webui/routers/agent_proxy.py`

---

### [26-03-2026] Feature Flags for Webpage URL and Reference Chats in Input Menu

**Dev:** @lexlubbers

**Context:** The chat input "+" menu (InputMenu.svelte) always showed "Webpage URL" and "Reference chats" items with no way to disable them via Helm config, unlike other menu items that have feature flag guards.

**What We Did:**
- Added `FEATURE_WEBPAGE_URL` and `FEATURE_REFERENCE_CHATS` env vars in `config.py` (default `True`)
- Imported and exposed both in `main.py` via the config endpoint's `features` dict
- Added `'webpage_url'` and `'reference_chats'` to the `Feature` union type in `src/lib/utils/features.ts`
- Wrapped the Webpage URL block with `{#if isFeatureEnabled('webpage_url')}` in `InputMenu.svelte`
- Added `isFeatureEnabled('reference_chats')` to the existing `($chats ?? []).length > 0` guard for Reference chats
- Added `featureWebpageUrl` and `featureReferenceChats` to Helm values.yaml and configmap.yaml

**Key Learnings:**
- The feature flag pipeline for InputMenu items is: Helm values → configmap (env var) → `config.py` (read) → `main.py` (expose in config endpoint) → `features.ts` (type) → `InputMenu.svelte` (`isFeatureEnabled()` guard)
- Most InputMenu sections already had guards (knowledge, capture, notes, tools) but Webpage URL and Reference Chats were the two exceptions

**Related:** Feature flags system documented in [20-03-2026] Gradient-DS Custom Features Overview

---

### [30-03-2026] Cloud KB Permission Leak — Sync Workers Mirror Cloud Sharing into Access Grants
> **Amended [30-03-2026]:** Fix implemented — see [30-03-2026] Cloud KB Permission Fix — Suspension Lifecycle Implementation.

**With:** @lexlubbers

**Context:** On gradient.soev.ai (test branch), users could see other users' OneDrive and Google Drive knowledge bases as read-only. Investigated whether the upstream `access_grants` migration (`f1e2d3c4b5a6`) was the cause.

**What We Did:**
- Queried the `access_grant` table on gradient — no wildcard (`*`) grants existed, ruling out the migration's NULL→public conversion
- Found explicit user-level grants with varying timestamps (March 25–30), proving they were created at runtime, not by a one-time migration
- Traced the grants to `_sync_permissions()` in both sync workers (`onedrive/sync_worker.py:279`, `google_drive/sync_worker.py:203`)
- This method runs on every sync cycle (called from `base_worker.py:701`), fetches cloud folder sharing permissions, maps emails to Open WebUI users, and creates `read` access grants for every matched user
- The router's defense-in-depth (`knowledge.py:504-506`, `577-581`) correctly blocks grant changes via the API, but sync workers call `update_knowledge_by_id` directly on the model layer, bypassing it

**Key Learnings:**
- The root cause is NOT the migration — it's the `_sync_permissions()` feature in both sync workers mirroring broad cloud sharing into Open WebUI access grants
- In corporate M365/Google Workspace tenants, folders are often shared with the entire team, so the sync gives every team member read access to every synced KB
- The migration fix (NULL knowledge → private) is still valid hardening but was not the actual trigger
- Defense-in-depth in the router only protects the HTTP API path — model-layer calls from sync workers bypass it
- Desired behavior: permission sync should only verify the **owner** still has cloud access, not grant other users access. KBs should remain private to their creator.

**Fix needed:**
1. Rewrite `_sync_permissions()` in both sync workers to only check owner access, not mirror cloud sharing
2. Clean up existing grants on gradient: `DELETE FROM access_grant WHERE resource_type = 'knowledge' AND resource_id IN (SELECT id FROM knowledge WHERE type IN ('onedrive', 'google_drive'));`
3. Deploy code fix BEFORE cleanup (sync runs every ≤15 min and would recreate grants)
4. Cleanup commands saved in `fix_kb_gradient_soev.md`

**Related:** Defense-in-depth commit `1e96c838b`, sync abstraction layer [24-03-2026], [26-03-2026]

---

### [30-03-2026] Cloud KB Permission Fix — Suspension Lifecycle Implementation

**With:** @lexlubbers

**Context:** Implementing the fix for the cloud KB permission leak identified earlier today. Plan: `thoughts/shared/plans/2026-03-30-cloud-kb-permission-fix.md`.

**What We Did:**
- **Phase 1**: Rewrote `_sync_permissions()` in both OneDrive and Google Drive sync workers — now only verifies owner access to the cloud folder. No access grants are created. If owner loses access, KB is suspended (`suspended_at` + `suspended_reason` in sync meta). If access is regained, suspension is cleared. Base worker returns early when KB is suspended; scheduler skips suspended KBs.
- **Phase 2**: Added suspension helpers to knowledge model (`is_suspended()`, `get_suspension_info()`, `get_suspended_expired_knowledge()`). Cleanup worker now hard-deletes KBs suspended for 30+ days (`SUSPENSION_TTL_DAYS = 30`).
- **Phase 3**: Knowledge router blocks non-admin access to suspended KBs (403 with explanatory message on `GET /{id}` and `GET /{id}/files`). Retrieval path skips suspended KBs in chat. List API returns `suspension_info` field for suspended KBs.
- **Phase 4**: Frontend grays out suspended KBs (`opacity-50 cursor-not-allowed`), prevents navigation on click, shows "Suspended" warning badge with tooltip showing days remaining.

**Key Learnings:**
- `suspended_at` lives in `meta[meta_key]` (no schema migration needed), consistent with existing sync state storage pattern
- Owner gets implicit access via `has_permission_filter()` — never needs an explicit access grant, so removing all grant creation is safe
- The `Users` import was only needed for email-mapping in the old `_sync_permissions()` — removed from both workers

**Still needed:**
- Manual cleanup of existing grants on gradient.soev.ai (see `fix_kb_gradient_soev.md`)
- Deploy code fix BEFORE cleanup (sync runs every ≤15 min)

**Related:** Investigation [30-03-2026] Cloud KB Permission Leak, plan `2026-03-30-cloud-kb-permission-fix.md`

---

### [30-03-2026] TOTP 2FA — Full Implementation (Phase 1)

**With:** @lexlubbers

**Context:** Adding TOTP-based two-factor authentication for email+password users, with admin enforcement and recovery codes.

**What We Did:**
- Implemented full TOTP 2FA feature across ~21 files (backend + frontend + Helm)
- Backend: pyotp + qrcode deps, Alembic migration (totp_secret/totp_enabled/totp_last_used_at on auth + recovery_code table), AES-GCM encrypted TOTP secrets, bcrypt-hashed recovery codes, replay protection, 5-attempt rate limiting
- New router at `/api/v1/auths/2fa` with 6 endpoints: status, setup, enable, disable, verify, recovery/regenerate
- Signin flow modified: partial JWT token (5min TTL, purpose=2fa_pending) returned when 2FA enabled, rejected by all normal endpoints
- Admin: PersistentConfig flags (ENABLE_2FA, REQUIRE_2FA, TWO_FA_GRACE_PERIOD_DAYS), config endpoints, force-disable user 2FA, per-user 2FA status check
- Frontend: TwoFactorChallenge (login page), TwoFactorSetup (account settings), API clients for all endpoints
- Created dedicated Security tab in admin settings (extracted from General) with lock icon
- EditUserModal: conditionally shows "Disable 2FA" only for users with 2FA enabled (fetches per-user status)
- Enforcement banner in (app)/+layout.svelte with grace period display and dismiss
- Helm chart: env vars in values.yaml + configmap.yaml
- i18n: en-US + nl-NL translations for all new strings
- Fixed Svelte template nesting bug in auth/+page.svelte ({#if show2FAChallenge} closing tag misplaced)

**Key Learnings:**
- Feature is fully off by default (ENABLE_2FA=false) — admin must enable via Security tab or env var
- LDAP, SSO/OAuth, API key, and trusted header auth all bypass 2FA — only email+password users affected
- Partial JWT token pattern (purpose claim) cleanly separates 2FA-pending state from authenticated sessions
- Admin settings tab system requires changes in 5 places: ADMIN_SETTINGS_TABS constant, Settings.svelte (import, allSettings, icon, rendering chain)

**Related:** Plan `thoughts/shared/plans/2026-03-30-totp-2fa-phase1.md`
