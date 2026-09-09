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

## Model request bodies for the security derivation

Phase 4a declares permissive request shapes for 25 model-invoking operations in
`backend/open_webui/main.py`, `backend/open_webui/routers/tasks.py`,
`backend/open_webui/routers/ollama.py`, and `backend/open_webui/routers/openai.py`.
Their signatures use dependencies in the fork-owned
`backend/open_webui/services/model_request_bodies.py`. Each dependency accepts a
typed body parameter and returns the cached raw JSON, preserving extras, omitted
fields, and internal dictionary callers without changing handler logic.

The models expose message text, multimodal content, tool descriptions and calls,
task prompts and responses, completion prompts, and embedding inputs to the
fork's OpenAPI security gate. All declared fields are optional and all models
allow extras. Arbitrary vendor keys and tool JSON schemas remain accepted but
cannot be enumerated by the derivation. This integration is fork-local because
upstream does not consume the fork's derived attack surface; no upstream PR has
been opened. The changes are unconditional on these routes.

Vendor payload controls passed on all 25 original handlers before typing and
again afterwards. Arrays, strings, numbers, booleans, and null already returned
pre-handler 422 responses on all 25 original `dict` parameters; those controls
remain unchanged. The generated surface grows from 205 routes / 810 strings to
230 routes / 2525 strings. Exactly 25 derivation waivers are removed, leaving
27 JSON write bodies for Phase 4b, one JSON read body, and seven multipart bodies.

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

An agent selector claims the model-selector slot in the navbar for new or
agent-bound chats, and the new-chat empty state drops the model-identity
greeting. Why: pilot tenants pick an *assistant*, not a model — exposing raw
model ids and a favicon greeting was noise.

Both flags must be on: `FEATURE_AGENT_PICKER` (`env.py:1049`, default `False`;
Helm `featureAgentPicker`, default `"false"`) **and** `AGENT_API_ENABLED`. Key
files: `src/lib/components/chat/Navbar.svelte` (the `agentSelectorActive`
predicate — picker on, agent API on, and `!chat?.id || chat?.meta?.agent_id`),
`src/lib/components/chat/AgentSelector.svelte`,
`src/lib/components/chat/Placeholder.svelte` (`agentPickerEnabled` suppresses the
greeting). The same pair also swaps the admin tab: `external-agents` hides and
`agents` appears (`src/lib/utils/features.ts:isAdminSettingsTabEnabled`).
Non-picker tenants see stock upstream UI.

## New-chat model inheritance, disabled under the picker

Upstream carries the previous chat's model selection into new chats via
`sessionStorage.selectedModels`. Under the picker the model selector is hidden,
so an explicitly chosen assistant (pinned sidebar entry, `?model=` link) became
silently sticky for the whole session with no visible way back. Picker
deployments therefore resolve user settings, then admin defaults, and skip the
sessionStorage read.

Gated on the same picker predicate, evaluated inline in `initNewChat`:
`src/lib/components/chat/Chat.svelte` (~line 1620, commit `0ddee4a61`). Tenants
without the picker keep upstream behavior — the selector is visible, so
stickiness is escapable and expected. The key is still *written* on selection;
`ChatItem` reads it to label the active chat's model.

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
