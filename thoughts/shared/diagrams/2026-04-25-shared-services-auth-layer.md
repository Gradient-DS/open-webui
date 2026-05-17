# Shared-Services Loader-Worker — Auth Layer

Companion diagram to `thoughts/shared/plans/2026-04-25-shared-services-loader-worker.md`.

## Bearer-key matrix

| Caller | Recipient | Bearer presented | Identity carried |
|---|---|---|---|
| Browser user | tenant `/ingest` | session cookie | `user.id` (UUID) from session, `provider` from `user.info` |
| Tenant pod | loader-worker `/jobs` | `PIPELINE_API_KEY` (per tenant) | `acting_user_id` (UUID), `provider_slug` in body |
| Loader-worker | doc-processor `/process-document` | `PIPELINE_API_KEY` (forwarded) | tenant slug from middleware |
| Loader-worker | tenant `/ingest` | `LOADER_INGEST_API_KEY` (per tenant) | `X-Acting-User-Id`, `X-Acting-Provider` headers |
| Doc-processor | LiteLLM `/embeddings` | tenant LiteLLM team key (sourced from `/var/tenant-litellm-keys/<tenant>/key`) | n/a |
| External agent | authz `/authorize` | `AGENT_API_KEY_<name>` (per agent, allow-listed) | `X-Acting-User-Id`, `X-Tenant` (Phase 5) |
| Authz service | open-webui internal endpoints | `INTERNAL_AUTHZ_API_KEY` (per tenant) | `user_id` in URL path (Phase 5) |
| External agent | tenant Weaviate (read-only) | per-tenant Weaviate read-only API key | filter constructed from authz response (Phase 5+6) |

## Sequence

```mermaid
sequenceDiagram
    autonumber
    participant U as Browser User
    participant OW as open-webui pod
    participant LW as loader-worker
    participant DP as doc-processor
    participant LL as LiteLLM
    participant C as OneDrive / GDrive

    Note over OW,LW: Outbound: tenant -> shared-services
    U->>OW: Trigger sync (cookie auth)
    OW->>OW: TokenManager.get_valid_access_token(user_id)
    OW->>LW: POST /tenants/{t}/jobs<br/>Bearer PIPELINE_API_KEY<br/>body: { acting_user_id, provider_slug, items, embedding_config }
    LW-->>OW: 202 + Location: /jobs/{id}

    Note over LW,DP: Internal to shared-services
    LW->>C: GET file (ephemeral OAuth token, single-use)
    C-->>LW: bytes
    LW->>DP: POST /process-document<br/>Bearer PIPELINE_API_KEY (tenant-scoped)<br/>multipart: bytes + embedding_config (no api_key)
    DP->>DP: read /var/tenant-litellm-keys/{tenant}/key
    DP->>LL: POST /v1/embeddings<br/>Bearer (tenant LiteLLM key, server-sourced)
    LL-->>DP: vectors
    DP-->>LW: chunks + embeddings

    Note over LW,OW: Inbound callback: shared-services -> tenant
    LW->>OW: POST /api/v1/integrations/ingest<br/>Bearer LOADER_INGEST_API_KEY<br/>X-Acting-User-Id, X-Acting-Provider<br/>body: { collection, documents }
    OW->>OW: bearer matches LOADER_INGEST_API_KEY<br/>=> machine principal<br/>=> resolve acting_user from header<br/>=> Files.insert_new_file(user_id=acting_user_id)
    OW-->>LW: 200 { created, updated, errors }
```

## Sequence — Phase 5+6: External agent → authz service → Weaviate (query path)

See `2026-04-25-authz-service-query-path.png` for the rendered diagram.

```mermaid
sequenceDiagram
    autonumber
    participant Agent as External Agent
    participant Authz as gradient-authz
    participant OW as open-webui pod
    participant W as Tenant Weaviate

    Note over Agent,Authz: agent -> shared-services
    Agent->>Authz: POST /authorize<br/>Bearer AGENT_API_KEY_<name><br/>X-Acting-User-Id, X-Tenant<br/>body: { resource_type: "kb_search" }

    alt cache miss (or expired, default 30s)
        Authz->>OW: GET /api/v1/internal/users/{user_id}/kb-access<br/>Bearer INTERNAL_AUTHZ_API_KEY
        OW-->>Authz: { allowed_kb_ids, kb_modes }
        Authz->>OW: GET /api/v1/internal/users/{user_id}/principals<br/>Bearer INTERNAL_AUTHZ_API_KEY
        OW-->>Authz: { principals: { confluence: [...], gdrive: [...] } }
    else cache hit
        Note over Authz: serve from in-memory LRU
    end

    Authz-->>Agent: { allowed_kb_ids, kb_modes,<br/>user_principals, weaviate_endpoint, expires_at }

    Note over Agent,W: agent constructs Weaviate query
    Agent->>W: query<br/>where: { kb_id IN allowed_kb_ids } AND<br/>(kb_mode=kb_inherited OR<br/>acl_principals CONTAINS_ANY user_principals)
    W-->>Agent: filtered chunks

    Note over Authz,OW: audit (Phase 6, fire-and-forget)
    Authz-)OW: POST /api/v1/internal/audit/authz-query<br/>Bearer INTERNAL_AUTHZ_API_KEY<br/>body: { agent_id, acting_user_id, allowed_kb_count, principal_count, cache_hit }
```

## Key insight

The loader-worker is a **machine**, not a user. It proves "I am the loader" with `LOADER_INGEST_API_KEY`. The `user_id` for File ownership and the `provider_slug` for KB attribution come from the request — set by the tenant pod when it submits the job (it knows who triggered the sync), echoed back by the loader-worker on the callback.

Phase 5+6 generalizes the same pattern to the query path: `gradient-authz` is also a machine, presenting `INTERNAL_AUTHZ_API_KEY` to open-webui. The user_id is in the URL path (a query *about* a user, not a write *as* a user). External agents present `AGENT_API_KEY_<name>` to authz, with `X-Acting-User-Id` carrying the open-webui user identity. Open-webui remains the single source of truth for ACL state — authz is a caching relay.

This means:
- No DB service-account user to provision per tenant
- No admin-UI binding for a synthetic user
- No FK abuse — `Files.user_id` references a real human, the same one the in-pod sync uses today
- Existing `/ingest` cookie-auth path is untouched; the acting headers are simply ignored when a real session is present

Trade-off: a stolen `LOADER_INGEST_API_KEY` lets an attacker impersonate any `user_id` they know. That blast radius is no worse than today's "ingest arbitrary chunks to arbitrary KBs" with the same key.
