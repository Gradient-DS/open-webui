### Facts & References

<!-- Tier 2 — searched on demand. Never guess these — look them up. Update world/index.md when this file changes. -->

#### Document processing / ingestion architecture (open-webui)

- **Parsing offload is ON by chart default**: `CONTENT_EXTRACTION_ENGINE=external` + `EXTERNAL_DOCUMENT_LOADER_URL` defaults (via the chart's `sharedServices.gatewayUrl` helper) to `http://gradient-gateway.<shared-services-ns>.svc:8000`, which proxies `/process` → `gradient-doc-processor:8001`. Verified live on gradient. So direct uploads already offload parsing — they do NOT parse in the OWUI pod.
- **Two knobs, don't confuse them**: `EXTERNAL_DOCUMENT_LOADER_URL` = parsing offload (`PUT /process`, bytes→text, **upstream**, in use — KEEP). `EXTERNAL_PIPELINE_URL` = chunking offload (`/chunk`, text→chunks, **soev-custom, removed 04-06-2026 PR #148**).
- **gradient-doc-processor** (shared-services, NetworkPolicy-gated by the `uses-shared-services` label, no API key): `PUT /process` (parse→`[{page_content,metadata}]`, OWUI-`ExternalDocumentLoader`-compatible), `POST /chunk` (text→chunks, OWUI-`EXTERNAL_PIPELINE`-compatible), `POST /process-document` (parse+chunk, used by the loader-worker). Single shared Deployment per cluster, ~4 CPU-bound parse slots/pod, HPA off by chart default (prod runs 2–8). Shared bottleneck across tenants.
- The loader-worker job queue adds **~2–4s polling latency** (worker lease poll 2s + OWUI status poll 2s) — wrong for interactive single-file uploads. Right arch: **one parser engine, two orchestration modes** — sync (`gradient-doc-processor /process`) for interactive, async queue (warren) for bulk/cloud sync.
- **Warren** (distributed doc-processing, genai-utils `document_processing/distributed/`; was `warren/`, now `framework/`+`pipeline/`): RabbitMQ job framework, requires a file **LOCATION** (local-path or `gs://` only — no S3/http resolver wired yet), NOT raw bytes; parses+chunks+**embeds** internally + optional Weaviate write (vs OWUI doing embedding today). Cutover is its own location-based integration; loader-worker job plumbing does not transfer.
- All tenants run `storageProvider: s3`; every uploaded file is already in S3 — direct uploads AND cloud-synced files (the latter via `/ingest` `original_files` → `Storage.upload_file`, default on through the loader-worker's `ingest_ship_original_bytes`).
