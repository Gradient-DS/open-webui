# Open WebUI (Gradient-DS Fork) — Dev Notes

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

**Related:** `dev_notes/external-integration-cookbook.md`, [20-03-2026] Gradient-DS Custom Features Overview
