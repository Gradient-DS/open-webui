# Shared-Services Loader-Worker & Agent Retrieval Endpoint — Implementation Plan

> **Amendment 2026-04-27 (rev 4)**: **Single `AGENT_API_KEY` for both directions of the open-webui ↔ agent trust boundary.** Replaces the rev-2 `AGENT_KEYS_DIR` file mount + per-agent allow-list.
>
> The rev-2 design used a directory of bearer files mounted on the open-webui pod (one file per allow-listed agent, mtime-poll hot reload, ESO sync from a cluster-wide 1P item). That bought hot reload, per-agent identity in audit logs, and per-agent rotation atomicity. At the current scale — a handful of per-tenant agents in the same namespace as open-webui — none of those benefits are pulling their weight. The pattern was overkill for the operational simplicity we actually need.
>
> **What replaces it**: open-webui validates inbound bearers on `/accessible-kbs` and `/query` against the same `AGENT_API_KEY` env var the existing agent-proxy uses for *outbound* calls to the agent service. Same secret, both directions, single 1P field (`agentApiKey`) per tenant — already wired through ESO and Helm before this plan.
>
> **What stays the same**:
> - Same `AgentPrincipal`, same `X-Acting-User-Id` header, same endpoint shapes. Only the bearer-validation step changes.
> - `agent_id` is a constant `"agent"` on every principal (kept as a typed field so a future per-agent rotation can re-introduce the distinction without changing call sites or log shape).
> - Open-webui still owns ACL state. The agent still queries Weaviate directly with rev-3's provider.
>
> **Concrete deltas vs. rev 2/3**:
> - **Drop**: `_AgentKeyAllowList` class, `AGENT_KEYS_DIR` env var, agent-keys ExternalSecret + SecretStore, agent-keys volume mount + Secret directory, `agentSearch.expectedAgents` / `agentSearch.externalSecrets` / `agentSearch.keysDir` Helm values, `populate-1password.sh` `shared-services` tenant case + `AUTO_GENERATED_ITEMS` extension.
> - **Keep**: `agentSearch.enabled` (single feature flag), the existing `AGENT_API_KEY` env var on the open-webui pod (now sourced unconditionally when either inbound or outbound agent path is enabled).
> - **Test side**: `_AgentKeyAllowList` tests deleted; replaced with `_agent_key_matches` env-var tests (mirrors `_loader_key_matches`).
> - **Genai-utils side**: `deploy/projects/soev/config/agents/base.yaml` — `api_key_env: AGENT_API_KEY` (was `OPENWEBUI_AGENT_API_KEY`).
>
> **Why this is strictly better at this scale**:
> - One env var to rotate vs. a directory of files. The "rotate one agent without rotating the others" capability is moot when there's one agent per tenant.
> - Same trust-boundary as the loader-worker → tenant `/ingest` shared bearer — the operational pattern is already established.
> - Smaller deploy surface: one less ExternalSecret, one less SecretStore, one less Secret per tenant, one less env var on the pod.
>
> **Trade-off accepted**: a leaked `AGENT_API_KEY` compromises both directions of the open-webui ↔ agent path within that tenant. But (a) the per-tenant deployment limits blast radius to one tenant, (b) the bearer is already used for the open-webui → agent direction so we're not enlarging the surface, and (c) rotation is one 1P field edit + ESO refresh + restart, identical to today's agent-proxy rotation flow.
>
> **Q2 follow-up still defers cleanly**: per-tenant keys, mTLS via Cilium, SPIFFE — none of those care whether the inbound side uses a file mount or an env var. They replace the bearer pattern entirely.

> **Amendment 2026-04-27 (rev 3)**: **Agents are deployed per-tenant in the tenant namespace, like the loader-worker. They query Weaviate directly using the per-tenant Weaviate access; open-webui's role shrinks to ACL gateway, not query proxy.**
>
> Rev 2 routed every agent query through open-webui because we feared a Weaviate hardening burden if external agents talked to it directly. Once you accept that agents run *in the tenant namespace*, that fear evaporates: the agent inherits the same trust boundary as the open-webui pod and the loader-worker, gated by NetworkPolicy and the existing tenant Weaviate access (with optional `WEAVIATE_API_KEY`). No new auth, no new infra. The cost we paid in rev 2 — every agent query going through open-webui's `query_collection`, locking the agent into open-webui's exact search semantics — is a real constraint we now lift.
>
> **What's added (kept from rev 2 + new)**:
> - **Kept**: `POST /api/v1/internal/retrieval/query` (rev-2 endpoint, single-call default for simple agents).
> - **New**: `GET /api/v1/internal/retrieval/accessible-kbs` returns `{user_id, kbs:[{id, collection_name, name, description, type, owner_id}], kb_index_collection_name}`. `collection_name` is sanitised by the active vector-DB client so the agent does not have to re-implement the rule. `kb_index_collection_name` points at the meta-collection where each KB has a `(name, description)` embedding indexed by `metadata.knowledge_base_id` — the agent can semantically pick which KBs to query.
> - **New**: agent-side Weaviate retrieval provider (genai-utils) that bootstraps from `/accessible-kbs` and queries Weaviate directly. Implements `SearchMode.{SEMANTIC, HYBRID, KEYWORD, TITLE}` — including title keyword search per KB, which `/query` cannot do. Replicates open-webui's existing search semantics where applicable (per-KB iteration, score fusion, reranker passthrough).
>
> **What stays the same**:
> - Same `AgentPrincipal` auth on `/accessible-kbs`. Same allow-list mount. Same `AGENT_SEARCH_ENABLED` feature flag.
> - **Open-webui still owns ACL state.** The agent never sees a principal list, never constructs an ACL filter, never makes its own access decision. `/accessible-kbs` returns *only* what `Knowledges.get_knowledge_bases_by_user_id(permission='read')` resolves; suspension is enforced server-side. The agent's responsibility is "query within the returned set" — a soft convention that holds because the agent is in the same trust boundary as the pod that issued it.
> - Phase 5 still ships in lockstep with the loader-worker per the rev-2 cadence.
>
> **Trade-off accepted**: agent has read access to the per-tenant Weaviate, so a buggy agent could query collections it didn't get from `/accessible-kbs`. This is the same shape as the loader-worker → tenant `/ingest` trade-off: the per-tenant deployment limits blast radius to one tenant. We don't enforce ACL twice (in open-webui *and* via Weaviate API keys + collection-level policies) because the namespace boundary already does it.
>
> **Q2 follow-up (auth, deferred)**: the agent-keys file mount is fine for the current ~5-agent set. If the set grows past ~10 or a customer demands cryptographic peer identity, options on the table — in order of effort — are: (a) per-tenant agent keys instead of cluster-wide, (b) mTLS via Cilium service mesh (PKI is already in cluster), (c) SPIFFE/SPIRE workload identity. None of these change the wire shape of `/accessible-kbs` or `/query`; they only swap the bearer-validation step.
>
> **Concrete deltas vs. the body of this plan**:
> - Phase 5.2 grows by one endpoint (`/accessible-kbs`) and one helper (`resolve_accessible_kbs`). The existing `/query` and `run_agent_search` are unchanged.
> - The "agent never touches Weaviate" line in the rev-2 sequence diagram is **superseded**: agents in the tenant namespace MAY query the per-tenant Weaviate directly, scoped to collections returned by `/accessible-kbs`.
> - The "no new ACL logic" guarantee strengthens, not weakens — open-webui's ACL is the *only* place ACL is enforced; agents do not duplicate or override it.
> - Helm: no changes vs rev 2. The new endpoint mounts on the same pod, same auth, same flag.
>
> Sections below that imply the agent reaches Weaviate only via open-webui's `query_collection` are partial-superseded: that path remains as the simple default (`/query`), but the recommended pattern for non-trivial agents is `/accessible-kbs` + agent-side Weaviate provider.

> **Amendment 2026-04-27 (rev 2)**: **Phase 5 is no longer a separate `gradient-authz` service. Agents call open-webui's existing retrieval pipeline directly via a new machine-auth endpoint.** Open-webui already owns the canonical ACL — the `AccessGrants` model, KB ownership, group memberships — and already does the per-KB Weaviate iteration that chat retrieval uses. The original Phase 5 design split that work across a separate authz service that returned a filter for the agent to apply against Weaviate. That split forced (a) a Weaviate schema change to add `acl_principals` and `kb_id` properties on every per-KB chunk class in every tenant, (b) a principal-mapping wire format the agent had to understand, and (c) a 30s LRU cache to keep open-webui off the hot path. None of those buy anything once you accept that **the only thing the agent actually needs is to authenticate as a machine acting on behalf of a user** — every other part of the retrieval path already exists.
>
> **What replaces it**: a new endpoint on the per-tenant open-webui pod, `POST /api/v1/internal/retrieval/query`, authenticated by a per-agent bearer key (allow-listed via 1Password + ESO) plus `X-Acting-User-Id`. Open-webui resolves the user, runs the existing chat-retrieval pipeline (ACL + per-KB Weaviate iteration + reranking), and returns chunks. **No `gradient-authz` service. No Weaviate schema change. No new ACL state of any kind** — the endpoint exposes today's exact ACL behavior under a different bearer.
>
> **The combined-cutover framing of the previous revision still holds** — Phase 5 work runs in the same local block as Phases 0–3, and the staging cutover ships loader-worker + agent-search-endpoint together. What changes vs rev 1:
> - No `gradient-authz` chart, no `INTERNAL_AUTHZ_API_KEY`, no `authz` namespace deployment.
> - Per-agent keys (`AGENT_API_KEY_<name>`) ship into the **open-webui pod**, not a shared-services service.
> - No Weaviate `acl_principals` pre-upgrade Job per tenant.
> - Feature flags shrink from three to two: `USE_SHARED_LOADER` (loader-worker), `AGENT_SEARCH_ENABLED` (the new endpoint).
>
> **Why this is strictly better**:
> - Eliminates the cross-tenant schema-migration pain (one chunk class per KB, multiple KBs per tenant, multiple tenants — see Phase 5.0 finding below).
> - **Zero new ACL logic.** open-webui already enforces ACL on every file today; the agent endpoint is purely a different auth gate over the same retrieval function.
> - Smaller deploy surface: ~150 LoC additive on the open-webui pod and a small handful of Helm fields, no shared-services service.
> - Preserves a clear single source of truth: ACL lives in open-webui Postgres only.
>
> **Trade-off accepted**: every agent search hits open-webui (no separate cache layer). At assumed 1 query/sec/user × ~100 concurrent agent users/tenant, that's ~100 QPS into a path that's already vector-search-bound (Weaviate-bound, not pod-bound). If a particular customer's load profile demands it, an in-process LRU on `AccessGrants` resolution is a one-day later add — not worth a separate service preemptively.
>
> **The original Phase 6 (org-scope app-credential syncs with chunk-ACL mirroring) is no longer part of this plan.** It moved to its own document, [`2026-04-27-org-scope-acl-mirroring.md`](./2026-04-27-org-scope-acl-mirroring.md), because it adds genuinely new ACL state to open-webui (file-level visibility, which doesn't exist today) rather than just exposing existing ACL. It is design-only until a customer commits to Confluence org-scope sync. The agent retrieval endpoint here is the integration point that plan extends; nothing in this plan blocks on it.
>
> **Concrete deltas vs. the body of this plan, applied throughout**:
> - Phase 5 sections are restructured: 5.1 is the auth helper extension (+ allow-list mount), 5.2 is the new retrieval endpoint, 5.3 is the local harness, 5.4 is helm + secrets. The previous 5.1 (kb-access/principals endpoints), 5.3 (gradient-authz service), 5.4 (Weaviate migration), 5.5 (agent query construction) are deleted.
> - End-State Architecture's "Authz service operational modes" subsection is deleted. The schema-implications subsection is deleted (no schema changes in this plan). The "external agent query path" sequence diagram is replaced.
> - The original Phase 6 section is **removed** from this plan; references to it throughout the plan are dropped.
> - Combined Staging Cutover section drops authz-service components; loader-worker + agent-search-endpoint + Weaviate-schema-unchanged ship together.
> - Phase 4 prod rollout per tenant provisions `loaderIngestApiKey` and the `agent-keys` mount on the open-webui pod, both feature flags flip together.
>
> Sections below that still describe a `gradient-authz` service, the per-tenant `INTERNAL_AUTHZ_API_KEY`, the Weaviate `acl_principals` field, or agents constructing Weaviate queries themselves are **superseded by this amendment**. Phase numbering and tenant ordering are otherwise unchanged.

> **Amendment 2026-04-26**: Embedding stays in open-webui, not loader-worker. The loader-worker's responsibilities shrink to **download + parse + chunk only**. Reasons: (1) embedding is network-bound (single `POST /v1/embeddings` per doc) and not a load source on the open-webui pod; the actual heavy work — bandwidth-heavy cloud download + CPU-heavy parsing — is what we're moving. (2) Open-webui already owns the tenant's `RAG_EMBEDDING_MODEL` config; keeping embedding here makes that config the single source of truth and avoids drift. (3) Eliminates `LITELLM_API_KEY` from the loader-worker container entirely — single-purpose container, smaller credential surface. (4) The wire payload to `/ingest` shrinks ~10× (text strings vs 1536-float vectors). (5) The existing `/ingest` `chunked_text` data_type already does exactly this end-to-end (joins chunks, embeds via `save_docs_to_vector_db`, stores), so the open-webui side is essentially free — no new endpoint, no new data type.
>
> **Concrete deltas vs. the body of this plan, applied throughout**:
> - Loader-worker pipeline: ~~download → parse+chunk → embed → push~~ → **download → parse+chunk → push text chunks**.
> - Drop from loader-worker: `embedder.py`, `LITELLM_API_KEY` env var, `embedding_config` in `JobSpec`, `embed_texts()` call in `_process_item`.
> - `/ingest` payload `data_type`: ~~`chunked_embedded`~~ → **`chunked_text`** (already exists in `integrations.py`).
> - `/ingest` chunk shape: ~~`[{text, embedding, metadata}]`~~ → **`chunks: list[str]`** (matches `ChunkedTextDocument` model).
> - `/ingest` HTTP encoding: loader-worker sends **`multipart/form-data`** with a single `data` JSON field (matches `data: str = Form(...)` on the endpoint), not raw JSON.
> - Auth Layer Overview: drop the loader-worker → LiteLLM bearer row. Only `LOADER_INGEST_API_KEY` remains as the tenant-scoped sync-path bearer.
> - 3.5.local harness: `litellm-stub` service is removed; embedding is exercised via the real open-webui `/ingest` path (which still hits `RAG_OPENAI_API_BASE_URL` per its existing config).
> - 1Password / ESO: `<tenant>-secrets` field `openaiApiKey` is **not** mounted on loader-worker. It stays mounted on open-webui exactly as today.
>
> Phase numbering, deployment topology (per-tenant loader-worker in tenant namespace), and the rest of the architecture are unchanged. Sections below that contradict this amendment are superseded by it.

## Overview

Move heavy ingestion work (cloud download, parsing, chunking, embedding) out of the open-webui pod and into a sibling **per-tenant** loader-worker pod in the same namespace, with the parse+chunk step delegated to the existing shared-services `gradient-doc-processor`. The HTTP API contract between open-webui and loader-worker is shaped to survive an eventual swap to a RabbitMQ-backed pipeline. Add one adjacent capability on the same auth foundation: a machine-auth retrieval endpoint on open-webui that lets external agents run KB queries on behalf of a specific user (replacing today's per-user open-webui API keys, which leak full-account permissions). **Open-webui remains the single owner of ACL state** — the new endpoint reuses the existing chat-retrieval pipeline (ACL resolution + per-KB Weaviate iteration + reranking), gated by a different bearer. Org-scope app-credential syncs with chunk-level ACL mirroring (the original Phase 6) are out of scope here — see [`2026-04-27-org-scope-acl-mirroring.md`](./2026-04-27-org-scope-acl-mirroring.md) for that design.

**Deployment topology**: loader-worker runs **per tenant** in the tenant namespace (1–8 replicas, HPA-driven). Loader-worker handles download + parse + chunk only (per amendment above) — no LiteLLM call, no `LITELLM_API_KEY`, no per-tenant projected volumes, no in-process tenant-context middleware, no RLS-shared job store. Embedding stays in open-webui (via `save_docs_to_vector_db` on the `/ingest` callback path). The Phase 5 agent retrieval endpoint also lives on the existing per-tenant open-webui pod (per amendment 2026-04-27 rev 2). Tenant isolation is enforced at the **namespace + pod** boundary, exactly as today's open-webui pod is. The only service that remains shared across tenants is doc-processor (parse+chunk only, NetworkPolicy-gated).

Open-webui shrinks toward a pure control plane (OAuth, RBAC, scheduling, KB metadata, source listing/delta detection, ACL resolution). All three target repos change: a new per-tenant Helm chart for loader-worker in `genai-utils`, per-tenant HelmReleases in `soev-gitops`, and net-deletions plus a small additive control-plane surface in `open-webui`.

This plan now covers four concerns:

| # | Concern | Source plan | Phases | Status |
|---|---|---|---|---|
| (a) | HTTP job contract for loader-worker ingestion | original Plan 1 | 0–4 | first to ship |
| (b) | Loader-worker → tenant `/ingest` machine-auth (per-tenant key) | original Plan 2 | 1 (foundation), 5 (extends), 6 (extends) | first to ship |
| (c) | Cloud-download relocation (per-tenant loader-worker pod) | original Plan 3 | 2–4 | first to ship |
| (d) | Machine-auth retrieval endpoint on open-webui for external agents | new | 5 | unlocked by (a)(b) auth pattern |

**(a)(b)(c) ship first** as Phases 0–4 and constitute the original loader-worker plan. **(d) is Phase 5**, bundled into the same staging cutover per the 2026-04-27 (rev 2) amendment, but feature-flagged independently. The original Phase 6 (org-scope app-credential syncs with chunk-ACL mirroring) lives in its own plan — see [`2026-04-27-org-scope-acl-mirroring.md`](./2026-04-27-org-scope-acl-mirroring.md) — and depends on (d) for the agent-facing surface. The original Plan 4 (re-embed primitives) remains deferred.

## Current State Analysis

### Where the work runs today

| Stage | Location today | After this plan | Process pressure |
|---|---|---|---|
| Cloud listing / delta detection | open-webui pod (per tenant) | unchanged | Lightweight HTTP metadata calls (Graph delta, Drive Changes) |
| Cloud download | **open-webui pod** | per-tenant `gradient-loader-worker` pod | Heavy: 100 MB files in RAM, hashing |
| Parse | shared-services `gradient-doc-processor` | unchanged | CPU-bound, HPA at 70% CPU |
| Chunk | shared-services `gradient-doc-processor` | unchanged | Lightweight, same service |
| Embed | **open-webui pod** orchestrates → LiteLLM → external embedder | unchanged — open-webui pod still embeds, but the trigger is now the loader-worker `/ingest` callback (see amendment 2026-04-26) | No change; embedding is network-bound and not the heavy work being moved |
| Vector write | open-webui pod → tenant Weaviate | loader-worker → tenant `/ingest` → open-webui embeds → tenant Weaviate | Network — text chunks (~10× smaller than vectors) over the wire |
| Permission sync | open-webui pod | unchanged | SQL-bound; tightly coupled to ACL model |

### What exists in genai-utils

- `gradient-doc-processor` source: `/Users/lexlubbers/Code/soev/genai-utils/api/gateway/doc_processor/main.py`
  - Endpoints: `GET /health`, `PUT /process` (parse), `POST /chunk` (text → chunks), `POST /process-document` (file → parse → chunks)
  - **No embedding** today
  - **No auth** — NetworkPolicy-gated only
  - Hardcoded chunker (1000/100) at `main.py:293`
  - Image: `ghcr.io/gradient-ds/gradient-doc-processor`
  - CI: `.github/workflows/build-services.yml:92-144`
- Helm chart: `/Users/lexlubbers/Code/soev/genai-utils/deploy/helm/gradient-gateway/`
  - Doc-processor templates: `templates/doc-processor/{deployment,service,hpa}.yaml`
  - Default values: `values.yaml:186-218`
- Distributed pipeline (`document_processing/distributed/`) is the future RMQ home; **not** part of this plan but the contract we expose now must be RMQ-compatible.

### What exists in open-webui

- `BaseSyncWorker` in `backend/open_webui/services/sync/base_worker.py` (~1500 LoC, Template Method pattern)
  - `sync()` orchestration: source verify → list/delta → semaphore-bounded fan-out (default 5) → finalize
  - Per-file: `_download_and_store()` (lines 728–912) and `_process_and_embed()` (lines 913–1041)
- Per-provider workers: `services/onedrive/sync_worker.py`, `services/google_drive/sync_worker.py`
  - `_download_file_content()` (5 lines OneDrive, 12 lines GDrive incl. Workspace export)
  - `_collect_folder_files()` / `_collect_single_file()` (delta + metadata; lightweight; **stay in open-webui**)
- Provider clients: `services/onedrive/graph_client.py`, `services/google_drive/drive_client.py`
  - Listing/metadata methods stay; `download_file()`/`export_file()` move
- Push-ingest endpoint: `POST /api/v1/integrations/ingest` in `backend/open_webui/routers/integrations.py:468`
  - Supports `data_type: parsed_text | chunked_text | full_documents` per `2026-03-18-generic-push-interface-design.md`
  - **Add** `chunked_embedded` is not yet wired — need to verify against current `_process_chunked_text_document` and add a `_process_chunked_embedded_document` if missing
  - Auth via `get_verified_user` (session cookie + `user.info['integration_provider']`); needs an additive service-account API-key path

### What exists in soev-gitops

- `shared-services/<cluster>/helmrelease.yaml` deploys the `gradient-gateway` chart at OCI ref `gradient-gateway-chart`
- Per-cluster patches at `shared-services/<cluster>/values-patch.yaml`
- LiteLLM team-virtual-key pattern (`scripts/provision-litellm-team.sh`, 1P vault `soev-<tenant>` field `openaiApiKey`, ESO into per-tenant `<tenant>-secrets`) — **load-bearing**: this is the same Secret loader-worker now mounts as `LITELLM_API_KEY` for embedding
- Existing autoscaling: HPA, CPU 70%, no KEDA, no queue-depth metrics
- Static iSCSI PVs catalogued under `infrastructure/<cluster>/iscsi-storage/pvs/`
- Each tenant namespace has an existing Postgres pod (open-webui DB + agents-state DB). Loader-worker reuses that pod with a new `loader_worker` DB; no new shared-services Postgres needed.

## End-State Architecture Overview

This section names the conceptual model the plan converges on, so that Phase 5 can be read against a shared mental picture. It is the design's "what we're aiming at" — the phases are how we get there.

### Sync model

This plan supports the existing user-OAuth model: an individual end user OAuths with OneDrive / Google Drive, syncs their own files into KBs they own, and KB-level ACL (`access_grants`) controls who else can read the KB. Nothing here changes that model. (The org-scope app-credential models — both "everyone in tenant" and "chunk-level ACL mirrored" — live in [`2026-04-27-org-scope-acl-mirroring.md`](./2026-04-27-org-scope-acl-mirroring.md), which builds on top of the agent retrieval endpoint introduced here.)

### Agent retrieval endpoint operational mode

The new agent-facing endpoint (`POST /api/v1/internal/retrieval/query` on the per-tenant open-webui pod) reuses the existing chat-retrieval pipeline. It calls `AccessGrants.get_accessible_resource_ids(user_id, 'knowledge', ...)` (already in production), iterates the resulting KB classes in Weaviate, merges results, and reranks — exactly as chat retrieval does today. **Zero new ACL logic.** The endpoint returns chunks (text + metadata + score); the agent does not construct any Weaviate queries directly.

### Schema implications

**No schema changes** — Postgres or Weaviate. Per-tenant Weaviate already has one chunk class per KB with a `file_id` property; chat retrieval already does the per-KB iteration. The agent endpoint is purely additive code on top of existing state.

### Sequence diagram: external agent query path

```mermaid
sequenceDiagram
    autonumber
    participant Agent as External Agent
    participant OW as open-webui pod (per-tenant)
    participant PG as open-webui Postgres (per-tenant)
    participant W as Tenant Weaviate

    Note over Agent,OW: agent → tenant open-webui
    Agent->>OW: POST /api/v1/internal/retrieval/query<br/>Bearer AGENT_API_KEY_<name><br/>X-Acting-User-Id<br/>body: { query, top_k, kb_ids? }

    OW->>OW: validate bearer against allow-list (agent-keys mount)
    OW->>OW: resolve user from X-Acting-User-Id
    OW->>PG: AccessGrants.get_accessible_resource_ids(user_id, 'knowledge', ...)
    PG-->>OW: allowed_kb_ids
    loop per accessible KB
        OW->>W: vector search on KB class
        W-->>OW: top-k chunks for this KB
    end
    OW->>OW: rerank + merge across KBs
    OW-->>Agent: { results: [{kb_id, file_id, chunk, score, metadata}, ...] }
```

Open-webui is the **only** code path that reads ACL state. The agent never touches Weaviate, never sees principal lists, never constructs query filters. This is the "single source of truth" guarantee — and the reason the design needs no separate caching layer or wire-format for ACL.

---

## Desired End State

After this plan ships and the cutover completes:

**From the loader-worker / Phases 0–4** (concerns a, b, c):

1. **No file bytes flow through open-webui pods during sync.** OneDrive/GDrive sync workers in open-webui only do listing/delta, mint short-lived OAuth tokens, and submit jobs.
2. **Doc-processor handles parse + chunk** (unchanged from today). It does not embed; it does not need tenant identification.
3. **A new per-tenant `gradient-loader-worker` pod** runs in each tenant namespace and owns the bandwidth-heavy half of orchestration: download → call doc-processor for parse+chunk → push **text** chunks back to the tenant `/ingest` (`data_type: chunked_text`). Open-webui's `/ingest` handler embeds and stores. Per amendment 2026-04-26, embedding is **not** in the loader-worker.
4. **Loader-worker → tenant `/ingest` is authenticated** via a per-tenant `LOADER_INGEST_API_KEY` (machine-auth bearer + acting-user headers). All other auth boundaries collapse: tenant pod ↔ loader-worker is same-namespace; loader-worker → doc-processor is the existing NetworkPolicy-by-namespace-label gate.
5. **The HTTP API contract** (`POST /tenants/{t}/jobs` → 202 + Location → `GET /jobs/{id}`) matches what the future RMQ-backed system will expose, so the swap is invisible to tenants.
6. **Open-webui's soev-fork delta vs upstream is smaller** than before — net ~290 LoC deleted from the sync path, ~50 added for control-plane endpoints (Phase 5.1), so ~240 net delete vs. today.
7. All tenants run on the new sync path behind a feature flag, with an instant rollback path until the final cleanup commit.

**From the agent retrieval endpoint / Phase 5** (concern d):

8. **External agents can run KB queries on behalf of a specific user** by calling a new endpoint on the per-tenant open-webui pod (`POST /api/v1/internal/retrieval/query`), authenticated by an allow-listed `AGENT_API_KEY_<name>` bearer plus `X-Acting-User-Id`. The endpoint returns chunks; the agent never touches Weaviate or constructs filters.
9. **Open-webui remains the single owner of ACL state.** No `gradient-authz` service. No separate caching layer. The new endpoint reuses the existing `AccessGrants.get_accessible_resource_ids()` and the existing chat-retrieval pipeline (per-KB Weaviate iteration + reranking) — gated by a different bearer.
10. **Per-user open-webui API keys are no longer required for agent integrations.** Agents authenticate as machines acting on behalf of a user, with the user identity carried in a header — same `LoaderPrincipal` pattern Phase 1 established for `/ingest`.

### Key Discoveries

- Doc-processor has no embedding today, and after this plan it still doesn't — embedding moves from open-webui's pod to the new per-tenant loader-worker pod, which has the tenant LiteLLM key in env. (`api/gateway/doc_processor/main.py:248-344`)
- Push-ingest endpoint already exists and is the natural callback target. (`backend/open_webui/routers/integrations.py:468`)
- The blast radius in open-webui is contained: `_download_file_content`, `_extract_content`, `_embed_to_collections` are referenced *only* from the sync pipeline.
- Each tenant namespace already runs an open-webui Postgres (and agents-state DB). Adding a `loader_worker` DB on the same pod is a small migration — no new Postgres deployment, no new iSCSI PV, no new backup chart.
- 1Password rate limits are account-wide; a HelmRelease in rollback loop has hit this once already (2026-04-21). The per-tenant deployment shape means each loader-worker pod sources only its own tenant's Secret — no shared-services-wide ESO refresh storm.
- Cilium NetworkPolicy gates ingress to `shared-services` namespace by `uses-shared-services: "true"` namespace label. Tenant namespaces already have it; loader-worker pods inherit that namespace label and can reach doc-processor with no further policy changes.

## What We're NOT Doing

Explicitly out of scope for this plan:

- **Plan 4 — re-embed primitives**: no `pipeline_version` field on Weaviate chunks, no collection-alias swap, no cross-tenant re-embed operator. Picked up later.
- **RabbitMQ deployment**: no broker added to shared-services in this plan. Loader-worker uses a Postgres-backed job store instead. The HTTP contract is RMQ-shaped.
- **KEDA install**: HPA on memory + CPU is enough for the loader-worker bridge period. KEDA arrives with RMQ.
- **mTLS / service mesh**: API-key auth in this round; mTLS via Cilium is a follow-up.
- **Org-scope (service-account / app-only) sources, including chunk-level ACL mirroring**: the OAuth + per-user TokenManager path stays. No Confluence org-wide sync, no `file_acl_principals`, no `principal_mappings` table, no chunk-level ACL filter in this plan. That work lives in the standalone [`2026-04-27-org-scope-acl-mirroring.md`](./2026-04-27-org-scope-acl-mirroring.md) plan and is design-only until a customer commits to it.
- **Doc-processor chunker parameterization**: the hardcoded 1000/100 stays; flagged for follow-up.
- **Removing the old in-pod sync code path** during initial rollout: it stays behind a feature flag for rollback safety. A final cleanup commit removes it after every tenant is on the new path.
- **Changes to per-tenant Weaviate or KB schema**: none.
- **Replacing `EXTERNAL_PIPELINE_URL` for the non-cloud-sync upload path**: direct user uploads in chat continue using the current external-pipeline route until a future consolidation.
- **Loader-worker → open-webui token-refresh callback**: for very long job queues where ephemeral tokens expire mid-job. Multi-cycle convergence (open-webui re-discovers and re-submits failed items on the next sync) is the accepted behavior in this plan; the callback is a viable hardening if measured queue depth makes it warranted.
- **Fixing the pre-existing delta-cursor-advances-on-failure issue** in `BaseSyncWorker._collect_folder_files`. This plan inherits but does not introduce that limitation; failed file_ids are surfaced through the same path the legacy code uses, so any future retry-queue work applies to both.

## Implementation Approach

Five phases plus a Phase 0 baseline. The local-first cadence is **non-negotiable** across every phase — a developer must be able to verify the full stack on a laptop via docker-compose before any cluster touch.

- **Phases 1, 2, 3, AND 5** are implemented locally in one continuous block (per Amendment 2026-04-27 rev 2) — automated tests + `helm template` validation gate each phase, plus two docker-compose harnesses as the joint release gate: **3.5.local** (real loader-worker + real open-webui + mocked LiteLLM and cloud APIs) and **5.3 local harness** (real open-webui + Weaviate + agent-keys fixture). No cluster deploys until **both** harnesses are green. The original 3.6 and 5.6 cluster cutovers merge into one **Combined Staging Cutover** (its own section after Phase 5). **Phase 4** is the combined per-tenant production rollout (loader-worker + agent endpoint, lockstep).

The two cross-cutting design constraints in the sections below — **Auth Layer Overview** and **Tenant Isolation** — are referenced by every phase and gate every PR.

The architectural cut is: **open-webui owns "what to sync"; shared-services owns "how to fetch and process each item"**. Delta detection (lightweight metadata calls, ACL mapping) stays in open-webui; download + parse + chunk + embed live in shared-services.

The fork-management constraint: **net-deletion of soev-fork code in open-webui**. New code in open-webui is one self-contained `pipeline_client.py` module plus a feature flag. The sync abstraction layer in `services/sync/` shrinks substantially. This *reduces* future merge-conflict surface vs. upstream.

### Auth Layer Overview

Per-tenant deployment collapses most auth boundaries into "same namespace = same trust boundary." Per amendment 2026-04-26, only **one** bearer key remains on the loader-worker side of the sync path: `LOADER_INGEST_API_KEY` (loader-worker → tenant `/ingest`). The previously-listed `LITELLM_API_KEY` on the loader-worker is gone — embedding moved back to open-webui, which keeps using the same `<tenant>-secrets.openaiApiKey` Secret it already mounted (no change to open-webui's existing Secret mount). Phase 5 adds **per-agent `AGENT_API_KEY_<name>` keys mounted directly on the open-webui pod** (no separate authz service). There is **no** key between the tenant pod and loader-worker (same namespace) and **no** key between loader-worker and doc-processor (NetworkPolicy by namespace label, matching doc-processor's existing model).

The loader-worker is a machine, not a user — it presents `LOADER_INGEST_API_KEY` to authenticate "I am the loader" and echoes the originating `user_id` and `provider_slug` back in headers so `/ingest` can attribute File records to the user who actually triggered the sync. No DB service account, no FK abuse, no admin-UI binding.

| Caller | Recipient | Bearer presented | Identity carried |
|---|---|---|---|
| Browser user | tenant `/ingest` | session cookie | `user.id` (UUID) from session, `provider` from `user.info` |
| Tenant `open-webui` pod | tenant `loader-worker` `/jobs` | _none_ — same namespace, NetworkPolicy by pod-selector | `acting_user_id` (UUID), `provider_slug` in body |
| Tenant `loader-worker` | doc-processor `/process-document` | _none_ — existing NetworkPolicy gate on `uses-shared-services: "true"` namespace label | n/a (doc-processor needs no tenant identity for parse+chunk) |
| Tenant `loader-worker` | tenant `/ingest` | `LOADER_INGEST_API_KEY` (per tenant, same `<tenant>-secrets`) | `X-Acting-User-Id`, `X-Acting-Provider` in headers (echoed from job) |
| Tenant `open-webui` | LiteLLM `/embeddings` | tenant LiteLLM team key (env var `LITELLM_API_KEY` from `<tenant>-secrets.openaiApiKey`) — **unchanged from today**; called from `save_docs_to_vector_db` on the `/ingest` callback path | n/a |
| External agent | tenant `open-webui` `/api/v1/internal/retrieval/query` | `AGENT_API_KEY_<name>` (per agent, allow-listed; mounted on open-webui pod) | `X-Acting-User-Id` header (Phase 5) |

**Why so few bearers**: the per-tenant loader-worker pod lives in the tenant namespace alongside open-webui. Same namespace = same trust boundary, gated by NetworkPolicy. The only crossings of trust boundaries on the loader-worker's sync path are (a) loader-worker → cloud APIs (short-lived OAuth tokens) and (b) loader-worker → tenant `/ingest` (`LOADER_INGEST_API_KEY` to distinguish the loader from a user-cookie session). LiteLLM is called from open-webui as it always was; that crossing is unchanged. Phase 5 adds **one** independent crossing — external agent → tenant open-webui — gated by `AGENT_API_KEY_<name>`. The agent never reaches Weaviate directly; the existing tenant Weaviate continues to accept connections only from the same-namespace open-webui pod.

```mermaid
sequenceDiagram
    autonumber
    participant U as Browser User
    participant OW as open-webui pod (tenant ns)
    participant LW as loader-worker (tenant ns)
    participant DP as doc-processor (shared-services)
    participant LL as LiteLLM (shared-services)
    participant C as OneDrive / GDrive

    Note over OW,LW: Same namespace
    U->>OW: Trigger sync (cookie auth)
    OW->>OW: TokenManager.get_valid_access_token(user_id)
    OW->>LW: POST /tenants/{t}/jobs<br/>(no bearer — same ns, NetworkPolicy)<br/>body: { acting_user_id, provider_slug, items }
    LW-->>OW: 202 + Location: /jobs/{id}

    Note over LW,DP: Cross-namespace (tenant ns -> shared-services)
    LW->>C: GET file (ephemeral OAuth token, single-use)
    C-->>LW: bytes
    LW->>DP: POST /process-document<br/>(no bearer — uses-shared-services namespace label)<br/>multipart: bytes
    DP-->>LW: chunks (text only)
    Note over LW,OW: Loader-worker does NOT embed (per amendment 2026-04-26)
    LW->>OW: POST /api/v1/integrations/ingest<br/>multipart: data={collection, documents:[{chunks:[text...]}]}<br/>data_type=chunked_text<br/>Bearer LOADER_INGEST_API_KEY + X-Acting-User-Id + X-Acting-Provider
    OW->>LL: POST /v1/embeddings (unchanged — open-webui's existing path)
    LL-->>OW: vectors

    Note over LW,OW: Same namespace, callback
    LW->>OW: POST /api/v1/integrations/ingest<br/>Bearer LOADER_INGEST_API_KEY<br/>X-Acting-User-Id, X-Acting-Provider<br/>body: { collection, documents (text + embedding per chunk) }
    OW->>OW: bearer matches LOADER_INGEST_API_KEY<br/>=> machine principal<br/>=> resolve acting_user from header<br/>=> Files.insert_new_file(user_id=acting_user_id)
    OW-->>LW: 200 { created, updated, errors }
```

**Why machine-auth + body-supplied identity (not a DB service account)**:

- The loader-worker only needs to prove "I am the loader" to be allowed to push chunks. It doesn't need a row in the `users` table.
- The `acting_user_id` already exists in the open-webui DB (it's the user who kicked off the sync from the UI). Echoing it back means `Files.user_id` and KB ownership land on the right human, exactly as today's in-pod sync does — no FK shimming.
- Trade-off: a stolen `LOADER_INGEST_API_KEY` lets an attacker impersonate any `user_id` they know **within that tenant**. The per-tenant deployment shape means a leaked key compromises only one tenant's `/ingest`, not all of them. Strictly better than the alternative (a DB service account that owns *every* loader-pushed File).

### Tenant Isolation

Today's per-tenant pod model gives strong isolation: each tenant's secrets, processing, memory, and PVCs live in its own pod and namespace. **This plan preserves that boundary for both the loader-worker and the agent retrieval endpoint** — loader-worker by deploying per-tenant in the tenant namespace, and the new agent endpoint by living on the existing per-tenant open-webui pod. The only service that remains shared across tenants is doc-processor (parse+chunk only, no tenant-scoped credentials).

Both per-tenant pods need **no in-process tenant-isolation invariants** — they only ever see one tenant's data. The invariants below apply **only to doc-processor**.

#### Today vs. after this plan

| Property | Today | After Phases 1–6 |
|---|---|---|
| Loader-worker process isolation | n/a | ✅ per-tenant pod in tenant namespace; no in-process mixing |
| Loader-worker credentials | n/a | ✅ only that tenant's `<tenant>-secrets` mounted (existing per-tenant ESO pattern, no new wiring) |
| Loader-worker job-store DB | n/a | ✅ new `loader_worker` DB on the tenant's existing per-tenant Postgres pod — single-tenant, no RLS needed |
| Doc-processor tenant separation | n/a (today the call comes from the tenant pod itself) | ⚠️ doc-processor parses all tenants' bytes in one pod; existing `ProcessPoolExecutor` separates parse jobs in-process. No tenant-scoped credentials are stored in doc-processor (embedding moved to loader-worker), so there's nothing tenant-specific to leak |
| Agent retrieval endpoint | n/a | ✅ lives on per-tenant open-webui pod; agent-keys allow-list mounted on that pod; tenant boundary is the namespace exactly as today |
| Principal mappings | n/a | ✅ stored in open-webui's per-tenant Postgres; never leaves the tenant boundary |
| NetworkPolicy | tenant pods reach `shared-services` via `uses-shared-services: "true"` namespace label | ✅ unchanged. Loader-worker inherits this from its tenant namespace |

#### Mandatory invariants (enforced in code, tested in CI)

These apply to **doc-processor** (the only service that remains shared). Loader-worker and the agent retrieval endpoint are exempt — both per-tenant.

1. **No persisted tenant-scoped credentials in doc-processor.** Doc-processor performs parse+chunk only and never embeds. It holds no LiteLLM keys, no per-tenant Secrets, no `request.state.tenant`-keyed credential lookups. A leaked doc-processor pod exposes no per-tenant credentials. The existing `ProcessPoolExecutor` keeps parse jobs in separate worker processes.

2. **Tenant-tagged structured logging in doc-processor.** Every log line and metric emitted by doc-processor includes `tenant=<slug>` (carried from the request, since doc-processor itself has no tenant identity baked in). CI lint check rejects un-tagged log/metric calls. Loader-worker's and open-webui's logs are inherently per-tenant by deployment, so this is a structured field but not a CI gate.

3. **No tenant data in error traces.** Exception handlers in doc-processor strip request bodies and Authorization headers before logging. Stack traces with file paths only — no captured locals containing per-tenant payloads.

#### Pod security context (applied to doc-processor; loader-worker and open-webui inherit the existing per-tenant pattern)

```yaml
securityContext:
  runAsNonRoot: true
  runAsUser: 1000
  fsGroup: 2000
  seccompProfile:
    type: RuntimeDefault
containers:
  - securityContext:
      allowPrivilegeEscalation: false
      readOnlyRootFilesystem: true
      capabilities:
        drop: [ALL]
```

Combined with the Pod Security Admission (`enforce: restricted` on `shared-services` namespace), this means an attacker who achieves code execution in a shared-services pod can't escalate, can't write to disk outside `emptyDir` volumes, and can't `ptrace` other processes.

#### Blast-radius summary

- **PRESERVED**: per-tenant pod, namespace, Postgres, Weaviate, ACL state, sync credentials, LiteLLM keys. Loader-worker preserves the namespace boundary that open-webui already has. The agent retrieval endpoint adds zero new cross-tenant components — it's just a new route on the existing per-tenant open-webui pod.
- **NEW BUT BOUNDED**: doc-processor processes parse jobs across tenants in one pod. It holds no tenant-scoped credentials, so the worst case is "an attacker who achieves RCE could observe other tenants' file bytes during the brief window they're being parsed." Hardened pod security context + small audited surface + no persistent state are the mitigations.
- **NEW BUT BOUNDED**: agent keys (`AGENT_API_KEY_<name>`) live in a cluster-wide 1Password item and are projected by ESO into every tenant namespace's `agent-keys` Secret. A leaked agent key compromises that agent's read access to *every* tenant the agent is allow-listed for; rotation is one 1P field edit + ESO refresh.

#### Detection and incident response

- Mimir alert on `count(invalid_agent_bearer_attempts) > 0 over 5m` in open-webui — a positive value indicates either a code bug or a probe.
- Mimir alert on per-tenant credential rotation lag (`time_since_last_rotation > 90d` per tenant per key purpose).
- Audit query for "did agent X ever query for user_id Y in tenant Z?" — out of scope here; the org-scope-acl-mirroring plan adds an `audit_agent_queries` table when chunk-level ACL ships.
- Runbook entries:
  - **"loader-worker compromise (single tenant)"** — rotate that tenant's `loaderIngestApiKey` and LiteLLM team key. Force ESO sync. Restart that tenant's loader-worker + open-webui pods. Other tenants unaffected.
  - **"agent key compromise"** — remove the agent's field from the cluster-wide `agent-keys` 1P item. Force ESO sync across all tenant namespaces. Open-webui pods reload the allow-list (mtime poll). All tenants the agent had access to are immediately revoked.
  - **"doc-processor compromise"** — restart shared-services pods. Cordon shared-services namespace until forensics complete (per-tenant chat traffic continues unaffected; sync is impacted because doc-processor parse calls fail, but data integrity is preserved — failures surface as job errors, not data leaks).

This section is the cross-cutting backbone for the per-tenant deployment shape; Phase 2 (loader-worker) and Phase 5 (agent retrieval endpoint) both stay inside the per-tenant boundary by construction.

---

## Phase 0: Baseline Measurement

### Overview

Capture legacy in-pod sync performance on the staging tenant **before any code changes** so the Phase 4 soak exit criteria ("p95 within 1.5x of legacy") have a concrete number to compare against. Without this, "1.5x of legacy" is an unfalsifiable claim.

### Procedure

1. On `staging` tenant in `previder-prod-staging`, configure a representative OneDrive folder. Recommended composition (mirrors the worst case observed in Vink hardening work):
   - 200 small files (≤1 MB each, mixed PDF/DOCX/XLSX)
   - 20 medium files (5–25 MB each, mostly PDF)
   - 5 large files (50–100 MB each, scanned PDF)
   - 10 Google Docs (Workspace export path)
   - **Total**: ~235 files, ~600 MB raw bytes
2. Trigger a full sync via the UI. Note start timestamp.
3. Capture from open-webui pod logs (or Mimir, if metrics already exist):
   - **Per-file processing latency**: timestamps from `_download_and_store` start to `_process_and_embed` complete, per file
   - **Wall-clock total**: sync start → sync complete event
   - **Peak memory**: `kubectl top pod` sampled every 30s during the sync
   - **Failures**: count of `failed_files` entries at end-of-sync
4. Repeat 3 times on different days/times to average out network variance. Discard outliers (cloud API latency spikes).
5. Compute and record p50, p95, p99 of per-file latency.

### Output

**File** (new): `/Users/lexlubbers/Code/soev/open-webui/thoughts/shared/research/2026-04-25-loader-worker-baseline.md`

Contents:
- Date, tenant, folder composition
- Per-file latency distribution (p50/p95/p99) with histograms
- Wall-clock total per run
- Peak tenant-pod memory observed
- Failure count and any error categorization

This file is the reference for Phase 4.1 exit criteria. The numbers do not move during the implementation phases; if architecture changes downstream, re-baseline.

### Success Criteria

#### Manual Verification:
- [ ] Three runs completed on staging within a 1-week window
- [ ] Baseline file committed to `thoughts/shared/research/`
- [ ] Phase 4.1 references the specific p50/p95/p99 numbers from this file (replacing "1.5x of legacy" with explicit milliseconds)

**Implementation Note**: Phase 0 is the only step in this plan that touches a cluster before the end-of-Phase-3 staging cutover, and it is read-only — no deploys, no config changes. Phases 1, 2, 3 then proceed locally.

---

## Phase 1: Auth Foundations

### Overview

Lay the auth plumbing for the loader-worker → tenant `/ingest` callback. Provision one new per-tenant key via 1Password + ESO. Add an additive machine-auth path to `/ingest`. No behavior change yet — the loader-worker doesn't exist; this just unblocks it.

The earlier draft of this phase added a second per-tenant key (`PIPELINE_API_KEY`) and a per-tenant projected-volume pattern in the shared-services namespace so doc-processor could identify which tenant's LiteLLM key to use. That pattern is **gone** because (a) doc-processor no longer embeds and needs no tenant identity, and (b) loader-worker is now per-tenant in the tenant namespace, so the tenant pod → loader-worker hop is same-namespace and needs no bearer.

### Changes Required

#### 1.1 Provision per-tenant API key in 1Password

**Vault**: `soev-<tenant>` (existing per-tenant vault)
**Item**: `tenant-secrets` (existing item)
**New field**:
- `loaderIngestApiKey` — loader-worker → tenant (callback to `/api/v1/integrations/ingest`)

The existing `openaiApiKey` field (the tenant's LiteLLM team-virtual key) is reused as-is and mounted on the loader-worker pod as `LITELLM_API_KEY`. No new field needed for that.

**Provisioning**: Extend `/Users/lexlubbers/Code/soev/soev-gitops/scripts/populate-1password.sh` to generate one random 256-bit key per tenant on first run, `op item edit` to update existing tenants in-place idempotently.

#### 1.2 Sync key via ESO (single side — tenant namespace)

**File**: `/Users/lexlubbers/Code/soev/open-webui/helm/open-webui-tenant/templates/external-secrets.yaml`

**Change**: extend the existing `ExternalSecret` for the tenant to pull the new field. The same `<tenant>-secrets` Secret is mounted on **both** the open-webui pod and the loader-worker pod (Phase 2.4) — they're in the same namespace and share the credential. Open-webui validates inbound bearers; loader-worker presents the same bearer outbound.

```yaml
spec:
  data:
    # existing fields...
    - secretKey: LOADER_INGEST_API_KEY
      remoteRef:
        key: tenant-secrets/loaderIngestApiKey
```

**File**: `/Users/lexlubbers/Code/soev/open-webui/helm/open-webui-tenant/templates/deployment.yaml`

**Change**: mount `LOADER_INGEST_API_KEY` as env var on the open-webui pod for inbound validation. The loader-worker pod (defined in Phase 2.4's per-tenant chart) mounts the same Secret for outbound presentation.

#### 1.3 Machine-auth path on `POST /api/v1/integrations/ingest`

_(unchanged from the earlier design — this is the LoaderPrincipal pattern.)_

**File**: `/Users/lexlubbers/Code/soev/open-webui/backend/open_webui/routers/integrations.py`
**File** (new): `/Users/lexlubbers/Code/soev/open-webui/backend/open_webui/utils/service_auth.py`

**Design**: the bearer key authenticates *the machine* ("this is the loader-worker"). The acting `user_id` and `provider_slug` for FK inserts come from request headers — set by the tenant pod when it submitted the job, echoed by the loader-worker on callback. No DB service-account user; no admin-UI binding; no FK abuse.

Add a new dependency `get_integration_principal` that returns either:
- a normal `user` (existing cookie path, unchanged), or
- a `LoaderPrincipal` wrapper around an existing user, where the user is resolved via `X-Acting-User-Id` and the provider is overridden via `X-Acting-Provider`.

```python
# backend/open_webui/utils/service_auth.py
from dataclasses import dataclass
from typing import Optional
import hmac
import os

from fastapi import Header, HTTPException, Request

from open_webui.models.users import Users
from open_webui.utils.auth import get_verified_user


@dataclass
class LoaderPrincipal:
    """Machine-auth principal — wraps a real user resolved from X-Acting-User-Id."""
    user: object              # the resolved Users row (has .id, .name, .email, .role, .info)
    provider_slug: str        # from X-Acting-Provider, overrides user.info['integration_provider']

    @property
    def id(self):
        return self.user.id


async def get_integration_principal(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    x_acting_user_id: Optional[str] = Header(default=None, alias="X-Acting-User-Id"),
    x_acting_provider: Optional[str] = Header(default=None, alias="X-Acting-Provider"),
):
    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()
        configured = os.environ.get("LOADER_INGEST_API_KEY", "")
        if configured and hmac.compare_digest(token, configured):
            if not x_acting_user_id or not x_acting_provider:
                raise HTTPException(
                    400,
                    "X-Acting-User-Id and X-Acting-Provider are required when "
                    "authenticating with the loader bearer key",
                )
            user = Users.get_user_by_id(x_acting_user_id)
            if not user:
                raise HTTPException(
                    404, f"acting user '{x_acting_user_id}' not found"
                )
            return LoaderPrincipal(user=user, provider_slug=x_acting_provider)
    # Fall through to existing user-cookie path. Acting headers are ignored.
    return await get_verified_user(request)
```

**Touch in `integrations.py`** (kept minimal for merge-resilience):
1. `from open_webui.utils.service_auth import LoaderPrincipal, get_integration_principal`
2. Swap the `Depends(get_verified_user)` on `ingest_documents` (and `delete_collection`) for `Depends(get_integration_principal)`. Rename the parameter from `user` to `principal`.
3. Inside the endpoint, derive the effective user and provider:
   ```python
   user = principal.user if isinstance(principal, LoaderPrincipal) else principal
   if isinstance(principal, LoaderPrincipal):
       provider = principal.provider_slug
       provider_config = request.app.state.config.INTEGRATION_PROVIDERS.get(provider)
       if not provider_config:
           raise HTTPException(403, f"provider '{provider}' is not registered")
   else:
       provider, provider_config = get_integration_provider(request, user)
   ```
4. Keep all existing call sites (`user.id`, `user_id=user.id`) unchanged downstream — `user` is now the resolved acting user.

**Effect**: cookie-authenticated calls behave identically to today (acting headers are ignored on that path). Loader-bearer calls produce File records owned by the originating user, exactly as the in-pod sync does today. The only attribute that changes per-call is `provider`, which is normal — the existing user-cookie path also picks provider per-call from `user.info`.

**Trade-off documented**: a stolen `LOADER_INGEST_API_KEY` would let an attacker impersonate any `user_id` they know. That is no worse than today's "ingest arbitrary chunks to arbitrary KBs with the same key" — and is strictly better than minting a DB service-account user that would *own* every loader-pushed File regardless of who triggered the sync.

#### 1.4 NetworkPolicy

**File**: `/Users/lexlubbers/Code/soev/soev-gitops/infrastructure/base/network-policies/shared-services-policy.yaml`

**Change**: doc-processor's existing ingress rule (`uses-shared-services: "true"` namespace label) already covers the new caller (tenant-namespace loader-worker), since tenant namespaces already carry that label. Loader-worker's own NetworkPolicy is defined in Phase 2.4 (per-tenant chart) and follows the same pattern as the existing open-webui-tenant policy: ingress restricted to the tenant's `open-webui` pod by pod-selector; egress to doc-processor (cross-ns), LiteLLM (cross-ns), tenant Weaviate (same ns), tenant `/ingest` (same ns), and the cloud APIs (internet).

**No change to doc-processor** in this phase — its existing `uses-shared-services: "true"` ingress rule and its lack of per-tenant credentials mean the previous plan's tenant-auth middleware is unnecessary.

### Success Criteria (all local — no cluster deploy in Phase 1)

#### Automated Verification:
- [x] `helm template` of the open-webui-tenant chart with `LOADER_INGEST_API_KEY` set produces valid manifests with the env var on the open-webui pod
- [x] `populate-1password.sh <tenant>` (dry-run, against a throwaway test vault) generates the new field and is idempotent on re-run
- [x] open-webui backend tests pass: `cd open-webui && npm run lint:backend`
- [x] Unit tests in `open-webui/backend/open_webui/utils/service_auth.py` cover: (a) valid bearer + both acting headers → `LoaderPrincipal` with the resolved user; (b) valid bearer but missing acting headers → 400; (c) valid bearer + unknown `acting_user_id` → 404; (d) invalid bearer → falls through to cookie auth; (e) no `Authorization` header → falls through to cookie auth
- [x] Unit test in `open-webui/backend/open_webui/routers/test_integrations.py` covers: `/ingest` accepts a loader-bearer call with acting headers and creates the File record owned by the acting user (`Files.user_id == acting_user_id`)

**Implementation Note**: Phase 1 ships no cluster changes. The next change to verify in a cluster is the single end-of-Phase-3 staging cutover (section 3.6). Continue directly into Phase 2.

---

## Phase 2: New per-tenant `gradient-loader-worker` Service

### Overview

Build the new per-tenant orchestration service that runs in each tenant namespace alongside open-webui. It receives jobs from the tenant pod (same namespace, no bearer), downloads from cloud sources using forwarded short-lived OAuth tokens, calls shared-services `gradient-doc-processor` for parse+chunk, embeds in-process via LiteLLM (using the tenant's existing `openaiApiKey` Secret), then pushes embedded chunks to the tenant's `/api/v1/integrations/ingest` (machine-auth bearer + acting headers).

### Changes Required

#### 2.1 Doc-processor stays parse+chunk only (no change in behavior)

The earlier draft of this plan extended `POST /process-document` with an optional `embedding_config` and a per-tenant LiteLLM-key projected volume so doc-processor could embed. **That extension is dropped.** Embedding moves into the per-tenant loader-worker (2.2), which already lives in the tenant namespace where the LiteLLM key is. Doc-processor's contract remains exactly what it is today: bytes in → text + chunks out.

**Net effect on doc-processor code**: zero. The existing `POST /process-document` is the one loader-worker calls; the existing `OpenwebuiChunkResponse` shape (no `embedding` field) is what loader-worker receives. Doc-processor needs no per-tenant credentials, no per-tenant projected volumes, no `request.state.tenant` middleware.

**No `tenant-litellm-key-<tenant>` ExternalSecrets in shared-services**. The existing per-tenant `<tenant>-secrets` (with `openaiApiKey`) in each tenant namespace is reused by the loader-worker pod next to it — no new secret-replication pattern.

#### 2.2 New service: `gradient-loader-worker`

**Directory** (new): `/Users/lexlubbers/Code/soev/genai-utils/api/gateway/loader_worker/`

```
loader_worker/
├── main.py                # FastAPI app, lifespan, routers
├── settings.py            # env var config (DB URL, doc-processor URL, LiteLLM URL, LITELLM_API_KEY, LOADER_INGEST_API_KEY, etc.)
├── job_store.py           # SQLAlchemy async, jobs table + result rows
├── routes/
│   ├── jobs.py            # POST /tenants/{t}/jobs, GET /jobs/{id}
│   └── health.py          # GET /healthz, GET /readyz
├── workers/
│   ├── job_runner.py      # per-job pipeline: download → parse+chunk → embed → push-back
│   └── pool.py            # asyncio.Semaphore + worker tasks dequeuing from DB
├── sources/
│   ├── base.py            # SourceClient ABC: list, download, export
│   ├── onedrive.py        # ports graph_client.download_file (and friends)
│   └── google_drive.py    # ports drive_client.download_file + export_file + GOOGLE_WORKSPACE_EXPORT_MAP
├── embedder.py            # in-process embedder: httpx → LiteLLM /v1/embeddings, batching + retry
├── ingest_client.py       # POST to tenant's /api/v1/integrations/ingest with LOADER_INGEST_API_KEY
├── doc_processor_client.py # POST to gradient-doc-processor (no bearer; NetworkPolicy gate)
├── alembic/               # migrations for the jobs DB
└── Dockerfile
```

**Auth posture**: no `auth.py` middleware on `/jobs` — the only inbound caller is the tenant's open-webui pod in the same namespace, gated by NetworkPolicy. Outbound auth lives in `ingest_client.py` (presents `LOADER_INGEST_API_KEY`) and `embedder.py` (presents `LITELLM_API_KEY` from env). `doc_processor_client.py` presents nothing — same `uses-shared-services: "true"` namespace label that doc-processor already trusts.

**Dockerfile**: adapt from doc-processor's pattern, base `python:3.11-slim`, exposes port `8002`.

**CI**: extend `.github/workflows/build-services.yml` with a parallel build job for `gradient-loader-worker`. Same registry pattern.

**Endpoints**:

```
POST /tenants/{tenant}/jobs
  (no Authorization header — same-namespace caller, NetworkPolicy-gated)
  Body: {
    "knowledge_id": "uuid",
    "acting_user_id": "<open-webui user.id who triggered the sync>",
    "provider_slug": "onedrive" | "google_drive",
    "callback_base_url": "http://<tenant>-open-webui.<ns>.svc:8080",  # in-cluster
    "embedding_config": {                              # used in-process by loader-worker's embedder
      "base_url": "http://litellm-proxy.shared-services.svc:4000/v1",
      "model": "text-embedding-3-small"
    },
    "items": [
      {
        "source": "onedrive" | "google_drive",
        "source_descriptor": { ... provider-specific item info ... },
        "source_credential": "<credential bytes — see credential_type>",
        "credential_type": "user_oauth" | "app_token",
        "file_id": "onedrive-<drive>-<item>",
        "filename": "...",
        "content_type": "...",
        "metadata": { ... goes into the ingest call ... }
      }
    ],
    "collection": {
      "source_id": "onedrive-<drive>",
      "name": "<KB name>",
      "data_type": "chunked_embedded"
    }
  }
  → 202 Accepted
    Location: /jobs/<uuid>
    Body: { "job_id": "<uuid>", "status": "queued", "items": <count> }

The `acting_user_id` and `provider_slug` are stored on the job row and echoed back to `/ingest` in `X-Acting-User-Id` and `X-Acting-Provider` headers on the callback. Loader-worker treats them as opaque strings — only open-webui validates them.

GET /jobs/{job_id}
  (no Authorization header — same-namespace caller, NetworkPolicy-gated)
  → 200 OK
    {
      "job_id": "...",
      "status": "queued | running | completed | partial | failed",
      "submitted_at": "...",
      "started_at": "...",
      "completed_at": "...",
      "items_total": N,
      "items_completed": M,
      "items_failed": K,
      "errors": [ { "file_id": "...", "error": "..." } ]
    }
```

**Per-job pipeline** (`workers/job_runner.py`):

```python
async def run_job(job_id: UUID):
    async with job_store.lease(job_id) as job:           # row-lock; status -> running
        sem = asyncio.Semaphore(settings.PER_JOB_CONCURRENCY)  # default 5
        results = await asyncio.gather(*[
            _run_item(sem, job, item) for item in job.items
        ], return_exceptions=True)
        await _push_to_ingest(job, results)              # one /ingest call with all chunks
        await job_store.finalize(job_id, results)

async def _run_item(sem, job, item):
    async with sem:
        client = source_for(item.source)
        bytes_ = await client.fetch(
            item.source_credential, item.credential_type, item.source_descriptor,
        )
        # Doc-processor returns text-only chunks; loader-worker embeds in-process.
        chunks = await doc_processor.process_document(
            bytes_, item.filename, item.content_type,
        )
        embeddings = await embedder.embed_chunks(
            [c.text for c in chunks],
            base_url=job.embedding_config["base_url"],
            api_key=settings.LITELLM_API_KEY,           # env var, sourced from <tenant>-secrets
            model=job.embedding_config["model"],
        )
        for chunk, vec in zip(chunks, embeddings):
            chunk.embedding = vec
        return chunks
```

**Source-credential handling** (load-bearing — do not "fix" by persisting):

Per-item credentials live in **memory only**. They never touch Postgres — not in `jobs.embedding_config`, not in `job_items`, not anywhere. The Alembic-managed schema deliberately has **no `source_credential` column**. This applies to both `credential_type` values, but the failure modes differ:

- **`credential_type: "user_oauth"`** (Phases 0–4 — OneDrive/GDrive sync workers):
  - Provider OAuth access token, ~1h lifetime. TokenManager (`backend/open_webui/services/sync/token_refresh.py:18-63`) guarantees ≥5 minutes remaining when minting.
  - On 401 from the cloud API, return a `TokenExpiredError`; the per-item result is failed-with-retry-hint with `error_code="needs_token_refresh"`; the job continues processing other items; open-webui retries the failed file_ids on the next sync cycle with fresh tokens (see "Failed-item reporting" below).
  - For very long queues (job queued >55 min before items run), token expiry mid-job is expected. Multi-cycle convergence is the accepted behavior. A loader-worker → open-webui token-refresh callback is a viable follow-up but **not** in scope here.

- **`credential_type: "app_token"`** (reserved; not exercised by this plan — the org-scope-acl-mirroring follow-up plan introduces it):
  - Long-lived service-account credential. Lifetime: months-to-years; rotation is a separate operational task, not a per-job concern.
  - **No retry-with-fresh-token loop** — open-webui passes the current app-token from its 1Password-sourced Secret at job-submission time. A 401 from the source API is a hard failure that pages an admin, not a transient retryable error.
  - The retry semantics are documented here so the contract is stable; the dispatch path on `credential_type` is added in this plan, but no source client emits `app_token` until the follow-up plan ships.

Common rules for both types:
- A pod restart loses the in-memory item records and **fails any in-flight job**. The job row goes to `status='failed'` (not `'queued'`) on next-replica recovery, so it isn't retried in-place. Open-webui re-discovers the affected files on the next sync cycle and submits a fresh job with new credentials.
- `Authorization` headers are masked in logs (custom `logging.Filter` that redacts `Bearer .*`). Never include credential bytes in error traces or DB rows.

**Failed-item reporting** (interaction with open-webui's pre-existing delta-cursor limitation):

Open-webui's `BaseSyncWorker._collect_folder_files` advances the per-source `delta_link` immediately when it lists items (`base_worker.py:128`), regardless of downstream processing success. Files that fail processing are tracked in a `failed_files` list but the cursor does not roll back — so failed files are stuck until a full re-sync clears the cursor (`FOLDER_MAP_VERSION` bump or manual reset). This is a **pre-existing limitation** that this plan does not introduce and does not fix.

What this plan **must not make worse**:
- The loader-worker's `GET /jobs/{id}` response includes `errors: [{ "file_id": "...", "error": "...", "error_code": "..." }]` for every failed item.
- `BaseSyncWorker._track_job_progress` (Phase 3.2) feeds those file_ids into the same `failed_files` list the legacy path uses, so the failure surface — and any future hardening like a retry queue — remains a single code path.
- Specifically: `error_code="needs_token_refresh"` does *not* permanently fail the file from open-webui's perspective; the next sync cycle will re-discover it via delta only if the cursor hasn't passed it. Acceptable now; tracked as follow-up alongside the cursor limitation.

**Cancellation** (`POST /jobs/{job_id}/cancel`):

```
POST /jobs/{job_id}/cancel
  (no Authorization header — same-namespace caller, NetworkPolicy-gated)
  → 202 Accepted   { "job_id": "...", "status": "cancelling" }
```

Behavior:
- Sets `status='cancelling'` in the job row.
- The worker pool checks job status before dequeuing the next item; in-flight items are cancelled via `asyncio.CancelledError` propagation through the `asyncio.gather` in `run_job`.
- `_push_to_ingest` is **skipped entirely** on cancel — no partial chunks land in the tenant KB. Items already pushed in a prior batch (none, since we push once at end-of-job today) would stay; the current single-batch push means cancellation = clean rollback.
- Final state: `status='cancelled'`, `items_completed`/`items_failed` reflect the count at cancel time.
- Idempotent: cancelling an already-terminal job returns the current state.

`status` enum updated: `queued | running | cancelling | cancelled | completed | partial | failed`.

**Push-back to tenant** (`ingest_client.py`):

```python
async def push_to_ingest(job, item_results):
    payload = {
        "collection": job.collection,
        "documents": [
            {
                "source_id": r.file_id,
                "filename": r.filename,
                "content_type": r.content_type,
                "metadata": r.metadata,
                "chunks": [
                    {"text": c.text, "embedding": c.embedding, "metadata": c.metadata}
                    for c in r.chunks
                ],
            }
            for r in item_results if r.status == "ok"
        ]
    }
    headers = {
        "Authorization": f"Bearer {settings.LOADER_INGEST_API_KEY}",  # env var, single-tenant pod
        "X-Acting-User-Id": job.acting_user_id,
        "X-Acting-Provider": job.provider_slug,
    }
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{job.callback_base_url}/api/v1/integrations/ingest",
            headers=headers, json=payload,
        )
        resp.raise_for_status()
```

The `LOADER_INGEST_API_KEY` is just an env var on the loader-worker pod, sourced from the tenant's existing `<tenant>-secrets` Secret (the same Secret open-webui mounts for inbound validation). The `acting_user_id` and `provider_slug` are stored on the job row at submission and echoed back here — loader-worker never validates them, it just relays.

**Job store schema** (Postgres, Alembic migration). The DB lives on the **tenant's existing Postgres pod** in a new `loader_worker` database — see 2.3. Because the DB is single-tenant by deployment, there is no `tenant` column and no RLS:

```sql
CREATE TABLE jobs (
    id UUID PRIMARY KEY,
    knowledge_id TEXT NOT NULL,
    acting_user_id TEXT NOT NULL,        -- echoed to /ingest as X-Acting-User-Id
    provider_slug TEXT NOT NULL,         -- echoed to /ingest as X-Acting-Provider
    status TEXT NOT NULL,                -- queued, running, completed, partial, failed
    submitted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    callback_base_url TEXT NOT NULL,
    embedding_config JSONB NOT NULL,     -- base_url, model, dimensions, batch_size; NO api_key (env-var on the pod)
    collection JSONB NOT NULL,
    items_total INTEGER NOT NULL,
    items_completed INTEGER NOT NULL DEFAULT 0,
    items_failed INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_jobs_status_submitted ON jobs(status, submitted_at);

CREATE TABLE job_items (
    job_id UUID NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    file_id TEXT NOT NULL,
    status TEXT NOT NULL,                -- pending, ok, failed
    error TEXT,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    metadata JSONB,
    PRIMARY KEY (job_id, file_id)
);
```

**Worker pool**: a background task per replica polls for `status='queued'` jobs (with a lease/lock pattern using `SELECT ... FOR UPDATE SKIP LOCKED`), runs them, updates rows. This pattern survives a replica restart: an orphaned `running` job whose `started_at` is stale gets reclaimed by the next available replica after a configurable timeout. No tenant context to thread through — the DB is single-tenant.

**Concurrency**: `MAX_CONCURRENT_JOBS_PER_REPLICA` (default 4) and `PER_JOB_ITEM_CONCURRENCY` (default 5). Memory ceiling per replica: ~max_concurrent_jobs * per_item_concurrency * max_file_size = 4 * 5 * 100 MB = 2 GB headroom.

#### 2.3 Loader-worker job DB on the tenant's existing Postgres pod

Each tenant namespace already runs a Postgres pod hosting open-webui's DB (and the `@librechat/agents` agents-state DB). The loader-worker job store lands on that same pod in a new database named `loader_worker`.

**No new HelmRelease, no new iSCSI PV, no new backup chart.** The existing per-tenant Postgres backup that already covers open-webui covers `loader_worker` automatically (whole-pod backup).

**Provisioning** — extend the existing per-tenant Postgres init/bootstrap pattern with a `loader_worker` database creation:

- **File** (new): `/Users/lexlubbers/Code/soev/open-webui/helm/open-webui-tenant/templates/loader-worker/postgres-bootstrap-job.yaml`

  A one-shot `helm.sh/hook: post-install,post-upgrade` Job that runs `psql` against the tenant Postgres and idempotently creates the `loader_worker` DB and a `loader_worker_app` role. Job credentials come from the existing per-tenant `postgresql-credentials` Secret (or whatever the existing pattern is — verify on first tenant).

  ```sql
  -- post-install/post-upgrade hook (idempotent)
  CREATE DATABASE loader_worker;
  CREATE ROLE loader_worker_app LOGIN PASSWORD :'pw';
  GRANT ALL PRIVILEGES ON DATABASE loader_worker TO loader_worker_app;
  ```

  The role password lands in the per-tenant `<tenant>-secrets` (1P field `loaderWorkerDbPassword`, generated by `populate-1password.sh`) and is mounted into the loader-worker pod as `LOADER_DB_PASSWORD`.

- **Alembic migrations** for the `jobs` and `job_items` tables run from an init container on the loader-worker Deployment (`helm.sh/hook: pre-upgrade` or just on pod startup with idempotent migrations). No tenant column, no RLS — the DB is single-tenant by deployment.

**Why this over a dedicated shared-services Postgres**: the previous draft proposed a new shared-services Postgres StatefulSet + iSCSI PV + new backup chart. With the per-tenant deployment shape, sharing a job store across tenants is unnecessary; reusing the tenant's existing Postgres pod saves an entire database deployment per cluster, an iSCSI PV, and a new backup workflow. The existing tenant Postgres is already sized for the workload (open-webui's chats easily exceed loader-worker's job-table size).

**Connection string**: `postgresql+asyncpg://loader_worker_app:${LOADER_DB_PASSWORD}@<existing tenant postgres svc>/loader_worker`. Both env vars (`LOADER_DB_PASSWORD`, `LOADER_DB_HOST`) come from the tenant's existing Secret + ConfigMap; same pattern open-webui uses.

#### 2.4 Loader-worker per-tenant Helm chart

The loader-worker is **not** a shared-services component. It deploys per tenant in the tenant namespace, alongside open-webui.

**Files** (new) — under the existing per-tenant open-webui chart so it ships in the same HelmRelease:

- `/Users/lexlubbers/Code/soev/open-webui/helm/open-webui-tenant/templates/loader-worker/deployment.yaml`
- `.../loader-worker/service.yaml`
- `.../loader-worker/hpa.yaml`
- `.../loader-worker/configmap.yaml`
- `.../loader-worker/networkpolicy.yaml`
- `.../loader-worker/postgres-bootstrap-job.yaml` (from 2.3)

The image continues to be built and published from `genai-utils/api/gateway/loader_worker/` (image source stays in genai-utils; chart lives next to open-webui because that's where the per-tenant deployment is).

**Values** (`values.yaml` of `open-webui-tenant`):

```yaml
loaderWorker:
  enabled: false                   # default off; set true in tenant patch alongside useSharedLoader
  image:
    repository: ghcr.io/gradient-ds/gradient-loader-worker
    tag: ""                        # set per-tenant
  replicaCount: 1
  resources:
    requests: {cpu: 250m, memory: 512Mi}
    limits:   {cpu: 2000m, memory: 4Gi}
  autoscaling:
    enabled: true
    minReplicas: 1
    maxReplicas: 8                 # 1–8 per tenant
    targetCPUUtilizationPercentage: 75
    targetMemoryUtilizationPercentage: 75
  config:
    docProcessorUrl: "http://gradient-doc-processor.shared-services.svc:8001"
    litellmBaseUrl: "http://litellm-proxy.shared-services.svc:4000/v1"
    maxConcurrentJobsPerReplica: 4
    perJobItemConcurrency: 5
  # Postgres is the existing tenant Postgres; just point at the new database.
  postgres:
    database: "loader_worker"
```

**Per-tenant overrides** go in the existing per-tenant HelmRelease values in `soev-gitops/tenants/<cluster>/<tenant>/`:

```yaml
loaderWorker:
  enabled: true
  autoscaling:
    minReplicas: 1
    maxReplicas: 8   # tune per tenant; small tenants can stay at 1
```

**Env on the loader-worker pod** (mounted from the existing `<tenant>-secrets`):

| Env var | Source | Purpose |
|---|---|---|
| `LITELLM_API_KEY` | `<tenant>-secrets` field `openaiApiKey` | LiteLLM bearer for embedding |
| `LOADER_INGEST_API_KEY` | `<tenant>-secrets` field `loaderIngestApiKey` (Phase 1.1) | bearer for outbound `/ingest` callback |
| `LOADER_DB_PASSWORD` | `<tenant>-secrets` field `loaderWorkerDbPassword` (Phase 2.3) | Postgres password |
| `LOADER_DB_HOST` | tenant ConfigMap | tenant Postgres pod DNS |
| `OPENWEBUI_BASE_URL` | tenant ConfigMap | `http://<tenant>-open-webui.<ns>.svc:8080` |

**HPA (memory primary, CPU secondary)** — autoscaling/v2 with both metric types, identical pattern to the existing open-webui-tenant HPA.

**NetworkPolicy** (new, per-tenant):
- **Ingress**: from the tenant's `open-webui` pod by pod-selector `app.kubernetes.io/name=open-webui`; otherwise deny.
- **Egress**: doc-processor (`shared-services` ns, port 8001), LiteLLM (`shared-services` ns, port 4000), tenant Weaviate (same ns), tenant `open-webui` (same ns, for `/ingest`), tenant Postgres (same ns), DNS, internet (for cloud-source HTTPS).

**Service**: `gradient-loader-worker.<tenant>.svc:8002`.

#### 2.5 Source clients in genai-utils

**Files** (new): `loader_worker/sources/onedrive.py`, `loader_worker/sources/google_drive.py`

Port the download-only paths from open-webui:
- From `open-webui/backend/open_webui/services/onedrive/graph_client.py:205-210` (`download_file`)
- From `open-webui/backend/open_webui/services/google_drive/drive_client.py:276-293` (`download_file`, `export_file`) plus `GOOGLE_WORKSPACE_EXPORT_MAP` (lines 13-26)

Auth: each call is a single-use HTTP request using `item.source_credential`. The behavior differs by `credential_type`:
- `user_oauth`: credential is the per-user OAuth access token. 401 → `TokenExpiredError` → per-item retry-on-next-cycle (handled by open-webui).
- `app_token`: credential is a long-lived service-account credential (used by the org-scope-acl-mirroring follow-up plan only — not exercised by this plan). 401 → hard failure surfaced to admin, no auto-retry. Used as-is on every call without per-call refresh.

No credential persistence in either case.

Tests: `pytest`-based, mock the Graph/Drive HTTP layer with `respx`. Verify: 401 raises `TokenExpiredError`, 429 retries with backoff, large file streams without OOM (use `httpx.AsyncClient` `stream`).

### Success Criteria (all local — no cluster deploy in Phase 2)

#### Automated Verification:
- [x] `cd genai-utils && ruff check api/gateway/loader_worker/` passes
- [x] `cd genai-utils && pytest api/gateway/loader_worker/tests/ -v` passes (unit tests for source clients, ingest_client, doc_processor_client, embedder, job_runner with mocked deps)
- [x] Tests cover: `ingest_client` sends `X-Acting-User-Id` and `X-Acting-Provider` headers from the job row; `job_runner` persists `acting_user_id`/`provider_slug` on submission and echoes them on callback
- [x] Tests cover: `embedder` batches, retries on 429/5xx, surfaces 401 as a structured error, and never logs the LiteLLM key bytes
- [x] `cd genai-utils && pytest api/gateway/doc_processor/tests/ -v` passes unchanged (doc-processor is not modified in this plan) — _no doc-processor tests exist; doc-processor source unchanged_
- [x] `helm template` of `open-webui-tenant` with `loaderWorker.enabled=true` renders without errors and includes the loader-worker Deployment, Service, HPA, ConfigMap, NetworkPolicy, and the Postgres bootstrap Job
- [x] CI builds the `gradient-loader-worker` image on the feature branch (doc-processor image unchanged)
- [x] Alembic migrations apply cleanly on a fresh local Postgres (`docker run postgres:16`); the `jobs` table has `acting_user_id` and `provider_slug` columns; no `tenant` column, no RLS policies
- [x] Local smoke test: `docker compose up` with mock cloud + mock tenant ingest + mock LiteLLM + Postgres, submit a one-item job with `acting_user_id="user-abc"` and `provider_slug="onedrive"`, verify the mock ingest endpoint receives the call with both headers set, and the chunks payload has embeddings attached
- [x] Mock-based load test: submit 20 jobs against the local stack, verify job_runner concurrency caps hold (no >`MAX_CONCURRENT_JOBS_PER_REPLICA` simultaneous jobs in `running` state)

**Implementation Note**: Phase 2 ships no cluster changes either. Continue directly into Phase 3. The single staging cutover happens at the end of Phase 3 (section 3.6).

### Forward Compatibility (Phases 5 and 6)

The contract finalized in this phase is deliberately RMQ-shaped *and* extension-shaped. Two design choices made here unlock later phases without a contract migration:

1. **`source_credential` + `credential_type` (instead of `ephemeral_token`)**. Today the only `credential_type` value emitted is `user_oauth`. The org-scope-acl-mirroring follow-up plan adds `app_token` — same field, different value, no schema change. The loader-worker's `SourceClient.fetch()` dispatches on `credential_type` to apply the correct retry semantics (see 2.5).

2. **Per-item `metadata` field is opaque to the loader-worker**. A future plan can piggyback on this to carry per-item `acl_principals: list[str]` without the loader-worker needing to know what those bytes mean. The doc-processor also ignores it. Only the open-webui `/ingest` endpoint, on receipt, decides what to do with such metadata.

3. **The `collection.data_type` enum** (`chunked_embedded`, etc.) is the dispatch point for any future ingest behavior — additive `data_type` values can be added without changing the contract.

What this means for migrations: future plans built on this contract get to add opaque fields (new `credential_type` values, new `metadata` keys, new `data_type` values) without field renames, type changes, or breaking changes. A loader-worker built at Phase 2 will accept future jobs unchanged in shape — though it will need a code update to implement any new `credential_type` retry semantics or `data_type` write paths on the open-webui side.

The per-tenant deployment also means future ACL-aware syncs need no additional tenant-isolation work in the loader-worker itself — each tenant's loader-worker pod only ever sees its own credentials and its own data.

---

## Phase 3: Shrink Open-WebUI Sync Workers

### Overview

Replace the heavy in-pod work in `BaseSyncWorker` with a thin RPC client. Net deletion in open-webui. Old code path stays under a feature flag for instant rollback.

### Changes Required

#### 3.1 New module: `services/sync/pipeline_client.py`

**File** (new): `/Users/lexlubbers/Code/soev/open-webui/backend/open_webui/services/sync/pipeline_client.py`

Single file, ~80 LoC. Wraps `httpx.AsyncClient` to call loader-worker:

```python
from typing import Any
import os
import httpx

class PipelineClient:
    def __init__(self):
        # Same-namespace service DNS, no bearer (NetworkPolicy gates pod-to-pod).
        self._base = os.environ["LOADER_WORKER_URL"]
        self._tenant = os.environ["TENANT_NAME"]
        self._timeout = httpx.Timeout(connect=5, read=30, write=30, pool=30)

    async def submit_job(
        self,
        knowledge_id: str,
        acting_user_id: str,        # the open-webui user.id who triggered the sync
        provider_slug: str,         # e.g. "onedrive", "google_drive"
        callback_base_url: str,
        embedding_config: dict,
        collection: dict,
        items: list[dict],
    ) -> str:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base}/tenants/{self._tenant}/jobs",
                json={
                    "knowledge_id": knowledge_id,
                    "acting_user_id": acting_user_id,
                    "provider_slug": provider_slug,
                    "callback_base_url": callback_base_url,
                    "embedding_config": embedding_config,
                    "collection": collection,
                    "items": items,
                },
            )
            resp.raise_for_status()
            return resp.json()["job_id"]

    async def get_status(self, job_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(f"{self._base}/jobs/{job_id}")
            resp.raise_for_status()
            return resp.json()
```

#### 3.2 Refactor `BaseSyncWorker`

**File**: `/Users/lexlubbers/Code/soev/open-webui/backend/open_webui/services/sync/base_worker.py`

**Add** at the top of the class:

```python
def __init__(self, ..., use_shared_loader: bool = False):
    self._use_shared_loader = use_shared_loader
    self._pipeline_client = PipelineClient() if use_shared_loader else None
```

**Replace** `_download_and_store()` (lines 728–912, ~185 lines) and `_process_and_embed()` (lines 913–1041, ~129 lines) with a branch:

```python
async def _download_and_store(self, file_info, ...):
    if self._use_shared_loader:
        # Just create the File record; bytes never touch this pod
        return await self._create_file_stub(file_info)
    # else: original code path (preserved verbatim under the flag)
    return await self._download_and_store_legacy(file_info, ...)

async def _process_and_embed(self, prepared):
    if self._use_shared_loader:
        return None  # Push from loader-worker → /ingest does file association
    return await self._process_and_embed_legacy(prepared)
```

The legacy methods (`_download_and_store_legacy`, `_process_and_embed_legacy`) are the exact current bodies, unchanged. They get deleted in the cleanup commit after rollout.

**New method**: `_submit_pipeline_job()` is called once per source-fanout batch instead of per-item, after `_collect_folder_files` produces the work list. It assembles `items[]` (one per file) using the same metadata that `_download_and_store_legacy` used to assemble for per-item processing, mints credentials per item via `TokenManager.get_valid_access_token()` (for `user_oauth`), and submits one job per source.

Pseudocode:

```python
async def _submit_pipeline_job(self, source, files):
    if not files:
        return
    token = await self._token_manager.get_valid_access_token(self._user_id, self._knowledge_id)
    items = [self._item_from_file_info(token, f) for f in files]
    job_id = await self._pipeline_client.submit_job(
        knowledge_id=self._knowledge_id,
        acting_user_id=self._user_id,                  # the originating user; echoed back on /ingest
        provider_slug=self.PROVIDER_SLUG,              # "onedrive" / "google_drive" — class constant
        callback_base_url=os.environ["WEBUI_PUBLIC_BASE_URL"],
        embedding_config={
            "base_url": app.state.config.RAG_OPENAI_API_BASE_URL,
            "model": app.state.config.RAG_EMBEDDING_MODEL,
            # api_key intentionally omitted — loader-worker sources LITELLM_API_KEY from its own env,
            # which is mounted from the existing tenant <tenant>-secrets Secret.
        },
        collection=self._collection_for(source),
        items=items,
    )
    # Track job_id in the worker's progress state so emit_sync_progress can poll
    self._active_jobs[source.id] = job_id
```

The semaphore-bounded fan-out in `sync()` is bypassed when `_use_shared_loader=True` — the loader-worker has its own concurrency. The progress tracking switches to "poll loader-worker job status, emit Socket.IO". Concretely:

```python
async def _track_job_progress(self, job_id, source):
    while True:
        status = await self._pipeline_client.get_status(job_id)
        await emit_sync_progress(
            self._event_prefix, self._user_id, self._knowledge_id,
            status="syncing",
            files_done=status["items_completed"],
            files_total=status["items_total"],
        )
        if status["status"] in ("completed", "partial", "failed"):
            return status
        await asyncio.sleep(2)
```

#### 3.3 Drop download methods from provider clients

**File**: `/Users/lexlubbers/Code/soev/open-webui/backend/open_webui/services/onedrive/graph_client.py`
- Remove `download_file()` (lines 205–210)

**File**: `/Users/lexlubbers/Code/soev/open-webui/backend/open_webui/services/google_drive/drive_client.py`
- Remove `download_file()` (lines 276–286)
- Remove `export_file()` (lines 288–293)
- Remove `GOOGLE_WORKSPACE_EXPORT_MAP` (lines 13–26)

These methods are only called from `_download_file_content` in the per-provider sync workers; once those are also removed (next item), there are no callers.

**File**: `/Users/lexlubbers/Code/soev/open-webui/backend/open_webui/services/onedrive/sync_worker.py`
- Remove `_download_file_content()` (lines 252–256) — only called from legacy path

**File**: `/Users/lexlubbers/Code/soev/open-webui/backend/open_webui/services/google_drive/sync_worker.py`
- Remove `_download_file_content()` (lines 198–209)

**Important**: under the feature flag, the legacy path needs these methods. So actually, **they stay during Phase 3 rollout** and are removed in the final cleanup commit (after Phase 4). Mark them with a docstring `# Removed in cleanup commit after USE_SHARED_LOADER rollout completes` so the cleanup is mechanical.

#### 3.4 Feature flag wiring

**File**: `/Users/lexlubbers/Code/soev/open-webui/backend/open_webui/config.py`

Add:

```python
USE_SHARED_LOADER = PersistentConfig(
    "USE_SHARED_LOADER",
    "USE_SHARED_LOADER",
    os.getenv("USE_SHARED_LOADER", "False").lower() == "true",
)
LOADER_WORKER_URL = os.getenv("LOADER_WORKER_URL", "")
TENANT_NAME = os.getenv("TENANT_NAME", "")
```

**File**: `/Users/lexlubbers/Code/soev/open-webui/backend/open_webui/main.py`

Pass the flag through `app.state.config.USE_SHARED_LOADER` and into `SyncProvider.execute_sync()` so the worker constructor receives `use_shared_loader=...`.

**File**: `/Users/lexlubbers/Code/soev/open-webui/backend/open_webui/services/sync/provider.py`

Plumb the flag from `execute_sync()` into `create_worker()`. Both ABC method signatures need updating; concrete providers in `services/onedrive/provider.py` and `services/google_drive/provider.py` accept the new param.

**File**: `/Users/lexlubbers/Code/soev/open-webui/helm/open-webui-tenant/templates/configmap.yaml`

Add env vars: `USE_SHARED_LOADER`, `LOADER_WORKER_URL`, `TENANT_NAME`. Default `USE_SHARED_LOADER: "false"`. Tenant-specific override goes in the per-tenant Helm values.

#### 3.5 Internationalization

No new user-facing strings in this phase. Per `collab/world/preferences.md`, all new user-facing text must be added to both `en-US/translation.json` and `nl-NL/translation.json`. Phase 3 doesn't introduce any.

#### 3.5.local Local end-to-end verification harness (pre-cutover dress rehearsal)

**This section is the release gate for Phases 1–3.** Phase 2's success criteria already include a smoke test against a *mocked* tenant ingest, but that proves only the loader-worker half of the stack. Before the single staging cutover at 3.6, a developer must verify the full integrated stack — real loader-worker talking to real open-webui (with `USE_SHARED_LOADER=true`) — on a laptop. This mirrors the `5.6.local` / `6.6.local` harnesses for Phases 5 and 6.

**File** (new): `/Users/lexlubbers/Code/soev/genai-utils/docker-compose.loader-worker.yaml`

```yaml
services:
  postgres-owui:               # open-webui's per-tenant DB
    image: postgres:16
    environment:
      POSTGRES_DB: openwebui
      POSTGRES_USER: owui
      POSTGRES_PASSWORD: dev

  postgres-loader:             # loader-worker job store (in cluster: same pod, different DB; here: separate for simplicity)
    image: postgres:16
    environment:
      POSTGRES_DB: loader_worker
      POSTGRES_USER: loader_worker_app
      POSTGRES_PASSWORD: dev

  weaviate:                    # tenant Weaviate
    image: semitechnologies/weaviate:1.27
    environment:
      AUTHENTICATION_ANONYMOUS_ACCESS_ENABLED: "true"
      DEFAULT_VECTORIZER_MODULE: none

  doc-processor:               # real shared-services parse+chunk
    build:
      context: ./api/gateway/doc_processor

  litellm-stub:                # mock embeddings (real LiteLLM keys cost money + nondeterministic)
    build:
      context: ./tests/fixtures/litellm-stub
    # Returns deterministic 1536-dim vectors keyed by sha256(text).
    # Validates Authorization: Bearer ${LITELLM_API_KEY} header is present.

  cloud-stub:                  # mock OneDrive + Google Drive download endpoints
    build:
      context: ./tests/fixtures/cloud-stub
    # GET /onedrive/items/{id}/content → bytes from a fixtures dir
    # GET /drive/files/{id}?alt=media → bytes
    # GET /drive/files/{id}/export?mimeType=... → bytes (Workspace export path)
    # Returns 401 on a header-controlled fault-injection toggle (for token-expiry tests).

  loader-worker:
    build:
      context: ./api/gateway/loader_worker
    depends_on: [postgres-loader, doc-processor, litellm-stub]
    environment:
      LOADER_DB_HOST: postgres-loader
      LOADER_DB_PASSWORD: dev
      LOADER_INGEST_API_KEY: dev-loader-ingest-key
      LITELLM_API_KEY: dev-litellm-key
      DOC_PROCESSOR_URL: http://doc-processor:8001
      LITELLM_BASE_URL: http://litellm-stub:4000/v1
      MAX_CONCURRENT_JOBS_PER_REPLICA: "2"
      PER_JOB_ITEM_CONCURRENCY: "3"
    ports: ["8002:8002"]

  open-webui:                  # real open-webui from this branch
    build:
      context: ../open-webui
    depends_on: [postgres-owui, weaviate, loader-worker]
    environment:
      DATABASE_URL: postgresql://owui:dev@postgres-owui:5432/openwebui
      WEBUI_SECRET_KEY: dev-secret
      VECTOR_DB: weaviate
      WEAVIATE_URL: http://weaviate:8080
      USE_SHARED_LOADER: "true"
      LOADER_WORKER_URL: http://loader-worker:8002
      LOADER_INGEST_API_KEY: dev-loader-ingest-key   # same value, both sides
      TENANT_NAME: dev
      # Re-point the cloud APIs at the stub
      MICROSOFT_GRAPH_BASE_URL: http://cloud-stub:9001/onedrive
      GOOGLE_DRIVE_BASE_URL: http://cloud-stub:9001/drive
    ports: ["8080:8080"]
```

**Smoke-test script** (new): `/Users/lexlubbers/Code/soev/genai-utils/tests/integration/test_loader_worker_e2e.sh`

The test must exercise the full chain end-to-end without any cluster:

1. Start the stack (`docker compose -f docker-compose.loader-worker.yaml up -d --build --wait`).
2. Bootstrap an open-webui user via the API (`POST /api/v1/auths/signup` or admin-signin pattern). Capture the user `id` (UUID) and a session cookie.
3. Pre-load `cloud-stub` fixtures: 3 OneDrive items (one PDF, one DOCX, one image) and 1 Google Drive item (a Workspace doc requiring export).
4. Configure a OneDrive KB on open-webui (`POST /api/v1/knowledge/cloud-sync/...`) pointing at the stub folder. Trigger a sync.
5. Poll loader-worker `GET /jobs/{job_id}` until terminal. Assert `status=completed` and `items_completed=4`.
6. **Auth-path assertions** (the heart of this gate):
   - The `cloud-stub` access log shows that `Authorization` headers used to fetch items came **from loader-worker**, not from open-webui (proving downloads moved off the open-webui pod).
   - The open-webui access log shows the `/api/v1/integrations/ingest` call presented `Authorization: Bearer dev-loader-ingest-key` and `X-Acting-User-Id: <user_id>`, `X-Acting-Provider: onedrive`.
   - The `litellm-stub` access log shows the embedding bursts came **from loader-worker** with `Authorization: Bearer dev-litellm-key`, not from open-webui.
7. **Data-path assertions**:
   - Query Weaviate directly: chunk count > 0 for the new KB.
   - Each chunk's `metadata.created_by` (or equivalent provenance field on the File record) equals the bootstrapped user `id`, **not** a service-account placeholder.
   - Each `File` row in `postgres-owui` has `user_id = <bootstrapped user id>`.
8. **Cancellation**: trigger a second sync, immediately call loader-worker `POST /jobs/{id}/cancel` mid-flight, assert `status=cancelled` and that **no** chunks from the cancelled job landed in Weaviate (single-batch `_push_to_ingest` is skipped on cancel — see 2.2 Cancellation).
9. **Token expiry**: flip the `cloud-stub` 401 fault-injection on, trigger a third sync. Assert per-item `error_code="needs_token_refresh"` is reported in `GET /jobs/{id}` and that the failed file_ids surface in open-webui's `failed_files` list (the legacy code-path's failure surface is preserved).
10. **Regression — legacy path**: stop the stack; restart with `USE_SHARED_LOADER=false` on the open-webui service; trigger a sync; assert it still works (proves the feature flag is a real switch and rollback is one env var away).
11. **Log scrub**: `docker compose logs` | `grep -E '(Bearer [A-Za-z0-9._-]{16,}|sk-[A-Za-z0-9]{30,}|EAA[A-Za-z0-9]{50,})'` returns no hits across all services.

**This harness is the release gate**: the 3.6 staging cutover **MUST NOT** be attempted until `test_loader_worker_e2e.sh` passes from a clean checkout on a developer laptop. The staging cutover then becomes a re-run of steps 4–9 against the deployed stack — same assertions, real cloud APIs, real LiteLLM.

**Iterate locally on failure**: any bug found here goes back to the corresponding phase (1, 2, or 3) as a fix; re-run that phase's unit tests, then re-run the full harness. Cluster touch only after this gate is green.

**What this harness deliberately does NOT cover** (and is therefore left for the staging cutover to verify):
- Real Microsoft Graph / Google Drive OAuth flow (stub uses static tokens; OAuth refresh interactions with `TokenManager` are exercised by their existing unit tests, not here).
- Real LiteLLM team-virtual-key rate limits and budget (stubbed; `1Password rate-limit budget` is a staging-soak concern, not local).
- Real Weaviate cluster mode / replication (single-node here; staging is also single-node so this is a small gap).
- HPA scaling behavior (no autoscaler in compose; capped concurrency is exercised via the existing Phase 2 mock-based load test).

#### 3.6 Single full-stack staging cutover

> **Superseded by Amendment 2026-04-27.** This section is **deferred** — Phase 3 now ends with the 3.5.local harness green, and the staging cutover happens at the end of Phase 5's local block as the **combined staging cutover** (see new section after Phase 5). The contents below are retained for context but are not the deployment trigger anymore.

This is the **first cluster deploy** of the entire plan. All of Phases 1–3 ship together to staging.

**Files**:
- `soev-gitops/tenants/previder-prod-staging/staging/helmrelease.yaml` — set:
  - `openWebui.config.useSharedLoader: "true"`
  - `loaderWorkerUrl: "http://gradient-loader-worker.staging.svc:8002"` (in-namespace)
  - `tenantName: "staging"`
  - `loaderWorker.enabled: true`, `loaderWorker.autoscaling.minReplicas: 1`, `maxReplicas: 3` (staging-sized)
  - `loaderWorker.image.tag: <feature-branch-tag>`

**No shared-services changes in this cutover.** Doc-processor is untouched (still parse+chunk only). No new shared-services Postgres, no new ExternalSecrets in `staging-shared-services`.

**Provisioning**:
1. Run `populate-1password.sh staging` to add `loaderIngestApiKey` and `loaderWorkerDbPassword` to the staging tenant vault (single tenant 1P item; both fields land in `<tenant>-secrets`).
2. Force ESO sync on the tenant ESO (only side that needs the new fields).
3. Push the gitops PR; Flux reconciles the staging tenant HelmRelease, which:
   - Runs the post-install Job to create the `loader_worker` DB and `loader_worker_app` role on the staging tenant Postgres
   - Brings up the loader-worker Deployment, Service, HPA, NetworkPolicy
   - Restarts the open-webui pod with `USE_SHARED_LOADER=true`

**End-to-end smoke test**:
- Trigger a OneDrive sync of a small folder (5–10 files) from the staging tenant UI.
- Verify in loader-worker logs: job received, items downloaded, doc-processor called for parse+chunk, LiteLLM called for embedding, callback to `/ingest` returns 200.
- Verify in open-webui logs: `/ingest` accepted the loader bearer, used the acting `user_id` for the File records.
- Verify in the staging KB UI: files appear with the correct owner (the user who triggered the sync, not a service account).
- Pod-log grep: no OAuth tokens, no API keys, no LiteLLM keys leaked.

**Iterate locally if broken**: bugs found here go back to the corresponding phase as fixes; re-run that phase's automated tests; bump image tags; re-deploy. Do **not** touch any production tenant until staging is clean.

### Success Criteria

#### Automated Verification (run after each of Phases 1, 2, 3 — all local):
- [x] `cd open-webui && npm run lint:backend` passes
- [x] `cd open-webui && npm run check` (svelte-check, no TS errors) — _no frontend changes in Phase 3; pre-existing svelte errors unaffected_
- [x] `cd open-webui && pytest backend/open_webui/services/sync/tests/ -v` includes new tests for `pipeline_client.py` (mocked httpx, verifies `acting_user_id` and `provider_slug` in the JSON payload) and the legacy/shared-loader branches in `BaseSyncWorker` — _added at `backend/open_webui/test/util/test_pipeline_client.py` (11 tests, all passing)_
- [x] `helm template` of open-webui-tenant chart with `useSharedLoader: true` and `loaderWorker.enabled: true` produces valid manifests with `USE_SHARED_LOADER`, `LOADER_WORKER_URL`, `TENANT_NAME`, `LOADER_INGEST_API_KEY` on the open-webui pod, and the loader-worker Deployment + Service + HPA + Postgres bootstrap Job alongside it
- [x] No new strings missing from `nl-NL/translation.json` (`npm run i18n:parse` clean) — _Phase 3 introduces no new user-facing strings_
- [ ] Confirm `git diff --stat` shows net-deletion in `services/sync/base_worker.py` (+50 / -80 or thereabouts) — _NOTE: this happens in the post-rollout cleanup commit; during Phase 3 the legacy methods (`_*_legacy`) are preserved verbatim under the feature flag, so the diff is +338/-2 (additions for branching + new pipeline path)._
- [ ] **Release gate**: `cd genai-utils && bash tests/integration/test_loader_worker_e2e.sh` passes from a clean checkout on a developer laptop (the 3.5.local harness; **the 3.6 staging cutover MUST NOT be attempted until this is green**)

#### Manual Verification (single staging cutover only):
- [ ] After the gitops PR merges, `flux reconcile kustomization shared-services` on staging succeeds; loader-worker, Postgres, and updated doc-processor pods all Ready
- [ ] Staging open-webui pod restarts with `USE_SHARED_LOADER=true`; check env vars
- [ ] OneDrive sync of a small folder (5–10 files) succeeds end-to-end: discovery → job submitted → chunks land in staging Weaviate → KB UI shows files **owned by the user who triggered the sync** (not a service account)
- [ ] Socket.IO `onedrive:sync:progress` events fire and reflect loader-worker progress
- [ ] Cancellation: cancel a running sync from the UI; loader-worker job status moves to `cancelled` (loader-worker exposes `POST /jobs/{id}/cancel`)
- [ ] Token expiry mid-job: revoke a token at Microsoft, retry sync; expect graceful failure with `needs_reauth` flag set on the KB
- [ ] **Regression**: temporarily flip `useSharedLoader: "false"` on staging, run an OneDrive sync, confirm the legacy path still works. Flip back to `true`.
- [ ] Pod-log grep across loader-worker, doc-processor, open-webui: no OAuth tokens, no LiteLLM keys, no `LOADER_INGEST_API_KEY` values appearing in logs

**Implementation Note**: Iterate on staging until all checks pass. Only then proceed to Phase 4 (per-tenant production rollout).

---

## Phase 4: Per-Tenant Production Rollout

> **Amended 2026-04-27 (rev 2)**: Phase 4 now rolls **loader-worker AND the agent retrieval endpoint together**, per tenant. Each tenant flip provisions `loaderIngestApiKey` in 1Password, ensures the cluster-wide `agent-keys` Secret is mounted on this tenant's open-webui pod, flips both `USE_SHARED_LOADER` and `AGENT_SEARCH_ENABLED` feature flags, and brings up the loader-worker pod. **No shared-services authz Deployment to roll out — the agent endpoint lives on the existing per-tenant open-webui pod.** The per-tenant order, soak criteria, and rollback procedure below extend to both components — see "Combined rollout deltas" subsection after 4.3.

### Overview

With staging soaking on the new combined path (loader-worker + agent retrieval endpoint, from the combined cutover at the end of Phase 5's local block), roll across production tenants one at a time. The old code path remains in the binary the entire time for instant rollback.

### Changes Required

#### 4.1 Soak window on staging

Before touching any production tenant, let staging run a representative workload.

**Monitor**:
- Alloy logs for the loader-worker (LGTM stack, dashboard panel: error rate, job duration p50/p95/p99, queue depth)
- Tenant Weaviate document counts before/after — they should match expected
- Open-webui sync metrics — `onedrive:sync:progress` event volume, sync completion rate
- HPA scale events (memory triggered vs CPU triggered)
- Postgres connection pool saturation
- 1Password rate-limit budget (Mimir alerting via the same path used for the LiteLLM incident in 2026-04-21)

**Synthetic load**: at least one large-folder sync (1000+ files); cross-reference against the Vink experience (`thoughts/shared/research/2026-04-06-vink-deployment-large-scale-processing-hardening.md` if the file exists; otherwise the equivalent index entry from the open-webui collab memory).

**Exit criteria** (must all be met before flipping the first production tenant):
- 0 sync failures attributable to loader-worker (token expiry retries don't count)
- p95 per-file latency within 1.5x of legacy in-pod path
- No memory-pressure restarts of loader-worker pods
- No 1Password rate-limit warnings
- All File records on the staging KB are owned by the human who triggered the sync (no orphaned ownership)

#### 4.2 Production rollout

**Order**: `gradient` → `demo` → `kwink` → `haute-equipe` → `haagsebeek` → `octobox` → `mkbot` (if unsuspended) → `intermax-prod/soev-test`.

**Rationale**: `gradient` first because it is the dogfooding tenant — own-team pain absorbed before any external customer is touched. `demo` second because it has low usage and any visible regression is recoverable without customer impact. `kwink`/`haute-equipe`/`haagsebeek` are mid-sized established tenants where the new-path performance benefits are most visible. `octobox` and `mkbot` later because they have agent-stack secrets that depend on the same shared-services namespace and add moving parts. `intermax-prod` last because it is the largest by data volume.

**Cadence**: one tenant at a time with explicit verification between each. Each tenant flip is two changes:
1. Run `populate-1password.sh <tenant>` to add `loaderIngestApiKey` and `loaderWorkerDbPassword` to that tenant's vault. Force ESO sync.
2. PR to `soev-gitops/tenants/previder-prod/<tenant>/helmrelease.yaml`:
   - Set `openWebui.config.useSharedLoader: "true"`, `loaderWorkerUrl: "http://gradient-loader-worker.<tenant>.svc:8002"`, `tenantName: "<tenant>"`.
   - Set `loaderWorker.enabled: true`, `loaderWorker.image.tag: <prod-tag>`, autoscaling `minReplicas: 1`, `maxReplicas: 8` (tune per tenant scale).
   - Flux reconciliation runs the post-install Job to create `loader_worker` DB on that tenant's Postgres, then brings up loader-worker pods alongside open-webui in the tenant namespace.

**No shared-services changes per tenant flip.** The shared-services namespace is untouched throughout production rollout.

**Per-tenant verification after flip**:
- Manual sync of a small folder works end-to-end
- Files owned by the triggering user (not a service account)
- Sync progress events fire correctly
- No errors in loader-worker logs scoped to that tenant slug

**Rollback procedure for any tenant**:
1. Revert `useSharedLoader: "true"` → `"false"` in the tenant helmrelease.yaml.
2. Optionally set `loaderWorker.enabled: false` to drop the loader-worker pods (or leave them up; they'll just sit idle).
3. Flux reconciles, open-webui pod restarts on legacy path.
4. Old code is still in the open-webui binary; no image rollback needed.
5. Secrets and the `loader_worker` DB stay provisioned (harmless when unused).

#### 4.3 Final cleanup commit

Once all tenants are running on `useSharedLoader: true` and verified clean:

**File**: `/Users/lexlubbers/Code/soev/open-webui/backend/open_webui/services/sync/base_worker.py`
- Delete `_download_and_store_legacy()` and `_process_and_embed_legacy()`
- Remove the `if self._use_shared_loader` branches; the methods become single-path
- Remove `self._use_shared_loader` from `__init__`

**File**: `services/onedrive/sync_worker.py` and `services/google_drive/sync_worker.py`
- Delete `_download_file_content()`

**File**: `services/onedrive/graph_client.py`
- Delete `download_file()`

**File**: `services/google_drive/drive_client.py`
- Delete `download_file()`, `export_file()`, `GOOGLE_WORKSPACE_EXPORT_MAP`

**File**: `config.py`
- Remove `USE_SHARED_LOADER` (the flag is no longer wired; `LOADER_WORKER_URL` and `TENANT_NAME` stay)

**File**: provider.py and ABC method signatures
- Drop the `use_shared_loader` parameter

**File**: `backend/open_webui/utils/service_auth.py`
- No change. This file stays — it's the auth path the loader-worker uses on every callback. It is not feature-flagged and has no legacy partner to delete.

**Cleanup sweep checklist** (avoid leaving dead code):
- [ ] `EXTERNAL_PIPELINE_URL` and `EXTERNAL_DOCUMENT_LOADER_URL` env vars **stay** — direct user-uploads in chat still use them per the "What We're NOT Doing" section. Do not remove.
- [ ] `populate-1password.sh`: the script is purely additive in this plan; no legacy code path was added. No removal needed.
- [ ] Confirm no orphaned `# Removed in cleanup commit after USE_SHARED_LOADER rollout completes` comments remain in the codebase: `rg "Removed in cleanup commit"` should return zero hits after the commit.
- [ ] Confirm no orphaned imports of `_download_file_content`, `download_file`, `export_file`, or `GOOGLE_WORKSPACE_EXPORT_MAP` remain: each has been deleted from its source file *and* from any test file that imported it.
- [ ] Update the README under `services/sync/` (if it exists) to remove references to in-pod download.

**Net effect after Phase 4 cleanup**: `git diff --stat` from main shows approximately:
- **`backend/open_webui/services/sync/`**: -290 / +80 (the bulk of the win)
- **`backend/open_webui/utils/service_auth.py`**: +60 (new file, kept; loader-worker auth path)
- **`backend/open_webui/routers/integrations.py`**: small touch (+10 / -5) for the `Depends` swap

**Net effect anticipating Phase 5** (per amendment 2026-04-27 rev 2, Phase 5 ships in lockstep with this rollout): adds `backend/open_webui/routers/internal_retrieval.py` (+50, one new endpoint), `backend/open_webui/services/retrieval/agent_search.py` (+80, the extracted pipeline), `+30` LoC in `service_auth.py` for `AgentPrincipal` + allow-list, and a small refactor in `routers/retrieval.py` (-15 / +20 — chat retrieval call site updated to use `run_agent_search`).

**Total fork delta after Phase 4 alone** vs. pre-Phase-3: approximately **-235 net-deleted LoC** in soev-specific code paths. The fork delta to upstream open-webui is materially smaller in the most-touched directories (`services/sync/`, `services/onedrive/`, `services/google_drive/`) which is where the merge-conflict surface lived. Even after Phase 5's additions, the trajectory remains net-deletion vs. the pre-loader-worker baseline.

### Success Criteria

#### Automated Verification:
- [ ] Final cleanup commit: `cd open-webui && npm run check && npm run lint:backend && pytest backend/` all pass
- [ ] Final cleanup commit: `git diff --stat` confirms net deletion vs. pre-Phase-3 in `services/sync/`, `services/onedrive/`, `services/google_drive/`

#### Manual Verification:
- [ ] Staging soak (4.1) exit criteria all met
- [ ] Each production tenant after flip (4.2): a manual sync of a small folder works end-to-end and File records are owned by the triggering user
- [ ] At final cleanup (4.3): full regression — manual upload, OneDrive sync, GDrive sync, KB query all work; no behavior change visible to users
- [ ] Post-cleanup, attempt to merge an upstream open-webui release branch into our dev branch — verify the conflict count in `services/sync/`, `services/onedrive/`, `services/google_drive/` is at most equal to (and ideally less than) what it would have been before

**Implementation Note**: Each tenant flip in Phase 4.2 is its own pause-for-confirmation point. Do not auto-flip the next tenant.

#### 4.4 Combined-rollout deltas (Amendment 2026-04-27 rev 2)

Per the amendment, Phase 4 rolls loader-worker AND the agent retrieval endpoint together to each tenant. Concrete additions to the per-tenant flip in 4.2:

**Per-tenant provisioning** (extends step 1 of 4.2):
1. `populate-1password.sh <tenant>` — adds `loaderIngestApiKey` and `loaderWorkerDbPassword` to the tenant 1P vault.
2. Confirm the cluster-wide `agent-keys` 1P item is provisioned (one-time, before the first prod flip — bootstrapped during the staging cutover). New agents added later just need a new field in that item; no per-tenant work.
3. Force ESO sync on the tenant ESO. Verify `<tenant>-secrets` and the projected `agent-keys` Secret both land in the tenant namespace.

**Per-tenant gitops PR** (extends step 2 of 4.2) sets:
- `openWebui.config.useSharedLoader: "true"`, `loaderWorkerUrl: …`, `tenantName: …` (loader-worker, as before)
- `agentSearch.enabled: true`, `agentSearch.expectedAgents: [...]` — turns on the new retrieval endpoint and asserts the expected agent set matches the mounted Secret
- `loaderWorker.enabled: true`, `loaderWorker.image.tag: <prod-tag>`, autoscaling tuned per tenant

There is no shared-services Deployment to update per tenant. Agent-key rotation (rare) edits one field in the cluster-wide 1P item; ESO refresh + 30s mtime poll on every tenant pod picks it up automatically.

**Per-tenant verification after flip** (extends 4.2 verification):
- Manual sync of a small folder works (loader-worker)
- `curl` to `https://<tenant-open-webui>/api/v1/internal/retrieval/query` with a known agent bearer + `X-Acting-User-Id: <staging-user>` returns chunks matching that user's chat-retrieval results
- Negative test: a deliberately invalid bearer → 401; missing `X-Acting-User-Id` → 400; unknown user → 404
- Test agent retrieves from this tenant's KB **without an open-webui per-user API key**

**Combined rollback** (extends 4.2 rollback):
- Loader-worker only fails: revert `useSharedLoader: "true"` → `"false"`. Agent endpoint keeps running.
- Agent endpoint only fails: revert `agentSearch.enabled: true` → `false`. The route unmounts; chat retrieval is unaffected (it shares `run_agent_search` but is not gated by the flag). Loader-worker keeps running. Agent integrations revert to broken for this tenant only.
- Both fail: revert both flags. Open-webui pod restarts on legacy paths. Other tenants unaffected.

**Tenant ordering**: same as 4.2 (`gradient → demo → kwink → …`). Each tenant flip is one pause-for-confirmation point covering both components.

---

## Phase 5: Agent Machine-Auth Retrieval Endpoint

### Overview

Add a single new endpoint to the per-tenant open-webui pod — `POST /api/v1/internal/retrieval/query` — that lets external agents run KB queries on behalf of a specific user, authenticated by a per-agent allow-listed bearer key. The endpoint reuses the existing chat-retrieval pipeline server-side: `AccessGrants.get_accessible_resource_ids()` for KB-level ACL, the existing per-KB Weaviate iteration, the existing reranker. **Open-webui owns ACL state; the agent never reaches Weaviate directly and never sees a filter, principal set, or `kb_mode`.**

This replaces the rev-1 design's `gradient-authz` service in shared-services. See the rev-2 amendment at the top of this plan for the rationale; the short version is that everything the agent actually needs already exists in open-webui's chat-retrieval path, and the only gap is "let a machine prove it's acting on behalf of a user without holding that user's API key."

This phase introduces:

- A `LoaderPrincipal`-shaped `AgentPrincipal` extension in `service_auth.py` (Phase 1.3 module). Same dependency pattern, different bearer source (an allow-list mounted from a Secret directory) and different acting-user-only header set.
- A new internal route on open-webui that runs the existing retrieval pipeline for the resolved acting user.
- A new `agent-keys` 1Password item + ESO ExternalSecret + Secret mount on the open-webui pod, with mtime-poll-driven hot-reload of the allow-list.
- **No source-system principal resolution.** The agent endpoint exposes today's KB-level ACL exactly. Chunk-level ACL (file-level visibility based on source-system permissions) is the responsibility of the standalone [org-scope-acl-mirroring plan](./2026-04-27-org-scope-acl-mirroring.md), which extends `run_agent_search` with a hook when it ships.

**Explicitly NOT in this phase**:
- No `gradient-authz` chart in shared-services. No `INTERNAL_AUTHZ_API_KEY`. No per-tenant projected-volume pattern.
- No Weaviate schema change. No `acl_principals` property on any chunk class. The existing `_create_collection()` in `backend/open_webui/retrieval/vector/dbs/weaviate.py:131-158` is unchanged.
- No agent-side query-construction reference (the agent does not construct queries).
- No outside-the-cluster Ingress for the endpoint — agents run inside the cluster initially. External access via the existing per-tenant open-webui Ingress is a follow-up.

**Cadence (matching Phases 1–3 — local-first, single combined staging cutover, per-tenant prod)**:
- **5.0 spike**: read-only investigation in the open-webui repo. No cluster touch.
- **5.1, 5.2**: code + unit tests + `helm template` validation. **Local only.**
- **5.3**: docker-compose harness as the joint release gate alongside Phase 3.5.local. **Local only.**
- **5.4**: helm + secrets definition. **Local only.**
- **Cluster touch** happens in the Combined Staging Cutover (after Phase 5 local block), per the 2026-04-27 (rev 2) amendment.

A developer must be able to land all of 5.1–5.4 plus the local harness without ever connecting to a cluster.

**Feature flag**: `AGENT_SEARCH_ENABLED` (single env var on the open-webui pod; gates whether the new route is mounted). Default `false` until per-tenant rollout flips it on.

### Prerequisite Spike (block on this before writing Phase 5)

Before writing the rest of this phase as concrete steps, run a 1-day spike to verify:

- **5.0.1** Is the existing chat-retrieval pipeline callable for an arbitrary user without a chat-session context? Find the entry point (likely in `backend/open_webui/routers/retrieval.py` near `_validate_collection_access` at line 2547 and the surrounding query-aggregation function). Spike output: a short doc identifying the smallest "given a user_id and a query, return chunks" function that can be extracted without changing chat-retrieval behavior. If the existing entry point is tightly bound to FastAPI request state or the user's session, factor out the inner work into a pure function called from both the chat router and the new agent endpoint.
- **5.0.2** Confirm `AccessGrants.get_accessible_resource_ids(user_id, 'knowledge', kb_ids)` (`models/access_grants.py:561`) is a clean function with no implicit user-session dependencies. Existing research suggests yes.
- **5.0.3** Confirm the existing reranker and score-fusion logic is in a layer above per-KB Weaviate calls and can be reused unchanged.

**Default position**: if the spike confirms the pipeline is callable as a function, 5.2 is a thin wrapper (~50 LoC). If it's tangled, increase 5.2's time budget; the future org-scope-acl-mirroring plan hooks into the same function so cleanliness here pays for itself later.

### Changes Required

#### 5.1 Agent auth helper + allow-list mount

**File**: `/Users/lexlubbers/Code/soev/open-webui/backend/open_webui/utils/service_auth.py` (extends Phase 1.3)

Add an `AgentPrincipal` dataclass and a `get_agent_principal` dependency that mirrors `get_integration_principal` but with two differences:
1. The bearer is validated against an **allow-list** loaded from a directory of files (one bearer per file), not a single env var. This makes per-agent rotation cheap (edit one 1P field, ESO refreshes the matching file, mtime poll picks it up).
2. Only `X-Acting-User-Id` is required — there's no per-call provider override (agent search is provider-agnostic; the user's existing KB grants determine reachable content).

```python
# additions in service_auth.py
import time
from pathlib import Path

@dataclass
class AgentPrincipal:
    """Machine-auth principal for an external agent acting on behalf of a user."""
    agent_id: str             # the filename of the matched key (e.g. "langgraph_foo")
    user: object              # resolved Users row

    @property
    def id(self):
        return self.user.id


class _AgentKeyAllowList:
    """Loads bearer keys from a directory; reloads on mtime change. Thread-safe."""
    def __init__(self, dir_path: str, poll_interval_s: float = 30.0):
        self._dir = Path(dir_path)
        self._interval = poll_interval_s
        self._mtime: float = 0
        self._keys: dict[str, str] = {}   # bearer -> agent_id

    def _maybe_reload(self):
        if not self._dir.is_dir():
            self._keys = {}
            return
        latest = max((p.stat().st_mtime for p in self._dir.iterdir() if p.is_file()), default=0)
        if latest > self._mtime:
            self._keys = {p.read_text().strip(): p.name for p in self._dir.iterdir() if p.is_file()}
            self._mtime = latest

    def lookup(self, bearer: str) -> Optional[str]:
        self._maybe_reload()
        for known, agent_id in self._keys.items():
            if hmac.compare_digest(bearer, known):
                return agent_id
        return None


_agent_keys = _AgentKeyAllowList(os.environ.get("AGENT_KEYS_DIR", "/var/agent-keys"))


async def get_agent_principal(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    x_acting_user_id: Optional[str] = Header(default=None, alias="X-Acting-User-Id"),
):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    agent_id = _agent_keys.lookup(token)
    if not agent_id:
        raise HTTPException(401, "invalid agent bearer")
    if not x_acting_user_id:
        raise HTTPException(400, "X-Acting-User-Id header is required")
    user = Users.get_user_by_id(x_acting_user_id)
    if not user:
        raise HTTPException(404, f"acting user '{x_acting_user_id}' not found")
    return AgentPrincipal(agent_id=agent_id, user=user)
```

**Allow-list mount**: a Kubernetes Secret with one key per agent (filename = agent_id, content = bearer value), mounted as a directory at `/var/agent-keys` on the open-webui pod. ESO syncs from a cluster-wide 1Password item `agent-keys`. Hot-reload via the 30s mtime poll above (Kubernetes mounts Secrets via atomic symlink swaps that `inotify` watches on leaf files miss; polling is bulletproof and key rotation is rare).

**1Password schema**: a new shared-services 1P vault item `agent-keys` with one field per allow-listed agent (e.g. `langgraph_dev`, `n8n_test`). Adding a new agent: edit the 1P item, force ESO sync, the open-webui pod's mtime poll picks up the new file within 30s. No restart.

**Default position**: agents are allow-listed by name (cluster-wide). Anonymous agent access is not supported. Adding a new agent = one field in 1P + one ESO refresh + one PR appending the agent name to an `expectedAgents:` list in chart values for the helm-template assertion.

**Trade-off documented**: a leaked agent bearer compromises read access for that agent across **every tenant the agent is allow-listed for**, scoped to whatever `acting_user_id` the attacker can guess (UUIDs, opaque). Per-tenant agent keys are a follow-up if a customer requires it; the operational simplicity of cluster-wide keys wins for the initial release.

#### 5.2 New retrieval endpoint

**File** (new): `/Users/lexlubbers/Code/soev/open-webui/backend/open_webui/routers/internal_retrieval.py`

Single endpoint, ~80 LoC including imports and pydantic models:

```python
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from typing import Optional

from open_webui.utils.service_auth import AgentPrincipal, get_agent_principal
from open_webui.services.retrieval.agent_search import run_agent_search

router = APIRouter()


class AgentSearchBody(BaseModel):
    query: str = Field(..., min_length=1)
    top_k: int = Field(10, ge=1, le=100)
    kb_ids: Optional[list[str]] = None      # optional explicit subset of accessible KBs


class AgentSearchResult(BaseModel):
    kb_id: str
    file_id: str
    chunk: str
    score: float
    metadata: dict


@router.post('/query')
async def agent_query(
    body: AgentSearchBody,
    request: Request,
    principal: AgentPrincipal = Depends(get_agent_principal),
):
    if not request.app.state.config.AGENT_SEARCH_ENABLED:
        raise HTTPException(404, "agent search not enabled")
    results = await run_agent_search(
        request=request,
        user=principal.user,
        query=body.query,
        top_k=body.top_k,
        kb_ids=body.kb_ids,
    )
    # Audit log entry written inside run_agent_search; the response shape is final.
    return {"results": [AgentSearchResult(**r).model_dump() for r in results]}
```

**Mount path**: `/api/v1/internal/retrieval/query` (matches the `internal/` namespace established in earlier drafts; the `internal/` prefix and a separate router make machine-auth routes grep-able and isolatable).

**Pipeline file** (new): `/Users/lexlubbers/Code/soev/open-webui/backend/open_webui/services/retrieval/agent_search.py`

This is the smallest extraction the spike (5.0) confirms is feasible — it pulls the inner work of the chat-retrieval router (ACL → per-KB Weaviate iteration → rerank → score fusion) into a function that takes `(user, query, top_k, kb_ids?)` and returns a list of result dicts. The chat-retrieval router and the new agent endpoint both call it.

```python
async def run_agent_search(*, request, user, query, top_k, kb_ids=None):
    accessible_kbs = resolve_user_accessible_kbs(user.id)   # see 5.0 spike output
    if kb_ids is not None:
        accessible_kbs = [kb for kb in accessible_kbs if kb.id in set(kb_ids)]

    results = []
    for kb in accessible_kbs:
        kb_chunks = await _query_kb_collection(
            collection=kb.collection_name,
            query=query,
            top_k=top_k,
        )
        results.extend(kb_chunks)

    return _rerank_and_truncate(results, query, top_k)
```

The helpers `_query_kb_collection` and `_rerank_and_truncate` wrap whatever exists today in the chat-retrieval router (the spike output names the exact functions). **No new ACL logic.** A future plan that needs file-level ACL (e.g. the org-scope-acl-mirroring plan) extends this function by inserting a per-KB filter step before `_query_kb_collection`; that's the integration seam, deliberately left simple here.

**Default position**: ship 5.1 + 5.2 + the spike output (`agent_search.py` + chat-retrieval refactor to call into it) in the same PR so both code paths exercise the same function from day one — preventing drift before it can start.

#### 5.3 Local verification harness (pre-cutover dress rehearsal)

**File** (new): `/Users/lexlubbers/Code/soev/genai-utils/docker-compose.agent-search.yaml`

A docker-compose stack that lets a developer verify the Phase 5 agent-search path end-to-end without touching a cluster. **This is part of the Combined Staging Cutover release gate** alongside 3.5.local — both must be green from a clean checkout before any cluster deploy.

```yaml
services:
  postgres:                # open-webui's per-tenant Postgres (single instance for the harness)
    image: postgres:16
    environment: { POSTGRES_DB: openwebui, POSTGRES_PASSWORD: dev }

  weaviate:                # tenant Weaviate
    image: semitechnologies/weaviate:1.27
    environment:
      AUTHENTICATION_ANONYMOUS_ACCESS_ENABLED: "true"

  litellm-stub:            # mock /v1/embeddings (deterministic vectors for fixtures)
    image: built-from-./tests/fixtures/litellm-stub

  open-webui:              # real open-webui pinned to the feature branch
    build: ../open-webui
    depends_on: [postgres, weaviate, litellm-stub]
    environment:
      AGENT_SEARCH_ENABLED: "true"
      AGENT_KEYS_DIR: /var/agent-keys
      RAG_OPENAI_API_BASE_URL: http://litellm-stub:4000/v1
    volumes:
      - ./tests/fixtures/agent-keys:/var/agent-keys:ro
    ports: ["8080:8080"]
```

**Smoke test** (`tests/integration/test_agent_search_e2e.sh`):
1. Start the stack
2. Bootstrap: create two open-webui users (alice, bob); create two KBs (`kb-shared` granted to both, `kb-alice` granted only to alice). Push a few chunks into each KB via the existing `/api/v1/integrations/ingest` path with deterministic embeddings.
3. `curl -X POST -H "Authorization: Bearer test-agent-key" -H "X-Acting-User-Id: alice-uuid" -d '{"query":"...","top_k":10}' localhost:8080/api/v1/internal/retrieval/query`
   Expect: chunks from both `kb-shared` and `kb-alice`.
4. Same call with `X-Acting-User-Id: bob-uuid`. Expect: chunks only from `kb-shared`.
5. Same call with an unknown bearer. Expect: 401.
6. Same call with no `X-Acting-User-Id`. Expect: 400.
7. Same call with `X-Acting-User-Id` pointing at a deleted user. Expect: 404.
8. Hot-reload check: write a new file into `tests/fixtures/agent-keys/`, wait 30s, present it as a bearer, expect 200.

**This harness is a release gate**: Phase 5 cannot ship to staging until the smoke test passes from a clean checkout on a developer laptop. The Combined Staging Cutover is then a re-run of the same test against the deployed stack.

#### 5.4 Helm + secrets

**Files** (new under the existing per-tenant chart — the agent endpoint lives on the existing open-webui pod, no new Deployment):

- Extend `/Users/lexlubbers/Code/soev/open-webui/helm/open-webui-tenant/templates/external-secrets.yaml` with an `agent-keys` ExternalSecret that pulls the cluster-wide `agent-keys` 1P item (one secretKey per allow-listed field, `dataFrom: { extract: ... }` syntax).
- Extend `.../templates/deployment.yaml` to mount the resulting `agent-keys` Secret as a directory at `/var/agent-keys` (`subPath`-less mount so all keys appear as files).
- Extend `.../templates/configmap.yaml` to add `AGENT_SEARCH_ENABLED` (`{{ .Values.agentSearch.enabled | quote }}`).
- Extend `.../values.yaml` with:

```yaml
agentSearch:
  enabled: false                 # default off; per-tenant patch flips on
  expectedAgents: []             # asserted by helm-template test
```

- Extend `/Users/lexlubbers/Code/soev/soev-gitops/scripts/populate-1password.sh` to bootstrap (idempotent) the cluster-wide `agent-keys` item with one random 256-bit field per name in a configurable list.

**No NetworkPolicy changes** — the new endpoint lives on the existing open-webui pod, served on the existing port, behind the existing ingress. Agent traffic enters via the same path as user traffic.

**Default position**: agents run inside the cluster initially (calling `http://<tenant>-open-webui.<ns>.svc:8080`). Outside-the-cluster access via the existing tenant Ingress is a one-line change once a customer requires it; documented as a follow-up.

### Success Criteria

#### Automated Verification (5.1, 5.2, 5.3 — all local):
- [x] `cd open-webui && pytest backend/tests/utils/test_service_auth.py` — covers: valid bearer + valid user → `AgentPrincipal`; invalid bearer → 401; missing bearer → 401; missing `X-Acting-User-Id` → 400; unknown user → 404; rev-4: `_agent_key_matches` env-var validation + constant-time compare *(implemented at `backend/open_webui/test/util/test_service_auth.py`; 19 tests passing)*
- [x] `cd open-webui && pytest backend/tests/routers/test_internal_retrieval.py` — covers: agent search returns chunks for accessible KBs; restricts to `kb_ids` subset when provided; 404 when `AGENT_SEARCH_ENABLED=false`; **rev-3:** `/accessible-kbs` returns sanitised collection names + meta-collection; 404 when disabled; supports `?kb_ids=` subset filter *(implemented at `backend/open_webui/test/util/test_internal_retrieval.py`; 8 tests passing)*
- [x] `cd open-webui && pytest backend/tests/services/retrieval/test_agent_search.py` — regression test parameterizes (user, query) pairs and verifies `run_agent_search` returns the same chunks as the existing chat-retrieval pipeline for the same user; **rev-3:** `resolve_accessible_kbs` returns the same KB set, sanitised collection names match the active vector-DB client, suspended KBs dropped, optional `kb_ids` subset honoured *(implemented at `backend/open_webui/test/util/test_agent_search.py`; 10 tests passing — adds `resolve_accessible_kbs` coverage)*
- [ ] Open-webui chat retrieval regression suite passes — the 5.2 extraction changes nothing visible to chat users *(no chat-retrieval refactor was needed — `run_agent_search` reuses `query_collection` directly and does not modify the chat path)*
- [x] `helm template` of `open-webui-tenant` with `agentSearch.enabled=true` and `expectedAgents: [a, b]` renders without errors and includes the `agent-keys` Secret mount + env var
- [x] `helm template` assertion: rendered `expectedAgents` count matches the agent-keys Secret field count *(verified manually; 3 expected agents → 3 remoteRefs)*
- [ ] `populate-1password.sh` against a throwaway test vault is idempotent on re-run for the new `agent-keys` item *(script extended; manual verification pending — requires 1P CLI access)*

**Manual verification** and **production rollout** for Phase 5 are folded into the Combined Staging Cutover and Phase 4 production rollout (see those sections, amended by 2026-04-27 rev 2). Phase 5 has no separate cutover — it ships in lockstep with the loader-worker.

**Implementation Note**: Phase 5 is fully decoupled from any future ACL extensions. It exposes today's KB-level ACL behavior unchanged, just gated by a different bearer. The org-scope-acl-mirroring follow-up plan can ship later without touching this endpoint's contract — it adds a hook inside `run_agent_search` and new Postgres state, neither of which is visible to the agent.

---


## Combined Staging Cutover (introduced by Amendment 2026-04-27, revised by rev 2)

### Overview

Replaces the original 3.6 (loader-worker staging cutover) and 5.6 (authz staging cutover) with one event. **The first cluster deploy of the entire plan**, gating Phase 4 production rollout. Per amendment rev 2, no `gradient-authz` service ships — the agent endpoint lives on the existing per-tenant open-webui pod.

**Pre-condition**: both local-harness release gates green from the same checkout:
- `cd genai-utils && bash tests/integration/test_loader_worker_e2e.sh` — Phase 3.5.local
- `cd genai-utils && bash tests/integration/test_agent_search_e2e.sh` — Phase 5.3 local harness

If either is red, do not deploy. Iterate locally until both pass.

### What ships

Single gitops PR to `soev-gitops/tenants/previder-prod-staging/staging/helmrelease.yaml` bundling:

1. **Loader-worker per-tenant chart** — same content as the original 3.6:
   - `openWebui.config.useSharedLoader: "true"`
   - `loaderWorkerUrl: "http://gradient-loader-worker.staging.svc:8002"`
   - `tenantName: "staging"`
   - `loaderWorker.enabled: true`, `loaderWorker.image.tag: <feature-branch-tag>`, `autoscaling.minReplicas: 1`, `maxReplicas: 3`

2. **Open-webui agent retrieval endpoint** — Phase 5.1, 5.2, 5.4:
   - `agentSearch.enabled: true`, `agentSearch.expectedAgents: [<list of agent names>]`
   - `agent-keys` ExternalSecret on the open-webui pod (sources from the cluster-wide `agent-keys` 1P item, one field per allow-listed agent)
   - `AGENT_KEYS_DIR=/var/agent-keys` env var on the open-webui pod
   - **No** Weaviate schema migration. **No** shared-services authz Deployment.

3. **Feature flags** — staging only, both on:
   - `USE_SHARED_LOADER: "true"`
   - `AGENT_SEARCH_ENABLED: "true"`

   (Chunk-level ACL is out of scope here — see the standalone org-scope-acl-mirroring plan if/when that ships.)

### Provisioning sequence

1. `populate-1password.sh staging` — adds `loaderIngestApiKey` and `loaderWorkerDbPassword` to the staging tenant 1P vault. Idempotent.
2. Add the `agent-keys` item to the **cluster-wide** 1P vault with one field per allow-listed agent (e.g. `langgraph_dev`, `n8n_test`). The same item is consumed by every tenant's ExternalSecret.
3. Force ESO sync on the staging tenant ESO. Verify the `<tenant>-secrets` and `agent-keys` Secrets land in the staging namespace.
4. Push the gitops PR. Flux reconciles in this order (dependency-driven, not authored):
   - Loader-worker Postgres-bootstrap Job (creates `loader_worker` DB on staging tenant Postgres)
   - Loader-worker Deployment + Service + HPA + NetworkPolicy come up
   - Open-webui pod restarts with `USE_SHARED_LOADER=true`, `AGENT_SEARCH_ENABLED=true`, and the `agent-keys` Secret mounted at `/var/agent-keys`
5. Wait for all pods Ready before running the smoke test.

### End-to-end smoke test

Two halves, both required:

**Loader-worker half** (re-runs 3.5.local steps 4–9 against the deployed stack):
- Trigger a OneDrive sync of a small folder (5–10 files) from staging UI
- Verify in loader-worker logs: job received, doc-processor called, callback to `/ingest` returns 200
- Verify in open-webui logs: `/ingest` accepted the loader bearer, used acting `user_id`
- Verify in staging KB UI: files appear with the correct owner
- Pod-log grep: no OAuth tokens, no `LOADER_INGEST_API_KEY` values appearing in logs

**Agent retrieval half** (re-runs 5.3 against the deployed stack):
- `curl -H "Authorization: Bearer <agent-key>" -H "X-Acting-User-Id: <staging-user>" .../api/v1/internal/retrieval/query -d '{"query":"..."}'` returns chunks identical to what the staging chat-retrieval pipeline returns for the same user
- Permission revocation: remove the user from a KB grant in open-webui; the next agent query immediately reflects the change (no caching layer to lag)
- Hot-reload: edit the `agent-keys` 1P item to add a new field; force ESO sync; within ~30s, the new bearer is accepted (mtime poll picks it up)
- Negative tests: invalid bearer → 401; missing `X-Acting-User-Id` → 400; unknown user → 404
- Confirm test agent works **without using a per-user open-webui API key**

### Soak window

Same exit criteria as the original 4.1, plus agent-search-specific:
- 0 sync failures attributable to loader-worker (token expiry retries don't count)
- p95 per-file latency within 1.5x of the Phase 0 baseline
- All File records on staging KB owned by the human who triggered the sync
- 0 `invalid_agent_bearer_attempts` alerts (rate >0/5m would indicate a code bug or probe)
- A test agent reliably retrieves from staging KB via `/api/v1/internal/retrieval/query` for ≥1 week
- Agent retrieval p95 latency within ~10% of the existing chat-retrieval p95 for equivalent queries (the path is the same code; meaningful divergence indicates a routing or auth-layer regression)

Iterate locally on any failure, bump image tags, redeploy. Do **not** touch any production tenant until staging is clean against both halves.

### Rollback

If either half regresses:
- **Loader-worker only fails**: flip `useSharedLoader: "false"` on staging. The agent endpoint keeps working (it has no dependency on loader-worker).
- **Agent endpoint only fails**: flip `agentSearch.enabled: false` (or `AGENT_SEARCH_ENABLED: "false"`). The route is unmounted; chat retrieval is unaffected because it shares the same `run_agent_search` function but is not gated by the flag. Agent integrations revert to "broken" — same state as today, no worse.
- **Both fail**: flip both flags off. Open-webui pod restarts on legacy in-pod sync path. Cluster state otherwise untouched.

The two flags are deliberately independent so failures are diagnosed in isolation.

---


## Testing Strategy

### Unit Tests (genai-utils)

- `loader_worker/sources/onedrive.py`: mocked Graph HTTP, verify download success, 401 → TokenExpiredError, 429 backoff, large-file streaming (no full-buffer read)
- `loader_worker/sources/google_drive.py`: same plus Workspace export path (mock the `/export` endpoint)
- `loader_worker/doc_processor_client.py`: verify retry on 5xx, error envelope on 4xx
- `loader_worker/ingest_client.py`: verify payload shape matches the existing `chunked_embedded` schema, masked auth header in logs
- `loader_worker/job_runner.py`: state transitions, partial failure handling, idempotent retry-after-restart (orphan job recovery)
- `doc_processor/embedder.py`: batching, retry, dimension validation
- `doc_processor/main.py`: `/process-document` with and without `embedding_config` produces correct response shape

### Unit Tests (open-webui)

- `services/sync/pipeline_client.py`: httpx mocked, header forwarding, JSON shape, error mapping; payload contains `source_credential` and `credential_type` (not `ephemeral_token`)
- `services/sync/base_worker.py`: with `_use_shared_loader=True`, verify `_submit_pipeline_job` is called once per source and the legacy methods are not called
- `services/sync/base_worker.py`: with `_use_shared_loader=False`, verify legacy path runs unchanged
- `utils/service_auth.py`: `LoaderPrincipal` — bearer matches → principal, mismatch → falls through; `AgentPrincipal` — `_AgentKeyAllowList.lookup` reload-on-mtime, valid bearer → principal, invalid → 401, missing user → 404

### Unit Tests (Phase 5 — agent retrieval endpoint)

- `open-webui/backend/tests/services/retrieval/test_agent_search.py`: regression test parameterizes (user, query) pairs and verifies `run_agent_search` returns the same chunk set the existing chat-retrieval pipeline returns for the same user (bit-for-bit on chunk IDs and rerank order). Covers: public KBs, group-granted KBs, suspended KBs, deleted users.
- `open-webui/backend/tests/routers/test_internal_retrieval.py`: valid bearer + valid user → results; invalid bearer → 401; missing bearer → 401; missing `X-Acting-User-Id` → 400; unknown user → 404; `AGENT_SEARCH_ENABLED=false` → 404; explicit `kb_ids` filter → results restricted to that subset
- `open-webui/backend/tests/utils/test_agent_keys_reload.py`: write a key file, look up, succeed; rewrite the file with new content, advance mtime, look up old → fail, look up new → succeed

### Tenant Isolation Tests (mandatory — gate Phase 5)

These tests enforce the cross-cutting Tenant Isolation invariants for the **shared** service (doc-processor). Loader-worker and the agent retrieval endpoint are per-tenant by deployment and exempt from in-process tenant-isolation gating.

- `genai-utils/api/gateway/doc_processor/tests/test_no_persisted_tenant_state.py`:
  - Static check: AST-walks `doc_processor/` asserting no module-level mutable variables hold tenant data, no per-tenant credential lookups, no `request.state.tenant`-keyed caches. Whitelist of allowed globals (constants, settings) is explicit.
- `genai-utils/api/gateway/doc_processor/tests/test_log_tagging.py`:
  - CI lint that any `log.{info,warn,error,debug}` call inside a request-scoped path in doc-processor includes `tenant=` (or uses a `LoggerAdapter` that injects it). Also checks Prometheus metrics have a `tenant` label.
  - **Not applied to loader-worker or open-webui** — both are per-tenant by deployment and `tenant=<slug>` is a deployment-time fact, not a per-call invariant.

### Integration Tests

- `genai-utils` `docker compose` harness with mock cloud + mock tenant ingest + Postgres; submit a job end-to-end; verify chunks land in the mock ingest endpoint with embeddings attached
- Open-webui local dev with `LOADER_WORKER_URL` pointing at a stubbed FastAPI server: full sync flow
- **Phase 5** integration: real local open-webui with the `agent-keys` Secret directory mounted; submit `/api/v1/internal/retrieval/query` as agent; verify chunks match chat-retrieval results for the same user; revoke a permission and verify the next call reflects it (no caching layer to lag)

### Manual Testing Steps

**Phases 0–4** (loader-worker):

1. Provision a fresh tenant on staging
2. Configure OneDrive and Google Drive integrations
3. Sync a folder with mixed file types (PDF, DOCX, XLSX, Google Doc, image)
4. Verify the KB UI shows progress, then files
5. Open the chat with the KB attached, ask a query that should hit synced content, verify retrieval
6. Delete a cloud file, run sync again, verify file disappears from KB
7. Revoke OAuth at Microsoft/Google, verify graceful failure + `needs_reauth` flag
8. Cancel an in-progress sync, verify loader-worker job is cancelled and partial results are not pushed
9. Submit 50 files at once, verify HPA scales loader-worker, chunks all land

**Phase 5** (agent retrieval endpoint):

10. Provision a test agent key in the cluster-wide `agent-keys` 1P item; force ESO sync; verify the file appears in `/var/agent-keys` on the staging open-webui pod
11. Test agent calls `POST /api/v1/internal/retrieval/query` for a known user; receives chunks matching what the user sees in the chat KB picker
12. Remove the user from a KB grant in open-webui; the next agent call immediately reflects the change (no cache to wait for)
13. Test agent runs the same query without using a per-user open-webui API key; old per-user-key path can be deprecated for that integration

## Performance Considerations

**Phases 0–4** (loader-worker):

- **Network egress from loader-worker to LiteLLM**: this is new traffic from the tenant namespace. Validate that LiteLLM's existing rate-limiting doesn't choke on a 50-file embedding burst from a single tenant's loader-worker. The LiteLLM team-virtual-key budget per tenant is the natural rate limiter — same key, same budget as today's open-webui-driven embedding calls.
- **Memory pressure on loader-worker**: a single 100 MB OneDrive download briefly buffers in RAM. With `MAX_CONCURRENT_JOBS_PER_REPLICA=4` × `PER_JOB_ITEM_CONCURRENCY=5` = 20 simultaneous downloads, peak ~2 GB. Memory limit set to 4 GB gives 2x headroom.
- **Postgres connection pool on the tenant pod**: with up to 8 loader-worker replicas per tenant and 5 connections per replica, that's 40 connections from loader-worker plus open-webui's existing pool. Verify each tenant's Postgres `max_connections` is sized accordingly (default 100 is borderline for the largest tenants — bump to 200 if needed).
- **Open-webui pod resource savings**: legacy in-pod sync of a 1000-file folder typically pegged a tenant pod at ~3 GB memory peak (Vink data). New path keeps that pod near-idle during sync. Per-tenant resource requests can stay as-is for safety, but limit headroom is now ample for chat traffic during sync.
- **Latency profile**: per-file latency increases by one extra HTTP hop (open-webui → loader-worker), but parallelism increases (loader-worker scales horizontally on memory; tenant open-webui pod did not). Both pods are in the same namespace, so the hop is sub-millisecond. Net: total sync time for large folders should improve.

**Phase 5** (agent retrieval endpoint):

- **Open-webui load**: every agent query lands directly on open-webui (no cache layer). At assumed 1 query/sec/user × 100 concurrent agent users/tenant, that's ~100 QPS into the existing chat-retrieval path — which is mostly Weaviate-bound (vector search), not pod-CPU-bound. The pod is also off the embedding hot path during sync (per amendment 2026-04-26 the loader-worker handles downloads), so the limit headroom is ample.
- **Endpoint latency**: dominated by the existing per-KB Weaviate call latency + reranker. Adding the agent endpoint adds ~1ms (auth helper + bearer lookup against the in-process allow-list dict). Expect agent retrieval p95 within ~10% of chat-retrieval p95 for equivalent queries.
- **Permission revocation propagation**: instant — there is no caching layer in front of `AccessGrants.get_accessible_resource_ids()`. The next agent query reflects the new state.
- **Allow-list reload**: 30s mtime poll on `/var/agent-keys`. The dict swap is atomic; no lock contention. Negligible overhead vs. an inotify watch.
- **No cache-layer follow-up unless measured**: an in-process LRU on `AccessGrants` resolution is a one-day later add if a particular customer's load profile demands it. Don't preemptively add caching for a load profile we haven't measured.

## Migration Notes

**Phases 0–4** (loader-worker):

- **Existing synced files**: the new path produces identical Weaviate chunks (same chunker, same embedding model). No re-embed required during cutover.
- **Cursor / delta state**: stays in open-webui. Cutover doesn't reset deltas.
- **Permission grants**: stay in open-webui's `access_grants` table. Loader-worker doesn't touch ACLs.
- **Quarantine path for failed migrations**: if a tenant fails on the new path, the rollback (Phase 4 step) drops them back to the legacy path on next sync cycle. Files synced on the new path are not re-synced.

**Phase 5** (agent retrieval endpoint):

- **Existing chunks and KBs**: untouched. The endpoint reuses the existing chat-retrieval pipeline; the 5.2 extraction is bit-for-bit identical to today's behavior for chat users.
- **Open-webui internal endpoint**: net-new surface on the per-tenant pod. No upgrade path needed; appears when the per-tenant chart upgrade lands with `agentSearch.enabled: true`.
- **Weaviate schema**: **unchanged.** No migration. No pre-upgrade Helm hook. No `acl_principals` field added to any chunk class.
- **`agent-keys` Secret**: net-new cluster-wide ESO ExternalSecret + per-tenant Secret mount. Created with the Phase 5 helm release.

## Open Questions Resolved

All questions raised during planning are resolved:

| Question | Resolution |
|---|---|
| Where does embedding live? | Per-tenant loader-worker pod (in tenant namespace). Doc-processor stays parse+chunk only and holds no tenant credentials. |
| Loader-worker deployment shape? | Per-tenant in the tenant namespace, 1–8 replicas via HPA. Sidecar to the existing open-webui pod. Tenant isolation is namespace-level, not in-process. |
| Loader-worker job state? | New `loader_worker` DB on each tenant's existing Postgres pod (which already hosts open-webui + agents-state). No new shared-services Postgres, no iSCSI PV, no new backup chart. |
| First production tenant? | gradient |
| Tenant auth on doc-processor in this round? | No. Doc-processor remains NetworkPolicy-gated by the existing `uses-shared-services: "true"` namespace label (tenant namespaces already have it). It needs no per-tenant identity because it holds no per-tenant credentials. |
| Per-tenant embedding model + LiteLLM key? | Loader-worker reads `LITELLM_API_KEY` from env, mounted from the tenant's existing `<tenant>-secrets` (field `openaiApiKey`). No new ExternalSecret pattern, no projected volume. |
| How does loader-worker authenticate to `/ingest`? | Machine bearer (`LOADER_INGEST_API_KEY`) authenticates "this is the loader"; `acting_user_id` and `provider_slug` come from `X-Acting-User-Id` / `X-Acting-Provider` headers, echoed from the original job submission. No DB service-account user. |
| Is `acting_user_id` PII? | No — `User.id` is an opaque UUID (`models/users.py:52`). Email is a separate column. Safe to ship in headers. |
| Where does the LiteLLM key for embedding live? | Existing `<tenant>-secrets` field `openaiApiKey`, mounted as `LITELLM_API_KEY` env var on the per-tenant loader-worker pod. **Never** in the loader-worker job payload; never in `jobs.embedding_config` JSONB. The key only lives in the namespace where it was already required. |
| How does open-webui authenticate to loader-worker? | It doesn't. Same namespace, gated by NetworkPolicy on pod-selector. No bearer between tenant pod and loader-worker. |
| How does loader-worker authenticate to doc-processor? | It doesn't. Cross-namespace, gated by doc-processor's existing `uses-shared-services: "true"` ingress NetworkPolicy. |
| What happens to in-flight jobs on loader-worker pod restart? | Failed. Source credentials live in memory only — never persisted. Job row goes to `status='failed'`; open-webui re-discovers and re-submits on the next sync cycle with fresh credentials. Schema deliberately has no `source_credential` column. |
| Cancellation semantics? | `POST /jobs/{id}/cancel` → `status='cancelling'` → `asyncio.CancelledError` propagates → `_push_to_ingest` skipped entirely (no partial chunks land) → terminal `status='cancelled'`. |
| Where do we capture the legacy baseline for "1.5x of legacy" claim? | New Phase 0: three runs of a representative folder on staging legacy path; p50/p95/p99 written to `thoughts/shared/research/2026-04-25-loader-worker-baseline.md`. Phase 4.1 references the absolute numbers, not the relative claim. |
| How do external agents query Weaviate while honoring open-webui ACLs? | New Phase 5 (rev 2): a machine-auth retrieval endpoint on the per-tenant open-webui pod (`POST /api/v1/internal/retrieval/query`) that reuses the existing chat-retrieval pipeline. No `gradient-authz` service. No Weaviate schema change. The agent calls open-webui directly with `Bearer AGENT_API_KEY_<name>` + `X-Acting-User-Id`. Replaces today's per-user open-webui API key model. (The earlier rev-1 design that exposed Weaviate to agents directly is superseded by the rev-2 amendment at the top of this plan.) |
| When do we deploy to staging? | Phase 0–5: once at the Combined Staging Cutover after both the loader-worker (Phase 3.5.local) and agent-search (Phase 5.3) local harnesses are green from the same checkout. Each phase's local-only block is gated by `helm template` + automated tests. |
| Which sync models does the platform support? | One in this plan: user-OAuth (today's flow). Org-scope models — `org-scope-open` and `org-scope-ACL-mirrored` — are designed in the standalone [`2026-04-27-org-scope-acl-mirroring.md`](./2026-04-27-org-scope-acl-mirroring.md) plan, gated on a customer commitment. |
| Agent auth pattern: fat JWT with claims, thin token + per-query call, or something else? | Thin token: `AGENT_API_KEY_<name>` bearer is the agent identity, `X-Acting-User-Id` is the user identity. Validated per-call against an in-process allow-list (mtime-poll reload). No JWT, no separate authz call — open-webui is on the call path anyway. |
| Cache TTL on agent ACL? | None. Open-webui resolves ACL per-call from `AccessGrants.get_accessible_resource_ids()`. Permission revocation propagates instantly. If load measurement reveals a hotspot, an in-process LRU is a one-day later add. |
| ACL function shape — clean enough to expose, or needs refactor? | Clean. `AccessGrants.get_accessible_resource_ids(user_id, 'knowledge', kb_ids)` (`models/access_grants.py:561`) is a pure function. The 5.2 extraction wraps the existing chat-retrieval pipeline (`AccessGrants` + per-KB Weaviate iteration + reranker) into a `run_agent_search(user, query, top_k)` function called by both the chat router and the new agent endpoint. |
| Phase ordering? | 0 → 1 → 2 → 3 → 5 (Combined Staging Cutover at the end of the local block per amendment 2026-04-27 rev 2) → 4 (per-tenant prod for loader-worker + agent endpoint together). The org-scope-acl-mirroring follow-up plan ships separately if/when a customer commits. |
| Is every phase fully verifiable locally before any cluster touch? | Yes. Phase 0 is read-only (baseline). Phases 1–2–3–5 are local-only with `helm template` + tests gating, joining at the Combined Staging Cutover release gate (3.5.local + 5.3 harnesses both green from one checkout). **No multi-step staging deploys are part of any phase.** |
| What about tenant isolation? Are shared-services pods holding all tenants' data? | No. Loader-worker is per-tenant in the tenant namespace (preserves the namespace boundary that open-webui already has). The agent retrieval endpoint lives on the existing per-tenant open-webui pod. Only doc-processor (parse+chunk, no tenant credentials) remains shared. Tenant Isolation invariants now apply only to doc-processor. |
| Postgres RLS on the loader-worker job store? | No. The job DB is single-tenant (lives on each tenant's existing Postgres in a `loader_worker` DB). No `tenant` column, no RLS — namespace isolation is the load-bearing protection. |

## References

**Existing research (load-bearing for this plan)**:
- Cross-repo research (this plan implements Plans 1, 2, 3 from): `thoughts/shared/research/2026-04-25-cross-repo-document-ingestion-architecture.md`
- Existing push-ingest design: `thoughts/shared/research/2026-03-18-generic-push-interface-design.md`
- Cloud-sync abstraction (the seam we extend): `thoughts/shared/research/2026-03-24-cloud-sync-abstraction-audit-merge-strategy.md`
- Airweave-vs-custom decision (still load-bearing): `thoughts/shared/research/2026-03-13-airweave-vs-custom-integration-comparison.md`
- Cloud-KB permission model precedent: `thoughts/shared/plans/2026-03-30-cloud-kb-permission-fix.md`
- Sync-worker performance lessons: collab memory entry `01-04-2026 Sync Worker Performance & Unified Upload Concurrency` and `06-04-2026 Vink Deployment — Large-Scale Processing Hardening`
- Upstream-merge policy: `CLAUDE.md` (Upstream Merge Policy section) and `collab/world/preferences.md` (Working Approach)
- Agent-authz sketch (superseded — first by Phase 5 rev 1, then by rev 2; retained only for design archeology): `thoughts/shared/research/2026-04-25-external-agents-weaviate-acl-sketch.md`

**Diagrams (companion to this plan)**:
- Loader-worker auth layer (Phases 0–4): `thoughts/shared/diagrams/2026-04-25-shared-services-auth-layer.{md,png}`
- Authz service query path (Phase 5 rev 1, **superseded**; left for archeology): `thoughts/shared/diagrams/2026-04-25-authz-service-query-path.png` — does not match the rev-2 design; the rev-2 sequence diagram lives inline in this plan (End-State Architecture section).

**Source references**:
- Doc-processor source: `genai-utils/api/gateway/doc_processor/main.py`
- gradient-gateway Helm chart: `genai-utils/deploy/helm/gradient-gateway/`
- LiteLLM team-key pattern (auth model reference): `soev-gitops/scripts/provision-litellm-team.sh`
- ACL resolution function (Phase 5 dependency): `backend/open_webui/models/access_grants.py:561`
- Existing per-query ACL guard: `backend/open_webui/routers/retrieval.py:2547` (`_validate_collection_access`)
- TokenManager (Phase 2 dependency): `backend/open_webui/services/sync/token_refresh.py:18-63`
- 1Password rate-limit incident: `soev-gitops/clusters/previder-prod/CLAUDE.md` (lines 36–46)

**Companion plan**:
- Org-scope (app-credential) syncs with chunk-ACL mirroring: [`thoughts/shared/plans/2026-04-27-org-scope-acl-mirroring.md`](./2026-04-27-org-scope-acl-mirroring.md) — extends the agent retrieval endpoint with file-level ACL filtering. Design-only until a customer commits.

**To-be-written research (referenced by Phase 5, not yet authored — flag if work begins on those phases)**:
- *Phase 5 prereq spike output*: `thoughts/shared/research/2026-MM-DD-acl-resolution-extraction-spike.md` — confirms the chat-retrieval ACL call sites are clean enough for the 5.2 refactor; output of the 1-day spike before Phase 5 begins
- *Phase 0 baseline measurement output*: `thoughts/shared/research/2026-04-25-loader-worker-baseline.md` — created during Phase 0; consumed by Phase 4.1 exit criteria
