---
date: 2026-04-25T17:00:00+02:00
researcher: Claude (Opus 4.7)
git_commit_open_webui: 80d3803440674fc3e48ac617461940d1864ddc64
git_commit_genai_utils: f0355fa7767c9818c4b1b0512dabc9c1ec2a2f96
git_commit_soev_gitops: 53a5970800f3a61a4cb94bc285bf679405760eaa
branch_open_webui: dev
branch_genai_utils: dev
branch_soev_gitops: main
repositories: [open-webui, genai-utils, soev-gitops]
topic: "Cross-repo document ingestion architecture: gaps and proposed shape (open-webui ↔ shared services ↔ genai-utils)"
tags: [research, codebase, ingestion, architecture, sync-abstraction, genai-utils, gitops, autoscaling, multi-tenancy, oauth, confluence, onedrive, google-drive]
status: complete
last_updated: 2026-04-25
last_updated_by: Claude (Opus 4.7)
---

# Research: Cross-Repo Document Ingestion Architecture

**Date**: 2026-04-25T17:00:00+02:00
**Researcher**: Claude (Opus 4.7)
**Repos at**: open-webui@80d3803, genai-utils@f0355fa, soev-gitops@53a5970

## Research Question

Given the current state of open-webui, genai-utils and soev-gitops, identify the biggest gaps relative to the desired ingestion architecture and propose the architecture (including auth, abstractions, and autoscaling) that satisfies these goals:

1. Heavy compute / download falls on the shared-services stack, not open-webui.
2. Admin can connect data sources from the admin panel, choosing between **user-scoped** (OneDrive-style) and **global / org-wide** (e.g. "sync all of Confluence") access.
3. Abstractions are extensible to future sources.
4. Background syncing that does not block the system.
5. Controlled re-parse / re-chunk / re-embed when a new pipeline version ships.
6. Good UI: discovery shows folder tree, instant feedback on what will sync, clear progress.

## Summary

The good news: most of the building blocks already exist, just in the wrong places. Open-webui has a **mature, generic sync abstraction** (`BaseSyncWorker` + `SyncProvider` ABC, used by OneDrive and Google Drive) and an **external-pipeline / push-ingest** pattern with `parsed_text | chunked_text | chunked_embedded | full_documents` data types. Genai-utils has just landed a **distributed RabbitMQ fanout pipeline** (parser → chunker → embedder workers + retry + job-status tracking) and is mid-rollout of a `POST /jobs` HTTP entrypoint. Soev-gitops runs `gradient-doc-processor` in shared-services with CPU-based HPA.

The bad news: those building blocks are not yet wired together end-to-end, and the heavy work today still lives on the wrong side of the wire.

**The five biggest gaps:**

1. **Sync workers run inside open-webui itself.** `OneDriveSyncWorker` and `GoogleDriveSyncWorker` download files, hash them, write them to local storage, and call `process_file()` (parse+chunk+embed) **in the open-webui pod**. That is the load that needs to move to shared services. Per-tenant open-webui pods do "heavy compute / downloading" today.
2. **No job queue in shared services.** Soev-gitops has no Redis/RabbitMQ/Temporal/NATS. Genai-utils' distributed pipeline assumes RabbitMQ exists *somewhere*, but no shared-services HelmRelease deploys it. The "background syncing that does not block" cannot scale without this.
3. **No global / org-wide source mode.** Every existing connector is **per-user OAuth** (token in `oauth_session` table, scoped by `user_id`). Confluence-as-org-knowledge requires a different auth model (admin-configured app credentials, optional service account, no per-user token), and currently no code path supports it.
4. **No re-embed orchestration.** Open-webui's "Reset vector DB" and "Reindex knowledge files" are blunt admin buttons; genai-utils has no `POST /jobs/{id}/re-embed`; soev-gitops has no operator/CRD for cluster-wide re-embed. None of these can do a controlled rolling re-embed when the pipeline is upgraded.
5. **Service-to-service auth is plaintext + NetworkPolicy.** Tenant `open-webui` calls `gradient-reranker.shared-services.svc:8000` with no auth. Once shared services receives genuine document content + per-tenant secrets via a job API, a tenant-scoped credential / mTLS layer is needed.

The proposed architecture (Section "Recommended Architecture") keeps **open-webui as the control plane and UI**, moves **discovery + download + processing into a shared-services ingestion engine** (genai-utils' distributed pipeline + new source-publisher workers), and uses the **existing `SyncProvider` ABC** as the seam — but with the worker's `_download_file_content` and `_process_file_via_api` *delegating* to a shared-services `POST /jobs` endpoint instead of doing the work in-process. That design preserves data-sovereignty RBAC (already validated, see `2026-03-13-airweave-vs-custom-integration-comparison.md`), keeps the upstream-merge surface small, and lets shared services autoscale on queue depth (KEDA) instead of on each tenant's CPU.

---

## Detailed Findings

### A. open-webui — current state

#### A1. Upload flow (today)

User uploads a file in chat/KB → `POST /api/v1/files/?process=true` →
`backend/open_webui/routers/files.py:185-205` → `upload_file_handler()` (`files.py:208-338`) → file written to S3/local via `Storage.upload_file()` → DB record `status='pending'` → `BackgroundTasks.add_task(process_uploaded_file)` → returns 200 immediately.

Background task `process_uploaded_file()` (`files.py:93-182`) → `process_file()` in `routers/retrieval.py:1622-1976` → `Loader.load()` (parsing) → `save_docs_to_vector_db()` (chunk + embed + Weaviate insert) → Socket.IO `file_status` event to room `user:{user_id}`.

**Where it runs**: in-process inside the per-tenant open-webui pod. Heavy CPU (parsing PDFs/DOCX, calling embedding API, Weaviate upsert) lives here.

#### A2. External-pipeline delegation (already exists)

`backend/open_webui/routers/external_retrieval.py:195-316` already supports delegating *chunking* to an external service when `EXTERNAL_PIPELINE_URL` is set:

- POST `{text, filename, filetype, total_pages}` to `<url>/chunk`
- Receive `{success, chunks[], errors[]}`
- Continue with embedding + Weaviate insert locally.

The `Loader` class (`retrieval/loaders/main.py:229-400+`) also supports an `external` engine via `EXTERNAL_DOCUMENT_LOADER_URL` for parsing.

**Implication**: open-webui already has *two* HTTP delegation seams (parser-as-a-service, chunker-as-a-service). What's missing is an **end-to-end** delegation seam (handoff document → get back collection populated).

#### A3. Push-ingest surface (already exists)

`POST /api/v1/integrations/ingest` is the existing **push** API for external publishers (Octobox-style). Discriminated-union design documented in `thoughts/shared/research/2026-03-18-generic-push-interface-design.md`:

| `data_type` | What the publisher sends | Pipeline stages skipped |
|---|---|---|
| `full_documents` | binary file (multipart) | none |
| `parsed_text` | full doc text | parse |
| `chunked_text` | `chunks: list[str]` | parse + chunk |
| `chunked_embedded` | `chunks: list[{text, embedding}]` | parse + chunk + embed |

This is the contract genai-utils should call back into for the "completed work" handoff.

#### A4. Sync abstraction — the big asset

The `services/sync/` directory is the most reusable piece of code in this whole stack. From `thoughts/shared/research/2026-03-24-cloud-sync-abstraction-audit-merge-strategy.md`:

```
services/sync/
├── provider.py        # SyncProvider, TokenManager ABCs + factories
├── base_worker.py     # ~993 LoC of shared orchestration
├── constants.py       # SUPPORTED_EXTENSIONS, CONTENT_TYPES, error types
├── events.py          # Generic Socket.IO emitters
├── scheduler.py       # SyncScheduler (parameterized per provider)
├── token_refresh.py   # Generic refresh + needs_reauth marking
└── router.py          # 13 shared endpoint handlers

services/onedrive/, services/google_drive/   # ~500 LoC of provider-specific code each
```

`BaseSyncWorker` defines 7 abstract properties + 10 abstract methods. The shared `sync()` method handles source verification → file collection → file-limit → semaphore-bounded `asyncio.gather` (default 5) → cancellation → status updates → permission sync → Socket.IO progress.

**Per the cookbook** (`collab/docs/external-integration-cookbook.md`, referenced by world/state.md), adding a new cloud-storage connector is ~10 new files + ~4 modified files, ~1-3 days. The interface generalizes to Dropbox/Box/SharePoint cleanly, to Confluence with naming-only friction, to Salesforce/Topdesk with auth-flexibility friction (current `TokenManager` assumes user OAuth, not API keys / static org credentials).

#### A5. Critical thing the sync workers do today

`BaseSyncWorker._process_file_via_api()` (`base_worker.py:379-451`):
1. Download bytes from cloud (Graph / Drive API).
2. Hash, dedup.
3. Write file to `Storage` (S3 or local PVC).
4. Create `File` + `KnowledgeFile` records.
5. `asyncio.to_thread(process_file)` — full RAG pipeline **in this pod**.
6. Cross-KB vector propagation.

Steps 1, 3, and 5 are exactly what we want to move out of open-webui.

#### A6. Auth + RBAC model (per-user OAuth)

- Tokens encrypted with Fernet, stored in `oauth_session` table keyed by `(user_id, provider)`.
- Refresh handled centrally by `services/sync/token_refresh.py`.
- Permissions mapped from Graph API → OW users on every sync (`onedrive/sync_worker.py:415-511`).
- Enforced at SQL level via `utils/db/access_control.py:9` — hard-isolation per knowledge base via separate vector DB collections (today: per-tenant Weaviate, one collection per KB).
- Decision in `2026-03-13-airweave-vs-custom-integration-comparison.md`: stay custom, do not adopt airweave. Our RBAC is materially deeper than airweave's connector-specific ACL. **This decision is still load-bearing for any architectural choice we make now.**

#### A7. Knowledge-base typing

`models/knowledge.py:47` has a `type` column (`local`, `onedrive`, `google_drive`, custom). External KBs have `meta` JSON storing per-provider sync state (cursor, delta links, sources, suspension status). External KBs are locked to private `access_control={}` on creation; sync workers manage grants.

---

### B. genai-utils — current state

#### B1. Two pipelines, one new

The repo has a **legacy** pipeline (`pipeline/`, CLI-driven, monolithic) and a **new distributed** pipeline (`document_processing/distributed/`) that is the active focus:

```
publisher → RabbitMQ fanout exchange "jobs"
  ├─ ParserWorker      (raw_document → parsed_document)
  ├─ ChunkerWorker     (parsed_document → chunked_document)
  ├─ EmbedderWorker    (chunked_document → embedded_document)
  ├─ RetryWorker       (soft-failure envelopes, asyncio.Lock + generation counter)
  └─ JobStatusWorker   (observes all stages, writes per-doc rows to MongoDB job_results)
```

- `framework/` is application-agnostic (pubsub, storage, workers/runners, processor protocol, common contracts).
- `pipeline/` is application-specific (PdfProcessor, HtmlProcessor, XmlProcessor, MarkdownChunker, OpenAIEmbeddingClient).
- All workers self-filter via `FilteringWorkerBase.should_process()` — fanout means everyone sees everything.

#### B2. Sources / loaders (today)

`framework/storage/documents/location.py` defines a discriminated `DocumentLocation` union: `PathLocation | UrlLocation | CloudLocation`. `CachedDocumentFetcher` resolves these via `resolve_local_path / resolve_url / resolve_gcs`, caching bytes in Redis (24h TTL).

- ✅ Local files, HTTP URLs, GCS object storage.
- ❌ No OneDrive, Google Drive, SharePoint, Confluence connector — **none**.
- ❌ No `SourceConnector` / `SourcePublisher` abstraction yet (only `Processor[T,U]` for transforms).

#### B3. APIs

- **Search API** (`api/main.py`) — FastAPI, static `X-API-Key` header. Routes: `/search/chunks`, `/datasources`, `/datasources/{id}/documents|chunks|chunks/{id}/neighbors`, `/rerank`, `/fetch-url`, `/web-search`. Datasources are YAML-configured per project. Reads from Weaviate (per-tenant in soev-gitops).
- **Management API** (`management/backend/main.py`) — job listing, cluster info, log streaming. `JobService` protocol with `HttpJobService` (proxies to "Pipeline API") and deprecated `RMQJobService`.
- **Pipeline API** — *being added*. `POST /jobs`, `GET /jobs/{id}`. Phase 1 design exists in genai-utils' collab notes; HTTP endpoint and job registration in MongoDB are wired, but **no end-to-end multipart upload flow yet**, and **no integration with open-webui**.

#### B4. What does *not* exist yet

- HTTP `POST /jobs` accepting multipart file uploads from open-webui.
- A `SourcePublisher` worker that lists items from an external source (OneDrive folder, Confluence space, GDrive folder) and emits `raw_document` messages.
- A `re-embed` workflow (`POST /jobs/{id}/re-embed`, dataclass-versioned `chunker_version` / `embedder_model` on jobs, deduplication strategy on Weaviate).
- A Weaviate writer in the distributed pipeline (currently writes to MongoDB `job_results`; downstream indexer is assumed but not implemented in `distributed/`).
- Per-tenant scoping (`job_id`, `tenant_id`, namespacing).
- Authentication on management/pipeline API (only Search API has the `X-API-Key`).

---

### C. soev-gitops — current state

#### C1. Where things live

```
shared-services/  (gradient-gateway HelmRelease)
  ├─ gradient-gateway        Deployment, 1 replica
  ├─ gradient-doc-processor  Deployment, HPA min=2/max=8 @ 70% CPU (previder), min=1/max=2 (intermax)
  ├─ gradient-reranker       Deployment, 1 replica, no HPA
  ├─ gradient-crawl4ai       Deployment, 1 replica, 4-8 CPU / 16-32 Gi RAM
  └─ gradient-searxng        Deployment, 1 replica

litellm/                LiteLLM proxy — team virtual keys, budgets
observability/          LGTM stack (Loki, Grafana, Tempo, Mimir, Alloy)
infrastructure/         Cilium NetworkPolicies, iSCSI PVs, Gateway, ESO ClusterSecretStore
tenants/                8 tenants on previder-prod, 1 on intermax-prod, each their own namespace
                        with Open WebUI (HA, 2 replicas) + Postgres (1 pod) + Weaviate (1 pod) + Redis (ephemeral)
aire-vector/            Separate vector-db-api + Weaviate (POC scope)
```

#### C2. The autoscaling story (today)

- **HPA only**, **CPU @ 70%** target, only on `gradient-doc-processor`.
- No KEDA, no queue-depth metric, no custom metrics.
- All other shared-services components are pinned at 1 replica.

#### C3. Storage

- **previder-prod**: 29 static iSCSI PVs (one per tenant Postgres + tenant Weaviate + observability). `previder-iscsi-pdc1` StorageClass, no-provisioner, ReclaimPolicy=Retain.
- **Object storage**: Previder S3-compatible (`object.previder.nl`) — observability buckets + per-tenant upload bucket (e.g. `previder-prod-octobox-uploads`).
- **Backups**: tenant-backup chart, daily pg_dump + Weaviate snapshot, 30-day retention (NFS PDC2).
- **GPU nodes**: none. All embedding/rerank runs on CPU.

#### C4. Service-to-service auth + networking

- **No mTLS / no service mesh**. Plain HTTP ClusterIP DNS.
- **Cilium NetworkPolicy** (previder) / **CiliumNetworkPolicy** (intermax) gates ingress to shared-services to namespaces labeled `uses-shared-services: "true"`.
- Tenant open-webui calls:
  - `http://litellm-proxy.shared-services.svc:4000/v1` — auth via team virtual key (`Authorization: Bearer …`).
  - `http://gradient-reranker.shared-services.svc:8000/v1/rerank` — **no auth**.
  - `http://gradient-gateway.shared-services.svc:8000` — **no auth**.

#### C5. Secrets

- **External Secrets Operator** + **1Password SDK provider**. Per-tenant vault `soev-<tenant>` with item `tenant-secrets`. Per-shared-service vaults (`soev-shared-services`, `soev-litellm`, `soev-observability`).
- OAuth client_id/secret for OneDrive/Google Drive **per tenant** in their own vault. Refresh tokens **per user** in open-webui's encrypted `oauth_session` table.
- **Known incident**: 1Password rate limit is account-wide; a HelmRelease in rollback loop exhausted shared budget once already (LiteLLM, 2026-04-21).

#### C6. Per-tenant Weaviate, not shared

Each tenant has its own Weaviate StatefulSet. Collections inside a tenant's Weaviate are named by feature (no tenant-prefixing needed — namespace = isolation). If we move to shared Weaviate later, collections need explicit tenant prefixes.

#### C7. Onboarding a new connector for a tenant — today's friction

Per `tenants/<cluster>/<tenant>/helmrelease.yaml` + 1Password vault edit. Concretely, to enable Confluence org-wide for a tenant today:
1. Tenant admin gives us their Confluence OAuth client_id/secret.
2. We add a field `confluenceClientSecret` to `soev-<tenant>/tenant-secrets`.
3. We edit their helmrelease.yaml to set `confluenceClientId`, `confluenceBaseUrl`, `enableConfluenceIntegration`, and the `externalSecrets.onepassword.fields.confluenceClientSecret: true` flag.
4. Commit + push, Flux reconciles in 5–10 min.
5. **And there's no Confluence connector in open-webui yet** — this is the missing 80%.

---

## The Five Biggest Gaps (Ranked)

### Gap 1: Heavy work runs in open-webui pods, not shared services

**Symptom**: `BaseSyncWorker._process_file_via_api()` downloads, hashes, stores, parses, chunks, embeds — all inside `open-webui-<tenant>` pods. Per-tenant pods are sized 2 replicas with default resources. A 1300-doc Vink load (see episodic memory 06-04-2026) required `asyncio.gather` chunking + `DOCUMENT_PROCESSING_TIMEOUT` workarounds.

**Fix shape**: Replace step 5 ("`asyncio.to_thread(process_file)`") with a shared-services HTTP call. Steps 1 (download) and 3 (storage) stay only as long as it takes to forward bytes; the *normal* path is the worker pushes a job request to genai-utils which downloads from the source directly.

**File**: `services/sync/base_worker.py:379-451`.

### Gap 2: No queue infrastructure in soev-gitops

**Symptom**: genai-utils' distributed pipeline assumes RabbitMQ exists. Soev-gitops has only ephemeral per-tenant Redis (session store). No HelmRelease deploys a shared message broker.

**Fix shape**: Add a `shared-services/<cluster>/rabbitmq.yaml` HelmRelease (or NATS JetStream — slightly simpler ops profile, lighter resource footprint, supports JetStream KV for cursor/dedup state). RabbitMQ is the path of least resistance because genai-utils is already coded against `aio_pika`. Deploy the broker behind a NetworkPolicy and emit a per-tenant JWT for tenant-side enqueue auth.

### Gap 3: No "global / org-wide" source mode

**Symptom**: `oauth_session.user_id` is a foreign key. `TokenManager.get_valid_access_token(user_id, knowledge_id)` is a per-user contract. There is no way to say "this Confluence connector is owned by the org and runs as a service account; sync everything; grant read access to all members of group X".

**Fix shape**: Extend `SyncProvider` with two modes:

```python
class SyncScope(Enum):
    USER = "user"       # current behavior — token per (user, knowledge_id)
    ORG = "org"         # service-account / app-only — token per (tenant, provider)

class SyncProvider(ABC):
    @abstractmethod
    def supported_scopes(self) -> set[SyncScope]: ...
```

For org-wide:
- Token storage: new table `tenant_oauth_session` keyed by `(tenant_id, provider)`, encrypted by ESO-injected per-tenant Fernet key.
- Auth model:
  - Confluence: API token + base URL + service account email; or OAuth 2.0 client-credentials flow (Atlassian "JWT app").
  - SharePoint: Azure AD app-only (client_credentials) — distinct from current Graph delegated flow.
  - GDrive (org-wide): Google Workspace domain-wide delegation with service account.
- ACL bridge: `_sync_permissions()` becomes optional. For org-wide KBs, a single `access_control={"read": {"groups": [<group_id>]}}` is set on creation by an admin and **does not change** per sync (avoids the suspension/leak class of bug fixed on 2026-03-30 in `2026-03-30-cloud-kb-permission-fix.md`).

Per the cookbook, the ABC accepts non-OAuth tokens via a `StaticTokenManager` variant. That is the right place to put the API-key auth for Confluence/Topdesk.

### Gap 4: No re-embed orchestration

**Symptom**: When the embedding model changes, today's options are (a) `Reset vector DB` (nukes everything, users lose their KBs), (b) one-by-one `addFileHandler` re-add (manual). Genai-utils has no `re-embed` data type or worker. Soev-gitops has no operator to trigger across tenants.

**Fix shape**: A 4-piece feature.

1. **Job-versioning**: every job stores `pipeline_version = (parser_v, chunker_v, embedder_model)` at creation.
2. **`POST /jobs/{id}/re-embed`** in genai-utils Pipeline API: queries `job_results` for all (job_id, doc_id) pairs, republishes with `data_type="re-embed"` and a *new* `embedder_model`. A `ReEmbedWorker(FilteringWorkerBase)` skips parse+chunk and goes straight to the embedder.
3. **Deduplication on Weaviate**: write into a versioned collection (`{kb_id}_v{n}`), atomic alias swap when complete (Weaviate supports collection aliases). Or: `delete by file_id` then `insert`. The collection-alias path avoids serving partial state; the delete-insert path is simpler.
4. **Cross-tenant control**: a Kubernetes CronJob (or a small operator) in shared-services queries all tenant Weaviates for "jobs whose pipeline_version != deployed pipeline_version" and submits re-embed jobs with rate-limiting. This avoids 8 tenants stampeding the queue at once.

### Gap 5: Service-to-service auth between tenant ↔ shared services

**Symptom**: Tenant pods call `gradient-reranker.shared-services.svc:8000` with no `Authorization` header. With the new ingestion API, shared services will receive **document content + per-user OAuth credentials**. NetworkPolicy alone is too soft.

**Fix shape**: Per-tenant JWT issued by an internal OIDC issuer (we already have Microsoft SSO for users; for service-to-service we want a separate, tenant-scoped issuer). Three simpler intermediate steps:

1. **Shared HMAC secret** per (tenant, shared-service) pair, stored in 1Password, mounted as env var in both ends. Simple, weak rotation story, but a real fence.
2. **Tenant-scoped LiteLLM-style virtual key** for the genai-utils Pipeline API (re-use the existing model: tenant has a key, shared service validates against a registry). LiteLLM already does this.
3. **mTLS via a service mesh** (Cilium has built-in mTLS now in newer versions; intermax-prod is on Cilium). Adds operational complexity but is the right end state.

For Phase 1, recommend (2) — it composes with the existing LiteLLM team-virtual-key pattern and tenant admins already understand it.

---

## Recommended Architecture

### Principle: open-webui is the control plane; shared services is the data plane

Open-webui owns: **user identity, access control, KB metadata, sync configuration, UI, real-time progress**.

Shared services owns: **OAuth/source clients, document downloads, parsing, chunking, embedding, vector writes, job orchestration**.

Genai-utils' distributed pipeline becomes "the ingestion engine"; open-webui's `BaseSyncWorker` becomes a **thin RPC stub** that hands off to it.

### Data flow (proposed)

```
┌──────────── Open-WebUI per tenant ─────────────────┐    ┌─────── Shared Services ────────┐
│                                                    │    │                                 │
│  Admin Panel "Connectors"                          │    │   ┌──────────────────────┐     │
│   ├─ list providers (registry)                     │    │   │ ingestion-api        │     │
│   ├─ user-scope onboarding (OAuth code flow)  ─────┼────┼──►│  POST /tenants/{t}/  │     │
│   └─ org-scope onboarding (service-acct creds)─────┼────┼──►│       sources       │     │
│                                                    │    │   │  POST /tenants/{t}/  │     │
│  KB UI                                             │    │   │       jobs          │     │
│   ├─ folder picker (browses via shared-svc API)────┼────┼──►│  GET  /jobs/{id}    │     │
│   ├─ progress (WS proxied from shared-svc)         │    │   │  POST /jobs/{id}/   │     │
│   └─ file list (synced from shared-svc state)      │    │   │       re-embed      │     │
│                                                    │    │   └────────┬─────────────┘     │
│  Push-completion sink                              │    │            │                   │
│   POST /api/v1/integrations/ingest ◄───────────────┼────┼──── pushes embedded chunks ───  │
│   (existing, "chunked_embedded" or "parsed_text")  │    │            │                   │
│                                                    │    │   ┌────────▼─────────────┐     │
│  Vector DB (per-tenant Weaviate)                   │◄───┼───┤ embedded chunks      │     │
│  RBAC enforcement (SQL + collection isolation)     │    │   │ written into tenant  │     │
│                                                    │    │   │ Weaviate via         │     │
└────────────────────────────────────────────────────┘    │   │ collection alias swap│     │
                                                          │   └──────────────────────┘     │
                                                          │                                 │
                                                          │   RabbitMQ "jobs" exchange      │
                                                          │   ├─ SourcePublisher per source │
                                                          │   │   (OneDrive, GDrive,        │
                                                          │   │    Confluence, S3 …)        │
                                                          │   ├─ ParserWorker (HPA/KEDA)    │
                                                          │   ├─ ChunkerWorker (HPA/KEDA)   │
                                                          │   ├─ EmbedderWorker (HPA/KEDA   │
                                                          │   │     gated on rate-limit)    │
                                                          │   ├─ RetryWorker                │
                                                          │   └─ JobStatusWorker            │
                                                          │                                 │
                                                          │   Mongo (jobs, job_results)     │
                                                          │   Redis (CachedDocumentStore)   │
                                                          └─────────────────────────────────┘
```

### Where each user goal is satisfied

| Goal | Where |
|---|---|
| 1. Heavy work in shared services | All download / parse / chunk / embed lives behind `POST /tenants/{t}/jobs`. Open-webui only forwards intent. |
| 2. Admin connects sources, choice of user vs org | New `SyncScope` enum on `SyncProvider`. Admin panel "Connectors" lets admins pick. User-scope keeps the OAuth code flow (working today). Org-scope uses service-account credentials provisioned via 1Password, surfaced via shared-svc `POST /tenants/{t}/sources`. |
| 3. Extensibility | The cookbook (`collab/docs/external-integration-cookbook.md`) already describes how to add a connector to open-webui. We mirror it: a new connector = a new `SourcePublisher` in genai-utils (~300 LoC) + a thin provider in open-webui (~300 LoC for the UI/auth code flow). |
| 4. Background syncing | RabbitMQ + KEDA-driven worker pool. Open-webui's request returns the moment the job is enqueued. |
| 5. Controlled re-embed | `POST /jobs/{id}/re-embed` + `ReEmbedWorker` + collection-alias swap. Triggered by a shared-services CronJob/operator that compares `pipeline_version` to deployed version. |
| 6. UI: folder tree, instant feedback, progress | Folder browse + discovery is a synchronous shared-services call (`GET /tenants/{t}/sources/{provider}/list?path=…`) that uses the source-side OAuth/credentials but returns *only* metadata (no downloads). Progress comes from `JobStatusWorker` writing to MongoDB; shared-svc proxies that as a Socket.IO stream open-webui forwards to the user. |

### Auth & access-rights model (in detail)

**User-scope (OneDrive today, GDrive today, future personal connectors):**
- OAuth code flow stays in open-webui (PKCE, callback HTML, state validation). The `oauth_session` table stays.
- The shared-services Pipeline API receives an *ephemeral access token* on each `POST /tenants/{t}/jobs` request (not a refresh token). Open-webui mints a short-lived (5-min) token via `TokenManager.get_valid_access_token()` and forwards it.
- Genai-utils' `SourcePublisher` uses the token to enumerate + download. If the token expires mid-job, it raises `SoftFailureException`; `RetryWorker` re-enqueues; on the next attempt open-webui has a fresh token.
- ACL: `_sync_permissions()` runs **after** ingestion completes, in open-webui (it has the SQL access_control model). Genai-utils does not need to know about ACLs — it just returns chunks and metadata.

**Org-scope (Confluence org-wide, SharePoint app-only, S3 bucket):**
- Credentials are provisioned in the tenant 1Password vault. Sync from ESO into a Kubernetes Secret in the tenant's namespace **and** mirrored into a shared-services namespace via External Secrets templating (or a per-tenant Kubernetes Secret in shared-services with a tenant label, populated by the same ESO ClusterSecretStore).
- The shared-services `SourcePublisher` reads credentials from its tenant-labeled Secret; no token round-trip with open-webui.
- ACL: at KB-creation time, admin picks "share with everyone" or "share with group X". This becomes the `access_control` on the KB and is **never updated by sync workers**. This is critical — see the 2026-03-30 cloud-KB permission-leak fix; the suspension lifecycle pattern is the load-bearing precedent here.

**Tenant ↔ shared-services auth:**
- Phase 1: tenant-scoped API key in `Authorization: Bearer <key>`, validated against a registry maintained in shared-services Postgres. One key per (tenant, "ingestion-api"). Keys provisioned by the same 1Password + ESO chain LiteLLM uses for its team virtual keys.
- Phase 2 (later): mTLS via Cilium (zero app-side change).

### Modularity boundaries (so things stay loose)

Two ABCs become the contract:

```python
# open-webui side
class SyncProvider(ABC):                       # exists today
    def supported_scopes(self) -> set[SyncScope]: ...
    def get_provider_type(self) -> str: ...
    def get_token_manager(self) -> TokenManager: ...   # for USER scope only
    def list_sources(self, scope, ...) -> list[Source]: ...   # NEW — folder browse
    def submit_job(self, scope, sources, ...) -> JobId: ...   # NEW — calls shared-svc
    # all worker logic disappears — was BaseSyncWorker, now lives shared-svc-side

# genai-utils side
class SourcePublisher(PublishingWorkerBase, ABC):     # NEW
    @abstractmethod
    def list(self, credentials, path) -> list[Item]: ...
    @abstractmethod
    def fetch(self, credentials, item) -> bytes: ...
    @abstractmethod
    def detect_changes(self, credentials, cursor) -> tuple[list[Item], list[Deleted], NewCursor]: ...
```

Adding **OneDrive support to the new architecture** = (a) keep open-webui's existing OneDrive UI/OAuth code, (b) write `OneDriveSourcePublisher` in genai-utils (a port of the relevant chunks of `services/onedrive/graph_client.py` and `_collect_folder_files` from `base_worker.py`), (c) wire it into the factory in `genai-utils/document_processing/distributed/pipeline/sources/__init__.py`. Estimated ~600 LoC genai-utils-side, ~150 LoC open-webui-side (mostly stripping the worker we no longer need).

Adding **Confluence org-wide** = (a) open-webui admin-panel "Connectors" entry with the org-scope onboarding form, (b) `ConfluenceSourcePublisher` in genai-utils with API-token auth, (c) a `StaticTokenManager` open-webui-side that just unwraps the 1Password reference. Estimated ~400 LoC each side.

### Autoscaling — when, on what

| Component | Scaler | Metric | Range | Why |
|---|---|---|---|---|
| `gradient-doc-processor` (legacy, still used by external_retrieval) | HPA | CPU 70% (already) | 2–8 | Keep until distributed pipeline carries 100% of load |
| `parser-worker` | KEDA | RabbitMQ queue length on `parser` filter | 1–20 | Bursty: a Confluence sync can dump 5k pages at once |
| `chunker-worker` | KEDA | RabbitMQ queue length on `chunker` filter | 1–10 | CPU-bound but cheaper than parsing |
| `embedder-worker` | KEDA | queue length **+** rate-limit token bucket | 1–4 | Needs a global throttle — embedding API is the budget pinch point |
| `source-publisher` (per-source) | KEDA cron | scheduled (15min) **+** queue depth on `publication-request` | 0–4 | Scale-to-zero between syncs; spike on demand |
| `retry-worker` | static | n/a | 1 | Single instance is fine; retries are rare and cheap |
| `job-status-worker` | static | n/a | 1–2 | Observers on fanout; minimal overhead |
| `ingestion-api` (FastAPI) | HPA | CPU 60% **+** RPS via OTel | 2–6 | Auth + small queue puts |
| `rabbitmq` | static | n/a | 1 (HA pair if budget allows) | Needs PV |

**KEDA prerequisite**: install KEDA into the cluster (small footprint). RabbitMQ scaler is built-in. Mimir-as-metrics-source for custom triggers is also built-in.

**Rate-limit token bucket for embedder**: a tiny per-tenant Redis counter keyed by `embed:{tenant}:{minute}`, capped at e.g. 1k tokens/minute. Shared services already operates a Redis (per-tenant), but a shared one would be cleaner here.

---

## Phasing (suggested order, smallest-blast-radius first)

1. **Deploy RabbitMQ + KEDA + ingestion-api skeleton in shared-services** (1–2 weeks). No tenant integration yet. Run the existing distributed pipeline E2E tests against it (already verified locally + GKE per genai-utils' e2e_test).
2. **Wire `external_retrieval.py` to call the new ingestion-api for chunking** (1 week). Replaces today's separate chunker URL. Reuses existing config var. Tenant-side change is one URL.
3. **Build `OneDriveSourcePublisher` in genai-utils + behind-flag flip in open-webui** (2 weeks). Open-webui keeps OAuth + UI + RBAC; the worker becomes a stub. Shadow-mode the new path on staging before flipping any real tenant. **Keep the old `BaseSyncWorker` code under a feature flag** for instant rollback.
4. **Add re-embed primitives**: `pipeline_version` on jobs, `ReEmbedWorker`, `POST /jobs/{id}/re-embed`, alias-swap on Weaviate (2 weeks).
5. **Add org-scope mode + `ConfluenceSourcePublisher`** as the first non-Microsoft, non-personal connector (3 weeks). This validates the `SyncScope.ORG` path.
6. **Cross-tenant re-embed CronJob/operator** + per-tenant rate-limiting (1 week).

Total ~10–12 weeks of focused work. Each phase is independently shippable and the system remains in a sane state at every cut-line.

---

## Code References

### open-webui

- `backend/open_webui/routers/files.py:185-338` — upload flow, where the BackgroundTask is dispatched
- `backend/open_webui/routers/retrieval.py:1622-1976` — `process_file()` (parse → chunk → embed → vector insert), the function we want to *not* run in tenant pods
- `backend/open_webui/routers/external_retrieval.py:195-316` — existing HTTP delegation to chunker; the precedent for a fuller delegation
- `backend/open_webui/routers/integrations.py` — push-ingest endpoint that genai-utils will call back into
- `backend/open_webui/services/sync/provider.py` — `SyncProvider` ABC, factory functions (extension point)
- `backend/open_webui/services/sync/base_worker.py:379-451` — `_process_file_via_api`, the heavy in-pod work that moves to shared services
- `backend/open_webui/services/sync/base_worker.py:693-979` — `sync()` orchestration (semaphore, cancellation, progress)
- `backend/open_webui/services/sync/router.py` — 13 shared endpoint handlers (all stay)
- `backend/open_webui/services/onedrive/sync_worker.py`, `services/google_drive/sync_worker.py` — both shrink to thin RPC stubs
- `backend/open_webui/models/oauth_sessions.py:25-41` — encrypted per-user token table
- `backend/open_webui/models/knowledge.py:47` — `type` column for KB typing
- `backend/open_webui/utils/db/access_control.py:9` — SQL-level permission filter (must remain authoritative)
- `backend/open_webui/config.py:2841-2882, 3129-3133` — OneDrive config block, `FILE_PROCESSING_MAX_CONCURRENT`
- `src/lib/components/admin/Settings/IntegrationProviders.svelte` — runtime provider registry (needs extension to capture scope)
- `collab/docs/external-integration-cookbook.md` — the cookbook that this proposal extends (rather than replaces)

### genai-utils

- `document_processing/distributed/framework/workers/runners.py` — worker lifecycle
- `document_processing/distributed/framework/workers/retry_worker.py` — soft-failure retry pattern (asyncio.Lock + generation counter)
- `document_processing/distributed/framework/storage/documents/location.py` — `DocumentLocation` discriminated union (extension point for new sources)
- `document_processing/distributed/framework/storage/documents/fetcher.py` — `CachedDocumentFetcher` (Redis 24h TTL)
- `document_processing/distributed/pipeline/workers/parser_worker.py:75-98` — processor dispatch by `supported_types`
- `document_processing/distributed/pipeline/workers/embedder_worker.py` — embedder; will need rate-limit gate
- `document_processing/distributed/pipeline/clients/embedding.py` — OpenAI-compatible embedding client
- `api/main.py`, `api/routers/search.py`, `api/middleware/auth.py` — current Search API + static API-key auth
- `management/backend/main.py`, `management/backend/services/protocols.py` — Management API + JobService protocol (extension point for the new ingestion-api)
- `collab/docs/job-status-management-design-31032026.md` — the design that the new Pipeline API extends

### soev-gitops

- `shared-services/base/values.yaml`, `shared-services/<cluster>/values-patch.yaml` — where new components (RabbitMQ, ingestion-api, source publishers, KEDA scalers) get added
- `shared-services/previder-prod/helmrelease.yaml` — the gradient-gateway HelmRelease that will gain new sub-charts
- `tenants/<cluster>/<tenant>/helmrelease.yaml` — per-tenant config; gains `ingestionApiKey` field via 1Password
- `infrastructure/base/network-policies/shared-services-policy.yaml` — must be extended to allow tenant ingress to ingestion-api, and must keep RabbitMQ ingress to shared-services-internal only
- `infrastructure/<cluster>/iscsi-storage/pvs/` — RabbitMQ + (eventually) re-embed staging Weaviate collections will need PVs
- `helm/tenant-backup/` — the CronJob pattern reused by the cross-tenant re-embed operator
- `litellm/<cluster>/litellm-values-patch.yaml` — the team-virtual-key precedent for tenant ↔ ingestion-api auth
- `scripts/create-tenant.sh`, `scripts/populate-1password.sh` — extended to provision the new ingestion-api key and any org-scope source credentials

---

## Architecture Insights

1. **Two parallel sync models is a real risk.** soev-rag (SharePoint app-only sync) and the new genai-utils source-publisher pattern would both download/parse/chunk/embed from cloud sources if we are not careful. The decision to make is whether soev-rag becomes a **first consumer of genai-utils' source-publisher framework** (recommended) or stays a parallel implementation. The former unifies the pipeline; the latter doubles maintenance.
2. **Per-tenant Weaviate is a strength, not a weakness.** It means we never have to invent tenant-prefix collection naming or worry about cross-tenant query leakage. The cost is more PVs and more StatefulSets to babysit. For data-sovereignty positioning, this is the right trade.
3. **The push-ingest API is the right return channel.** Genai-utils does not need to know how open-webui's `File` and `KnowledgeFile` and `access_control` tables work. It calls `POST /api/v1/integrations/ingest` with `data_type=chunked_embedded` (or `chunked_text` if we want shared-services to embed but tenant-side to validate / add to KB transactions). Open-webui handles file-record creation, KB linking, RBAC. This is exactly the boundary we want.
4. **The decision in 2026-03-13 to stay custom (vs airweave) is still load-bearing.** Even with an in-house shared-services pipeline, the *connector code* gravitates toward this design (BaseSyncWorker pattern). We should not regress on RBAC depth.
5. **Re-embed via Weaviate aliases avoids "stale results during migration" entirely.** Insert into `{kb_id}_v{n+1}`, atomic alias swap, drop old. Users see a brief moment of new vectors but never partial state. Worth ~2 days of engineering.
6. **KEDA on RabbitMQ queue depth, not CPU, is the autoscaling story.** CPU is too laggy a signal for bursty ingestion (a Confluence sync of 5k pages spikes parsers for 10 minutes then drops to zero). Queue depth scales the right thing at the right time.
7. **The hardest organizational thing is operationalizing per-tenant `ingestionApiKey` rotation.** 1Password rate limits are account-wide (we already hit this once). We should establish a rotation procedure now, not after the first incident.

---

## Historical Context (from thoughts/)

The open-webui repo has by far the densest record of related research. This proposal extends, not replaces, those decisions:

- `thoughts/shared/research/2026-03-06-external-data-pipeline-ingestion.md` — original Octobox external-pipeline design; introduced `save_docs_to_vector_db(split=False)` and `save_embeddings_to_vector_db()` as the embedding entry points
- `thoughts/shared/research/2026-03-13-airweave-vs-custom-integration-comparison.md` — **decision: stay custom for OneDrive/SharePoint due to RBAC depth**. Still load-bearing.
- `thoughts/shared/research/2026-03-15-push-ingest-integration.md` + `thoughts/shared/plans/2026-03-15-push-ingest-integration.md` — push-ingest provider registry pattern (Phases 1–4 complete)
- `thoughts/shared/research/2026-03-18-generic-push-interface-design.md` — discriminated-union `data_type` design (`parsed_text | chunked_text | chunked_embedded | full_documents`); the contract genai-utils returns into
- `thoughts/shared/research/2026-03-20-upstream-merge-strategy.md` — fork-management policy: additive, feature-flagged changes wherever possible. Anything we propose must respect this.
- `thoughts/shared/research/2026-03-24-cloud-sync-abstraction-audit-merge-strategy.md` — the most relevant doc; the abstraction is **production-ready and generalizes to Topdesk/Confluence/Box/Dropbox with minor caveats**. Includes integration cookbook.
- `thoughts/shared/plans/2026-03-24-cloud-sync-abstraction-refactor.md` — implementation plan for the abstraction we're now extending
- `thoughts/shared/plans/2026-03-25-google-drive-backend-token-proxy.md` — proxy pattern for backend-mediated token refresh (reusable for the new architecture)
- `thoughts/shared/research/2026-03-29-google-drive-sync-bugs.md` — concrete failure modes that informed the retry/timeout design
- `thoughts/shared/research/2026-03-31-file-upload-processing-pipeline.md` (+ follow-up) — concurrency analysis; documents the proven `Semaphore(5)` pattern that becomes the reference for KEDA settings
- `thoughts/shared/plans/2026-04-06-cloud-kb-single-collection.md` — single-collection-per-tenant trade-off (informs alias-swap re-embed)
- `thoughts/shared/plans/2026-03-30-cloud-kb-permission-fix.md` — **load-bearing precedent for the org-scope ACL design**: never let sync workers mutate `access_control`. Use suspension lifecycle for revoked sources.
- `thoughts/shared/plans/2026-04-09-data-sovereignty-guard.md` — the data-sovereignty contract this whole architecture must respect.
- `thoughts/shared/plans/2026-03-26-push-integration-metadata-and-openapi.md` — OpenAPI surface for push-ingest; informs the genai-utils → open-webui callback shape.

genai-utils:
- `thoughts/shared/research/2026-02-05-document-processing-distributed-restructure.md` — the distributed pipeline restructure; the foundation we build on.
- `thoughts/shared/research/2026-02-04-distributed-worker-parallelization.md` — worker parallelization model (RabbitMQ fanout + self-filtering).
- `thoughts/shared/research/2026-01-13-gradient-gateway-migration-plan.md` — context on the shared-services gateway.

soev-gitops:
- `thoughts/shared/research/2026-03-22-fluxcd-gitops-adoption.md` — FluxCD topology; informs where new HelmReleases land.
- `thoughts/shared/research/2026-04-24-loki-ingestion-outage-iscsi-portal.md` — recent reliability lesson on multi-portal iSCSI; relevant for RabbitMQ PV placement.
- `thoughts/shared/research/2026-04-23-octobox-chat-agent-redeploy.md` — agent-stack deployment pattern; informs the per-tenant ingestion-api-key rollout.

---

## Open Questions

1. **soev-rag's role**: keep as a standalone SharePoint-only sync service, or migrate it to be the first `SourcePublisher` in genai-utils' new framework? Recommend the latter, but it is a real refactor (~3 weeks) and it currently works.
2. **Broker choice**: RabbitMQ (genai-utils is already coded against `aio_pika`, no rewrite) vs NATS JetStream (lighter ops, scale-to-zero friendlier). Recommend RabbitMQ for Phase 1 to ship fast; revisit at Phase 5.
3. **Org-scope onboarding UX**: where exactly does the admin paste the Confluence service-account credentials? Inline in `IntegrationProviders.svelte` (current pattern, plain DB storage) — but secrets in DB is a regression. Better: a "request connector" flow that emits a 1Password vault item template the operator fills in, then ESO syncs. Slower but correct.
4. **Per-tenant Weaviate vs shared with multi-tenancy**: Weaviate now supports tenant-per-shard. With ~30 PVs already, do we scale by adding tenant Weaviates or consolidate into a multi-tenant cluster? Out of scope for this proposal but will surface within 12 months.
5. **Embedding model migration concurrency**: when we re-embed across all 9 tenants, what's the safe parallelism? Empirically the embedding-API budget is the constraint. Recommend `1 tenant at a time, all collections in that tenant in parallel` as Phase 6 default; tune from there.
6. **Worker isolation per tenant**: should `ParserWorker` pods be per-tenant or shared? Sharing is cheaper; per-tenant gives stronger noisy-neighbor isolation. Recommend shared with **per-tenant rate-limit token buckets** in Redis (this is the airweave-style "soft isolation" we ruled out for the *vector store* but is fine for *worker pools*).
7. **Rollback story**: if shared-services ingestion goes wrong, how do tenants fall back to the in-pod path? Feature flag (`USE_SHARED_INGESTION`) per tenant in their HelmRelease. Phase 3 must keep both code paths working until at least one tenant has run on the new path for a full 30 days.

---

## Cross-Repo Companion Note

This document lives in `open-webui/thoughts/` because that is where the densest related research already exists and where most of the implementation surface is. A short pointer should be added to the corresponding indexes in genai-utils and soev-gitops once the user agrees to proceed past discussion stage, so the architecture is discoverable from any of the three repos.
