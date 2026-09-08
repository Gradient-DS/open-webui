# Deployed configuration snapshot

Source: Gradient-DS/soev-gitops, read 2026-09-08.
This snapshot must be re-synced when tenant config changes.
- fleet defaults: tenants/base/values.yaml
- representative tenant: tenants/previder-prod/gradient/helmrelease.yaml
- chart defaults: open-webui/helm/open-webui-tenant/values.yaml
- env mapping: open-webui/helm/open-webui-tenant/templates/open-webui/configmap.yaml

Every production tenant (demo, gradient, haagsebeek, datafryslan, staging, kwink,
haute-equipe, afk, kompanen, gemeente-zuidplas, bzk-ministerie) sets
agentApiEnabled=true and enableAgentProxy=true.

## Flags that decide which request paths exist

| Helm value | Env var | Chart default | Deployed | Notes |
|---|---|---|---|---|
| agentApiEnabled | AGENT_API_ENABLED | "false" | "true" | gates the whole agent block in the configmap |
| agentApiBaseUrl | AGENT_API_BASE_URL | "" | http://gradient-agent-agents-api:8080 | in-cluster |
| agentApiAgents | AGENT_API_AGENTS | "" | soev_chat_manual,assistant_onboarding | |
| agentApiPickerDefaultSlug | AGENT_API_PICKER_DEFAULT_SLUG | — | soev_chat_manual | |
| featureAgentPicker | FEATURE_AGENT_PICKER | "false" | "false" | |
| enableAgentProxy | ENABLE_AGENT_PROXY | "false" | "true" | external-facing agent API |
| enableCodeExecution | ENABLE_CODE_EXECUTION | "false" | "true" | Jupyter sink |
| enableCodeInterpreter | ENABLE_CODE_INTERPRETER | "false" | "true" | Jupyter sink |
| enableFeedbackReporting | ENABLE_FEEDBACK_REPORTING | "False" | "True" | the unguarded webhook sink |
| enableWebSearch | ENABLE_WEB_SEARCH | "true" | "true" | |
| webSearchEngine | WEB_SEARCH_ENGINE | "searxng" | "searxng" | shared-services searxng |
| contentExtractionEngine | CONTENT_EXTRACTION_ENGINE | "external" | "external" | external document loader |
| webLoaderEngine | WEB_LOADER_ENGINE | "external" | "external" | |
| ragEmbeddingEngine | RAG_EMBEDDING_ENGINE | — | "openai" | via litellm-proxy |
| ragOpenaiApiBaseUrl | RAG_OPENAI_API_BASE_URL | — | http://litellm-proxy.shared-services.svc:4000/v1 | |
| ragExternalRerankerUrl | RAG_EXTERNAL_RERANKER_URL | — | http://gradient-reranker.shared-services.svc:8000/v1/rerank | |
| openaiApiBaseUrl | OPENAI_API_BASE_URL | https://router.huggingface.co/v1 | http://litellm-proxy.shared-services.svc:4000/v1 | chart default is a REAL external host |
| storageProvider | STORAGE_PROVIDER | — | "s3" | S3 endpoint egress |
| enableOnedriveIntegration | — | — | "true" | Graph egress |
| enableGoogleDriveIntegration/Sync | — | — | "true" | Google egress |
| enableImageGeneration | ENABLE_IMAGE_GENERATION | — | "true" | via litellm-proxy |
| imageGenerationEngine | IMAGE_GENERATION_ENGINE | — | "openai" | |
| enableEmailInvites | — | — | "true" | MS Graph mail |
| enableOauthSignup | ENABLE_OAUTH_SIGNUP | — | "true" | Microsoft IdP |
| bypassAdminAccessControl | BYPASS_ADMIN_ACCESS_CONTROL | "false" | "false" | admins follow access control |
| enableWeaviateMultitenancyMode | ENABLE_WEAVIATE_MULTITENANCY_MODE | "false" | "true" | |
| telemetry.otel.enabled | ENABLE_OTEL | — | true (alloy.observability.svc:4317) | |
| agentSearch.enabled | AGENT_SEARCH_ENABLED | — | true (doc-processor) | |

## CI divergences and coverage limits

The table above is the source snapshot, not an assertion of full integration
coverage. `TestCiMatchesDeployedConfig` checks every row, including the three
Helm rows whose env names are supplied by the chart configmap. Boolean feature
flags cannot be waived; telemetry is the sole boolean exception.

| Setting | CI value | Reason / coverage limit |
|---|---|---|
| AGENT_API_BASE_URL | http://stub:8000 | Replace the production agents-api hostname with the sealed stub. |
| OPENAI_API_BASE_URL, RAG_OPENAI_API_BASE_URL | http://stub:8000/v1 | Replace the production LiteLLM hostname with the sealed stub. |
| RAG_EXTERNAL_RERANKER_URL | http://stub:8000/v1/rerank | Replace the production reranker hostname with the sealed stub. |
| STORAGE_PROVIDER | local | The existing sealed stack has no S3 service. Disposable local storage supports file handling, but S3 signing, multipart transfers and presigning are not covered. |
| ENABLE_OTEL | empty | No Alloy OTLP collector exists in the sealed stack. Disable telemetry exports; they do not decide application request coverage. |
| Upstream API keys, database password, signing key | disposable CI values | Production secrets are neither available nor needed by the stub. |
| OAuth / OneDrive / Google Drive / Graph mail credentials and stored tokens | empty | Provider authentication uses public Microsoft/Google endpoints, including hard-coded URLs. No real provider credentials or tokens are available. Integration, Google sync, email invite and OAuth signup flags remain enabled, but successful cloud authentication, sync and mail delivery are not covered. |
| CODE_EXECUTION_ENGINE, CODE_INTERPRETER_ENGINE / Jupyter URLs | pyodide / empty | The snapshot supplies enabled flags and a Jupyter note but no deployed engine, URL or authentication settings. Keep both flags enabled and explicitly select the application's default engine; Jupyter HTTP/WebSocket execution is not covered. Re-sync these missing settings before claiming Jupyter coverage. |
| External loader, search, pipeline and webhook URLs | http://stub:8000 with caller-specific paths | Production destination details are not supplied in the snapshot; route all configured outbound services to the sealed stub. |
| RAG_RERANKING_ENGINE, RAG_RERANKING_MODEL | external, ci-reranker | The snapshot specifies the external reranker URL but omits engine/model. Select the external engine to exercise it without downloading a model. The stub returns deterministic scores. |

Caller contracts verified in this checkout:

- Agent proxy uses `/v1/models`, `/v1/chat/completions` (JSON or SSE),
  `/v1/gradient_agent_meta` and `/openapi.json`. Metadata exposes
  `config.welcome_message`, as consumed by the chat UI.
- External document processing sends raw bytes to `PUT /process` and expects
  `page_content` / `metadata` documents. External web loading posts URL batches
  to `/extract` and expects the same document shape.
- SearXNG requests `/search?format=json` and expects a `results` envelope with
  `url`, `title`, `content` and `score`. The legacy external search branch keeps
  its bare list response when that query parameter is absent.
- External reranking posts to `/v1/rerank` and reads indexed relevance scores.
- `AGENT_SEARCH_ENABLED` gates inbound `/api/v1/internal/retrieval/*` routes in
  this checkout; it is not an outbound doc-processor URL. The document pipeline
  client separately uses `POST /jobs` and `GET /jobs/{id}`. The stub supplies job
  IDs and terminal status but does not perform asynchronous ingestion callbacks;
  the snapshot does not specify distributed-pipeline feature flags.

The snapshot is mounted read-only at `/app/security` so the same drift test can
run inside the application container. Docker startup and the runtime egress
proof must be rerun by the reviewer; local tests only use loopback HTTP.
