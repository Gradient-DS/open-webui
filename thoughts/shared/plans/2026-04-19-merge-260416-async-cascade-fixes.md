# Upstream Merge (260416) — Async Cascade Fixes Implementation Plan

## Overview

The `merge/260416` branch merged upstream's sync→async DB refactor
(commit `27169124f`). The earlier audit found ~20 regressions, and a
follow-up plan at
`thoughts/shared/plans/2026-04-19-merge-260416-remaining-regressions.md`
is addressing those. A deeper round of auditing has surfaced a further
**29 items neither the research doc nor either existing plan catches**
— concentrated in async call-site gaps that only matter once the
affected feature is exercised at runtime (OAuth token exchange, invite
flow, external pipeline processing, external-agent registration,
conversation feedback endpoint, file post-processing socket emits).

This plan closes all of them. It does not overlap with the earlier
plan — the earlier one owns model-surface restoration (`get_inactive_users`,
`get_knowledge_bases_by_type`, `UserModel.__repr__`) and the routers
that dispatch to those. This plan owns everything that still breaks
on invocation after those changes land.

## Current State Analysis

### Impact summary

- **OneDrive + Google Drive connect flow is completely broken** — token
  exchange silently fails, stored-token checks always return truthy
  (coroutine), token-refresh poll crashes.
- **Invite flow is 100% broken** — every invite create returns 400,
  every invite accept silently writes nothing, list-invites 500s.
- **External pipeline processing silently writes nothing to DB** — file
  status never flips to `completed`; chunks never persist.
- **External agents fail to register on boot** — the lifespan path
  invokes a sync function that calls async `Functions.*` methods
  without awaiting them.
- **Conversation feedback endpoint (404)** — custom
  `/evaluations/feedback/conversation/{chat_id}` endpoint + its model
  method were dropped; frontend still calls it.
- **Integration-provider service-account binding silently no-ops** —
  `/integrations` config save appears to succeed but never persists
  `user.info.integration_provider`.
- **File post-processing socket.io emit breaks** for any upload — the
  `{file_id, collection_name}` payload can't be built because
  `Files.get_file_by_id` returns a coroutine.
- Inside `services/sync/provider.py:96`, every sync execution for
  both OneDrive and Google Drive short-circuits on a coroutine-truthy
  check and then crashes on `.meta` access.
- The `update_file_path_by_id` custom method was dropped from
  `models/files.py`; its only caller in `base_worker.py:885` will
  raise `AttributeError` on every cloud-synced file path update.

### Key file inventory

| Area | Files |
|---|---|
| Dropped model methods + endpoints | `models/files.py`, `models/feedbacks.py`, `routers/evaluations.py` |
| OneDrive auth async conversion | `services/onedrive/auth.py`, `services/onedrive/provider.py` |
| Google Drive auth async conversion | `services/google_drive/auth.py`, `services/google_drive/provider.py` |
| Sync router call sites | `services/sync/router.py`, `services/sync/scheduler.py`, `services/sync/token_refresh.py`, `services/sync/provider.py` |
| Missing awaits in routers | `routers/invites.py`, `routers/files.py` |
| Sync helpers that should be async | `routers/external_retrieval.py`, `routers/configs.py`, `utils/external_agents.py` |
| Knowledge router shim cleanup | `routers/knowledge.py` |
| Frontend feature-flag guards | `src/lib/components/workspace/Models/ModelEditor.svelte` |

### Pre-merge source of truth

For every dropped method, block, or endpoint, run
`git show 457f01af2:<path>` and port semantics forward to the async
shape of the post-merge codebase.

## Desired End State

1. OneDrive and Google Drive OAuth connect succeeds end-to-end —
   token persists to `oauth_session`, `has_stored_token` returns
   correctly, `/api/v1/sync/token/status` returns 200.
2. Invite lifecycle works — create → validate → accept → list all
   succeed without 400/500/silent-no-op.
3. External pipeline file processing marks file as `completed`,
   writes chunks to DB, and emits the socket-io ready event.
4. External agents that exist in `agents_config` register successfully
   during startup lifespan; re-registering an existing agent updates
   it.
5. Integration-provider service-account binding persists
   `user.info.integration_provider` on save and clears it on unbind.
6. `/api/v1/evaluations/feedback/conversation/{chat_id}` returns 200
   with the conversation-feedback payload.
7. Every uploaded file's post-processing socket.io event fires with
   the correct `{file_id, collection_name}` payload.
8. Every OneDrive/Google Drive sync tick completes without
   `AttributeError` on `knowledge.meta`.
9. `Files.update_file_path_by_id` exists as `async def` and is awaited
   at `base_worker.py:885`.
10. Knowledge detail endpoints (`GET /`, `GET /{id}`) no longer hop
    through the `get_suspension_info` thread-pool shim; they call
    `async_get_suspension_info` directly.
11. Model editor shows Knowledge/Tools/Skills/Voice sections only
    when the corresponding `$config?.features?.feature_*` flag is
    truthy.

### Verification (entire plan)

- `open-webui dev` starts clean, runs ≥5 min without `AttributeError`,
  `TypeError: object ... is not awaitable`, or
  `RuntimeWarning: coroutine ... was never awaited` from any code
  path exercised by the smoke tests below.
- `npm run build` succeeds; `npm run lint:backend` and
  `npm run lint:frontend` clean on all touched files (no new errors).
- Manual smoke tests per phase pass.

### Key Discoveries

- **`Files.update_file_path_by_id`** — pre-merge `457f01af2:backend/open_webui/models/files.py:349`
  was a sync `def`. Restore as `async def` mirroring the post-merge
  `update_file_hash_by_id` (:349) shape.
- **`Feedbacks.get_conversation_feedback_by_chat_id_and_user_id`** —
  dropped by upstream. Pre-merge shape: joins `Feedback` on `chat_id`
  and `user_id`, returns list ordered by `created_at asc`. Post-merge
  async restoration should use `select(Feedback).filter_by(...)` +
  `.order_by(Feedback.created_at.asc())` — single DB round-trip.
- **OneDrive + Google Drive `auth.py`** — both files are structurally
  parallel. The fix shape is identical; doing one while reading the
  other open makes the second easier.
- **TokenManager interface flip** — `has_stored_token` / `delete_token`
  / `get_stored_token` need to become `async def` across both
  providers. Callers (`sync/router.py`, `sync/scheduler.py`,
  `sync/token_refresh.py`) are already inside `async def` contexts —
  only the TokenManager-side methods change. No new async propagation
  needed upward.
- **`register_external_agent_direct`** — called from sync
  `load_external_agents_at_startup`, which is invoked from
  `async def lifespan` at `main.py:837` via direct call (not
  `run_in_threadpool`). Safe to flip both helpers to `async def` +
  `await` their model calls.
- **`process_file_with_external_pipeline`** — called once from
  `routers/retrieval.py:1813` inside already-`async def process_file`.
  Safe to flip to `async def` + `await` at the call site.
- **`_bind_service_account` / `_unbind_service_account`** — called
  twice from already-`async def set_integrations_config` (configs.py:834).
  Safe flip.
- **`Feedbacks` model class name** — verify exact class name in
  `models/feedbacks.py` (upstream may have renamed `Feedbacks` →
  `FeedbackTable` or similar). Use the post-merge export name when
  restoring the method.
- **The `update_file_path_by_id` caller pattern at `base_worker.py:885`**
  is inside an `async def` helper that returns a `Union[PreparedFile,
  FailedFile]`; the surrounding call `await Files.update_file_by_id(...)`
  at :881 already uses the await pattern — model the new call after
  that.
- **ModelEditor feature-flag guard shapes from pre-merge**
  (`457f01af2:src/lib/components/workspace/Models/ModelEditor.svelte`):

  ```svelte
  {#if $config?.features?.feature_knowledge !== false}
      <div class="my-4"><Knowledge bind:selectedItems={knowledge} /></div>
  {/if}
  {#if $config?.features?.feature_tools !== false}
      <div class="my-4"><ToolsSelector bind:selectedToolIds={toolIds} tools={$tools ?? []} /></div>
  {/if}
  {#if $config?.features?.feature_skills}
      <div class="my-4"><SkillsSelector bind:selectedSkillIds={skillIds} /></div>
  {/if}
  {#if $config?.features?.feature_voice !== false && $config?.audio?.tts?.engine}
      <div class="my-4">...<input bind:value={tts.voice} .../></div>
  {/if}
  ```

  Note: `feature_knowledge`, `feature_tools`, `feature_voice` use
  `!== false` (default-on). `feature_skills` uses truthy (default-off).
  Match exactly.

## What We're NOT Doing

- Not restoring the cosmetic Notes-page breadcrumb header at
  `src/routes/(app)/notes/+page.svelte:~67-77` — user decision, left
  as-is.
- Not touching the soft-delete surface or the remaining-regressions
  plan's scope. This plan runs in parallel (separate PR).
- Not adding new features or refactoring beyond what these fixes
  require.
- Not re-running the merge.
- Not adding new Cypress tests — existing suites should continue to
  pass.
- Not introducing cross-provider abstractions to reduce
  OneDrive/Google Drive duplication. The two files remain
  independent; matching fixes are applied to both.
- Not migrating `get_suspension_info` sync shim away globally — only
  the two router call sites that reside in `async def` handlers. The
  shim stays for any sync callers (there are none left after Phase 5
  but deleting the shim is out of scope).

## Implementation Approach

Six phases, each independently committable. Phase dependencies:

- Phase 1 is a prerequisite for Phase 3 (`base_worker.py:885` needs
  `Files.update_file_path_by_id` to exist) and the conversation
  feedback endpoint.
- Phase 2 must land as a single unit — the OAuth `auth.py` ↔
  `provider.py` ↔ `sync/router.py` chain cannot be partially
  converted without broken intermediate states.
- Phases 3, 4, 5, 6 are independent and can interleave.

Order the PR commits: Phase 1 → Phase 2 → Phase 3 → Phase 4 → Phase 5 → Phase 6.

---

## Phase 1: Restore Dropped Model Methods + Endpoints

### Overview

Restore two dropped backend surfaces:
- `Files.update_file_path_by_id` (sync method dropped by upstream's
  async refactor; no async replacement exists).
- `Feedbacks.get_conversation_feedback_by_chat_id_and_user_id` +
  the custom `/evaluations/feedback/conversation/{chat_id}` endpoint.

### Changes Required

#### 1. `backend/open_webui/models/files.py` — restore `update_file_path_by_id`

Location: after `update_file_hash_by_id` (post-merge line ~349) and
before `update_file_data_by_id` (~:364). Mirror the `update_file_hash_by_id`
shape.

```python
async def update_file_path_by_id(
    self, id: str, path: str, db: Optional[AsyncSession] = None
) -> Optional[FileModel]:
    async with get_async_db_context(db) as db:
        try:
            result = await db.execute(select(File).filter_by(id=id))
            file = result.scalars().first()
            if not file:
                return None
            file.path = path
            file.updated_at = int(time.time())
            await db.commit()
            return FileModel.model_validate(file)
        except Exception:
            return None
```

Confirm `File` table has a `path` column (it does — it's the primary
storage-location column). No migration needed.

#### 2. `backend/open_webui/models/feedbacks.py` — restore `get_conversation_feedback_by_chat_id_and_user_id`

Grep for the post-merge class name (`Feedbacks`, `FeedbackTable`, or
similar). Append a new async method on that class:

```python
async def get_conversation_feedback_by_chat_id_and_user_id(
    self, chat_id: str, user_id: str, db: Optional[AsyncSession] = None
) -> list[FeedbackModel]:
    """Return all feedback rows tied to a given chat, owned by a given user, oldest-first."""
    async with get_async_db_context(db) as db:
        result = await db.execute(
            select(Feedback)
            .filter_by(chat_id=chat_id, user_id=user_id)
            .order_by(Feedback.created_at.asc())
        )
        return [FeedbackModel.model_validate(f) for f in result.scalars().all()]
```

Verify column names (`chat_id`, `user_id`, `created_at`) match the
post-merge `Feedback` table definition. Adjust if upstream renamed any.

#### 3. `backend/open_webui/routers/evaluations.py` — restore endpoint

Port pre-merge endpoint from `457f01af2:backend/open_webui/routers/evaluations.py`:

```python
@router.get('/feedback/conversation/{chat_id}')
async def get_conversation_feedback(
    chat_id: str,
    user=Depends(get_verified_user),
):
    feedback_items = await Feedbacks.get_conversation_feedback_by_chat_id_and_user_id(
        chat_id=chat_id, user_id=user.id
    )
    return feedback_items
```

Match the exact imports and return shape the frontend expects at
`src/lib/apis/evaluations/index.ts:275-299`.

#### 4. Frontend verification (no code change)

`src/lib/components/chat/ConversationFeedback.svelte:39` calls
`getConversationFeedback(token, chatId)`. After this phase it should
return 200 instead of 404. Smoke-test only; no edit.

### Success Criteria

#### Automated Verification

- [x] `grep -n "async def update_file_path_by_id" backend/open_webui/models/files.py` returns one match
- [x] `grep -n "async def get_conversation_feedback_by_chat_id_and_user_id" backend/open_webui/models/feedbacks.py` returns one match
- [x] `grep -n "/feedback/conversation/{chat_id}" backend/open_webui/routers/evaluations.py` returns one match
- [x] `npm run lint:backend` passes on the three touched files
- [x] `npm run format:backend` produces no diff
- [x] `python -c "from open_webui.models.files import Files; from open_webui.models.feedbacks import Feedbacks; import inspect; assert inspect.iscoroutinefunction(Files.update_file_path_by_id); assert inspect.iscoroutinefunction(Feedbacks.get_conversation_feedback_by_chat_id_and_user_id)"` exits 0

#### Manual Verification

- [ ] Enable conversation feedback and open a chat with at least one feedback entry — `ConversationFeedback.svelte` renders the feedback list (200, not 404)
- [ ] Connect a OneDrive KB and trigger a sync that includes a file rename on the provider side — no `AttributeError` on `base_worker.py:885` (full test comes after Phase 3 adds the await)

**Implementation Note**: After this phase's automated checks pass, pause for human confirmation before Phase 2.

---

## Phase 2: OAuth Auth Modules → Async (OneDrive + Google Drive + TokenManager Interface)

### Overview

Flip the entire OneDrive and Google Drive OAuth auth path to async.
This covers `services/{onedrive,google_drive}/auth.py` (6 top-level
helpers each), the `TokenManager` interface in each provider's
`provider.py` (3 methods each), and the two sync-router handlers that
call the TokenManager surface.

All changes land as one commit — leaving the provider interface half
sync while `auth.py` is fully async creates broken intermediate
states.

### Changes Required

#### 1. `backend/open_webui/services/onedrive/auth.py`

Flip these helpers to `async def` and `await` every `OAuthSessions.*`
call inside:

- `exchange_code_for_tokens` (around line 170) — already `async def`.
  Add `await` at lines 181, 183, 186.
- `get_stored_token` (around line 199) → `async def`; `await` the
  `OAuthSessions.get_session_by_provider_and_user_id` call inside.
- `delete_stored_token` (around line 218) → `async def`; `await` the
  `OAuthSessions.*` call inside.
- `_migrate_legacy_sessions` (around line 230) → `async def`; `await`
  the 3 `OAuthSessions.*` calls inside.
- `_delete_legacy_sessions` (around line 264) → `async def`; `await`
  the 2 `OAuthSessions.*` calls inside.

Pattern to apply at each call site:

```python
# Before
existing = OAuthSessions.get_session_by_provider_and_user_id(provider, user_id)

# After
existing = await OAuthSessions.get_session_by_provider_and_user_id(provider, user_id)
```

#### 2. `backend/open_webui/services/google_drive/auth.py`

Mirror the exact same changes as `onedrive/auth.py` — the files are
structurally parallel. Specific sites:

- `exchange_code_for_tokens` (around line 170) — add `await` at
  lines 177, 179, 181.
- `get_stored_token` (around line 194) → `async def` with `await`.
- `delete_stored_token` (around line 203) → `async def` with `await`.

Check for any `_migrate_legacy_sessions` / `_delete_legacy_sessions`
equivalents in this file; if present, apply the same flip.

#### 3. `backend/open_webui/services/onedrive/provider.py`

The `OneDriveTokenManager` class (around line 25+) has three methods
that wrap the `auth.py` helpers:

```python
# Before
class OneDriveTokenManager(TokenManager):
    def has_stored_token(self, user_id: str) -> bool:
        return get_stored_token(user_id) is not None

    def delete_token(self, user_id: str) -> bool:
        return delete_stored_token(user_id)

    def get_stored_token(self, user_id: str) -> Optional[dict]:
        return get_stored_token(user_id)

# After
class OneDriveTokenManager(TokenManager):
    async def has_stored_token(self, user_id: str) -> bool:
        return await get_stored_token(user_id) is not None

    async def delete_token(self, user_id: str) -> bool:
        return await delete_stored_token(user_id)

    async def get_stored_token(self, user_id: str) -> Optional[dict]:
        return await get_stored_token(user_id)
```

The abstract base class `TokenManager` in `services/sync/provider.py`
also needs its three method declarations flipped to `async def` so
subclasses match. Verify the abstract declarations — if they were
`@abstractmethod def has_stored_token`, update to `@abstractmethod async def has_stored_token` etc.

#### 4. `backend/open_webui/services/google_drive/provider.py`

Mirror the exact same changes as `onedrive/provider.py` for
`GoogleDriveTokenManager`.

#### 5. `backend/open_webui/services/sync/router.py` — call sites

Line ~270 inside `async def handle_get_token_status`:

```python
# Before
token_data = get_stored_token_fn(user.id)

# After
token_data = await get_stored_token_fn(user.id)
```

Line ~300 inside `async def handle_revoke_token`:

```python
# Before
deleted = delete_stored_token_fn(user.id)

# After
deleted = await delete_stored_token_fn(user.id)
```

Grep for any remaining `has_stored_token` / `delete_token` /
`get_stored_token` invocations across `services/sync/` and
`routers/` — add `await` at each (likely 2–3 more sites in
`scheduler.py` and `token_refresh.py`).

### Success Criteria

#### Automated Verification

- [x] `grep -rn "async def exchange_code_for_tokens\|async def get_stored_token\|async def delete_stored_token\|async def _migrate_legacy_sessions\|async def _delete_legacy_sessions" backend/open_webui/services/onedrive/auth.py backend/open_webui/services/google_drive/auth.py` returns ≥8 matches (5 onedrive + 3 google_drive — google_drive may not have the `_migrate_legacy_sessions` variant).
- [x] `grep -rn "async def has_stored_token\|async def delete_token\|async def get_stored_token" backend/open_webui/services/` returns ≥6 matches (base class + 2 providers × 3 methods = 7, depending on base shape).
- [x] `grep -rn "= OAuthSessions\.\|OAuthSessions\.[a-z_]\+(" backend/open_webui/services/onedrive/auth.py backend/open_webui/services/google_drive/auth.py` — every match is prefixed with `await`.
- [x] `grep -n "get_stored_token_fn(user.id)\|delete_stored_token_fn(user.id)" backend/open_webui/services/sync/router.py` — every match has `await` on the same line.
- [x] `python -W error::RuntimeWarning -c "from open_webui.services.onedrive.provider import OneDriveSyncProvider; from open_webui.services.google_drive.provider import GoogleDriveSyncProvider; import inspect; assert inspect.iscoroutinefunction(OneDriveSyncProvider().token_manager.has_stored_token); assert inspect.iscoroutinefunction(GoogleDriveSyncProvider().token_manager.has_stored_token)"` exits 0.
- [x] `npm run lint:backend` clean on the 6 touched files.

#### Manual Verification

- [ ] Connect OneDrive via admin UI — OAuth consent → callback → UI shows "connected". Verify `oauth_session` row exists for this user+provider.
- [ ] Same for Google Drive.
- [ ] `GET /api/v1/sync/token/status?provider=onedrive` returns `{stored: true, expires_at: ...}`.
- [ ] `DELETE /api/v1/sync/token?provider=onedrive` returns 200; subsequent `/token/status` returns `{stored: false}`.
- [ ] Trigger a manual sync on a connected OneDrive KB — sync worker executes end-to-end, no `AttributeError` in logs.

**Implementation Note**: Pause for human confirmation before Phase 3.

---

## Phase 3: Missing Awaits in Routers/Services

### Overview

Quick-win `await`-only fixes — 7 call sites, no function signature
changes needed.

### Changes Required

#### 1. `backend/open_webui/routers/invites.py`

Four sites — each inside an already-`async def` handler:

- Line ~90 inside `create_invite`:
  ```python
  # Before
  if Users.get_user_by_email(email):
  # After
  if await Users.get_user_by_email(email):
  ```
- Line ~211 inside `validate_invite`:
  ```python
  # Before
  invited_by_user = Users.get_user_by_id(invite.invited_by)
  # After
  invited_by_user = await Users.get_user_by_id(invite.invited_by)
  ```
- Line ~274 inside `accept_invite`:
  ```python
  # Before
  new_user = Auths.insert_new_auth(
      email=..., password=..., name=..., role=...,
  )
  # After
  new_user = await Auths.insert_new_auth(
      email=..., password=..., name=..., role=...,
  )
  ```
- Line ~351 inside `list_invites` (within `for invite in invites:`):
  ```python
  # Before
  invited_by_user = Users.get_user_by_id(invite.invited_by)
  # After
  invited_by_user = await Users.get_user_by_id(invite.invited_by)
  ```

#### 2. `backend/open_webui/routers/files.py:~158`

Inside `async def _process_handler` (nested inside
`async def process_uploaded_file`):

```python
# Before
file_data = Files.get_file_by_id(file_item.id)
# After
file_data = await Files.get_file_by_id(file_item.id)
```

#### 3. `backend/open_webui/services/sync/provider.py:~96`

Inside `async def execute_sync` (base class, inherited by both
OneDrive and Google Drive sync providers):

```python
# Before
knowledge = Knowledges.get_knowledge_by_id(id=knowledge_id)
# After
knowledge = await Knowledges.get_knowledge_by_id(id=knowledge_id)
```

#### 4. `backend/open_webui/services/sync/base_worker.py:885`

Once Phase 1 landed `Files.update_file_path_by_id` as async:

```python
# Before
Files.update_file_path_by_id(file_id, file_path)
# After
await Files.update_file_path_by_id(file_id, file_path)
```

### Success Criteria

#### Automated Verification

- [x] `grep -n "^\s*await Users.get_user_by_email\|^\s*if await Users.get_user_by_email" backend/open_webui/routers/invites.py` returns one match
- [x] `grep -n "await Users.get_user_by_id" backend/open_webui/routers/invites.py` returns two matches (lines 211, 351)
- [x] `grep -n "await Auths.insert_new_auth" backend/open_webui/routers/invites.py` returns one match
- [x] `grep -n "await Files.get_file_by_id" backend/open_webui/routers/files.py` returns at least one match at or near line 158
- [x] `grep -n "await Knowledges.get_knowledge_by_id" backend/open_webui/services/sync/provider.py` returns one match
- [x] `grep -n "await Files.update_file_path_by_id" backend/open_webui/services/sync/base_worker.py` returns one match
- [x] `grep -rn "\bFiles\.get_file_by_id\b\|\bFiles\.update_file_path_by_id\b\|\bUsers\.get_user_by_email\b\|\bUsers\.get_user_by_id\b\|\bAuths\.insert_new_auth\b\|\bKnowledges\.get_knowledge_by_id\b" backend/open_webui/routers/invites.py backend/open_webui/routers/files.py backend/open_webui/services/sync/provider.py backend/open_webui/services/sync/base_worker.py` — every match prefixed with `await`
- [x] `npm run lint:backend` clean on the 4 touched files

#### Manual Verification

- [ ] Admin invites a new user — POST `/api/v1/invites` returns 201; Microsoft Graph email sent.
- [ ] Invite link opens — GET `/api/v1/invites/validate/{token}` returns valid invite with `invited_by` name.
- [ ] Invite acceptance creates a real user row; user can log in.
- [ ] Admin lists invites — `GET /api/v1/invites/` returns 200 with invited-by names populated.
- [ ] Upload a file via chat — socket.io `file-processing-complete` event fires with correct `collection_name`.
- [ ] Manual OneDrive sync for a connected KB — `execute_sync` runs end-to-end without `AttributeError` on `knowledge.meta`.
- [ ] File rename on OneDrive side is picked up by next sync — `Files.update_file_path_by_id` runs, new path persists.

**Implementation Note**: Pause for human confirmation before Phase 4.

---

## Phase 4: Sync Helpers → Async

### Overview

Three sync helper functions that call async model methods. Each flips
to `async def`, gets `await` at every internal model call, and gets
`await` prefixed at each call site in the already-async parent.

### Changes Required

#### 1. `backend/open_webui/routers/external_retrieval.py:195`

```python
# Before
def process_file_with_external_pipeline(
    request, file, file_path, collection_name, form_data,
    loader_instance, save_docs_to_vector_db_func, user,
) -> dict:
    ...
    Files.update_file_data_by_id(...)      # :280
    Files.update_file_hash_by_id(...)      # :285
    Files.update_file_metadata_by_id(...)  # :321
    Files.update_file_data_by_id(...)      # :328
    ...

# After
async def process_file_with_external_pipeline(
    request, file, file_path, collection_name, form_data,
    loader_instance, save_docs_to_vector_db_func, user,
) -> dict:
    ...
    await Files.update_file_data_by_id(...)
    await Files.update_file_hash_by_id(...)
    await Files.update_file_metadata_by_id(...)
    await Files.update_file_data_by_id(...)
    ...
```

Caller at `backend/open_webui/routers/retrieval.py:~1813` (inside
`async def process_file`):

```python
# Before
return process_file_with_external_pipeline(request=..., ...)

# After
return await process_file_with_external_pipeline(request=..., ...)
```

#### 2. `backend/open_webui/routers/configs.py:806-823`

```python
# Before
def _bind_service_account(user_id: str, provider_slug: str):
    user = Users.get_user_by_id(user_id)
    if not user:
        return
    info = dict(user.info) if user.info else {}
    info['integration_provider'] = provider_slug
    Users.update_user_by_id(user_id, {'info': info})

def _unbind_service_account(user_id: str):
    user = Users.get_user_by_id(user_id)
    if not user:
        return
    info = dict(user.info) if user.info else {}
    info.pop('integration_provider', None)
    Users.update_user_by_id(user_id, {'info': info})

# After
async def _bind_service_account(user_id: str, provider_slug: str):
    user = await Users.get_user_by_id(user_id)
    if not user:
        return
    info = dict(user.info) if user.info else {}
    info['integration_provider'] = provider_slug
    await Users.update_user_by_id(user_id, {'info': info})

async def _unbind_service_account(user_id: str):
    user = await Users.get_user_by_id(user_id)
    if not user:
        return
    info = dict(user.info) if user.info else {}
    info.pop('integration_provider', None)
    await Users.update_user_by_id(user_id, {'info': info})
```

Call sites inside `async def set_integrations_config` at
`configs.py:834`:

```python
# Before
_unbind_service_account(old_sa)  # :849
_bind_service_account(sa_id, slug)  # :858

# After
await _unbind_service_account(old_sa)
await _bind_service_account(sa_id, slug)
```

#### 3. `backend/open_webui/utils/external_agents.py`

```python
# Before
def register_external_agent_direct(agent_id, ...):
    existing_function = Functions.get_function_by_id(agent_id)     # :276
    if existing_function:
        Functions.update_function_by_id(agent_id, ...)              # :281
    else:
        Functions.insert_new_function(...)                          # :298
    ...
    Functions.update_function_by_id(agent_id, ...)                  # :302

# After
async def register_external_agent_direct(agent_id, ...):
    existing_function = await Functions.get_function_by_id(agent_id)
    if existing_function:
        await Functions.update_function_by_id(agent_id, ...)
    else:
        await Functions.insert_new_function(...)
    ...
    await Functions.update_function_by_id(agent_id, ...)
```

Caller: `load_external_agents_at_startup` in the same file — also
needs to become `async def`. Its caller in `main.py` (look for the
call inside `async def lifespan` around line 837) becomes
`await load_external_agents_at_startup(...)`.

Audit the caller chain in `utils/external_agents.py` — any other
synchronous functions that call `load_external_agents_at_startup`
need the same treatment. If there are only two functions in the
chain, the fix is a 4-line flip.

### Success Criteria

#### Automated Verification

- [x] `grep -n "^async def process_file_with_external_pipeline" backend/open_webui/routers/external_retrieval.py` returns one match
- [x] `grep -n "^async def _bind_service_account\|^async def _unbind_service_account" backend/open_webui/routers/configs.py` returns two matches
- [x] `grep -n "^async def register_external_agent_direct\|^async def load_external_agents_at_startup" backend/open_webui/utils/external_agents.py` returns two matches
- [x] `grep -n "await process_file_with_external_pipeline\|await _bind_service_account\|await _unbind_service_account\|await load_external_agents_at_startup" backend/open_webui/` — every call site prefixed with `await`
- [x] `grep -rn "\bFiles\.update_file_data_by_id\b\|\bFiles\.update_file_hash_by_id\b\|\bFiles\.update_file_metadata_by_id\b" backend/open_webui/routers/external_retrieval.py` — every match is prefixed with `await`
- [x] `grep -rn "\bUsers\.get_user_by_id\b\|\bUsers\.update_user_by_id\b" backend/open_webui/routers/configs.py` — every match inside `_bind_service_account`/`_unbind_service_account` is prefixed with `await`
- [x] `grep -rn "\bFunctions\." backend/open_webui/utils/external_agents.py` — every call prefixed with `await`
- [x] `python -W error::RuntimeWarning -c "from open_webui.routers.external_retrieval import process_file_with_external_pipeline; from open_webui.routers.configs import _bind_service_account; from open_webui.utils.external_agents import register_external_agent_direct; import inspect; assert inspect.iscoroutinefunction(process_file_with_external_pipeline); assert inspect.iscoroutinefunction(_bind_service_account); assert inspect.iscoroutinefunction(register_external_agent_direct)"` exits 0
- [x] `npm run lint:backend` clean on the 4 touched files

#### Manual Verification

- [ ] Upload a file to a KB with external pipeline enabled — file status flips to `completed` in DB; chunks land in vector DB; frontend spinner resolves.
- [ ] Admin saves integration providers config with a service-account ID — user row for that service account has `info.integration_provider` set. Unbind flow clears it.
- [ ] Configure an external agent in `agents_config`, restart backend — agent function is registered as a `Function` row; re-startup updates existing row.

**Implementation Note**: Pause for human confirmation before Phase 5.

---

## Phase 5: Knowledge Router Suspension Shim Cleanup

### Overview

Two sites in `routers/knowledge.py` still use the sync
`Knowledges.get_suspension_info` shim (which internally hops to a
`ThreadPoolExecutor` to call async code) instead of calling the
native async method directly. Both sites are inside `async def`
handlers — free to call `await Knowledges.async_get_suspension_info`.

### Changes Required

#### `backend/open_webui/routers/knowledge.py`

- Line ~434 inside the `async def get_knowledge_by_id` handler:
  ```python
  # Before
  suspension_info = Knowledges.get_suspension_info(id)

  # After
  suspension_info = await Knowledges.async_get_suspension_info(id)
  ```
- Line ~508 inside `async def update_knowledge_by_id`:
  ```python
  # Before
  suspension_info = Knowledges.get_suspension_info(id)

  # After
  suspension_info = await Knowledges.async_get_suspension_info(id)
  ```

Grep `routers/knowledge.py` for any other `get_suspension_info` call
sites — if found and inside an `async def`, flip the same way.

### Success Criteria

#### Automated Verification

- [x] `grep -n "Knowledges.get_suspension_info\b" backend/open_webui/routers/knowledge.py` returns zero matches
- [x] `grep -n "await Knowledges.async_get_suspension_info" backend/open_webui/routers/knowledge.py` returns at least two matches
- [x] `npm run lint:backend` clean

#### Manual Verification

- [ ] `GET /api/v1/knowledge/{id}` on a cloud KB returns `suspension_info` populated and without noticeable extra latency (no ThreadPoolExecutor hop)
- [ ] No regression: admin PATCH `/api/v1/knowledge/{id}` on a cloud KB still returns `suspension_info` in response

**Implementation Note**: Pause for human confirmation before Phase 6.

---

## Phase 6: Restore ModelEditor Feature-Flag Guards

### Overview

Four `{#if $config?.features?.feature_*}` guards dropped by upstream's
refactor of `ModelEditor.svelte`. Admin feature-toggles for
Knowledge, Tools, Skills, and Voice sections no longer hide the
corresponding form sections.

### Changes Required

#### `src/lib/components/workspace/Models/ModelEditor.svelte`

The store import is already present at line 5 (`import { config } from '$lib/stores'`).

Wrap each of the four current UI blocks in the guard from pre-merge.
Current line locations (post-merge):

- **Knowledge** section at `:784-786`:
  ```svelte
  {#if $config?.features?.feature_knowledge !== false}
      <div class="my-4"><Knowledge bind:selectedItems={knowledge} /></div>
  {/if}
  ```
- **Tools** section at `:788-790`:
  ```svelte
  {#if $config?.features?.feature_tools !== false}
      <div class="my-4"><ToolsSelector bind:selectedToolIds={toolIds} tools={$tools ?? []} /></div>
  {/if}
  ```
- **Skills** section at `:792-794`:
  ```svelte
  {#if $config?.features?.feature_skills}
      <div class="my-4"><SkillsSelector bind:selectedSkillIds={skillIds} /></div>
  {/if}
  ```
- **TTS Voice** section at `:871-883` (multi-line block; wrap the
  entire `<div class="my-4">...</div>` that contains the TTS voice
  input):
  ```svelte
  {#if $config?.features?.feature_voice !== false && $config?.audio?.tts?.engine}
      <div class="my-4">
          ... existing TTS voice UI ...
      </div>
  {/if}
  ```

Verify after edits: `BuiltinTools.svelte` already uses
`$config?.features?.feature_knowledge` (line 15) — the store shape is
correct. Reference: pre-merge `457f01af2:src/lib/components/workspace/Models/ModelEditor.svelte`
lines ~743, 747, 751, 819-831.

### Success Criteria

#### Automated Verification

- [x] `grep -n "feature_knowledge\b\|feature_tools\b\|feature_skills\b\|feature_voice\b" src/lib/components/workspace/Models/ModelEditor.svelte` returns at least four matches
- [x] `npm run lint:frontend` clean on the file (no new errors beyond pre-existing baseline)
- [x] `npm run build` succeeds
- [x] `npm run check` shows no NEW svelte-check errors on this file (pre-existing errors per CLAUDE.md OK)

#### Manual Verification

- [ ] Set `feature_knowledge: false` in features config → open model editor → Knowledge section is hidden
- [ ] Set `feature_tools: false` → Tools section hidden
- [ ] Set `feature_skills: false` (or unset) → Skills section hidden (skills is default-off)
- [ ] Set `feature_voice: false` OR unconfigure TTS engine → Voice section hidden
- [ ] Enable all four → all four sections render

**Implementation Note**: Final phase. On completion: update
`collab/world/state.md` marking both regression plans complete, and
propose a collab note capturing this round of audit + findings so the
next upstream merge gets a tighter regression checklist.

---

## Testing Strategy

### Unit Tests

Add where infrastructure already supports them; skip otherwise.
Candidates:

- `Files.update_file_path_by_id` updates `path` and `updated_at`; returns `None` for unknown id
- `Feedbacks.get_conversation_feedback_by_chat_id_and_user_id` orders by `created_at asc`; respects user+chat scoping
- Async `OneDriveTokenManager.has_stored_token` returns `True` only when a row exists; `False` after delete

### Integration / End-to-end smoke tests

Consolidated walk-through after Phase 6:

1. Backend clean-start, ≥5 min, no runtime warnings in logs.
2. **Invite lifecycle** — create → email received → validate → accept
   → user logs in → list invites.
3. **OneDrive flow** — connect → pick folder → sync → rename a file on
   OneDrive side → re-sync → `Files.update_file_path_by_id` persists
   new path.
4. **Google Drive flow** — connect → pick folder → sync.
5. **External pipeline** — upload file to a KB with external pipeline
   enabled → file status → `completed`.
6. **External agents** — restart backend → agents in `agents_config`
   register cleanly.
7. **Integration providers** — admin saves/updates integration
   providers config → service-account `info.integration_provider` is
   persisted.
8. **Conversation feedback** — open chat with feedback →
   `ConversationFeedback.svelte` shows feedback.
9. **Model editor** — toggle each of the four feature flags; confirm
   UI sections show/hide.

### Cypress E2E

Existing suites should continue to pass. No new Cypress tests for
this scope.

## Performance Considerations

- Phase 2's async flip replaces a broken `ThreadPoolExecutor` bridge
  (sync auth helpers calling async DB) with direct async — strictly
  faster, no new event-loop risk since DB driver is fully async.
- Phase 5's swap from `get_suspension_info` shim to
  `async_get_suspension_info` removes one thread-pool hop per KB
  fetch — meaningful on admin KB-list views with many cloud KBs.
- Phase 3/4 `await` additions convert no-op coroutine leaks into real
  DB round-trips. Latency increases but correctness is restored
  (previous silent no-ops meant the features were non-functional).

## Migration Notes

No DB migrations. All column / table state is already correct.

## References

- Research doc (original, incomplete): `thoughts/shared/research/2026-04-19-upstream-merge-260416-lost-customizations.md`
- Prerequisite plan: `thoughts/shared/plans/2026-04-19-async-soft-delete-surface.md`
- Sibling plan (different scope): `thoughts/shared/plans/2026-04-19-merge-260416-remaining-regressions.md`
- Upstream async DB refactor: commit `27169124f`
- Pre-merge fork HEAD (source of truth): commit `457f01af2`
- Merge commit: `5f6f1905d` on branch `merge/260416`
- Sync abstraction cookbook: `collab/docs/external-integration-cookbook.md`
- Upstream merge master plan: `collab/docs/upstream-merge-260416-plan.md`
