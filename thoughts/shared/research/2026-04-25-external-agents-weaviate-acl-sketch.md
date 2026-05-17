---
date: 2026-04-25
status: sketch
related-plan: thoughts/shared/plans/2026-04-25-shared-services-loader-worker.md
---

# External Agents → Tenant Weaviate, with Open-WebUI ACLs

A design sketch, not a plan. Captures the direction so that Phase 1 of the loader-worker plan builds infrastructure compatible with this follow-up.

## The problem

External agents (LangGraph, n8n flows, custom retrieval tools) want to query the tenant's Weaviate directly — different search modes, hybrid retrieval, multi-collection joins — without going through open-webui's chat-retrieval router. The open-webui router is the bottleneck this plan also targets for the sync path.

But Weaviate has no notion of "user X can read KB A but not KB B." The ACL model lives in open-webui's Postgres (`access_grants` table, group membership, KB ownership) and is enforced today by a clean function:

```python
# backend/open_webui/models/access_grants.py:561
AccessGrants.get_accessible_resource_ids(
    user_id, resource_type='knowledge', resource_ids, permission='read', ...
) -> set[str]
```

So the question reduces to: **how does an external agent, on every query, learn which `kb_id` values it is allowed to filter Weaviate by, on behalf of a specific user?**

## Standard practice

OAuth 2.0 Token Exchange (RFC 8693) → short-lived token-with-claims → server-side filter injection at the data store. Variants:

- **Fat token (JWT with claims)**: agent verifies signature locally, no per-query round-trip, stale until expiry. Bad revocation.
- **Thin token + per-query authz call**: always fresh, extra latency.
- **Hybrid**: thin token for identity, ACL bundle cached for 30–60s.

In all cases, the ACL is applied as a **filter at query construction** (`where: kb_id IN [...]`) — Weaviate itself stays oblivious.

## Recommended direction: Option C (open-webui owns ACL resolution, authz service caches)

1. **Open-webui exposes** `GET /api/v1/internal/users/{user_id}/kb-access`:
   - Auth: machine-bearer (`INTERNAL_AUTHZ_API_KEY`, sibling of `LOADER_INGEST_API_KEY` from Phase 1).
   - Returns: `{ allowed_kb_ids: [...], weaviate_endpoint: "...", expires_at: "..." }`.
   - Implementation: 1–2 day task. Calls `AccessGrants.get_accessible_resource_ids(user_id, 'knowledge', all_kb_ids)`, filters out suspended KBs, returns the list. **The ACL function is already clean** — no extraction refactor required.

2. **`gradient-authz` service in shared-services**:
   - Wraps the open-webui endpoint with a 30s LRU cache keyed by `(tenant, user_id)`.
   - Exposes `POST /authorize` to agents — same machine-bearer + acting-user-header shape as the loader-worker's `/ingest` callback.
   - Returns: KB filter list + connection details for tenant Weaviate.
   - Becomes the natural home for future token-exchange work (OIDC → user_id mapping), audit logging.

3. **Agents** construct Weaviate queries with the returned filter:
   ```python
   client.query.get("ChunkCollection", [...]).with_where({
       "path": ["kb_id"], "operator": "ContainsAny", "valueText": allowed_kb_ids
   })
   ```

### Why C over alternatives

- **Over Option A (query through open-webui)**: explicitly defeats the goal. Tenant-pod load reduction is the whole point.
- **Over Option B (authz service reads tenant Postgres directly)**: introduces a cross-service DB-read coupling. Drift risk between authz's ACL implementation and open-webui's is permanent. Option C uses open-webui as the single source of truth — no second implementation to maintain.
- **Over Option D (JWT with embedded claims)**: revocation is hard. A removed group membership should propagate in seconds, not at JWT-expiry time.

### Why this fits the loader-worker plan

- **Reuses the per-tenant key infrastructure** from loader-worker Phase 1.3 (per-tenant `tenant-keys-<tenant>` Secrets). The authz service mounts the same projected volume.
- **Reuses the machine-bearer + acting-user-header pattern** from Phase 1.4. Same `LoaderPrincipal`-shaped dependency, same threat model, same trade-off documentation.
- **Reuses the doc-processor's auth middleware** pattern from Phase 1.5 (poll-mtime hot-reload of per-tenant Secrets).

The shape is the same. The query path just needs an additional resource: an authz call before constructing the Weaviate query.

## What this plan does *not* commit to

- Direction of the answer to "where does the agent run?" — could be in shared-services (alongside the authz service) or per-tenant in chat. The authz service supports both.
- Whether `gradient-authz` is a separate pod or a route group inside an existing service. Probably separate, for blast-radius isolation, but not load-bearing for the design.
- Audit logging, rate limits, OIDC integration. All future surface area on top of this base.

## Concrete next steps when this becomes a priority

1. Land the loader-worker plan first. Phase 1's per-tenant Secret pattern and `LoaderPrincipal` are prerequisites.
2. Add `INTERNAL_AUTHZ_API_KEY` to the per-tenant `tenant-secrets` 1P item; sync via the same ESO pattern.
3. Implement `GET /api/v1/internal/users/{user_id}/kb-access` in open-webui. ~1–2 days. Gate on `INTERNAL_AUTHZ_API_KEY` via the same `get_integration_principal` helper, with a new `AuthzPrincipal` variant that requires only the bearer (no acting-user header — the user_id is in the path).
4. Build `gradient-authz` in `genai-utils/api/gateway/authz/` mirroring the loader-worker structure. Cache + relay.
5. Document the agent integration contract: how to obtain the per-tenant key, what headers to send, how to apply the returned filter to Weaviate queries.

## Risks and open questions

- **Cache invalidation on permission revocation**: 30s is a deliberate trade-off. An admin removing a user from a group needs to know that revocation isn't instant. Document this in admin docs when the feature ships. A small `POST /invalidate` from open-webui to the authz service on permission changes is a reasonable hardening.
- **Per-call latency floor**: even with cache, the first query for any (user, tenant) pair pays one round-trip to open-webui. Probably ~10–30ms. Acceptable for non-streaming agent queries; revisit if streaming-token-by-token agents become a use case.
- **Tenant Weaviate endpoint exposure**: today, tenant Weaviate is namespace-scoped behind ClusterIP. Agents in shared-services need network access — this is a NetworkPolicy diff. Probably fine via a `uses-tenant-weaviate: "true"` label on the authz pod's namespace.
- **What about agents that should bypass user ACLs** (admin/system agents)? Out of scope for this sketch — would be a separate principal type with explicit super-user grants, audit-logged per call.

## Reference

- Loader-worker plan (prerequisite): `thoughts/shared/plans/2026-04-25-shared-services-loader-worker.md`
- ACL resolution: `backend/open_webui/models/access_grants.py:561`
- Existing per-query ACL guard: `backend/open_webui/routers/retrieval.py:2547` `_validate_collection_access`
- Auth pattern this builds on: `backend/open_webui/utils/service_auth.py` (introduced in loader-worker Phase 1)
