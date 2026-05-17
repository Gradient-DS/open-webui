---
date: 2026-04-21T11:25:00+02:00
researcher: Lex Lubbers
git_commit: decb3bbf6297bdb0aaf53c0edd6456f54de88b80
branch: feat/agent-selector
repository: Gradient-DS/open-webui
topic: "On-prem client deployment config handoff: EntraID SSO, OneDrive/SharePoint, Confluence, TOPdesk, and baseline config"
tags: [research, on-prem, deployment, sso, entraid, onedrive, sharepoint, confluence, topdesk, helm, config]
status: complete
last_updated: 2026-04-21
last_updated_by: Lex Lubbers
---

# Research: On-Prem Client Config & Secrets Handoff

**Date**: 2026-04-21 11:25 CEST
**Researcher**: Lex Lubbers
**Git Commit**: decb3bbf6
**Branch**: feat/agent-selector
**Repository**: Gradient-DS/open-webui

## Research Question

Client is installing our soev.ai Open WebUI fork on-prem (via Intermax). Compile the list of config variables and secrets needed for:

1. SSO with Entra ID (and required API permissions)
2. OneDrive/SharePoint coupling (same vs separate app registration, required Graph rights)
3. Baseline/standard config
4. Confluence (Atlassian OAuth 2.0)
5. TOPdesk (service account, Base URL + app username + app password)
6. General networking/DevOps concerns

Also: is Confluence/TOPdesk setup "hetzelfde als OneDrive/Google Drive"? And: should Confluence be a knowledge base or a separate search tool?

## Summary

- **SSO (Entra ID)** and **OneDrive/SharePoint** are supported out of the box. SSO can reuse the OneDrive app registration, but **email invites require a separate app registration** (different auth flow + admin-consent-only permissions).
- **Confluence and TOPdesk are NOT implemented** in the current fork. There is no code, no config scaffolding, no router. They are mentioned only as *examples* in the integration cookbook.
- Two paths forward for Confluence/TOPdesk (see §5):
  - **Push-based integration provider** — framework exists today, the external system pushes docs to `/api/v1/integrations/ingest`. Lowest effort on our side; zero sync code to write.
  - **Pull-based sync provider** (like OneDrive/Google Drive) — 1-3 days of engineering per provider following `collab/docs/external-integration-cookbook.md`.
- Intermax's description is **subtly different from OneDrive/GDrive**. OneDrive/GDrive use **delegated OAuth (per-user PKCE flow)** — each user authorises their own drive. Confluence/TOPdesk as described are **single service-account** credentials shared across all users. That's closer to our email invite pattern (client credentials) than to OneDrive.
- **Recommendation on Confluence**: KB ingestion (not a separate search tool). Reusing the existing external pipeline / push-integration model is the least-maintenance path and fits the "all retrieval via RAG" architecture.

The full audit below covers every env var/secret the Helm chart wires up, grouped by concern. Send Intermax §1-4 verbatim; internal §5-6 for our own planning.

---

## 1. What Intermax Needs From Entra ID (SSO + OneDrive/SharePoint + Email)

### 1.1 App Registrations — How Many?

**Minimum: 2 app registrations.** Optionally 3.

| App reg | Required? | Auth flow | Why separate? |
|---|---|---|---|
| **App A — User-facing (SSO + OneDrive)** | Yes | Delegated OAuth 2.0 (Auth Code + PKCE) | Users sign in and authorise drive access |
| **App B — Email invites (Graph Mail)** | Yes, if invites enabled | Client Credentials | Needs `Mail.Send` *application* permission — admin-consented, no user in the loop |
| **App C — Separate SSO reg** | Optional | Delegated OAuth 2.0 | Only needed if security policy demands SSO and drive rights be split |

SSO and OneDrive can live in **App A** (our current pattern). Email **must** be its own app registration — `Mail.Send` application permission cannot be combined with the user-consent delegated flow OneDrive uses.

### 1.2 App A — SSO + OneDrive/SharePoint (single delegated app)

**Register in Entra ID:**
- Platform: Web
- Redirect URIs to whitelist:
  - `https://<webui-domain>/oauth/microsoft/login/callback` (SSO login)
  - `https://<webui-domain>/oauth/microsoft/callback` (OneDrive OAuth callback)
- Implicit grant: leave OFF
- Allow public client flows: OFF
- Supported account types: "Accounts in this organisational directory only" (single tenant)
- Issue one **client secret** (24 month max lifetime; client to rotate)

**API permissions (Microsoft Graph — delegated):**

| Permission | Purpose | Admin consent? |
|---|---|---|
| `openid` | OIDC sign-in | No |
| `email` | Return email claim | No |
| `profile` | Return profile claims | No |
| `User.Read` | Read signed-in user profile | No |
| `Files.Read.All` | OneDrive sync: read user's drives | No (user-consented at drive connect) |
| `offline_access` | Issue refresh tokens (90-day) | No |
| `Sites.Read.All` | SharePoint reading (only if SharePoint sites used) | **Yes** |
| `Directory.Read.All` | Optional — only if syncing groups/roles via token claims | **Yes** |

**Token configuration (for roles/groups sync):**
- Add optional claims: `email`, `groups`, `roles`
- If using app roles: "Application roles" included in token

**Handoff to Intermax for App A:**
```
Tenant ID:       (GUID)                → MICROSOFT_CLIENT_TENANT_ID
                                          ONEDRIVE_SHAREPOINT_TENANT_ID
Client ID:       (GUID)                → MICROSOFT_CLIENT_ID
                                          ONEDRIVE_CLIENT_ID_BUSINESS
Client Secret:   (VALUE, not ID)       → MICROSOFT_CLIENT_SECRET
SharePoint URL:  https://<org>.sharepoint.com   → ONEDRIVE_SHAREPOINT_URL (optional)
```

### 1.3 App B — Email Invites (mandatory if invites enabled)

- Platform: none — this is a confidential client, no redirect URI needed
- Issue a **client secret**
- API permissions (Microsoft Graph — **application**, not delegated):
  - `Mail.Send` — **admin consent required**
- Optional hardening: Exchange `ApplicationAccessPolicy` restricting which mailboxes the app can send from (so the app can only send as `no-reply@<client-domain>`, not any mailbox).

**Handoff for App B:**
```
Tenant ID:       (GUID)                → EMAIL_GRAPH_TENANT_ID
Client ID:       (GUID)                → EMAIL_GRAPH_CLIENT_ID
Client Secret:   (VALUE)               → EMAIL_GRAPH_CLIENT_SECRET
Sender mailbox:  no-reply@<domain>     → EMAIL_FROM_ADDRESS
```

### 1.4 SSO env var list (paste into Helm values)

Required:
```
MICROSOFT_CLIENT_ID              = <App A client id>
MICROSOFT_CLIENT_SECRET          = <App A client secret>   (Kubernetes secret)
MICROSOFT_CLIENT_TENANT_ID       = <tenant id>
MICROSOFT_REDIRECT_URI           = https://<domain>/oauth/microsoft/login/callback
MICROSOFT_OAUTH_SCOPE            = openid email profile
ENABLE_OAUTH_SIGNUP              = true
OAUTH_ALLOWED_DOMAINS            = <client-domain>.nl
OAUTH_MERGE_ACCOUNTS_BY_EMAIL    = true
```

Recommended (cleaner logout + group/role sync):
```
OPENID_PROVIDER_URL              = https://login.microsoftonline.com/<tenant>/v2.0/.well-known/openid-configuration
OPENID_END_SESSION_ENDPOINT      = https://login.microsoftonline.com/<tenant>/oauth2/v2.0/logout
ENABLE_OAUTH_ROLE_MANAGEMENT     = true     # if using app roles
OAUTH_ROLES_CLAIM                = roles
OAUTH_ALLOWED_ROLES              = user,admin
OAUTH_ADMIN_ROLES                = admin
ENABLE_OAUTH_GROUP_MANAGEMENT    = true     # if using security groups
OAUTH_GROUPS_CLAIM               = groups
OAUTH_UPDATE_NAME_ON_LOGIN       = true
OAUTH_UPDATE_PICTURE_ON_LOGIN    = true
```

On-prem sovereign cloud override (unlikely for this client, but available):
```
MICROSOFT_CLIENT_LOGIN_BASE_URL  = https://login.microsoftonline.com  # default
```

Source: `backend/open_webui/config.py:381-425`, `backend/open_webui/config.py:451-659`, `backend/open_webui/utils/oauth.py`.

### 1.5 OneDrive/SharePoint env var list

```
ENABLE_ONEDRIVE_INTEGRATION       = true
ENABLE_ONEDRIVE_BUSINESS          = true
ENABLE_ONEDRIVE_PERSONAL          = false      # disable for enterprise client
ONEDRIVE_CLIENT_ID_BUSINESS       = <App A client id>   # can reuse MICROSOFT_CLIENT_ID
ONEDRIVE_SHAREPOINT_TENANT_ID     = <tenant id>
ONEDRIVE_SHAREPOINT_URL           = https://<org>.sharepoint.com   # optional, for SharePoint picker

ENABLE_ONEDRIVE_SYNC              = true
ONEDRIVE_SYNC_INTERVAL_MINUTES    = 15
ONEDRIVE_MAX_FILES_PER_SYNC       = 250
ONEDRIVE_MAX_FILE_SIZE_MB         = 10
```

Source: `backend/open_webui/config.py:2842-2883`, Helm chart `helm/open-webui-tenant/values.yaml:394-405`, `configmap.yaml:264-282`.

### 1.6 Email invites env var list

```
ENABLE_EMAIL_INVITES              = true
EMAIL_GRAPH_TENANT_ID             = <App B tenant>
EMAIL_GRAPH_CLIENT_ID             = <App B client id>
EMAIL_GRAPH_CLIENT_SECRET         = <App B secret>       (Kubernetes secret)
EMAIL_FROM_ADDRESS                = no-reply@<client-domain>
EMAIL_FROM_NAME                   = <Tenant display name>
INVITE_EXPIRY_HOURS               = 168
```

Source: `backend/open_webui/config.py:2889-2910`, `helm/open-webui-tenant/values.yaml:411-419`.

---

## 2. Baseline / Standard Deployment Config

These are independent of integrations. Intermax must set all of them.

### 2.1 Hard requirements (must be set in production)

| Variable | Secret | Notes |
|---|---|---|
| `WEBUI_SECRET_KEY` | Yes | Random 32+ char string. Auto-generated by Helm if not provided; **set it explicitly** so it doesn't rotate on every install. |
| `DATABASE_PASSWORD` | Yes | PostgreSQL password (Helm uses internal Postgres StatefulSet by default). |
| `OPENAI_API_KEY` | Yes | For LLM completions. Points at `OPENAI_API_BASE_URL` — can be the client's own Azure OpenAI or hosted endpoint. |
| `RAG_OPENAI_API_KEY` | Yes | For embeddings. Often the same as `OPENAI_API_KEY`. |
| `WEBUI_URL` | No | Public URL (`https://chat.<client-domain>.nl`) — used for OAuth redirect building, email links. |
| `CORS_ALLOW_ORIGIN` | No | Defaults to `https://<tenant.domain>` in the chart. Override if frontend served elsewhere. |

### 2.2 Tenant / branding

```
WEBUI_NAME                   = soev.ai           # or white-label
CLIENT_NAME                  = <Client Name>
DEFAULT_LOCALE               = nl-NL
ENV                          = prod
```

### 2.3 Auth baseline (on top of SSO config in §1)

```
WEBUI_AUTH                   = true
ENABLE_SIGNUP                = false             # SSO-driven, invites only
ENABLE_LOGIN_FORM            = true              # allow local admin rescue
ENABLE_INITIAL_ADMIN_SIGNUP  = true              # first bootstrap user
ENABLE_PASSWORD_AUTH         = true
DEFAULT_USER_ROLE            = user
JWT_EXPIRES_IN               = 4w
WEBUI_SESSION_COOKIE_SECURE  = true
ENABLE_2FA                   = false             # or true if required
REQUIRE_2FA                  = false
TWO_FA_GRACE_PERIOD_DAYS     = 7
```

### 2.4 LLM provider

```
ENABLE_OPENAI_API             = true
ENABLE_OLLAMA_API             = false
OPENAI_API_BASE_URL           = <LLM endpoint>
OPENAI_API_KEY                = <secret>
```

Multi-endpoint alternatives: `OPENAI_API_BASE_URLS` + `OPENAI_API_KEYS` (semicolon-separated).

### 2.5 Vector DB — Weaviate (chart default)

The chart ships Weaviate as an embedded StatefulSet. No extra credentials needed. Only override if client wants an external Weaviate instance.

```
VECTOR_DB                     = weaviate
WEAVIATE_HTTP_HOST            = <computed from chart>
WEAVIATE_HTTP_PORT            = 8080
WEAVIATE_GRPC_PORT            = 50051
```

### 2.6 RAG / embeddings

```
RAG_EMBEDDING_ENGINE          = openai
RAG_EMBEDDING_MODEL           = text-embedding-3-small
RAG_OPENAI_API_BASE_URL       = <embedding endpoint, if different>
RAG_OPENAI_API_KEY            = <secret>
RAG_EMBEDDING_BATCH_SIZE      = 100
RAG_TOP_K                     = 15
RAG_TOP_K_RERANKER            = 10
CHUNK_SIZE                    = 2000
CHUNK_OVERLAP                 = 200
ENABLE_RAG_HYBRID_SEARCH      = true
RAG_HYBRID_BM25_WEIGHT        = 0.5
RAG_ALLOWED_FILE_EXTENSIONS   = pdf,txt,md,docx,csv,json,xlsx
```

### 2.7 External content extraction + reranker (shared services)

Our chart expects a shared `gradient-gateway` + `reranker` deployment in the cluster (for Tika/Docling/Crawl4AI-style extraction and reranking). Intermax needs to deploy `gradient-gateway` Helm chart too, or provide equivalent endpoints:

```
CONTENT_EXTRACTION_ENGINE         = external
EXTERNAL_DOCUMENT_LOADER_URL      = http://<gateway>.shared-services.svc:8000
EXTERNAL_DOCUMENT_LOADER_API_KEY  = <secret, optional>
RAG_RERANKING_ENGINE              = external
RAG_EXTERNAL_RERANKER_URL         = http://<reranker>.shared-services.svc:8000/v1/rerank
RAG_EXTERNAL_RERANKER_API_KEY     = <secret, optional>
```

### 2.8 Web search (optional)

```
ENABLE_WEB_SEARCH             = true
WEB_SEARCH_ENGINE             = searxng
SEARXNG_LANGUAGE              = nl
WEB_SEARCH_RESULT_COUNT       = 5
SEARXNG_QUERY_URL             = http://<gateway>/search
EXTERNAL_WEB_LOADER_URL       = http://<gateway>/extract
```

### 2.9 Storage backend

Default: PVC on PostgreSQL/Weaviate volumes (10-20 GiB). Switch to S3-compatible for file storage only if client wants object storage:

```
STORAGE_PROVIDER              = s3       # or leave unset for local PVC
S3_BUCKET_NAME                = <bucket>
S3_REGION_NAME                = <region, e.g. nl-ams>
S3_ENDPOINT_URL               = https://object.<provider>.nl
S3_ACCESS_KEY_ID              = <secret>
S3_SECRET_ACCESS_KEY          = <secret>
```

### 2.10 Data retention / DPIA (GDPR)

```
ENABLE_DATA_EXPORT            = true
DATA_EXPORT_RETENTION_HOURS   = 24
ENABLE_USER_ARCHIVAL          = true
DEFAULT_ARCHIVE_RETENTION_DAYS = 1095     # 3 years, ISO 27001

# Configurable TTL — opt-in per customer
DATA_RETENTION_TTL_DAYS       = 0         # disabled by default
USER_INACTIVITY_TTL_DAYS      = 0
CHAT_RETENTION_TTL_DAYS       = 0
KNOWLEDGE_RETENTION_TTL_DAYS  = 0
DATA_RETENTION_WARNING_DAYS   = 30
ENABLE_RETENTION_WARNING_EMAIL = true     # requires email invites App B
```

### 2.11 Security headers

```
HSTS                          = max-age=31536000;includeSubDomains
XFRAME_OPTIONS                = SAMEORIGIN
XCONTENT_TYPE                 = nosniff
REFERRER_POLICY               = strict-origin-when-cross-origin
CACHE_CONTROL                 = no-store, max-age=0
```

### 2.12 Audit logging

```
AUDIT_LOG_LEVEL               = METADATA     # NONE|METADATA|REQUEST|REQUEST_RESPONSE
ENABLE_AUDIT_STDOUT           = true         # emit as JSON for log collector
LOG_FORMAT                    = json
```

### 2.13 Agent API (external agent service — Gradient agents)

If client wants to route chats to our agent service:
```
AGENT_API_ENABLED             = true
AGENT_API_BASE_URL            = https://<agent-service>
AGENT_API_AGENT               = <default agent name>
```

Separately, to expose the agent API externally (reverse proxy, sk- key auth):
```
ENABLE_AGENT_PROXY            = true       # mounts /api/v1/agent/
```

See `collab/index.md` entry 26-03-2026 — "Agent Proxy — External API for soev.ai Agents".

---

## 3. Confluence — What We Need From Intermax

**⚠ Currently not implemented in the codebase.** Implementation required on our side (see §5 for options).

### 3.1 Your answer to Intermax on auth mechanism

> *"Dat is hoe we het bij onedrive/google drive ook doen toch?"*

**Nee, niet precies.** OneDrive and Google Drive use **per-user delegated OAuth**: each user who wants to connect a drive goes through the consent flow and we store *their* refresh token. You see their files, filtered by their own permissions.

What Intermax is describing for Confluence (one app registration → one Client ID/Secret → ingest everything) is a **service-account / client-credentials** pattern: a single credential reads all accessible Confluence content for all users. That's how our **email invite** path works, not how OneDrive/GDrive work.

This matters for:
- **Permission handling** — with a service account, everyone sees whatever the service account can read. We'd need KB-level access grants to restrict exposure, not Confluence-level ACLs.
- **Audit trail** — actions in Confluence are logged as the service account, not the end user.
- **Implementation path** — push-based integration provider (§5.1) is a much better fit than our sync abstraction (§5.2), which is built around per-user OAuth.

If per-user delegated OAuth is what we actually want, ask Intermax to register an Atlassian OAuth 2.0 (3LO) app instead; `client_id` + `client_secret` stay the same but users individually authorise.

### 3.2 Variables (no implementation exists — these are the ones we'd introduce)

```
CONFLUENCE_BASE_URL           = https://<org>.atlassian.net
CONFLUENCE_CLIENT_ID          = <from Atlassian Developer Console>
CONFLUENCE_CLIENT_SECRET      = <secret>
CONFLUENCE_SCOPES             = read:confluence-content.all read:confluence-space.summary offline_access   # for 3LO
# OR for OAuth 2.0 (2LO / app-level):
CONFLUENCE_REDIRECT_URI       = https://<webui-domain>/oauth/confluence/callback
```

### 3.3 KB vs separate search tool — recommendation

> *"Willen we confluence als knowledgebase er in zetten? of aparte search tool?"*

**KB ingestion.** Reasons:

1. **Architecture alignment** — all retrieval in our stack flows through RAG → Weaviate. A separate search tool splits the retrieval path in two and we'd lose hybrid ranking / reranker / citation UI.
2. **Lowest engineering cost** — implementing a new search tool means a new model tool, new UI, new auth plumbing. KB ingestion reuses the existing integration provider framework (zero new UI, just a new admin-configured provider slug).
3. **Consistent permissions / retention** — KBs honour our access-grants model and the new `DATA_RETENTION_TTL_DAYS`. A side-channel search tool has no such handling.
4. **Offline operation** — once ingested, Confluence content stays searchable even if Confluence is down / network segmented.

Trade-off: freshness lag (depends on sync interval) and duplicate storage. For public-sector clients both are acceptable; Confluence pages change at human pace, not machine pace.

---

## 4. TOPdesk — What We Need From Intermax

**⚠ Currently not implemented in the codebase.** No OAuth exists for TOPdesk — service account is the only option, matching what Intermax is already doing.

Intermax correctly states: **TOPdesk does not support OAuth**. Auth is HTTP Basic with an **Application Password** (`Application password` in TOPdesk admin → User → Application passwords).

### 4.1 Variables (to be introduced)

```
TOPDESK_BASE_URL              = https://<client>.topdesk.net
TOPDESK_USERNAME              = <service account login>
TOPDESK_APP_PASSWORD          = <secret — NOT the user's normal password>
TOPDESK_SCOPE                 = readonly       # scope is informational; actual rights are set in TOPdesk
```

### 4.2 Service account rights

Ask Intermax to provision a TOPdesk account with:

- **Read-only** across whatever TOPdesk modules we need to index (Incidents? Changes? Knowledge items? Asset data?). Nail this down with the client — pulling *all* tickets is usually overkill and carries privacy risk (PII in ticket bodies).
- **Application password** issued under that account (not the normal password).
- **License type**: usually "Operator" or a dedicated "API" license — Intermax/TOPdesk admin to confirm licensing impact.
- **IP allowlisting** if TOPdesk supports it, restricted to the client's egress IPs from the cluster.

### 4.3 Compliance warning

TOPdesk tickets frequently contain PII (names, phone numbers, BSN, sometimes health info). Before ingesting:

- Confirm with the client's DPO which record types are in scope.
- Strongly consider filtering by category / permission before ingest, not after.
- Document retention expectations (should purging a TOPdesk ticket also purge our vector copy? If yes, we need sync to detect deletions — an additional engineering cost).

---

## 5. Implementation Options for Confluence & TOPdesk

**Both are currently absent from the codebase.** Zero code exists under `backend/open_webui/services/confluence/`, `backend/open_webui/services/topdesk/`, or `backend/open_webui/routers/`. The only mentions are in the integration cookbook (as hypothetical examples).

There are two implementation patterns to choose from:

### 5.1 Push-based — "External Integration Provider"

Framework already exists (`backend/open_webui/routers/integrations.py`, `POST /api/v1/integrations/ingest`). Admin creates a provider config (slug, max files, custom metadata fields), issues a service account API key, and the external system pushes documents in.

**For Confluence/TOPdesk:**
- We (or Intermax) build a small standalone worker (Python/cron job) that:
  - Calls Confluence/TOPdesk REST API with the service-account credentials
  - For each page/ticket: posts to `/api/v1/integrations/ingest` with the content + metadata
- Our side: admin configures provider in UI (`Admin → Integration Providers`), generates a service account, done.

**Pros:** No new code in our fork. Intermax owns the connector. Works today.
**Cons:** Intermax has to write + maintain two small sync services. Change detection logic lives on their side.

**Reference**: `src/lib/components/admin/Settings/IntegrationProviders.svelte`, `backend/open_webui/config.py:3320` (`INTEGRATION_PROVIDERS`).

### 5.2 Pull-based — native sync provider (like OneDrive/GDrive)

Follow `collab/docs/external-integration-cookbook.md` — subclass `BaseSyncWorker`, add router, picker, etc. Estimated 1-3 days per provider.

**Pros:** First-class UX (KB picker, sync status, cancellation, per-user delegated auth if Atlassian OAuth 3LO is used). Lives in our repo, we own it.
**Cons:** Engineering time. TOPdesk has no OAuth → the "auth" module for TOPdesk would bypass the token-refresh abstraction entirely, so fit is poor. Confluence fits cleanly.

### 5.3 Recommendation

| Provider | Path | Why |
|---|---|---|
| **Confluence** | Option A: push (5.1) if we want this live soon without dev budget. Option B: pull (5.2) if we want first-class UX and client is paying for the integration. | Confluence has a proper REST API + OAuth 3LO; either path works. |
| **TOPdesk** | Push (5.1), almost certainly. | No OAuth means we'd shoehorn service-account Basic auth into an abstraction built for token refresh. Not worth it. Write a standalone Basic-auth poller, push to our ingest endpoint. |

For the handoff to Intermax, this means:

- **Confluence**: tell them we'll build this; get the app registration + sandbox credentials now so dev can start.
- **TOPdesk**: tell them *they* build the poller (or we build it as a separate service, not inside Open WebUI), and they need a service account + application password provisioned.

---

## 6. DevOps / Networking Things Often Forgotten

Grouped so you can tick them off with Intermax:

### 6.1 DNS + TLS
- `chat.<client-domain>.nl` (or similar) resolving to the ingress load balancer.
- Valid TLS cert for that hostname. Chart defaults to cert-manager `letsencrypt-prod` — won't work in an air-gapped setup, so client must provide cert-manager with an internal issuer, or they ship the cert as a Kubernetes Secret.
- Ingress class name (client's cluster-specific, e.g., `nginx`, `traefik`, `cilium`).

### 6.2 Egress firewall rules
The pods need outbound HTTPS to (at minimum):
- `login.microsoftonline.com` — SSO token endpoint
- `graph.microsoft.com` — OneDrive + email
- `<org>.sharepoint.com` — SharePoint sync (if used)
- `<OPENAI_API_BASE_URL>` — LLM completions
- `<RAG_OPENAI_API_BASE_URL>` — embeddings
- `<AGENT_API_BASE_URL>` — agent service (if enabled)
- `<CONFLUENCE_BASE_URL>` — Confluence (once implemented)
- `<TOPDESK_BASE_URL>` — TOPdesk (once implemented)
- Web search (SearXNG) targets — if web search enabled

Client will likely run this via an egress proxy; configure `HTTPS_PROXY` / `NO_PROXY` env vars on the deployment if so (not currently wired into our Helm chart — would need a values override).

### 6.3 Intra-cluster networking
- We rely on `gradient-gateway` + `reranker` in a `shared-services` namespace. Either:
  - Intermax deploys `gradient-gateway` Helm chart separately, **or**
  - They host these services elsewhere and we override the URLs.
- `NetworkPolicy` is `enabled: true` by default (`values.yaml:710`); tune `extraEgressNamespaces` if client uses non-default namespaces.
- `CiliumNetworkPolicy` (`values.yaml:724`) — enable if cluster uses Cilium Gateway API.

### 6.4 Secrets management
Chart supports two modes:
- **Plain Kubernetes Secrets** — client ships values via `values.secrets.*` keys.
- **ExternalSecrets Operator → 1Password** (our default) — requires ESO installed on cluster + a 1Password service account token as `onepassword-sa-token` secret. Client likely doesn't use 1Password → they'll go with plain secrets or their own vault (HashiCorp Vault, Azure Key Vault via ESO).

Either way the list of secrets they need to populate (Kubernetes Secret names):
```
openwebui-secrets:
  WEBUI_SECRET_KEY
  POSTGRES_PASSWORD
  OPENAI_API_KEY
  RAG_OPENAI_API_KEY
  MICROSOFT_CLIENT_SECRET              # App A
  EMAIL_GRAPH_CLIENT_SECRET            # App B
  # Optional:
  GOOGLE_CLIENT_SECRET                 # if Google Drive too
  GOOGLE_DRIVE_API_KEY
  EXTERNAL_DOCUMENT_LOADER_API_KEY
  RAG_EXTERNAL_RERANKER_API_KEY
  S3_ACCESS_KEY_ID                     # if using object storage
  S3_SECRET_ACCESS_KEY
```

### 6.5 Persistence + backups
- PostgreSQL PVC (10 GiB default, `values.yaml:559`) — client to provision storage class + backup tool (Velero, pg_dump cron, etc.).
- Weaviate PVC (20 GiB default, `values.yaml:589`) — *do not treat as ephemeral*; re-embedding everything is slow.
- Open WebUI data PVC (10 GiB default, `values.yaml:88`) — session/secret key material if not using externalised secrets.
- Weaviate backup module is enabled by default (`BACKUP_FILESYSTEM_PATH=/var/lib/weaviate/backups`) — client needs to add a sidecar or CronJob that pushes these to object storage.

### 6.6 Observability
OpenTelemetry wired to an in-cluster Alloy collector by default:
```
ENABLE_OTEL                    = false    # off by default — enable if client has LGTM stack
OTEL_EXPORTER_OTLP_ENDPOINT    = http://alloy.observability.svc:4317
OTEL_SERVICE_NAME              = open-webui
LOG_FORMAT                     = json     # we do emit structured logs
```
Ask Intermax whether they want traces/metrics/logs pushed anywhere.

### 6.7 Multi-replica concerns
Default chart is single-replica (RWO PVC, `Recreate` strategy). For HA, client needs:
- `persistence.enabled = false` (move state to external Postgres/Weaviate)
- `redis.enabled = true` (for WebSocket/session sharing)
- `strategy.type = RollingUpdate`
- `replicaCount > 1` + `podDisruptionBudget.enabled = true` + topology spread constraints

Single-replica is almost always fine for a small on-prem tenant. Flag multi-replica only if the client expects enterprise availability SLOs.

### 6.8 Proxy / outbound through client proxy
If client routes all outbound through a corporate proxy, we need to set `HTTPS_PROXY`, `HTTP_PROXY`, `NO_PROXY` env vars at the pod level. Chart doesn't natively expose these — Intermax will need to either add them via a values override (`extraEnv`) or we patch the chart.

### 6.9 Time zone + locale
```
TZ                            = Europe/Amsterdam
DEFAULT_LOCALE                = nl-NL
```
Useful for retention job schedules (daily cleanup) and log readability.

---

## Code References

SSO/Entra ID:
- `backend/open_webui/config.py:381-425` — Microsoft-specific OAuth config
- `backend/open_webui/config.py:451-659` — generic OIDC + role/group mapping
- `backend/open_webui/utils/oauth.py:1372-1693` — callback flow
- `backend/open_webui/routers/auths.py:759-843` — logout flow
- `backend/open_webui/config.py:550-562` — SCIM

OneDrive/SharePoint:
- `backend/open_webui/config.py:2842-2883` — OneDrive config
- `backend/open_webui/services/onedrive/auth.py:31` — OAuth scope string
- `backend/open_webui/services/onedrive/graph_client.py` — Graph API calls
- `backend/open_webui/services/onedrive/token_refresh.py:26` — refresh flow
- `backend/open_webui/routers/onedrive_sync.py:203` — callback URL

Email invites:
- `backend/open_webui/config.py:2889-2910`
- `backend/open_webui/services/email/auth.py:31` — `.default` scope
- `backend/open_webui/services/email/graph_mail_client.py:31` — `sendMail` endpoint

Google Drive (if also enabled):
- `backend/open_webui/config.py:2931-2965`
- `backend/open_webui/services/google_drive/` (reference impl)

Integration Provider framework (for Confluence/TOPdesk push path):
- `backend/open_webui/routers/integrations.py:1-771`
- `backend/open_webui/config.py:3320-3324` — `INTEGRATION_PROVIDERS`
- `src/lib/components/admin/Settings/IntegrationProviders.svelte`

Agent API:
- `backend/open_webui/env.py:794-797` — internal routing
- `backend/open_webui/config.py:2975+` — agent proxy

Helm chart (source of truth for on-prem wiring):
- `helm/open-webui-tenant/values.yaml`
- `helm/open-webui-tenant/templates/open-webui/configmap.yaml`
- `helm/open-webui-tenant/templates/open-webui/deployment.yaml`
- `helm/open-webui-tenant/templates/secrets.yaml`

Existing guidance:
- `collab/docs/external-integration-cookbook.md` — pull-sync provider recipe (for Confluence path B)
- `collab/notes.md:49-52` — external pipeline provider notes
- `collab/index.md` 26-03-2026 — Agent Proxy — external API for soev.ai agents

## Architecture Insights

- **Entra ID app registration strategy**: SSO + OneDrive *can* coexist in one app (delegated flow + user consent). Email invites **cannot** — `Mail.Send` is an application permission requiring admin consent and client-credentials flow. Separate app = mandatory.
- **OneDrive vs Google Drive vs Confluence/TOPdesk**: we have two distinct integration architectures. Per-user delegated OAuth (OneDrive, GDrive) stores a refresh token per user and syncs *their* files. Service-account (email, would-be Confluence/TOPdesk) shares one credential across all users. The user's question conflated these — worth clarifying in the handoff.
- **Extension points**: push-ingest framework (integration providers) is the cheapest way to add any source, sacrificing UX polish. The sync abstraction is the richer path but assumes OAuth + per-user auth.
- **Helm chart architecture**: tenant chart (this repo) ships Postgres + Weaviate in-cluster by default. Shared services (`gradient-gateway`, `reranker`) live in `shared-services` namespace and must be deployed separately — Intermax needs to know this isn't self-contained.
- **Secrets plumbing**: chart supports both plain K8s Secrets and ExternalSecrets → 1Password. Almost certainly this client uses neither — set `externalSecrets.enabled: false` and ship plain secrets, or integrate ESO with their vault.

## Historical Context (from thoughts/ and collab/)

- `collab/index.md` 2026-03-20 — "Gradient-DS Custom Features Overview" documents the 9 custom features on top of upstream; all shown in §2 here.
- `collab/docs/external-integration-cookbook.md` — step-by-step recipe for new pull-sync providers; Confluence path B would follow this.
- `collab/index.md` 2026-03-26 — Agent Proxy work; relevant for §2.13.
- `collab/index.md` 2026-03-31 — DPIA compliance (data export + retention); relevant for §2.10.
- `thoughts/shared/research/2026-02-04-gke-to-previder-migration.md` — prior migration to Previder; on-prem infra patterns overlap with what Intermax will need.
- `thoughts/shared/research/2026-03-26-airweave-adoption-feasibility.md` — discusses Airweave as a potential universal connector layer (Confluence, Jira, etc. would "come for free"); could be a future alternative to building Confluence ourselves.

## Related Research

- `thoughts/shared/research/2026-02-04-gke-to-previder-migration.md`
- `thoughts/shared/research/2026-03-26-airweave-adoption-feasibility.md`
- `thoughts/shared/research/2026-03-13-airweave-vs-custom-integration-comparison.md`

## Open Questions

1. **Confluence: push vs pull?** Needs product decision before quoting Intermax. Push is zero-eng for us (Intermax writes the poller); pull is a feature in our product that any tenant gets.
2. **TOPdesk: scope of ingestion?** Incidents only, or also change requests / knowledge items / asset data? Affects privacy review and DPA terms.
3. **Does client already use an ExternalSecrets-compatible vault (Vault/Azure KV/AWS SM)?** Affects secrets handoff mechanism.
4. **LLM provider location?** On-prem Azure OpenAI, our hosted endpoint, or something else? Affects `OPENAI_API_BASE_URL` and egress rules.
5. **Shared services (gateway + reranker) deployment model?** Are we deploying `gradient-gateway` Helm chart into their cluster, or providing external endpoints?
6. **Multi-tenancy?** Is this one tenant or will Intermax host multiple organisations on the same cluster? Affects `WEBUI_NAME`, tenant routing, Postgres separation.
