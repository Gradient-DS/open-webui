# Agent API Retry/Regenerate Handoff via `parent_message_id`

## Overview

When a user hits Retry/Regenerate on an assistant message in a chat routed to
the Gradient agent service, the agent does not re-execute tools, the answer is
a recap of the previous one, and citations are missing. Open WebUI sends the
correct payload (the old assistant turn is excluded from `messages`), but the
agent service is stateful per `chat_id`: it reconstructs the prior assistant
turn and tool results from its own thread store, so the model believes it has
already answered and short-circuits.

This plan forwards a new `parent_message_id` field in the agent API payload so
the agent service can rewind its thread state to the point before that
message — turning every retry into a true replay from the user-question state.

The coordinated change in the agent service repo (acting on
`parent_message_id`) is out of scope for this plan but is documented in the
"Coordinated Agent Service Change" section below so the two sides ship
together.

## Current State Analysis

### What is sent today

- **Frontend** (`src/lib/components/chat/Chat.svelte:2725-2761`):
  `regenerateResponse` creates a new (empty) assistant message whose `parentId`
  is the original user message. It then calls `sendMessage` which walks the
  parent chain from the new empty assistant via `createMessagesList`
  (`src/lib/utils/index.ts:1263-1277`). The walked chain contains only the
  parent's ancestors plus the new (empty) message, so the previous assistant
  message — a sibling of the new one — is correctly excluded.
- **Frontend wire payload** (`Chat.svelte:2504-2546`):
  Sends `messages` (filtered, empty content stripped at line 2447) plus
  metadata fields including `id` (new assistant id) and `parent_id`
  (= user message id).
- **Backend metadata mapping** (`backend/open_webui/main.py:2199-2227`):
  `parent_message_id` is popped from `form_data['parent_id']` and stored in
  `metadata['parent_message_id']`. The value is already available on every
  request — including retries (= the original user message id) and first
  turns (= `None`).
- **Backend agent dispatch** (`backend/open_webui/utils/middleware.py:2517-2519`,
  `backend/open_webui/utils/agent.py:227-300`): When `route_to_agent` is true,
  `call_agent_api` builds the payload via `build_agent_payload` and POSTs to
  `${AGENT_API_BASE_URL}/v1/chat/completions`. It forwards `chat_id`,
  `user_id`, `message_id`, `session_id`, `features`, `files`, `knowledge`,
  `tool_ids`, `rag_filter`, `system_prompt` — but **not** `parent_message_id`.
- **Agent service** (separate repo): receives the request with `chat_id` and
  uses its own thread/checkpoint store to reconstruct prior conversation
  state. Has no signal that this request is a retry/regenerate of an earlier
  branch.

### Why this is broken on retry

The model's monologue in the reproducer (chat with Nemotron, KB "CKWL",
question "Wat betaal ik aan mijn zakelijke rekening") literally reads:

> *"We have answered the question earlier? The user repeated 'Wat betaal ik
> aan mijn zakelijke rekening'. We already gave answer with recent invoice.
> [...] We have data for June 2025: €25,39."*

The model can recall the prior tool-derived numbers verbatim, which means the
prior assistant turn (with tool messages) is in its context. Since OWUI's
payload does not contain it, the only source is the agent service's stored
thread state. The model sees an apparent duplicate user question, decides
"already answered", does not invoke tools, and produces a recap. With no tool
calls in this turn, the agent emits no `type: source` SSE events
(`utils/agent.py:364-377`), so the new assistant message's `sources` array is
empty → citations are missing.

### Key Discoveries

- `AgentPayload` is a dataclass in `backend/open_webui/utils/agent.py:54-88`.
  `build_agent_payload` (line 91-131) converts it to a dict via `asdict()` and
  strips `None` values — so adding an optional field is fully additive: absent
  when `None`, present otherwise.
- `parent_message_id` is already threaded into `metadata` for every request
  (`main.py:2204`); other call sites use it for DB upserts
  (`main.py:2360, 2393`). No frontend change is needed.
- The Gradient layering convention in `agent.py:7-17` keeps `AgentPayload` and
  `build_agent_payload` deliberately dependency-free of OpenWebUI so they can
  be extracted into the agent API's own Python package. The fix preserves this
  separation: only the `call_agent_api` glue layer reads from `metadata`.
- No existing tests cover `build_agent_payload`; backend test convention is
  pytest + `unittest.mock` in `backend/open_webui/test/util/`
  (e.g. `test_agent_search.py`).
- Comparable Gradient-flag plan for reference structure:
  `thoughts/shared/plans/2026-03-16-rag-filter-handoff.md` (similar
  cross-repo coordination pattern).

## Desired End State

After this plan ships **and** the agent service is updated:

1. Every chat completion routed to the agent service includes a
   `parent_message_id` field in the JSON body when the user has a parent
   message (i.e. every turn except the very first one of a new chat).
2. On retry/regenerate, the agent service rewinds its server-side thread
   state to the state immediately before `parent_message_id` was produced,
   then processes the new turn fresh — invoking tools, emitting
   `type: source` SSE events, and producing citations exactly like the first
   try.
3. Continue-response (where the same message id is being extended) and
   normal follow-up turns are unaffected: behavior is identical to today.

### How to verify

- `curl`-style observation: with `LOG_LEVEL=debug`, the `Agent API payload`
  debug log line (`utils/agent.py:295`) shows the JSON keys; on a retry the
  payload includes `parent_message_id` and on a fresh chat it does not.
- Reproduce the original transcript: ask "Wat betaal ik aan mijn zakelijke
  rekening" against the CKWL KB with the Nemotron model, click Retry on the
  answer. The retry should fire tool calls again ("Documenten in CKWL
  opsommen…", "Zoeken in CKWL naar 'kosten'…", "Document … lezen…") and
  produce citation chips at the bottom of the message.

## What We're NOT Doing

- **Not modifying the frontend.** `parent_id` is already on the wire
  (`Chat.svelte:2545`); the value reaches `metadata['parent_message_id']`
  without any further frontend work.
- **Not changing OpenAI/Ollama/other routes.** Only the Gradient agent route
  is affected; the existing `build_agent_payload` is the single seam.
- **Not modifying frontend retry/regenerate semantics.** The branching tree
  (siblings on the same `parentId`) is preserved as-is.
- **Not implementing the rewind logic in the agent service.** That is a
  separate, coordinated change in the external agents repo. This plan only
  ships the OWUI-side wire change so the field becomes available to the agent.
- **Not adding feature flags.** The change is backwards-compatible: agent
  service versions that don't yet understand `parent_message_id` will simply
  ignore the unknown field. No staged rollout is needed on the OWUI side.
- **Not changing how citations are stored or emitted.** Citations will start
  appearing on retries automatically once the agent service rewinds and
  re-invokes tools — same SSE/event-emitter path as today.

## Implementation Approach

This is a small, additive payload-extension change confined to one file
(`backend/open_webui/utils/agent.py`) plus a new unit test file. The Gradient
layering convention is preserved: `AgentPayload` and `build_agent_payload`
stay OpenWebUI-agnostic (Layer 1); only `call_agent_api` reads from
`metadata` (Layer 2). Each edit is marked with a `[Gradient]` comment matching
the style elsewhere in the file.

## Phase 1: Forward `parent_message_id` to the Agent API

### Overview

Add a single optional field to the agent payload schema, the builder
signature, and the call-site wiring. Add unit tests covering the
serialization behavior (absent when `None`, present when set).

### Changes Required

#### 1. Add `parent_message_id` to `AgentPayload`

**File**: `backend/open_webui/utils/agent.py`
**Changes**: Add an optional `parent_message_id` field next to `message_id` in
the dataclass. Document its purpose for retries/branches.

```python
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
    agent: Optional[str] = None
    stream: bool = True
    chat_id: Optional[str] = None
    user_id: Optional[str] = None
    message_id: Optional[str] = None
    # [Gradient] Parent of ``message_id`` in the chat's branching tree
    # (typically the user message that prompted this response). The agent
    # service uses this to rewind its persisted thread state on
    # retry/regenerate, so re-runs replay from the pre-answer point and
    # tools fire again instead of the model recapping cached output.
    # Omitted on the first turn of a new chat (no parent exists).
    parent_message_id: Optional[str] = None
    session_id: Optional[str] = None
    features: dict[str, Any] = field(default_factory=dict)
    files: Optional[list[dict[str, Any]]] = None
    knowledge: Optional[list[dict[str, Any]]] = None
    tool_ids: Optional[list[str]] = None
    rag_filter: Optional[dict[str, Any]] = None
    # Operator-supplied system prompt from the custom-model definition
    # (model.params.system). Variables are pre-substituted upstream so the
    # agent can use the value as-is.
    system_prompt: Optional[str] = None
    # Model params forwarded directly
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    max_tokens: Optional[int] = None
    frequency_penalty: Optional[float] = None
    presence_penalty: Optional[float] = None
    seed: Optional[int] = None
    stop: Optional[list[str]] = None
```

#### 2. Add `parent_message_id` to `build_agent_payload` signature and body

**File**: `backend/open_webui/utils/agent.py`
**Changes**: Add the parameter next to `message_id` in the signature, pass it
through to `AgentPayload(...)`. The existing `asdict()` + `None`-strip on
line 131 already handles absent values correctly.

```python
def build_agent_payload(
    *,
    model: str,
    messages: list[dict[str, Any]],
    agent: Optional[str] = None,
    stream: bool = True,
    chat_id: Optional[str] = None,
    user_id: Optional[str] = None,
    message_id: Optional[str] = None,
    parent_message_id: Optional[str] = None,
    session_id: Optional[str] = None,
    features: Optional[dict[str, Any]] = None,
    files: Optional[list[dict[str, Any]]] = None,
    knowledge: Optional[list[dict[str, Any]]] = None,
    tool_ids: Optional[list[str]] = None,
    rag_filter: Optional[dict[str, Any]] = None,
    system_prompt: Optional[str] = None,
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
        **{k: v for k, v in model_params.items() if v is not None},
    )
    return {k: v for k, v in asdict(payload).items() if v is not None}
```

#### 3. Forward `metadata['parent_message_id']` at the call site

**File**: `backend/open_webui/utils/agent.py`
**Changes**: In `call_agent_api`, pull `parent_message_id` from `metadata`
and pass it to `build_agent_payload`. Add a `[Gradient]` comment explaining
why this field matters specifically for the agent route.

```python
    # [Gradient] Forward parent_message_id so the agent service can rewind
    # its persisted thread state on retry/regenerate. Without this, the
    # agent's stateful thread store leaks the prior assistant turn into
    # the model's context and tools don't re-fire on re-runs.
    payload = build_agent_payload(
        model=llm_model,
        agent=selected_agent,
        messages=form_data.get('messages', []),
        stream=stream,
        chat_id=metadata.get('chat_id'),
        user_id=metadata.get('user_id'),
        message_id=metadata.get('message_id'),
        parent_message_id=metadata.get('parent_message_id'),
        session_id=metadata.get('session_id'),
        features=features,
        files=metadata.get('files'),
        knowledge=metadata.get('knowledge'),
        tool_ids=metadata.get('tool_ids'),
        rag_filter=metadata.get('rag_filter'),
        system_prompt=metadata.get('system_prompt'),
        **model_params,
    )
```

#### 4. Add unit tests for the payload builder

**File**: `backend/open_webui/test/util/test_agent.py` (new file)
**Changes**: Create a small pytest module covering `build_agent_payload`. We
specifically want to lock in the absent-when-None / present-when-set
serialization contract so future changes don't accidentally regress the
retry fix.

```python
"""Unit tests for the agent API payload builder.

These tests pin the wire-format contract that the Gradient agent service
depends on. In particular, they guard the retry/regenerate fix: when the
user hits retry, ``parent_message_id`` must be present in the payload so
the agent service can rewind its thread state. On fresh chats it must be
absent so the agent does not attempt to rewind to a non-existent point.
"""

from __future__ import annotations

from open_webui.utils.agent import build_agent_payload


def _base_kwargs(**overrides):
    kwargs = {
        'model': 'gpt-oss-120b',
        'messages': [{'role': 'user', 'content': 'hi'}],
    }
    kwargs.update(overrides)
    return kwargs


def test_minimal_payload_omits_optional_fields():
    payload = build_agent_payload(**_base_kwargs())
    assert payload['model'] == 'gpt-oss-120b'
    assert payload['messages'] == [{'role': 'user', 'content': 'hi'}]
    assert payload['stream'] is True
    # Optional fields are stripped when None.
    for absent in (
        'parent_message_id',
        'message_id',
        'chat_id',
        'user_id',
        'session_id',
        'agent',
        'system_prompt',
    ):
        assert absent not in payload, f'expected {absent!r} to be absent'


def test_parent_message_id_present_when_set():
    payload = build_agent_payload(
        **_base_kwargs(
            chat_id='chat-1',
            message_id='msg-new',
            parent_message_id='msg-user-prompt',
        )
    )
    assert payload['parent_message_id'] == 'msg-user-prompt'
    assert payload['message_id'] == 'msg-new'
    assert payload['chat_id'] == 'chat-1'


def test_parent_message_id_absent_on_fresh_chat():
    payload = build_agent_payload(
        **_base_kwargs(
            chat_id='chat-1',
            message_id='msg-new',
            parent_message_id=None,
        )
    )
    assert 'parent_message_id' not in payload


def test_model_params_passthrough_strips_none():
    payload = build_agent_payload(
        **_base_kwargs(
            temperature=0.2,
            top_p=None,
            max_tokens=512,
        )
    )
    assert payload['temperature'] == 0.2
    assert payload['max_tokens'] == 512
    assert 'top_p' not in payload
```

### Success Criteria

#### Automated Verification

- [ ] New file exists at `backend/open_webui/test/util/test_agent.py`
- [ ] Unit tests pass: `cd backend && pytest open_webui/test/util/test_agent.py -v`
- [ ] All existing backend tests still pass: `cd backend && pytest open_webui/test/`
- [ ] Type/import sanity: `python -c "from open_webui.utils.agent import AgentPayload, build_agent_payload; AgentPayload(model='x', messages=[], parent_message_id='y')"`
- [ ] Backend linting passes: `npm run lint:backend`
- [ ] Backend formatting clean: `black --check backend/open_webui/utils/agent.py backend/open_webui/test/util/test_agent.py`

#### Manual Verification

- [ ] Start backend with `LOG_LEVEL=debug`, click Retry on an existing
      assistant message in an agent-routed chat. Confirm the
      `Agent API payload: model=…` debug log appears once per retry and that
      the request body sent to `${AGENT_API_BASE_URL}/v1/chat/completions`
      includes `"parent_message_id": "<user message id>"` (verify via
      backend log of the JSON, or via an HTTP proxy / tcpdump if needed).
- [ ] Send a brand-new chat message. Confirm the same debug log shows the
      payload **without** a `parent_message_id` key.
- [ ] (Requires the coordinated agent-service change.) Reproduce the original
      bug transcript: ask "Wat betaal ik aan mijn zakelijke rekening" against
      the CKWL KB with the Nemotron model, click Retry. Verify the retry
      shows tool invocations (status events: "Documenten in CKWL opsommen…",
      etc.) and produces citation chips at the bottom of the new message.
      Without the agent-side change, this step will still fail — that is
      expected and confirms the OWUI side is correctly forwarding without
      causing regressions.
- [ ] Confirm no regression for non-retry follow-up turns: ask a follow-up
      question in an existing chat, verify the answer streams normally and
      citations still appear.

**Implementation Note**: After automated verification, the OWUI side can be
merged independently. The retry symptom will only fully clear once the
coordinated agent-service change ships — until then, the new field is sent
but the agent ignores it (no harm, no fix).

---

## Coordinated Agent Service Change (out of scope, separate repo)

This section documents what the external agents repo needs to do so the two
repos can be coordinated by whoever owns the agent service.

### Contract

The agent service's `POST /v1/chat/completions` endpoint should accept an
optional `parent_message_id: str | None` field in the request body.

### Expected behavior

- **When `parent_message_id` is present** in the request body: before
  building the LLM context from the persisted thread state for `chat_id`,
  truncate/rewind that state to the snapshot immediately **before** the
  message with id `parent_message_id` was produced. Concretely: keep the
  user message identified by `parent_message_id` (it is the prompt being
  answered), drop any persisted assistant turn(s) and tool messages that
  were generated after it (i.e. the prior sibling branch). Then continue
  processing as a fresh assistant turn — invoking tools, emitting `source`
  events for citations, etc.
- **When `parent_message_id` is absent**: process as today (no rewind).
- **When `parent_message_id` references an unknown message**: log a warning
  and process as today (no rewind). This is defensive — OWUI can send IDs
  that the agent service has not yet checkpointed in the rare case of
  cross-version retries.

### Why this works

OWUI's frontend models retries as a new sibling assistant message under the
same user prompt (`parentId = userMessage.id`). Sending
`parent_message_id = userMessage.id` is unambiguous: "answer the question
this user message asked, ignoring any sibling answer you may have already
stored for it."

### Implication for non-retry turns

Every follow-up turn also sends `parent_message_id = <latest user message>`,
which is exactly the point the agent's thread state should already be at. So
the rewind on follow-ups is a no-op (or rewind-to-current-tip). The
implementation should be a `<=` truncation, not strict `<`, to avoid an
off-by-one drop of the latest user message.

---

## Testing Strategy

### Unit Tests

The new `test_agent.py` covers:

- Minimal payload includes only required fields; all optional fields absent
- `parent_message_id` is present in the dict when set
- `parent_message_id` is stripped when `None` (fresh chat case)
- Model param `None`-stripping still works (regression guard for the
  `**model_params` filter on line 129)

### Manual Testing Steps

1. **Wire-level check**: With backend running locally and `LOG_LEVEL=debug`,
   open the network panel or backend logs. Trigger:
   - A brand-new chat first message → payload **omits** `parent_message_id`.
   - A retry on any assistant message → payload **includes**
     `parent_message_id` set to the parent user message's id.
   - A follow-up question (not retry) → payload **includes**
     `parent_message_id` set to the latest user message's id.
2. **End-to-end (post-agent-service change)**: Reproduce the original bug
   exactly as in the linked transcript and confirm retry produces tool calls
   + citations.
3. **Regression**: Verify non-agent routes (OpenAI/Ollama models) are
   unaffected by running a normal chat against e.g. a vanilla OpenAI model
   configured in OWUI.

## Performance Considerations

None. The change adds a single string field (a UUID) to the request body and
no new code paths or I/O.

## Migration Notes

- No database migrations.
- No env vars added.
- No frontend changes — `parent_id` is already on the wire from the
  frontend.
- Forward-compat with older agent service versions: unknown JSON fields are
  ignored by the agent service today (standard FastAPI/Pydantic permissive
  behavior for the receiving endpoint), so OWUI can ship this change before
  the agent-side rewind logic lands. The retry bug stays until the agent
  side ships, but no new bug is introduced.

## References

- Bug reproducer (chat transcript, CKWL KB / Nemotron model): see commit
  thread that introduced this plan.
- Frontend retry path: `src/lib/components/chat/Chat.svelte:2725-2761`
  (`regenerateResponse`), `src/lib/utils/index.ts:1263-1277`
  (`createMessagesList`).
- Frontend wire payload: `src/lib/components/chat/Chat.svelte:2504-2546`
  (`generateOpenAIChatCompletion` call, `parent_id` field at line 2545).
- Backend metadata mapping: `backend/open_webui/main.py:2199-2227`
  (`parent_message_id` populated from `form_data['parent_id']`).
- Backend DB-side history reconstruction (for non-agent routes; verified
  *not* the cause of this bug): `backend/open_webui/utils/middleware.py:2090-2210`
  (`load_messages_from_db` + `process_messages_with_output`),
  `backend/open_webui/utils/misc.py:71-108` (`get_message_list` walks
  parent chain only, so siblings aren't included).
- Agent dispatch and SSE source-event path:
  `backend/open_webui/utils/middleware.py:2517-2519`,
  `backend/open_webui/utils/agent.py:227-403`.
- Related coordination-style plan to model after:
  `thoughts/shared/plans/2026-03-16-rag-filter-handoff.md`.
