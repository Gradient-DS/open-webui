"""
Agent API client for OpenWebUI integration.

[Gradient] This module connects OpenWebUI to an external agent service that
replaces built-in web search, RAG, and LLM orchestration.

Architecture (two layers, designed for later extraction):

  Layer 1 — Transport client (no OpenWebUI dependency):
    - AgentPayload: dataclass defining the agent API request schema
    - build_agent_payload(): constructs the payload from raw inputs
    - stream_agent_response(): async generator yielding parsed SSE events
    These can be extracted into the agent API's own Python package.

  Layer 2 — OpenWebUI integration glue:
    - call_agent_api(): extracts OpenWebUI metadata, calls transport layer,
      routes custom SSE events to Socket.IO, returns StreamingResponse

SSE protocol from agent:
    Custom events (routed to Socket.IO, not passed to process_chat_response):
        event: status
        data: {"description": "Searching...", "done": false}

        event: source
        data: {"name": "doc.pdf", "url": "..."}

        event: present_ui
        data: {"name": "choice", "props": {...}}

        event: context_usage
        data: {"tokens_used": int, "tokens_budget": int, "fraction": float}

    Standard OpenAI chunks (passed through to process_chat_response):
        data: {"choices": [{"delta": {"content": "..."}}]}

    End of stream:
        data: [DONE]
"""

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from typing import Any

import aiohttp
from open_webui.config import ENABLE_SKILL_EXECUTION, FEATURE_SKILL_FILES
from open_webui.env import AGENT_API_BASE_URL, AGENT_API_KEY
from open_webui.models.chats import Chats
from open_webui.models.config import Config
from open_webui.socket.main import get_event_emitter
from open_webui.utils.auth import create_token
from starlette.responses import StreamingResponse

log = logging.getLogger(__name__)


# ============================================================================
# Layer 1 — Transport client (no OpenWebUI dependency beyond env config)
# ============================================================================


@dataclass
class AgentPayload:
    """Request schema for the agent API's chat completions endpoint.

    The ``agent`` field is optional — when omitted, the agent service
    uses its configured ``default_agent``. The ``model`` field carries
    the user-selected LLM and is validated server-side against the
    service's allowlist.
    """

    model: str
    messages: list[dict[str, Any]]
    agent: str | None = None
    stream: bool = True
    chat_id: str | None = None
    user_id: str | None = None
    message_id: str | None = None
    # [Gradient] Parent of ``message_id`` in the chat's branching tree
    # (the user message that prompted this response). The agent service
    # uses this to rewind its persisted thread state on retry/regenerate,
    # so re-runs replay from the pre-answer point and tools fire again
    # instead of the model recapping cached output. Omitted on the first
    # turn of a new chat (no parent exists).
    parent_message_id: str | None = None
    session_id: str | None = None
    features: dict[str, Any] = field(default_factory=dict)
    files: list[dict[str, Any]] | None = None
    knowledge: list[dict[str, Any]] | None = None
    tool_ids: list[str] | None = None
    rag_filter: dict[str, Any] | None = None
    # Operator-supplied system prompt from the custom-model definition
    # (model.params.system). Variables are pre-substituted upstream so the
    # agent can use the value as-is.
    system_prompt: str | None = None
    # [Gradient] Conversation-level system prompt — the merged per-chat /
    # Chat Controls / folder prompt. Distinct from ``system_prompt`` (the
    # custom-model prompt). Forwarded so the agent composes it into its
    # system prompt; OpenWebUI also still inlines it into ``messages``.
    chat_system_prompt: str | None = None
    # [Gradient] Resolved Open WebUI skills for this turn. Each entry is
    # {name, description, content, is_selected}. User-selected skills
    # carry full content for the agent to render; model-attached skills
    # form a manifest the agent expands on demand via a tool.
    # Skills with bundled markdown files also carry an optional
    # ``files: [{filename, content}, ...]`` list (present only when
    # non-empty) so the agent can render a <bundled_files> manifest and
    # serve file content via its ``read_skill_file`` tool.
    skills: list[dict[str, Any]] | None = None
    # [Gradient] Generic metadata forwarded as-is to the agent service.
    # Today used for ``user_language`` (UI locale, BCP-47 like "nl-NL")
    # so the agent can resolve the response language. Open-ended so we
    # can extend it without changing the contract.
    metadata: dict[str, Any] | None = None
    # Model params forwarded directly
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    frequency_penalty: float | None = None
    presence_penalty: float | None = None
    seed: int | None = None
    stop: list[str] | None = None


def _maybe_attach_fetch_token(skill_entry: dict[str, Any], user_id: str) -> dict[str, Any]:
    """Return skill_entry with a fetch_token added iff it has binary files.

    The token is scoped to {user_id, skill_id, purpose: "skill_file_read"} with
    a 120-second TTL.  It is accepted ONLY by the raw-bytes route for the
    matching skill_id; all other routes reject it.
    """
    skill_id = skill_entry.get('id')
    if not skill_id:
        return skill_entry
    files = skill_entry.get('files') or []
    has_binary = any(f.get('is_binary') for f in files)
    if not has_binary:
        return skill_entry
    token = create_token(
        data={'id': user_id, 'skill_id': skill_id, 'purpose': 'skill_file_read'},
        expires_delta=timedelta(seconds=120),
    )
    return {**skill_entry, 'fetch_token': token}


def build_agent_payload(
    *,
    model: str,
    messages: list[dict[str, Any]],
    agent: str | None = None,
    stream: bool = True,
    chat_id: str | None = None,
    user_id: str | None = None,
    message_id: str | None = None,
    parent_message_id: str | None = None,
    session_id: str | None = None,
    features: dict[str, Any] | None = None,
    files: list[dict[str, Any]] | None = None,
    knowledge: list[dict[str, Any]] | None = None,
    tool_ids: list[str] | None = None,
    rag_filter: dict[str, Any] | None = None,
    system_prompt: str | None = None,
    chat_system_prompt: str | None = None,
    skills: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
    **model_params,
) -> dict[str, Any]:
    """Build a JSON-serialisable payload for the agent API.

    Constructs an AgentPayload and converts it to a dict, stripping None
    values so the agent only sees fields that are actually set.
    """
    payload = AgentPayload(
        model=model,
        messages=messages,
        agent=agent,
        stream=stream,
        chat_id=chat_id,
        user_id=user_id,
        message_id=message_id,
        parent_message_id=parent_message_id,
        session_id=session_id,
        features=features or {},
        files=files,
        knowledge=knowledge,
        tool_ids=tool_ids,
        rag_filter=rag_filter,
        system_prompt=system_prompt,
        chat_system_prompt=chat_system_prompt,
        skills=skills,
        metadata=metadata,
        **{k: v for k, v in model_params.items() if v is not None},
    )
    result = {k: v for k, v in asdict(payload).items() if v is not None}

    # [Gradient] Phase 8b: mint a short-lived, skill-scoped token for each
    # forwarded skill that carries binary files.  The agent uses it to fetch
    # binary asset bytes from GET /api/v1/skills/id/{skill_id}/files/content
    # with Authorization: Bearer <fetch_token>.  Only minted when
    # ENABLE_SKILL_EXECUTION is on (full execution mode), FEATURE_SKILL_FILES is
    # on, the skill has a known id, the request has a user_id, and at least one
    # file in the bundle is binary (is_binary=True).
    # Text-only skills and skills without ids never get a token.
    # With ENABLE_SKILL_EXECUTION off (simple-skills mode), no token is ever minted.
    if ENABLE_SKILL_EXECUTION and FEATURE_SKILL_FILES and user_id and result.get('skills'):
        result['skills'] = [_maybe_attach_fetch_token(skill_entry, user_id) for skill_entry in result['skills']]

    return result


def _agent_api_headers() -> dict[str, str]:
    """Build outbound headers for requests to the agent API.

    Always sends ``Content-Type: application/json``. When ``AGENT_API_KEY``
    is configured, adds the ``X-API-Key`` header so the agent service
    (which enforces auth on all ``/v1/*`` routes) accepts the request.
    """
    headers: dict[str, str] = {'Content-Type': 'application/json'}
    if AGENT_API_KEY:
        headers['X-API-Key'] = AGENT_API_KEY
    return headers


def _resolve_model_vision_capable(model: dict[str, Any] | None) -> bool:
    """Resolve whether a model can process image input.

    Reads OpenWebUI's per-model vision capability flag
    (``info.meta.capabilities.vision``, set via the admin panel).
    Defaults to ``True`` when unset — matching OpenWebUI's own frontend
    default — so a model without explicit capability config is treated
    as vision-capable.
    """
    info = (model or {}).get('info') or {}
    capabilities = (info.get('meta') or {}).get('capabilities') or {}
    return bool(capabilities.get('vision', True))


def _resolve_model_citations_enabled(model: dict[str, Any] | None) -> bool:
    """Resolve whether inline source citations are enabled for a model.

    Reads OpenWebUI's per-model citations capability flag
    (``info.meta.capabilities.citations``, the "Show sources" toggle in
    the admin panel). Defaults to ``True`` when unset — matching
    OpenWebUI's own frontend default — so a model without explicit
    capability config keeps citing. Forwarded to the agent so it can
    suppress citation pills for assistants configured citation-free.
    """
    info = (model or {}).get('info') or {}
    capabilities = (info.get('meta') or {}).get('capabilities') or {}
    return bool(capabilities.get('citations', True))


def _error_sse_chunk(message: str) -> str:
    """Build an SSE data line carrying an error in OpenAI shape.

    Open WebUI renders the inline error banner only for a chunk with an
    ``error`` object and no ``choices`` (see middleware
    ``stream_body_handler``); error text placed in ``choices[].delta``
    is shown as ordinary assistant content instead.
    """
    return f'data: {json.dumps({"error": {"message": message}})}\n\n'


@dataclass
class SSEEvent:
    """A parsed SSE event from the agent stream."""

    event_type: str  # "data" for standard OpenAI chunks, or custom event name
    data: Any  # parsed JSON or raw string


async def stream_agent_response(
    base_url: str,
    payload: dict[str, Any],
    timeout: int = 300,
) -> AsyncIterator[SSEEvent]:
    """POST to the agent API and yield parsed SSE events.

    This is a pure transport function — it knows nothing about OpenWebUI
    internals. It yields SSEEvent objects that the caller can dispatch.

    The agent returns a standard SSE stream where:
    - Lines starting with "event:" set the event type for the next data line
    - Lines starting with "data:" carry the payload
    - Custom events (status, source) have an explicit event type
    - Standard OpenAI chunks have no event type (defaults to "data")
    """
    # aiohttp caps a single readline() at 2 * read_bufsize (default 128 KiB).
    # Agent tool deltas can ship a whole HTML artifact in one `delta.content`.
    # Bump the buffer so the consumer tolerates lines well into the megabytes.
    session = aiohttp.ClientSession(
        trust_env=True,
        timeout=aiohttp.ClientTimeout(total=timeout),
        read_bufsize=8 * 1024 * 1024,
    )

    try:
        response = await session.request(
            method='POST',
            url=f'{base_url}/v1/chat/completions',
            data=json.dumps(payload),
            headers=_agent_api_headers(),
        )

        if response.status >= 400:
            body = await response.text()
            await session.close()
            raise Exception(f'Agent API returned {response.status}: {body}')

        current_event_type = 'data'

        async for raw_line in response.content:
            line = raw_line.decode('utf-8').rstrip('\n\r')

            if not line:
                continue

            if line.startswith('event:'):
                current_event_type = line[len('event:') :].strip()
                continue

            if line.startswith('data:'):
                data_str = line[len('data:') :].strip()

                if data_str == '[DONE]':
                    yield SSEEvent(event_type='done', data='[DONE]')
                    break

                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    data = data_str

                yield SSEEvent(event_type=current_event_type, data=data)
                # Reset event type after yielding — next data line defaults
                # to "data" unless preceded by a new event: line
                current_event_type = 'data'

    finally:
        await session.close()


# ============================================================================
# Layer 2 — OpenWebUI integration glue
# ============================================================================


async def call_agent_api(
    request,
    form_data: dict[str, Any],
    metadata: dict[str, Any],
    features: dict[str, Any],
    override_agent: str | None = None,
):
    """Route a chat completion to the external agent API.

    [Gradient] Called from main.py either when ``AGENT_API_ENABLED`` is the
    global bypass OR when a chat is bound to an agent via ``chat.meta.agent_id``.
    When ``override_agent`` is provided it takes precedence over the global
    ``AGENT_API_SELECTED_AGENT`` admin setting. Extracts fields from
    OpenWebUI's form_data/metadata, calls the transport layer, routes custom
    SSE events to Socket.IO, and returns either a StreamingResponse or a dict
    for process_chat_response to consume.
    """
    stream = form_data.get('stream', True)

    # Extract model params that should be forwarded
    model_params = {}
    for key in (
        'temperature',
        'top_p',
        'max_tokens',
        'frequency_penalty',
        'presence_penalty',
        'seed',
        'stop',
    ):
        if key in form_data:
            model_params[key] = form_data[key]

    # [Gradient] Resolve to the underlying LLM ID. The agents service
    # validates ``model`` against its LLM allowlist; OpenWebUI custom-model
    # IDs (e.g. "offertemachine") aren't LLMs and aren't in the allowlist.
    # The base model (e.g. "gpt-oss-120b") is. ``metadata["model"]["info"]``
    # carries the persisted custom_model dump for custom models; absent for
    # pure base models, arena models, and direct mode — those fall back to
    # the form_data model, which is already the LLM ID in those cases.
    model_dict = metadata.get('model') or {}
    info = model_dict.get('info') or {}
    llm_model = info.get('base_model_id') or form_data.get('model', '')

    # [Gradient] Per-chat override (chat.meta.agent_id) wins over the global
    # AGENT_API_SELECTED_AGENT admin setting. When neither is set we omit
    # ``agent`` from the payload and the agents service uses its own
    # ``default_agent``.
    selected_agent = override_agent or await Config.get('agent_api.selected_agent') or None

    # [Gradient] Forward the turn anchor so the agent service can rewind its
    # persisted thread state on retry/regenerate. The agents side forks its
    # checkpoint on the payload's ``parent_message_id`` field, which it defines
    # as "the user-message id this assistant turn replies to" (= the assistant
    # message's parent). That is ``user_message_id`` here — NOT
    # ``metadata['parent_message_id']`` (which OWUI sets to the *user* message's
    # parent: null on a new chat's first turn, so the rewind never fired and
    # regenerate replayed the prior turn's accumulated tool state). The anchor
    # must be stable across regenerations and present on turn 1; user_message_id
    # is both. Without it the agent's thread store leaks the prior assistant
    # turn into context and tools don't re-fire on re-runs.

    # [Gradient] Build agent-side metadata from the OWUI metadata dict.
    # user_language carries the frontend UI locale (BCP-47, e.g. "nl-NL")
    # forwarded from Chat.svelte so the agent resolves the response language.
    agent_metadata: dict[str, Any] = {}
    user_language = metadata.get('user_language')
    if user_language:
        agent_metadata['user_language'] = user_language

    # [Gradient] Forward the selected model's vision capability so the
    # agent service can drop images for a misconfigured non-vision
    # model instead of crashing on multimodal content.
    agent_metadata['vision_capable'] = _resolve_model_vision_capable(model_dict)

    # [Gradient] Forward the selected model's citations capability ("Show
    # sources" toggle) as a feature flag so the agent service can suppress
    # inline [N] source pills for assistants configured citation-free
    # (e.g. the Offertemachine, whose offer document must not carry
    # citations). Model capability is the source of truth, so it wins over
    # any inbound features value.
    features = {**(features or {}), 'citations': _resolve_model_citations_enabled(model_dict)}

    payload = build_agent_payload(
        model=llm_model,
        agent=selected_agent,
        messages=form_data.get('messages', []),
        stream=stream,
        chat_id=metadata.get('chat_id'),
        user_id=metadata.get('user_id'),
        message_id=metadata.get('message_id'),
        parent_message_id=metadata.get('user_message_id'),
        session_id=metadata.get('session_id'),
        features=features,
        files=metadata.get('files'),
        knowledge=metadata.get('knowledge'),
        tool_ids=metadata.get('tool_ids'),
        rag_filter=metadata.get('rag_filter'),
        system_prompt=metadata.get('system_prompt'),
        chat_system_prompt=metadata.get('chat_system_prompt'),
        skills=metadata.get('skills'),
        metadata=agent_metadata or None,
        **model_params,
    )

    log.debug(f'Agent API payload: model={payload.get("model")}, stream={stream}, features={features}')

    if not stream:
        return await _call_agent_api_non_streaming(payload)

    return _build_streaming_response(request, payload, metadata)


async def _call_agent_api_non_streaming(
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Handle non-streaming agent API call. Returns a dict response."""
    session = aiohttp.ClientSession(
        trust_env=True,
        timeout=aiohttp.ClientTimeout(total=300),
    )
    try:
        response = await session.request(
            method='POST',
            url=f'{AGENT_API_BASE_URL}/v1/chat/completions',
            data=json.dumps(payload),
            headers=_agent_api_headers(),
        )

        if response.status >= 400:
            body = await response.text()
            raise Exception(f'Agent API returned {response.status}: {body}')

        return await response.json()
    finally:
        await session.close()


def _build_streaming_response(
    request,
    payload: dict[str, Any],
    metadata: dict[str, Any],
) -> StreamingResponse:
    """Build a StreamingResponse that streams from the agent API.

    Custom SSE events (status, source) are routed to Socket.IO via
    get_event_emitter. Standard OpenAI data lines are passed through
    to the response body for process_chat_response to consume.
    """

    async def body_generator():
        # [Gradient] Source events are emitted individually via Socket.IO
        # as they arrive, so citation chips render while the answer streams.
        # get_event_emitter is async (Phase 1.5 upstream); await inside the
        # generator since the enclosing _build_streaming_response is sync.
        event_emitter = await get_event_emitter(metadata)
        # [Gradient] Accumulate every ``event: subagent`` payload so the
        # message's persisted ``subagents`` field carries the full lifecycle
        # for rehydration on reload. The frontend's ``reduceSubAgents``
        # consumes this same flat list, so persisting verbatim avoids any
        # FE/BE shape divergence. Empty for non-bezwaar agents.
        subagent_events: list[dict] = []
        # [Gradient] Latest post-turn context-budget estimate. Persisted onto
        # the message on `done` so the banner rehydrates on reload (mirrors
        # subagents). Latest wins across multi-iteration turns.
        last_context_usage: dict | None = None
        try:
            async for sse_event in stream_agent_response(AGENT_API_BASE_URL, payload):
                if sse_event.event_type == 'done':
                    # [Gradient] Persist accumulated per-turn message state in a
                    # single upsert: the subagent lifecycle and the latest
                    # context-budget estimate. Both rehydrate the message on
                    # reload. Upsert merges into the existing message, so writing
                    # them together never clobbers either field.
                    updates: dict[str, Any] = {}
                    if subagent_events:
                        updates['subagents'] = subagent_events
                    if last_context_usage is not None:
                        updates['contextUsage'] = last_context_usage
                    if updates:
                        chat_id = metadata.get('chat_id')
                        message_id = metadata.get('message_id')
                        if chat_id and message_id:
                            try:
                                await Chats.upsert_message_to_chat_by_id_and_message_id(
                                    chat_id,
                                    message_id,
                                    updates,
                                )
                            except Exception as e:
                                log.warning(f'Error persisting message updates: {e}')
                    break

                if sse_event.event_type == 'status':
                    # [Gradient] Route status events to Socket.IO so the UI
                    # shows status spinners in real time.
                    if event_emitter:
                        try:
                            await event_emitter(
                                {
                                    'type': 'status',
                                    'data': sse_event.data,
                                }
                            )
                        except Exception as e:
                            log.warning(f'Error emitting status event: {e}')
                    continue

                if sse_event.event_type == 'source':
                    # [Gradient] Emit each source immediately so citation
                    # chips render while the answer is still streaming.
                    if event_emitter:
                        try:
                            await event_emitter(
                                {
                                    'type': 'source',
                                    'data': sse_event.data,
                                }
                            )
                        except Exception as e:
                            log.warning(f'Error emitting source event: {e}')
                    continue

                if sse_event.event_type == 'present_ui':
                    # [Gradient] Generative-UI directive — route to the
                    # frontend over Socket.IO so the message-level
                    # component dispatcher can render it. Payload shape:
                    # {"name": "<component>", "props": {...}}.
                    if event_emitter:
                        try:
                            await event_emitter(
                                {
                                    'type': 'present_ui',
                                    'data': sse_event.data,
                                }
                            )
                        except Exception as e:
                            log.warning(f'Error emitting present_ui event: {e}')
                    continue

                if sse_event.event_type == 'subagent':
                    # [Gradient] SubAgent lifecycle / streaming events for the
                    # Leiden bezwaar agent (and any future multi-SubAgent flow).
                    # Payload is the typed event from the agent backend with a
                    # ``phase`` discriminator: start / token / reasoning /
                    # status / source / step / done.
                    # The frontend's <SubAgentGroup> reducer keys cards by
                    # parallel_group_id and agent_id; per-token streams append
                    # to the matching card's text_buffer.
                    subagent_events.append(sse_event.data)
                    if event_emitter:
                        try:
                            await event_emitter(
                                {
                                    'type': 'subagent',
                                    'data': sse_event.data,
                                }
                            )
                        except Exception as e:
                            log.warning(f'Error emitting subagent event: {e}')
                    continue

                if sse_event.event_type == 'context_usage':
                    # [Gradient] Post-turn context-budget estimate from the
                    # agent service. Payload shape:
                    # {"tokens_used": int, "tokens_budget": int, "fraction": float}.
                    # The frontend renders a banner above the chat input when
                    # fraction crosses a threshold. Retain the latest payload so
                    # it persists onto the message on `done` (banner rehydration).
                    last_context_usage = sse_event.data
                    if event_emitter:
                        try:
                            await event_emitter(
                                {
                                    'type': 'context_usage',
                                    'data': sse_event.data,
                                }
                            )
                        except Exception as e:
                            log.warning(f'Error emitting context_usage event: {e}')
                    continue

                if sse_event.event_type == 'panel_filter':
                    # [Gradient] Per-message citation-panel scope. Payload
                    # shape: {"ns": [int, ...]} naming the cumulative source
                    # ids that should appear in the chip list for THIS
                    # message. The backend keeps dispatching `source` events
                    # cumulatively so inline `[N]` tokens resolve via the
                    # dense-array lookup across cross-turn cites; this event
                    # prevents the rendered chip list from accumulating.
                    if event_emitter:
                        try:
                            await event_emitter(
                                {
                                    'type': 'panel_filter',
                                    'data': sse_event.data,
                                }
                            )
                        except Exception as e:
                            log.warning(f'Error emitting panel_filter event: {e}')
                    continue

                # Standard OpenAI chunk — pass through as SSE data line
                if isinstance(sse_event.data, dict):
                    yield f'data: {json.dumps(sse_event.data)}\n\n'
                else:
                    yield f'data: {sse_event.data}\n\n'

        except Exception as e:
            log.error(f'Agent API streaming error: {e}')
            # Emit an OpenAI-shape error object (no `choices`) so the UI
            # renders a proper error banner instead of inline text.
            yield _error_sse_chunk(f'Agent API error: {e}')

        yield 'data: [DONE]\n\n'

    return StreamingResponse(
        body_generator(),
        media_type='text/event-stream',
    )
