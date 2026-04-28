# Org-Scope (App-Credential) Syncs with Chunk-ACL Mirroring — Implementation Plan

> **Status (2026-04-27)**: Design-only. Pulled out of the loader-worker plan (`2026-04-25-shared-services-loader-worker.md`) as a separate plan because (a) it adds genuinely new ACL state to open-webui (file-level visibility, which doesn't exist today) rather than just exposing existing ACL via a new bearer, and (b) it depends on a customer commitment to Confluence org-scope sync that is not yet in scope.
>
> **Do not start implementation** until a concrete customer requirement lands. The "default positions" and open questions throughout this plan are deliberate placeholders that need team review against the real customer's permission graph.

## Overview

Add a third sync model to the platform: an admin configures an org-scope source (initially Confluence) using an app-level service-account credential. The loader-worker syncs all items the credential can see, **and** for each item extracts the source-system permission set (the principals — users + groups — that can read it). Those principals land in **open-webui Postgres** as `file_acl_principals(file_id PK, principals TEXT[])`. At query time, the agent retrieval endpoint resolves the calling user's source-system principals server-side (via a new `principal_mappings` table) and joins against `file_acl_principals` to compute the per-KB allowed-file set; the existing per-KB Weaviate iteration applies a `where: file_id ContainsAny [...]` filter. **No Weaviate schema change** — the existing `file_id` property is sufficient.

This is the largest piece of ACL work in the platform. Treat it as **design-heavy with explicit open questions**, not as fully-specified implementation steps. **Default positions** are marked throughout for the reviewer to confirm or override.

### What this plan adds

1. A third sync model — **org-scope-ACL-mirrored** — alongside today's user-OAuth and the soft-launch org-scope-open.
2. New file-level ACL in open-webui's data model: `file_acl_principals` table, populated by the sync path and read by the agent retrieval endpoint.
3. Source-system principal extraction (Confluence first, via `SourceClient.fetch_acl()`).
4. A `principal_mappings` table that maps source-system identities (Confluence usernames, group IDs, eventually Azure AD object IDs) to open-webui user IDs and group IDs. Email-as-key for users; CLI-driven for groups.
5. Audit logging for every chunk push and every agent query at the granularity needed to answer "who saw what when."

### What this plan does NOT add

- A new agent surface. The agent retrieval endpoint shipped in the loader-worker plan (Phase 5 there) is the integration point — no new bearer, no new wire format. The endpoint's existing chunk-ACL hook (`_maybe_chunk_acl_filter`) is filled in by this plan's Postgres lookups.
- Weaviate schema changes. The `file_id` property already on every chunk is the load-bearing primitive.
- Source-system support beyond Confluence. OneDrive/SharePoint and Google Drive shared-drives are deferred — the `SourceClient.fetch_acl()` interface is designed to make adding sources additive, but the proving ground is Confluence.
- Webhook-driven ACL invalidation. ACLs refresh on the regular sync cycle; SLO is "≤1 sync cycle" propagation.
- An admin UI for source configuration or principal mapping. API/CLI-driven on first release; UI is a follow-up.
- A separate audit DB or SIEM destination. Audit rows land in open-webui's per-tenant Postgres until volume crosses ~1 GB/month/tenant.

## Prerequisites

This plan assumes the following from `2026-04-25-shared-services-loader-worker.md` are **in production**:

| Capability | Plan section | Why required |
|---|---|---|
| Per-tenant `gradient-loader-worker` pod with the `chunked_text` push-back path | Phase 2, Phase 4 | The org-scope sync uses the same job-submission contract; only `credential_type: "app_token"` and a per-item `acl_principals` metadata field are added. |
| `LOADER_INGEST_API_KEY` machine-auth on `/api/v1/integrations/ingest` | Phase 1.3 | The ingest write path gains a Postgres `file_acl_principals` upsert when `acl_mode='chunk_principals'`. |
| `Source

Client` ABC with `fetch()` + `credential_type` dispatch | Phase 2.5 | Adding `fetch_acl()` is a method addition on the existing ABC. |
| Agent retrieval endpoint (`POST /api/v1/internal/retrieval/query`) | Phase 5 | The integration point for chunk-ACL filtering. The endpoint's `_maybe_chunk_acl_filter` hook is the single change site this plan extends. |
| `run_agent_search()` shared by chat retrieval and agent endpoint | Phase 5.2 | Chunk-ACL enforcement applies uniformly to both paths once `acl_mode='chunk_principals'` is set on a KB. |
| Combined Staging Cutover green | end of Phase 5 local block | Production loader-worker + agent endpoint provide the foundation this plan builds on. |

If any of those are not yet shipped, this plan should not start. Iterate on the loader-worker plan first.

## Current State Analysis

### Today's sync model (single, known)

Open-webui's existing model is **user-OAuth + KB-level ACL**:
- A user OAuths with OneDrive / Google Drive, mints short-lived access tokens via `services/sync/token_refresh.py:18-63` (`TokenManager`).
- The user creates KBs and syncs their own files into them.
- ACL is at the KB level via `AccessGrants.get_accessible_resource_ids()` (`models/access_grants.py:561`). Granted readers see all chunks in the KB.
- `Files.user_id` is the FK to the originating human; chunks in Weaviate carry the `created_by` field but it is not used for ACL.

There is **no file-level ACL** in open-webui's model today. There is no concept of "files inside this KB are visible to different users based on source-system permissions." That's the gap this plan closes.

### Three sync models after this plan

| Model | Who owns the credential | Who can see the chunks | When to use it | Status |
|---|---|---|---|---|
| **user-OAuth** | individual end user (per-user OAuth token) | open-webui's KB-level ACL (`access_grants`) decides which users can see the KB | a user wants to sync their own files (today's flow) | ✅ unchanged |
| **org-scope-open** | admin (app-level service-account token) | every user in the tenant who has KB access; **chunks carry no per-item ACL** | small tenants, public-knowledge sources, where source-system permissions don't matter | ✅ added (admin-acknowledged risk) |
| **org-scope-ACL-mirrored** | admin (app-level service-account token) | only users whose source-system principals (user + group IDs) are in the file's `acl_principals` list | medium-to-large tenants syncing the company wiki where source-system permissions are the real boundary | ✅ added (default for new org-scope KBs where `fetch_acl()` is implemented) |

**Default position when an admin configures an org-scope source**: the platform defaults to `org-scope-ACL-mirrored` for any source whose `SourceClient` implements `fetch_acl()`. Falls back to `org-scope-open` with an explicit admin acknowledgment for sources where ACL extraction isn't supported.

### Schema additions

Two Postgres additions per tenant DB, both non-breaking. **No Weaviate schema changes.**

| Field | Where | Type | Default | Set by |
|---|---|---|---|---|
| `acl_mode` | open-webui Postgres `knowledge.meta` | enum `kb_inherited \| chunk_principals` | `kb_inherited` | open-webui at KB creation; org-scope KBs override |
| `file_acl_principals` (table) | open-webui Postgres | `(file_id PK, principals TEXT[], updated_at)` | empty until first sync runs | loader-worker → `/ingest` writes one row per file when `acl_mode='chunk_principals'`; backfill seeds `principals = ['user:<owner_user_id>']` for every existing file |

Existing KBs remain `kb_inherited`. Existing files are seeded by the backfill so the unified ACL model has full coverage on day one. The `chunk_principals` filter only fires when an admin explicitly configures a KB in that mode.

## Phases

### Phase 1: Admin configuration model

**Default position**: per-KB configuration. App credentials live in the per-tenant 1Password vault under a new field group. Initial release is API/config-file only; admin UI deferred.

**1Password schema**: extend the per-tenant `tenant-secrets` vault item with a new field group `orgScopeSources` (a JSON-encoded field for now; structured fields can come later):

```json
{
  "confluence": {
    "base_url": "https://acme.atlassian.net",
    "app_token": "<api token>",
    "user_email": "service-account@acme.com"
  }
}
```

ESO syncs the field as a JSON blob into the per-tenant Secret as `org-scope-sources.json`. Open-webui mounts it as a file and parses on demand.

**Open-webui KB creation API extension**: existing KB-creation endpoint accepts new optional fields:

```python
class KnowledgeForm(BaseModel):
    # ...existing fields...
    sync_model: Optional[str] = None      # "user_oauth" (default) | "org_scope_open" | "org_scope_acl"
    sync_source: Optional[dict] = None    # { "system": "confluence", "space_key": "ENG", ... }
```

The KB's `meta.integration` gains a `sync_model` and `sync_source` block. `acl_mode` is set automatically: `org_scope_acl` → `chunk_principals`; everything else → `kb_inherited`.

**Default position**: admins create org-scope KBs via direct API call (`POST /api/v1/knowledge`) with the new fields. CLI script in `scripts/create-org-scope-kb.sh` documents the call.

**Open question**: per-KB or per-tenant scope for the app-token? **Default position**: per-tenant credential, multiple KBs can reference the same source system. Allows one Confluence integration + N KBs (one per space).

### Phase 2: Loader-worker changes + open-webui write path

The loader-worker plan's Phase 2 contract already accommodates this (see that plan's "Forward Compatibility" section). Concrete changes:

- **`SourceClient.fetch()` dispatches on `credential_type`** (already shipped):
  - `user_oauth`: existing behavior. 401 → `TokenExpiredError` → per-item retry next cycle.
  - `app_token`: use credential as-is. 401 → `AppTokenInvalidError` → per-item failed with `error_code="app_credential_invalid"`. Job continues. Open-webui surfaces the error to the admin via KB sync-status UI.

- **`SourceClient.fetch_acl()` is the new method**:

```python
class SourceClient(ABC):
    @abstractmethod
    async def fetch(self, credential, credential_type, descriptor) -> bytes: ...

    @abstractmethod
    async def fetch_acl(self, credential, descriptor) -> list[str]:
        """Returns source-system principal IDs (users + groups) that can read the item.

        Returned IDs are opaque strings — the principal-mapping table (Phase 4) decides what
        they mean per source system. Format suggestion: '<source>:<type>:<id>',
        e.g. 'confluence:user:557058:f9de9b58' or 'confluence:group:engineering'.
        """
```

- **Loader-worker `job_runner.py` per-item flow** — passes `acl_principals` through as opaque per-item metadata. The loader-worker does not write to Weaviate or Postgres directly; it forwards to `/ingest`:

```python
async def _run_item(sem, job, item):
    async with sem:
        client = source_for(item.source)
        bytes_ = await client.fetch(item.source_credential, item.credential_type, item.source_descriptor)
        chunks = await doc_processor.process_document(...)
        # acl_principals is forwarded on the item, NOT replicated per chunk —
        # open-webui's /ingest writes one row per file, keyed by file_id.
        return ItemResult(chunks=chunks, acl_principals=item.metadata.get("acl_principals"))
```

- **Open-webui `/ingest` write path** — this is the load-bearing change. In `routers/integrations.py:_process_chunked_text_document` (and adjacent paths), after the existing Weaviate write completes, dispatch on the per-collection `acl_mode`:

```python
# in _process_chunked_text_document, after save_docs_to_vector_db returns
if collection.get("acl_mode") == "chunk_principals":
    for doc in documents:
        principals = doc.get("acl_principals")
        if principals is not None:
            FileAclPrincipals.upsert(
                file_id=doc["source_id"],
                principals=principals,
            )
# else: legacy kb_inherited path — no file_acl_principals row (matches today's behavior)
```

The Weaviate write itself is **unchanged from today**. The `_create_collection()` properties stay as-is, no migration.

- **`file_acl_principals` table** (Alembic migration in open-webui):

```sql
CREATE TABLE file_acl_principals (
    file_id TEXT PRIMARY KEY,                      -- references files.id (logical FK; same DB)
    principals TEXT[] NOT NULL,                    -- source-system principal IDs (e.g. 'confluence:user:abc')
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_file_acl_principals_principals ON file_acl_principals USING GIN (principals);
```

The GIN index makes `principals && ARRAY[...]` queries fast — that's the operator the agent retrieval endpoint uses to compute allowed-file-ids for a user.

- **One-shot backfill** (idempotent migration step bundled with the Alembic migration):

```sql
INSERT INTO file_acl_principals (file_id, principals)
SELECT id, ARRAY['user:' || user_id]::TEXT[]
FROM files
ON CONFLICT (file_id) DO NOTHING;
```

This seeds **every existing `Files` row** with a principal set of `['user:<owner_user_id>']` so the unified ACL model has full coverage on day one. For `kb_inherited` KBs the rows exist but are never read (the retrieval pipeline only joins `file_acl_principals` for `chunk_principals` KBs). For a future migration of an existing KB to `chunk_principals` mode, the rows are already populated with the conservative default ("only the original owner can see this file") and the admin can re-sync to populate richer source-system ACLs.

**Open question**: does the loader-worker call `fetch_acl()` itself, or does open-webui do the ACL-fetch during listing/delta and pass principals in the job submission? **Default position**: open-webui calls `fetch_acl()` during `_collect_folder_files` (alongside listing) and passes principals in `item.metadata.acl_principals`. Keeps loader-worker stateless w.r.t. ACL semantics. Cost: open-webui has to know about source ACLs (more code in `services/<source>/`); benefit: loader-worker stays a pure pipeline.

### Phase 3: Source permission graph extraction (Confluence first)

**File** (new): `/Users/lexlubbers/Code/soev/open-webui/backend/open_webui/services/confluence/{provider,sync_worker,confluence_client,acl_extractor}.py`

Confluence-specific implementation of `SourceClient.fetch_acl()`:

```python
class ConfluenceAclExtractor:
    async def fetch_acl(self, page_id: str) -> list[str]:
        """Returns Confluence principal IDs that can read this page.

        Combines:
        - Space-level read permissions (users + groups)
        - Page-level restrictions (if any, they OVERRIDE space permissions)
        Anonymous-readable spaces map to the special principal "*".
        """
        space_perms = await self._client.get_space_read_permissions(self._space_key_for(page_id))
        page_restrictions = await self._client.get_page_restrictions(page_id)
        if page_restrictions.get("read"):
            return [_format_principal(p) for p in page_restrictions["read"]]
        return [_format_principal(p) for p in space_perms]
```

**Pagination is gnarly** — Confluence returns space permissions in pages of ≤200; large enterprises have thousands of users per space. Specify:
- Cache space-level permissions in-memory per sync cycle (TTL: duration of one sync cycle). Expected 10–50 spaces per tenant; permissions list per space is bounded.
- Fail open on Confluence API errors during ACL fetch: log a warning, mark the item with `error_code="acl_fetch_failed"` (item is not synced — better to skip than to push a chunk with wrong ACLs).

**Default position**: Confluence is the **only** source supported in this phase. OneDrive/SharePoint and Google Drive shared-drives are flagged as future work. The `SourceClient.fetch_acl()` interface is designed to make adding sources additive.

**Open question**: what's the smallest unit of ACL granularity? Per-page (cheap, decent fit) or per-page-with-attachment-overrides (more accurate, more API calls)? **Default position**: per-page only. Attachments inherit the page's ACL. Documented as a known approximation.

### Phase 4: Principal mapping

**Schema** (new Alembic migration in open-webui):

```sql
CREATE TABLE principal_mappings (
    id UUID PRIMARY KEY,
    tenant_id TEXT NOT NULL,                -- required (the per-tenant-namespace deployment shape)
    source_system TEXT NOT NULL,            -- 'confluence', 'onedrive', 'google_drive', ...
    source_principal_id TEXT NOT NULL,      -- as returned by SourceClient.fetch_acl
    source_principal_type TEXT NOT NULL,    -- 'user' | 'group'
    owui_principal_id TEXT NOT NULL,        -- references users.id or groups.id
    owui_principal_type TEXT NOT NULL,      -- 'user' | 'group'
    mapping_source TEXT NOT NULL,           -- 'auto_email' | 'manual' (audit trail)
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, source_system, source_principal_id)
);
CREATE INDEX idx_principal_mappings_owui ON principal_mappings(tenant_id, owui_principal_id);

-- RLS as defense-in-depth (open-webui is per-tenant by deployment; the per-tenant pod
-- is bound to one tenant_id, but RLS guards against future deployment-shape changes).
ALTER TABLE principal_mappings ENABLE ROW LEVEL SECURITY;
CREATE POLICY principal_mappings_tenant_isolation ON principal_mappings
    USING (tenant_id = current_setting('app.tenant_id', true));
```

**Initial population strategy**:
- **Users (auto)**: on first sync where a Confluence email matches an open-webui `users.email`, insert a `mapping_source='auto_email'` row.
- **Groups (manual)**: surface unmapped principal IDs in the admin sync-status UI as a "needs mapping" list. Admin runs `scripts/map-principal.sh <tenant> <source> <source_id> <source_type> <owui_id> <owui_type>` to create the row.
- **Default position**: ship with `auto_email` for users and CLI-only for groups. Admin UI for mapping is a follow-up.

**`resolve_user_principals(user_id)` implementation** — fills in the stub left by the loader-worker plan's Phase 5.2:

```python
def resolve_user_principals(user_id: str) -> list[str]:
    """Returns the flat list of source-system principal IDs the user is mapped to,
    plus the always-present 'user:<user_id>' self-principal that user-OAuth files
    are seeded with by the Phase 2 backfill.
    """
    user = Users.get_user_by_id(user_id)
    user_groups = Groups.get_groups_by_user_id(user_id)
    owui_principal_ids = [user_id] + [g.id for g in user_groups]
    rows = PrincipalMappings.get_by_owui_principal_ids(owui_principal_ids)
    return [f"user:{user_id}"] + [row.source_principal_id for row in rows]
```

**Used by**: the `_maybe_chunk_acl_filter` hook in `run_agent_search` (loader-worker plan Phase 5.2). For each accessible KB with `acl_mode='chunk_principals'`, the endpoint computes:

```sql
SELECT file_id FROM file_acl_principals
 WHERE principals && ARRAY[<resolved_principals>]::TEXT[]
   AND file_id IN (<files_in_this_kb>)
```

…and applies the result as a Weaviate `where: file_id ContainsAny [...]` filter on the per-KB query.

**Open question**: do we support nested groups in source systems (e.g. Confluence groups that contain other groups)? **Default position**: no. The ACL extractor flattens nested groups during `fetch_acl` (Confluence's API supports this via the expand parameter). Mapping is one-to-one.

### Phase 5: Staleness and re-sync

**ACL refresh model**: ACLs refresh on every sync cycle, alongside content. No webhook subscriptions in this phase.

**Explicit SLO**: "ACL changes in the source system propagate to retrieval results within one sync cycle (typically ≤1 hour, configurable per KB)." With the Postgres-resident ACL model and no caching layer in front of the retrieval endpoint, the propagation delay is `sync_cycle_duration` exactly — there is no additional cache TTL to add on top.

**Re-sync semantics for ACL-only changes**:
- An item's content hash didn't change but its ACL did → open-webui's sync worker still needs to update `file_acl_principals` for that file. **Default position**: during `_collect_folder_files`, when `fetch_acl()` returns a principal list that differs from the current `file_acl_principals.principals` row, open-webui can either (a) submit a job containing only the changed item to refresh end-to-end, or (b) write directly to `file_acl_principals` without re-pushing chunks (the bytes haven't changed). **Default position**: option (b) — Postgres `UPDATE` on `file_acl_principals` is cheap, idempotent, and avoids re-embedding unchanged content. The job-based path stays available for cases where a content re-sync is needed for other reasons.
- For new items / items with content changes, the existing flow applies: `POST /tenants/{t}/jobs` → loader-worker → `/ingest` → Weaviate write + `file_acl_principals` upsert.

**Lifecycle**: when a file is deleted from the source system, open-webui's existing delete path removes the Weaviate chunks; this plan's addition is to also `DELETE FROM file_acl_principals WHERE file_id = ?`. Done in the same transaction as the `Files` row delete to avoid orphaned ACL rows.

**Webhook-based invalidation**: explicit follow-up. Confluence offers webhooks for permission changes; wiring them up shrinks the staleness window from "≤1 sync cycle" to near-real-time by writing directly to `file_acl_principals` on receipt. Out of scope here but the Postgres-resident model makes this cleaner — no Weaviate state to mutate on a webhook.

### Phase 6: Audit logging

**Two log streams, both into open-webui's Postgres for the initial release**:

```sql
CREATE TABLE audit_chunk_pushes (
    id BIGSERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT now(),
    tenant_id TEXT NOT NULL,
    source_system TEXT NOT NULL,
    source_item_id TEXT NOT NULL,
    owui_kb_id TEXT NOT NULL,
    chunk_count INTEGER NOT NULL,
    principal_set_hash TEXT NOT NULL,       -- SHA256 of sorted acl_principals; lets us detect ACL drift without storing the full list
    job_id UUID NOT NULL                    -- references loader-worker job (logical fk; cross-DB)
);
CREATE INDEX idx_audit_chunk_pushes_tenant_time ON audit_chunk_pushes(tenant_id, timestamp DESC);
CREATE INDEX idx_audit_chunk_pushes_item ON audit_chunk_pushes(tenant_id, source_system, source_item_id);

CREATE TABLE audit_agent_queries (
    id BIGSERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT now(),
    tenant_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,                 -- the matched filename in /var/agent-keys
    acting_user_id TEXT NOT NULL,
    query_text_hash TEXT NOT NULL,          -- SHA256 of the query string; raw query never persisted
    allowed_kb_count INTEGER NOT NULL,
    chunk_principals_kb_count INTEGER NOT NULL,
    result_chunk_count INTEGER NOT NULL
);
CREATE INDEX idx_audit_agent_queries_user_time ON audit_agent_queries(tenant_id, acting_user_id, timestamp DESC);
CREATE INDEX idx_audit_agent_queries_agent_time ON audit_agent_queries(tenant_id, agent_id, timestamp DESC);

ALTER TABLE audit_chunk_pushes ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_agent_queries ENABLE ROW LEVEL SECURITY;
CREATE POLICY audit_chunk_pushes_tenant_isolation ON audit_chunk_pushes
    USING (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY audit_agent_queries_tenant_isolation ON audit_agent_queries
    USING (tenant_id = current_setting('app.tenant_id', true));
```

**Write paths** (both inside open-webui):
- `audit_chunk_pushes`: written by `/api/v1/integrations/ingest` when a push lands chunks for an `acl_mode='chunk_principals'` collection.
- `audit_agent_queries`: written by `/api/v1/internal/retrieval/query` (the loader-worker plan's Phase 5 endpoint) at the end of every successful call. The `if AUDIT_AGENT_QUERIES_ENABLED` flag in the endpoint becomes a no-op once this plan lands the table; the flag is set true in this plan's cutover.

**Default position**: open-webui's Postgres for the initial release. A dedicated audit DB (or shipping to an external SIEM) is deferred until total audit volume crosses ~1 GB/month/tenant.

**Retention**: 90 days for agent-queries, 1 year for chunk-pushes (ACL audit trail). Both configurable via `AUDIT_RETENTION_*_DAYS` env vars; pruning runs as a daily Job.

### Phase 7: Local verification harness (pre-cutover dress rehearsal)

**File** (new): `/Users/lexlubbers/Code/soev/genai-utils/docker-compose.org-scope.yaml` (extends `docker-compose.agent-search.yaml` from the loader-worker plan's Phase 5.3).

A docker-compose stack covering the full org-scope path end-to-end without an Atlassian account:

```yaml
services:
  postgres-owui:           # open-webui's Postgres (per-tenant in cluster)
    image: postgres:16
    environment: { POSTGRES_DB: openwebui, POSTGRES_PASSWORD: dev }

  postgres-loader:         # loader-worker's job store
    image: postgres:16
    environment: { POSTGRES_DB: loader_worker, POSTGRES_PASSWORD: dev }

  weaviate:
    image: semitechnologies/weaviate:1.27

  litellm-stub:
    image: built-from-./tests/fixtures/litellm-stub

  confluence-stub:         # respx-style mock Atlassian API
    image: built-from-./tests/fixtures/confluence-stub
    # Configurable fixture spaces with per-page restrictions

  open-webui:
    build: ../open-webui
    depends_on: [postgres-owui, weaviate]
    environment:
      LOADER_WORKER_URL: http://loader-worker:8002
      USE_SHARED_LOADER: "true"
      AGENT_SEARCH_ENABLED: "true"
      ORG_SCOPE_SYNC_ENABLED: "true"
      AGENT_KEYS_DIR: /var/agent-keys
    volumes:
      - ./tests/fixtures/agent-keys:/var/agent-keys:ro

  doc-processor:
    image: built-from-./api/gateway/doc_processor

  loader-worker:
    image: built-from-./api/gateway/loader_worker
    depends_on: [postgres-loader, doc-processor]
```

**Smoke test** (`tests/integration/test_org_scope_e2e.sh`):
1. Start the stack
2. Bootstrap: create two open-webui users with `email=alice@dev`, `email=bob@dev`. Create groups `engineering` (alice) and `product` (bob).
3. Configure Confluence stub with two pages: `page-1` readable by user `confluence:user:alice@dev` only; `page-2` readable by group `confluence:group:engineering` only.
4. POST `/api/v1/knowledge` with `sync_model: "org_scope_acl"` pointing at the stub Confluence space.
5. Trigger sync. Assert:
   - Both pages chunked and pushed
   - `audit_chunk_pushes` rows present
   - `file_acl_principals` has one row per file with the correct principals (`page-1`: `['confluence:user:alice@dev']`; `page-2`: `['confluence:group:engineering']`)
   - Weaviate write happened with the unchanged schema (no `acl_principals` field on the chunk class)
6. Agent query for alice (`POST /api/v1/internal/retrieval/query` with `X-Acting-User-Id: alice-uuid`) → returns chunks from both `page-1` and `page-2` (alice is in `engineering`)
7. Agent query for bob → returns zero chunks (no mapping yet for `confluence:group:product`, and the engineering-only page-2 is unreachable)
8. CLI: `scripts/map-principal.sh dev confluence "group:product" group <bob's group id> group` — adds bob's product mapping
9. Re-query for bob → still zero chunks for the engineering-only page-2 (mapping is for `product`, but `page-2` requires `engineering`)
10. Add bob to `engineering` group in open-webui → next agent query → bob sees `page-2`
11. Audit assertion: `audit_agent_queries` rows for every query in steps 6–10, with `acting_user_id`, `agent_id`, `result_chunk_count` correctly populated

**This harness is a release gate**: this plan cannot ship to staging until the smoke test passes from a clean checkout on a developer laptop. The staging cutover is a re-run against the deployed stack with a real (small) Confluence space.

## Open Questions

Listed explicitly so the next iteration of the plan or the implementation phase has a punch-list. Each defaults below should be confirmed or overridden against the real customer's permission graph **before** writing code.

1. **Nested groups in source systems**: see Phase 4. Default: no, flatten at extract time. Reconsider if customers report missing access.
2. **ACL change affecting already-synced chunks**: re-sync everything for that item (default), or write a "blocked" flag on chunks that lose visibility? **Default position**: re-sync the item; the new push overwrites `acl_principals`. Stale chunks are eventually consistent.
3. **Rollback if a chunk's ACL is wrong**: immediate Weaviate delete? Mark as `acl_invalid=true` and filter at query time? **Default position**: immediate delete + admin alert. The filter-at-query path adds complexity to every query and isn't worth it for a low-frequency case.
4. **"User has no principals in source system Y"**: empty filter (sees nothing from Y) or explicit denial? **Default position**: empty filter — sees nothing. A user who has no Confluence principals legitimately has no Confluence access; returning denial would leak the existence of the source.
5. **Cross-tenant principal collisions**: principal IDs are unique per source system, not per tenant. Two tenants both syncing Confluence can't share a `principal_mappings` table without `tenant_id` qualifying. Schema includes it; **confirm**: is `tenant_id` always non-null in our deployment shape? **Likely yes** in the current per-tenant-namespace model.
6. **Sync cadence for org-scope KBs vs user-OAuth KBs**: same scheduler? Or separate to avoid noisy-neighbor? **Default position**: same, but per-KB cadence config so admins can dial up/down. Org-scope KBs default to 1h; user-OAuth defaults to 15min (matches today).
7. **What if Confluence's permission API rate-limits during a large sync?** Backoff + partial sync (those items become `error_code="acl_fetch_failed"` and retry next cycle). Same pattern as the user-OAuth `needs_token_refresh` flow.
8. **Per-chunk vs. per-file storage of `acl_principals`**: per-file (one row per `Files.id` in Postgres `file_acl_principals`). Chunk-level is unnecessary because all chunks of the same file share the same source-system permissions. Re-opens only if a source system surfaces sub-file permission granularity (e.g. SharePoint section-level ACLs) — would require a separate table keyed by chunk_id.

## Success Criteria

### Automated Verification (Phases 1–4 — all local):
- [ ] `cd open-webui && pytest backend/tests/services/confluence/ -v` — `respx`-mocked Atlassian API; tests for `fetch_acl` covering: page-restriction override, space-only permissions, anonymous-readable space → `["*"]`, paginated permissions list, 429 backoff, 5xx retry
- [ ] `cd open-webui && pytest backend/tests/services/retrieval/test_principal_mappings.py` — covers: auto_email user mapping on first match; idempotent re-run; `resolve_user_principals` returns flat list including `user:<self>` plus mapped principals; unmapped principal_id → not in result
- [ ] `cd open-webui && pytest backend/tests/services/retrieval/test_chunk_acl_filter.py` — covers: `_maybe_chunk_acl_filter` returns `None` for `kb_inherited`; computes `file_id ContainsAny [...]` from `file_acl_principals` join for `chunk_principals`; empty filter (no matching files) returns zero chunks
- [ ] `cd genai-utils && pytest api/gateway/loader_worker/sources/test_confluence.py` — `app_token` credential path, no retry on 401, `fetch()` integration with `fetch_acl()`-supplied principals
- [ ] Alembic migrations for `principal_mappings`, `file_acl_principals`, `audit_chunk_pushes`, `audit_agent_queries` apply cleanly on a fresh local Postgres and are reversible
- [ ] **Backfill correctness**: after the Alembic migration runs against a Postgres seeded with N existing `Files` rows, `file_acl_principals` has N rows with `principals = ['user:<owner_user_id>']`; existing `kb_inherited` retrieval continues to return identical results to pre-migration
- [ ] `helm template` of open-webui-tenant chart with `orgScopeSync.enabled: true` produces valid manifests including the `org-scope-sources.json` Secret mount

### Manual Verification (Phases 5, 6 — staging cutover):
- [ ] Provision a Confluence app token for a test space; add to staging tenant 1P vault under `orgScopeSources`
- [ ] Confirm the Alembic migration ran on staging Postgres: `file_acl_principals` row count == staging `files` row count, all with `principals = ['user:<owner_user_id>']`
- [ ] Configure an org-scope KB via API (`POST /api/v1/knowledge` with `sync_model: "org_scope_acl"`)
- [ ] Trigger an initial sync; confirm:
  - Loader-worker job submitted with `credential_type: "app_token"`
  - `audit_chunk_pushes` rows landed for each item, with non-empty `principal_set_hash`
  - `file_acl_principals` has one row per Confluence page with the principals returned by `fetch_acl`
  - **Weaviate schema is unchanged** (curl Weaviate's schema endpoint, confirm no `acl_principals` field on any chunk class)
- [ ] Two test users, one with Confluence space access, one without, both have rows in `principal_mappings`. Each user issues an agent query through the retrieval endpoint. The user without access sees zero chunks; the user with access sees chunks.
- [ ] Revoke a user's Confluence space permission; trigger a sync; within one cycle, that user's queries return zero chunks for the space (next sync updates `file_acl_principals` rows in-place).
- [ ] `audit_agent_queries` rows present for every agent call, with `result_chunk_count` matching what the user actually saw.

### Production Rollout:
- One tenant at a time. First production tenant: dogfooding (gradient) + an internal-only Confluence space.
- Per-tenant: run the Alembic migration (idempotent backfill seeds existing files); provision app credentials in 1P; flip `ORG_SCOPE_SYNC_ENABLED`; admin creates the first org-scope KB; soak for a week before enabling for any other tenant.

## What We're NOT Doing

- **Source-system permission-graph extraction beyond Confluence**. OneDrive/SharePoint, Google Drive shared-drives, and other sources are deferred until the abstraction proves out on Confluence. The `SourceClient.fetch_acl()` interface in Phase 3 is designed to make adding sources additive; **default position**: ship one source per quarter after the Confluence baseline, ordered by customer demand.
- **Webhook-driven ACL invalidation**. ACLs refresh on every sync cycle (per-source cadence, typically hourly). Webhook subscriptions for instant ACL change propagation are a known follow-up; the explicit SLO in Phase 5 is "up to one sync interval delay before ACL changes propagate." Revisit when a customer requirement makes sub-hour propagation necessary.
- **Admin UI for org-scope source configuration and principal mapping**. This plan ships with API/config-file driven configuration. A web UI on the `/admin` panel is a follow-up; the API contract is stable and the UI can be added without schema changes. **Default position**: ship config-only first, prioritize UI once the second tenant onboards an org-scope source.
- **Per-source-system audit log destination**. Phase 6 audit logs land in open-webui's Postgres for the initial release. A dedicated audit DB (or shipping to an external SIEM) is a deferred hardening that should be revisited when total audit volume crosses ~1 GB/month/tenant.
- **Sub-file ACL granularity** (e.g. SharePoint section-level permissions). The data model is per-`file_id`. Sub-file granularity would require a separate table keyed by `chunk_id` and is left as a Phase 8+ option if a source system surfaces that need.

## Migration Notes

- **Existing user-OAuth KBs**: untouched. They keep `acl_mode=kb_inherited`. No re-embed.
- **Existing user-OAuth files**: backfilled by the `file_acl_principals` Alembic migration with `principals = ['user:<owner_user_id>']`. The backfill is **idempotent** and **read-only with respect to Weaviate** — a Postgres-only migration. Files participate in the unified ACL model from day one even though they retain the `kb_inherited` enforcement mode (the `chunk_principals` filter is only invoked when a KB's `acl_mode` is `chunk_principals`).
- **Org-scope KBs**: net-new. Created via API (not migrations of existing KBs). Each new KB gets `acl_mode=chunk_principals` from the start, and its files get `file_acl_principals` rows populated by the loader-worker → `/ingest` write path.
- **Converting an existing KB to `chunk_principals` mode** (a likely future operation): explicit re-sync needed to populate `file_acl_principals` from the source system's ACLs (the backfill default of `['user:<owner_user_id>']` is a conservative starting point but doesn't reflect source-system permissions). No Weaviate state needs to change for the conversion — it's purely a Postgres update.
- **Principal mappings**: bootstrapped on first sync via `auto_email`. Manual mappings populated as gaps appear. No data migration from any existing source — `principal_mappings` is a new table.
- **Audit tables**: net-new. Daily prune Job manages retention.

## Performance Considerations

- **Postgres `principals && ARRAY[...]` query performance**: the GIN index on `file_acl_principals.principals` makes `&&` (overlap) queries O(log n + matches). For a 50K-page Confluence sync with ~30K accessible to a typical user, expect lookup latency under 20ms. **Flag for measurement** during staging soak.
- **Weaviate `file_id ContainsAny [...]` filter at scale**: the `file_id` property already has Weaviate's default inverted index. For a `ContainsAny` against ~30K file_ids per KB, expect query latency in the 50–200ms range based on Weaviate's filtering benchmarks. **Flag for measurement.** Mitigation if needed: chunk the agent search across KBs and aggregate at the open-webui layer (already what `run_agent_search` does — per-KB iteration), keeping the per-KB filter list bounded by per-KB file count.
- **Confluence permission API rate limits**: Atlassian Cloud's default is ~100 req/sec per IP. A bulk sync of a 1000-page space with per-page restriction lookups = 1000 calls. With backoff and the per-cycle space-permission cache (Phase 3), expect sustained throughput well within the limit. **Flag for measurement** with a customer-scale Confluence space.
- **Audit table growth**: ~10 rows/sec sustained for `audit_agent_queries` at the assumed agent query rate, ~1MB/day/tenant. ~100 rows/sync-cycle for `audit_chunk_pushes`. Both well within Postgres performance budget for the retention windows defined in Phase 6 (90d / 1y respectively). The daily prune job keeps growth bounded.
- **Principal-mapping + file_acl_principals table sizes**: `principal_mappings` is O(users × source_systems × group_count) ≈ 30k rows/tenant for typical sizing; `file_acl_principals` is one row per File ≈ 100k–1M rows/tenant for large customers. Both well within Postgres performance budget; the GIN index on `principals` is the load-bearing performance choice.

## Testing Strategy

### Unit Tests (open-webui)

- `services/confluence/test_acl_extractor.py`: `respx`-mocked Atlassian API. Covers: page-restriction overrides space, anonymous space → `["*"]`, paginated permissions, 429 backoff, 5xx retry, fail-open on persistent error (item marked `acl_fetch_failed`)
- `services/retrieval/test_principal_mappings.py`: `auto_email` user mapping on first match; idempotent re-run; `resolve_user_principals` returns flat list including `user:<self>`; unmapped principal → not in result
- `services/retrieval/test_chunk_acl_filter.py`: `_maybe_chunk_acl_filter` returns `None` for `kb_inherited`; for `chunk_principals`, joins `file_acl_principals` against the user's resolved principals, returns `where: file_id ContainsAny [allowed_files]`; empty match → empty filter (zero chunks)
- `db/test_file_acl_principals_backfill.py`: Alembic migration on a fresh DB seeded with N `Files` rows produces N `file_acl_principals` rows with `principals = ['user:<owner_user_id>']`; idempotent re-run is a no-op
- `routers/test_integrations_acl.py`: `/ingest` with `acl_mode='chunk_principals'` writes one `file_acl_principals` row per file; with `kb_inherited` writes none; Weaviate write is identical in both cases (no `acl_principals` field on the chunk class); `audit_chunk_pushes` row is written

### Unit Tests (genai-utils)

- `loader_worker/sources/test_confluence.py`: `app_token` credential path; no retry on 401 (`AppTokenInvalidError` raised); `acl_principals` forwarded on the item, not replicated per chunk

### Integration Tests

- `docker compose up` with mock Confluence (`respx`) + real loader-worker + real open-webui + real Weaviate; sync a fixture space with two pages of differing ACLs; agent query for a user with `principal_mappings` row sees one chunk, user without sees zero; `file_acl_principals` rows match the source-system principals exactly

### Manual Testing Steps

1. Configure a Confluence app token in the staging tenant 1P vault
2. Create an org-scope KB via API targeting a small Confluence space (3 pages, 2 with restrictions)
3. Trigger sync; verify `file_acl_principals` rows land with the correct principals; Weaviate schema unchanged (curl confirms)
4. Two test users with different Confluence access; each user issues an agent query through the retrieval endpoint. Each sees only what they have Confluence access to.
5. Revoke a user's Confluence space permission; trigger sync; within one cycle, that user's queries return zero chunks for the space.
6. Audit query: `SELECT * FROM audit_chunk_pushes WHERE source_system = 'confluence' AND timestamp > now() - interval '1 hour'` returns the expected push rows. Same for `audit_agent_queries`.

## References

**Predecessor plan**:
- `thoughts/shared/plans/2026-04-25-shared-services-loader-worker.md` — provides loader-worker, agent retrieval endpoint (Phase 5 there), and the `SourceClient` ABC this plan extends. Read its End-State Architecture and Phase 5 sections before starting this plan.

**Source references**:
- ACL resolution function: `backend/open_webui/models/access_grants.py:561` (`AccessGrants.get_accessible_resource_ids`)
- Existing chat-retrieval ACL guard: `backend/open_webui/routers/retrieval.py:2547` (`_validate_collection_access`)
- TokenManager (delta-cursor + token-refresh dependency): `backend/open_webui/services/sync/token_refresh.py:18-63`
- Weaviate schema function (kept unchanged by this plan): `backend/open_webui/retrieval/vector/dbs/weaviate.py:131-158` (`_create_collection`)

**To-be-written research (referenced by this plan, not yet authored)**:
- *Confluence permission graph*: `thoughts/shared/research/2026-MM-DD-confluence-permission-graph-extraction.md` — pagination patterns, rate-limit budgets, anonymous-space handling, customer-scale measurements
- *Principal mapping operational doc*: `thoughts/shared/research/2026-MM-DD-principal-mapping-operations.md` — admin runbook for `auto_email` failures, manual mapping CLI usage, audit trail review

## Implementation Note

This plan is **the largest design surface** in the post-loader-worker roadmap. Default positions are explicitly marked throughout. Before starting implementation, walk through the open-questions list with the team and confirm or override each default. Some answers (especially nested groups in Phase 4 and sync cadence in Open Questions §6) materially affect the schema and `fetch_acl` interface and should be locked before writing code.

The plan is self-contained from a code-organization standpoint, but it builds directly on the loader-worker plan's primitives. If any of those primitives change before this plan ships, revisit the relevant phase here.
