---
date: 2026-06-03T00:00:00+02:00
researcher: Lex Lubbers (@lexlubbers)
git_commit: 977df406c3bcea42b80456c1c60c8a11fed23b95
branch: test
repository: Gradient-DS/open-webui (+ genai-utils, soev-gitops)
topic: "Weaviate collections-vs-tenants: how we should model vectors, connector provenance, refactor design, cutover + migration plan"
tags: [research, weaviate, multi-tenancy, vector-db, open-webui, genai-utils, gitops, migration]
status: complete
last_updated: 2026-06-03
last_updated_by: Lex Lubbers
---

# Research: Weaviate Collections-vs-Tenants Refactor

**Date**: 2026-06-03 (Europe/Amsterdam)
**Researcher**: Lex Lubbers (@lexlubbers)
**Git Commit**: `977df406c3bcea42b80456c1c60c8a11fed23b95` (open-webui `test`)
**Repositories**: `open-webui` (connector), `genai-utils` (agents + Search API consumers), `soev-gitops` (deployment + migration)

## Research Question

We currently create a **new Weaviate collection per logical unit** — every KB, every uploaded file, every web search, every user's memory. That is the wrong primitive: a Weaviate *collection* is meant to define a *schema*, and isolated data subsets sharing that schema should be *tenants*. This costs compute and retrieval-time overhead and is unstable at scale. The session goals:

1. Get sharp on how we **should** be doing this.
2. Determine whether the Weaviate connector code is **ours or upstream's** (→ possible upstream PR).
3. Design the optimal refactor: one `File` / `Knowledge` / `WebSearch` / `UserMemory` collection per Weaviate instance, with **native multi-tenancy** isolating the individual data.
4. Design a cutover where **new** data is created the right way the moment the image lands, while **old** data can be migrated whenever.
5. Produce a migration plan, starting with running backups over previder.

---

## Summary (TL;DR)

- **How it should work (Goal 1):** Weaviate's official guidance is unambiguous — past ~20 collections you should switch to multi-tenancy. A *collection* defines schema/index config; a *tenant* is a data partition that gets its own dedicated shard + vector index (one shard per tenant). Tenants are far cheaper than collections, scale to 100k+ per cluster, query faster (each hits only its own small index, not a giant filtered one), and can be tiered ACTIVE→INACTIVE→OFFLOADED(S3). Source: [Weaviate scaling-limits docs](https://docs.weaviate.io/weaviate/starter-guides/managing-collections/collections-scaling-limits), [best-practices](https://docs.weaviate.io/weaviate/best-practices), [MT architecture blog](https://weaviate.io/blog/weaviate-multi-tenancy-architecture-explained).

- **Whose code is it (Goal 2):** The Weaviate connector is an **upstream file we have heavily forked**. It was added upstream as PR #14747 (`b8728064d feat: add support for Weaviate vector database (#14747)`) and self-host support came from upstream PR #20620. But the current head of `weaviate.py` is **ours** — authored by Lex Lubbers (2026-05-25), adding the explicit schema, metadata sanitization, and the `_FLAT_INDEX_PREFIXES` + BQ-quantization policy. **Upstream has *no* Weaviate multi-tenancy variant.** Upstream *does* ship `qdrant_multitenancy.py` and `milvus_multitenancy.py` (selected by `ENABLE_QDRANT_MULTITENANCY_MODE` / `ENABLE_MILVUS_MULTITENANCY_MODE`), which consolidate into ~5 shared collections and isolate by a `tenant_id`/`resource_id` *property*. **A `weaviate_multitenancy.py` is the missing sibling — and it is a clean upstream PR candidate.**

- **The refactor (Goal 3):** Add a `weaviate_multitenancy.py` connector + `ENABLE_WEAVIATE_MULTITENANCY_MODE` flag mirroring the qdrant/milvus pattern, but using **Weaviate-native** `multiTenancyConfig`. Five schema collections — `Knowledge`, `File`, `WebSearch`, `UserMemory`, `HashBased` — each created with `multi_tenancy(enabled=True, auto_tenant_creation=True, auto_tenant_activation=True)` and a **dynamic** vector index (flat→HNSW auto-upgrade). The existing OWUI `collection_name` (`file-…`, `user-memory-…`, `web-search-…`, KB-UUID) maps deterministically to **(collection, tenant)**. This satisfies "collections isolate schema, tenants isolate data" exactly.

- **Cutover (Goal 4):** Ship the new connector behind the flag; on the new image, **all new writes** go to MT collections+tenants immediately. A **dual-read shim** in the connector reads MT first and falls back to the legacy per-class collection, so un-migrated old data keeps working and you can migrate "whenever." Remove the shim once a tenant is fully migrated.

- **Migration (Goal 5):** Reuse the existing `soev-gitops/scripts/migration/weaviate-flat-migration/` harness (it already does schema-read → iterate-with-vectors → batch-reinsert → checkpoint/crash-safety). Adapt it from "recreate class as flat+BQ" to "read legacy class → ensure (MT collection, tenant) → batch-insert into `collection.with_tenant(tenant)` → delete legacy class." Run as a K8s Job per Weaviate instance. **Back up every previder Weaviate first.** Note the v1.37 backup caveat (below): on our 1.35.x, keep tenants ACTIVE.

- **Cross-repo coupling (critical):** This is **not** an open-webui-only change. `genai-utils` reads these same collections directly — the agents provider (`weaviate_openwebui.py` + `_weaviate_adapter.py`) and the Search API (`api/backends/chunk/weaviate.py`) call `collections.get(name)` and would need `.with_tenant(tenant)`, and `genai-utils/.../_weaviate_naming.py` (which must mirror OWUI's name mapping) would need the same (collection, tenant) logic. **A coordinated, version-locked release across open-webui + genai-utils is required.**

---

## Detailed Findings

### Current state — the collection explosion (the problem, precisely)

The active Weaviate connector at `open-webui/backend/open_webui/retrieval/vector/dbs/weaviate.py` treats **every OWUI `collection_name` as a distinct Weaviate class**. `_sanitize_collection_name` (`weaviate.py:122-142`) turns the logical name into a class name (dash→underscore, capitalize, `C`-prefix if it starts with a digit), and `insert`/`upsert` call `_ensure_collection` → `collections.create(...)` per name (`weaviate.py:155-218`).

The logical names that become classes:
- **KB:** the KB UUID (sanitized to `C<uuid_underscored>`). One class per knowledge base. (`routers/knowledge.py`, sync workers `services/sync/base_worker.py:510,541,…`.)
- **File:** `file-{file_id}` → `File_<id>`. Created on every direct file upload (`routers/files.py:887`, `routers/retrieval.py:1652`).
- **Web search:** `web-search-{sha256(queries)}` → `Web_search_<hash>` (`routers/retrieval.py:2520`), plus content/url hash classes (`:2004,:2045`).
- **Memory:** `user-memory-{user_id}` → `User_memory_<id>` (`routers/memories.py`).

On `haute-equipe` this measured **3440 classes** (2906 `File_*`, ~400 `Web_search_*`), each carrying its own HNSW graph and buffers, pinning Weaviate's heap at `GOMEMLIMIT=11GiB` and making 10-PDF uploads take 3+ minutes (`soev-gitops/thoughts/shared/plans/2026-05-17-weaviate-flat-index-migration.md:5-14`). This is the exact failure mode Weaviate documents for "thousands of collections."

**Prior soev mitigations (partial, do not solve the root cause):**
- *April 2026 — cloud-KB single collection* (`open-webui/thoughts/shared/plans/2026-04-06-cloud-kb-single-collection.md`): stop creating per-file collections for **cloud-synced** KBs; write vectors into the KB collection and filter by `file_id`. **Landed.**
- *May 2026 — BQ toggle + no per-file collections for KB uploads* (`open-webui/thoughts/shared/plans/2026-05-25-bq-disable-and-no-per-file-collections-for-kb-uploads.md`): extend the above to **local** KB uploads; make BQ an env flag (`ENABLE_WEAVIATE_BQ_QUANTIZATION`, default off). Phases 1–3 code landed; Phase 4 (lazy cleanup) + manual verification pending.
- *May 2026 — flat-index migration* (`soev-gitops/.../2026-05-17-weaviate-flat-index-migration.md`): a **band-aid** — keep per-file/web-search/memory classes but switch them HNSW→flat+BQ to cut per-class memory. Explicitly *rejected* consolidation as "too invasive." Built; image push + per-tenant runs were pending.

**Key point:** None of the prior work uses Weaviate **native** multi-tenancy. The April/May consolidation is "one collection per KB (UUID) + `file_id` property filter." The fundamental per-KB / per-chat-file / per-web-search / per-user-memory class explosion remains. This research proposes the proper fix the prior plans deferred.

### Goal 1 — How we should model it (Weaviate's official guidance)

- **The 20-collection rule:** *"If you are creating more than 20 collections, take a moment to consider if multi-tenancy might be utilized."* ([scaling-limits](https://docs.weaviate.io/weaviate/starter-guides/managing-collections/collections-scaling-limits))
- **Documented per-collection cost:** each collection has its own definition, indexes, and storage → more memory + disk; schema changes must be applied per-collection; "managing thousands of collections becomes nearly impossible." On startup Weaviate loads data from all shards (mitigated by lazy shard loading from v1.36.6).
- **Tenant model:** one **shard per tenant**, sharing the collection's schema/index config. *"Each tenant has a dedicated, high-performance vector index, which results in faster query speeds"* and definition updates apply to all tenants at once.
- **Why filtering one big collection is *also* wrong:** with a single monolithic index you'd query across all vectors while typically needing <0.01% — partition shards (tenants) avoid this. (This is why we want native MT, not just "one big `Knowledge` collection + `knowledge_id` filter.")
- **Tenant activity states:** `ACTIVE` (RAM, was `HOT`) / `INACTIVE` (local disk, was `COLD`, fast to reactivate) / `OFFLOADED` (S3, was `FROZEN`). Renamed in v1.26. Offload target is **AWS S3 only**.
- **Scale:** ~170k active tenants on a 9-node cluster (~18–19k/node); 1M concurrent tenants on ~20 nodes; "50,000+ active shards per node." Limits are OS/RAM-bound (open-file limits), not a hard published cap.
- **Memory mechanisms that make this cheap:** lazy shard loading, lazy segment loading, and **dynamic** vector indexes that auto-switch flat→HNSW per tenant past a vector-count threshold.

### Goal 2 — Connector provenance (ours vs upstream)

Git history of `open-webui/backend/open_webui/retrieval/vector/dbs/weaviate.py`:
- **Origin = upstream.** First commit `b8728064d feat: add support for Weaviate vector database (#14747)`; self-host `connect_to_custom` from `9d642f635 … (#20620)`. Both are upstream Open WebUI PR numbers. Remotes confirm `upstream → github.com/open-webui/open-webui`, `origin → github.com/Gradient-DS/open-webui`.
- **Current head = ours.** `git log -1` author is **Lex Lubbers (2026-05-25)**. Our additions: explicit TEXT schema in `_create_collection` (`weaviate.py:155-182`, fixes OneDrive/PDF type conflicts), `_sanitize_property_name`/`_sanitize_metadata_keys` (`:49-72`), and `_FLAT_INDEX_PREFIXES` + `_build_vector_config` BQ policy (`:75-95`) gated by `ENABLE_WEAVIATE_BQ_QUANTIZATION`.
- **No upstream Weaviate MT variant exists.** The factory (`vector/factory.py:79-82`) has a single `WEAVIATE` case with no flag. Compare the Qdrant/Milvus cases (`:16-38`) which branch on `ENABLE_QDRANT_MULTITENANCY_MODE` (default **true**, `config.py:2654`) / `ENABLE_MILVUS_MULTITENANCY_MODE` (default false, `config.py:2642`) to load `*_multitenancy.py`.

**Conclusion:** A `weaviate_multitenancy.py` + `ENABLE_WEAVIATE_MULTITENANCY_MODE` is a natural, additive contribution that fills an obvious gap in the upstream connector family. It is a **strong upstream PR candidate**. (Caveat: upstream's qdrant/milvus variants use a *property-based* tenant id, not the DB's native MT; our Weaviate version using native `multiTenancyConfig` is arguably *better* and upstream-worthy, but we should expect review discussion about that divergence. We can keep our fork on native MT regardless.)

### The existing upstream pattern to mirror — `qdrant_multitenancy.py` / `milvus_multitenancy.py`

Both consolidate the per-logical-name explosion into **five shared collections** and route by mapping the OWUI name to `(shared_collection, tenant_or_resource_id)`:

```
user-memory-*      -> MEMORY_COLLECTION
file-*             -> FILE_COLLECTION
web-search-*       -> WEB_SEARCH_COLLECTION
63-char hex hash   -> HASH_BASED_COLLECTION   (YouTube/web URL content)
everything else    -> KNOWLEDGE_COLLECTION    (KB UUIDs)
```

Reference: `qdrant_multitenancy.py:98-131` (`_get_collection_and_tenant_id`) and `milvus_multitenancy.py:64-86` (`_get_collection_and_resource_id`). Both carry an explicit warning: this mapping is coupled to OWUI's naming conventions and breaking it risks **data corruption** (`qdrant_multitenancy.py:105-111`). Qdrant uses a `tenant_id` keyword payload index with `is_tenant=True` (`:153-161`); Milvus uses a `resource_id` VARCHAR + filter. Every CRUD op filters by that id (`search`, `query`, `get`, `delete`, `delete_collection`).

**Our Weaviate version differs in one important way:** instead of a `tenant_id` *property filter* over one big index, we use Weaviate's **native tenants** — `collection.with_tenant(tenant)` — giving each logical unit its own shard + index. Same five-collection mapping, native isolation.

### Goal 3 — Recommended refactor design

**New file:** `open-webui/backend/open_webui/retrieval/vector/dbs/weaviate_multitenancy.py`, selected via `ENABLE_WEAVIATE_MULTITENANCY_MODE` added to `config.py` and a branch in `factory.py:79-82` (mirror the Qdrant case exactly).

**Schema collections (created once, lazily):**

| Collection | Holds | Tenant key (from OWUI `collection_name`) |
|---|---|---|
| `Knowledge` | KB chunks (incl. KB-bound file chunks, filtered by `file_id`) | KB UUID |
| `File`      | ad-hoc chat-upload file chunks (`file-…` not in a KB) | file id |
| `WebSearch` | web-search result chunks (TTL'd) | `sha256(queries)` |
| `UserMemory`| per-user memory chunks | user id |
| `HashBased` | YouTube/URL content (63-char hex) | the hash |

Each created with:
```python
client.collections.create(
    name="Knowledge",
    multi_tenancy_config=Configure.multi_tenancy(
        enabled=True, auto_tenant_creation=True, auto_tenant_activation=True),
    vector_config=Configure.Vectors.self_provided(
        vector_index_config=Configure.VectorIndex.dynamic()),  # flat->HNSW per tenant
    properties=[... same explicit TEXT props as weaviate.py:162-181 ...],
)
```

**Mapping function** (the heart, mirror `qdrant_multitenancy._get_collection_and_tenant_id`):
`collection_name -> (mt_collection, tenant)` using the same prefix rules. Keep the upstream warning comment verbatim — the coupling risk is identical.

**Per-op behavior:**
- `insert`/`upsert(collection_name, items)` → resolve `(coll, tenant)`; ensure collection exists; `auto_tenant_creation=True` means the tenant is created on first write; `coll.with_tenant(tenant).data.insert_many(...)`.
- `search`/`query`/`get`/`delete(collection_name, …)` → resolve `(coll, tenant)`; `coll.with_tenant(tenant).query.…`. `auto_tenant_activation=True` re-activates INACTIVE/OFFLOADED tenants on access.
- `has_collection` → tenant exists (and non-empty) in the collection.
- `delete_collection(collection_name)` → `coll.tenants.remove([tenant])` (drops the shard), **not** dropping the schema collection.
- `reset` → delete the five schema collections.

**Why `dynamic()` index:** small KBs/files/searches stay on a cheap flat index; large tenants auto-upgrade to HNSW. This is the principled replacement for the `_FLAT_INDEX_PREFIXES` + BQ hand-tuning — and it removes the per-class index guesswork entirely. (BQ can still be layered on the HNSW/flat config if desired.)

**Interface fit:** `VectorDBBase` passes a single `collection_name` per call (`vector/main.py:23-86`), so the entire MT mapping is internal to the connector — **no router/caller changes in open-webui** beyond the factory flag. That is the same blast-radius the qdrant/milvus variants enjoy, which is why this is clean.

**Important scope note:** the "tenant" here is the **individual KB/file/search/user**, *not* the customer. Customer isolation is already physical — each OWUI customer has its **own Weaviate instance** (per-tenant StatefulSet; confirmed across previder/intermax/nebul, `tenants/base/values.yaml:38-42`, `semitechnologies/weaviate:1.35.0`). So within one customer's Weaviate, "one tenant per KB" replaces "one collection per KB."

### Goal 4 — Cutover (new data right, old data later)

The requirement ("new data the proper way the moment the image lands; migrate old whenever") **requires read-time backward compatibility**, because previder OWUI auto-updates via ImagePolicy (below) — the image will land before any migration finishes.

**Dual-read shim in the connector:** for `collection_name=X`:
1. Compute `(mt_collection, tenant)` and the legacy class name `sanitize(X)` — both deterministic from `X`.
2. Writes → always MT (new world).
3. Reads → if `tenant` exists in `mt_collection`, read it; **else if** legacy class `sanitize(X)` exists, read legacy. (Optionally lazily migrate-on-read.)

This makes the image safe to land before migration, lets old data be migrated on any schedule, and the shim is removed per-tenant once migration is verified complete. Gate the fallback behind a flag (e.g. `WEAVIATE_MT_LEGACY_FALLBACK`, default on during transition) so it can be turned off cleanly.

**ImagePolicy reality (confirmed in gitops):**
- **Auto-updating (previder):** gradient (`open-webui-test`), staging (`open-webui-dev`), haagsebeek/demo/kwink/haute-equipe (`open-webui-semver`); intermax soev-test (`open-webui-test`). These get the new image automatically → **must** be dual-read-safe.
- **Pinned / manual:** octobox, mkbot (previder); intermax soev-max; nebul bzk-ministerie. These can be deferred and migrated on a chosen date. This matches your read: "only previder is on an imagepolicy; the others are pinned."

### Goal 5 — Migration plan

**Step 0 — Back up every previder Weaviate first.** Backups use Weaviate's native `/v1/backups/filesystem` API, driven by either the `tenant-backup` Helm chart (previder: `helm/tenant-backup/`, daily `0 2 * * *`/30d, weekly `0 3 * * 0`/56d, PVC on `previder-backup-pdc2`) or standalone `backup-cronjob.yaml` (intermax/nebul). Trigger an on-demand full backup per tenant before touching their data.
- **v1.37 caveat:** before v1.37, `/v1/backups` includes **only ACTIVE tenants** (INACTIVE/OFFLOADED are skipped). We run **1.35.0 / 1.35.2** → after migration, **keep all tenants ACTIVE** (don't offload) until we either upgrade to ≥1.37 or add an "activate-all-then-backup" step. This is a hard operational constraint for the backup runbooks. ([backups docs](https://docs.weaviate.io/deploy/configuration/backups))

**Step 1 — Migration job (adapt the existing harness).** `soev-gitops/scripts/migration/weaviate-flat-migration/migrate.py` is an excellent base — it already: connects v4 with long timeouts, lists target classes by prefix, `read_all_objects(include_vector=True)`, `batch_reinsert` preserving UUIDs+vectors, per-class checkpoint before destructive delete, idempotent skip, crash-replay (`migrate.py:215-413`). Fork it to `weaviate-mt-migration/`:
- Replace `create_class_flat_bq` with `ensure_mt_collection(coll)` + `coll.tenants.create([tenant])`.
- Replace per-class recreate with: read legacy class `X` → `(coll, tenant) = map(X)` → batch into `coll.with_tenant(tenant)` with original UUIDs/vectors/properties → verify count → `delete_class(X)`.
- Keep checkpointing/idempotency/`--dry-run`/`--only`.
- This is the official Weaviate **collection→tenant** migration (cursor `iterator(include_vector=True)` + `batch` into `with_tenant`); see [manage-collections/migrate](https://docs.weaviate.io/weaviate/manage-collections/migrate). Cross-references travel as properties.

**Step 2 — Rollout order (bulk is previder).** Stage on `staging` first, then a low-risk previder tenant (e.g. `demo`), then the big one (`haute-equipe`, the 3440-class instance), then the rest of previder. Run one Weaviate instance at a time via a K8s Job (the harness already does per-instance). Deferred/pinned tenants (octobox, mkbot, intermax soev-max, nebul bzk) migrate when their image is manually bumped.

**Step 3 — Decommission the shim.** After a tenant's legacy classes are migrated and verified (counts match, retrieval spot-checked), turn off `WEAVIATE_MT_LEGACY_FALLBACK` for that tenant; once all are done, delete the shim and the legacy `weaviate.py` path.

### Cross-repo coupling — genai-utils MUST move in lockstep (critical)

`genai-utils` reads the **same** Weaviate instances directly and will break the moment the schema model changes unless updated together:

- **Agents direct provider** — `agents/retrieval/providers/weaviate_openwebui.py` + `_weaviate_adapter.py` call `collections.get(sanitize(collection_name))` then v4 query. Under MT they need `.with_tenant(tenant)` and the same `(collection, tenant)` mapping. The provider gets `collection_name` from OWUI's ACL endpoint `/api/v1/internal/retrieval/accessible-kbs` (`AccessibleKb.collection_name`, `_openwebui_clients.py:25-35`).
- **Name sanitizer** — `agents/retrieval/providers/_weaviate_naming.py` exists **specifically to mirror OWUI's `_sanitize_collection_name`** (its docstring says so). It must gain the identical (collection, tenant) logic, or OWUI must expose the resolved pair over the ACL API.
- **Search API** — `api/backends/chunk/weaviate.py` resolves a datasource to `collections.get(datasource.collection_name)` (`:438-446`). MT-targeted datasources need `.with_tenant(...)`; `DatasourceConfig` (`api/config/models.py:64-79`) would need a tenant field.
- All three use `weaviate-client>=4.10` (v4), so the `.with_tenant` API is available everywhere.

**Implication:** treat this as a **coordinated release** — OWUI connector + genai-utils provider/Search API shipped together, version-locked, with the dual-read fallback on both sides during the window. The single shared mapping (collection + tenant from an OWUI `collection_name`) should ideally live in one canonical place and be duplicated verbatim (as `_weaviate_naming.py` already duplicates `_sanitize_collection_name` today).

---

## Code References

- `open-webui/backend/open_webui/retrieval/vector/dbs/weaviate.py:122-218` — current per-name class creation (the thing to replace)
- `open-webui/backend/open_webui/retrieval/vector/dbs/weaviate.py:75-95` — `_FLAT_INDEX_PREFIXES` + BQ policy (ours; superseded by `dynamic()` index under MT)
- `open-webui/backend/open_webui/retrieval/vector/dbs/qdrant_multitenancy.py:98-161` — the pattern to mirror (name→(collection,tenant) + MT collection creation)
- `open-webui/backend/open_webui/retrieval/vector/dbs/milvus_multitenancy.py:64-126` — second reference implementation
- `open-webui/backend/open_webui/retrieval/vector/factory.py:16-84` — connector dispatch; add `ENABLE_WEAVIATE_MULTITENANCY_MODE` branch at `:79-82`
- `open-webui/backend/open_webui/config.py:2642,2654` — existing MT flags (default true for qdrant); add Weaviate flag near `:2657-2670`
- `genai-utils/agents/retrieval/providers/weaviate_openwebui.py:641-707` + `_weaviate_adapter.py:48-202` — agents read path needing `.with_tenant`
- `genai-utils/agents/retrieval/providers/_weaviate_naming.py:14-30` — sanitizer that mirrors OWUI; must mirror new mapping
- `genai-utils/api/backends/chunk/weaviate.py:438-446` — Search API collection targeting
- `soev-gitops/scripts/migration/weaviate-flat-migration/migrate.py` — migration harness to fork for collection→tenant
- `soev-gitops/scripts/survey-weaviate-classes.sh` — inventory current classes per tenant (use to size the migration)
- `soev-gitops/tenants/base/values.yaml:38-42` — Weaviate version pin (`1.35.0`); per-tenant instances
- `soev-gitops/helm/tenant-backup/` — backup chart (Step 0)

## Architecture Documentation

- **Deployment:** one Weaviate StatefulSet per OWUI tenant (`<tenant>-weaviate`); no shared Weaviate. previder ~9 + `mkbot-agent` + standalone `aire`; intermax 2; nebul 1. Customer isolation = separate instance; in-instance isolation today = separate collection (to become: separate tenant).
- **Index today:** KB collections = default HNSW; `File_*`/`Web_search_*`/`User_memory_*` = HNSW, or flat+BQ if `ENABLE_WEAVIATE_BQ_QUANTIZATION` (default off).
- **Web search TTL:** `WEAVIATE_WEB_SEARCH_TTL_MINUTES` default 1440 — these are ephemeral; as tenants they churn (create/delete) rather than accumulate classes.
- **Client:** open-webui connector is **sync** v4 (`connect_to_custom`); genai-utils is **async** v4 (`use_async_with_custom`).

## Historical Context (from thoughts/)

- `open-webui/thoughts/shared/research/2026-04-06-cloud-kb-single-collection.md` + plan — stop per-file collections for cloud KBs via KB-collection + `file_id` filter (landed).
- `open-webui/thoughts/shared/research/2026-05-25-bq-quantization-and-per-file-collections.md` + plan — BQ toggle + no per-file collections for KB uploads (Phases 1–3 landed; note `POST /files/` still always makes `file-<id>` for chat uploads).
- `soev-gitops/thoughts/shared/plans/2026-05-17-weaviate-flat-index-migration.md` — flat+BQ band-aid; **explicitly rejected** consolidation as too invasive. This research revisits that rejected direction with native MT.
- `genai-utils/thoughts/shared/research/2026-05-08-openwebui-direct-vs-search-api.md` — the two genai-utils read paths over the same Weaviate.
- Existing memory: `factoid_weaviate_135_bq_migration_cost.md` (recommends MT over many classes on haute-equipe), `factoid_owui_weaviate_class_name_sanitization.md` (`C<uuid_underscored>`).

## Decisions (locked 2026-06-03)

- **D1 — Isolation primitive:** ✅ **Native Weaviate multi-tenancy** (`multiTenancyConfig` + real tenants/shards), *not* the upstream qdrant/milvus property-filter style.
- **D2 — First-pass scope:** ✅ **All five collections** — `Knowledge`, `File`, `WebSearch`, `UserMemory`, `HashBased` — converted together (File_* is the bulk of the class explosion, so it must be in scope).
- **D3 — Upstream PR:** ⏸️ **Decide later.** Build/stabilize on our fork first; revisit upstreaming `weaviate_multitenancy.py` after it's proven and previder is migrated.
- **D4 — Index config:** ✅ **Keep flat + BQ for now** (reuse the existing per-prefix policy, applied at MT-collection level). Revisit `dynamic()` (flat→HNSW per tenant) later if/when we have headroom to provision the compute.
- **D5 — Weaviate version:** ✅ **Upgrade to 1.37.7 (latest stable) first**, *before* the MT migration. Stepped, per Weaviate policy: `1.35.22 → 1.36.17 → 1.37.7` (latest patch at each minor, backup before each step). 1.37 gives INACTIVE-tenant backups + the dynamic lazy-shard-load fix for the many-tenant OOM (#10322) — both directly de-risk a many-shard MT world. Drop deprecated `DISABLE_LAZY_LOAD_SHARDS` from the chart if present.
- **D6 — Migration mechanism:** ✅ **Batched, like the flat-index migration** — fork the existing `weaviate-flat-migration` harness into a per-instance collection→tenant Job, run one tenant at a time.
- **D7 — Branches:** ✅ `feat/weaviate-tenancy` exists on **both** `open-webui` and `genai-utils`. Rollout order: merge to `test` → validate on gradient.soev.ai → cut a semver release → previder-prod semver tenants → bump pinned tenants last.

## Release & rollout mechanics (resolved)

### Image-policy reality (from soev-gitops)
Per tenant, OWUI + agents-api + search-api(`vector-db-api`) + loader-worker share one cadence — none mix auto/pinned:
- **`-test` (auto):** gradient, intermax soev-test
- **`-semver` (auto):** demo, haagsebeek, kwink, haute-equipe (haute-equipe runs OWUI+loader only, **no agent stack**)
- **`-dev` (auto):** staging
- **pinned:** octobox, mkbot (previder); soev-max (intermax); bzk-ministerie (nebul)

Markers: OWUI + loader-worker in `<tenant>/helmrelease.yaml`; agents-api + search-api in `<tenant>/helmrelease-agent-stack.yaml`. Automation is one `ImageUpdateAutomation` per tenant, `strategy: Setters`, whole-dir path.

**Footguns:** (1) Setters rewrites each image marker **independently** — a multi-image push is **not atomic**; expect minutes of version skew as each lands. (2) Three tag formats must be published to reach all tracks (`test-…`, `vX.Y.Z`, `dev-…`). (3) `vector-db-api` is the search-api repo name and lives in the agent-stack file. (4) octobox/bzk keep live automation objects with markers stripped — re-adding a marker to one image only would silently recreate skew.

### Cutover decouples behavior from image (resolves the non-atomic risk)
Because OWUI and genai-utils roll non-atomically, **do not tie the MT switch to image arrival.** Ship `ENABLE_WEAVIATE_MULTITENANCY_MODE` **default false** in both new images (+ a matching env on the agent-stack/search-api side). Image rollout is then behavior-neutral. Cut over by flipping the flag via a **single Helm values commit per tenant** (Flux applies it to both HelmReleases in one reconcile). The dual-read shim covers the few-seconds pod-roll gap. Flip back = instant rollback. Sequence per tenant: deploy flag-off images → upgrade Weaviate to 1.37.7 → run migration Job → flip flag on → verify → (later) drop legacy fallback.

## Open Design Questions (still to settle while planning)

1. **Dual-read window & lazy migrate-on-read:** batch Job only, or also migrate-on-read (amortized, self-healing) during the dual-read window?
2. **Canonical mapping location:** single source-of-truth for `collection_name -> (collection, tenant)` shared across open-webui + genai-utils (today `_weaviate_naming.py` duplicates OWUI's sanitizer verbatim — same approach, or a shared package?).
3. **BQ-on-flat at MT-collection level:** confirm the existing `File_*`/`Web_search_*`/`User_memory_*` flat+BQ policy maps cleanly onto 5 fixed collections (the index config is now set once per collection, not per logical name) — and whether `Knowledge` stays default HNSW or also goes flat+BQ.

## Related Research

- `open-webui/thoughts/shared/plans/2026-04-06-cloud-kb-single-collection.md`
- `open-webui/thoughts/shared/plans/2026-05-25-bq-disable-and-no-per-file-collections-for-kb-uploads.md`
- `soev-gitops/thoughts/shared/plans/2026-05-17-weaviate-flat-index-migration.md`
- `genai-utils/thoughts/shared/research/2026-05-08-openwebui-direct-vs-search-api.md`

## External Sources

- [Weaviate — Collections scaling limits (the 20-collection rule)](https://docs.weaviate.io/weaviate/starter-guides/managing-collections/collections-scaling-limits)
- [Weaviate — Best practices (collections vs tenants)](https://docs.weaviate.io/weaviate/best-practices)
- [Weaviate — Multi-tenancy operations](https://docs.weaviate.io/weaviate/manage-collections/multi-tenancy)
- [Weaviate — Tenant states & temperature (ACTIVE/INACTIVE/OFFLOADED)](https://docs.weaviate.io/weaviate/manage-collections/tenant-states)
- [Weaviate — Migrate data (collection→tenant cursor+batch)](https://docs.weaviate.io/weaviate/manage-collections/migrate)
- [Weaviate — Backups configuration (v1.37 active+inactive tenants)](https://docs.weaviate.io/deploy/configuration/backups)
- [Blog — Native multi-tenancy architecture](https://weaviate.io/blog/weaviate-multi-tenancy-architecture-explained)
- [Blog — Multi-tenancy with millions of tenants](https://weaviate.io/blog/multi-tenancy-vector-search)
- [Forum — multiTenancyConfig.enabled is immutable (no in-place conversion)](https://forum.weaviate.io/t/how-to-enable-multi-tenancy-on-the-existing-collection/9191)
