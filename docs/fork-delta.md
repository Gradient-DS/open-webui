# Fork delta — Gradient open-webui vs. upstream

**Scope.** Seeded 2026-08-11 at the NEO pilot wrap-up. This is *not* the complete
delta: the fork is hundreds of commits ahead of upstream and a full reconstruction
was never attempted. What is captured here is (a) the major standing divergences
that a reader must know before touching this repo, and (b) the NEO-era changes on
`merge/neo-to-dev`. The file grows opportunistically — when you touch a divergence
that has no entry, add one; when you change a gate, fix the entry. Per the
Gradient doc strategy (soev-docs repo, `doc-strategy.md`), this is the OWUI
fork's special-case doc: every divergence from upstream + why.

**Convention.** Gradient code is marked `[Gradient]` in comments throughout the
tree; `grep -rn '\[Gradient\]'` is the mechanical index this file summarizes.
Each entry states what diverges, why, how it is gated, and the key files.

---

## Agent API integration (chat routed to an external agent service)

Chat completions bypass OWUI's built-in web search / RAG / LLM orchestration and
are routed to the soev agent service instead — the agent is a complete
orchestrator that executes its own tools server-side. Why: OWUI is the UI and
auth boundary; retrieval and agentic control flow belong in soev-agents, not in
a fork of a chat frontend.

Gated by `AGENT_API_ENABLED` (`backend/open_webui/env.py:1007`, default `False`;
Helm `openWebui.config.agentApiEnabled`, default `"false"`). The routing decision
itself is a pure function isolated from upstream code in
`backend/open_webui/utils/agent_routing.py` (`resolve_agent_route`,
`agent_owns_tool_execution`) so the delta stays one-liners at the call sites in
`main.py`. The client — payload build, SSE streaming, error redaction — is
`backend/open_webui/utils/agent.py` (`build_agent_payload`, `call_agent_api`,
`stream_agent_response`). Routing matrix: bound `chat.meta.agent_id` → agent
route; no agent id + picker off + `AGENT_API_ENABLED` → agent route (legacy
bypass); otherwise the stock route.

D-Runtime keeps upstream's sub-agents, tool approval, ask-user, timers, and
automations out of agent-routed turns. The agent still owns tool execution and
the post-tool continuation loop. The model editor hides the subagents builtin
unconditionally because `/api/config` exposes no subagents feature flag; memory
capabilities and tools use upstream's `enable_memories` flag.

## Agent proxy (inbound API surface, separate from the above)

`backend/open_webui/routers/agent_proxy.py`, mounted at `/api/v1/agent`
(`main.py:1066`), reverse-proxies the agent service's OpenAI-compatible API
(`GET /models`, `POST /chat/completions`, `GET /openapi.json`) to callers holding
an **OWUI API key**. Why: the agent service has no auth of its own — OWUI owns
auth, the agent service owns inference. It also resolves knowledge-base
collection refs before forwarding (`_resolve_collection_refs`).

Gated separately from the chat integration by the persisted config key
`agent_proxy.enable` (Helm `enableAgentProxy`, default `"false"`). Do not
conflate the two flags: `agentApiEnabled` is outbound (OWUI → agent),
`enableAgentProxy` is inbound (client → OWUI → agent).

## Agent picker (replaces the model selector)

The v0.11.3 merge moves the picker from the navbar into the composer's right
slot (Q7). `agentPickerActive` is computed in `Chat.svelte` / `Placeholder.svelte`
and passed to `MessageInput.svelte`: `FEATURE_AGENT_PICKER` and
`feature_agent_api_enabled` must be on, and the chat must be new or agent-bound
(`!chat?.id || chat?.meta?.agent_id`). The slot renders `AgentSelector` when
active, otherwise `ModelSelector` whenever there is anything to pick. Why: picker
tenants choose an assistant; non-picker tenants using the legacy agent bypass
still need their model selector. `agentRouted` does not control this slot.

The selector's list excludes assistants (models carrying `info.base_model_id`)
unless one is currently selected, so the trigger can still name an assistant-bound
chat. Assistants are reached from the sidebar pins (`visiblePinnedAgents`) and the
AI-assistants workspace instead; the workspace is therefore the only pin surface
for them. The list is not gated on a minimum model count: on a single-model tenant
the selector still carries model info, set-as-default and the pin toggle.

The navbar keeps upstream's title block; the new-chat greeting remains suppressed
under the picker. Both flags default off (`FEATURE_AGENT_PICKER` / Helm
`featureAgentPicker`, `AGENT_API_ENABLED` / Helm `agentApiEnabled`). The same pair
swaps the admin tabs `external-agents` and `agents` through
`src/lib/utils/features.ts:isAdminSettingsTabEnabled`.

## Two-menu composer and retired input pins

The fork previously combined attachments and capability switches in one
`InputMenu`, with a user-pinned `pinnedInputItems` rail. Q6 adopts upstream's
two-menu toolbar: `MessageInput/InputMenu.svelte` hosts attachments and
`MessageInput/IntegrationsMenu.svelte` hosts capability switches; active
capabilities appear as echo chips. Why: follow upstream's maintained composer
structure while retaining Document Writer, strict data separation, the interview
chat's `restrictTo` allowlist, and web-search/image-generation mutual exclusion.
`FEATURE_INPUT_MENU` gates both menus. Pin buttons and the pinned input rail are
retired; `Settings.pinnedInputItems` remains optional, inert stored data.

The picker-only model-inheritance override is also retired. `Chat.svelte` uses
upstream's one-shot `sessionStorage.selectedModels` read/removal before falling
back to user settings and admin defaults; it was never a session-long default.

## Chat attachments and loading

`MessageInput.svelte` hides per-file retrieval controls with `edit={!agentRouted}`.
Its callers now pass `isAgentRouted($pendingAgentId)`; the bound-id argument is
narrowed to the pending selection, rather than also resolving saved-chat metadata
there. The helper still covers the legacy bypass when the agent API is enabled
and the picker is off. This UI hint does not replace backend routing from
`chat.meta.agent_id`. Why: retrieval on an agent turn is owned by the agent.

GRA-184 keeps `skipUpload` conditional on both temporary-chat mode and the agent
API flag: temporary agent chats still upload real attachments. GRA-222 keeps
the `'url'` attachment type in `Chat.svelte`, the globe icon in `FileItem.svelte`,
and the `FEATURE_WEBPAGE_URL`-gated Webpage URL entry in `InputMenu.svelte`.
URL-keyed removal avoids treating distinct URLs as the same unnamed file.
`openWebpageModal` remains exported by InputMenu but has no caller after the
pinned rail's retirement; the menu itself opens the modal.

`Chat.svelte:loadChat` remains abortable and returns `'aborted'`, `'loaded'`, or
`'not_found'`. Why: a stale load must not overwrite the newly selected chat or
trigger the not-found redirect (the #163 chat-state leak fix). Unconditional.

Temporary chats minted by the fork keep `local:<sid>:<uuid>` (GRA-221), giving
each chat a separate agent ledger. Upstream's `temporary:<sid>` is still accepted
for compatibility. `src/lib/utils/index.ts:temporaryChatId`,
`src/lib/utils/chatId.ts`, and `backend/open_webui/utils/chat_id.py` agree on the
two prefixes; backend session recovery takes only the first segment after the
prefix, dropping the UUID. The old backend `chat_ids.py` is retired.

`Commands/Knowledge.svelte` (`#`) now searches knowledge bases only; KB file
search remains in `Commands/AtCommands.svelte` (`@`). This follows upstream's
command split; the knowledge feature gate remains in effect.

## Weaviate multi-tenancy connector

The sole **Weaviate** connector in the fork (upstream's other backends —
chroma, milvus, qdrant, pgvector, … — remain in `retrieval/vector/factory.py`
untouched but unused by soev tenants). Every OWUI logical collection maps into
one of five fixed schema collections (`Knowledge`, `File`, `WebSearch`,
`UserMemory`, `HashBased`) as a native Weaviate **tenant** — one shard and a
dedicated vector index per tenant — plus a standalone non-MT meta collection
`Knowledge_bases`. Why: per-class collections did not scale across tenants; the
legacy per-class connector and its dual-read shim were removed after the
fleet-wide MT migration completed 2026-07.

Because the schema is fixed, every retrievable key must be a declared property:
`_mt_properties()` is the canonical list and `_ensure_declared_properties()`
backfills it onto collections created before a property existed (`source_url`,
`chunk_index`, `bboxes`). `chunk_index` — the position in the final post-split
doc list — is stamped on every ingest so a full document can be reconstructed
from its chunks. Files:
`backend/open_webui/retrieval/vector/dbs/weaviate_multitenancy.py`,
`backend/open_webui/retrieval/vector/dbs/_weaviate_mt_mapping.py`,
`backend/open_webui/routers/retrieval.py` (~1679–1694, the `chunk_index` stamp).
Unconditional for Weaviate tenants; `ENABLE_WEAVIATE_BQ_QUANTIZATION` gates BQ on
the flat collections only.

Q5 ports upstream's `$eq` / `$in` metadata-filter contract into
`weaviate_multitenancy.py` for search, query, and deletion. Why: the builtin
knowledge tool scopes metadata search to accessible KB ids; dropping that filter
would cross ACL boundaries. Unsupported conditions and null values fail closed,
including `hash=None`, instead of issuing an unfiltered query or deletion.
`knowledge_base_id` and `hash` are declared and added to existing schemas, but
schema backfill does not populate old objects: this upgrade requires one-time
re-embed of KB metadata for the builtin knowledge tool. Existing objects need
their `knowledge_base_id` / `hash` values backfilled; until then ACL-filtered
search safely omits metadata without those values.

## Retrieval config carve-out

`backend/open_webui/routers/retrieval.py` retains `RAG_CONFIG_KEYS` and
`get_rag_config_state()` returning a `SimpleNamespace`, plus the trailing optional
`config` argument used by ingestion callers. Q3 adds these 16 missing keys and
matching form fields: `CONTENT_EXTRACTION_SUPPORTED_MEDIA_MIME_TYPES`,
`OPENSERP_BASE_URL`, `TIKA_SERVER_VERSION`, `ENABLE_WEB_SEARCH_CONFIRMATION`,
`WEB_SEARCH_CONFIRMATION_CONTENT`, `EXTERNAL_DOCUMENT_LOADER_HEADERS`,
`LINKUP_API_KEY`, `LINKUP_SEARCH_PARAMS`, `MICROSOFT_WEB_IQ_API_BASE_URL`,
`MICROSOFT_WEB_IQ_API_KEY`, `MICROSOFT_WEB_IQ_LANGUAGE`, `MINERU_FILE_EXTENSIONS`,
`MISTRAL_OCR_USE_BASE64`, `RAG_TOKENIZER_MODEL`, `SERPHOUSE_API_KEY`, and
`SERPHOUSE_DOMAIN`. Why: keep the fork's ingestion contract while restoring the
config surface consumed by upstream additions. Convergence onto upstream's
`RetrievalConfig` is the first post-merge follow-up.

The splitter adopts `TIKTOKEN_DISALLOWED_SPECIAL=()`: text resembling a special
token is treated as ordinary text instead of raising during tokenization.
`RAG_TOKENIZER_MODEL` is persisted and returned through config APIs but remains
inert for splitting; the fork does not adopt upstream's transformers-tokenizer
helpers. Q4 keeps disk reload and single-collection ingestion, without restoring
`file-{id}` reuse or upstream's stored-text vector repair path.

Upstream's `ENABLE_KNOWLEDGE_FILE_RETENTION` is separate from the fork's retention
worker and soft-delete policy. The KB convergence ledger still excludes file
rename, content editing, per-file ellipsis menus, unlink-only deletion,
pending-files polling, and always-on AccessControl.

## Citation bbox highlighting

Chunks ingested through warren's doc pipeline may carry `metadata.bboxes` — a
list of `{page?, x0, y0, x1, y1}` rects in PDF points, top-left origin, 0-based
page. These flow ingest → Weaviate (`bboxes`, a TEXT property holding the JSON
string, because the MT schema is fixed) → citation viewer, which paints exact
rects over the PDF. Why: text matching against the PDF layer is approximate;
geometry from the parser is not.

Files: `backend/open_webui/routers/integrations.py` (~467–480, serializes
`bboxes` on ingest), the `bboxes` property in the Weaviate MT schema,
`src/lib/utils/citationRects.ts` (framework-free parser, unit-tested),
`src/lib/components/chat/Messages/Citations/CitationModal.svelte` →
`src/lib/components/common/PDFViewer.svelte` (rect overlay). Commit `dcebcd312`.
Unconditional when the metadata is present — exact rects beat text matching. The
separate approximate text-match highlighter is Helm
`enableCitationTextHighlight`, default `"false"`.

## Document Writer — rendering from output items

Documents produced by the Document Writer are derived from the message's
**output items** (`open_webui:document`), not from message content. Why: content
is a single text stream, so a document emitted mid-turn was fragile to
reconstruct and could be clobbered by subsequent deltas; output items are
addressable and survive interim prose.

`src/lib/components/chat/Messages/structuredOutput.ts` pre-serializes an
`open_webui:document` item into the `<details type="document">` block the
existing Markdown path (`Markdown/DocumentCard.svelte`) already renders;
`StructuredOutputRenderer.svelte` consumes the result. Backend emission:
`backend/open_webui/utils/middleware.py` (`serialize_output()` and the streaming
branches, ~4066–4230). Commit `0d200a8bf`. Feature itself: Helm
`enableDocumentWriter` (default `"true"`).

Q2 keeps `tag_output_handler('document', DEFAULT_DOCUMENT_WRITER_TAGS, …)` before
upstream's Responses API delta block. At tag boundaries it emits corrected
`response.output_item.added` / `response.output_item.done` items before the new
tail's delta, removing already-streamed partial tags. `structuredOutput.ts`
keeps document `markdown` aligned with text deltas. Agent responses pass through
the same response handler but use `write_document` tool-call markers rendered as
`DocumentCard`; agent routes skip the Document Writer prompt injection and do
not emit `<document>` tags.

`ChatControls.svelte` retains the fork's Document tab in both the desktop
resizable panel and mobile drawer, driven by `documentContents`, `showDocument`,
and `openDocumentTabSignal`. Why: documents remain accessible while prose and
tool output continue. The Document Writer feature gate controls the surface;
the desktop panel starts at 600px and then respects the saved width.

Word download is gated separately by `ENABLE_DOCX_EXPORT`
(`backend/open_webui/config.py:2912`, default `True`; Helm `enableDocxExport`).
It hides every `.docx` button in the UI *and* returns 403 from `/utils/chat/docx`
and `/utils/document/docx` (`backend/open_webui/routers/utils.py:143,192`) — the
switch is enforced server-side, not just visually.

## Feedback report → Slack / notification router

An in-app feedback form posts a structured event out of the tenant. Why: pilot
tenants needed a zero-friction path from "this answer was wrong" to the team,
without granting anyone access to the tenant's chat DB.

Gated by `ENABLE_FEEDBACK_REPORTING` (`config.py:2809` area, default `False`).
Two mutually exclusive sinks, not a fallback chain:
`FEEDBACK_REPORT_WEBHOOK_URL` (in-cluster notification router — a URL, not a
secret, so it lives in the ConfigMap) takes precedence, and when it is set the
Slack card is skipped entirely; otherwise `FEEDBACK_REPORT_SLACK_WEBHOOK_URL`
(a secret) receives a Slack card. `FEEDBACK_REPORT_INCLUDE_USER_IDENTITY`
(default `True`) and `FEEDBACK_REPORT_TRACE_URL_TEMPLATE` shape the payload.
Files: `backend/open_webui/routers/feedback_report.py`,
`backend/open_webui/utils/feedback_report.py`,
`src/lib/components/feedback/FeedbackModal.svelte`, `src/lib/apis/feedback/`.

## Sidebar pin pill in the simple assistant editor

A "Keep in Sidebar" / "Hide from Sidebar" toggle inside the simple assistant
editor, writing per-user `settings.pinnedModels` with the same handler shape as
the workspace list's `pinModelHandler`. Why: upstream's only pin surface is the
model selector, which the agent picker hides — without this there is no way to
pin an assistant in picker tenants.

Files: `src/lib/components/workspace/Models/SimpleModelEditor.svelte` (~65–81,
~320–330). Commit `1f61ab3b0`. Visible only where the simple builder is used,
which is gated by `FEATURE_SIMPLE_ASSISTANT_BUILDER`
(`backend/open_webui/config.py:2894`, default `False`; Helm
`featureSimpleAssistantBuilder`). Note the flag is **off by default**: although
`isFeatureEnabled()` treats an *absent* key as enabled, `main.py` always emits
`feature_simple_assistant_builder` in the authenticated config, so the absent-key
default never applies to this feature. The editor additionally yields to the
advanced editor when `?advanced` is present in the URL.

## Model profiles, hosting, and pinning

The selector's `ModelItem.svelte` keeps two-line rows for the model name,
best-for text, profile meters, hosting information, and data warnings. Why:
tenants need an understandable model choice and visibility into where data is
processed. `MODEL_PROFILES`, `MODEL_HOSTING`, `FEATURE_MODEL_METERS`, and
`ENABLE_DATA_WARNINGS` control the metadata and warnings. `Selector.svelte`
keeps 56px virtual rows, a 28rem panel capped by the viewport, and a slim
right-aligned `ModelProfileLegend` at the top of the list (Q9). Upstream's avatar
and its LICENSE notice remain inside the two-line row (Q8).

`ModelSelector/ModelItemMenu.svelte` and `workspace/Models/ModelMenu.svelte`
use the Q19 pin gate `base_model_id || $pinnedModels.includes(id)`: custom
assistants can be pinned, and already-pinned base models can be unpinned.
The derived `pinnedModels` store honors admin `default_pinned_models` until the
user sets their own list. `visiblePinnedAgents` keeps the sidebar agent-only.
The simple editor's pin pill now reads the same derived store.

## User data export

`chat/Settings/DataControls.svelte` retains the asynchronous GDPR export flow
through `$lib/apis/export` and `backend/open_webui/routers/export.py`. Users
request an archive, receive progress, and download it when ready. Why: data
access includes the fork's user data beyond upstream's chat JSON export.
`ENABLE_DATA_EXPORT` gates the flow; `DATA_EXPORT_RETENTION_HOURS` controls
archive retention.

## Admin settings in the settings modal

Q13 adopts `chat/SettingsModal.svelte` as the host and retires
`admin/Settings.svelte`. The six fork tabs are `cloud-sync`, `email`, `security`,
`acceptance`, `external-agents`, and `agents`, with their existing save behavior.
`isAdminSettingsEnabled` / `isAdminSettingsTabEnabled` filter discovery, search,
and deep links; old settings routes redirect through those gates. Why: keep
tenant feature restrictions while following upstream's unified settings host.

The separate `authentication` tab stays hidden (Q14); its settings remain
inlined in General using `AdminSettingSection` / `Row` / `Field`. The upstream
`subagents` tab stays hidden under D-Runtime. Analytics is available to admins
based only on `enable_admin_analytics`, independently of `FEATURE_ADMIN_SETTINGS`
and `FEATURE_ADMIN_SETTINGS_TABS`; it is not constrained by the fork tab allowlist.

---

## CI / image channels

`.github/workflows/docker-build-soev.yaml` builds and pushes the slim image
(linux/amd64 + linux/arm64, to GHCR and optionally Harbor) on pushes to `main`,
`dev`, `test` and on `v*` / `chart-v*` tags. Tags produced per build: the branch
name (`:dev`, `:test`, `:main`), `:git-<sha>`, `v<semver>` for semver tags,
`:latest` on `main` only, and `:<branch>-<sha>-<run_number>` — the last is the
one automation actually tracks, because its run number is monotonic. Chart-only
`chart-v*` tags skip the image build so they cannot overwrite what prod pulls.

`.github/workflows/branch-guard.yaml` enforces the promotion chain: a PR into
`test` must come from `dev`, and a PR into `main` must come from `test`. Any
other source branch fails the check.

Rollout is Flux in `soev-gitops` (`tenants/<provider>/_shared/image-policy-*.yaml`):
dev tenants track `^dev-<sha>-<run>$` numerically ascending, test tenants track
`^test-<sha>-<run>$` the same way, and **prod tenants track the semver policy —
`^v<major>.<minor>.<patch>$`, range `>=1.0.0`** — i.e. prod advances only on an
explicit release tag. `:main` and `:latest` are published by CI but no prod
ImagePolicy follows them.
