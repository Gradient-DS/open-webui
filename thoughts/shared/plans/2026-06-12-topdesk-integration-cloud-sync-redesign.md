# TOPdesk Integration + Cloud Sync Panel Redesign — Implementation Plan

## Overview

Two coupled workstreams:

1. **TOPdesk integration** — a new cloud-sync provider that syncs TOPdesk **knowledge items**
   into a single **pre-synced, shared, read-only knowledge base**, following the Confluence
   `basic + shared` pattern. Service-account auth only (TOPdesk has no OAuth), GraphQL
   knowledge-base API, admin-driven tree picker for selecting which knowledge-item subtrees
   to sync.
2. **Cloud Sync admin panel redesign** — replace the 1109-line monolithic `CloudSync.svelte`
   with a provider-descriptor-driven **accordion-card** layout: per-provider collapsible cards
   with logo, enable state, and an at-a-glance status line (last sync, file count, state) for
   *all* providers. Adding a future provider becomes one descriptor + one section component.

Driving constraints:

- **Prod-key-only verification.** No TOPdesk test tenant exists; we hold one production API
  key from the client. All live verification is **strictly read-only**, low-volume, and
  happens in a dedicated Phase 0 (schema discovery) and Phase 4 (E2E). Everything in between
  builds against recorded fixtures.
- **Upstream merge compatibility.** Everything is additive: new `services/topdesk/` package,
  new router, new config keys, new frontend components. The only upstream-adjacent touch
  points are the same ones Confluence already touches (`main.py` registration blocks,
  `configs.py`, the admin Settings tab — all already ours).

## Current State Analysis

### The sync abstraction (what we get for free)

`backend/open_webui/services/sync/` provides the full orchestration. A new provider implements:

- `TokenManager` (3 methods) + `SyncProvider` (4 methods) — `services/sync/provider.py:68-127`.
  `SyncProvider.execute_sync()` (`provider.py:129-207`) is concrete: resolves token, builds
  worker, stamps `last_sync_at`/`status`.
- A `BaseSyncWorker` subclass — abstract properties (`base_worker.py:136-181`: `meta_key`,
  `file_id_prefix`, `event_prefix`, `provider_slug`, `internal_request_path`,
  `max_files_config`, `source_clear_delta_keys`) and abstract methods (`base_worker.py:187-322`:
  client lifecycle, `_collect_folder_files`, `_collect_single_file`, `_download_file_content`,
  `_get_cloud_hash`, `_get_provider_file_meta`, `_sync_permissions`, `_verify_source_access`,
  `_handle_revoked_source`, etc.).
- Factory registration: branch in `get_sync_provider()` / `get_token_manager()`
  (`services/sync/provider.py:210-247`) + entry in `PROVIDER_FILE_ID_PREFIXES`
  (`provider.py:43-47`).

In return the base class provides: classification (added/updated/unchanged via
`_get_cloud_hash`), deletion-by-set-difference, file-count caps, cancellation, status events,
stub file rows, and both ingestion paths. **TOPdesk runs the legacy in-pod path**
(`use_shared_loader=False` forced) — same as Confluence; the loader-worker has no TOPdesk
source and that cross-repo work is out of scope.

### Confluence as the template

Confluence (`backend/open_webui/services/confluence/`, router
`backend/open_webui/routers/confluence_sync.py`) is the only provider exercising everything
TOPdesk needs:

- **Service-account auth** (`basic_auth.py`): credentials as `PersistentConfig`, a
  `BASIC_AUTH_SENTINEL` standing in for the OAuth token so `execute_sync` doesn't bail
  (`basic_auth.py:33`), `POST /auth/test` admin probe (`confluence_sync.py:435-483`).
- **Shared read-only KB** (`confluence_sync.py:690-1008`): `_find_shared_kb()` by
  `type + meta.shared`, `POST /shared/provision` creating the KB via
  `Knowledges.insert_new_knowledge` (bypassing the user router) + public read grant
  `user:*:read` via `AccessGrants.set_access_grants` (`:953-957`), `/shared/status` with live
  progress, `/shared/sync`, `DELETE /shared`. Workspace delete/reset blocked by
  `_assert_not_managed_shared_kb` (`routers/knowledge.py:1040-1052`, enforced at `:1069,:1121`);
  suspension hard-delete skipped in `services/deletion/cleanup_worker.py:172`.
- **Synthetic documents**: API records rendered to Markdown (`html_renderer.py`), `size: 0`
  items, pinned `content_type='text/markdown'`, front-matter block, **version-based**
  `_get_cloud_hash` with a per-source `page_map` (`sync_worker.py:411-422`).
- **httpx client skeleton** (`confluence_client.py`): retry with 429 `Retry-After`, 5xx
  backoff, 401-is-terminal in basic mode, cursor pagination.

TOPdesk is *simpler* than Confluence: no OAuth mode, no multi-site discovery, no per-user KBs.
The genuinely new pieces are the **GraphQL client** and the **knowledge-item tree** model.

### The admin panel (what's wrong)

`src/lib/components/admin/Settings/CloudSync.svelte` (1109 lines):

- Three hand-written provider blocks (Confluence 504-843, Google Drive 847-933, OneDrive
  937-1069), no sub-components, no descriptor/loop. Adding a provider = editing 5 places
  (state vars, response type, apply-mapper, `onMount`, `persistConfig`) + a template block.
- The "Sync Settings" block (background toggle + interval + max + helper) is copy-pasted
  verbatim 3× (664-705 / 890-931 / 1026-1067).
- Capability asymmetry (auth-method, sync-mode, test-connection, shared-KB — Confluence only)
  is expressed as inline markup presence, not a model.
- Observability exists only for Confluence's shared KB (status block 764-839 + bespoke 2.5s
  polling 215-248). No cross-provider status.
- Dead file: `src/lib/components/admin/Settings/CloudSync/SpacePickerModal.svelte`
  (imported nowhere).
- Tab registration: `Settings.svelte:169-174` (descriptor), `:626-639` (icon), `:679-687`
  (render); `features.ts:105` (tab id). API clients: `apis/configs/index.ts:1126-1289`
  (3 identical GET/SET pairs), `apis/confluence/index.ts` (test-connection + shared-KB),
  `apis/sync/index.ts:80-162` (`createSyncApi` factory — reusable).

### TOPdesk API facts (from research, 2026-06)

- **Auth**: HTTP Basic with operator username + **application password**; persons can
  alternatively use `Authorization: TOKEN id="..."`. **No OAuth2.** Application passwords are
  shown once, support expiry dates. Read-only "API account" operators (free license) are the
  recommended integration identity, scoped by permission groups + branch/category filters.
  Required permissions: `API Access > REST API` + `Use application passwords`.
- **Knowledge Base API is GraphQL-only** on current SaaS: the legacy REST
  `/tas/api/knowledgeItems` was removed in TOPdesk 2025 R2 (announced July 2024). Fields
  include `id`, `number`, `parent`, `visibility`, `status`, `language`,
  `availableTranslations`, `title`, `description`, `content` (HTML), `keywords`,
  `creationDate`, `modificationDate`. Knowledge items form a **parent/child tree**.
- **Incremental sync**: filter on `modificationDate`; no usable outbound webhooks (TOPdesk
  "webhooks" are inbound; outbound push needs per-tenant Action Sequences). Scheduled polling
  with the existing `SyncScheduler` is the right model.
- **Rate limits**: none published. Default REST page size 10, max 100. Treat as fair-use:
  bounded concurrency, backoff on 429/503.
- **Base URL**: `https://{tenant}.topdesk.net` (SaaS). Per-component API versioning.
- **Flagged uncertainties → resolved by Phase 0**: exact GraphQL endpoint path + query/type
  names + pagination arguments; attachment download mechanism; `content` format confirmation;
  which auth form the client's key is.

### Key Discoveries

- Factory registration points: `services/sync/provider.py:210-247`; `PROVIDER_FILE_ID_PREFIXES`
  at `:43-47` (fallback is `f'{slug}-'`, so `topdesk` → `topdesk-` works, but add the explicit
  entry for consistency).
- `allowed_kb_types` (`routers/knowledge.py:285`) gates only the **user** create endpoint.
  Shared-KB provisioning calls `Knowledges.insert_new_knowledge` directly — so we deliberately
  do **NOT** add `topdesk` to `allowed_kb_types`: users can never self-create TOPdesk KBs;
  only the admin provision endpoint creates the one shared KB.
- `_assert_not_managed_shared_kb` (`knowledge.py:1040`) and the cleanup-worker skip
  (`cleanup_worker.py:172`) are Confluence-hardcoded — they must be generalized for TOPdesk.
- `ConfluenceClient` uses **httpx** (`confluence_client.py:20,50`) — the TOPdesk client uses
  the same library and copies the retry skeleton.
- KB-card provider branding: `CLOUD_PROVIDERS` registry in
  `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte:109`; icons in
  `src/lib/components/icons/` (Confluence/GoogleDrive/OneDrive exist).
- Helm pattern: `helm/open-webui-tenant/values.yaml:457-477` (Confluence config block),
  `:851-854` (secrets), `:933-934` (external-secrets flags); templates under
  `helm/open-webui-tenant/templates/open-webui/`.
- No DB migration needed anywhere: all new state is `PersistentConfig`, KB `meta` JSON, and
  existing `access_grant` rows (same as Confluence Phases 1-3).

## Desired End State

- An admin opens **Cloud Sync** and sees four accordion cards — Confluence, Google Drive,
  OneDrive, TOPdesk — each with logo, enable state, and a status line (synced KBs / files,
  last sync, state). Expanding a card shows its config; one Save persists everything.
- For TOPdesk the admin enters URL + service-account credentials, clicks **Test connection**
  and gets a clear pass/fail, picks knowledge-item subtrees in a tree-picker modal, provisions
  the shared KB, and triggers/schedules syncs — all without Helm edits or pod restarts.
- Every user sees the shared TOPdesk KB read-only, can attach it from the chat `+` menu, and
  gets TOPdesk knowledge-item content in RAG answers with language/keywords metadata.
- All new strings have en-US + nl-NL translations; all new settings have Helm keys.
- `CloudSync.svelte` is decomposed: adding provider #5 means one descriptor entry + one
  section component + one backend config endpoint.

Verification: per-phase success criteria below.

## What We're NOT Doing

- **No incidents / changes / assets sync.** Knowledge items only. Incident interaction is a
  future **agent** concern in `genai-utils` (out of scope for this repo); the only design
  concession here is keeping `topdesk_client.py` + auth cleanly separated from the sync worker
  so they could be lifted later.
- **No per-user TOPdesk KBs.** Shared-KB-only — no `kb_mode`, no per-user picker, no
  `/sync/items` per-user flow, no OAuth (TOPdesk has none).
- **No attachment sync in v1.** The GraphQL attachment mechanism is unverified; Phase 0
  records what it is, but v1 ingests knowledge-item text content only. Attachments become a
  follow-up once verified.
- **No loader-worker (genai-utils) TOPdesk source.** `use_shared_loader=False` is forced,
  exactly like Confluence (`confluence/provider.py` pattern). Cross-repo offload is a separate
  workstream if/when scale demands it.
- **No on-prem Virtual Appliance support.** Target SaaS (`*.topdesk.net`) with the GraphQL KB
  API. Appliances ≤ 2025 R1 (legacy REST KB API) are unsupported.
- **No translation fan-out in v1.** Sync the item's primary content; `language` /
  `availableTranslations` are stored as metadata. Multi-language duplication is a follow-up
  decision once we see real client data in Phase 4.
- **Not migrating the existing Confluence/Drive/OneDrive endpoints or payload shapes.** The
  panel refactor is frontend-structural; backend API contracts stay identical (except the new
  status endpoint and the shared-KB helper extraction, which preserves routes/payloads).

## Implementation Approach

Five phases. Phase 0 de-risks the API. Phases 1 (panel refactor) and 2 (TOPdesk backend) are
**independent and parallelizable**. Phase 3 (TOPdesk frontend) needs both. Phase 4 is live
verification against the client tenant.

Design tenets:

- TOPdesk mirrors the Confluence module layout minus OAuth: `services/topdesk/` with
  `auth.py` (service account only), `topdesk_client.py` (GraphQL), `sync_worker.py`,
  `provider.py`, `scheduler.py`, `sync_events.py`.
- Generalize rather than copy-paste where Confluence built one-off subsystems we now need
  twice: shared-KB lifecycle helpers and the HTML→Markdown renderer move into
  `services/sync/`; Confluence delegates/re-exports so its behavior is unchanged.
- The frontend gets a real capability model: each provider declares
  `{ slug, icon, authModes, hasTestConnection, supportsSharedKb, itemNoun }` and the panel
  renders shared chrome around provider-specific field sections.

---

## Phase 0: TOPdesk API verification (read-only, against prod)

### Overview

Resolve every flagged API uncertainty using the client's production key **before** writing
integration code. Produces a findings doc + sanitized fixtures that Phases 2-3 build and test
against.

**Safety rules (hard requirements):**

- **Read-only**: GET requests and GraphQL **queries** only. No mutations, ever. GraphQL
  introspection is a query — safe.
- **Low volume**: sequential requests, ≤ ~100 total, no concurrency, generous delays. This is
  a production ITSM system.
- **Data hygiene**: responses contain client data. Fixtures committed to the repo must be
  **sanitized/synthesized** — structure preserved, real titles/content/names replaced.
  The raw capture stays local and is deleted after fixture extraction.
- The API key is handled like any production secret: env var in the local shell, never
  committed, never logged.

### Changes Required

#### 1. Credential-form determination

Try, in order, against `{TOPDESK_URL}`:

1. `Authorization: Basic base64(username:key)` — if the client supplied a username; probe with
   a lightweight authenticated GET (candidate: `GET /tas/api/version`; fall back to
   `GET /tas/api/operators/current` — record which works and with what permission errors).
2. `Authorization: TOKEN id="<key>"` (person-token form) if no username exists.

Record: which form authenticates, what account type it is (operator vs person), and what the
permission scope appears to be (which modules return 200 vs 403).

#### 2. GraphQL knowledge-base discovery

- Pin the exact GraphQL endpoint path (consult
  `developers.topdesk.com/explorer/?page=knowledgebase-graphql` first, then verify live).
- Run an introspection query; capture the schema for knowledge-item types.
- Verify with minimal queries: list knowledge items (first page, small page size), pagination
  mechanism (cursor/offset arguments), `modificationDate` filtering, parent/children tree
  traversal, fields `status` / `visibility` / `language` / `availableTranslations` /
  `keywords` / `content`, and confirm `content` is HTML.
- Record the attachment representation (for the v1 exclusion note + future work), and the
  canonical web URL format for a knowledge item (for the `web_url` front-matter link).

#### 3. Deliverables

- **Findings doc**: `thoughts/shared/research/2026-06-topdesk-api-verification.md` — auth
  form, endpoint paths, schema excerpt, pagination/filter syntax, sample (sanitized) queries +
  responses, rate-limit observations (any 429s seen), open risks.
- **Fixtures**: `backend/open_webui/test/services/topdesk/fixtures/*.json` — sanitized
  GraphQL responses: item list page, single item with content, children-of-parent, empty page.
- **Go/no-go checkpoint**: if the GraphQL API deviates materially from the research (e.g.
  pagination or `modificationDate` filtering missing), STOP and revisit the design with the
  user before Phase 2.

### Success Criteria

#### Automated Verification

- [ ] Fixtures parse as JSON and are committed: `python -m json.tool` over each fixture file

#### Manual Verification

- [ ] Findings doc answers all four flagged uncertainties (auth form, GraphQL
      endpoint/schema/pagination, attachment mechanism, content format)
- [ ] Request log confirms: read-only, sequential, ≤ ~100 requests
- [ ] Fixtures contain no real client data (reviewed line-by-line before commit)

**Implementation Note**: Pause after Phase 0 — review findings with the user before Phase 2
work that depends on the schema. Phase 1 may proceed in parallel at any time.

---

## Phase 1: Cloud Sync panel refactor + accordion redesign

### Overview

Decompose `CloudSync.svelte` into a descriptor-driven accordion, add a cross-provider status
endpoint, and make capability asymmetry explicit. **No behavior change to existing provider
config endpoints.** TOPdesk is not in this phase (Phase 3 adds it as a descriptor entry).

### Changes Required

#### 1. Backend — cross-provider status endpoint

**File**: `backend/open_webui/routers/configs.py`
**Changes**: Add `GET /api/v1/configs/cloud-sync/status` (admin-only), next to the existing
provider config pairs (`configs.py:919-994`). Returns, per provider slug:

```python
{
  "confluence":   {"kb_count": 1, "file_count": 482, "last_sync_at": 1765400000,
                    "status": "idle", "syncing": False, "suspended_count": 0,
                    "shared": True},
  "google_drive": {"kb_count": 0, ...},
  "onedrive":     {"kb_count": 3, ...},
}
```

Implementation: one pass over `Knowledges` rows filtered by the provider `type` values
(`confluence`, `google_drive`, `onedrive` — Phase 3 adds `topdesk`), reading
`meta[<meta_key>]` for `last_sync_at` / `status` / `suspended_at` and aggregating
(max last_sync_at, any syncing, count suspended). File counts via the KB's file id list
length (`data.file_ids`) — no per-file queries. Cheap enough to call on tab open and reuse
the existing Confluence poll cadence while a sync runs.

**File**: `src/lib/apis/configs/index.ts`
**Changes**: Add `getCloudSyncStatus(token)`.

#### 2. Frontend — component decomposition

**New directory**: `src/lib/components/admin/Settings/CloudSync/` (delete the dead
`SpacePickerModal.svelte` currently in it).

| File | Responsibility |
|---|---|
| `types.ts` | `ProviderDescriptor` type: `{ slug, name, icon, hasTestConnection, supportsSharedKb, itemNoun ('pages'\|'files'\|'items'), authModes? }` + config form types moved out of the monolith |
| `ProviderCard.svelte` | Collapsible card: header row (icon, name, enabled `Badge`, status line from the status endpoint, chevron), `<slot>` body. Accordion state local to the panel; all collapsed by default. |
| `SyncSettingsSection.svelte` | The thrice-duplicated block, once: background-sync `Switch`, interval input, max-per-sync input with the `itemNoun`-aware label + "Leave empty for no limit" helper. Props: bound values + noun. |
| `SharedKbSection.svelte` | Generalized from the Confluence shared-KB block (`CloudSync.svelte:707-841`): provision/re-provision/sync/delete buttons, status badges, progress %, owner dropdown (optional), polling loop. Provider-specifics injected via props: API functions object, picker component, owner-pick visibility. |
| `ConfluenceSection.svelte` | Confluence-specific fields (auth-method + kb-mode selects with their coupling rule, OAuth vs service-account credential inputs, test connection, OAuth-connect popup) composed with the shared sections. |
| `GoogleDriveSection.svelte` | Client ID + API key + `SyncSettingsSection`. |
| `OneDriveSection.svelte` | Accounts/SharePoint groups + `SyncSettingsSection`. |
| `CloudSync.svelte` (rewritten, stays at `Settings/CloudSync.svelte`) | Orchestrator (~200 lines): descriptor array, status load, `{#each}` over descriptors rendering `ProviderCard` + the matching section component, single `<form>` + Save calling each section's persist. |

State management: each section component owns its provider's config state and exposes
`load()` / `persist()` (Svelte component bindings or a small registration callback — match
the existing `Settings.svelte` save-dispatch pattern, parent `on:save` handler unchanged at
`Settings.svelte:679-687`).

Status line content (from `getCloudSyncStatus`): `{kb_count} KBs · {file_count} files ·
last sync {time} · {status}` with a warning variant when `suspended_count > 0`. Providers
with `kb_count === 0` show "Not in use".

#### 3. Visual polish (within the accordion)

- Card chrome: `rounded-xl` bordered cards (`border-gray-100 dark:border-gray-850`), subtle
  hover, consistent with existing admin styling (Tailwind 4, no new dependencies).
- Header: provider icon (`size-5`), name, right-aligned enabled state `Badge`
  (success/muted), status line in `text-xs text-gray-500` under the name.
- Keep `SensitiveInput` for secrets, `Switch` for toggles, existing `Badge` types — no new
  primitives.

#### 4. i18n

**Files**: `src/lib/i18n/locales/en-US/translation.json`, `nl-NL/translation.json`
**Changes**: New keys (alphabetical, nl-NL translated): "Not in use", "Synced knowledge
bases", "last sync", status-line strings, any relabeled section headers. Existing keys reused
wherever text is unchanged.

### Success Criteria

#### Automated Verification

- [ ] Type-check introduces no new errors vs. baseline: `npm run check`
- [ ] Frontend lints clean for new/changed files: `npm run lint:frontend`
- [ ] Production build succeeds: `npm run build`
- [ ] Backend imports without error: `python -c "import open_webui.main"`
- [ ] `GET /api/v1/configs/cloud-sync/status` is admin-gated (401/403 for non-admin)

#### Manual Verification

- [ ] All three providers render as cards; expanding shows the exact same fields as before
- [ ] Saving each provider's config persists across reload (behavior parity with the monolith)
- [ ] Confluence shared-KB flow (provision / sync / progress % / delete / owner pick / OAuth
      connect popup) works identically inside `SharedKbSection`
- [ ] Status lines show plausible values for a tenant with synced KBs; "Not in use" otherwise
- [ ] Test connection still works in the Confluence card
- [ ] nl-NL labels correct throughout; no layout breakage at narrow widths

**Implementation Note**: Pause for manual confirmation (this phase touches the live Confluence
admin flow for existing deployments) before building Phase 3 on top.

---

## Phase 2: TOPdesk backend

### Overview

The provider implementation: config, GraphQL client, sync worker, shared-KB lifecycle, router,
factory registration. Built and unit-tested entirely against Phase 0 fixtures. Includes the
two generalizations (shared-KB helpers, HTML renderer) extracted from Confluence.

### Changes Required

#### 1. Config keys

**File**: `backend/open_webui/config.py` (new TOPdesk block after the Confluence block,
~line 3240)

| Name | Key | Default |
|---|---|---|
| `ENABLE_TOPDESK_INTEGRATION` | `topdesk.enable` | `False` |
| `ENABLE_TOPDESK_SYNC` | `topdesk.enable_sync` | `False` |
| `TOPDESK_URL` | `topdesk.url` | `''` (e.g. `https://x.topdesk.net`) |
| `TOPDESK_USERNAME` | `topdesk.username` | `''` |
| `TOPDESK_APP_PASSWORD` | `topdesk.app_password` | `''` |
| `TOPDESK_SYNC_INTERVAL_MINUTES` | `topdesk.sync_interval_minutes` | `60` |
| `TOPDESK_MAX_ITEMS_PER_SYNC` | `topdesk.max_items_per_sync` | `500` (0 = unlimited) |

Plain env (deploy-time only): `TOPDESK_MAX_ITEM_SIZE_MB` (default `25`, mirrors
`CONFLUENCE_MAX_PAGE_SIZE_MB` at `config.py:3195`).

Auth-form rule (from Phase 0): when `TOPDESK_USERNAME` is set → `Basic
base64(username:app_password)`; when empty → `Authorization: TOKEN id="<app_password>"`
(person-token form). Both implemented; helper text documents the operator+app-password form as
the recommended one. No `auth_mode`, no `kb_mode` configs — TOPdesk is service-account +
shared-KB by definition.

Register all on `app.state.config` in `main.py` next to the Confluence block
(`main.py:1500-1510`).

#### 2. Generalization A — shared-KB lifecycle helpers

**File**: `backend/open_webui/services/sync/shared_kb.py` (new)
**Changes**: Extract the provider-agnostic core of the Confluence `/shared/*` implementation
(`routers/confluence_sync.py:690-1008`) into parameterized helpers:

```python
async def find_shared_kb(provider_type: str, meta_key: str) -> Optional[KnowledgeModel]
async def provision_shared_kb(provider_type, meta_key, name, owner_id, selected_items, extra_meta) -> KnowledgeModel
async def shared_kb_status(provider_type, meta_key) -> dict   # provisioned/status/progress/file_count/last_sync_at/suspended_at/owner/items
async def delete_shared_kb(provider_type, meta_key) -> bool
def is_managed_shared_kb(kb) -> bool   # any provider's meta[<meta_key>].shared == True
SHARED_SYNC_META_KEYS = ['confluence_sync', 'topdesk_sync']
```

`provision_shared_kb` keeps the exact Confluence semantics: `Knowledges.insert_new_knowledge`
with `access_grants=[]`, then the public read grant via
`AccessGrants.set_access_grants('knowledge', kb.id, [{'principal_type': 'user',
'principal_id': '*', 'permission': 'read'}])`; empty `owner_id` → system-owned (`user_id=''`).

**File**: `backend/open_webui/routers/confluence_sync.py`
**Changes**: Delegate `_find_shared_kb` / provision / status / delete internals to the new
helpers. **Routes, request/response payloads, and the OAuth-mode owner rules are unchanged** —
this is an extract-and-delegate refactor; Confluence-specific logic (space selection shape,
OAuth owner resolution, `_resolve_shared_kb_sources`) stays in the Confluence module.

**File**: `backend/open_webui/routers/knowledge.py:1040-1052`
**Changes**: `_assert_not_managed_shared_kb` uses `is_managed_shared_kb(knowledge)` instead of
the Confluence-only meta check.

**File**: `backend/open_webui/services/deletion/cleanup_worker.py:172`
**Changes**: Replace the `confluence_sync`-hardcoded shared check with
`is_managed_shared_kb(kb)`.

#### 3. Generalization B — HTML renderer

**Files**: `backend/open_webui/services/sync/html_renderer.py` (new — moved from
`services/confluence/html_renderer.py`), `services/confluence/html_renderer.py` (becomes a
re-export shim: `from open_webui.services.sync.html_renderer import html_to_markdown  # noqa`)
**Changes**: Pure move, no logic change. TOPdesk imports from the shared location. The
`_SAFE_URL_PREFIXES` link sanitization applies to TOPdesk content as-is.

#### 4. `services/topdesk/` package (new)

| File | Contents |
|---|---|
| `__init__.py` | Re-exports (mirror `confluence/__init__.py:1-17`) |
| `auth.py` | Service-account module modeled on `confluence/basic_auth.py`: `TOPDESK_AUTH_SENTINEL = '__topdesk_service_auth__'`, `service_auth_configured()` (URL + password, username optional per the auth-form rule), `build_client()`, `auth_headers()` building Basic or TOKEN form |
| `topdesk_client.py` | `TopdeskClient` (httpx, copy `confluence_client.py` retry skeleton — 429 `Retry-After`, 5xx exponential backoff, 401 terminal, `ConnectError` → friendly error). Surface: `async def graphql(query, variables)` (POST to the Phase-0-pinned endpoint); `list_knowledge_items(modified_since=None, page_cursor=None)`, `get_knowledge_item(item_id, include_content=True)`, `list_item_children(item_id)`, `list_root_items()` — all GraphQL, pagination per Phase 0 findings; `async def probe()` for test-connection (the Phase-0-verified lightweight read). Bounded sequential pagination; no parallel fan-out against the tenant (no published rate limits — be polite). |
| `sync_worker.py` | `TopdeskSyncWorker(BaseSyncWorker)` — see below |
| `provider.py` | `TopdeskTokenManager` (sentinel when `service_auth_configured()`, mirrors `ConfluenceTokenManager` basic branch at `confluence/provider.py:28-49`); `TopdeskSyncProvider` (`get_provider_type` → `'topdesk'`, `get_meta_key` → `'topdesk_sync'`, `create_worker` forcing `use_shared_loader=False` with the explanatory comment, mirroring the Confluence forced-in-pod pattern) |
| `scheduler.py` | Thin wrapper instantiating the generic `SyncScheduler` with `ENABLE_TOPDESK_SYNC` / `TOPDESK_SYNC_INTERVAL_MINUTES` (copy `confluence/scheduler.py:1-21`) |
| `sync_events.py` | Thin forwarder, `_PREFIX = 'topdesk'` (copy `confluence/sync_events.py:1-62`) |

**`TopdeskSyncWorker` specifics:**

- Abstract properties: `meta_key='topdesk_sync'`, `file_id_prefix='topdesk-'`,
  `event_prefix='topdesk'`, `provider_slug='topdesk'`,
  `internal_request_path='/internal/topdesk-sync'`,
  `max_files_config = TOPDESK_MAX_ITEMS_PER_SYNC.value or None`,
  `source_clear_delta_keys=['item_map', 'last_synced_modified']`.
- **Source taxonomy**: `folder` = a knowledge item + all descendants (tree picker subtree
  selection, `include_descendants` like Confluence pages); `file` = a single knowledge item.
  Sources resolved dynamically each run from the shared KB's admin-selected items (port of
  `_resolve_shared_kb_sources`, `confluence/sync_worker.py:267-358`, carrying over `item_map`
  by item id and keeping deselected sources one extra run for revoked-source file cleanup).
- **Change detection**: `_get_cloud_hash` returns the item's `modificationDate` string; per
  `folder` source keep `source['item_map']: {item_id → modificationDate}`; skip when unchanged
  AND the File row is `completed` (port of `confluence/sync_worker.py:411-422,480-490`).
  Deletion by set-difference against the fresh enumeration. Dedup across overlapping subtree
  sources via a `_seen_item_ids` set (port of `sync_worker.py:140,405-409`).
- **Filtering**: enumerate only `status == published` items (drafts never sync). `visibility`
  is recorded as metadata in v1 (the service account's TOPdesk-side permission filters are the
  visibility boundary).
- **Document build** (`_download_file_content`): fetch item content → `html_to_markdown`
  off-thread → front-matter block (title, KI number, keywords, language, status, web URL,
  created/modified dates — format per the Confluence `_build_front_matter` pattern,
  `sync_worker.py:65-109`) → byte-cap at `TOPDESK_MAX_ITEM_SIZE_MB` →
  `content_type='text/markdown'`, `size: 0` synthetic items.
- `_get_provider_file_meta`: provider `'topdesk'`, item id, KI number, web URL, language,
  keywords (propagates to vector-chunk metadata).
- `_sync_permissions`: shared-KB rules only — owner/credential validity check + suspension
  lifecycle (port the Confluence shared-mode branch; there is no per-user mode to handle).

#### 5. Factory + registry

**File**: `backend/open_webui/services/sync/provider.py`
**Changes**: `'topdesk': 'topdesk-'` in `PROVIDER_FILE_ID_PREFIXES` (`:43-47`); `topdesk`
branches in `get_sync_provider()` (`:210-229`) and `get_token_manager()` (`:232-247`).

#### 6. Router

**File**: `backend/open_webui/routers/topdesk_sync.py` (new), mounted **unconditionally** at
`/api/v1/topdesk` (`main.py`, next to `:2105`). All endpoints **admin-only** (there is no
end-user surface):

- `POST /auth/test` — builds a client from submitted-or-stored credentials, runs `probe()`,
  returns `{ok, detail, item_count?}` with friendly 401/403/404 mapping (model:
  `confluence_sync.py:435-483`). Blank password falls back to the saved one.
- `GET /browse/items?parent_id=` — tree-picker proxy: root items when `parent_id` absent,
  children otherwise. Returns `{id, name (title), number, has_children, status}`.
- `GET /shared/status`, `POST /shared/provision`, `POST /shared/sync`, `DELETE /shared` —
  thin wrappers over the Phase-2.2 shared-KB helpers + `provider.execute_sync` in the
  background (model: `confluence_sync.py:690-1008` minus OAuth owner logic; owner pick =
  admin choice or system-owned, identical to Confluence basic mode).

#### 7. Configs endpoint

**File**: `backend/open_webui/routers/configs.py`
**Changes**: `/topdesk` GET/POST pair modeled on `/confluence` (`configs.py:919-994`):
admin-only, same disclosure profile (credentials round-trip masked-with-reveal via
`SensitiveInput`, consistent with Confluence), URL `.strip().rstrip('/')`, max-items clamped
≥ 0. Add `topdesk` to the Phase-1 status endpoint's provider list.

#### 8. `main.py` wiring

**File**: `backend/open_webui/main.py`
**Changes** (each next to its Confluence counterpart): import `topdesk_sync` (`:119` block);
config registration (`:1500-1510` block); router mount (`:2105` block); scheduler start/stop
in lifespan (`:976-981` / `:1071-1076`); `/api/config` features —
`enable_topdesk_integration`, `enable_topdesk_sync`, `topdesk_shared_kb_id` (resolved via
`find_shared_kb` only when integration enabled; empty otherwise — model `main.py:3120-3128`).

#### 9. Unit tests

**Files**: `backend/open_webui/test/services/topdesk/` (new)
**Changes**: Against Phase 0 fixtures (mocked httpx transport, no network):

- Client: auth-header construction (both forms), GraphQL request shape, pagination loop,
  429/5xx retry, 401 terminal.
- Worker: classification (changed `modificationDate` → update; same → skip; missing → delete),
  subtree dedup, front-matter/Markdown rendering output.
- Shared-KB helpers: provision creates KB + public read grant; `is_managed_shared_kb` true for
  both Confluence and TOPdesk shared KBs (regression-guards the knowledge.py/cleanup_worker
  generalization).
- Registry: extend `test_provider_registry.py` for the `topdesk` factory branches.

### Success Criteria

#### Automated Verification

- [ ] Backend imports without error: `python -c "import open_webui.main"`
- [ ] Backend lints clean for changed files: `npm run lint:backend`
- [ ] New unit tests pass: `pytest backend/open_webui/test/services/topdesk/ backend/open_webui/test/services/sync/`
- [ ] Existing Confluence tests still pass (renderer move + shared-KB delegation are
      behavior-neutral): `pytest backend/open_webui/test/services/confluence/`
- [ ] `/api/v1/configs/topdesk` and all `/api/v1/topdesk/*` routes are admin-gated

#### Manual Verification

- [ ] With fixtures-backed mocks: a full worker `sync()` run produces the expected File rows
      and skips/deletes correctly on the second run
- [ ] Confluence shared-KB provision/status/delete still works end-to-end after the helper
      extraction (regression check on dev)

**Implementation Note**: Pause for manual confirmation of the Confluence regression check
before Phase 3.

---

## Phase 3: TOPdesk frontend

### Overview

TOPdesk lands in the redesigned panel as a descriptor + section component, plus the tree
picker, KB branding, chat shortcut, i18n, and Helm keys.

### Changes Required

#### 1. API client

**File**: `src/lib/apis/topdesk/index.ts` (new)
**Changes**: `testTopdeskConnection`, `getTopdeskSharedKbStatus`, `provisionTopdeskSharedKb`,
`syncTopdeskSharedKb`, `deleteTopdeskSharedKb`, `browseTopdeskItems(parentId?)` — model
`apis/confluence/index.ts:116-250`, using its `apiFetch` helper style.

**File**: `src/lib/apis/configs/index.ts`
**Changes**: `getTopdeskConfig` / `setTopdeskConfig` against `/configs/topdesk`.

#### 2. Panel integration

**Files**: `src/lib/components/admin/Settings/CloudSync/` (from Phase 1)
**Changes**:

- Descriptor entry: `{ slug: 'topdesk', name: 'TOPdesk', icon: Topdesk,
  hasTestConnection: true, supportsSharedKb: true, itemNoun: 'items' }`.
- `TopdeskSection.svelte`: enable toggle → service-account inputs (URL, username with
  "optional — leave empty for person-token auth" helper, app password via `SensitiveInput`,
  Test connection) → `SyncSettingsSection` → `SharedKbSection` wired to the topdesk API
  functions and `TopdeskPickerModal`. No auth-mode or sync-mode dropdowns — the card states
  "Service account · pre-synced shared knowledge base" as fixed descriptive text.
- `topdesk` added to the status-endpoint consumption + tab keywords
  (`Settings.svelte:169-174`: add `'topdesk'`).

#### 3. Tree picker

**File**: `src/lib/components/admin/Settings/CloudSync/TopdeskPickerModal.svelte` (new)
**Changes**: Modeled on `ConfluencePickerModal` (tree explorer, checkbox selection,
`include_descendants` semantics, `currentItems` pre-selection) but backed by
`browseTopdeskItems` lazy child-loading. Emits `SyncItem[]`-shaped
`{type: 'folder'|'file', item_id, name, include_descendants}` to `SharedKbSection`'s
provision flow. Lives under `CloudSync/` (admin-only — unlike Confluence there is no
workspace per-user picker to share it with).

#### 4. Branding + user-facing surfaces

- **Icon**: `src/lib/components/icons/Topdesk.svelte` (simple mark, consistent `className`
  prop pattern with `Confluence.svelte`).
- **KB card**: add `topdesk` to `CLOUD_PROVIDERS` in
  `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte:109` (logo, read-only
  treatment — the existing non-local-type handling applies; verify the shared-KB gray-out and
  managed-KB delete-block behave for `topdesk` type).
- **Chat `+` menu shortcut**: `topdesk_shared_kb_id` consumed in `InputMenu.svelte` exactly
  like the Confluence Phase-3 addendum (top-level entry when integration enabled + provisioned;
  attaches the KB as a `collection`). Add the three feature fields to the `Config` type in
  `src/lib/stores/index.ts`.
- **No create-flow entry**: TOPdesk deliberately does NOT appear in workspace KB creation
  (users can't create TOPdesk KBs — see Key Discoveries).

#### 5. i18n

**Files**: `en-US/translation.json`, `nl-NL/translation.json`
**Changes**: All new strings, alphabetical, both locales — "TOPdesk", "Configure TOPdesk as a
knowledge base sync source.", "TOPdesk URL", "Application password", service-account helper
text, "Items to sync", picker strings, test-connection success/failure
(`"TOPdesk connection successful."` / `"TOPdesk connection failed: {{error}}"`), shared-KB
strings reused from Confluence where the wording is provider-neutral.

#### 6. Helm

**Files**: `helm/open-webui-tenant/values.yaml`, `templates/open-webui/configmap.yaml`,
`templates/secrets.yaml` (or top-level secrets block per `values.yaml:851-854`),
`templates/external-secrets.yaml`, `templates/open-webui/deployment.yaml`
**Changes**: Mirror the Confluence pattern (`values.yaml:457-477,851-854,933-934`):
`topdeskUrl`, `topdeskUsername`, `topdeskSyncIntervalMinutes`, `topdeskMaxItemsPerSync` as
configmap envs; `topdeskAppPassword` as a secret (env `TOPDESK_APP_PASSWORD`) with the
external-secrets/1Password field. Enable flags default off; all keys additive.

### Success Criteria

#### Automated Verification

- [ ] Type-check introduces no new errors vs. baseline: `npm run check`
- [ ] Frontend lints clean for new/changed files: `npm run lint:frontend`
- [ ] Production build succeeds: `npm run build`
- [ ] `helm template helm/open-webui-tenant` renders with the new keys set and unset

#### Manual Verification

- [ ] TOPdesk card renders in the Cloud Sync accordion with icon, status line, and all fields
- [ ] Saving TOPdesk config persists across reload; password never shown unmasked by default
- [ ] Tree picker browses fixture/mocked data correctly (lazy children, pre-selection)
- [ ] Chat `+` menu shows the TOPdesk entry only when enabled + provisioned
- [ ] Shared TOPdesk KB card in workspace shows the logo and is read-only for users
- [ ] nl-NL labels correct throughout

---

## Phase 4: Production verification + polish

### Overview

Careful end-to-end against the client's production tenant with their key. Incremental blast
radius: connection probe → tiny subtree → full selection.

### Steps

1. Enter URL + credentials in the panel; **Test connection** → expect success with the
   Phase-0-confirmed auth form.
2. Provision the shared KB with **one small knowledge-item subtree** (a handful of items).
   Run **Sync now**; verify: progress %, File rows complete, Markdown content + front matter
   correct, items retrievable in chat with sources cited.
3. Re-run sync → all items skip (modificationDate unchanged). Deselect the subtree,
   re-provision, sync → files removed.
4. Expand to the real selection; monitor duration, memory, and any 429s (tune
   `TOPDESK_MAX_ITEMS_PER_SYNC` / interval accordingly).
5. Enable background sync; confirm a scheduled run completes.
6. Negative checks: wrong password → clean test-connection failure; revoked credential
   mid-operation → sync fails cleanly, KB suspends (not deleted), admin panel shows the
   warning badge; workspace delete/reset of the shared KB is blocked.
7. Full nl-NL pass over the new surfaces.

### Success Criteria

#### Automated Verification

- [ ] Full test suite still green: `pytest backend/open_webui/test/` + `npm run test:frontend`

#### Manual Verification

- [ ] All steps above pass against the client tenant
- [ ] Sync volume stayed polite (no sustained 429s; sequential pagination confirmed in logs)
- [ ] A second non-admin account sees the shared KB read-only and gets TOPdesk content in
      chat answers

---

## Testing Strategy

- **Unit** (fixtures, no network): client auth/pagination/retry; worker classification +
  deletion + dedup; renderer output; shared-KB helpers incl. the `is_managed_shared_kb`
  generalization; provider registry.
- **Regression**: Confluence unit tests + manual Confluence shared-KB flow after the Phase 2
  extractions; panel behavior parity checks in Phase 1.
- **Integration**: live verification is Phase 0 (read-only discovery) and Phase 4 (E2E) only —
  there is no test tenant, so CI never touches TOPdesk's network.

## Performance Considerations

- First full sync of a large KB set is the cost spike (in-pod pipeline, like Confluence);
  steady-state syncs skip unchanged items via `modificationDate`. Default cap 500 items/sync;
  the admin can raise it after observing Phase 4 behavior.
- The client tenant is production ITSM: the client enforces **sequential pagination** and
  bounded enumeration; no parallel API fan-out (unlike Confluence's `Semaphore(8)` enrichment,
  TOPdesk enrichment data arrives in the same GraphQL item query — no second fetch wave).
- No published TOPdesk rate limits — backoff on 429/503 is implemented but should rarely
  trigger at this request profile.

## Migration Notes

- **No database migration.** New state = `PersistentConfig` rows, KB `meta` JSON
  (`topdesk_sync`), existing `access_grant` mechanism.
- All Helm keys additive with safe defaults (integration off) — existing tenant values files
  unchanged unless a deployment opts in.
- The shared-KB helper extraction and renderer move are behavior-neutral for Confluence;
  existing Confluence shared KBs need no data change (`is_managed_shared_kb` reads the same
  meta it always had).
- Frontend refactor preserves all existing config endpoints/payloads — no coordinated
  backend/frontend deploy needed beyond normal image rollout.

## References

- Predecessor plan (template + Confluence context):
  `thoughts/shared/plans/2026-05-21-confluence-cloud-sync-admin.md`
- Integration cookbook (12-step recipe; note its code snippets predate async OAuthSessions /
  shared pending-flows / unconditional mounts — follow the Confluence code, not the snippets):
  `collab/docs/external-integration-cookbook.md`
- Sync abstraction: `backend/open_webui/services/sync/` (`provider.py`, `base_worker.py`,
  `scheduler.py`, `router.py`, `events.py`, `pending_flows.py`)
- Confluence implementation (the template): `backend/open_webui/services/confluence/`,
  `backend/open_webui/routers/confluence_sync.py`, `routers/configs.py:919-994`
- Shared-KB guards to generalize: `routers/knowledge.py:1040-1052`,
  `services/deletion/cleanup_worker.py:172`
- Admin panel: `src/lib/components/admin/Settings/CloudSync.svelte` (monolith to decompose),
  `Settings.svelte:169-174,626-639,679-687`, `features.ts:105,143-183`
- Frontend API patterns: `apis/configs/index.ts:1126-1289`, `apis/confluence/index.ts`,
  `apis/sync/index.ts:80-162` (`createSyncApi`)
- KB branding: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte:109`
  (`CLOUD_PROVIDERS`)
- Helm pattern: `helm/open-webui-tenant/values.yaml:457-477,851-854,933-934`
- TOPdesk API (researched 2026-06; verify in Phase 0): developers.topdesk.com — tutorial,
  Knowledge Base GraphQL explorer (`?page=knowledgebase-graphql`); docs.topdesk.com — "API
  Account", "Authorizing access to TOPdesk API", "What's new in TOPdesk 2025 R2" (legacy REST
  KB API removal)
