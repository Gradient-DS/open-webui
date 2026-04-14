# Upstream Merge Plan: v0.6.43 → v0.8.9

## Overview

Merge 1126 upstream open-webui commits (v0.6.43 → v0.8.9) into the Gradient-DS fork while preserving all 8 custom features (106 commits). The merge produces 127 conflicting files. This plan provides a phased, agent-assisted workflow where the main operator executes the merge and pauses at each conflict group for a dedicated research+resolution agent.

## Current State Analysis

**Fork base**: v0.6.43 (`a7271532f8a`, 2025-12-22)
**Upstream HEAD**: v0.8.9 (`e4e69a10ec08`)
**Our commits**: 106 (326 files, +45K/-5.6K)
**Upstream commits**: 1126 (639 files, +76K/-18K)
**Conflict files**: 127 (verified via dry-run merge)

### Our 8 Custom Features

| Feature                       | New Files                                    | Modified Upstream Files                                      |
| ----------------------------- | -------------------------------------------- | ------------------------------------------------------------ |
| Typed Knowledge Bases         | 1 migration, TypeSelector                    | `models/knowledge.py`, `routers/knowledge.py`                |
| OneDrive Integration          | 7 services, 3 components, 1 router, 1 API    | `main.py`, `config.py`, KB components                        |
| Email Invite System           | 1 model, 1 router, 1 migration, 2 components | `main.py`, `config.py`, `routers/configs.py`, `Users.svelte` |
| GDPR User Archival/Deletion   | 1 model, 1 router, 3 services, 2 migrations  | `main.py`, `config.py`, `routers/users.py`                   |
| Acceptance Modal              | 1 component, 1 admin setting                 | `(app)/+layout.svelte`, `admin/Settings.svelte`              |
| Feature Flag System           | 2 utils (FE+BE), tests                       | `Sidebar.svelte`, `MessageInput.svelte`                      |
| Configurable Feedback         | —                                            | `config.py`, evaluations components                          |
| External Pipeline Integration | 1 router module                              | `routers/retrieval.py`, `config.py`                          |

### Migration Chain Situation

**Our chain** (branching from upstream's `c440947495f3` + `018012973d35`):

```
c440947495f3 ─┐
               ├─> f8e1a9c2d3b4 → 2c5f92a9fd66 → eaa33ce2752e → a1b2c3d4e5f6 (COLLISION!)
018012973d35 ─┘     (archive)      (kb type)       (invite)        (soft delete)
```

**Upstream's new chain** (branching from `c440947495f3`):

```
c440947495f3 → 374d2f66af06 → 8452d01d26d7 → f1e2d3c4b5a6 → a1b2c3d4e5f6 → b2c3d4e5f6a7
                (prompt hist)   (chat msg)     (access grant)   (skill table)   (scim column)
```

**CRITICAL**: Both sides have revision `a1b2c3d4e5f6` — ours is "soft delete columns", upstream's is "skill table". This MUST be fixed before merging.

## Desired End State

- All upstream v0.8.9 code merged into our branch
- All 8 custom features fully functional
- Alembic migration chain clean with single head
- `npm run build` passes
- `open-webui dev` starts without errors
- All custom features manually verified

## What We're NOT Doing

- Rebasing our commits on top of upstream (too complex with 106 commits)
- Cherry-picking upstream commits one by one (1126 commits, impractical)
- Dropping or rewriting any of our custom features
- Upgrading beyond v0.8.9 in this merge

## Implementation Approach

Single `git merge upstream/main`, then systematic conflict resolution in 8 phases. Each phase groups related conflict files. The operator (you) pauses at each phase to spawn a dedicated research+resolution agent that:

1. Reads upstream's version of each conflicted file
2. Reads our version and understands the custom feature intent
3. Proposes a resolution that keeps both sides
4. You review and approve

---

## Phase 0: Pre-Merge Preparation (before `git merge`)

### Overview

Fix the migration ID collision and create safety branches. This MUST happen before starting the merge.

### Step 0A: Fix Migration Revision ID Collision

**Problem**: Our `a1b2c3d4e5f6_add_soft_delete_columns.py` collides with upstream's `a1b2c3d4e5f6_add_skill_table.py`.

**Action**: Rename our migration to use a new unique revision ID.

**File**: `backend/open_webui/migrations/versions/a1b2c3d4e5f6_add_soft_delete_columns.py`

1. Rename file to `d4e5f6a7b8c9_add_soft_delete_columns.py`
2. Change `revision = "a1b2c3d4e5f6"` → `revision = "d4e5f6a7b8c9"`
3. No other migration references `a1b2c3d4e5f6` as `down_revision` (it's our chain head), so no other files need updating

Commit this change on the current branch before proceeding.

### Step 0B: Create Safety Branch

```bash
git checkout main
git checkout -b main-backup-pre-merge  # Safety net — never delete this
git checkout merge/upstream-260320      # Work branch
```

### Step 0C: Verify Upstream is Fetched

```bash
git fetch upstream main
```

### Success Criteria:

#### Automated Verification:

- [x] `grep -r 'a1b2c3d4e5f6' backend/open_webui/migrations/` returns ZERO results on our branch
- [x] New migration file exists with new revision ID
- [x] `git log --oneline -1` shows the fix commit
- [x] Safety branch `main-backup-pre-merge` exists

---

## Phase 1: Start Merge + i18n Translations (57 files)

### Overview

Start the merge and immediately resolve the easiest batch: all 57 i18n translation JSON files. These are purely additive on both sides (new translation keys).

### Step 1A: Start the Merge

```bash
git merge upstream/main
# Will report 127 conflicts — this is expected
```

### Step 1B: Resolve i18n Files

**Files**: All 57 `src/lib/i18n/locales/*/translation.json`

**Strategy**: Accept upstream's version for all non-English locales (our custom keys only exist in en-US). For en-US, accept upstream and re-add our ~30 custom keys.

```bash
# Accept upstream for all non-English i18n files
git checkout --theirs src/lib/i18n/locales/ar-BH/translation.json
git checkout --theirs src/lib/i18n/locales/ar/translation.json
# ... (all non-en-US locales)
git add src/lib/i18n/locales/*/translation.json

# For en-US: manually merge — accept upstream, add our keys back
```

**Our custom i18n keys to preserve** (in `en-US/translation.json`):

- OneDrive-related: `Sync from OneDrive`, `OneDrive Sources`, `Add OneDrive Source`, etc.
- Invite-related: `Invite User`, `Send Invite`, `Pending Invites`, `Invite Link`, etc.
- Acceptance modal: `Accept Terms`, `Terms and Conditions`, etc.
- KB types: `Knowledge Base Type`, `Local`, `OneDrive`, etc.
- Feedback: `Positive Tags`, `Negative Tags`, etc.

**Agent task description**: "Resolve en-US/translation.json merge conflict: accept upstream version, then re-add our custom i18n keys (OneDrive, Invites, Acceptance, KB types, Feedback). Read both versions, output the merged file."

### Success Criteria:

#### Automated Verification:

- [x] `git diff --name-only --diff-filter=U | grep i18n | wc -l` returns 0
- [x] All i18n JSON files are valid JSON: `for f in src/lib/i18n/locales/*/translation.json; do python3 -c "import json; json.load(open('$f'))" || echo "INVALID: $f"; done`

---

## Phase 2: Package Files + Trivial Config (4 files)

### Overview

Resolve `package.json`, `package-lock.json`, `Dockerfile`, `.github/pull_request_template.md`. Accept upstream versions, verify no custom dependencies are lost.

**Files**:

- `package.json` — accept upstream, verify version bump is fine
- `package-lock.json` — accept upstream (will regenerate anyway)
- `.github/pull_request_template.md` — accept ours (we have a custom template)
- `.github/workflows/deploy-to-hf-spaces.yml.disabled` — delete (upstream deleted the original)

**Agent task description**: "Resolve package.json conflict: compare our version vs upstream, check if we added any custom dependencies. Accept upstream version if no custom deps."

### Success Criteria:

#### Automated Verification:

- [x] `git diff --name-only --diff-filter=U | grep -E 'package|Dockerfile|github' | wc -l` returns 0
- [x] `cat package.json | python3 -c "import json,sys; json.load(sys.stdin)"` succeeds

---

## Phase 3: Backend Models (6 files)

### Overview

Resolve conflicts in SQLAlchemy model files. Most are upstream-only changes where we have no modifications. The exception is `models/knowledge.py` where we added `type` and `deleted_at` columns.

**Files**:
| File | Our changes | Risk |
|------|-------------|------|
| `models/knowledge.py` | Added `type` column, `deleted_at`, `soft_delete_by_id` | HIGH |
| `models/chats.py` | None (upstream only) | LOW |
| `models/files.py` | None | LOW |
| `models/channels.py` | None | LOW |
| `models/messages.py` | None | LOW |
| `models/prompts.py` | None | LOW |
| `models/tags.py` | None | LOW |
| `models/feedbacks.py` | None | LOW |

**Strategy for LOW risk**: `git checkout --theirs <file> && git add <file>`

**Agent task description for knowledge.py**: "Resolve `models/knowledge.py` merge conflict. Our additions: `type` column (Text, default 'local'), `deleted_at` column (BigInteger), `soft_delete_by_id` method. Read upstream's new version to understand their schema changes, then re-add our columns and methods on top of their version."

### Success Criteria:

#### Automated Verification:

- [x] `git diff --name-only --diff-filter=U | grep models/ | wc -l` returns 0
- [x] `python3 -c "from open_webui.models.knowledge import *"` succeeds (syntax check)

---

## Phase 4: Backend Foundation — config.py, env.py, main.py (3 files)

### Overview

The three most complex backend files. These are the backbone of our customizations — every feature adds imports, config variables, and router mounts here.

**Files**:
| File | Size | Our additions |
|------|------|---------------|
| `config.py` | ~121KB | ~200 lines: feature flags, OneDrive, email, acceptance, feedback, external pipeline, weaviate, integrations |
| `main.py` | Large | Router mounts (4), background workers (3), config endpoint extensions, OAuth callback |
| `env.py` | Small | `CLIENT_NAME` env var |

**Agent task description for config.py**: "Resolve `config.py` merge conflict. This is a ~121KB config file. Accept upstream's version as the base. Then re-add our custom config blocks. I'll provide the list of our additions — search for them in the ours version and add them to the appropriate locations in the upstream version. Our additions: FEATURE*\* flags, ACCEPTANCE_MODAL*\_, FEEDBACK\__, ARCHIVE*\*, ONEDRIVE*_, EMAIL\__, EXTERNAL*PIPELINE*_, INTEGRATION*PROVIDERS, WEAVIATE*\_."

**Agent task description for main.py**: "Resolve `main.py` merge conflict. Accept upstream's version as the structure base. Re-add: (1) imports for archives, integrations, onedrive_sync, invites routers; (2) app.include_router mounts for these 4 routers; (3) background worker start/stop in lifespan for deletion cleanup, OneDrive scheduler, archive cleanup; (4) config endpoint extensions for feature flags, OneDrive, email, acceptance, integrations; (5) OneDrive OAuth callback handler."

### Success Criteria:

#### Automated Verification:

- [x] `git diff --name-only --diff-filter=U | grep -E 'config\.py|env\.py|main\.py' | wc -l` returns 0
- [x] `python3 -c "import ast; ast.parse(...)"` config.py syntax OK
- [x] `python3 -c "import ast; ast.parse(...)"` main.py syntax OK

---

## Phase 5: Backend Routers (12 files)

### Overview

Resolve router conflicts. 4-5 routers have significant custom changes; the rest are upstream-only.

**Files with our changes (HIGH priority)**:
| File | Our changes |
|------|-------------|
| `routers/knowledge.py` | Type validation on create, non-local file op blocking, soft delete, DeletionService |
| `routers/retrieval.py` | External pipeline integration (imports + fallback in process_file) |
| `routers/users.py` | Archive-before-delete with DeletionService |
| `routers/configs.py` | Email, invite content, integrations GET/POST endpoints |
| `routers/auths.py` | Email validation utility |

**Files with upstream-only changes (accept theirs)**:
`routers/audio.py`, `routers/chats.py`, `routers/files.py`, `routers/models.py`, `routers/ollama.py`, `routers/openai.py`, `routers/prompts.py`, `routers/tools.py`, `routers/utils.py`

**Agent task description for knowledge.py**: "Resolve `routers/knowledge.py` merge conflict. IMPORTANT: upstream significantly overhauled the knowledge system (added knowledge_file table, pagination, server-side search). Accept upstream's structural changes. Then re-add our customizations: (1) type validation in create endpoint — validate type against {'local', 'onedrive'} | integration_providers; (2) block file upload/delete operations for non-local KBs; (3) use soft_delete_by_id instead of hard delete; (4) import DeletionService."

**Agent task description for retrieval.py**: "Resolve `routers/retrieval.py` conflict. Accept upstream base. Re-add: (1) import of external_retrieval functions; (2) external pipeline fallback in process_file flow."

### Success Criteria:

#### Automated Verification:

- [x] `git diff --name-only --diff-filter=U | grep routers/ | wc -l` returns 0
- [x] `python3 -c "from open_webui.routers import knowledge, retrieval, users, configs"` succeeds

---

## Phase 6: Other Backend Files (4 files)

### Overview

Remaining backend conflicts: weaviate adapter, middleware, utils, storage.

**Files**:
| File | Situation |
|------|-----------|
| `retrieval/vector/dbs/weaviate.py` | BOTH sides modified — need careful comparison |
| `utils/middleware.py` | Likely upstream only |
| `utils/models.py` | Likely upstream only |
| `storage/provider.py` | Likely upstream only |
| `retrieval/utils.py` | Likely upstream only |

**Agent task description for weaviate.py**: "Resolve `retrieval/vector/dbs/weaviate.py` conflict. Both sides modified this file. Read both versions completely. Our version adds Weaviate support as a vector DB adapter. Upstream may have also added/updated Weaviate support. Merge the two implementations, preferring upstream's API patterns but keeping our Weaviate-specific config (WEAVIATE_HTTP_HOST, WEAVIATE_GRPC_PORT, WEAVIATE_API_KEY, WEAVIATE_WEB_SEARCH_TTL_MINUTES)."

### Success Criteria:

#### Automated Verification:

- [x] `git diff --name-only --diff-filter=U | grep backend/ | wc -l` returns 0
- [x] All backend imports resolve

---

## Phase 7: Frontend — Admin, Chat, Knowledge, Layout (~41 files)

### Overview

All non-i18n frontend conflicts. Split into sub-batches for agent focus.

### Sub-batch 7A: Stores, Utils, APIs (5 files)

| File                                 | Our changes                                       |
| ------------------------------------ | ------------------------------------------------- |
| `stores/index.ts`                    | Custom stores for acceptance modal, feature flags |
| `utils/index.ts`                     | Possibly custom additions                         |
| `utils/marked/citation-extension.ts` | Japanese brackets, bold/italic citation fix       |
| `apis/knowledge/index.ts`            | Type parameter support                            |
| `apis/evaluations/index.ts`          | Feedback config API                               |
| `apis/configs/index.ts`              | Email, invite, integrations API functions         |

### Sub-batch 7B: Admin Components (10 files)

| File                                       | Our changes                                                | Risk   |
| ------------------------------------------ | ---------------------------------------------------------- | ------ |
| `admin/Settings.svelte`                    | New tabs (Acceptance, Email, Integrations) + feature flags | HIGH   |
| `admin/Settings/Integrations.svelte`       | **NAME COLLISION** — upstream now has their own!           | HIGH   |
| `admin/Users.svelte`                       | Invites tab                                                | MEDIUM |
| `admin/Users/UserList.svelte`              | Invite-related changes                                     | LOW    |
| `admin/Evaluations/Feedbacks.svelte`       | Feedback tag customization                                 | MEDIUM |
| `admin/Settings/Interface.svelte`          | Likely upstream only                                       | LOW    |
| `admin/Settings/General.svelte`            | Likely upstream only                                       | LOW    |
| `admin/Settings/Database.svelte`           | Likely upstream only                                       | LOW    |
| `admin/Settings/Models.svelte`             | Likely upstream only                                       | LOW    |
| `admin/Users/UserList/AddUserModal.svelte` | Likely upstream only                                       | LOW    |

**NOTE on Integrations.svelte**: Upstream added their own `admin/Settings/Integrations.svelte`. We also have one with our integration provider config. The agent must compare both and merge them, or rename ours.

### Sub-batch 7C: Chat Components (15 files)

| File                                                         | Our changes                            | Risk   |
| ------------------------------------------------------------ | -------------------------------------- | ------ |
| `chat/MessageInput.svelte`                                   | `isFeatureEnabled('input_menu')` guard | MEDIUM |
| `chat/Messages/RateComment.svelte`                           | Feedback tag customization             | MEDIUM |
| `chat/Chat.svelte`, `ChatControls.svelte`, `Controls.svelte` | Check for custom changes               | LOW    |
| `chat/SettingsModal.svelte`, `Settings/General.svelte`       | Check for custom changes               | LOW    |
| Others (CodeBlock, ContentRenderer, Citations, etc.)         | Likely upstream only                   | LOW    |

### Sub-batch 7D: Knowledge/Workspace (7 files)

| File                                                                                                                              | Our changes                                             | Risk |
| --------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------- | ---- |
| `workspace/Knowledge.svelte`                                                                                                      | Type filter UI, type badges                             | HIGH |
| `workspace/Knowledge/CreateKnowledgeBase.svelte`                                                                                  | Type handling, OneDrive redirect, access control hiding | HIGH |
| `workspace/Knowledge/KnowledgeBase.svelte`                                                                                        | OneDrive sync UI, empty state cards                     | HIGH |
| `workspace/Models.svelte`, `Models/Capabilities.svelte`, `Models/ModelEditor.svelte`, `Models/Knowledge/KnowledgeSelector.svelte` | Likely upstream only                                    | LOW  |

### Sub-batch 7E: Layout & Routes (7 files)

| File                                                  | Our changes                    | Risk   |
| ----------------------------------------------------- | ------------------------------ | ------ |
| `(app)/+layout.svelte`                                | AcceptanceModal import + logic | MEDIUM |
| `layout/Sidebar.svelte`                               | `isFeatureEnabled()` guards    | MEDIUM |
| `layout/Navbar/Menu.svelte`                           | Check for changes              | LOW    |
| Route layouts (`admin/`, `workspace/`, `playground/`) | Check for changes              | LOW    |
| `routes/auth/+page.svelte`                            | Check for changes              | LOW    |

**Agent task descriptions** — spawn one agent per sub-batch:

- "Resolve admin component conflicts (7B). Accept upstream for LOW-risk files. For Settings.svelte: re-add Acceptance/Email/Integrations tabs. For Integrations.svelte: compare upstream's version with ours and merge. For Users.svelte: re-add Invites tab."
- "Resolve chat component conflicts (7C). Accept upstream for most files. Re-add isFeatureEnabled guard in MessageInput.svelte. Re-add feedback tag config in RateComment.svelte."
- "Resolve Knowledge/workspace conflicts (7D). Accept upstream's structural changes. Re-add: type filter in Knowledge.svelte, type handling in CreateKnowledgeBase.svelte, OneDrive sync in KnowledgeBase.svelte."

### Success Criteria:

#### Automated Verification:

- [x] `git diff --name-only --diff-filter=U | grep src/ | wc -l` returns 0
- [x] `npm run build` compiles successfully

---

## Phase 8: Migrations — Rewire Chain (post-conflict)

### Overview

After all file conflicts are resolved, fix the Alembic migration chain so both our custom migrations and upstream's new migrations coexist.

### Current State After Merge

**Upstream's new migrations** (not in our fork before merge):

```
c440947495f3 → 374d2f66af06 → 8452d01d26d7 → f1e2d3c4b5a6 → a1b2c3d4e5f6 → b2c3d4e5f6a7
                (prompt hist)   (chat msg)     (access grant)   (skill table)   (scim column)
```

**Our custom migrations**:

```
c440947495f3 ─┐
               ├─> f8e1a9c2d3b4 → 2c5f92a9fd66 → eaa33ce2752e → d4e5f6a7b8c9
018012973d35 ─┘     (archive)      (kb type)       (invite)        (soft delete, renamed)
```

### Action: Create a Merge Migration

Create a new migration that merges both heads:

**File**: `backend/open_webui/migrations/versions/merge_upstream_v089.py`

```python
"""merge upstream v0.8.9 with custom migrations

Revision ID: <generate-new-id>
Revises: ('b2c3d4e5f6a7', 'd4e5f6a7b8c9')
Create Date: 2026-03-20
"""

revision = "<generate-new-id>"
down_revision = ("b2c3d4e5f6a7", "d4e5f6a7b8c9")
branch_labels = None
depends_on = None

def upgrade():
    pass  # Both branches already applied their changes

def downgrade():
    pass
```

### Verify Migration Chain

```bash
# On a fresh database
alembic upgrade head

# On existing database (at our current head d4e5f6a7b8c9)
alembic upgrade head
```

### Success Criteria:

#### Automated Verification:

- [x] `alembic heads` returns exactly 1 head
- [ ] `alembic upgrade head` succeeds on fresh DB
- [ ] `alembic history --verbose` shows clean linear chain to merge point

---

## Phase 9: Verification & Finalization

### Overview

Full verification that the merge is complete and everything works.

### Steps

1. **No remaining conflicts**: `git diff --name-only --diff-filter=U` returns nothing
2. **Build**: `npm run build` passes
3. **Backend**: `open-webui dev` starts without import errors
4. **Migrations**: `alembic upgrade head` on fresh + existing DB

### Manual Verification (Smoke Tests):

- [ ] Login works
- [ ] Chat with a model works
- [ ] Knowledge base creation (local type) works
- [ ] Knowledge base creation (OneDrive type) shows sync UI
- [ ] Admin settings shows all custom tabs (Acceptance, Email, Integrations)
- [ ] Feature flags hide/show sidebar items correctly
- [ ] Invite system sends emails
- [ ] Feedback tags appear in chat
- [ ] External pipeline integration config accessible
- [ ] New upstream features (Skills, SCIM, Access Grants) accessible

### Final Steps

```bash
git add -A
git commit -m "Merge upstream open-webui v0.8.9 into Gradient-DS fork"
```

---

## References

- Research document: `thoughts/shared/research/2026-03-20-upstream-merge-strategy.md`
- Merge base: `a7271532f8a` (v0.6.43)
- Upstream HEAD: `e4e69a10ec08` (v0.8.9)
- Migration collision: our `a1b2c3d4e5f6` vs upstream's `a1b2c3d4e5f6`
