---
date: 2026-05-25T08:28:17+0000
researcher: Lex Lubbers
git_commit: 5caaf1dae68dca3c2694ba1f528c6d380a157b76
branch: feat/bq-weaviate-disabling
repository: open-webui
topic: "BQ quantization on Weaviate + per-file collections for local KB uploads"
tags: [research, codebase, weaviate, binary-quantization, per-file-collections, knowledge-bases, chat-input-menu, performance]
status: complete
last_updated: 2026-05-25
last_updated_by: Lex Lubbers
---

# Research: Disabling Weaviate BQ Quantization & Removing Per-File Collections for KB Uploads

**Date**: 2026-05-25T08:28:17+0000
**Researcher**: Lex Lubbers
**Git Commit**: 5caaf1dae68dca3c2694ba1f528c6d380a157b76
**Branch**: feat/bq-weaviate-disabling
**Repository**: open-webui

## Research Question

Two related fixes Lex wants to evaluate:

1. The 8s-per-collection creation cost observed in this fork is suspected to come from the Weaviate **binary quantization (BQ)** policy at `backend/open_webui/retrieval/vector/dbs/weaviate.py:82-90`. Is BQ the cause, and what does it take to roll back?

2. Every uploaded file currently gets its own per-file vector collection (`File_<file_id>`). What features actually depend on per-file collections? Lex's hypothesis: the only chat-side consumer is the "attach an individual file from a KB" path in the `+` / `#` input menu, and we already block that for cloud-sync KBs — so disabling it for local KBs too should let us stop creating per-file collections entirely (for the KB upload path).

## Summary

### #1 — BQ quantization

The BQ + flat-index policy is narrow and well-bounded: it is applied only to collections whose **sanitized class name** starts with `File_`, `Web_search_`, or `User_memory_`. KB collections (raw UUID class names) keep the Weaviate default HNSW. The policy is a single helper (`_build_vector_config`) wired into `_create_collection`, and is locked in by one parametrised test (`test_weaviate_index_policy.py`). No env-var/Helm plumbing exists — disabling BQ is a 2-file change.

I could not find a recorded measurement of "8s per collection" anywhere in `thoughts/` / `collab/`. The plausible mechanical reason is server-side: with BQ enabled, Weaviate has to set up extra index metadata at `collections.create()` time. The client-side code is pure builder objects; there is no per-collection timeout, retry, or vectorizer init that would account for it. None of the other potentially-slow steps (`connect_to_custom` handshake, `_ensure_collection` exists-check) scale with collection count.

### #2 — Per-file collections

There is **already a precedent and a written plan** for exactly this work, scoped to cloud-sync KBs: `thoughts/shared/research/2026-04-06-cloud-kb-single-collection.md` + `thoughts/shared/plans/2026-04-06-cloud-kb-single-collection.md`. Cloud-sync ingestion (`base_worker._split_embed_and_store`) now writes only to the KB collection and even gradually deletes legacy `file-<id>` collections during re-sync. The chat input menu's "show individual files in this KB" chevron is gated to `local`-only KBs.

Lex's hypothesis is **mostly right but with one critical caveat**: the *KB upload* path is not the only producer of `file-<id>` collections. The `POST /api/v1/files/` endpoint always creates a `file-<id>` collection on upload (before any KB membership is decided), and this is the dominant path for the one-off "drag a PDF into chat" use case. So we cannot stop creating per-file collections globally without breaking that flow.

The right framing is therefore: **stop creating per-file collections when a file is uploaded directly into a KB**, and migrate the KB-file chat-attach feature to query the KB collection filtered by `file_id`. This matches what the April 2026 plan describes (and partially implemented) for cloud-sync KBs, and there is no architectural reason it cannot extend to local KBs.

## Detailed Findings

### Topic 1: BQ / flat-index policy

#### What the policy does today

`backend/open_webui/retrieval/vector/dbs/weaviate.py:74-90`:

```python
_FLAT_INDEX_PREFIXES = ('File_', 'Web_search_', 'User_memory_')

def _build_vector_config(sane_collection_name: str):
    if sane_collection_name.startswith(_FLAT_INDEX_PREFIXES):
        return weaviate.classes.config.Configure.Vectors.self_provided(
            vector_index_config=weaviate.classes.config.Configure.VectorIndex.flat(
                quantizer=weaviate.classes.config.Configure.VectorIndex.Quantizer.bq()
            )
        )
    return weaviate.classes.config.Configure.Vectors.self_provided()
```

- The match is performed against the **sanitized** class name produced by `_sanitize_collection_name` (`weaviate.py:117-137`), which replaces `-` with `_` and capitalises the first letter — so the raw `file-<uuid>` becomes `File_<uuid>` and matches `File_`.
- "Flat" = brute-force scan over every vector at query time (no graph).
- "BQ" (binary quantization) compresses each `float32` dim to 1 bit at insert time; queries use Hamming distance for a fast first pass and rescore against the full vectors. No training step.
- Both `Configure.*` builder calls are pure Python; there is no synchronous network IO inside `_build_vector_config` itself. The cost lands inside `self.client.collections.create(...)` at `weaviate.py:154-177`.

Wired in once at `weaviate.py:156` (the `vector_config=_build_vector_config(collection_name)` kwarg on the `collections.create()` call).

#### What the contract test pins

`backend/open_webui/test/util/test_weaviate_index_policy.py` locks three things:

1. `File_`, `Web_search_`, `User_memory_` MUST serialise to `vectorIndexType=='flat'` + `vectorIndexConfig=={'bq': {'enabled': True}}` (lines 23–35).
2. KB-shaped names + near-miss prefixes (`Filer_`, `Web_`, `User_`) MUST stay default HNSW with no `bq` key (lines 37–51).
3. `_FLAT_INDEX_PREFIXES == ('File_', 'Web_search_', 'User_memory_')` — exact tuple (line 53–54).

The test docstring references `thoughts/shared/plans/2026-05-17-weaviate-flat-index-migration.md`. Disabling BQ requires either updating these assertions or deleting the test (and ideally the linked plan).

#### Why BQ is suspect for the 8s/collection cost (and what isn't)

- `_create_collection` (`weaviate.py:150-177`) issues a **single REST POST** with the full schema (9 explicit `TEXT` properties + the vector config). Weaviate must persist the schema (RAFT consensus on multi-node) before responding.
- With BQ + flat, the server-side schema work includes provisioning quantizer state, which is a heavier code path than the default HNSW shell that gets lazily initialised on first vector insert.
- `__init__` runs `connect_to_custom(...)` + `client.connect()` (`weaviate.py:94-115`) once per process — not per collection.
- `_ensure_collection` does an exists-check (REST GET) before the create — small overhead, not multi-second.
- No client-side timeout/retry parameters are passed; defaults from the `weaviate` Python client apply.

I could **not find a direct latency measurement** anywhere in `thoughts/` / `collab/`. The 8s number is Lex's observation and is consistent with BQ-on-flat schema setup on a small/under-resourced Weaviate, but is not codified. Worth a one-shot timing comparison before/after the change (e.g. wrap `_create_collection` in `time.perf_counter` on a dev stack).

#### Where prefixed collections come from (so we know the blast radius)

All these flow into `WeaviateClient.insert` / `upsert` (`weaviate.py:190`, `:210`) → `_ensure_collection` → `_create_collection`:

- `file-<id>` (the dominant case):
  - `backend/open_webui/routers/retrieval.py:1652` — `process_file()` default when no `collection_name` is passed (file upload).
  - `backend/open_webui/retrieval/utils.py:1070` — RAG retrieval for chat-attached files (`item.type == 'file'`).
  - `backend/open_webui/routers/files.py:831` — delete-on-remove cascade.
  - `backend/open_webui/services/sync/router.py:444`, `backend/open_webui/services/deletion/service.py:95`, `:173`, `backend/open_webui/tools/builtin.py:2114` — assorted lookups.
- `web-search-<sha>`: single call at `backend/open_webui/routers/retrieval.py:2526`.
- `user-memory-<user.id>`: one collection per user, created at `backend/open_webui/routers/memories.py:89` (and queried/reset at `:142, :178, :188, :233, :277, :321`), plus `backend/open_webui/services/deletion/service.py:365`, `backend/open_webui/tools/builtin.py:696`.

KB collections are created from a different prefix-less name (the KB UUID) and never match the BQ branch.

#### Touchpoints to disable BQ (return everything to default HNSW)

1. `backend/open_webui/retrieval/vector/dbs/weaviate.py:74-90` — delete `_FLAT_INDEX_PREFIXES` and `_build_vector_config`, or collapse to always return `Configure.Vectors.self_provided()`.
2. `backend/open_webui/retrieval/vector/dbs/weaviate.py:156` — drop the `vector_config=` kwarg in `collections.create(...)` (or pass plain `Configure.Vectors.self_provided()`).
3. `backend/open_webui/test/util/test_weaviate_index_policy.py` — entire file: remove or rewrite to assert "all collections use default HNSW".
4. `thoughts/shared/plans/2026-05-17-weaviate-flat-index-migration.md` — referenced from the test docstring; mark superseded (or rewrite) once the rollback lands.

No Helm/env-var changes are needed — `Grep` for `BQ`, `quantizer`, `flat_index`, `Quantizer.bq`, `VectorIndex.flat`, `_FLAT_INDEX_PREFIXES`, `_build_vector_config` only hits those two source files plus the plan markdown.

#### How other vector backends handle the same upload flow (for cross-checking)

| Backend | Per-`file-<id>` collection? | Reference |
|---|---|---|
| Weaviate (our fork) | Yes; uses BQ+flat for `File_*`, `Web_search_*`, `User_memory_*` | `weaviate.py` |
| Chroma | Yes (`get_or_create_collection` per name) | `chroma.py:141, 159` |
| Qdrant (single-tenancy) | Yes (`create_collection` per name) | `qdrant.py:90-123` |
| Qdrant (multitenancy) | **No** — shared `<prefix>_files` collection per tenant via partitions | `qdrant_multitenancy.py:85-86, 115-123` |
| Milvus (single-tenancy) | Yes | `milvus.py:98-167` |
| Milvus (multitenancy) | **No** — single shared collection per prefix | `milvus_multitenancy.py:53, 77-81` |
| pgvector | **No** — single `document_chunk` table; `collection_name` is just a column value | `pgvector.py:77, 285-330` |
| S3Vector | Mixed (file vs knowledge split hard-coded) | `s3vector.py:571-598` |

So per-file collection is the upstream norm for non-multitenant Weaviate/Chroma/Qdrant/Milvus, but the multitenant variants and pgvector avoid it. That confirms the architectural shape Lex is reaching for is already a recognised pattern.

### Topic 2: Per-file collections — feature dependencies

#### File upload → vector store flow today

Frontend `uploadFile()` posts to `POST /api/v1/files/` (`src/lib/apis/files/index.ts:23`). Backend chain:

- `backend/open_webui/routers/files.py:185 upload_file` → `:208 upload_file_handler` → `:93 process_uploaded_file` → `:127 process_file(ProcessFileForm(file_id=...))` **with no `collection_name`**.
- `backend/open_webui/routers/retrieval.py:1629 process_file`. Because `form_data.collection_name is None`, line `:1652` sets `collection_name = f'file-{file.id}'`.
- Both internal (`retrieval.py:1840 → :1926`) and external-pipeline (`external_retrieval.py:308`) embed paths call `save_docs_to_vector_db(..., collection_name=collection_name)`. **Every one-off upload writes into a `file-<id>` collection.**

When the same `process_file` is later called from the KB "add file" path (`backend/open_webui/routers/knowledge.py:733, :818`, with `collection_name=knowledge.id`), it takes the `elif form_data.collection_name:` branch (`retrieval.py:1679`) which **reads** from `file-<id>` (`retrieval.py:1683`) and **writes** the docs into the KB collection. So per-file collections act as a cross-KB vector cache.

Cloud-sync writers (`integrations.py:370/436/532`, `services/sync/base_worker.py:600-629`) already write KB-only and gradually delete legacy `file-<id>` collections — the April 2026 plan landed.

#### Where `file-<id>` collections are READ from

| file:line | Feature | Affected by removal? |
|---|---|---|
| `backend/open_webui/retrieval/utils.py:1070` | `get_sources_from_items` when chat message attaches `type:'file'` — used both for one-off chat uploads and "attach a single file from a KB" via the `+` / `#` menu | **Yes** (for KB-uploaded files only) |
| `backend/open_webui/routers/retrieval.py:1683` | `process_file` cross-KB cache lookup — re-using already-embedded chunks when adding the same file to a second KB | **Yes** — needs a fallback to "query KB collection filtered by `file_id`" |
| `backend/open_webui/tools/builtin.py:2114` | Built-in knowledge-search tool when a model has individual files attached as `__model_knowledge__` items | **Yes** for KB-uploaded files |
| `backend/open_webui/routers/retrieval.py:2570` | `_validate_collection_access` authorization gate (`startswith('file-')`) | Tolerant — gates by file ownership; if the collection doesn't exist downstream queries no-op |
| `backend/open_webui/routers/internal_retrieval.py:121` | External agents API; returns text content from `Files.get_file_by_id`, not vectors | **No** |

Notably **not** dependent:

- **Citations / "show source"** — sources come from the metadata of the vector hits (`file_id`, `name`, `source`); no separate per-file vector lookup.
- **File deletion / sync legacy cleanup** — uses `has_collection` guards, tolerates missing collections.
- **External agents** — text-only, never reads vectors.

#### Chat input "+" and "#" menu — current attach payloads

The frontend uses one `files` array on the chat payload for both KBs and individual files. Discriminator is the top-level `type` field.

**Attach whole KB** (`src/lib/components/chat/MessageInput/InputMenu/Knowledge.svelte:180-186`):

```js
onSelect({ ...item, knowledge_type: item.type, type: 'collection' });
```

Backend: `backend/open_webui/retrieval/utils.py:1072-1125` — collection branch. Resolves to `collection_names.append(item['id'])` (the raw KB UUID = KB collection). Already works for every KB type (`local`, `onedrive`, `google_drive`, `confluence`, `custom`).

**Attach individual file from a KB** (`src/lib/components/chat/MessageInput/InputMenu/Knowledge.svelte:256-281` from the chevron submenu; `src/lib/components/chat/MessageInput/Commands/Knowledge.svelte:120-135, 169-178` from the `#` search):

```js
onSelect({ type: 'file', name: file?.meta?.name, ...file });
```

Backend: `backend/open_webui/retrieval/utils.py:1029-1070` — file branch. Resolves to `collection_names.append(f'file-{item["id"]}')` (the per-file collection).

**Cloud-sync gating today** — exactly the one-liner Lex remembered:

`src/lib/components/chat/MessageInput/InputMenu/Knowledge.svelte:222`:

```svelte
{#if item.type === 'local' || !item.type}
    <!-- expand chevron button: only shown for local KBs -->
```

`onedrive`, `google_drive`, `confluence`, `custom` KBs render only the KB-level row (no drill-down). No backend enforcement — gating is frontend-only.

**Gap to note:** the `#` slash-command flow (`src/lib/components/chat/MessageInput/Commands/Knowledge.svelte`) does **not** have an equivalent guard. `searchKnowledgeFiles` returns files from every KB type. Today a user can still type `#somefilename` and surface a file inside a cloud KB, which would resolve to a non-existent `file-<id>` collection and silently return empty results. The April plan flagged this and proposed backend-side filtering (`models/knowledge.py search_knowledge_files`) — that fix has not landed.

#### Other features that produce / consume `file-<id>`

- **One-off chat file upload (drag/drop, paste, picker, audio)** — Always goes through `POST /api/v1/files/` → `process_file` with no `collection_name`, so it ALWAYS creates a `file-<id>` collection. This is the dominant consumer and **cannot be removed** without changing the API surface.
- **File reprocessing** (`POST /files/{id}/data/content/update`, `files.py:573-629`): deletes `file-<id>` (`retrieval.py:1660`), recreates it, then for each linked KB deletes-by-file_id from the KB collection and re-runs `process_file` with `collection_name=knowledge.id`.
- **File deletion** (`routers/files.py:831`, `services/deletion/service.py:95-100, :173`, `services/sync/router.py:444`): deletes the `file-<id>` collection.

#### Impact analysis: "stop creating per-file collections for KB uploads + remove KB-file chat attach"

Assuming we (a) skip `file-<id>` creation when a file is uploaded directly into a KB, (b) keep `file-<id>` for one-off chat uploads outside KBs, and (c) hide the "attach individual file from a KB" UI for **all** KB types (matching today's cloud-sync gating):

- **KB-file attach from `+` menu (local KBs)** — UI gone. Was the primary consumer of `file-<id>` on the chat side.
- **`#` slash-command file search** — needs the same backend filter the April plan proposed (`models/knowledge.py search_knowledge_files`) extended to exclude **all** files belonging to a KB (or all non-`local` plus we drop the local case from UI too). Otherwise typing `#filename` still returns KB files and resolves to missing collections.
- **One-off chat file upload + RAG** — unchanged; `process_file` with no `collection_name` still creates `file-<id>`.
- **`POST /knowledge/{id}/file/add`** — the cross-KB cache lookup at `retrieval.py:1683` would miss when the file was uploaded straight into a KB. Two options (per the April plan):
  1. Let it fall back to the `existing_content` re-embedding path (`retrieval.py:1693`) — slightly slower, no correctness loss.
  2. Replace the per-file query with `VECTOR_DB_CLIENT.query(collection_name=<kb_id>, filter={'file_id': ...})` — preserves the optimisation, requires a tiny code change.
- **Built-in knowledge-search tool with individual file attached** (`tools/builtin.py:2114`) — breaks for KB-uploaded files. Same fix: query the KB collection filtered by `file_id` when the file is known to live in a KB; otherwise use `file-<id>`. The tool already has access to file metadata, so it knows.
- **Citations** — unaffected (metadata-driven).
- **External agents** — unaffected.

If we **stop short of removing the local KB-file attach UI**, then we cannot stop creating per-file collections for KB uploads, because the chat-side resolver at `retrieval/utils.py:1070` would query a non-existent collection for any local-KB file. So the UI removal and the backend creation-skip are coupled.

## Code References

### BQ / Weaviate policy

- `backend/open_webui/retrieval/vector/dbs/weaviate.py:74-90` — `_FLAT_INDEX_PREFIXES` + `_build_vector_config`
- `backend/open_webui/retrieval/vector/dbs/weaviate.py:117-137` — `_sanitize_collection_name`
- `backend/open_webui/retrieval/vector/dbs/weaviate.py:150-177` — `_create_collection`
- `backend/open_webui/test/util/test_weaviate_index_policy.py` — full file (policy contract)
- `backend/open_webui/config.py:2657-2666` — Weaviate env vars (unrelated to BQ, listed for completeness)

### Per-file collection producers

- `backend/open_webui/routers/files.py:185, 208, 93, 127` — `POST /api/v1/files/` chain
- `backend/open_webui/routers/retrieval.py:1629, 1652` — `process_file` default `collection_name = f'file-{file.id}'`
- `backend/open_webui/routers/retrieval.py:1679-1693` — `process_file` `elif collection_name:` branch (cross-KB cache lookup + fallback)
- `backend/open_webui/routers/retrieval.py:1840, 1926` — internal `save_docs_to_vector_db` call
- `backend/open_webui/routers/external_retrieval.py:308` — external-pipeline equivalent
- `backend/open_webui/services/sync/base_worker.py:600-629` — cloud-sync KB-only write + legacy `file-<id>` deletion

### Per-file collection consumers (read paths)

- `backend/open_webui/retrieval/utils.py:1029-1070` — chat attach `type:'file'` branch (the main read)
- `backend/open_webui/retrieval/utils.py:1072-1125` — chat attach `type:'collection'` branch
- `backend/open_webui/routers/retrieval.py:1683` — cross-KB cache query
- `backend/open_webui/tools/builtin.py:2114` — built-in knowledge-search tool
- `backend/open_webui/routers/retrieval.py:2570` — `_validate_collection_access` authorization gate

### Chat input menu

- `src/lib/components/chat/MessageInput.svelte:1798-1846` — `<PlusAlt>` button + `InputMenu` mount
- `src/lib/components/chat/MessageInput/InputMenu.svelte:200-217` — `onSelect` (appends to `files`)
- `src/lib/components/chat/MessageInput/InputMenu/Knowledge.svelte:180-186` — attach whole KB
- `src/lib/components/chat/MessageInput/InputMenu/Knowledge.svelte:222` — cloud-sync gating one-liner
- `src/lib/components/chat/MessageInput/InputMenu/Knowledge.svelte:256-281` — per-file picker (the part we'd remove)
- `src/lib/components/chat/MessageInput/Commands/Knowledge.svelte:110-178` — `#` slash command (KB + individual file)
- `src/lib/components/chat/Chat.svelte:2168-2183, 2192-2221, 2980` — message payload assembly (`files` array → backend)

## Architecture Insights

- **The BQ policy is a small, isolated, well-tested change.** Disabling it is mechanically trivial (2 files); the only judgement call is whether to accept the (likely small) query latency increase that HNSW + no quantization brings on the `File_*` / `Web_search_*` / `User_memory_*` collections — these are small enough that brute-force flat search was already a defensible choice, and HNSW will be at least as fast in practice. The case to keep BQ would be Weaviate disk/RAM footprint on shared-services tenants; the case to drop it is the 8s/collection creation cost, which on a 1000-file local KB upload dominates everything else.

- **Per-file collections are a caching abstraction, not a data model.** The KB collection already aggregates all file vectors. The cross-KB cache hit at `retrieval.py:1683` is the only optimisation that meaningfully depends on per-file collections, and it can be replaced by a metadata-filtered KB query (per the April plan's decision #1).

- **The cloud-sync precedent is the template.** `services/sync/base_worker.py` already proves the model: KB-collection-only writes + lazy/gradual cleanup of legacy `file-<id>` + frontend gating of per-file attachment. Extending this to local KB uploads is conceptually a "lift and shift" of the same pattern with one extra question: how to handle the file-uploaded-outside-a-KB case (answer: keep `file-<id>` for those).

- **The frontend and backend changes are coupled.** Stop creating per-file collections for KB uploads, and chat-side attach-from-KB-file silently breaks. Hide the attach-from-KB-file UI, and the per-file collections become almost wasted. Land them together.

## Historical Context (from thoughts/)

- `thoughts/shared/research/2026-04-06-cloud-kb-single-collection.md` — Definitive research on the same problem scoped to cloud-sync KBs. Lists every per-file consumer + the cross-KB cache + the cloud-sync gating. Reuse the impact-assessment table and decisions verbatim where possible.
- `thoughts/shared/plans/2026-04-06-cloud-kb-single-collection.md` — Implementation plan that landed for cloud-sync (write KB-only, gradual cleanup, backend filter on `search_knowledge_files`, frontend chevron gate at `InputMenu/Knowledge.svelte:222`). Verifies that the pattern works.
- `thoughts/shared/plans/2026-05-17-weaviate-flat-index-migration.md` — The plan that introduced the BQ policy (referenced from the test docstring; will need to be superseded by a rollback note).
- `thoughts/shared/research/2026-02-14-onedrive-dedup-and-sync-status.md:26` — Documents that the original purpose of per-file collections in the OneDrive flow was vector reuse on hash matches.
- `thoughts/shared/research/2026-04-21-on-prem-client-config-handoff.md:532` — Reminds that re-embedding on the Weaviate PVC is slow; the cross-KB cache replacement (KB-filtered query) is the right substitute.
- `collab/notes.md:13`, `collab/world/context.md:23` — Typed KBs (`local` / `onedrive` / `google_drive` / `confluence` / `custom`) with frontend guards on file ops for non-local KBs.

## Open Questions

1. **Empirical confirmation of "8s per collection" → BQ.** Worth wrapping `_create_collection` with `time.perf_counter` on the dev stack with BQ on vs off, before committing. The mechanical explanation lines up, but no codified measurement exists.
2. **Local-KB-file chat attach: is anyone actually using it?** If usage is non-zero, the migration story is "stop offering it, give users `attach whole KB` instead". Worth checking analytics or asking around before removing the UI.
3. **Cross-KB cache replacement: query-on-demand vs eager copy.** Decision #1 from the April plan (query the KB collection filtered by `file_id`) avoids re-embedding. Confirm this still holds for local KBs where the KB collection may not yet have the file at the moment of the second-KB add (race condition — unlikely but worth a guard).
4. **`User_memory_<user.id>` and `Web_search_<sha>` — should they also keep BQ?** These are small per-user / per-query collections and the per-collection creation cost matters less. If we disable BQ globally, the test must be updated; if we keep BQ for `User_memory_*` / `Web_search_*` only, the policy becomes more surgical (just remove `'File_'` from `_FLAT_INDEX_PREFIXES`). Worth a deliberate call rather than "remove all".

## Related Research

- `thoughts/shared/research/2026-04-06-cloud-kb-single-collection.md` — direct precedent
- `thoughts/shared/plans/2026-04-06-cloud-kb-single-collection.md` — the cloud-sync implementation
- `thoughts/shared/plans/2026-05-17-weaviate-flat-index-migration.md` — the BQ-introducing plan
- `thoughts/shared/research/2026-03-22-weaviate-hyphen-metadata-keys.md` — adjacent Weaviate schema work
