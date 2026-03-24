---
date: 2026-03-13T14:30:00+01:00
researcher: Claude
git_commit: db8b3acca67356114eb0e7c46709efedd6e4387f
branch: dev
repository: open-webui
topic: "Airweave vs Custom Integration: Should we adopt airweave for OneDrive/SharePoint or stay custom?"
tags: [research, codebase, airweave, onedrive, sharepoint, rbac, integrations]
status: complete
last_updated: 2026-03-13
last_updated_by: Claude
---

# Research: Airweave vs Custom Integration Comparison

**Date**: 2026-03-13T14:30:00+01:00
**Researcher**: Claude
**Git Commit**: db8b3acca67356114eb0e7c46709efedd6e4387f
**Branch**: dev
**Repository**: open-webui

## Research Question

How does airweave-ai/airweave's approach to external datasource integration differ from our custom implementation in open-webui? Should we adopt airweave, or do we need to stay custom for proper RBAC with OneDrive/SharePoint?

## Summary

**Recommendation: Stay custom for OneDrive/SharePoint RBAC. Consider airweave only for adding non-Microsoft connectors where permissions don't matter.**

Our custom implementation has deeper RBAC integration than airweave offers. Airweave's ACL support for SharePoint Online was only introduced in v0.9.25 (March 2026) and is connector-specific — there is no generalized permission propagation framework. Our implementation already maps OneDrive folder permissions to Open WebUI users/groups at every sync cycle, enforces owner-only write access, and integrates directly with Open WebUI's access control system at the SQL query level.

## Detailed Comparison

### Architecture

| Aspect | Airweave | Our Implementation |
|--------|----------|-------------------|
| **Scope** | 59 connectors, general-purpose retrieval layer | OneDrive-focused with sync provider abstraction for extensibility |
| **Deployment** | Standalone service (FastAPI + Temporal + Redis + Vespa/Qdrant + PostgreSQL) | Integrated into open-webui (no extra services) |
| **Vector DB** | Vespa (migrating from Qdrant) | Pluggable (15+ backends via `VectorDBBase` ABC) |
| **Orchestration** | Temporal workflows | asyncio scheduler + background tasks |
| **Auth** | Auth0 JWT | Open WebUI's native auth |
| **File processing** | Entity pipeline with micro-batching | Per-file processing with semaphore concurrency |
| **Incremental sync** | Content-hash based (entity-level) | Delta queries (Microsoft Graph delta API) + content hashes |

### RBAC Comparison (Critical Differentiator)

| Capability | Airweave | Our Implementation |
|------------|----------|-------------------|
| **Platform auth** | Auth0 JWT with org-level isolation | Open WebUI native auth (users, groups, roles) |
| **Multi-tenancy** | Soft isolation via metadata filtering on shared HNSW index | Hard isolation via separate vector collections per KB |
| **ACL from source** | New (v0.9.25, SharePoint Online only), connector-specific | Mature: maps Graph API permissions → OW users on every sync |
| **Permission enforcement at query time** | Not documented as general feature | SQL-level `has_permission()` filter on knowledge base queries |
| **Write access control** | Not differentiated | Owner-only write; shared users get read-only |
| **Group-based access** | Not documented | Full support via `group_ids` in access_control |
| **Access control for non-local KBs** | N/A | Locked to `{}` (private) on creation, auto-managed by sync |
| **Stale permission handling** | Unknown | Permissions refreshed every sync cycle; `needs_reauth` flag |

### OneDrive/SharePoint Specifics

| Feature | Airweave | Our Implementation |
|---------|----------|-------------------|
| **Delta queries** | Content-hash comparison | Native Microsoft Graph delta API with delta links |
| **Token management** | OAuth2 + PKCE, encrypted storage | OAuth2 + PKCE, stored in OAuthSessions table, 5-min refresh buffer |
| **File picker** | Not documented | MSAL.js frontend integration with OneDrive file picker SDK |
| **Folder-to-file decomposition** | Not documented | When removing single file from folder source, decomposes to individual file sources |
| **Cross-KB vector propagation** | N/A | When file updates, vectors propagate to all KBs referencing it |
| **File limits** | Not documented | 250 files per non-local KB |
| **Real-time progress** | Not documented | Socket.IO events for file processing, completion, progress |
| **Supported file types** | Not specified | 14 types (.pdf, .docx, .xlsx, .pptx, .md, etc.) |

### What Airweave Gives Us That We Don't Have

1. **Breadth of connectors**: 59 sources (Google Drive, Slack, GitHub, Confluence, Salesforce, Jira, etc.)
2. **Temporal-based orchestration**: More robust for long-running, complex sync workflows
3. **Micro-batching pipeline**: Better throughput for large-scale ingestion
4. **MCP protocol support**: Agents can query collections via MCP
5. **SDK availability**: Python and TypeScript SDKs for programmatic access

### What We Have That Airweave Doesn't

1. **Deep RBAC integration**: Permissions flow from Graph API → user lookup → access_control on KB → SQL-level query filtering → per-user search results
2. **No extra infrastructure**: Runs within open-webui (no Temporal, Redis, separate PostgreSQL, separate vector DB)
3. **Delta query sync**: True incremental sync using Microsoft's delta API rather than content-hash comparison
4. **File picker UX**: Native OneDrive file picker in the frontend
5. **Granular deletion semantics**: Folder-to-file decomposition, orphan detection, soft delete with cascade cleanup
6. **Real-time sync UI**: Socket.IO events for live progress feedback

## Recommendation

### Stay custom for OneDrive/SharePoint
Our RBAC implementation is significantly more mature and tightly integrated than airweave's. Key reasons:
- **Permission mapping**: We map Graph API permissions to OW users and enforce at the SQL level. Airweave's ACL support is brand new and connector-specific.
- **Multi-tenancy model**: We use separate vector collections per KB (hard isolation). Airweave uses metadata filtering on a shared index (soft isolation) — a potential security concern for enterprise deployments.
- **No extra infrastructure**: Adopting airweave means deploying Temporal, Redis, Vespa/Qdrant, and another PostgreSQL instance. Our current approach runs within the existing open-webui stack.
- **Delta query advantage**: Microsoft's delta API is more efficient than content-hash comparison for change detection.

### Consider airweave for non-Microsoft connectors (with caveats)
If we want to add Google Drive, Slack, Confluence, etc., airweave's connector library is valuable. However:
- **RBAC gap**: Airweave would not enforce our access control model. We'd need a bridge layer to map airweave collections to OW knowledge bases with proper access_control.
- **Infrastructure cost**: The deployment footprint is substantial (6+ services).
- **Our sync provider abstraction is extensible**: `services/sync/provider.py` already has a `SyncProvider` ABC with a factory function. Adding a `GoogleDriveSyncProvider` following the same pattern as `OneDriveSyncProvider` would maintain our RBAC model.

### If we do adopt airweave
The integration pattern would be:
1. Deploy airweave as a sidecar service
2. Use airweave's MCP server or SDK to query collections
3. Build a bridge layer that maps airweave collections → OW knowledge bases
4. Override airweave's auth with our own (bypass Auth0, inject OW user context)
5. Implement permission filtering at the OW layer since airweave's ACL is insufficient

This is significant engineering effort and may not be worth it compared to building custom connectors following our existing provider pattern.

## Code References

### Our Implementation
- `backend/open_webui/services/sync/provider.py` — Sync provider ABC (extensible for new connectors)
- `backend/open_webui/services/onedrive/sync_worker.py:415-511` — Permission sync from Graph API
- `backend/open_webui/utils/access_control.py:124` — `has_access()` check
- `backend/open_webui/utils/db/access_control.py:9` — SQL-level permission filtering
- `backend/open_webui/routers/knowledge.py:189` — Type validation (local, onedrive)
- `backend/open_webui/services/onedrive/scheduler.py:31` — Background sync scheduler
- `backend/open_webui/models/knowledge.py:47` — Knowledge type column

### Airweave
- GitHub: https://github.com/airweave-ai/airweave
- Docs: https://docs.airweave.ai
- SharePoint ACL PR: https://github.com/airweave-ai/airweave/pull/1419

## Historical Context
- `thoughts/shared/research/2026-02-14-onedrive-dedup-and-sync-status.md` — Confirms airweave directory not checked out; documents patterns adopted from airweave
- `thoughts/shared/plans/2026-02-04-typed-knowledge-bases.md` — Typed knowledge bases feature plan

## Open Questions
1. **Google Drive priority**: Is adding Google Drive the next connector? If so, building a custom `GoogleDriveSyncProvider` following our existing pattern is likely faster and more secure than integrating airweave.
2. **MCP integration**: Could we use airweave purely as an MCP tool provider (like in soev.ai) rather than as the primary retrieval layer? This would avoid the RBAC gap but limit the integration depth.
3. **Airweave's ACL roadmap**: Is their SharePoint ACL support evolving toward a generalized framework, or will it remain connector-specific? Worth monitoring.
