# Weaviate Native Multi-Tenancy Refactor — Implementation Plan

## Overview

Replace the "one Weaviate **collection** per KB / file / web-search / memory" model with Weaviate **native multi-tenancy**: five fixed schema collections, each logical OWUI data unit becoming a **tenant** (one shard + dedicated vector index per tenant). Collections isolate schema; tenants isolate data. This removes the per-class explosion (3440 classes on haute-equipe) that drives compute + retrieval overhead.

Work spans three repos on branch `feat/weaviate-tenancy` (exists on open-webui and genai-utils; soev-gitops uses `main`):
- **open-webui** — new MT connector behind a flag (the bulk of the code).
- **genai-utils** — agents direct provider gains `.with_tenant(...)` behind the same flag.
- **soev-gitops** — Weaviate upgrade to 1.37.7, a collection→tenant migration Job, and the cutover/rollout.

Full research + locked decisions: `thoughts/shared/research/2026-06-03-weaviate-collections-vs-tenants-refactor.md`.

## Current State Analysis

- **Connector**: `open-webui/backend/open_webui/retrieval/vector/dbs/weaviate.py` (upstream PR #14747, heavily forked by us). `_sanitize_collection_name` (`:122-142`) turns each logical `collection_name` into its own Weaviate class; `insert`/`upsert` create per-name classes via `_ensure_collection` (`:184-218`). `_build_vector_config` (`:82-95`) applies flat+BQ to `File_*`/`Web_search_*`/`User_memory_*` when `ENABLE_WEAVIATE_BQ_QUANTIZATION` is set, else HNSW.
- **Factory**: `vector/factory.py:79-82` has a single `WEAVIATE` case (no flag). Qdrant/Milvus branch on `ENABLE_QDRANT_MULTITENANCY_MODE` (default true, `config.py:2654`) / `ENABLE_MILVUS_MULTITENANCY_MODE` (`config.py:2642`).
- **The pattern to mirror**: `qdrant_multitenancy.py:98-161` and `milvus_multitenancy.py:64-126` consolidate into 5 shared collections, mapping `collection_name` → `(shared_collection, tenant_id)` with `tenant_id = collection_name`, and filtering by that id. Our Weaviate version uses **native tenants** (`with_tenant`) instead of a property filter.
- **Call-site parity (verified)**: `delete_collection` is only ever called with a KB-UUID or `file-<id>` (never web-search/hash); `reset` (2 admin callers: `routers/files.py:501`, `routers/retrieval.py:2747`) wipes everything; `has_collection` gates delete/search control flow. Full table in the research doc.
- **Cloud sync**: OneDrive/Google Drive/Confluence workers write to `collection_name=self.knowledge_id` (KB collection) — **no Drive-specific collections**. `onedrive_item_id`/`onedrive_drive_id` are chunk **properties** (`weaviate.py:174-178`), used as filters.
- **Dead config**: `WEAVIATE_WEB_SEARCH_TTL_MINUTES` (`config.py:2663`) is defined but never read; web-search/hash collections accumulate forever (only removed by in-place overwrite or global `reset`).
- **genai-utils**: the agents direct provider reads these OWUI collections via `agents/retrieval/providers/_weaviate_adapter.py` — **5 query sites** (`near_vector :81`, `hybrid :103`, `bm25 :126`, `bm25_grouped :164`, `fetch_objects :196`), each doing `collections.get(name)` then a query. The **Search API** (`api/backends/chunk/weaviate.py`) reads curated datasets (empty for soev) — **out of scope**.
- **gitops**: Weaviate image pinned at `tenants/base/values.yaml:38-42` (`semitechnologies/weaviate:1.35.0`), not under any ImagePolicy → version bump is a manual values edit. Per-tenant Weaviate StatefulSet `<tenant>-weaviate` in ns `open-webui-<tenant>` (previder) / `gradient-soev-<tenant>` (intermax). Backup = native `/v1/backups/filesystem` POST+poll (`helm/tenant-backup/templates/configmap.yaml:45-117`). Existing migration harness `scripts/migration/weaviate-flat-migration/` (iterate-with-vectors → batch-reinsert → checkpoint/crash-safety; image `ghcr.io/gradient-ds/weaviate-flat-migration`).

## Desired End State

With `ENABLE_WEAVIATE_MULTITENANCY_MODE=true`, each tenant's Weaviate holds **five MT data collections** (`Knowledge`, `File`, `WebSearch`, `UserMemory`, `HashBased`) + the existing standalone `Knowledge_bases` meta-collection (unchanged). Every KB/file/search/memory is a **tenant**, not a class. open-webui and genai-utils both route reads/writes through `.with_tenant(...)`. Legacy per-class data is migrated into tenants by a batched Job; a dual-read shim keeps un-migrated data reachable during the window. Verifiable: `GET /v1/schema` shows ≤6 classes per instance; `/v1/nodes?output=verbose` shows tenant shards; retrieval returns identical results pre/post.

### Key Discoveries
- Tenant key = the **raw `collection_name`** (mirrors qdrant/milvus `tenant_id = collection_name`); all values are valid Weaviate tenant names (`file-<id>`, `<uuid>`, `web-search-<hash≤63>`, `user-memory-<id>`, 64-hex). No parsing needed; trivially derivable in both repos.
- `VectorDBBase` passes a single `collection_name` per call (`vector/main.py:23-86`) → the entire MT mapping stays **inside the connector**; no router changes in open-webui.
- Index config under MT is set **once per collection**: `File`/`WebSearch`/`UserMemory` → flat+BQ (when `ENABLE_WEAVIATE_BQ_QUANTIZATION`), `Knowledge`/`HashBased` → HNSW. Matches current behavior.
- Cloud-sync metadata works as long as `Knowledge`/`File` schemas carry the full property set from `_create_collection` (`weaviate.py:162-181`).
- genai-utils per-file path (`weaviate_openwebui.py:338-421`) queries `file-*` names that never appear in the ACL response → the tenant **must be derived** (not threaded via ACL). This makes "duplicate the pure mapping fn in both repos" the uniform choice.

## What We're NOT Doing

- **Not** touching the genai-utils Search API (`api/backends/chunk/weaviate.py`) — curated datasets, separate collection set.
- **Not** converting the `Knowledge_bases` meta-index to MT (single collection, well under threshold).
- **Not** implementing migrate-on-read — batch Job + dual-read fallback only.
- **Not** adopting `dynamic()` index yet (D4: keep flat+BQ; revisit later).
- **Not** wiring up TTL / tenant offloading / S3 in this pass (note `WEAVIATE_WEB_SEARCH_TTL_MINUTES` stays dead).
- **Not** removing the legacy `weaviate.py` connector until all targeted tenants are migrated and verified.
- **Not** auto-updating the Weaviate image via ImagePolicy — version bumps stay manual/staged.

## Implementation Approach

Ship behavior-neutral first (flag default OFF in both images), so the non-atomic Flux image rollout carries **no** behavior change. Cut over per-tenant by flipping `ENABLE_WEAVIATE_MULTITENANCY_MODE` via a **single Helm values commit** (Flux applies to both HelmReleases together); the dual-read shim covers the seconds-level pod-roll skew. Upgrade Weaviate to 1.37.7 **before** creating many tenants (gets the dynamic lazy-shard-load fix + INACTIVE-tenant backups). Migrate legacy classes with a batched, crash-safe Job forked from the proven flat-index harness. Roll out test → gradient → semver tenants → pinned tenants.

---

## Phase 1: open-webui — MT connector + flag (default OFF)

### Overview
Add the native-MT connector, the shared mapping, the config flag, the factory branch, and the dual-read shim. No behavior change until the flag flips.

### Changes Required

#### 1. Shared mapping function
**File**: `backend/open_webui/retrieval/vector/dbs/_weaviate_mt_mapping.py` (new)
**Changes**: Pure function mirroring qdrant/milvus prefix logic, returning `(collection, tenant)`. This is the canonical mapping duplicated verbatim into genai-utils (Phase 2). Keep the upstream data-corruption warning comment.

```python
# Maps an OWUI logical collection_name to (mt_collection, tenant).
# WARNING: coupled to OWUI naming conventions (user-memory-/file-/web-search-/
# KB-UUID/hash). Changing OWUI naming without updating this AND the genai-utils
# copy risks routing data to the wrong tenant — data corruption.
KNOWLEDGE, FILE, WEB_SEARCH, USER_MEMORY, HASH_BASED = (
    "Knowledge", "File", "WebSearch", "UserMemory", "HashBased",
)
KNOWLEDGE_BASES_META = "Knowledge_bases"  # standalone, NOT multi-tenant

def map_collection(collection_name: str) -> tuple[str, str | None]:
    name = collection_name
    if name == "knowledge-bases":
        return KNOWLEDGE_BASES_META, None        # meta-index: no tenant
    if name.startswith("user-memory-"):
        return USER_MEMORY, name
    if name.startswith("file-"):
        return FILE, name
    if name.startswith("web-search-"):
        return WEB_SEARCH, name
    if len(name) in (63, 64) and all(c in "0123456789abcdef" for c in name):
        return HASH_BASED, name                  # URL/YouTube/text hash
    return KNOWLEDGE, name                        # KB UUID
```

#### 2. The MT connector
**File**: `backend/open_webui/retrieval/vector/dbs/weaviate_multitenancy.py` (new)
**Changes**: `class WeaviateClient(VectorDBBase)` reusing connection setup + property schema + metadata sanitization from `weaviate.py`. Five collections created lazily with `multi_tenancy(enabled=True, auto_tenant_creation=True, auto_tenant_activation=True)` and per-collection index config (flat+BQ for File/WebSearch/UserMemory when `ENABLE_WEAVIATE_BQ_QUANTIZATION`, else HNSW). Each op resolves `(coll, tenant) = map_collection(name)`, ensures the collection, and operates on `client.collections.get(coll).with_tenant(tenant)`.

Method semantics (parity verified against call sites):
- `insert`/`upsert` → `auto_tenant_creation` makes the tenant on first write; `coll.with_tenant(tenant).data.insert_many(...)`.
- `search`/`query`/`get` → `coll.with_tenant(tenant).query.*`; **dual-read fallback** (see #4) when the tenant is absent.
- `has_collection` → tenant exists in `coll` (`coll.tenants.get_by_name(tenant)` / `tenants.exists`).
- `delete(ids|filter)` → tenant-scoped delete.
- `delete_collection(name)` → `coll.tenants.remove([tenant])` (drop the shard, **not** the schema collection). `Knowledge_bases` (tenant=None) → delete-by-id as today.
- `reset` → delete the five MT collections + `Knowledge_bases`.

Reuse: `_make_json_serializable`, `_sanitize_property_name`, `_sanitize_metadata_keys`, the explicit property list (`weaviate.py:162-181`), and `_build_vector_config` (generalized to take the target collection name).

#### 3. Config flag + factory branch
**Files**: `backend/open_webui/config.py` (near `:2657-2670`), `backend/open_webui/retrieval/vector/factory.py:79-82`
```python
# config.py
ENABLE_WEAVIATE_MULTITENANCY_MODE = os.environ.get(
    "ENABLE_WEAVIATE_MULTITENANCY_MODE", "false").lower() == "true"
```
```python
# factory.py
case VectorType.WEAVIATE:
    if ENABLE_WEAVIATE_MULTITENANCY_MODE:
        from open_webui.retrieval.vector.dbs.weaviate_multitenancy import WeaviateClient
    else:
        from open_webui.retrieval.vector.dbs.weaviate import WeaviateClient
    return WeaviateClient()
```

#### 4. Dual-read shim
**File**: `weaviate_multitenancy.py` (within the read methods), flag `WEAVIATE_MT_LEGACY_FALLBACK` (default `true`) in `config.py`.
**Changes**: On `search`/`query`/`get`/`has_collection`, if the MT tenant is absent (or empty) and fallback is on, read the legacy class `_legacy_sanitize(name)` (the old `_sanitize_collection_name` logic) if it exists. Writes always go MT. This makes the flag flip safe before migration completes and lets migration run on any schedule.

#### 5. Tests
**File**: `backend/open_webui/test/util/test_weaviate_mt_mapping.py` (new) + extend `test/util/test_weaviate_index_policy.py`
**Changes**: Unit-test `map_collection` for every prefix incl. `knowledge-bases`, 63 vs 64-hex, KB-UUID; test index-config selection per collection under BQ on/off. (Connector I/O is exercised by the cutover, not unit tests — no live Weaviate in CI.)

### Success Criteria

#### Automated Verification:
- [ ] Mapping unit tests pass: `cd open-webui/backend && python -m pytest open_webui/test/util/test_weaviate_mt_mapping.py -q`
- [ ] Index-policy tests pass: `python -m pytest open_webui/test/util/test_weaviate_index_policy.py -q`
- [ ] Lint clean: `npm run lint:backend`
- [ ] With flag unset, factory still returns legacy `weaviate.WeaviateClient` (assert in a test)

#### Manual Verification:
- [ ] Flag OFF: existing behavior unchanged on a local stack (KB upload, chat-file, web search, memory all work against per-class collections)
- [ ] Flag ON against a scratch Weaviate: KB upload creates a `Knowledge` collection with a tenant = KB UUID; `GET /v1/schema` shows the 5 classes, not per-KB classes
- [ ] Dual-read: with flag ON and pre-existing legacy `C<uuid>` data, retrieval still returns it

**Implementation Note**: Pause after automated checks pass for manual confirmation before Phase 2.

---

## Phase 2: genai-utils — MT-aware agents adapter (flag OFF)

### Overview
Thread tenant into the 5 adapter query sites behind a matching flag. Behavior-neutral until flipped. Search API untouched.

### Changes Required

#### 1. Duplicate the mapping
**File**: `agents/retrieval/providers/_weaviate_naming.py`
**Changes**: Add `map_collection(collection_name) -> (collection, tenant)` — **verbatim copy** of the open-webui function (same warning comment). Keep the existing `sanitize_collection_name` for the legacy/fallback path.

#### 2. `.with_tenant` at the 5 sites
**File**: `agents/retrieval/providers/_weaviate_adapter.py:71-202`
**Changes**: Each method computes `coll, tenant = map_collection(collection_name)` and uses `self._client.collections.get(coll).with_tenant(tenant)` when MT mode is on; else the current `collections.get(sanitize(name))`. Carry the mode flag on the adapter (`__init__ :33-46`). Methods: `near_vector`, `hybrid`, `bm25`, `bm25_grouped`, `fetch_objects`.

#### 3. Flag + wiring
**Files**: `agents/config/deploy_config.py` (`ProviderDeployConfig`, near `:135-142`), `agents/deploy/bootstrap.py` (`_build_weaviate_client_adapter :908-929`, `_create_weaviate_openwebui_provider :812-874`), `deploy/projects/soev/config/agents/base.yaml` (under `openwebui_direct`, `:66-73`)
```yaml
# base.yaml (openwebui_direct provider block)
weaviate_multitenancy_enabled: false
```
Add `weaviate_multitenancy_enabled: bool = False` to the config model; read it in bootstrap and pass into the adapter. Per-env overlay can flip it.

#### 4. Dual-read on the agents side
**File**: `_weaviate_adapter.py`
**Changes**: When MT tenant query yields nothing and fallback is enabled, retry against the legacy class name (mirrors Phase 1 #4) so the agent path matches OWUI during the window.

#### 5. Tests
**File**: `agents/retrieval/providers/test_weaviate_naming.py` (extend)
**Changes**: Assert the genai-utils `map_collection` is identical to open-webui's for the full prefix matrix (copy the same cases).

### Success Criteria

#### Automated Verification:
- [ ] Naming/mapping tests pass: `cd genai-utils && python -m pytest agents/retrieval/providers/test_weaviate_naming.py -q`
- [ ] Agents module lint/type checks pass (per `agents/` CLAUDE.md tooling)
- [ ] With flag false, adapter constructs the same query calls as today (assert in a test/mock)

#### Manual Verification:
- [ ] Flag ON against scratch Weaviate seeded with MT data: agent search returns chunks via `.with_tenant`
- [ ] Per-file path (`_do_search_in_documents`) resolves tenant `file-<id>` correctly (derived, not from ACL)
- [ ] Mapping byte-identical to open-webui's (diff the two functions)

**Implementation Note**: Pause for manual confirmation before Phase 3.

---

## Phase 3: soev-gitops — Weaviate upgrade to 1.37.7 (staged, gradient first)

### Overview
Stepped, per-tenant upgrade `1.35.22 → 1.36.17 → 1.37.7` (latest patch each minor). Done before the migration so the many-shard world gets the lazy-shard-load fix + INACTIVE-tenant backups.

**Order: gradient.soev.ai first, on its own.** Do the entire stepped upgrade on **only the gradient tenant** first and let it soak — gradient is the test bed and the cutover gate (Phase 5 runs there). Confirm it's healthy on 1.37.7 before upgrading any other tenant. Only after gradient is proven do staging and the remaining tenants follow (each still upgraded ahead of its own migration/cutover). This means the Weaviate bump and the MT cutover both lead with gradient, so a version problem surfaces on the one tenant we're already exercising — not on a customer.

### Changes Required

#### 1. Stage the tag per-tenant (not the global base bump)
**File**: each target `tenants/<cluster>/<tenant>/helmrelease.yaml` under `spec.values.weaviate.image.tag`
**Changes**: Override the base `1.35.0` per tenant to walk the steps, so rollout is staged rather than all-at-once. (Base `tenants/base/values.yaml:42` is bumped to `1.37.7` only at the end, once all tenants are there.)
```yaml
spec:
  values:
    weaviate:
      image:
        tag: "1.36.17"   # then 1.37.7 in a later commit
```

#### 2. Chart env audit (out-of-repo chart)
**Changes**: Pull `oci://ghcr.io/gradient-ds/charts/open-webui-tenant` and check the Weaviate subchart for `DISABLE_LAZY_LOAD_SHARDS` (deprecated no-op since 1.36.6) — remove if present. Confirm `ENABLE_MODULES` includes `backup-filesystem`. The repo only injects `GOMEMLIMIT`/`GOGC` via postRenderer (`haute-equipe/helmrelease.yaml:13-37`); leave those.

#### 3. Runbook
**File**: `soev-gitops/thoughts/shared/commands/2026-06-03-weaviate-1.37-upgrade.md` (new)
**Changes**: Per-tenant: backup → bump to 1.36.17 → verify ready/object counts → backup → bump to 1.37.7 → verify. Single-line commands per repo convention. **Section 1 = gradient only** (run + soak); subsequent sections = staging, then the rest, each gated on gradient being healthy on 1.37.7.

### Success Criteria

#### Automated Verification:
- [ ] Flux reconciles the HelmRelease cleanly (no SSA errors) — user runs `flux get hr -n open-webui-<tenant>`
- [ ] `kustomize build` of the tenant dir succeeds locally

#### Manual Verification:
- [ ] `<tenant>-weaviate-0` reports the new version: `GET /v1/meta` shows `1.36.17` then `1.37.7`
- [ ] Object counts unchanged across each step (`/v1/nodes?output=verbose`)
- [ ] No crashloop on rolling restart; **gradient upgraded first and soaked on 1.37.7** before any other tenant
- [ ] A post-1.37 filesystem backup completes and (if any tenants exist) includes INACTIVE tenants

**Implementation Note**: Upgrade **gradient.soev.ai first, on its own**, and confirm it's healthy on 1.37.7 before touching staging or any customer tenant. Pause before Phase 4.

---

## Phase 4: soev-gitops — collection→tenant migration Job

### Overview
Fork the flat-index harness into `weaviate-mt-migration`: read each legacy class, map to `(collection, tenant)`, batch-insert into `with_tenant`, verify, delete the legacy class. Crash-safe + idempotent + `--dry-run`. Pre-migration backup baked in.

### Changes Required

#### 1. Migration script
**File**: `soev-gitops/scripts/migration/weaviate-mt-migration/migrate.py` (new, forked from flat-migration)
**Changes**: Reuse connection, `read_all_objects(include_vector=True)`, batch reinsert, checkpoint/replay. Replace flat-recreate with:
- Determine target `(coll, tenant)` from the legacy class name via the **same** `map_collection` logic (vendored into the script).
- `ensure_mt_collection(coll)` (create with `multi_tenancy(enabled=True)` + property schema + index config) and `coll.tenants.create([tenant])` if absent.
- Batch into `coll.with_tenant(tenant)` preserving UUIDs/vectors/properties (cursor `iterator(include_vector=True)` + `batch.fixed_size`).
- Verify tenant count == source count, then `delete_class(legacy)`. Checkpoint before delete.
- Legacy class discovery: all classes except the 6 MT/meta names (covers `C<uuid>`, `File_*`, `Web_search_*`, `User_memory_*`, hash classes).
**Note**: this is Weaviate's official collection→tenant migration pattern.

#### 2. Job template (generalize namespace)
**File**: `scripts/migration/weaviate-mt-migration/job.yaml.template` (new)
**Changes**: Add `__NAMESPACE__` placeholder (flat template hardcodes `open-webui-__TENANT__`) so intermax (`gradient-soev-<tenant>`) works. Keep hardened pod spec, `__TENANT__-weaviate` host, ports 8080/50051, `backoffLimit: 0`, `ttlSecondsAfterFinished`.

#### 3. Pre-migration backup
**File**: same dir — `prebackup.sh` (or a Job init step)
**Changes**: POST `/v1/backups/filesystem` with id `premigration-<ts>` + poll to SUCCESS (reuse the block from `helm/tenant-backup/templates/configmap.yaml:45-117`) before the destructive run.

#### 4. Build/launch/check
**Files**: `Dockerfile` (python:3.12-slim + `weaviate-client==4.20.3 requests packaging`), `launch.sh`, `check.sh`, `README.md`
**Changes**: Build/push `ghcr.io/gradient-ds/weaviate-mt-migration:v1.0.0` (buildx `linux/amd64`). `launch.sh` does `sed __TENANT__/__NAMESPACE__/__IMAGE__ | kubectl apply`; `check.sh` reads Job status + execs Weaviate for `/v1/schema` (expect ≤6 classes) and tenant counts.

### Success Criteria

#### Automated Verification:
- [ ] `migrate.py --dry-run` reports planned (class → collection/tenant) moves without mutating (user runs against a scratch instance)
- [ ] Script unit test for the vendored `map_collection` matches the open-webui copy: `python -m pytest scripts/migration/weaviate-mt-migration/ -q`
- [ ] Image builds + pushes to GHCR

#### Manual Verification:
- [ ] On a scratch Weaviate seeded with legacy classes: post-run `/v1/schema` shows only the 5 MT + `Knowledge_bases`; per-tenant counts == pre-run per-class counts
- [ ] Vectors preserved (spot-check a `near_vector` returns the same neighbors)
- [ ] Crash-safety: kill mid-run, re-run replays checkpoint, no data loss/dup
- [ ] Idempotent: second run is a no-op

**Implementation Note**: Pause before touching any real tenant (Phase 5).

---

## Phase 5: Cutover on gradient (test track)

### Overview
Exercise the whole sequence end-to-end on gradient.soev.ai (on `-test` images, all four images auto-update together).

### Changes Required (operational, runbook)
**File**: `soev-gitops/thoughts/shared/commands/2026-06-03-weaviate-mt-cutover-gradient.md` (new)
1. Merge `feat/weaviate-tenancy` (open-webui + genai-utils) → `test`; CI publishes `test-<sha>-<rn>` images; Flux rolls gradient OWUI + agents-api + search-api + loader-worker (flag still **OFF** → no behavior change).
2. Upgrade gradient Weaviate to 1.37.7 (Phase 3 steps) if not already.
3. Pre-migration backup (`premigration-<ts>`), verify SUCCESS.
4. Run `weaviate-mt-migration` Job for gradient; `check.sh` until complete.
5. Flip both flags in **one commit**: `ENABLE_WEAVIATE_MULTITENANCY_MODE=true` on the OWUI HelmRelease values **and** `weaviate_multitenancy_enabled: true` on the agent-stack values. Flux applies together; pods roll; dual-read covers the gap.
6. Verify retrieval (KB search, chat-file, web search, memory, agent search) returns correct results.
7. After a soak, set `WEAVIATE_MT_LEGACY_FALLBACK=false` for gradient; confirm everything still resolves (i.e. migration was complete).

### Success Criteria

#### Automated Verification:
- [ ] `flux get hr -n open-webui-gradient` Ready; pods healthy
- [ ] `GET /v1/schema` on gradient-weaviate shows ≤6 classes

#### Manual Verification:
- [ ] KB retrieval, chat-file attach, web search, user memory, and agent (genai-utils) search all return correct results post-flip
- [ ] OneDrive/Google Drive KB metadata filtering still works (e.g. `onedrive_drive_id` filter)
- [ ] No retrieval gap during the flag flip (dual-read confirmed via a just-uploaded doc)
- [ ] With fallback off, no missing-data errors (migration completeness confirmed)
- [ ] Rollback rehearsed: flipping the flag back returns to legacy reads cleanly

**Implementation Note**: Gradient is the gate. Do not proceed to customer tenants until this is clean and rollback is proven.

---

## Phase 6: Rollout — semver tenants, then pinned

### Overview
Promote to the auto-updating semver tenants, then manually bump the pinned ones.

### Changes Required (operational)
**File**: `soev-gitops/thoughts/shared/commands/2026-06-03-weaviate-mt-rollout.md` (new)
1. Cut a **semver release** (`vX.Y.Z`) of all four images → demo, haagsebeek, kwink auto-update (flag OFF); haute-equipe gets OWUI+loader only (no agent stack — agents flag N/A). Per tenant: upgrade Weaviate → backup → migrate → flip flag(s) → verify → drop fallback. **haute-equipe** (3440 classes) is the big one — schedule a window, watch memory.
2. **Pinned tenants** (octobox, mkbot on previder; soev-max on intermax; bzk-ministerie on nebul): manually bump image tags to the released version, then run the same per-tenant sequence on a chosen date. (mkbot is suspended; bzk OWUI has no agent stack image marker — check coupling per tenant.)
3. Once every tenant is on 1.37.7 + MT, bump `tenants/base/values.yaml:42` to `1.37.7` and remove per-tenant tag overrides; later, delete the legacy `weaviate.py` path + fallback flag.

### Success Criteria

#### Automated Verification:
- [ ] Each tenant's Flux HR Ready post-flip; `/v1/schema` ≤6 classes
- [ ] Backups green after each tenant's cutover

#### Manual Verification:
- [ ] Retrieval verified per tenant (esp. haute-equipe under load — confirm the per-class memory pressure is gone)
- [ ] Pinned tenants migrated on their scheduled dates without auto-update surprises
- [ ] Base values consolidated; legacy connector removal PR opened only after all targeted tenants verified

**Implementation Note**: Per-tenant pause/verify; never batch customer cutovers blindly.

---

## Testing Strategy

### Unit Tests
- `map_collection` parity (open-webui ↔ genai-utils ↔ migration script) — identical outputs for the full prefix matrix incl. `knowledge-bases`, 63 vs 64-hex, KB-UUID, edge cases.
- Index-config selection per collection under BQ on/off.
- Factory returns legacy vs MT connector by flag.

### Integration / Cutover Tests (manual, against scratch + gradient)
- Seed legacy classes → run migration → assert schema collapses to ≤6 classes, counts + vectors preserved.
- Flag-flip with dual-read: a doc written pre-migration (legacy) and one written post-flip (MT tenant) are both retrievable.
- Full retrieval matrix: KB search, chat-file, web search, memory, agent search, Drive metadata filters.

### Manual Testing Steps
1. Local stack, flag OFF → confirm zero behavior change.
2. Local stack, flag ON, scratch Weaviate → upload KB, verify `Knowledge` collection + KB-UUID tenant in `/v1/schema`.
3. Gradient cutover → full matrix + rollback rehearsal.
4. haute-equipe → memory/latency before vs after.

## Performance Considerations
- Per-tenant dedicated index → faster queries than a filtered monolith; eliminates the per-class HNSW memory multiplied across thousands of classes.
- 1.37 dynamic lazy-shard-load (threshold 1000 shards / 100 GB) protects against the schema-update OOM (#10322) in the many-tenant world.
- Migration is I/O heavy (read-all-with-vectors + reinsert) — run one instance at a time, off-peak, with the existing checkpointing; haute-equipe needs a real window.
- Keep tenants **ACTIVE** on ≤1.36; only after 1.37 do INACTIVE tenants get backed up (offloading still out of scope).

## Migration Notes
- Tenant key = raw `collection_name`; legacy class name = `_legacy_sanitize(collection_name)`. Both deterministic → dual-read needs no state.
- Cloud-sync/Drive data rides along in the `Knowledge`/`File` schemas (full property set required).
- `Knowledge_bases` meta-index is migrated/kept as a standalone non-MT collection.
- Backups: take one before each Weaviate version step AND before each tenant migration.

## References
- Research: `open-webui/thoughts/shared/research/2026-06-03-weaviate-collections-vs-tenants-refactor.md`
- Prior art: `open-webui/thoughts/shared/plans/2026-04-06-cloud-kb-single-collection.md`, `.../2026-05-25-bq-disable-and-no-per-file-collections-for-kb-uploads.md`, `soev-gitops/thoughts/shared/plans/2026-05-17-weaviate-flat-index-migration.md`
- Pattern: `open-webui/backend/open_webui/retrieval/vector/dbs/qdrant_multitenancy.py:98-161`, `milvus_multitenancy.py:64-126`
- Connector: `open-webui/backend/open_webui/retrieval/vector/dbs/weaviate.py`
- genai-utils touchpoints: `agents/retrieval/providers/_weaviate_adapter.py:71-202`, `_weaviate_naming.py`, `deploy/bootstrap.py:812-929`
- gitops: `scripts/migration/weaviate-flat-migration/`, `helm/tenant-backup/templates/configmap.yaml:45-117`, `tenants/base/values.yaml:38-42`
- Weaviate docs: [migrate (collection→tenant)](https://docs.weaviate.io/weaviate/manage-collections/migrate), [multi-tenancy](https://docs.weaviate.io/weaviate/manage-collections/multi-tenancy), [release-notes](https://docs.weaviate.io/weaviate/release-notes), [backups](https://docs.weaviate.io/deploy/configuration/backups)
