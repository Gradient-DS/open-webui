# Upstream Merge Plan — April 16, 2026

## Overview

Merge 245 commits from `upstream/dev` (open-webui) into our fork at `merge/260416`. Last merge was March 29, 2026 (`289e02c2a`). Merge base: `9bd84258d`.

**Total conflicts**: 31 code files + 60 i18n translation files.

## Current State Analysis

### Upstream changes by category:
- **19 security fixes** (SSRF, XSS, auth bypasses, timing attacks, access control)
- **12 new features** (automations, backchannel logout, mistral TTS, shortcode emojis, etc.)
- **10 performance optimizations** (async vector DB, worker threads, CSS transitions, early returns)
- **1 major infrastructure refactor** — async DB (74 files, sync→async SQLAlchemy)
- **~100 "refac" commits** — code cleanup, formatting, dep bumps
- **~30 i18n updates** across many locales

### New files from upstream (all auto-merge, no conflicts):
- `backend/open_webui/models/automations.py` — Automation DB models
- `backend/open_webui/routers/automations.py` — Automation API endpoints
- `backend/open_webui/utils/automations.py` — Automation worker loop
- `backend/open_webui/migrations/versions/d4e5f6a7b8c9_add_automation_tables.py`
- `backend/open_webui/retrieval/vector/async_client.py` — AsyncVectorDBClient
- `backend/open_webui/utils/asgi_middleware.py` — Pure ASGI middleware replacements
- `backend/open_webui/utils/session_pool.py` — Session pool utility
- `src/lib/apis/automations/index.ts` — Frontend automation API client
- `src/lib/components/AutomationModal.svelte` + 4 automation components
- `src/routes/(app)/automations/+page.svelte` + `[id]/+page.svelte`
- `src/lib/components/chat/MessageInput/Commands/Emojis.svelte`
- `src/lib/components/chat/MessageInput/InputMenu/Files.svelte`
- `src/lib/components/chat/Messages/ResponseMessage/TaskList.svelte`
- `src/lib/components/common/PanzoomContainer.svelte`
- Various icon components

## Desired End State

All 245 upstream commits merged. Our custom features (OneDrive, Google Drive, TOTP 2FA, agent proxy, GDPR archival, data retention, data export, email invites, feature flags, external pipeline, acceptance modal) fully preserved. Automations adopted but disabled by default in Helm. Build compiles, backend starts, frontend loads.

### Verification:
- `npm run build` succeeds
- `open-webui dev` starts without errors
- All custom feature flags still work
- Alembic migrations apply cleanly
- No regressions in custom API endpoints

## What We're NOT Doing

- **Not testing every custom feature end-to-end** in this merge — that's a separate QA pass
- **Not upgrading our custom model/router code to async** in the merge itself — Phase 10 handles that
- **Not adopting upstream changes that delete our custom code** — we always prefer ours
- **Not resolving any conflict without explicit review** — every conflict is a separate session

## Implementation Approach

Phases are ordered to minimize risk: trivial conflicts first, then bulk i18n, then increasing complexity. Heavy conflicts are isolated into individual phases so each gets full attention. The async DB migration is last because it's the most mechanical and touches the most files.

**Branch strategy**: Work on `merge/260416`. Each phase can be a separate commit for easy bisecting.

---

## Phase 1: Pre-Merge Setup

### Overview
Create the merge state without resolving anything. Validate we can reproduce the conflict list.

### Steps:
1. Ensure `merge/260416` is up to date with `origin/main`
2. `git fetch upstream dev`
3. `git merge --no-commit --no-ff upstream/dev`
4. Verify conflict count matches expectations (31 code + 60 i18n)
5. Save conflict list: `git diff --name-only --diff-filter=U > /tmp/conflicts.txt`

### Success Criteria:
- [x] Merge state is active (not committed, not aborted)
- [x] Conflict file list matches this plan

---

## Phase 2: Trivial Backend Conflicts (No Custom Code)

### Overview
Resolve conflicts in backend files where we have **no custom code**. Accept upstream's version for these.

### Files (10):
| File | Upstream Change | Resolution |
|------|----------------|------------|
| `backend/open_webui/env.py` | Backchannel logout env vars, torch skip | Accept upstream |
| `backend/open_webui/models/chats.py` | Async DB refactor | Accept upstream |
| `backend/open_webui/models/files.py` | Async DB refactor | Accept upstream |
| `backend/open_webui/models/users.py` | Async DB refactor | Accept upstream |
| `backend/open_webui/retrieval/web/firecrawl.py` | Refactoring | Accept upstream |
| `backend/open_webui/routers/chats.py` | Async DB refactor | Accept upstream |
| `backend/open_webui/routers/evaluations.py` | Async DB refactor | Accept upstream |
| `backend/open_webui/routers/folders.py` | Async DB refactor | Accept upstream |
| `backend/open_webui/utils/models.py` | Async DB refactor | Accept upstream |
| `backend/open_webui/utils/oauth.py` | Backchannel logout + async DB | Accept upstream |

### Resolution approach:
For each file: `git checkout --theirs <file> && git add <file>`

But **verify first** that we truly have no custom code by checking for our markers:
```bash
git show HEAD:<file> | grep -i "soev\|gradient\|onedrive\|google_drive\|totp\|2fa\|archival\|gdpr\|agent_proxy\|feature_\|external_pipeline\|invite\|suspend"
```

### Success Criteria:

#### Automated Verification:
- [x] All 10 files show no conflict markers: `grep -r '<<<<<<' <files>`
- [x] No custom code patterns lost in these files

---

## Phase 3: Trivial Frontend Conflicts (No Custom Code)

### Overview
Resolve conflicts in frontend files where we have no or negligible custom code.

### Files (8):
| File | Upstream Change | Resolution |
|------|----------------|------------|
| `src/lib/apis/evaluations/index.ts` | Async refactor | Accept upstream |
| `src/lib/components/admin/Evaluations/Feedbacks.svelte` | Refactoring | Accept upstream |
| `src/lib/components/chat/MessageInput/InputVariablesModal.svelte` | Refactoring | Accept upstream |
| `src/lib/components/workspace/Models/ModelEditor.svelte` | Refactoring | Accept upstream |
| `src/routes/+layout.svelte` | Refactoring | Accept upstream |
| `backend/requirements-min.txt` | New deps (aiosqlite, asyncpg) | Accept upstream |

### Files needing light merge (2):
| File | Our Custom Code | Resolution |
|------|-----------------|------------|
| `src/lib/components/layout/Sidebar/UserMenu.svelte` | soev.ai docs link | Keep our link, accept upstream's automation menu item |
| `backend/requirements.txt` | google-auth, pyotp, qrcode deps | Keep our deps + accept upstream's new deps (aiosqlite, asyncpg) |

### Success Criteria:

#### Automated Verification:
- [x] All 8 files resolved, no conflict markers
- [x] `backend/requirements.txt` contains both our deps and upstream's new deps
- [x] UserMenu still has soev.ai docs link AND upstream's automation menu item

---

## Phase 4: i18n Bulk Resolution (60 files)

### Overview
All 60 translation JSON files have conflicts from both sides adding new keys. Our custom keys (2FA, cloud sync, data export, etc.) must be preserved alongside upstream's new keys (automations, emojis, scheduling).

### Resolution strategy:
Script-based merge: for each conflicting translation file:
1. Extract keys from both sides
2. Merge into a single sorted JSON
3. For duplicate keys, prefer our value (it may contain our translations)

```bash
# For each i18n conflict file:
python3 -c "
import json, sys
# Read both versions
ours = json.loads(open('/tmp/ours.json').read())
theirs = json.loads(open('/tmp/theirs.json').read())
# Merge: theirs first (base), then ours (override)
merged = {**theirs, **ours}
# Sort
merged = dict(sorted(merged.items()))
print(json.dumps(merged, indent='\t', ensure_ascii=False))
"
```

### Special attention:
- `en-US/translation.json` — verify all our custom keys are present (2FA, cloud sync, invites, data export, archival, acceptance modal, feature flags)
- `nl-NL/translation.json` — same, plus verify Dutch translations aren't lost

### Success Criteria:

#### Automated Verification:
- [x] All 60 i18n files resolved, no conflict markers
- [x] `en-US/translation.json` contains all our custom keys (spot check ~10)
- [x] `nl-NL/translation.json` contains all our Dutch translations
- [x] All JSON files are valid: `python3 -c "import json; json.load(open('<file>'))"` for each
- [x] Keys are sorted alphabetically in each file

---

## Phase 5: `backend/open_webui/routers/files.py` (Light)

### Overview
Only 1 custom match (Google Drive export comment). Verify and merge.

### Resolution:
Open the conflict, keep our comment, accept upstream changes.

### Success Criteria:
- [x] File resolved, no conflict markers
- [x] Google Drive export comment preserved

---

## Phase 6: `backend/open_webui/utils/tools.py` (Light)

### Overview
2 feature flag matches. Upstream has async DB refactor here.

### Resolution:
Accept upstream's async refactor, ensure our feature flag checks still work (they may need `async` adjustment).

### Success Criteria:
- [x] File resolved, no conflict markers
- [x] Feature flag checks functional

---

## Phase 7: `src/lib/components/chat/Chat.svelte` (Light-Moderate)

### Overview
Our Google Drive file fetch + type handling vs upstream changes.

### Resolution:
Accept upstream structure, re-apply our Google Drive additions.

### Success Criteria:
- [x] File resolved, no conflict markers
- [x] Google Drive file fetch logic preserved
- [x] `google-drive` type handling works

---

## Phase 8: `src/lib/components/chat/MessageInput.svelte` (Moderate)

### Overview
Our OneDrive/Google Drive picker imports and handlers vs upstream's shortcode emoji additions.

### Resolution:
Keep both — our cloud picker imports/handlers AND upstream's emoji autocomplete. Both are additive features.

### Success Criteria:
- [x] File resolved, no conflict markers
- [x] OneDrive picker import + handler present
- [x] Google Drive picker import + handler present
- [x] Emoji command suggestion integration present (`:` char added)
- [x] Pinned input items still work

---

## Phase 9: `src/lib/components/chat/MessageInput/InputMenu.svelte` (Moderate)

### Overview
Our OneDrive/Google Drive menu items + feature flag guards vs upstream restructuring (Files.svelte extracted).

### Resolution:
Keep our cloud integration items and feature flag guards. Adopt upstream's structural changes. May need to move some of our items into the new `Files.svelte` component if upstream extracted file-related items there.

### Success Criteria:
- [x] File resolved, no conflict markers
- [x] OneDrive menu item with feature flag guard present
- [x] Google Drive menu item with feature flag guard present
- [x] `FEATURE_WEBPAGE_URL` and `FEATURE_REFERENCE_CHATS` guards preserved
- [x] Pin support for cloud items preserved
- [x] Upstream's new structure adopted (Files.svelte tab added)

---

## Phase 10: `backend/open_webui/models/auths.py` (Moderate)

### Overview
Our TOTP 2FA columns (`totp_secret`, `totp_enabled`, `totp_last_used_at`) + methods vs upstream's async DB refactor converting all methods to async.

### Resolution:
Accept upstream's async conversion, then re-apply our TOTP columns and convert our TOTP methods to async.

### Changes:
- Keep TOTP columns in the model
- Convert `update_totp()` → `async def update_totp()`
- Convert `update_totp_last_used()` → `async def update_totp_last_used()`
- Use `AsyncSession` instead of sync `Session`

### Success Criteria:
- [x] File resolved, no conflict markers
- [x] TOTP columns present in model
- [x] TOTP methods are async
- [x] All upstream async DB changes adopted

---

## Phase 11: `backend/open_webui/routers/auths.py` (Moderate-Heavy)

### Overview
Our TOTP partial-JWT flow in signin + acceptance modal config vs upstream's backchannel logout, async DB, JWT expiry fix, and first-admin-race fix.

### Resolution:
Accept all upstream security fixes and async conversion. Re-apply our TOTP partial-JWT flow (converting to async). Re-apply acceptance modal config endpoints (converting to async).

### Key upstream security to preserve:
- `faf935ef5` — JWT expiry on `/auths/add`
- `96a0b3239` — first-user admin race prevention
- `b78dabb44` — empty LDAP password rejection

### Success Criteria:
- [x] File resolved, no conflict markers
- [x] TOTP partial-JWT flow works (async)
- [x] Acceptance modal config endpoints present (async)
- [x] All 3 upstream security fixes preserved
- [x] Backchannel logout integration present

---

## Phase 12: `backend/open_webui/routers/users.py` (Moderate)

### Overview
Our user archival (ArchiveService) + admin 2FA status/disable endpoints vs upstream's async DB refactor.

### Resolution:
Accept upstream async refactor, convert our archival and 2FA admin endpoints to async.

### Success Criteria:
- [x] File resolved, no conflict markers
- [x] ArchiveService integration present (async)
- [x] 2FA admin status/disable endpoints present (async)

---

## Phase 13: `backend/open_webui/routers/configs.py` (Heavy)

### Overview
Our 6 custom endpoint groups (invites, agent proxy, 2FA, data retention, invite content, invite settings) vs upstream's async DB refactor + new config patterns.

### Resolution:
Accept upstream's async structure, re-apply all our custom endpoint groups with async conversion.

### Custom endpoints to preserve:
1. Invite content CRUD
2. Invite settings CRUD
3. Agent proxy config
4. 2FA config (ENABLE_2FA, REQUIRE_2FA, grace period)
5. Data retention config + test endpoint
6. Any other soev-specific config

### Success Criteria:
- [x] File resolved, no conflict markers
- [x] All 6 custom endpoint groups present and async
- [x] Upstream async patterns adopted

---

## Phase 14: `backend/open_webui/routers/knowledge.py` (Heavy)

### Overview
Our suspension checks (403 for suspended KBs), cloud KB type handling, OneDrive file removal logic vs upstream's async DB refactor.

### Resolution:
Accept upstream async refactor, re-apply all suspension and cloud KB logic with async conversion.

### Custom logic to preserve:
- Suspension check (return 403 for suspended KBs)
- Cloud KB type validation on create/update
- OneDrive/Google Drive file removal with source tracking
- Cloud KB filtering in stale-KB operations

### Success Criteria:
- [x] File resolved, no conflict markers
- [x] Suspension checks functional (async)
- [x] Cloud KB type handling present
- [x] File removal with source tracking preserved

---

## Phase 15: `backend/open_webui/routers/retrieval.py` (Heavy)

### Overview
Our external pipeline integration (import, config, try-external-then-fallback) + cloud integration toggles vs upstream's async vector DB, loader threading, and retrieval security fixes.

### Resolution:
Accept upstream's AsyncVectorDBClient and threading changes. Re-apply our external pipeline fallback logic. Preserve cloud integration config toggles.

### Upstream infrastructure to adopt:
- `AsyncVectorDBClient` wrapper usage
- Worker thread for `Loader.load`
- Security: SSRF protection improvements

### Custom logic to preserve:
- External pipeline provider integration
- Try-external-then-fallback pattern
- OneDrive/Google Drive integration toggles in RAG config

### Success Criteria:
- [x] File resolved, no conflict markers
- [x] External pipeline fallback works
- [x] Cloud integration config toggles present
- [x] AsyncVectorDBClient used correctly
- [x] SSRF protection from upstream adopted

---

## Phase 16: `backend/open_webui/models/knowledge.py` (Heavy)

### Overview
Our typed KBs + suspension lifecycle (`suspension_info`, `is_suspended()`, `get_suspension_info()`, `get_suspended_expired_knowledge()`) vs upstream's async DB refactor.

### Resolution:
Accept upstream async refactor. Convert all our custom methods to async. Preserve suspension lifecycle and typed KB logic.

### Custom methods to convert:
- `is_suspended()` — may stay sync if it's a property
- `get_suspension_info()` → async if it touches DB
- `get_suspended_expired_knowledge()` → `async def`
- Type validation in create/update

### Success Criteria:
- [x] File resolved, no conflict markers
- [x] Typed KB support preserved (onedrive, google_drive types)
- [x] Suspension lifecycle functional (async)
- [x] All upstream async patterns adopted

---

## Phase 17: `backend/open_webui/main.py` (Heavy)

### Overview
The most complex file. Our additions: agent proxy router, TOTP router, OneDrive/Google Drive sync routers + schedulers, email invites router, external pipeline health check, data retention periodic task, data export cleanup, archival service, acceptance modal config, feature flags, SOEV branding, AGENT_API bypass. Upstream: automation router mount, mistral TTS, backchannel logout, middleware refactor (pure ASGI), async DB session wiring.

### Resolution:
Accept upstream's structural changes (middleware refactor, automation router mount, async session wiring). Re-apply all our custom router mounts, schedulers, periodic tasks, and config wiring.

### Upstream additions to adopt:
- `automations.router` mount at `/api/v1/automations`
- `automation_worker_loop` task
- `AUTOMATION_MAX_COUNT`, `AUTOMATION_MIN_INTERVAL` config
- Mistral TTS router additions
- Backchannel logout setup
- Pure ASGI middleware replacements
- `APIKeyRestrictionMiddleware`
- Provider error logging

### Custom code to preserve:
- Agent proxy router mount
- TOTP router mount
- OneDrive sync router + scheduler
- Google Drive sync router + scheduler
- Email invites router mount
- External pipeline health check endpoint
- Data retention periodic task
- Data export cleanup task
- Archival service initialization
- Acceptance modal config state
- Feature flag config state (FEATURE_WEBPAGE_URL, FEATURE_REFERENCE_CHATS)
- SOEV branding
- AGENT_API bypass logic

### Success Criteria:
- [x] File resolved, no conflict markers
- [x] All custom router mounts present
- [x] All custom periodic tasks present
- [x] Automation router mounted
- [x] Automation worker loop started
- [x] Middleware stack uses pure ASGI where upstream changed
- [ ] Backend starts without import errors

---

## Phase 18: Automation Feature — Helm Integration

### Overview
Upstream's automation feature is already gated by `USER_PERMISSIONS_FEATURES_AUTOMATIONS` (default `False`). We need to wire this into our Helm chart so it's configurable and defaulted to off.

### Changes:

#### 1. `helm/open-webui-tenant/values.yaml`

Add under `openWebui.config`:
```yaml
# Automations
userPermissionsFeaturesAutomations: "False"
automationMaxCount: ""
automationMinInterval: ""
```

#### 2. `helm/open-webui-tenant/templates/open-webui/configmap.yaml`

Add to the configmap:
```yaml
USER_PERMISSIONS_FEATURES_AUTOMATIONS: {{ .Values.openWebui.config.userPermissionsFeaturesAutomations | quote }}
AUTOMATION_MAX_COUNT: {{ .Values.openWebui.config.automationMaxCount | quote }}
AUTOMATION_MIN_INTERVAL: {{ .Values.openWebui.config.automationMinInterval | quote }}
```

### Success Criteria:

#### Automated Verification:
- [ ] `helm template` renders without errors
- [ ] `USER_PERMISSIONS_FEATURES_AUTOMATIONS` defaults to `"False"` in rendered output

#### Manual Verification:
- [ ] Automation menu item NOT visible for regular users
- [ ] Admin can enable automations via user permissions
- [ ] When enabled, automation page loads and functions

---

## Phase 19: Async DB Migration — Custom Routers & Models

### Overview
The async DB refactor (`27169124f`) converts all model methods and router DB calls from sync to async. In earlier phases, we already resolved the conflicting files. This phase handles our custom routers and models that had NO conflicts but still need async conversion to match the new DB layer.

**Important**: This phase may not be needed if our custom routers/models are in separate files that don't share the sync session. Check if:
1. Our custom routers use their own `get_db` context or import from `internal/db.py`
2. The sync `get_db` / `get_db_context` is still available (upstream keeps it for startup)

### Files to check:
- `backend/open_webui/routers/onedrive.py` — OneDrive sync endpoints
- `backend/open_webui/routers/google_drive.py` — Google Drive sync endpoints
- `backend/open_webui/routers/invites.py` — Email invite endpoints
- `backend/open_webui/routers/agent_proxy.py` — Agent proxy
- `backend/open_webui/routers/export.py` — Data export
- `backend/open_webui/routers/totp.py` — TOTP 2FA endpoints
- `backend/open_webui/services/` — Archival, data retention, sync workers
- Any custom model files

### Approach:
For each custom file:
1. Check if it imports `get_db` or `Session` from `internal/db.py`
2. If so, convert to use `get_async_db` / `AsyncSession`
3. Convert affected methods to `async def`
4. Add `await` to all DB calls

### Driver consideration:
We use PostgreSQL. The async refactor switches from `psycopg2` (sync) to `asyncpg` (async). The sync engine is kept for startup/migrations. Our `DATABASE_URL` is auto-converted by `_make_async_url()`:
- `postgresql://` → `postgresql+asyncpg://`
- `postgresql+psycopg2://` → `postgresql+asyncpg://`

Verify SSL/connection parameters work with asyncpg.

### Success Criteria:

#### Automated Verification:
- [ ] `open-webui dev` starts without import errors
- [ ] No sync DB calls in async request paths (audit via grep)
- [ ] All custom endpoints respond correctly

#### Manual Verification:
- [ ] OneDrive sync triggers successfully
- [ ] Google Drive sync triggers successfully
- [ ] Email invite sends successfully
- [ ] Data export generates zip
- [ ] TOTP setup/verification works
- [ ] User archival works

---

## Phase 20: Post-Merge Verification

### Overview
Full verification pass after all conflicts resolved.

### Steps:

1. **Build check**:
   ```bash
   npm run build
   ```

2. **Backend startup**:
   ```bash
   open-webui dev
   ```

3. **Alembic migrations**:
   ```bash
   # Verify migration chain is clean
   cd backend && alembic heads
   # Should show single head including automation migration
   ```

4. **Custom feature audit**:
   ```bash
   # Verify all custom routers are mounted
   grep -n "include_router" backend/open_webui/main.py | grep -i "onedrive\|google_drive\|invite\|totp\|agent_proxy\|export\|archiv"
   ```

5. **Security fix audit**:
   Verify the 19 upstream security fixes are present by spot-checking key ones:
   ```bash
   grep -n "hmac.compare_digest" backend/open_webui/utils/auth.py
   grep -n "ENABLE_OPENAI_API_PASSTHROUGH" backend/open_webui/config.py
   grep -n "DOMPurify\|sanitize" src/lib/components/chat/ModelSelector/
   ```

6. **Dependency check**:
   ```bash
   grep "asyncpg\|aiosqlite\|pyotp\|qrcode\|google-auth" backend/requirements.txt
   ```

### Success Criteria:

#### Automated Verification:
- [ ] `npm run build` succeeds
- [ ] `open-webui dev` starts and serves requests
- [ ] Alembic shows single head
- [ ] All custom router mounts present in main.py
- [ ] Security fixes present (spot check)
- [ ] All dependencies present in requirements.txt

#### Manual Verification:
- [ ] Frontend loads in browser
- [ ] Login works
- [ ] Chat functions
- [ ] Admin panel accessible
- [ ] Custom features visible in UI (OneDrive, Google Drive, feature flags)
- [ ] Automation menu NOT visible by default

---

## Phase Summary

| Phase | Description | Complexity | Est. Files |
|-------|-------------|------------|------------|
| 1 | Pre-merge setup | Trivial | 0 |
| 2 | Trivial backend conflicts | Easy | 10 |
| 3 | Trivial frontend conflicts + requirements | Easy | 8 |
| 4 | i18n bulk resolution | Scripted | 60 |
| 5 | `routers/files.py` | Easy | 1 |
| 6 | `utils/tools.py` | Easy | 1 |
| 7 | `Chat.svelte` | Easy-Moderate | 1 |
| 8 | `MessageInput.svelte` | Moderate | 1 |
| 9 | `InputMenu.svelte` | Moderate | 1 |
| 10 | `models/auths.py` | Moderate | 1 |
| 11 | `routers/auths.py` | Moderate-Heavy | 1 |
| 12 | `routers/users.py` | Moderate | 1 |
| 13 | `routers/configs.py` | Heavy | 1 |
| 14 | `routers/knowledge.py` | Heavy | 1 |
| 15 | `routers/retrieval.py` | Heavy | 1 |
| 16 | `models/knowledge.py` | Heavy | 1 |
| 17 | `main.py` | Heavy | 1 |
| 18 | Automation Helm integration | Easy | 2 |
| 19 | Async DB — custom code | Moderate-Heavy | ~10 |
| 20 | Post-merge verification | Audit | 0 |

## Testing Strategy

### Per-Phase:
- Each phase ends with `grep -r '<<<<<<' <resolved-files>` to verify no remaining markers
- Commit after each phase for easy bisecting

### Post-Merge:
- `npm run build` — frontend compiles
- `open-webui dev` — backend starts
- Manual smoke test of key flows

## Migration Notes

### Database:
- Automation tables migration: `d4e5f6a7b8c9_add_automation_tables.py` (auto-applies)
- Tasks/summary migration: `a3dd5bedd151_add_tasks_and_summary_to_chat.py` (auto-applies)
- Last-read-at migration: `b7c8d9e0f1a2_add_last_read_at_to_chat.py` (auto-applies)
- Note pinning migration: `e1f2a3b4c5d6_add_is_pinned_to_note.py` (auto-applies)

### Dependencies:
- New: `asyncpg==0.30.0` (PostgreSQL async driver), `aiosqlite==0.21.0` (SQLite async)
- Kept: Our `pyotp`, `qrcode`, `google-auth`, `google-auth-oauthlib`, `google-auth-httplib2`

### PostgreSQL async driver:
- `asyncpg` replaces `psycopg2` for runtime queries
- `psycopg2` kept for startup migrations only
- URL auto-conversion: `postgresql://` → `postgresql+asyncpg://`
- Verify SSL/connection parameters work with asyncpg in deployment

## References

- Last upstream merge: PR #58, commit `289e02c2a` (March 29, 2026)
- Merge base: `9bd84258d`
- Upstream target: `upstream/dev` at `70a6a24f1`
- Custom features inventory: See index.md entries from 20-03-2026 to 06-04-2026
- Sync abstraction cookbook: `collab/docs/external-integration-cookbook.md`
