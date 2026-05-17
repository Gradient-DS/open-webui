---
date: 2026-05-04T14:44:21Z
researcher: Claude Opus 4.7 (with @lexlubbers)
git_commit: 077331e5d5d8eb4fe186feba4c8bfe7e880a0569
branch: feat/octobox-agent
repository: Gradient-DS/open-webui
topic: "Fork divergence from upstream Open WebUI — strategy, scoping, and high-value upstream changes (incl. async DB)"
tags: [research, upstream-merge, fork-strategy, async-db, divergence, merge-policy]
status: complete
last_updated: 2026-05-04
last_updated_by: Claude Opus 4.7
---

# Fork Divergence Strategy: How to Stop Drowning in Upstream Merges

## Research questions

@lexlubbers asked three things, then a fourth follow-up:

1. **Web research:** What proven strategies exist for managing a fork that has heavily diverged from upstream?
2. **Measurement:** How far have we actually diverged from `open-webui/open-webui:main`?
3. **Distribution:** What modules should we keep merging vs freeze, while still tracking the upstream version number?
4. **Follow-up:** What are the *big upstream changes* that might be worth taking despite conflicts? Specifically, the **sync→async DB migration** — what would that bring us?

---

## TL;DR

- **Divergence numbers (since merge base `9bd84258`, last successful merge at v0.8.12, 2026-03-27):**
  - 339 commits ahead, **410 commits behind**.
  - Our side: 546 files, +102,236 / −6,005 lines.
  - Upstream side: 319 files, +35,546 / −11,936 lines.
  - 150 files touched by both. **Excluding 60 i18n files, ~90 truly contested. Of those, only ~12 files are heavily bilateral** (>200 lines on both sides).
  - **396 of our 546 files (73%) are pure-custom — they cannot conflict.**
  - **169 of upstream's 319 files (53%) are pure-upstream — they merge automatically.**
- **Real merge surface is concentrated in ~12 files** (`main.py`, `models/chats.py`, `models/knowledge.py`, `models/users.py`, `utils/middleware.py`, `routers/{knowledge,retrieval,auths,users}.py`, `components/chat/Chat.svelte`, `components/layout/Sidebar.svelte`, plus `MessageInput/InputMenu.svelte` which is mostly ours). The "fork is unmergeable" feeling is largely caused by this dozen-file core, **plus the new dimension introduced by upstream's async-DB refactor** (see §5).
- **The async DB refactor (`27169124f`, Apr 12 2026)** is a single commit that rewrote 74 files (+4829 / −4477), including every model file and most routers. **It is the dominant new merge driver in the v0.8.12 → v0.9.2 release.** It cannot be merged incrementally (model layer is fully migrated, no sync shim).
- **Recommended strategy:** GitLab CE/EE-style **namespaced separation** as the long-term direction (extension layer in `services/`, custom routers, custom Settings panels — already 73% of our diff lives outside upstream files). Combined with a **path-scoped merge policy**: freeze a documented list of modules (helm, services/, custom routers, our admin Settings panels) and continue merging the rest. Adopt **`git rerere` + Mergiraf** for the next merge to capture and replay conflict resolutions.
- **Async DB verdict:** **Worth absorbing, not for performance — for fork sustainability.** Performance gains for our workload (LLM streaming, vector DB, Graph API) will be in the noise; the database is rarely the gate. But staying sync while upstream goes async makes 74 files diverge permanently, and every future release will compound the cost. Plan: **2-week migration**, await-ify ~215 model call sites in our custom code, keep our `BaseSyncWorker` and friends on a sync session factory (two engines, same DB).

---

## 1. Divergence by the numbers

| Metric | Value |
|---|---|
| Last upstream merge | `9bd84258` (2026-03-27, at v0.8.12) |
| Upstream now | `8dae237a` (2026-04-24, v0.9.2) |
| Time elapsed | ~5 weeks |
| Our commits ahead | 339 |
| Upstream commits behind | 410 |
| Our files changed | 546 (+102,236 / −6,005) |
| Upstream files changed | 319 (+35,546 / −11,936) |
| Both-touched files | 150 (60 are i18n) |

### Per-directory heat map (top 2nd-level dirs by combined volume)

| Directory | Our F | Our +/− | Up F | Up +/− | Overlap | Heat |
|---|---:|---:|---:|---:|---:|---|
| `src/lib/i18n/` | 60 | 19,967 / 2,635 | 62 | 11,568 / 2,767 | 60 | **HOT** (mechanical) |
| `thoughts/shared/` | 90 | 35,849 / 0 | 0 | 0 / 0 | 0 | CUSTOM |
| `src/lib/components/` | 96 | 11,958 / 2,224 | 94 | 6,372 / 2,186 | 31 | **HOT** |
| `backend/open_webui/services/` | 43 | 7,103 / 0 | 0 | 0 / 0 | 0 | CUSTOM |
| `backend/open_webui/routers/` | 21 | 4,261 / 119 | 30 | 3,722 / 2,344 | 11 | **HOT** |
| `backend/open_webui/models/` | 15 | 1,097 / 38 | 25 | 4,654 / 2,696 | 11 | **HOT** (upstream-dominated) |
| `backend/open_webui/utils/` | 14 | 1,433 / 57 | 34 | 3,056 / 676 | 9 | **HOT** (upstream-dominated) |
| `helm/open-webui-tenant/` | 22 | 2,283 / 0 | 0 | 0 / 0 | 0 | CUSTOM |
| `backend/open_webui/retrieval/` | 17 | 125 / 64 | 10 | 752 / 147 | 3 | UPSTREAM |
| `backend/open_webui/tools/` | 1 | 39 / 0 | 1 | 1,106 / 125 | 1 | **UPSTREAM** (take wholesale + reapply 39 lines) |

### Top non-i18n conflict-zone files (combined edit volume)

| File | Ours +/− | Upstream +/− | Total |
|---|---:|---:|---:|
| `backend/open_webui/main.py` | 601/81 | 474/265 | 1,421 |
| `backend/open_webui/models/chats.py` | 126/15 | 556/487 | 1,184 |
| `src/lib/components/chat/MessageInput/InputMenu.svelte` | 819/268 | 58/0 | 1,145 |
| `src/lib/components/admin/Evaluations/Feedbacks.svelte` | 320/63 | 377/225 | 985 |
| `src/lib/components/chat/Chat.svelte` | 318/4 | 354/258 | 934 |
| `backend/open_webui/utils/middleware.py` | 103/9 | 522/140 | 774 |
| `src/lib/components/chat/MessageInput.svelte` | 505/147 | 78/6 | 736 |
| `src/lib/components/layout/Sidebar.svelte` | 178/39 | 309/115 | 641 |
| `backend/open_webui/models/knowledge.py` | 246/20 | 189/168 | 623 |
| `backend/open_webui/config.py` | 454/4 | 156/9 | 623 |
| `backend/open_webui/utils/oauth.py` | 8/10 | 402/119 | 539 |
| `backend/open_webui/routers/chats.py` | 10/4 | 340/177 | 531 |
| `backend/open_webui/routers/knowledge.py` | 218/54 | 114/116 | 502 |
| `backend/open_webui/routers/retrieval.py` | 244/27 | 126/102 | 499 |

**Key observation:** several of these are *not* truly bilateral. `MessageInput.svelte` and `MessageInput/InputMenu.svelte` are effectively ours — upstream barely touched them. Conversely, `oauth.py` and `routers/chats.py` are upstream-dominated — easy "take upstream, lose nothing." The middle tier (`main.py`, `models/chats.py`, `models/knowledge.py`, `utils/middleware.py`) is where 80% of merge time is spent.

---

## 2. Strategy catalog (web research)

Eight named patterns exist for forks like ours. The full table:

| Strategy | Fit for us | Notes |
|---|---|---|
| **Vendor branch / subtree merge** | Poor | Customizations are scattered, not relocatable to a subdirectory. |
| **Patch queue (Quilt, StGit, TopGit, Git Patch Stack)** | Poor | Requires re-flattening 339 commits to atomic patches first. Heavy tooling. |
| **Plugin/extension architecture (GitLab CE/EE pattern)** | **Best long-term** | We already partially do this (services/, custom routers, helm/). 73% of our diff already lives outside upstream files. |
| **Shim/dual-stack (Meta WebRTC pattern)** | Overkill | Solves "escape from a fork" by running both versions side-by-side. Massive engineering cost. |
| **Cherry-pick whitelist** | Tactical | Good for security patches and isolated fixes; bad for keeping up with feature releases. |
| **Path-scoped merges (the "frozen modules" pattern)** | **Best transitional** | Exactly what @lexlubbers proposed. Native Git doesn't have it, but `git merge --no-commit` + `git checkout HEAD -- <frozen paths>` approximates it. |
| **Vendoring upstream as a dependency** | Doesn't fit | Open WebUI doesn't ship as a library. |
| **Hard fork** | Not yet | Reserve for "upstream's roadmap diverges from ours" scenario, not "merges are tedious." |

### Real-world case studies

- **GitLab CE/EE.** EE-only code lives in `ee/`. CI can run `pipeline:run-as-if-foss`. Cleanest example of fork-via-namespace.
- **AOSP Common Kernel.** Tagged commit prefixes (`UPSTREAM:`, `BACKPORT:`, `FROMGIT:`, `ANDROID:`) make every patch's relationship to upstream explicit. Multi-level fork hierarchy works because metadata is rigorous.
- **Shopify's LibreChat fork** (Tobi Lütke on X): "we merge most everything back" — feasible only because they enforced merge-back discipline early.
- **MariaDB ↔ MySQL.** Started as drop-in replacement, deliberately diverged from version 10 (2014); now effectively independent. Took years and dedicated headcount.
- **io.js → Node.js.** Reunified after 9 months because divergence was bounded *and* both sides wanted reunification.
- **LibreOffice ↔ OpenOffice.** A fork can become the de-facto upstream, but both sides need community vitality.
- **Meta WebRTC (Apr 2026).** Renamespaced symbols (`webrtc::` → `webrtc_legacy::` and `webrtc_latest::`) to migrate off a 5-year-old fork. Demonstrates that "escape from a fork trap" is technically possible but expensive.

### Key tooling

| Tool | Role |
|---|---|
| **`git rerere`** | Records and replays conflict resolutions. **Enable this immediately** — `git config rerere.enabled true; git config rerere.autoupdate true`. Costs nothing, every conflict resolved is reusable. |
| **`git-imerge`** | Pairwise incremental merge. For 410 × 339 commits, this surfaces ~1 conflict per commit-pair instead of one giant ball. |
| **Mergiraf** | Tree-sitter-based syntax-aware merge driver (Python, TS, Svelte, JSON, TOML). Eliminates noise conflicts caused by reordering/moves. Worth registering for the next merge. |
| **`git range-diff`** | Find commits that have already been upstreamed independently. |
| **`git checkout <ref> -- <paths>`** | The path-scoped merge primitive — take a specific path from one side mid-merge. |

---

## 3. Module distribution recommendation

### Three-bucket policy

#### A. KEEP TRACKING (continue merging from upstream)

These modules are upstream-dominated, low-volume on our side, or contain core shared functionality where upstream improvements matter:

- `backend/open_webui/main.py` — high HOT but unavoidable; this is the integration seam.
- `backend/open_webui/models/` — upstream churns hard. Take their changes, re-apply our small additions (typed KB column, suspended_at, etc.).
- `backend/open_webui/routers/` — 19 files we never touched. 11 overlapping files we must reconcile case-by-case.
- `backend/open_webui/utils/middleware.py`, `utils/oauth.py` — upstream-dominated; our changes are surgical.
- `backend/open_webui/retrieval/` — we barely touch (125/64) while upstream improves loaders/vectors significantly.
- `backend/open_webui/migrations/` — both sides only add files; just regenerate Alembic merge revisions.
- `backend/open_webui/tools/builtin.py` — upstream rewrote, our delta is 39 lines. Take wholesale + reapply.
- `src/lib/components/chat/` — HOT but core; we depend on upstream improvements.
- `src/lib/components/layout/Sidebar.svelte`, `Sidebar/UserMenu.svelte` — bilateral but small.
- `src/lib/components/common/` — we barely touch; take freely.

#### B. FREEZE (stop merging — these are effectively forked subsystems)

| Path | Why |
|---|---|
| `backend/open_webui/services/` | 43 files, +7,103 lines, **zero overlap with upstream**. Pure CUSTOM. |
| `backend/open_webui/routers/{agent_proxy,archives,data_warnings,export,external_retrieval,google_drive_sync,integrations,invites,onedrive_sync,totp}.py` | 10 routers that don't exist upstream. |
| `src/lib/components/admin/Settings/` | Most files are pure-custom (Acceptance, Database, Email, IntegrationProviders, Integrations, Interface, ManageModelsModal, ModelSettingsModal, Security). Only `Evaluations/Feedbacks.svelte` is contested — handle that one surgically. |
| `src/lib/components/workspace/` | 16 files we own (+2,487 / −377) vs upstream's 99/37. Effectively CUSTOM. |
| `src/lib/components/chat/MessageInput.svelte` | 505/147 ours vs 78/6 upstream. Effectively ours. |
| `src/lib/components/chat/MessageInput/InputMenu.svelte` | 819/268 ours vs 58/0 upstream. Effectively ours. |
| `src/lib/components/chat/Messages/Citations/CitationModal.svelte` | 188/107 ours vs 3/1 upstream. |
| `src/lib/components/notes/NoteEditor.svelte` | 172/154 ours vs 13/3 upstream. |
| `src/lib/utils/onedrive-file-picker.ts` | Effectively ours. |
| `src/lib/apis/{agent_proxy,archives,data_warnings,evaluations,export,feedbacks,googleDrive,integrations,invites,onedrive,totp}.ts` | Custom API clients. |
| `backend/open_webui/routers/configs.py` | 360/5 ours vs 17/10 upstream. |
| `helm/`, `.github/workflows/`, `.claude/`, `collab/`, `thoughts/`, `scripts/`, `backend/open_webui/test/`, `backend/open_webui/templates/` | Pure-ours infrastructure. |
| `src/lib/i18n/locales/nl-NL/translation.json` | We own this locale's content. Upstream additions should be unioned in, but never overwritten. (See union-merge driver suggestion below.) |

#### C. EASY WINS (take wholesale on next merge)

These are pure-upstream files — auto-merge, free upgrade. Largest clusters:

- `automations/` (5 files, +1,110) — entirely new, no conflict.
- `calendar/` (4 files, +883) — entirely new.
- 19 new routers (`analytics`, `audio`, `automations`, `calendar`, `channels`, `functions`, `groups`, `images`, `memories`, `models`, `notes`, `ollama`, `openai`, `pipelines`, `prompts`, `scim`, `skills`, `terminals`, `tools`).
- 14 new model files (`access_grants`, `automations`, `calendar`, `channels`, `chat_messages`, `feedbacks`, `memories`, `messages`, `oauth_sessions`, `prompt_history`, `prompts`, `shared_chats`, `skills`, `tags`).
- New retrieval components: `loaders/mistral`, `loaders/paddleocr_vl`, `models/colbert`, `vector/async_client`, `vector/dbs/pgvector`.
- 7 upstream-only Alembic migrations (calendar, tasks/summary, last_read_at, shared_chat, automations, is_pinned).
- 63 upstream-only Svelte components: `XTerminal`, `FileNav` + previewer, `ShareChatModal`, `ModelSelector` rewrite, `Markdown` decomposition, `MultiResponseMessages`, `StatusHistory`, `TaskList`, `Emojis`, `TerminalMenu`, `VoiceRecording`, `FloatingButtons`, `AutomationModal`, etc.

### Tracking the upstream version honestly

Once you adopt path-scoped merges, your version label needs two parts:

```yaml
# UPSTREAM_VERSION.md
last_full_merge: v0.9.2          # version we tracked for B (frozen) paths is older
frozen_paths_baseline: v0.8.12   # last version where frozen paths were rebased
last_security_sync: 2026-04-19   # date of last security-only cherry-pick across all paths
```

This is the AOSP/RHEL pattern: be explicit that "we are at upstream v0.9.2 *for the parts we merge* and at v0.8.12 *for the parts we've frozen*." Do not pretend a single version label covers the whole repo once you start freezing modules.

### A `MERGE_POLICY.md` proposal

Codify the freeze list as a script:

```bash
# scripts/upstream-merge.sh (sketch)
git fetch upstream main
git merge --no-commit --no-ff upstream/main || true
# Restore frozen paths
git checkout HEAD -- backend/open_webui/services/ \
  backend/open_webui/routers/{agent_proxy,archives,data_warnings,export,external_retrieval,google_drive_sync,integrations,invites,onedrive_sync,totp}.py \
  src/lib/components/admin/Settings/ \
  src/lib/components/workspace/ \
  src/lib/components/chat/MessageInput.svelte \
  src/lib/components/chat/MessageInput/InputMenu.svelte \
  src/lib/components/chat/Messages/Citations/CitationModal.svelte \
  src/lib/components/notes/NoteEditor.svelte \
  src/lib/utils/onedrive-file-picker.ts \
  src/lib/apis/{agent_proxy,archives,data_warnings,evaluations,export,feedbacks,googleDrive,integrations,invites,onedrive,totp}.ts \
  backend/open_webui/routers/configs.py \
  helm/ .github/workflows/ .claude/ collab/ thoughts/ scripts/ \
  backend/open_webui/test/ backend/open_webui/templates/
# Then resolve remaining conflicts manually
```

Plus a custom merge driver in `.gitattributes` for nl-NL (union-merge to stop overwrites):

```
src/lib/i18n/locales/nl-NL/translation.json merge=ours-then-union
```

Note: the i18n merge driver is non-trivial — JSON files don't union-merge cleanly with `git`'s built-in `union` driver (it would produce invalid JSON). A small Python script registered as a custom driver that does key-level deep merge is the right shape. (Worth a separate small spike.)

---

## 4. The async DB refactor — what it brings, what it costs

### What changed

Single commit `27169124f` (Timothy Jaeryang Baek, 2026-04-12, "refac: async db"):

- **74 files changed, +4829 / −4477 lines.**
- Adds an `AsyncSession` engine + sessionmaker in `backend/open_webui/internal/db.py` (sync engine kept *only* for startup config, Alembic, healthcheck).
- Rewrites every model class (~25 files) and every standard router (~25 files) from `def`/`Session` to `async def`/`AsyncSession`.
- The pattern is mechanical: `db.commit()` → `await db.commit()`, `db.query(X).filter(...)` → `await db.execute(select(X).filter(...))`, `with get_db_context(db)` → `async with get_async_db_context(db)`.

The new pattern, by example:

```python
# Model method (models/chats.py): before → after
def insert_new_chat(self, user_id, form_data, db: Optional[Session] = None) -> ChatModel:
    with get_db_context(db) as db:
        ...
        db.commit()
# becomes:
async def insert_new_chat(self, id, user_id, form_data, db: Optional[AsyncSession] = None) -> ChatModel:
    async with get_async_db_context(db) as db:
        ...
        await db.commit()

# Router endpoint (routers/chats.py): before → after
def get_session_user_chat_list(..., db: Session = Depends(get_session)):
    return Chats.get_chat_title_id_list_by_user_id(user.id, ..., db=db)
# becomes:
async def get_session_user_chat_list(..., db: AsyncSession = Depends(get_async_session)):
    return await Chats.get_chat_title_id_list_by_user_id(user.id, ..., db=db)
```

### What it would bring us

**Performance: not as much as it sounds, but real where it matters.**

- The "FastAPI is async, so async DB is faster" claim is *only* true under specific conditions: high concurrency, remote DB with non-trivial RTT, full-async stack, no lazy-load patterns. Per-query, async ORM is consistently *slower* than sync (greenlet overhead, result pre-buffering).
- Shippo's [production case study](https://goshippo.com/blog/why-is-my-fastapi-throughput-so-low) is instructive — they got 195 RPS by going **sync**, beating their async-with-blocking-calls hybrid by 4.6×.
- Mike Bayer (SQLAlchemy author): "For stereotypical database logic, there are no advantages to using async versus a traditional threaded approach, and you can likely expect a small to moderate decrease in performance."
- **For Open WebUI's workload — LLM streaming, vector DB, Graph API roundtrips, file uploads — the database is rarely the bottleneck.** Chat metadata writes happen before/after the streaming token loop, not inside it. The expensive work is `await openai_client.chat.completions.create(stream=True)`.
- **Where async DB *does* help us specifically:**
  - `services/sync/base_worker.py` (54 sync DB calls inside an async coroutine — every Vink-scale doc load currently blocks the event loop).
  - Streaming endpoints that do touch the DB inside the stream.
  - Future fan-out workloads we don't have today.

**Maintainability: this is the actual reason to do it.**

- Diverging from upstream on 74 files of model/router code is permanent merge pain. Every future upstream release builds on the async base — `asyncpg`→`psycopg` swap, AsyncVectorDBClient (already in v0.9.2), automation worker async DB handling.
- Our `services/sync/base_worker.py` already mixes `async def` (HTTP/file I/O) with sync DB calls — we've been writing patches around this exact bottleneck (chunked gather, document timeout). The upstream refactor *is* the principled fix.
- The pattern is mechanical, so the migration is a grep-and-prefix exercise, not a redesign.

### What it would cost us

**Adoption cost in our custom code:**

- ~215 model call sites in our custom code need `await`-ification (counted by grepping `Chats.|Users.|Knowledges.|Files.|Models.|Tags.|Folders.` in `services/` and our 10 custom routers).
- ~25 function signatures need `def` → `async def` propagation (cascading: callers also need `await`).
- Breakdown:
  - `services/sync/base_worker.py`: 54 call sites — biggest single conversion target, **also where we benefit most**.
  - `services/deletion/service.py`: 35
  - `services/sync/router.py`: 15
  - `services/export/service.py`: 12
  - `services/deletion/cleanup_worker.py`: 9
  - `services/confluence/sync_worker.py`: 8
  - `services/retention/service.py`: 7
  - `services/onedrive/sync_worker.py`: 7
  - `services/google_drive/sync_worker.py`: 7
  - `routers/integrations.py`: 25
  - `routers/totp.py`: 11
  - Other custom routers: ~9 combined
- **Effort estimate: 5–8 dev-days for the await-ification itself**, plus the upstream merge conflict resolution (the real cost).

**This is an all-or-nothing merge.** Upstream did *not* keep sync model methods alive — the moment `models/chats.py` lands, every caller must `await`. You can't merge the engine + a single router/model and leave the rest. So you either merge it cleanly all at once, or you freeze around it (and pay a permanent merge cost on every future model/router change upstream makes).

### Pitfalls to plan for

1. **Silent type drift.** Forgetting an `await` on a model call returns a coroutine where you expected a `KnowledgeModel`. Fails at attribute access, looks fine to linters. Need `RuntimeWarning: coroutine was never awaited` enabled in dev.
2. **Connection pool sizing.** Async `gather`-chunked workers (which we already have for Vink) compete for the shared pool. Tune `DATABASE_POOL_SIZE` post-merge.
3. **AsyncSession is not concurrent-safe.** `gather(*[Knowledges.x(db=db), Knowledges.y(db=db)])` against the same session raises `MissingGreenlet`. Audit our `db=db` parameter passing inside gathers.
4. **Lazy loading dies.** Every `chat.messages`, `user.workspaces` access in async needs `selectinload`/`joinedload` in the query *or* the `AsyncAttrs` mixin. N+1 bugs that were silent become runtime crashes.
5. **Sync workers staying sync.** Our `BaseSyncWorker`, retention service, archival cleanup are called from coroutines but use sync sessions today. Best path: **two engines** — async for routes, sync for workers — both pointing at the same DB. SQLAlchemy supports this cleanly. Document the pattern in `external-integration-cookbook.md`.
6. **psycopg v3 driver flip.** Upstream flipped `asyncpg` → `psycopg` in a later commit (post-`27169124f`) to fix SSL parameter brittleness. **Don't merge `27169124f` without also pulling the psycopg fix.**
7. **SQLCipher unsupported async.** Not a concern today — we don't ship it — but flag for any future deployment.

### Verdict on async DB

**Absorb it. Plan it as a 2-week effort, not a casual merge.**

- **Week 1:** Merge `27169124f` + psycopg fix into a feature branch. Resolve conflicts in `routers/integrations.py` (biggest custom call-site cluster) and `services/sync/base_worker.py`. Get the backend booting.
- **Week 1–2:** await-ify the 215 call sites. Mechanical pass first, then targeted: `base_worker.py` → all sync workers → custom routers → utilities. Run lint+tests after each block.
- **Week 2:** Stress-test Vink-equivalent (~1000 docs) with the new pool settings. Tune `DATABASE_POOL_SIZE`. Verify retention/deletion/archival workers still tick.
- **Honest framing for the team:** this is a maintainability migration, not a performance migration. p95 chat latency before/after should be within noise.
- **Don't freeze around it.** Freezing means accumulating a year of upstream improvements that all assume async DB. The technical debt of staying sync compounds faster than the migration cost.

---

## 5. Other high-value upstream changes (v0.8.12 → v0.9.2)

Even if we narrow the merge scope, these specific upstream commits are worth pulling explicitly via cherry-pick because they bring real wins:

### Performance (cheap to absorb, free wins)

- `3560d2f63` perf(chats): drop redundant `db.refresh` after commit in `update_chat_by_id`
- `f0e0cfcf0` perf: avoid redundant knowledge re-fetch in `update_knowledge_access_by_id`
- `5eae0a5cd` perf(users): drop redundant `get_user_by_id` refetch in session-user endpoints
- `e396af3cc` perf: reuse request db session in `get_model_profile_image`
- `b3ca943da` perf(channels): batch user lookup in `model_response_handler` thread history
- `32cfb5788` perf(chats): select only `meta` column in `get_chat_tags_by_id_and_user_id`
- `e5f31c2e1` / `51cd43229` perf: replace `JSON.stringify` equality with `fast-deep-equal`

These all touch files we co-edit. If we go async DB, they come along for free. If we don't, they're worth cherry-picking individually.

### Concurrency / async (HIGH value for our Vink workloads)

- **`27169124f` refac: async db** — the big one (see §4).
- `a3ea7bf04` fix(retrieval): offload `Loader.load` to a worker thread so file uploads stop blocking the event loop. **Highly relevant** — we have document processing.
- `804f9f315` fix(retrieval): offload sync `VECTOR_DB_CLIENT` calls in async paths via `AsyncVectorDBClient`. **Highly relevant.**
- `ee28032fb` fix(middleware): replace `BaseHTTPMiddleware` HTTP middlewares with pure ASGI implementations. Significant perf for streaming.
- `f6bd08c85` fix(utils): switch throttle decorator to async.

### Security fixes (must absorb regardless of strategy)

These are all small fixes that should be cherry-picked even if we freeze the surrounding modules:

- `67023037f` fix: replace brittle `profile_image_url` allowlist with safe-scheme validation.
- `e7ff4768f` fix: add ownership checks to global task endpoints.
- `0753409e7` fix: use `ipaddress` stdlib for IPv6 SSRF protection.
- `b78dabb44` fix: reject empty passwords in LDAP authentication (prevent unauthenticated binds).
- `83024d00b` fix: enforce API key endpoint restrictions at the auth layer, not middleware.
- `4f94d2178` fix: enforce `filter_allowed_access_grants` on channel create and update.
- `fb5ef978b` fix: enforce `OAUTH_ALLOWED_DOMAINS` on token exchange endpoint.
- `977d638af` fix: invalidate stale Socket.IO sessions on role change and user deletion.
- `f6b85700e` fix: gate OpenAI catch-all proxy behind `ENABLE_OPENAI_API_PASSTHROUGH` toggle.
- `b618d8406` fix: add missing read-access check on channel members endpoint.
- `a2a9a3a42` fix: prevent path traversal via model name in Azure deployment URLs.
- `4498c21f4` fix: enforce model access control on Ollama generate/show/embed/embeddings endpoints.

### New features (additive — easy)

- `98c4f264e` feat: calendar (entirely new directory).
- `58bc25480` feat: PaddleOCR-vl loader support and retrieval router infrastructure.
- `b73538ece` feat(ui): citation source overflow badge.
- `4292358bd` feat: log provider errors to console.
- `4d2f18981` feat: add `RAG_RERANKING_BATCH_SIZE` configuration.

### Infrastructure / observability

- `588b81eed` fix(redis): opt-in `health_check_interval` for stale pooled connections.
- `db7f122cb` fix(redis): opt-in TCP socket keepalive on all client connections.
- `26a645f9e` refac: license — relevant if upstream changed licensing terms (worth reading the diff).

---

## 6. Concrete next-step recommendation

### Immediate (this week, before any merge attempt)

1. **Enable `git rerere`** globally for the repo:
   ```bash
   git config rerere.enabled true
   git config rerere.autoupdate true
   ```
   Costs nothing, captures conflict resolutions for replay on subsequent merges.

2. **Install Mergiraf as a merge driver** for `.py`, `.ts`, `.svelte`, `.json`. Many of our 90 non-i18n bilateral conflicts are syntactic noise that Mergiraf resolves automatically.

3. **Decide on async DB now, not later.** This is the single biggest variable in the next merge. Recommendation: yes, absorb. Plan 2 weeks.

### Short-term (next 1–3 weeks)

4. **Write `MERGE_POLICY.md`** in the repo root (or `docs/`), documenting the freeze list (§3.B) and a path-scoped merge script. Reference this doc.

5. **Write a small JSON union-merge driver** for `nl-NL/translation.json`. Eliminates a recurring pain point.

6. **Cherry-pick the security fixes (§5)** *before* the next big merge — they apply cleanly, they're small, they shouldn't wait.

7. **Run `git range-diff main upstream/main`** to identify any of our 339 commits that have been upstreamed independently. Skip those during the merge.

### Medium-term (next 1–3 months)

8. **Refactor any remaining sync-from-async-blocking patterns** out of our code as part of the async DB migration. The `services/sync/base_worker.py` rewrite is the highest-leverage one.

9. **Identify upstreamable commits.** At least the GDPR/data-export work, the feature-flag plumbing, and the agent-proxy pattern have general value. Each upstreamed feature reduces ongoing merge cost permanently.

10. **Set a divergence budget.** Define a metric (e.g., "number of upstream files where we have inline modifications") and a ceiling. When the metric exceeds the ceiling, refactoring takes priority over new features.

### The endgame question

Two viable long-term paths:

- **Sustainable soft fork:** Continue the GitLab CE/EE-style separation. Invest in extension points so customizations stay in `services/`, custom routers, and admin Settings. Merges become near-trivial in 12 months. **This is the recommended path — it matches existing preferences and the existing diff shape.**
- **Conscious hard fork:** If upstream's roadmap meaningfully diverges from soev.ai's data-sovereignty/Dutch-public-sector focus, declare a hard fork at a stable version, cherry-pick security patches only. Don't go here unless upstream's direction clearly conflicts with ours — currently it doesn't.

---

## Appendix A: Sources

**Engineering / case studies:**
- [Engineering at Meta: Escaping the Fork (WebRTC)](https://engineering.fb.com/2026/04/09/developer-tools/escaping-the-fork-how-meta-modernized-webrtc-across-50-use-cases/)
- [GitLab: Guidelines for implementing EE features](https://docs.gitlab.com/development/ee_features/)
- [Android Common Kernels (AOSP)](https://source.android.com/docs/core/architecture/kernel/android-common)
- [GitHub Blog: Strategies for friendly fork management](https://github.blog/developer-skills/github/friend-zone-strategies-friendly-fork-management/)
- [Salesforce Engineering: No Forking Way — 6 Rules for OSS Code Management](https://engineering.salesforce.com/no-forking-way-dc5fa842649b/)
- [Nick Desaulniers: Forking is not free; the hidden costs](https://nickdesaulniers.github.io/blog/2023/02/01/forking-is-not-free-the-hidden-costs/)
- [Tobi Lütke on Shopify's LibreChat fork](https://x.com/tobi/status/1932846291794510241)
- [DIE ANTWORT: Git Tricks for Maintaining a Long-Lived Fork](https://die-antwort.eu/techblog/2016-08-git-tricks-for-maintaining-a-long-lived-fork/)
- [Reuse and maintenance practices among divergent forks (2021)](https://link.springer.com/article/10.1007/s10664-021-10078-2)

**Git tooling:**
- [Pro Git: Subtree Merging](https://yeeon.github.io/book/ch6-7.html)
- [git-rerere documentation](https://git-scm.com/book/en/v2/Git-Tools-Rerere)
- [git-imerge: Incremental merge for git](https://github.com/mhagger/git-imerge)
- [Mergiraf: syntax-aware merging](https://mergiraf.org/) ([LWN coverage](https://lwn.net/Articles/1042355/))
- [Stacked Git (StGit)](https://stacked-git.github.io/), [TopGit](https://github.com/mackyle/topgit), [Git Patch Stack](https://git-ps.sh/)

**FastAPI / SQLAlchemy async:**
- [FastAPI: Concurrency and async / await](https://fastapi.tiangolo.com/async/)
- [SQLAlchemy 2.0 Asynchronous I/O docs](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html)
- [SQLAlchemy Discussion #7898 (Should I use async?)](https://github.com/sqlalchemy/sqlalchemy/discussions/7898)
- [Mike Bayer: Asynchronous Python and Databases](https://techspot.zzzeek.org/2015/02/15/asynchronous-python-and-databases/)
- [Shippo: Why is my FastAPI throughput so low?](https://goshippo.com/blog/why-is-my-fastapi-throughput-so-low)
- [hackeryarn: What async really means for your Python web app](https://hackeryarn.com/post/async-python-benchmarks/)
- [Shane Zhang: Async SQLAlchemy Journey](https://shanechang.com/p/async-sqlalchemy-journey/)
- [matt.sh: SQLAlchemy: the async-ening](https://matt.sh/sqlalchemy-the-async-ening)
- [Gold Lapel: SQLAlchemy 2.0 Async Slower Than Sync](https://goldlapel.com/how-to/sqlalchemy-async-slower-than-sync)

## Appendix B: Key commits referenced

| Commit | Date | Subject |
|---|---|---|
| `9bd84258` | 2026-03-27 | Merge base — last successful sync from upstream/main (v0.8.12) |
| `27169124f` | 2026-04-12 | refac: async db (74 files, +4829/−4477) |
| `a3ea7bf04` | 2026-04 | fix(retrieval): offload Loader.load to worker thread |
| `804f9f315` | 2026-04 | fix(retrieval): AsyncVectorDBClient |
| `ee28032fb` | 2026-04 | fix(middleware): pure ASGI HTTP middlewares |
| `8dae237a` | 2026-04-24 | 0.9.2 release |
| `077331e5` (ours) | 2026-04-19 | feat: fixed the file path in loading (current branch HEAD) |

## Related

- `collab/docs/external-integration-cookbook.md` — sync abstraction layer cookbook (the additive-pattern recipe that's been keeping `services/` conflict-free)
- `collab/world/preferences.md` — "Build for upstream merge compatibility — minimize touch points with upstream files" (this doc is the operational expansion of that preference)
- `collab/index.md` `[20-03-2026]` — past v0.6.43 → v0.8.9 merge (no detailed pain log was written; should be on next time)
