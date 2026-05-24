# Confluence — Decouple Auth Method from Sync Mode — Implementation Plan

## Overview

The Cloud Sync admin tab presents Confluence as a single "Modus" choice that welds
two independent settings together: the **authentication method** and the
**knowledge-base sync mode**. As a result the combination an admin actually needs
for Intermax — OAuth credentials with one pre-synced, company-wide knowledge base —
cannot be selected at all.

This plan decouples the two into independent controls (Auth method × Sync mode), so
every valid combination is reachable. The backend already stores `auth_mode` and
`kb_mode` as independent config keys and already supports an OAuth-authenticated
shared KB; the only true gaps are the **frontend** (one dropdown driving both) and
**one backend endpoint** (`/shared/spaces`) that today only works for service-account
auth.

## Current State Analysis

### Already built — no work needed

- `CONFLUENCE_AUTH_MODE` (`config.py:3021`, values `oauth`/`basic`) and
  `CONFLUENCE_KB_MODE` (`config.py:3052`, values `per_user`/`shared`) are **independent**
  `PersistentConfig` keys. `CONFLUENCE_SHARED_KB_OWNER_ID` (`:3062`) is a third. The
  `/api/v1/configs/confluence` GET/POST pair (`configs.py:894-977`) reads and writes all
  three independently.
- The shared KB syncs an **admin-selected subset of spaces**, not all spaces. The legacy
  `sync_all_spaces` flag was removed. Selection lives in `confluence_sync.spaces` KB meta,
  written by `POST /shared/provision` (`confluence_sync.py:772-855`), surfaced by
  `GET /shared/status` (`:692-721`), and turned into per-space sync sources each run by
  `_resolve_shared_kb_sources()` (`sync_worker.py:255-330`).
- OAuth + pre-synced **already works end to end** when an owner is set: `/shared/provision`
  rejects an OAuth shared KB with no owner (`confluence_sync.py:800-804`); the scheduler
  resolves the sync token from `kb.user_id` (`services/sync/scheduler.py:112`).
- The chat `+` menu already branches on `confluence_kb_mode`: the per-user picker for
  `!== 'shared'`, the one-click shared-KB attach for `=== 'shared'`
  (`InputMenu.svelte:549-577`). No change needed.
- Helm keys for `CONFLUENCE_AUTH_MODE`, `CONFLUENCE_KB_MODE`, `CONFLUENCE_SHARED_KB_OWNER_ID`
  already exist (`configmap.yaml:318,333,335`; `values.yaml:448,462,463`).

### The coupling — frontend only

`CloudSync.svelte` is the sole place the two axes are welded:

- `CloudSync.svelte:62-64` — a single `CONFLUENCE_MODE` variable; `CONFLUENCE_AUTH_MODE`
  and `CONFLUENCE_KB_MODE` are reactive derivations of it (`company_wide`→`basic`+`shared`,
  `per_user`→`oauth`+`per_user`). The two off-diagonal combinations are unreachable.
- `CloudSync.svelte:169-172` — on load, `applyConfluenceConfig` rebuilds `CONFLUENCE_MODE`
  from `CONFLUENCE_KB_MODE` only, discarding the response's `CONFLUENCE_AUTH_MODE`.
- Conditional rendering keys off `CONFLUENCE_MODE`: the Service-account / OAuth auth
  sections (`:454` `if`, `:513` `else`) and the Shared-KB section (`:592` `if`).
- The POST body (`:274-290`) already sends `CONFLUENCE_AUTH_MODE` and `CONFLUENCE_KB_MODE`
  as distinct fields — the backend contract is ready for decoupled values.

### The backend gap — `/shared/spaces` is service-account only

`GET /shared/spaces` (`confluence_sync.py:730-769`) raises HTTP 400 unless
`resolve_auth_mode(None) == 'basic'`, and enumerates spaces only via `build_basic_client()`.
The admin space picker (`SpacePickerModal.svelte`) calls this on open
(`getConfluenceSharedKbSpaces`). So in OAuth pre-synced mode an admin cannot list spaces
to choose from.

### Flow consequence — the OAuth owner has no connect UI

In pre-synced mode the chat `+` per-user picker is hidden, and that picker is also where a
user authorizes OAuth. The OAuth owner of a pre-synced KB therefore has no affordance to
connect their Atlassian account. The connect flow itself exists and is reusable:
`GET /confluence/auth/initiate` (`confluence_sync.py:307-333`) initiates 3LO for the
current user (optional `knowledge_id`, falls back to `__general__`); the established
frontend pattern is the OAuth popup in `KnowledgeBase.svelte:1305-1340`.

### Key Discoveries

- Decoupling is a frontend change plus one endpoint — the backend model already separates
  the axes (`config.py:3021,3052`; `configs.py:894-977`).
- `_picker_client(user)` / `_browse_client(user, cloud_id)` (`confluence_sync.py:480-527`)
  build OAuth clients keyed by **any** `UserModel`. Passing the *owner's* `UserModel`
  enumerates the owner's spaces — exactly the token the pre-synced sync will run with, so
  the picker shows precisely what the sync can fetch.
- A token connected via `/auth/initiate` is stored per user and is usable by the scheduler
  for any KB that user owns.
- Service-account + on-demand is a permission-flattening combination with no use case —
  gate it rather than expose it.

## Desired End State

The Cloud Sync tab's Confluence section has two independent controls —
**Authentication method** (OAuth / Service account) and **Sync mode**
(On-demand / Pre-synced):

- **OAuth + On-demand** — today's per-user picker (unchanged).
- **OAuth + Pre-synced** — one company-wide, read-only KB synced via the owner's OAuth
  token; the admin connects the owner account and picks spaces from the live catalog.
- **Service account + Pre-synced** — today's company-wide KB (unchanged).
- **Service account + On-demand** — disabled (not offered).

An admin can fully configure OAuth + Pre-synced from the tab — connect the owner, pick
spaces, provision — without Helm/env edits or a pod restart.

## What We're NOT Doing

- **Not** renaming the stored config values — `auth_mode` stays `oauth`/`basic`, `kb_mode`
  stays `per_user`/`shared`. Only UI labels change. No migration, no Helm change.
- **Not** building the TopDesk provider (separate spec).
- **Not** building per-user OAuth pre-sync with cross-user deduplication (separate spec).
- **Not** adding a pre-synced mode for OneDrive / Google Drive (separate spec).
- **Not** changing the chat `+` menu — it already branches on `confluence_kb_mode`.
- **Not** changing the on-demand per-user picker, the provision/sync/delete endpoints, or
  the scheduler.
- No DB migration.

## Implementation Approach

Two phases. Phase 1 makes the backend able to enumerate spaces and report owner-connection
state for OAuth pre-synced. Phase 2 splits the UI control and adds the owner-connect
affordance. Phase 1 ships first so that when Phase 2 exposes OAuth + Pre-synced, the space
picker already works.

---

## Phase 1: Backend — OAuth space enumeration & owner-connection status

### Overview

Make `GET /shared/spaces` work in OAuth mode by enumerating spaces through the configured
owner's token, and expose whether that owner has connected so the UI can show state
without a failing call.

### Changes Required

#### 1. `GET /shared/spaces` — OAuth branch

**File**: `backend/open_webui/routers/confluence_sync.py` (`list_shared_kb_spaces`, ~`:730-769`)

Replace the hard `resolve_auth_mode(None) != 'basic'` rejection with a branch. Keep the
basic path unchanged; add an OAuth path that resolves the configured owner, builds OAuth
clients per owner site, and aggregates `list_all_spaces()`.

```python
@router.get('/shared/spaces')
async def list_shared_kb_spaces(user: UserModel = Depends(get_admin_user)) -> dict:
    """List Confluence spaces available for the shared KB (admin).

    Pre-synced mode. In basic auth, enumerates every space the service
    account sees. In OAuth, enumerates spaces the configured owner's token
    can reach — the owner's token is what the scheduler syncs with, so the
    picker shows exactly what the sync can fetch.
    """
    auth_mode = resolve_auth_mode(None)

    if auth_mode == 'basic':
        if not basic_auth_configured():
            raise HTTPException(
                400,
                'Confluence basic auth is not configured. Save the service account credentials first.',
            )
        basic_site = get_basic_site()
        cloud_id = basic_site['cloud_id'] if basic_site else ''
        client = build_basic_client()
        try:
            spaces = await client.list_all_spaces()
        except httpx.HTTPStatusError as e:
            raise HTTPException(e.response.status_code, f'Confluence error: {e.response.status_code}')
        finally:
            await client.close()
        return {
            'spaces': [
                {'id': s.get('id'), 'key': s.get('key'), 'name': s.get('name'),
                 'type': s.get('type'), 'cloud_id': cloud_id}
                for s in spaces
            ],
        }

    # OAuth — enumerate via the configured owner's token.
    owner_id = (CONFLUENCE_SHARED_KB_OWNER_ID.value or '').strip()
    if not owner_id:
        raise HTTPException(400, 'Select and save a shared knowledge base owner before picking spaces.')
    owner = Users.get_user_by_id(owner_id)
    if not owner:
        raise HTTPException(400, 'The configured shared KB owner is not a valid user.')

    try:
        _, sites = await _picker_client(owner)
    except HTTPException as e:
        if e.status_code == 401:
            raise HTTPException(400, 'The selected owner has not connected their Confluence account yet.')
        raise

    spaces: list = []
    for site in sites:
        cloud_id = site.get('cloud_id')
        client, _ = await _browse_client(owner, cloud_id)
        try:
            site_spaces = await client.list_all_spaces()
        except httpx.HTTPStatusError as e:
            raise HTTPException(e.response.status_code, f'Confluence error: {e.response.status_code}')
        finally:
            await client.close()
        spaces.extend(
            {'id': s.get('id'), 'key': s.get('key'), 'name': s.get('name'),
             'type': s.get('type'), 'cloud_id': cloud_id}
            for s in site_spaces
        )
    return {'spaces': spaces}
```

`_browse_client` branches internally on `resolve_auth_mode(None)`, so in OAuth mode it
builds the per-token client correctly. The point is passing `owner` (not the requesting
admin) so the catalog matches what the sync will run with. `Users`,
`CONFLUENCE_SHARED_KB_OWNER_ID`, and `httpx` are already imported in this module
(`:14,24,7`).

#### 2. `owner_connected` in shared status

**File**: same file, `_shared_kb_status()` (~`:692-721`)

Add an `owner_connected` boolean to the status dict.

```python
auth_mode = resolve_auth_mode(None)
owner_id = (CONFLUENCE_SHARED_KB_OWNER_ID.value or '').strip()
if auth_mode == 'basic':
    owner_connected = basic_auth_configured()
else:
    from open_webui.services.confluence.auth import get_stored_token
    owner_connected = bool(owner_id) and get_stored_token(owner_id) is not None
# include 'owner_connected': owner_connected in the returned status dict
```

The `ConfluenceSharedKbStatus` TypeScript interface in `src/lib/apis/confluence/index.ts`
(`:158-175`) gains `owner_connected?: boolean`.

### Success Criteria

#### Automated Verification
- [x] Backend imports without error: `python -c "import open_webui.main"`
- [x] Backend lints clean on the changed file: `npm run lint:backend` — no new pylint
      warning categories vs. baseline; added instances (C0301/C0415/W0707) all match
      patterns already pervasive in `confluence_sync.py`
- [x] `GET /api/v1/confluence/shared/spaces` and `/shared/status` stay admin-gated
      (401/403 for a non-admin) — both retain `Depends(get_admin_user)`, unchanged
- [x] Unit test added: `test/services/confluence/test_shared_kb_spaces_oauth.py` —
      OAuth branch (no owner / not connected / connected aggregation), 3 passed

#### Manual Verification
- [x] OAuth mode, connected owner: `/shared/spaces` returns spaces from the owner's sites
- [x] OAuth mode, no owner saved: returns a clear 400
- [x] OAuth mode, owner never connected: returns the "not connected" 400
- [x] Basic mode `/shared/spaces` still works unchanged
- [x] `/shared/status` reports `owner_connected` correctly in both auth modes

**Implementation Note**: After Phase 1 automated verification passes, pause for manual
confirmation before starting Phase 2.

---

## Phase 2: Frontend — decouple the Cloud Sync admin UI

### Overview

Split the single `CONFLUENCE_MODE` dropdown into two independent controls, add the OAuth
owner-connect affordance, gate the service-account + on-demand combination, and relabel.

### Changes Required

#### 1. Two independent state variables

**File**: `src/lib/components/admin/Settings/CloudSync.svelte`

Replace `CONFLUENCE_MODE` and the two reactive derivations (`:62-64`) with two plain
variables, plus the gating rule:

```ts
let CONFLUENCE_AUTH_MODE: 'oauth' | 'basic' = 'oauth';
let CONFLUENCE_KB_MODE: 'per_user' | 'shared' = 'per_user';

// Service account + on-demand flattens permissions and has no use case —
// when auth is the service account, force the pre-synced shared KB.
$: if (CONFLUENCE_AUTH_MODE === 'basic' && CONFLUENCE_KB_MODE === 'per_user') {
    CONFLUENCE_KB_MODE = 'shared';
}
```

#### 2. Load both fields independently

`applyConfluenceConfig` (`:169-172`) — assign each axis directly from the response instead
of deriving one dropdown:

```ts
CONFLUENCE_AUTH_MODE = config.CONFLUENCE_AUTH_MODE === 'basic' ? 'basic' : 'oauth';
CONFLUENCE_KB_MODE = config.CONFLUENCE_KB_MODE === 'shared' ? 'shared' : 'per_user';
```

The POST body (`:274-290`) already sends both fields — no change there.

#### 3. Two `<select>` controls

Replace the single Mode dropdown block (`:430-452`) with:
- An **Authentication method** select — `OAuth` / `Service account`, bound to
  `CONFLUENCE_AUTH_MODE`.
- A **Sync mode** select — `On-demand` / `Pre-synced`, bound to `CONFLUENCE_KB_MODE`. The
  `On-demand` `<option>` is `disabled` when `CONFLUENCE_AUTH_MODE === 'basic'`.

Each control gets a short helper line. The Sync-mode helper explains, when auth is the
service account, that it always uses the pre-synced shared KB.

#### 4. Conditional rendering keyed off each axis

- Auth sections: `{#if CONFLUENCE_MODE === 'company_wide'}` (`:454`) → `{#if
  CONFLUENCE_AUTH_MODE === 'basic'}` (Service account section), `{:else}` (`:513`, OAuth
  section).
- Shared-KB section: `{#if CONFLUENCE_MODE === 'company_wide'}` (`:592`) → `{#if
  CONFLUENCE_KB_MODE === 'shared'}`.
- The Sync Settings block (`:549-590`) stays unconditional.

#### 5. OAuth owner-connect affordance

In the Shared-KB section, when `CONFLUENCE_AUTH_MODE === 'oauth'`:
- Show owner connection state from `sharedKbStatus.owner_connected` — e.g. an "Account
  connected" / "No account connected" badge near the owner dropdown.
- A **Connect Confluence account** button (label "Reconnect" when already connected) that
  opens an OAuth popup to `${WEBUI_API_BASE_URL}/confluence/auth/initiate`, reusing the
  popup pattern in `KnowledgeBase.svelte:1305-1340` (popup name `confluence_auth`, listen
  for the `confluence_auth_callback` postMessage, and re-fetch `/shared/status` when the
  popup closes as a fallback).
- Helper text: the button connects the account you are currently signed in as — select
  that same admin as the owner.

#### 6. Persist config before opening the space picker

`SpacePickerModal` calls `/shared/spaces` on open, which in OAuth mode reads the saved
owner. The Provision / Re-provision button currently just sets `showSpacePicker = true`
(`:652-664`). Change its handler to `await persistConfig()` first, so `CONFLUENCE_AUTH_MODE`
and `CONFLUENCE_SHARED_KB_OWNER_ID` are saved before the picker queries spaces.

#### 7. SpacePickerModal wording

`SpacePickerModal.svelte:136` empty-state text — "No Confluence spaces are visible to the
service account." → auth-mode-neutral wording, e.g. "No Confluence spaces are available."

#### 8. i18n

Add en-US + nl-NL keys (alphabetically sorted), e.g.: "Authentication method",
"Sync mode", "Service account", "On-demand", "Pre-synced", the auth/mode helper texts,
"Connect Confluence account", "Reconnect", "Account connected", "No account connected",
and the owner helper text. The now-unused "Company-wide shared knowledge base" /
"Per-user knowledge bases" keys can be left in place to avoid churn.

### Success Criteria

#### Automated Verification
- [x] Type-check introduces no new errors vs. baseline: `npm run check` — changed files
      show only the pre-existing `i18n`-store error (project baseline 10208); no non-i18n
      errors in `CloudSync.svelte` / `SpacePickerModal.svelte`
- [x] Frontend lints clean on changed files: `npm run lint:frontend` — ESLint clean on
      both changed Svelte files
- [x] Production build succeeds: `npm run build` — built in 1m, exit 0

#### Manual Verification
- [ ] The Confluence section shows two independent dropdowns (Auth method, Sync mode)
- [ ] Selecting Service account disables the On-demand option and forces Pre-synced
- [ ] OAuth + Pre-synced: the Connect button runs OAuth and the status flips to connected
- [ ] OAuth + Pre-synced: opening the space picker lists the owner's spaces; selecting
      spaces and provisioning creates the shared KB
- [ ] The shared KB appears read-only for a non-admin user and as a one-click entry in the
      chat `+` menu
- [ ] OAuth + On-demand still shows the per-user picker (unchanged)
- [ ] Service account + Pre-synced still works (unchanged)
- [ ] A saved OAuth + Pre-synced config reloads with both dropdowns reflecting saved state
- [ ] nl-NL labels are correct throughout the section

**Implementation Note**: After Phase 2, confirm the full OAuth + Pre-synced scenario
end-to-end (connect → pick spaces → provision → sync → attach in chat) before closing the
work.

---

## Testing Strategy

### Unit Tests
- A test for the `/shared/spaces` OAuth branch under `backend/open_webui/test/services/confluence/`
  (or a router test): owner not set → 400; owner not connected → 400; connected owner →
  spaces aggregated across sites. Mock `_picker_client` / `_browse_client`.

### Manual Testing
- Full OAuth + Pre-synced setup against a real Confluence Cloud account (the developer's
  own), per the Phase 2 Manual Verification list.
- Regression: OAuth + On-demand per-user picker, and Service account + Pre-synced, both
  unchanged.

## Performance Considerations

`/shared/spaces` in OAuth mode runs one `list_all_spaces()` per accessible site — a handful
of paginated calls, triggered on-demand from the admin tab only. No scheduler or chat-path
impact.

## Migration Notes

No DB migration. Config values are unchanged, so existing deployments keep their behavior:
`auth_mode=oauth`+`kb_mode=per_user` (OAuth on-demand) and `auth_mode=basic`+`kb_mode=shared`
(service-account pre-synced) render identically to today. The new capability is purely the
previously-unreachable OAuth + Pre-synced cell. No Helm change — all keys already exist.

## References

- Builds on: `thoughts/shared/plans/2026-05-21-confluence-cloud-sync-admin.md` (Phases 1-3,
  implemented)
- Backend: `backend/open_webui/routers/confluence_sync.py`,
  `backend/open_webui/services/confluence/`, `backend/open_webui/routers/configs.py:894-977`,
  `backend/open_webui/config.py:2977-3066`
- Frontend: `src/lib/components/admin/Settings/CloudSync.svelte`,
  `src/lib/components/admin/Settings/CloudSync/SpacePickerModal.svelte`,
  `src/lib/apis/confluence/index.ts`,
  `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte:1305-1340` (OAuth popup pattern)
- `+` menu (unchanged): `src/lib/components/chat/MessageInput/InputMenu.svelte:549-577`
