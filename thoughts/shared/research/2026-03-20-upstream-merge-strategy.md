---
date: 2026-03-20T14:00:00+01:00
researcher: Claude
git_commit: 773beeb7dbdd32b9beeaa25a10b8ea1231160f92
branch: merge/upstream-260320
repository: open-webui
topic: 'Upstream merge strategy: v0.6.43 → v0.8.9'
tags: [research, merge-strategy, upstream, git]
status: complete
last_updated: 2026-03-20
last_updated_by: Claude
---

# Research: Upstream Merge Strategy (v0.6.43 → v0.8.9)

**Date**: 2026-03-20
**Researcher**: Claude
**Git Commit**: 773beeb7d
**Branch**: merge/upstream-260320
**Repository**: open-webui (Gradient-DS fork)

## Research Question

How to merge 1126 upstream commits (v0.6.43 → v0.8.9) into the Gradient-DS fork which has 106 custom commits across 8 feature areas, without losing any custom work.

## Summary

- **Merge base**: `a7271532f8a` (v0.6.43, 2025-12-22)
- **Our changes**: 106 commits, 326 files changed (+45K/-5.6K lines)
- **Upstream changes**: 1126 commits, 639 files changed (+76K/-18K lines)
- **Actual merge conflicts**: **127 files** (verified via dry-run `git merge`)
- **CRITICAL**: Migration revision ID collision — our `a1b2c3d4e5f6` (soft delete) collides with upstream's `a1b2c3d4e5f6` (skill table)

### Conflict Breakdown

| Category                      | Conflict Files | Difficulty       |
| ----------------------------- | -------------- | ---------------- |
| Backend Python                | 27             | High             |
| Frontend Svelte/TS (non-i18n) | 41             | Medium-High      |
| i18n translation JSONs        | 57             | Low (mechanical) |
| Config/Other                  | 2              | Low              |

## Recommended Strategy: Phased Merge with Feature-Branch Replay

### Why NOT commit-by-commit or folder-by-folder

- **Commit-by-commit** (1126 commits): Impractical. Would take weeks and many upstream commits are interdependent.
- **Folder-by-folder**: Git merge doesn't work this way — a merge is atomic across the whole tree. You'd need cherry-picks which break history.

### Recommended Approach: Single Merge + Systematic Conflict Resolution

Do one `git merge upstream/main` and resolve the 127 conflicts systematically in phases. This preserves full history on both sides and is the standard approach for fork maintenance.

---

## Step-by-Step Merge Plan

### Pre-Work (Before Starting)

#### Step 0A: Fix Migration ID Collision

**CRITICAL — do this FIRST before any merge attempt.**

Our migration `a1b2c3d4e5f6_add_soft_delete_columns.py` has the same revision ID as upstream's `a1b2c3d4e5f6_add_skill_table.py`. This will break Alembic.

**Action**: On our branch (before merging), create a new commit that:

1. Renames our migration file to use a new unique ID (e.g., `b7c8d9e0f1a2_add_soft_delete_columns.py`)
2. Updates the `revision` inside the file
3. Updates any migration that references it as `down_revision`

#### Step 0B: Create a Safety Branch

```bash
git checkout main
git checkout -b main-backup-pre-merge  # Safety net
git checkout merge/upstream-260320     # Work branch
```

#### Step 0C: Understand Our 8 Custom Features

Document which files each feature touches (for conflict resolution reference):

| Feature               | Key Modified Upstream Files                                  |
| --------------------- | ------------------------------------------------------------ |
| **Typed KBs**         | `models/knowledge.py`, `routers/knowledge.py`                |
| **OneDrive**          | `main.py`, `config.py`, KB components                        |
| **Email Invites**     | `main.py`, `config.py`, `routers/configs.py`, `Users.svelte` |
| **GDPR Archival**     | `main.py`, `config.py`, `routers/users.py`                   |
| **Acceptance Modal**  | `(app)/+layout.svelte`, `admin/Settings.svelte`              |
| **Feature Flags**     | `Sidebar.svelte`, `MessageInput.svelte`, `config.py`         |
| **Feedback Config**   | `config.py`, evaluations components                          |
| **External Pipeline** | `routers/retrieval.py`, `config.py`                          |

---

### Phase 1: Start the Merge and Triage (Day 1)

```bash
git merge upstream/main
# This will report 127 conflicts
```

Immediately categorize the conflicts into resolution batches:

#### Batch 1A: Trivial / Auto-resolvable (57 files) — ~1 hour

**i18n translation files** — All 57 `translation.json` files. These are just key additions on both sides in alphabetical JSON. Resolution: accept both (upstream translations + our custom keys).

Strategy:

```bash
# For each i18n file: accept upstream version, then re-add our custom keys
git checkout --theirs src/lib/i18n/locales/*/translation.json
# Then manually add back our ~30 custom keys (OneDrive, Invites, Acceptance, etc.)
```

#### Batch 1B: Config Files (2 files) — ~30 min

- `package.json` / `package-lock.json` — Accept upstream versions, verify no custom deps needed

---

### Phase 2: Backend Core (Day 1-2) — ~27 files

Resolve in dependency order:

#### 2A: Foundation files first

1. **`backend/open_webui/config.py`** — Accept upstream changes, then re-add our config blocks (feature flags, OneDrive, email, acceptance, feedback, external pipeline, weaviate, integrations). Our additions are mostly appended blocks — low overlap risk.

2. **`backend/open_webui/env.py`** — Minor, just re-add `CLIENT_NAME`.

3. **`backend/open_webui/main.py`** — Most complex file. Accept upstream structure, then carefully re-add:
   - Router imports + mounts (archives, integrations, onedrive_sync, invites)
   - Background worker lifecycle (deletion cleanup, OneDrive scheduler, archive cleanup)
   - Config endpoint additions
   - OneDrive OAuth callback

#### 2B: Models (6 files)

- **`models/knowledge.py`** — Re-add `type` column, `deleted_at` column, `soft_delete_by_id` method on top of upstream's version
- `models/chats.py`, `models/files.py`, `models/channels.py`, `models/messages.py`, `models/prompts.py`, `models/tags.py` — Likely upstream-only changes; verify we have no modifications, accept theirs

#### 2C: Routers (12 files)

- **`routers/knowledge.py`** — HIGH RISK. Re-add type validation, non-local file operation blocking, soft delete. Upstream likely restructured this significantly (KB overhaul in v0.6.41-v0.8.x).
- **`routers/retrieval.py`** — Re-add external pipeline integration
- **`routers/users.py`** — Re-add archive-before-delete logic
- **`routers/configs.py`** — Re-add invite/email/integrations endpoints
- **`routers/auths.py`** — Re-add email validation
- Others (`audio.py`, `chats.py`, `files.py`, `models.py`, `ollama.py`, `openai.py`, `prompts.py`, `tools.py`) — Likely accept upstream, verify no custom changes

#### 2D: Other backend

- **`retrieval/vector/dbs/weaviate.py`** — BOTH sides modified this. Compare carefully.
- **`utils/middleware.py`**, **`utils/models.py`** — Check for custom modifications
- **`storage/provider.py`** — Likely accept upstream

---

### Phase 3: Frontend Core (Day 2-3) — ~41 non-i18n files

#### 3A: Stores and Utils (3 files)

- `src/lib/stores/index.ts` — Re-add any custom stores (acceptance modal state, feature flags)
- `src/lib/utils/index.ts` — Check for custom additions
- `src/lib/utils/marked/citation-extension.ts` — Re-add citation improvements

#### 3B: Admin components (10 files)

- **`admin/Settings.svelte`** — Re-add Acceptance/Email/Integrations tabs + feature flag visibility
- **`admin/Users.svelte`** — Re-add Invites tab
- **`admin/Users/UserList.svelte`** — Check for invite-related changes
- **`admin/Settings/Interface.svelte`**, `General.svelte`, `Database.svelte`, `Models.svelte` — Likely accept upstream
- **`admin/Settings/Integrations.svelte`** — CONFLICT: upstream now has their own Integrations component! Must merge our integration provider config with upstream's version
- **`admin/Evaluations/Feedbacks.svelte`** — Re-add feedback customization

#### 3C: Chat components (15 files)

- **`chat/MessageInput.svelte`** — Re-add `isFeatureEnabled('input_menu')` guard
- **`chat/Chat.svelte`** — Check for custom changes
- **`chat/Messages/RateComment.svelte`** — Re-add feedback tag customization
- Others — Mostly accept upstream, verify no custom logic

#### 3D: Knowledge/Workspace components (7 files)

- **`workspace/Knowledge.svelte`** — Re-add type filter UI
- **`workspace/Knowledge/CreateKnowledgeBase.svelte`** — Re-add type handling + OneDrive redirect
- **`workspace/Knowledge/KnowledgeBase.svelte`** — Re-add OneDrive sync integration
- Others — Check and merge

#### 3E: Layout and Routes (5 files)

- **`(app)/+layout.svelte`** — Re-add AcceptanceModal
- **`layout/Sidebar.svelte`** — May auto-merge (if upstream didn't touch our feature flag areas)
- Route layouts — Check for custom changes

#### 3F: API clients (2 files)

- `apis/knowledge/index.ts` — Re-add type parameter support
- `apis/evaluations/index.ts` — Re-add feedback config API calls

---

### Phase 4: Migrations (Day 3) — Critical

After resolving all code conflicts:

1. **Verify our migration chain still works:**
   - Our migrations: `f8e1a9c2d3b4` → `2c5f92a9fd66` → `eaa33ce2752e` → `<new-id>` (was `a1b2c3d4e5f6`)
   - Upstream's new migrations: `374d2f66af06`, `8452d01d26d7`, `a1b2c3d4e5f6` (skill), `b2c3d4e5f6a7` (scim), `f1e2d3c4b5a6` (access grant)

2. **Create a merge migration** that combines the upstream's latest head with our latest head, similar to what `f8e1a9c2d3b4` already does.

3. **Test the migration chain**: Run `alembic upgrade head` on a fresh database and on a database at our current state.

---

### Phase 5: Verification (Day 3-4)

1. **Build test**: `npm run build` — Must compile without errors
2. **Backend test**: `open-webui dev` — Must start without import errors
3. **Migration test**: Fresh DB `alembic upgrade head` + existing DB upgrade
4. **Smoke test**: Manual walkthrough of all 8 custom features
5. **Upstream features**: Verify new upstream features (Skills, SCIM, Access Grants, Prompt History) work

---

## Key Risks and Mitigations

| Risk                                           | Impact                                 | Mitigation                                                |
| ---------------------------------------------- | -------------------------------------- | --------------------------------------------------------- |
| Migration ID collision (`a1b2c3d4e5f6`)        | Alembic breaks completely              | Fix BEFORE merge (Step 0A)                                |
| `config.py` is 121KB                           | Easy to miss additions                 | Diff our additions separately, re-apply as blocks         |
| Knowledge router restructured upstream         | Our type/soft-delete logic may not fit | Read upstream's new knowledge router fully before merging |
| Upstream added their own `Integrations.svelte` | Name collision with our component      | May need to rename ours or merge functionality            |
| 57 i18n files                                  | Tedious but not hard                   | Script the merge: accept theirs + append our keys         |

## Time Estimate

| Phase                                   | Estimated Effort      |
| --------------------------------------- | --------------------- |
| Pre-work (migration fix, safety branch) | 1-2 hours             |
| Phase 1: Trivial (i18n + config)        | 1-2 hours             |
| Phase 2: Backend (27 files)             | 4-6 hours             |
| Phase 3: Frontend (41 files)            | 4-6 hours             |
| Phase 4: Migrations                     | 2-3 hours             |
| Phase 5: Verification                   | 2-3 hours             |
| **Total**                               | **~2-3 working days** |

## Code References

- Merge base: `a7271532f8a` (v0.6.43)
- Upstream HEAD: `e4e69a10ec08` (v0.8.9)
- Our migration collision: `backend/open_webui/migrations/versions/a1b2c3d4e5f6_add_soft_delete_columns.py`
- Upstream collision: `backend/open_webui/migrations/versions/a1b2c3d4e5f6_add_skill_table.py`
- Our custom feature files (new, won't conflict): `backend/open_webui/routers/invites.py`, `archives.py`, `onedrive_sync.py`, `integrations.py`, `external_retrieval.py`
- Our custom services (new): `backend/open_webui/services/onedrive/`, `deletion/`, `archival/`, `email/`

## Open Questions

1. **Upstream Integrations.svelte**: Does upstream's new `Integrations.svelte` overlap with our integration provider config? Need to read upstream's version.
2. **Knowledge model changes**: Upstream may have restructured knowledge significantly (v0.6.41+ added `knowledge_file` table). Need to verify our `type` column still makes sense.
3. **Weaviate adapter**: Both sides modified `weaviate.py`. Need to compare implementations.
4. **Feature flags**: Upstream may have added their own feature flag system. Check for overlap with our `utils/features.py`.
