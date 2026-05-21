# Confluence Cloud Sync — Admin Panel, Dual Auth & Shared KB Mode — Implementation Plan

## Overview

Add a dedicated **Cloud Sync** admin Settings tab and extend the Confluence integration so an
admin can, entirely from the UI:

1. Configure Confluence via **OAuth 3LO credentials** (client ID/secret) — currently Helm/env only.
2. Configure Confluence via **username + API token** (HTTP Basic auth) — a new auth path.
3. Choose a global **KB sharing mode**: per-user Confluence KBs (current behaviour) or a single
   **shared, read-only, auto-syncing knowledge base containing the full Confluence content**,
   visible to all users and managed only by admins.

Driving use case: unblock the Intermax deployment. Intermax runs Confluence **Cloud**
(`intermax-algemeen.atlassian.net`) and wants one shared KB with all Confluence content,
synced automatically, readable by every user.

## Current State Analysis

The Confluence integration already exists and works for **per-user OAuth** on Confluence Cloud.
It plugs into the generic sync abstraction (`backend/open_webui/services/sync/`) shared with
OneDrive and Google Drive.

What exists:

- OAuth 3LO + PKCE flow, token storage in `oauth_session` (Fernet-encrypted), automatic refresh,
  background `SyncScheduler`, picker modal, HTML→Markdown sync worker.
- Config keys as `PersistentConfig` (`config.py:2909-2942`): `ENABLE_CONFLUENCE_INTEGRATION`
  (`confluence.enable`), `CONFLUENCE_OAUTH_CLIENT_ID` (`confluence.client_id`),
  `CONFLUENCE_OAUTH_CLIENT_SECRET` (`confluence.client_secret`), `ENABLE_CONFLUENCE_SYNC`
  (`confluence.enable_sync`), `CONFLUENCE_SYNC_INTERVAL_MINUTES` (`confluence.sync_interval_minutes`).
  `CONFLUENCE_MAX_PAGES_PER_SYNC` / `CONFLUENCE_MAX_PAGE_SIZE_MB` are plain ints (env only).

What is missing:

- **No admin UI for Confluence beyond a single on/off toggle** in `Documents.svelte:1471`
  (`RAGConfig.ENABLE_CONFLUENCE_INTEGRATION`). OAuth client ID/secret are settable only via
  Helm/env. There is no `/api/v1/configs/confluence` endpoint.
- **No Basic-auth path.** Every layer assumes OAuth: `ConfluenceClient` always sends
  `Authorization: Bearer` (`confluence_client.py:88`) and routes through the Atlassian cloud
  gateway `https://api.atlassian.com/ex/confluence/{cloudId}` (`confluence_client.py:17,61`);
  `token_refresh.get_valid_access_token` hard-codes `expires_at` + refresh logic;
  `ConfluenceTokenManager` resolves tokens from `OAuthSessions`.
- **No shared / public Confluence KB.** Cloud-typed KBs are forced private: the create endpoint
  clears `access_grants` for any non-`local` type (`routers/knowledge.py:293-294`) and
  `update_knowledge_access_by_id` rejects grant changes on non-local KBs (`:577-582`). KBs are
  always owned by the authorising user; there is no service/shared owner concept.

Two runtime caveats discovered:

- The Confluence router is mounted **once at startup** gated on `ENABLE_CONFLUENCE_SYNC`
  (`main.py:2011-2013`); the scheduler is started once in lifespan (`main.py:927-932`). Toggling
  config in the admin panel does **not** mount/unmount the router or start/stop the scheduler.
- Latent bug: `ConfluenceSyncProvider.create_worker` (`confluence/provider.py:51`) does not
  declare the `use_shared_loader` kwarg that `SyncProvider.execute_sync` passes
  (`sync/provider.py:172-180`) — with `USE_SHARED_LOADER` enabled, a Confluence sync raises
  `TypeError` immediately.
- **Confluence shared-loader sync has never worked.** Beyond the `create_worker` bug, the
  `genai-utils` loader-worker has **no Confluence source client registered** — a
  `source='confluence'` job dies at `source_for('confluence')` with `KeyError`. Confluence sync
  therefore only works via the **legacy in-pod pipeline** (`_download_file_content`), which
  fetches page bodies and renders HTML→Markdown in-process. That path works fine for both auth
  modes; shared-loader offload for Confluence is unbuilt (specified in the cross-repo section).

### Key Discoveries

- A second auth mode for the **same** provider should *not* introduce a new `provider_type`.
  Keep `type='confluence'` / `meta_key='confluence_sync'` and discriminate with an `auth_mode`
  field inside the KB's `confluence_sync` meta — the scheduler discovers KBs by `type` and
  `PROVIDER_FILE_ID_PREFIXES` stays untouched (`sync/provider.py:42-46`).
- Confluence Cloud's **REST API v2 works with Basic auth** when addressed at the site directly
  (`https://{site}.atlassian.net/wiki/api/v2/...`). So the existing v2 `ConfluenceClient` can be
  reused for Basic auth by changing only the base URL and the `Authorization` header — no
  separate v1 client is required.
- A public read-only KB in this fork = exactly one `access_grant` row
  `principal_type='user', principal_id='*', permission='read'` and no `write` row
  (`access_grants.py:521-527,688-693`). Setting it via `AccessGrants.set_access_grants(...)`
  directly (not the user router) cleanly bypasses the non-local-type guards without weakening them.
- The configs router has a clean per-feature GET/POST pattern — `/agent_proxy` (`configs.py:874-890`),
  `/2fa` (`:959-985`), `/email` (`:756-779`). A new `/confluence` pair slots in identically.
- The admin Settings tab system requires three coordinated edits to add a tab: `ADMIN_SETTINGS_TABS`
  in `features.ts:97-116`, the `allSettings` registry + sidebar icon in `Settings.svelte`, and a
  content branch in the `{#if selectedTab === ...}` chain.
- No DB schema change is needed anywhere: all new state lives in `PersistentConfig`, the KB
  `meta` JSON, and the existing `access_grant` table.
- The loader-worker job contract (`base_worker._item_from_file_info`) already passes
  `source_credential` and `source_descriptor` through opaquely, so a future Confluence
  `basic_auth` job needs no wire-schema change — only a new `credential_type` value and a new
  source client (cross-repo section).

## Desired End State

- A **Cloud Sync** tab exists in `/admin/settings`, showing the Confluence logo, with:
  enable toggles, an auth-method choice (OAuth credentials *or* username + API token), the
  relevant credential inputs, a "Test connection" action for Basic auth, the sync interval, and
  a sharing-mode choice (per-user *or* shared).
- An admin can fully configure and enable Confluence without editing Helm/env, and without a
  pod restart.
- With `auth_mode = basic`, Confluence syncs using a configured username + API token against the
  site directly.
- With `kb_mode = shared`, a single `confluence`-typed KB exists, owned by an admin, granted
  `user:*:read`, syncs the full set of accessible Confluence spaces on the scheduler interval,
  and is readable by every user in chat retrieval. Non-admin users no longer see Confluence
  self-service entry points.
- Helm keys exist for every new setting; all new user-facing strings have en-US + nl-NL i18n.

Verification: see per-phase Success Criteria.

## What We're NOT Doing

- **Not** supporting self-hosted Confluence Data Center / Server. Intermax is Cloud; the Basic-auth
  path targets Confluence **Cloud** v2 API only. (DC would need a v1 client — separate effort.)
- ~~**Not** migrating OneDrive / Google Drive config into the Cloud Sync tab.~~
  **Amended 21-05-2026:** scope expanded at the user's request — OneDrive and Google Drive
  config (OAuth-only) were migrated into the Cloud Sync tab as part of Phase 1. See
  "Phase 1 (extended)" below.
- **Not** building per-KB sharing or per-KB auth selection. Both are global settings, per the
  agreed design.
- **Not** building group-scoped shared KBs — the shared KB is public to all users.
- **Not** implementing the `genai-utils` loader-worker Confluence source within Phases 1–3.
  Confluence shared-loader support is a net-new cross-repo workstream (no Confluence source
  exists there today) — it is **specified** in its own section at the end of this document for
  the genai-utils engineer. Phases 1–3 deliberately force Confluence onto the legacy in-pod
  pipeline, which fully works for both OAuth and Basic auth.
- ~~**Not** removing the OneDrive/Google Drive toggles from `Documents.svelte`.~~
  **Amended 21-05-2026:** the OneDrive and Google Drive toggles were also moved out of
  `Documents.svelte` — the entire "Integration" section there is now removed.

## Implementation Approach

Three independently shippable phases in the **open-webui** repo, plus one **cross-repo**
workstream (genai-utils loader-worker) specified separately at the end. Phase 1 alone unblocks an
OAuth-based Confluence setup (admin-configurable, no Helm edit). Phase 2 adds the Basic-auth
credential path. Phase 3 adds the shared full-content KB. Each phase carries its own i18n and
Helm keys. The cross-repo workstream is **not** on the critical path for Intermax — Confluence
runs on the in-pod pipeline regardless; the loader-worker is a scaling optimization.

Global model fixed across phases:

- `auth_mode` ∈ `{oauth, basic}` — global config (`CONFLUENCE_AUTH_MODE`), also stamped into each
  KB's `confluence_sync` meta at creation so existing KBs keep their mode if the global flips.
- `kb_mode` ∈ `{per_user, shared}` — global config (`CONFLUENCE_KB_MODE`).
- Recommended pairings: `oauth + per_user` (per-user permission fidelity) and `basic + shared`
  (one service credential, the Intermax case). All four combinations function; `basic + per_user`
  flattens permissions (every user's KB syncs via the one service credential) and is documented
  as such.

---

## Phase 1: Cloud Sync admin tab + Confluence config plumbing

### Overview

Introduce the Cloud Sync tab and a dedicated `/api/v1/configs/confluence` endpoint, making the
existing OAuth integration fully admin-configurable. Remove the startup-only mount caveat. Fix the
`create_worker` kwarg bug.

### Changes Required

#### 1a. Backend — promote the page limit to a configurable setting

**File**: `backend/open_webui/config.py` (Confluence block, ~line 2940)
**Changes**: Promote `CONFLUENCE_MAX_PAGES_PER_SYNC` from a plain `int` to a `PersistentConfig`
(`confluence.max_pages_per_sync`) so it is admin-editable. Default `500`; treat `0` as
**no limit**. Register it on `app.state.config` in `main.py` alongside the other Confluence
configs. (`CONFLUENCE_MAX_PAGE_SIZE_MB` stays an env-only int — out of scope.)

**File**: `backend/open_webui/services/sync/base_worker.py`
**Changes**: The per-sync file cap at `base_worker.py:2056` is `min(max_files_config,
KNOWLEDGE_MAX_FILE_COUNT)`. Make it treat a falsy (`0`/`None`) `max_files_config` as "no provider
cap" and fall back to `KNOWLEDGE_MAX_FILE_COUNT` alone. `ConfluenceSyncWorker.max_files_config`
reads the new `PersistentConfig` `.value` and returns `None` when it is `0`. Note: with the
page limit cleared, `KNOWLEDGE_MAX_FILE_COUNT` still applies as a KB-wide safety net unless that
too is unset — call this out in the Cloud Sync tab helper text.

#### 1b. Backend — config endpoint

**File**: `backend/open_webui/routers/configs.py`
**Changes**: Add a `/confluence` GET/POST pair, modelled on `/agent_proxy` (`configs.py:874-890`).
The client secret is never returned — expose a boolean `HAS_CONFLUENCE_OAUTH_CLIENT_SECRET`
instead (mirrors `main.py:2802-2805`). On POST, only overwrite the secret when a non-empty value
is supplied, so the UI can re-save other fields without round-tripping the secret.
`CONFLUENCE_MAX_PAGES_PER_SYNC` is sent/received as an int, where `0` means unlimited.

```python
class ConfluenceConfigForm(BaseModel):
    ENABLE_CONFLUENCE_INTEGRATION: Optional[bool] = None
    ENABLE_CONFLUENCE_SYNC: Optional[bool] = None
    CONFLUENCE_OAUTH_CLIENT_ID: Optional[str] = None
    CONFLUENCE_OAUTH_CLIENT_SECRET: Optional[str] = None  # blank/None = keep existing
    CONFLUENCE_SYNC_INTERVAL_MINUTES: Optional[int] = None
    CONFLUENCE_MAX_PAGES_PER_SYNC: Optional[int] = None  # 0 = unlimited

@router.get('/confluence')
async def get_confluence_config(request: Request, user=Depends(get_admin_user)):
    c = request.app.state.config
    return {
        'ENABLE_CONFLUENCE_INTEGRATION': c.ENABLE_CONFLUENCE_INTEGRATION,
        'ENABLE_CONFLUENCE_SYNC': c.ENABLE_CONFLUENCE_SYNC,
        'CONFLUENCE_OAUTH_CLIENT_ID': c.CONFLUENCE_OAUTH_CLIENT_ID,
        'HAS_CONFLUENCE_OAUTH_CLIENT_SECRET': bool(c.CONFLUENCE_OAUTH_CLIENT_SECRET),
        'CONFLUENCE_SYNC_INTERVAL_MINUTES': c.CONFLUENCE_SYNC_INTERVAL_MINUTES,
        'CONFLUENCE_MAX_PAGES_PER_SYNC': c.CONFLUENCE_MAX_PAGES_PER_SYNC,
    }

@router.post('/confluence')
async def set_confluence_config(request: Request, form_data: ConfluenceConfigForm,
                                user=Depends(get_admin_user)):
    c = request.app.state.config
    if form_data.ENABLE_CONFLUENCE_INTEGRATION is not None:
        c.ENABLE_CONFLUENCE_INTEGRATION = form_data.ENABLE_CONFLUENCE_INTEGRATION
    if form_data.ENABLE_CONFLUENCE_SYNC is not None:
        c.ENABLE_CONFLUENCE_SYNC = form_data.ENABLE_CONFLUENCE_SYNC
    if form_data.CONFLUENCE_OAUTH_CLIENT_ID is not None:
        c.CONFLUENCE_OAUTH_CLIENT_ID = form_data.CONFLUENCE_OAUTH_CLIENT_ID.strip()
    if form_data.CONFLUENCE_OAUTH_CLIENT_SECRET:  # blank = keep
        c.CONFLUENCE_OAUTH_CLIENT_SECRET = form_data.CONFLUENCE_OAUTH_CLIENT_SECRET.strip()
    if form_data.CONFLUENCE_SYNC_INTERVAL_MINUTES is not None:
        c.CONFLUENCE_SYNC_INTERVAL_MINUTES = form_data.CONFLUENCE_SYNC_INTERVAL_MINUTES
    if form_data.CONFLUENCE_MAX_PAGES_PER_SYNC is not None:
        c.CONFLUENCE_MAX_PAGES_PER_SYNC = max(0, form_data.CONFLUENCE_MAX_PAGES_PER_SYNC)
    return await get_confluence_config(request, user)
```

Assigning to a `PersistentConfig` attribute on `app.state.config` persists to the DB config blob.

#### 2. Backend — remove the startup-only mount caveat

**File**: `backend/open_webui/main.py`
**Changes**:
- Mount the Confluence router **unconditionally** (drop the `if ... ENABLE_CONFLUENCE_SYNC`
  guard at `main.py:2011-2013`). Endpoints are admin/user-gated and already no-op without config;
  an unmounted router is what makes runtime enablement impossible.
- Always call `start_confluence_scheduler(app)` in lifespan (`main.py:927-932`).

**File**: `backend/open_webui/services/sync/scheduler.py`
**Changes**: Make `SyncScheduler` resilient to runtime toggling — `start()` always creates the
loop; `_execute_due_syncs()` (or the top of the `_run` loop) re-reads `self._enable_config.value`
each tick and returns early when disabled. This removes the restart requirement for
`ENABLE_CONFLUENCE_SYNC` without per-provider start/stop wiring.

#### 3. Backend — fix `create_worker` and force Confluence in-pod

**File**: `backend/open_webui/services/confluence/provider.py`
**Changes**: Add `use_shared_loader: bool = False` to `ConfluenceSyncProvider.create_worker` so
it no longer raises `TypeError` when `execute_sync` passes the kwarg (`sync/provider.py:172-180`).
**Pass `use_shared_loader=False`** into `ConfluenceSyncWorker(...)` regardless of the incoming
value, with a comment explaining that the `genai-utils` loader-worker has no Confluence source
client — Confluence must run the legacy in-pod pipeline. This single line is the switch to flip
once the cross-repo loader-worker workstream (final section) ships.

#### 4. Frontend — API client

**File**: `src/lib/apis/configs/index.ts`
**Changes**: Add `getConfluenceConfig(token)` / `setConfluenceConfig(token, config)` against
`${WEBUI_API_BASE_URL}/configs/confluence`, copying the `getAgentProxyConfig`/`setAgentProxyConfig`
boilerplate (`configs/index.ts:905-930`).

#### 5. Frontend — Cloud Sync tab registration

**File**: `src/lib/utils/features.ts`
**Changes**: Add `'cloud-sync'` to `ADMIN_SETTINGS_TABS` (`features.ts:97-116`). Treat it as a
normal admin tab (no special gating branch needed in `isAdminSettingsTabEnabled`).

**File**: `src/lib/components/admin/Settings.svelte`
**Changes**:
- Import the new panel: `import CloudSync from './Settings/CloudSync.svelte';`
- Add an `allSettings` entry: `{ id: 'cloud-sync', title: 'Cloud Sync',
  route: '/admin/settings/cloud-sync', keywords: ['cloud', 'sync', 'confluence', 'onedrive',
  'google drive', 'integration'] }`.
- Add a sidebar icon branch for `cloud-sync` (use an existing cloud/arrow icon, e.g. reuse an
  icon from `../icons/`).
- Add a content branch: `{:else if selectedTab === 'cloud-sync'}` mounting `<CloudSync
  on:save={async () => { await config.set(await getBackendConfig()); }} />`.

#### 6. Frontend — Cloud Sync panel

**File**: `src/lib/components/admin/Settings/CloudSync.svelte` (new)
**Changes**: New panel modelled on `Settings/Email.svelte`. Phase 1 content:
- Header row with `<Confluence className="size-5" />` (`src/lib/components/icons/Confluence.svelte`)
  and a "Confluence" title.
- `Switch` for `ENABLE_CONFLUENCE_INTEGRATION` and `ENABLE_CONFLUENCE_SYNC`.
- Text input for `CONFLUENCE_OAUTH_CLIENT_ID`.
- Password input for `CONFLUENCE_OAUTH_CLIENT_SECRET` — shows a "configured" placeholder when
  `HAS_CONFLUENCE_OAUTH_CLIENT_SECRET` is true and the field is left blank.
- Number input for `CONFLUENCE_SYNC_INTERVAL_MINUTES`.
- Number input for `CONFLUENCE_MAX_PAGES_PER_SYNC` — blank or `0` means **no limit**; helper
  text states this explicitly and notes the KB-wide `KNOWLEDGE_MAX_FILE_COUNT` still applies.
- Loads via `getConfluenceConfig` in `onMount`; `submitHandler` calls `setConfluenceConfig` then
  `dispatch('save')`.
- Structure the Confluence block as a self-contained section so OneDrive/Google Drive sections
  can be appended later.

#### 7. Frontend — move the Confluence toggle out of Documents

**File**: `src/lib/components/admin/Settings/Documents.svelte`
**Changes**: Remove the Confluence `Switch` row (`Documents.svelte:1471-1474`) from the
Integration section to avoid a duplicate control. Leave the Google Drive and OneDrive rows.

#### 8. i18n

**Files**: `src/lib/i18n/locales/en-US/translation.json`, `src/lib/i18n/locales/nl-NL/translation.json`
**Changes**: Add (alphabetically) all new strings — tab title "Cloud Sync", "Confluence",
"Confluence OAuth Client ID", "Confluence OAuth Client Secret", "Sync interval (minutes)",
"Maximum pages per sync", "Leave empty for no limit", enable-toggle labels, helper text. en-US
may use empty-string = key; nl-NL must be translated.

### Success Criteria

#### Automated Verification

- [x] Backend lints clean for changed files: `npm run lint:backend` (pylint 9.95/10; only 2
      pre-existing errors, none in changed hunks)
- [x] Backend imports without error: `python -c "import open_webui.main"`
- [x] Type-check introduces no new errors vs. baseline: `npm run check` (only the baseline
      `getContext('i18n')` store-type pattern shared by every component; the one genuine new
      implicit-`any` was typed)
- [x] Frontend lints clean for changed files: `npm run lint:frontend` (CloudSync.svelte,
      configs/index.ts, features.ts clean; 14 errors all pre-existing in Settings/Documents)
- [x] Production build succeeds: `npm run build` (built in 59.33s)
- [x] `GET /api/v1/configs/confluence` returns 200 for an admin and 401/403 for a non-admin
      (route registered; GET + POST both gated by `get_admin_user`)

#### Manual Verification

- [ ] The Cloud Sync tab appears in `/admin/settings` with the Confluence logo
- [ ] Entering an OAuth client ID/secret + interval and saving persists across a page reload
- [ ] The client secret is never sent to the browser (only `HAS_..._SECRET` boolean)
- [ ] Toggling `ENABLE_CONFLUENCE_SYNC` on, then creating a Confluence KB and authorising via
      OAuth, runs a successful sync — with no pod restart
- [ ] nl-NL UI shows Dutch labels throughout the tab

**Implementation Note**: After Phase 1 automated verification passes, pause for manual
confirmation before starting Phase 2.

---

## Phase 1 (extended): OneDrive + Google Drive migration into the Cloud Sync tab

Added 21-05-2026 at the user's request. Migrates the OneDrive and Google Drive **OAuth-only**
setup into the same Cloud Sync admin tab, so all three providers are admin-configurable in one
place. No Basic-auth or shared-KB equivalent — those are Confluence-specific (Phases 2–3).

### Changes Required

#### Backend

- **`config.py`** — promoted env-only vars to `PersistentConfig` so they are admin-editable:
  `GOOGLE_DRIVE_MAX_FILES_PER_SYNC` (`google_drive.max_files_per_sync`),
  `ONEDRIVE_MAX_FILES_PER_SYNC` (`onedrive.max_files_per_sync`),
  `ENABLE_ONEDRIVE_PERSONAL`/`_BUSINESS` (`onedrive.enable_personal`/`_business`),
  `ONEDRIVE_CLIENT_ID_PERSONAL`/`_BUSINESS` (`onedrive.client_id_personal`/`_business`).
  `0` = no per-sync file limit. `ONEDRIVE_CLIENT_ID` stays a plain env (base default).
- **Worker `max_files_config`** — `OneDriveSyncWorker` / `GoogleDriveSyncWorker` now read
  `.value or None` (0 → fall back to `KNOWLEDGE_MAX_FILE_COUNT`).
- **`.value` fixes** — `onedrive/auth.py`, `onedrive/token_refresh.py` for the promoted
  `ONEDRIVE_CLIENT_ID_BUSINESS`.
- **`main.py`** — registered all the new/previously-unregistered cloud-sync `PersistentConfig`s
  on `app.state.config`; mounted the `onedrive` + `google-drive` sync routers unconditionally
  (runtime enablement, like Confluence); fixed the `config.features` + admin-config reads to use
  `app.state.config` / `.value`.
- **`configs.py`** — new `/configs/google_drive` and `/configs/onedrive` GET/POST pairs
  (admin-gated), mirroring `/confluence`. No secret masking — both use PKCE public clients;
  client IDs / API key are browser-exposed already.

#### Frontend

- **`CloudSync.svelte`** — added a **Google Drive** section (client ID, API key, interval, max
  files) and an **OneDrive** section (Accounts sub-group with personal/business toggles + client
  IDs, SharePoint sub-group with URL + tenant ID, Sync sub-group). One form, one Save posting all
  three providers.
- **`configs/index.ts`** — `getGoogleDriveConfig`/`setGoogleDriveConfig`,
  `getOneDriveConfig`/`setOneDriveConfig`.
- **`Documents.svelte`** — removed the OneDrive + Google Drive toggle rows; the now-empty
  "Integration" section is deleted entirely. The three integration flags are stripped from the
  RAGConfig round-trip so the Documents tab never overwrites Cloud Sync-owned state.

#### Helm

- Added `onedriveClientIdPersonal` (configmap env `ONEDRIVE_CLIENT_ID_PERSONAL`) — the business
  client ID already had a key. All other OneDrive/Google Drive env keys already existed.

### Success Criteria

#### Automated Verification

- [x] Backend imports without error: `python -c "import open_webui.main"`
- [x] `/api/v1/configs/google_drive` and `/api/v1/configs/onedrive` registered, admin-gated
- [x] ESLint clean for new/changed frontend files
- [x] `helm template helm/open-webui-tenant` renders with the new key
- [x] Production build succeeds: `npm run build` (built in 56.86s)

#### Manual Verification

- [ ] The Cloud Sync tab shows Confluence, Google Drive, and OneDrive sections
- [ ] Saving Google Drive / OneDrive credentials persists across a reload
- [ ] OneDrive section sub-groups (Accounts / SharePoint) render and toggle correctly
- [ ] Enabling a provider's sync at runtime works without a pod restart
- [ ] The Documents tab no longer shows an Integration section
- [ ] nl-NL labels correct throughout the new sections

---

## Phase 2: Username + API-token (Basic auth) mode

### Overview

Add a static-credential auth path: a configured username + API token + site URL, used to talk to
Confluence Cloud v2 with `Authorization: Basic`. No new provider type — discriminated by an
`auth_mode` field.

### Changes Required

#### 1. Backend — new config keys

**File**: `backend/open_webui/config.py` (Confluence block, ~line 2940)
**Changes**: Add `PersistentConfig` entries:
- `CONFLUENCE_AUTH_MODE` → `confluence.auth_mode`, default `'oauth'`.
- `CONFLUENCE_BASIC_AUTH_USERNAME` → `confluence.basic_auth_username`, default `''`.
- `CONFLUENCE_BASIC_AUTH_API_TOKEN` → `confluence.basic_auth_api_token`, default `''`.
- `CONFLUENCE_SITE_URL` → `confluence.site_url`, default `''` (e.g. `https://x.atlassian.net`).

Register all four on `app.state.config` in `main.py` next to the existing Confluence configs
(`main.py:1427-1428`).

#### 2. Backend — `ConfluenceClient` Basic-auth mode

**File**: `backend/open_webui/services/confluence/confluence_client.py`
**Changes**: Allow constructing the client for Basic auth:
- Add an `auth_mode` parameter (default `'oauth'`) plus optional `site_url`, `basic_username`,
  `basic_api_token`.
- `_v2_url`: in `basic` mode build `f'{site_url.rstrip("/")}/wiki/api/v2/{path}'` instead of the
  `api.atlassian.com/ex/confluence/{cloudId}` gateway (`confluence_client.py:56-61`).
- `_request_with_retry`: in `basic` mode send
  `Authorization: Basic base64(username:api_token)` instead of `Bearer`
  (`confluence_client.py:88`), and skip the 401 → `token_provider` refresh branch
  (`:93-103`) — a 401 with a static credential is terminal.
- The `_paginated_get` absolute-URL rewrite (`:177-178`) must use `site_url` host in basic mode.

#### 3. Backend — credential resolution branch

**File**: `backend/open_webui/services/confluence/provider.py` + `sync_worker.py`
**Changes**:
- `ConfluenceTokenManager.has_stored_token`: in `basic` mode return `True` when
  `CONFLUENCE_BASIC_AUTH_USERNAME` / `_API_TOKEN` / `CONFLUENCE_SITE_URL` are all set.
- `ConfluenceSyncWorker._create_client` (`sync_worker.py:115-122`): read the KB's
  `confluence_sync.auth_mode` (fallback to `CONFLUENCE_AUTH_MODE`); when `basic`, build
  `ConfluenceClient` in basic mode from config and skip OAuth site/cloud_id logic.
- `SyncProvider.execute_sync` / scheduler: for `basic`, bypass `get_valid_access_token` (there is
  nothing to refresh). Simplest: `ConfluenceTokenManager.get_valid_access_token` returns a
  sentinel for `basic` mode and the worker ignores it because `_create_client` reads config
  directly.
- Stamp `auth_mode` into `confluence_sync` meta when a KB is created (so a KB's mode is stable).

#### 4. Backend — site/space browsing for Basic auth

**File**: `backend/open_webui/routers/confluence_sync.py`
**Changes**: The picker proxy routes `/browse/sites|spaces|pages` (`:354,370,412`) and the
`cloud_id` allow-list check in `/sync/items` (`:85-90`) assume OAuth `get_stored_sites`. For
`basic` mode there is a single configured site: `/browse/sites` returns that one site (derived
from `CONFLUENCE_SITE_URL`); the `cloud_id` check is replaced by a site-URL match. `/browse/spaces`
and `/browse/pages` use a basic-mode `ConfluenceClient`.

#### 5. Backend — test-connection endpoint

**File**: `backend/open_webui/routers/confluence_sync.py`
**Changes**: Add `POST /api/v1/confluence/auth/test` (`get_admin_user`) that builds a basic-mode
`ConfluenceClient` from the submitted (or stored) credentials and calls
`list_spaces(limit=1)`; returns `{ok: bool, detail: str, space_count?: int}`.

#### 6. Backend — confirm Confluence stays in-pod

No `_item_from_file_info` override and no shared-loader work in this phase. Phase 1 step 3
already forces `use_shared_loader=False` for Confluence, so both OAuth and Basic-auth KBs run the
legacy in-pod pipeline — which already downloads page bodies, renders HTML→Markdown, and enriches
metadata for either auth mode. Verify a Basic-auth sync completes via the in-pod path with
`USE_SHARED_LOADER` both off and on (it must behave identically — Confluence ignores the flag).
The shared-loader path for Confluence is the cross-repo workstream at the end of this document;
the `_item_from_file_info` override belongs there, shipped together with the genai-utils source
client.

#### 7. Frontend — Cloud Sync panel: auth-method section

**File**: `src/lib/components/admin/Settings/CloudSync.svelte`
**Changes**: Add an auth-method selector (segmented control / radio): "OAuth credentials" vs
"Username + API token". When Basic is selected, show inputs for site URL, username, API token
(password field, "configured" placeholder pattern) and a **Test connection** button calling
`testConfluenceConnection`. Extend `ConfluenceConfigForm` / `getConfluenceConfig` /
`setConfluenceConfig` with the new fields (`CONFLUENCE_AUTH_MODE`, `CONFLUENCE_SITE_URL`,
`CONFLUENCE_BASIC_AUTH_USERNAME`, `HAS_CONFLUENCE_BASIC_AUTH_API_TOKEN`).

**File**: `src/lib/apis/confluence/index.ts`
**Changes**: Add `testConfluenceConnection(token)` → `POST /confluence/auth/test`.

#### 8. i18n + Helm

**Files**: en-US + nl-NL translations — auth-method labels, site URL, username, API token,
"Test connection", success/failure messages.

**Files**: `helm/open-webui-tenant/values.yaml`, `templates/open-webui/configmap.yaml`,
`templates/secrets.yaml`, `templates/external-secrets.yaml`, `templates/open-webui/deployment.yaml`
**Changes**: Add `confluenceAuthMode`, `confluenceSiteUrl`, `confluenceBasicAuthUsername` as
config (configmap env vars `CONFLUENCE_AUTH_MODE`, `CONFLUENCE_SITE_URL`,
`CONFLUENCE_BASIC_AUTH_USERNAME`); add `confluenceBasicAuthApiToken` as a secret
(`confluence-basic-auth-api-token`, env `CONFLUENCE_BASIC_AUTH_API_TOKEN`) following the existing
`confluenceOauthClientSecret` pattern, including the 1Password external-secrets field.

### Success Criteria

#### Automated Verification

- [x] Backend lints clean for changed files: `npm run lint:backend` (pylint 9.39/10
      on changed files; only pre-existing C/W/R warnings, no errors in changed hunks)
- [x] Backend imports without error: `python -c "import open_webui.main"`
- [x] Type-check introduces no new errors vs. baseline: `npm run check` (only the
      baseline `getContext('i18n')` store-type pattern in CloudSync.svelte; no new
      genuine errors, `apis/confluence` clean)
- [x] Frontend lints clean: `npm run lint:frontend` (CloudSync.svelte,
      confluence/index.ts, configs/index.ts clean)
- [x] Production build succeeds: `npm run build` (built in 1m 24s)
- [x] `helm template helm/open-webui-tenant` renders with the new keys set and unset
- [x] Unit test: `ConfluenceClient` in basic mode builds the correct base URL and
      `Authorization: Basic` header (`backend/open_webui/test/services/confluence/`,
      4 tests pass)

#### Manual Verification

- [ ] With `auth_mode = basic` and valid Intermax credentials, "Test connection" succeeds
- [ ] "Test connection" with a bad token returns a clear failure message
- [ ] Creating a Confluence KB in basic mode, picking a space, runs a successful sync; pages
      appear as Markdown files
- [ ] The API token is never returned to the browser
- [ ] A 401 mid-sync (revoked token) fails the sync cleanly without an infinite refresh loop
- [ ] nl-NL labels correct throughout the auth-method section

**Implementation Note**: After Phase 2 automated verification passes, pause for manual
confirmation before starting Phase 3.

---

## Phase 3: Shared, read-only, full-content KB mode

### Overview

Add a global `kb_mode`. In `shared` mode, a single admin-owned `confluence` KB syncs the full set
of accessible Confluence spaces and is granted public read access; non-admins lose Confluence
self-service.

### Changes Required

#### 1. Backend — config keys

**File**: `backend/open_webui/config.py` + `main.py`
**Changes**:
- `CONFLUENCE_KB_MODE` → `confluence.kb_mode`, default `'per_user'`.
- `CONFLUENCE_SHARED_KB_OWNER_ID` → `confluence.shared_kb_owner_id`, default `''`. This is the
  user id that owns the shared KB. **Empty string = no owner** — the KB is treated as
  system-owned (`user_id = ''`). The `user_id` column is plain `Text` with no FK
  (`knowledge.py:46`), so an empty/sentinel value is storable.
Register both on `app.state.config`. Expose `confluence_kb_mode` in the frontend
`config.features` block (`main.py:2726-2733`) so the workspace UI can react.

**Constraint**: a no-owner shared KB is only valid with `auth_mode = basic` — the basic service
credential is global config and needs no user token. With `auth_mode = oauth` the shared KB
**must** have an owner, because the scheduler resolves the OAuth token by `kb.user_id`
(`sync/scheduler.py:102-106`). The provisioning endpoint and the Cloud Sync tab enforce this.

#### 2. Backend — shared KB provisioning

**File**: `backend/open_webui/routers/confluence_sync.py`
**Changes**: Add `POST /api/v1/confluence/shared/provision` (`get_admin_user`):
- Find an existing KB with `type='confluence'` and `meta.confluence_sync.shared == True`;
  create one if absent (`Knowledges.insert_new_knowledge`, `type='confluence'`, name e.g.
  "Confluence").
- Owner: `user_id = CONFLUENCE_SHARED_KB_OWNER_ID` (empty string when "no owner" is chosen).
  Reject the request (`400`) if `auth_mode == 'oauth'` and no owner is set — see the constraint
  in step 1. If the KB already exists and the owner setting changed, update `knowledge.user_id`.
- Set `meta.confluence_sync.shared = True`, `meta.confluence_sync.sync_all_spaces = True`, and
  `meta.confluence_sync.auth_mode` from the global `CONFLUENCE_AUTH_MODE`.
- Grant public read directly via
  `AccessGrants.set_access_grants('knowledge', kb.id, [{'principal_type':'user',
  'principal_id':'*','permission':'read'}])` — this bypasses the non-local-type guards in the
  user router (`knowledge.py:293-294,577-582`) **by not going through it**; the guards stay intact
  for normal user KBs.
- Add `POST /api/v1/confluence/shared/sync` to trigger an immediate sync, and have
  `GET` status surface the shared KB id + last sync result for the admin panel.

#### 3. Backend — "sync all spaces"

**File**: `backend/open_webui/services/confluence/sync_worker.py`
**Changes**: When `confluence_sync.sync_all_spaces` is set, resolve sources at sync time by
calling `ConfluenceClient.list_all_spaces()` and treating each space as a `folder` source
(reusing `_collect_folder_files`). New spaces are picked up automatically on each run; removed
spaces drop out via the existing deletion handling. Respect `CONFLUENCE_MAX_PAGES_PER_SYNC`.

#### 4. Backend — protect the shared KB from suspension hard-delete

**File**: `backend/open_webui/services/deletion/cleanup_worker.py`
**Changes**: Skip KBs with `meta.confluence_sync.shared == True` in the suspended-expired
hard-delete (`cleanup_worker.py:159-167`). A shared KB losing access should surface an admin
warning, not silently self-delete. (Suspension still hides it from retrieval — acceptable and
visible in the admin panel status.)

#### 5. Backend — keep service sync running as the owner

No change required: the scheduler already runs `execute_sync` as `kb.user_id`
(`sync/scheduler.py:102-106`). The shared KB's owner-admin holds the OAuth token (oauth mode) or
the worker reads the global service credential (basic mode, from Phase 2).

#### 6. Frontend — sharing-mode section in Cloud Sync

**File**: `src/lib/components/admin/Settings/CloudSync.svelte`
**Changes**: Add a sharing-mode selector (Per-user / Shared). When Shared, show:
- An **owner** dropdown for `CONFLUENCE_SHARED_KB_OWNER_ID` — a "No owner (system)" option plus
  one entry per admin user. Populate the admin list from the existing users API
  (`getUsers` / `GET /api/v1/users/` filtered to `role === 'admin'`). When `auth_mode = oauth`,
  disable the "No owner" option and require a selection; when `auth_mode = basic`, "No owner" is
  the default. Show inline helper text explaining the constraint.
- A "Shared knowledge base" status block — provision state, last sync result, and **Provision** /
  **Sync now** buttons calling the new endpoints.

Extend the config form with `CONFLUENCE_KB_MODE` and `CONFLUENCE_SHARED_KB_OWNER_ID`.

#### 7. Frontend — hide self-service for non-admins in shared mode

**Files**: `src/lib/components/workspace/Knowledge/Knowledge.svelte:287-297`,
`CreateKnowledgeBase.svelte`, `KnowledgeBase/EmptyStateCards.svelte`
**Changes**: When `$config.features.confluence_kb_mode === 'shared'` and the user is not an admin,
hide the "From Confluence" create entry and the `?type=confluence` create path. The shared KB
still appears in their KB list (via the `user:*:read` grant) as read-only.

#### 8. i18n + Helm

**Files**: en-US + nl-NL — sharing-mode labels, owner-dropdown labels ("No owner (system)",
the oauth-requires-owner helper text), shared-KB status strings, provision/sync buttons.

**Files**: Helm — add `confluenceKbMode` (configmap env `CONFLUENCE_KB_MODE`) and
`confluenceSharedKbOwnerId` (configmap env `CONFLUENCE_SHARED_KB_OWNER_ID`, default empty).

### Success Criteria

#### Automated Verification

- [x] Backend lints clean: `npm run lint:backend` (errors-only pylint 9.96/10 on changed
      files; the 3 `E` findings are all pre-existing, none in Phase-3 hunks)
- [x] Backend imports without error: `python -c "import open_webui.main"`
- [x] Type-check introduces no new errors vs. baseline: `npm run check` (only the baseline
      `getContext('i18n')` store-type pattern in CloudSync.svelte; `apis/confluence` clean)
- [x] Frontend lints clean: `npm run lint:frontend` (CloudSync.svelte + confluence/index.ts
      clean; 5 errors all pre-existing in unchanged lines of Knowledge/CreateKnowledgeBase)
- [x] Production build succeeds: `npm run build` (built in 57.66s)
- [x] `helm template helm/open-webui-tenant` renders with `confluenceKbMode` set and unset
- [x] `POST /api/v1/confluence/shared/provision` is admin-only (401/403 for non-admin)
      (route registered; provision/sync/status all gated by `get_admin_user`)

#### Manual Verification

- [ ] In `shared` mode, provisioning creates one `confluence` KB visible to every user
- [ ] With `auth_mode = basic` and owner "No owner (system)", provisioning succeeds and the
      shared KB syncs via the global service credential
- [ ] With `auth_mode = oauth` and no owner selected, provisioning is rejected with a clear error
- [ ] Selecting an admin from the owner dropdown sets `knowledge.user_id` to that admin
- [ ] A non-admin user sees the shared KB in their list, read-only (no edit/delete/add-file), and
      can use it in chat retrieval
- [ ] A non-admin user no longer sees any "From Confluence" create entry point
- [ ] "Sync now" populates the shared KB with pages from all accessible spaces; a newly created
      Confluence space appears after the next sync
- [ ] Scheduled background sync updates the shared KB on the configured interval
- [ ] Suspending the shared KB (revoke credential) does not hard-delete it after 30 days; status
      is visible in the Cloud Sync tab
- [ ] nl-NL labels correct throughout the sharing-mode section

**Implementation Note**: After Phase 3, confirm the full Intermax scenario end-to-end
(`basic + shared`) with the user before closing the work.

---

## Cross-Repo Workstream: Loader-Worker Confluence Source (genai-utils)

**This section is a spec for the `genai-utils` engineer — it is NOT implemented by Phases 1–3.**
It is optional for the Intermax rollout: Confluence runs fine on the in-pod pipeline. Build this
only when Confluence sync needs to offload heavy processing to the per-tenant loader-worker pod
(the same reason OneDrive/Google Drive use it).

### Why it is net-new

The loader-worker (`genai-utils/api/gateway/loader_worker/`) does **download → parse/chunk via
the shared doc-processor → push chunks to `/ingest`**. It has source clients only for `onedrive`
and `google_drive` (`sources/__init__.py:11-14`). A `source='confluence'` job dies at
`source_for('confluence')` with `KeyError` (`sources/base.py:90-103`). It recognises only two
credential types — `user_oauth` and `app_token` (`sources/base.py:16-17`); there is no
`basic_auth`. None of the open-webui Confluence client / HTML-renderer code is imported or
duplicated there (the cross-repo coupling is deliberately one-way). So Confluence support is a
new source client plus a new credential type — not a small branch.

### The job contract (already in place, no change needed)

`base_worker._item_from_file_info` (`open-webui` `base_worker.py:226-266`) emits per item:
`source`, `source_descriptor` (free-form dict), `source_credential` (opaque string),
`credential_type`, `file_id`, `source_id`, `filename`, `content_type`, `metadata`. Jobs are
submitted via HTTP `POST {LOADER_WORKER_URL}/tenants/{TENANT_NAME}/jobs`
(`pipeline_client.py:42-76`). `_ItemRequest` / `SourceItem` already pass `source_credential` and
`source_descriptor` through opaquely — **no wire-schema change is required**.

### Changes in genai-utils

| File | Change |
|---|---|
| `loader_worker/sources/base.py:16-17` | Add `CREDENTIAL_TYPE_BASIC_AUTH = "basic_auth"` |
| `loader_worker/routes/jobs.py:33-37,50` | Add `basic_auth` to `_VALID_CREDENTIAL_TYPES` (else jobs are rejected `400` at `routes/jobs.py:155-160`) |
| `loader_worker/sources/confluence.py` | **New file** — `ConfluenceSourceClient` (see below) |
| `loader_worker/sources/__init__.py:11-14` | Import the new module so it self-registers via `register_source("confluence", ...)` |

`job_runner.py`, `doc_processor_client.py`, `ingest_client.py`, `job_store.py`, `settings.py`
need **no change** — they are source-agnostic; `source_for(item.source)` dispatches and the
existing `TokenExpiredError` / `HardSourceError` / `SourceAccessRevokedError` plumbing applies.

### The new `ConfluenceSourceClient`

Implements `SourceClient.fetch(item) -> (bytes, filename, content_type)` (`sources/base.py:69-83`).
Per item it fetches one Confluence page body and returns parseable bytes. Branch on
`item.credential_type`:

- `user_oauth` → `Authorization: Bearer {source_credential}`, base URL
  `https://api.atlassian.com/ex/confluence/{cloud_id}/wiki/api/v2`; 401 → `TokenExpiredError`
  (retry next sync with a fresh token).
- `basic_auth` → `Authorization: Basic base64(source_credential)` where `source_credential`
  is the `"username:api_token"` pair, base URL `https://{site}.atlassian.net/wiki/api/v2`;
  401 → `HardSourceError` (API tokens do not refresh — a 401 is a bad/revoked credential).
- 403/404 → `SourceAccessRevokedError`; 429/5xx → retry. `httpx` supports an arbitrary base URL
  and headers, so there is no library limitation — only new code.

`cloud_id` (oauth) / `site_url` (basic) and `page_id` travel in `source_descriptor`.

**Content & enrichment — recommended split:** keep page enumeration, version-based change
detection, and labels/ancestors enrichment in open-webui's discovery
(`_collect_folder_files` / `_collect_single_file` already call the Confluence API), and ship the
enrichment in the job item's `metadata`. The loader-worker `fetch` then only needs the page body.
Return it as `text/markdown` bytes (the doc-processor maps `text/markdown` → `MD`) — which means
the HTML→Markdown renderer (`open-webui/.../confluence/html_renderer.py`) must be **ported** into
genai-utils — or, simpler, return raw `text/html` bytes and let the doc-processor parse the HTML.
Decide with the team; returning HTML avoids porting the renderer but changes RAG output slightly.

### Open-webui side, shipped together with the above (not in Phases 1–3)

1. Override `ConfluenceSyncWorker._item_from_file_info` to emit `credential_type` (`basic_auth`
   or `user_oauth`), the `"username:api_token"` pair (basic) or Bearer token (oauth) in
   `source_credential`, and `site_url` / `cloud_id` / `page_id` / `space_id` in
   `source_descriptor`.
2. Flip the Phase 1 step 3 switch: stop forcing `use_shared_loader=False` for Confluence.

### Risk

The loader-worker model is "1 item = 1 downloadable blob". Confluence has no file bytes — content
is HTML from a paged REST API. Pre-computing enrichment in open-webui (above) keeps the
loader-worker `fetch` simple; doing it inside the loader-worker means re-fetching labels/ancestors
per page. Prefer the pre-compute approach.

---

## Testing Strategy

### Unit Tests

- `ConfluenceClient` basic mode: base-URL construction (`https://{site}/wiki/api/v2/...`),
  `Authorization: Basic` header, no refresh on 401.
- `auth_mode` resolution: KB meta value overrides nothing / falls back to global config.
- Config endpoint: secret never serialised; blank secret on POST preserves the stored value.
- Extend `backend/open_webui/test/services/sync/test_provider_registry.py` if any prefix/registry
  surface changes (it should not — same `provider_type`).

### Integration Tests

- OAuth path unchanged: existing per-user OAuth sync still works (regression).
- Basic path: end-to-end sync of one space against a Confluence Cloud test site.
- Shared mode: provision → public grant present → non-owner read access via
  `AccessGrants.has_access(..., 'read')` and via retrieval `get_sources_from_items`.

### Manual Testing Steps

1. Phase 1: configure OAuth entirely in the Cloud Sync tab; enable sync without restart; run a
   per-user OAuth sync.
2. Phase 2: switch to basic mode; Test connection; sync one space; verify Markdown output and
   that no secret leaks to the browser.
3. Phase 3: switch to shared mode; provision; verify a second non-admin account sees the shared
   KB read-only and can retrieve from it in chat; verify self-service entry points are hidden.
4. Intermax scenario: `basic + shared`, full-content sync, scheduled refresh.

## Performance Considerations

- **Full-space enumeration** in shared mode runs `list_all_spaces()` + per-space page listing
  every sync, bounded by `CONFLUENCE_MAX_PAGES_PER_SYNC` — now admin-editable (Phase 1) with `0`
  = unlimited. Running it unlimited for an Intermax-scale instance means the **in-pod** pipeline
  processes the whole corpus in-process; watch pod memory/CPU and `DOCUMENT_PROCESSING_TIMEOUT`.
  The existing per-page `version.number` delta check means steady-state syncs only re-process
  changed pages, so the cost spike is the first full sync. For the first rollout prefer a
  generous explicit cap over unlimited; the loader-worker offload (cross-repo section) is the
  real fix for very large instances.
- Basic-mode requests hit the customer site directly (`*.atlassian.net`) rather than the Atlassian
  gateway — same rate-limit posture; the existing 429 `Retry-After` handling
  (`confluence_client.py:105-109`) still applies.
- No change to the scheduler interval default (60 min).

## Migration Notes

- **No database migration** — all new state is `PersistentConfig`, KB `meta` JSON, and existing
  `access_grant` rows.
- Existing OAuth Confluence KBs are unaffected: `auth_mode` defaults to `oauth`; absence of the
  field in `confluence_sync` meta is treated as `oauth`.
- `CONFLUENCE_AUTH_MODE` / `CONFLUENCE_KB_MODE` default to `oauth` / `per_user` — deployments not
  touching the Cloud Sync tab keep current behaviour.
- **Cross-repo**: Confluence shared-loader support is unbuilt and is specified in its own
  section above. Phases 1–3 force Confluence onto the in-pod pipeline, so `USE_SHARED_LOADER`
  has no effect on Confluence — there is no dependency and no blocker for Intermax. The
  loader-worker workstream is a scaling optimization to schedule independently in `genai-utils`.
- Helm: new keys are additive with safe defaults; existing values files need no change unless a
  deployment opts into basic/shared mode.

## References

- Original investigation: this session (Confluence Cloud vs self-hosted; Intermax old ingestion
  pipeline was a bespoke pgvector + MCP RAG, Cloud + email + API token).
- Backend Confluence: `backend/open_webui/services/confluence/`,
  `backend/open_webui/routers/confluence_sync.py`
- Sync abstraction: `backend/open_webui/services/sync/` (`base_worker.py`, `provider.py`,
  `scheduler.py`, `token_refresh.py`, `router.py`)
- Access control: `backend/open_webui/models/access_grants.py`,
  `backend/open_webui/routers/knowledge.py:283-294,577-582`
- Config pattern: `backend/open_webui/routers/configs.py:874-985` (`/agent_proxy`, `/2fa`)
- Admin tabs: `src/lib/components/admin/Settings.svelte`, `src/lib/utils/features.ts:97-181`
- Confluence frontend: `src/lib/apis/confluence/index.ts`,
  `src/lib/components/workspace/Knowledge/ConfluencePickerModal.svelte`,
  `src/lib/components/icons/Confluence.svelte`
- Helm: `helm/open-webui-tenant/values.yaml:430-439`, `configmap.yaml:301-312`,
  `secrets.yaml:28-30`, `external-secrets.yaml:58-61`, `deployment.yaml:122-128`
- Loader-worker (cross-repo): `genai-utils/api/gateway/loader_worker/` — `sources/base.py:16-17`,
  `sources/__init__.py:11-14`, `routes/jobs.py:50,155-160`, `workers/job_runner.py`;
  job contract producer `open-webui` `base_worker.py:226-266,1318-1386`,
  `services/sync/pipeline_client.py:38-76`
- Loader-worker architecture: `thoughts/shared/plans/2026-04-25-shared-services-loader-worker.md`,
  `thoughts/shared/research/2026-04-25-cross-repo-document-ingestion-architecture.md`
