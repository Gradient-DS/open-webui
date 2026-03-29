---
date: 2026-03-26T16:00:00+02:00
researcher: Claude
git_commit: 84d62d6bd6abd3d83626a54906d97bb7381a619d
branch: feat/external-base-agents
repository: open-webui
topic: "Airweave as unified integration layer: adoption feasibility and contribution opportunities"
tags: [research, airweave, integrations, onedrive, google-drive, rag, mcp, connectors]
status: complete
last_updated: 2026-03-26
last_updated_by: Claude
---

# Research: Airweave as Unified Integration Layer

**Date**: 2026-03-26T16:00:00+02:00
**Researcher**: Claude
**Git Commit**: 84d62d6bd6abd3d83626a54906d97bb7381a619d
**Branch**: feat/external-base-agents
**Repository**: open-webui

## Research Question

Airweave (github.com/airweave-ai/airweave) offers a unified integration layer for agents with data sources. How does it differ from our OneDrive/Google Drive integrations, could we use it instead, and what could we contribute to make it workable for our use case?

## Summary

**Airweave is a strong project (MIT, YC S25-backed, 6.1k stars, 61+ connectors) but replacing our custom integrations with it would be a net negative today.** Our OneDrive and Google Drive implementations are more tightly integrated with Open WebUI's permission model, use provider-native incremental sync (delta API / Changes API), require zero extra infrastructure, and provide native file picker UX. However, Airweave has massive connector breadth and a mature MCP server — making it valuable as a **complementary tool provider** rather than a replacement for our core sync pipeline.

The most impactful contribution we could make to Airweave is **generalized ACL/permission propagation** — something Airweave currently lacks as a framework, and which we've already solved in our sync abstraction layer.

## What's Changed Since Previous Research (2026-03-13)

The prior comparison (`thoughts/shared/research/2026-03-13-airweave-vs-custom-integration-comparison.md`) was written before Google Drive was implemented. Key updates:

1. **Google Drive is now fully built** using the same sync provider abstraction as OneDrive — validating our extensibility model
2. **Airweave grew from 59 to 61+ connectors** and released v0.9.55 (452 releases total)
3. **Airweave migrated from Qdrant to Vespa** as primary vector DB
4. **Airweave's MCP server matured** with tiered search (instant/classic/agentic) and Streamable HTTP transport (MCP 2025-03-26)

## Detailed Comparison

### Architecture Side-by-Side

| Aspect | Airweave | Our Implementation |
|--------|----------|-------------------|
| **Scope** | 61+ generic connectors | 2 deep integrations (OneDrive, Google Drive) with provider abstraction |
| **Deployment** | 6+ services: FastAPI + Temporal + Redis + Vespa + PostgreSQL + frontend | Zero extra services — runs inside open-webui |
| **Vector DB** | Vespa (recently migrated from Qdrant) | 12 pluggable backends (Chroma default) via `VectorDBBase` ABC |
| **Orchestration** | Temporal workflows with micro-batching (64-entity batches, 200ms latency) | asyncio scheduler + `BackgroundTasks` with semaphore concurrency |
| **Auth model** | Auth0 JWT, org-level isolation, soft multi-tenancy via metadata filtering | Native OW auth, hard isolation via separate collections per KB |
| **Incremental sync** | Content-hash comparison at entity level | Provider-native: Microsoft Graph delta API, Google Changes API |
| **Embedding** | OpenAI text-embedding-3-small/large, MiniLM-L6-v2 | Configurable: local SentenceTransformers, Ollama, OpenAI, Azure OpenAI |
| **Agent access** | REST API, Python/TS SDKs, MCP server | Direct integration with OW chat pipeline |
| **File picker** | None (headless connector model) | Native OneDrive picker (MSAL.js) + Google Picker API |
| **License** | MIT | BSD-3 (Open WebUI) |

### What Airweave Does Better

1. **Connector breadth**: 61+ sources including Slack, Confluence, Jira, Salesforce, HubSpot, Notion, GitHub, Gmail, Zendesk — adding these ourselves would take months
2. **MCP server**: Production-ready with three search tiers:
   - **Instant**: Sub-second vector search
   - **Classic**: LLM-planned retrieval strategy (2-5s)
   - **Agentic**: Multi-step agent navigation
3. **Temporal orchestration**: Better suited for complex, long-running sync workflows with retry semantics
4. **Micro-batching pipeline**: `AsyncSourceStream` → 64-entity micro-batches with adaptive vector write batching (auto-halves on timeout)
5. **White-label multi-tenancy**: Embeddable connection widget for SaaS providers
6. **SDKs**: Python (`airweave-sdk`) and TypeScript (`@airweave/sdk`) for programmatic access

### What We Do Better

1. **RBAC / Permission propagation**:
   - We map provider permissions (Graph API, Drive Permissions API) → local user lookup → `access_grants` on KB → SQL-level `has_access()` filtering
   - Airweave has connector-specific ACL (SharePoint Online only since v0.9.25) with no generalized framework
   - We enforce owner-only write access; shared users get read-only
   - We use hard isolation (separate vector collections per KB) vs Airweave's soft isolation (metadata filtering on shared index)

2. **Provider-native incremental sync**:
   - OneDrive: Microsoft Graph delta API with delta links (tracks adds/modifies/deletes natively)
   - Google Drive: Changes API with page tokens
   - Airweave relies on content-hash comparison — more API calls, no native delete detection

3. **Zero infrastructure overhead**: No Temporal, Redis, Vespa, or separate PostgreSQL needed

4. **File picker UX**: Native OneDrive/Google Drive pickers in the browser — users see their familiar file browser

5. **Real-time progress**: Socket.IO events for file-level processing progress

6. **Granular file management**: Folder-to-file decomposition, cross-KB vector propagation, orphan detection, soft delete with cascade cleanup

## Could We Use Airweave Instead?

### For OneDrive/Google Drive: No

Replacing our existing integrations with Airweave would be a regression:

- **Lost RBAC**: We'd lose permission mapping from provider → OW users/groups. Airweave's ACL is nascent and connector-specific.
- **Lost UX**: No file picker — users would need to connect via Airweave's separate UI, then manually link collections to OW knowledge bases.
- **Lost incremental sync quality**: Delta API/Changes API are more efficient and reliable than content-hash comparison.
- **Added infrastructure**: 6+ new services to deploy and maintain.
- **Bridge layer needed**: We'd need to build a mapping layer (Airweave collection → OW knowledge base → access_control), which is essentially rebuilding what we already have.

### For additional connectors (Slack, Confluence, Jira, etc.): Maybe, as a sidecar

If we want to ingest data from sources beyond OneDrive/Google Drive, Airweave's connector library is valuable. The integration pattern would be:

1. Deploy Airweave as a sidecar service
2. Use Airweave's **MCP server** as a tool that agents can call during chat
3. Keep OW's knowledge base system for documents that need RBAC (OneDrive, Google Drive)
4. Use Airweave for "reference data" sources where per-user permissions don't matter (e.g., company-wide Confluence, public Jira boards, shared Slack channels)

This avoids the RBAC gap because these sources typically have uniform access within an organization.

### As an MCP tool provider: Yes, this is the sweet spot

The most natural integration point is Airweave's MCP server. Our soev.ai monorepo already configures Airweave as an MCP tool provider in `librechat.soev.ai.yaml`. For Open WebUI:

- Agents could call `search-{collection}` MCP tools during chat
- No need to sync data into OW's vector DB — Airweave manages its own
- Users manage connections via Airweave's UI (or embeddable widget)
- Keeps our RBAC model intact for files that need it

## What We Could Contribute to Airweave

### 1. Generalized ACL/Permission Propagation Framework (High Impact)

**The gap**: Airweave has connector-specific ACL for SharePoint Online only. There is no generalized framework for:
- Extracting permissions from source APIs
- Mapping external identities to Airweave users/organizations
- Filtering search results by user permissions at query time

**What we've built**: Our `BaseSyncWorker._sync_permissions()` pattern extracts provider permissions, maps emails to local users, and writes `access_grants`. This pattern is implemented for both OneDrive (`sync_worker.py:294-400`) and Google Drive (`sync_worker.py:210-290`).

**Contribution**: Propose an `ACLProvider` interface in Airweave's connector framework:
```python
class ACLProvider(ABC):
    async def get_entity_permissions(self, entity_id: str) -> list[Permission]
    async def resolve_identity(self, external_id: str) -> Optional[str]  # → airweave user
```
Plus a query-time filter that intersects search results with the requesting user's permissions. This would make Airweave viable for enterprise deployments where not all users should see all data.

### 2. Provider-Native Incremental Sync (Medium Impact)

**The gap**: Airweave's sync relies on content-hash comparison at the entity level. This works but is suboptimal for providers that offer native change tracking.

**What we've built**: Delta query support for Microsoft Graph and Google Drive Changes API.

**Contribution**: Add `DeltaSyncMixin` or similar to Airweave's connector framework that allows connectors to use provider-native change feeds instead of full enumeration + hash comparison. This would reduce API calls and improve delete detection.

### 3. File Picker Components (Medium Impact)

**The gap**: Airweave is headless — users connect via Airweave's UI or embeddable widget, but there's no native file/folder picker for selecting specific items to sync.

**What we've built**: MSAL.js OneDrive picker integration and Google Picker API integration that let users browse and select specific files/folders.

**Contribution**: Embeddable React components (Airweave's frontend is React) wrapping the OneDrive/Google Drive picker SDKs, allowing Airweave users to select specific items rather than syncing entire connections.

### 4. Pluggable Vector DB Backend (Lower Impact)

**The gap**: Airweave is coupled to Vespa (recently migrated from Qdrant).

**What we've built**: 12 vector DB adapters behind a `VectorDBBase` ABC.

**Contribution**: Abstract the vector DB interface in Airweave similar to our pattern, making it possible to use Chroma, Milvus, Pinecone, pgvector, etc. This lowers the deployment barrier (not everyone wants to run Vespa).

## Recommendation Summary

| Scenario | Recommendation |
|----------|---------------|
| Replace OneDrive/Google Drive sync | **No** — regression in RBAC, UX, and sync quality |
| Add Slack/Confluence/Jira/etc. connectors | **Consider Airweave as sidecar** for uniform-access sources |
| Give agents access to external data | **Use Airweave's MCP server** as a tool provider |
| Reduce our maintenance burden | **No** — Airweave adds infrastructure, not removes it |
| Contribute upstream | **Yes** — ACL framework is highest impact, benefits both projects |

## Code References

### Our Implementation
- `backend/open_webui/services/sync/provider.py` — SyncProvider ABC + factory
- `backend/open_webui/services/sync/base_worker.py` — Shared sync orchestration (~1000 lines)
- `backend/open_webui/services/onedrive/sync_worker.py:294-400` — OneDrive permission mapping
- `backend/open_webui/services/google_drive/sync_worker.py:210-290` — Google Drive permission mapping
- `backend/open_webui/services/google_drive/drive_client.py:192-230` — Changes API incremental sync
- `backend/open_webui/retrieval/vector/main.py` — VectorDBBase ABC (12 adapters)
- `backend/open_webui/models/knowledge.py:45` — Knowledge type column

### Airweave
- GitHub: https://github.com/airweave-ai/airweave
- Docs: https://docs.airweave.ai
- MCP server: https://docs.airweave.ai/mcp-server
- SDKs: https://docs.airweave.ai/sdks
- SharePoint ACL: https://github.com/airweave-ai/airweave/pull/1419

## Related Research
- `thoughts/shared/research/2026-03-13-airweave-vs-custom-integration-comparison.md` — Previous comparison (pre-Google Drive implementation)
- `thoughts/shared/plans/2026-02-04-typed-knowledge-bases.md` — Typed knowledge bases feature plan

## Open Questions

1. **MCP integration in Open WebUI**: Does Open WebUI support MCP tool calling? If so, Airweave's MCP server could be connected directly as a tool. If not, this would be a prerequisite for the sidecar pattern.
2. **Which additional connectors are most wanted?** Slack, Confluence, and Jira seem most likely for enterprise users. Prioritizing these would determine whether Airweave as a sidecar is worth the infrastructure cost vs building 1-2 custom providers.
3. **Airweave's ACL roadmap**: Is the SharePoint ACL support evolving toward a generalized framework? If so, contributing upstream becomes more attractive. Worth engaging with the Airweave team via a discussion issue.
4. **Contribution reception**: Airweave is YC-backed — would they accept significant architectural contributions from external contributors? Worth opening a discussion before investing effort.
