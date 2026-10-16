# Deployed configuration snapshot

Source: Gradient-DS/soev-gitops, read 2026-09-08.
Legacy agent settings were removed for the v2-only release on 2026-10-11.
The remaining historical snapshot must be re-synced against tenant config;
platform credentials and URLs are specified in docs/agent-api-deployment.md.
- fleet defaults: tenants/base/values.yaml
- representative tenant: tenants/previder-prod/gradient/helmrelease.yaml
- chart defaults: open-webui/helm/open-webui-tenant/values.yaml
- env mapping: open-webui/helm/open-webui-tenant/templates/open-webui/configmap.yaml

Every production tenant sets agentApiEnabled=true.

## Flags that decide which request paths exist

| Helm value | Env var | Chart default | Deployed | Notes |
|---|---|---|---|---|
| agentApiEnabled | AGENT_API_ENABLED | "false" | "true" | gates the whole agent block in the configmap |
| agentApiAgents | AGENT_API_AGENTS | "" | soev_chat_manual,assistant_onboarding | |
| enableCodeExecution | ENABLE_CODE_EXECUTION | "false" | "true" | Jupyter sink |
| enableCodeInterpreter | ENABLE_CODE_INTERPRETER | "false" | "true" | Jupyter sink |
| codeExecutionEngine (unset) | CODE_EXECUTION_ENGINE | pyodide (app default) | pyodide | set by no tenant and no chart value, so production runs the app default (`config.py:402`); a tenant setting `jupyter` activates a server-side HTTP+WebSocket sink and will show here as drift |
| enableFeedbackReporting | ENABLE_FEEDBACK_REPORTING | "False" | "True" | the unguarded webhook sink |
| enableWebSearch | ENABLE_WEB_SEARCH | "true" | "true" | |
| webSearchEngine | WEB_SEARCH_ENGINE | "searxng" | "searxng" | shared-services searxng |
| contentExtractionEngine | CONTENT_EXTRACTION_ENGINE | "external" | "external" | external document loader |
| webLoaderEngine | WEB_LOADER_ENGINE | "external" | "external" | |
| ragEmbeddingEngine | RAG_EMBEDDING_ENGINE | — | "openai" | via litellm-proxy |
| ragOpenaiApiBaseUrl | RAG_OPENAI_API_BASE_URL | — | http://litellm-proxy.shared-services.svc:4000/v1 | |
| ragExternalRerankerUrl | RAG_EXTERNAL_RERANKER_URL | — | http://gradient-reranker.shared-services.svc:8000/v1/rerank | |
| openaiApiBaseUrl | OPENAI_API_BASE_URL | https://router.huggingface.co/v1 | http://litellm-proxy.shared-services.svc:4000/v1 | chart default is a REAL external host |
| storageProvider | STORAGE_PROVIDER | — | "s3" | parity: CI selects the same S3 storage provider for uploads and downloads |
| s3EndpointUrl | S3_ENDPOINT_URL | "" | https://object.previder.nl | CI replaces the production endpoint with the in-network S3 gateway |
| s3BucketName | S3_BUCKET_NAME | "" | <tenant>-uploads | CI uses a disposable bucket |
| enableOnedriveIntegration | — | — | "true" | Graph egress |
| enableGoogleDriveIntegration/Sync | — | — | "true" | Google egress |
| enableImageGeneration | ENABLE_IMAGE_GENERATION | — | "true" | via litellm-proxy |
| imageGenerationEngine | IMAGE_GENERATION_ENGINE | — | "openai" | |
| enableEmailInvites | — | — | "true" | MS Graph mail |
| enableOauthSignup | ENABLE_OAUTH_SIGNUP | — | "true" | Microsoft IdP |
| enable2fa | ENABLE_2FA | "false" | "true" | TOTP routes; also soev-max, soev-test, staging (soev-gitops 105c694) |
| require2fa | REQUIRE_2FA | "false" | "true" | enrolment enforced at sign-in; also soev-test, staging |
| twoFaGracePeriodDays | TWO_FA_GRACE_PERIOD_DAYS | "7" | "7" | a password account without TOTP keeps a capped session until the deadline |
| bypassAdminAccessControl | BYPASS_ADMIN_ACCESS_CONTROL | "false" | "false" | admins follow access control |
| enableWeaviateMultitenancyMode | ENABLE_WEAVIATE_MULTITENANCY_MODE | "false" | "true" | |
| telemetry.otel.enabled | ENABLE_OTEL | — | true (alloy.observability.svc:4317) | |

## CI divergences and coverage limits

The table above is the source snapshot, not an assertion of full integration
coverage. `TestCiMatchesDeployedConfig` checks every row, including the three
Helm rows whose env names are supplied by the chart configmap. Boolean feature
flags must match except telemetry and the explicitly absent private soev services.
CI runs no soev server.

| Setting | CI value | Reason / coverage limit |
|---|---|---|
| AGENT_API_ENABLED | absent | Private soev services and stand-ins never run here; platform-backed routes have soev-solutions waivers. |
| OPENAI_API_BASE_URL, RAG_OPENAI_API_BASE_URL | http://stub:8000/v1 | Replace the production LiteLLM hostname with the sealed stub. |
| RAG_EXTERNAL_RERANKER_URL | http://stub:8000/v1/rerank | Replace the production reranker hostname with the sealed stub. |
| S3_ENDPOINT_URL | http://s3:9000 | The endpoint hostname remains a divergence: the in-network S3 gateway replaces object.previder.nl. CI drives the same S3 provider code path, but does not cover production TLS or Previder-specific behavior. |
| S3_BUCKET_NAME | ci-uploads | Disposable bucket replaces the tenant bucket; the gateway creates it before it listens and its health check verifies it before the app starts. |
| ENABLE_OTEL | empty | No Alloy OTLP collector exists in the sealed stack. Disable telemetry exports; they do not decide application request coverage. |
| Upstream API keys, database password, signing key, S3 credentials | disposable CI values | Production secrets are neither available nor needed by the sealed stack. |
| OAuth / OneDrive / Google Drive / Graph mail credentials and stored tokens | empty | Provider authentication uses public Microsoft/Google endpoints, including hard-coded URLs. No real provider credentials or tokens are available. Integration, Google sync, email invite and OAuth signup flags remain enabled, but successful cloud authentication, sync and mail delivery are not covered. |
| CODE_EXECUTION_ENGINE, CODE_INTERPRETER_ENGINE | pyodide | **Not a divergence — this is parity.** Verified 2026-09-08: neither soev-gitops nor the chart sets an engine, so production runs the application default `pyodide` (`config.py:402,424`). Pyodide executes in the browser, so the server-side Jupyter sink in `utils/code_interpreter.py` is dormant across the whole fleet, not merely uncovered here. If any tenant ever sets `jupyter`, the snapshot row below turns this into drift the parity test will catch. |
| External loader, search, pipeline and webhook URLs | http://stub:8000 with caller-specific paths | Production destination details are not supplied in the snapshot; route all configured outbound services to the sealed stub. |
| RAG_RERANKING_ENGINE, RAG_RERANKING_MODEL | external, ci-reranker | The snapshot specifies the external reranker URL but omits engine/model. Select the external engine to exercise it without downloading a model. The stub returns deterministic scores. |

Caller contracts verified in this checkout:

- S3 storage uses `http://s3:9000`, disposable credentials, region `us-east-1`
  and `S3_ADDRESSING_STYLE=path`. The gateway joins only `internal` and
  publishes no ports. It is [Versity's S3 gateway](https://github.com/versity/versitygw)
  on its posix backend, pinned by digest, storing objects in ephemeral `/data`;
  a bucket is a directory under that root. The service creates the bucket
  directory under `sh -e` and only then execs the gateway, and its health check
  requires both the `/health` endpoint and the bucket directory, so a listening
  server alone cannot release app startup. The gateway verifies SigV4
  signatures against its root key, which the app's credentials must match.
  Recreate the stack together for each fresh run so the bucket is created again
  after ephemeral data is discarded.
- External document processing sends raw bytes to `PUT /process` and expects
  `page_content` / `metadata` documents. External web loading posts URL batches
  to `/extract` and expects the same document shape.
- SearXNG requests `/search?format=json` and expects a `results` envelope with
  `url`, `title`, `content` and `score`. The legacy external search branch keeps
  its bare list response when that query parameter is absent.
- External reranking posts to `/v1/rerank` and reads indexed relevance scores.
- `AGENT_API_AGENTS` matches the deployed list; agent routing is disabled and
  no private-service URL is configured.
- `GET /api/v1/users/{user_id}/shared-data` is an admin-only, read-only
  offboarding inventory of another user's shared KB and assistant IDs and kinds;
  it returns no content and grants no ordinary-user sharing access. It uses the
  disposable user seed and has no route-coverage waiver. With soev-api absent,
  only local shared assistants can appear; the remote KB inventory is not
  exercised by the sealed stack.
- `DELETE /api/v1/users/{user_id}` remains admin-only and destructive. Its
  `mode=delete|transfer|archive` query selects resource disposition, `transfer_to`
  selects a transfer target, and in delete mode repeated `confirm_shared_deletion`
  values must exactly match the shared-resource IDs. The scalar query pass
  derives `mode` and `transfer_to` alongside `archive_reason`; confirmation is
  an array of strings and is outside that derivation.
- Legacy sync-daemon, loader-worker, document-job and `PIPELINE_API_*` settings
  have no callers in this checkout and are removed. Pipelines management uses
  the configured OpenAI connections (`routers/pipelines.py`).

The snapshot is mounted read-only at `/app/security` so the same drift test can
run inside the application container. Docker startup and the runtime egress
proof must be rerun by the reviewer; local tests only use loopback HTTP.
