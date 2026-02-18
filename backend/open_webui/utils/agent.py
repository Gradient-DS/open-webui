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

    Standard OpenAI chunks (passed through to process_chat_response):
        data: {"choices": [{"delta": {"content": "..."}}]}

    End of stream:
        data: [DONE]
"""

import json
import logging
from dataclasses import dataclass, field, asdict
from typing import Any, AsyncIterator, Optional

import aiohttp
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

from open_webui.env import AGENT_API_BASE_URL
from open_webui.socket.main import get_event_emitter

log = logging.getLogger(__name__)


# ============================================================================
# Layer 1 — Transport client (no OpenWebUI dependency beyond env config)
# ============================================================================


@dataclass
class AgentPayload:
    """Request schema for the agent API's chat completions endpoint."""

    model: str
    messages: list[dict[str, Any]]
    stream: bool = True
    chat_id: Optional[str] = None
    user_id: Optional[str] = None
    message_id: Optional[str] = None
    session_id: Optional[str] = None
    features: dict[str, Any] = field(default_factory=dict)
    files: Optional[list[dict[str, Any]]] = None
    knowledge: Optional[list[dict[str, Any]]] = None
    tool_ids: Optional[list[str]] = None
    # Model params forwarded directly
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    max_tokens: Optional[int] = None
    frequency_penalty: Optional[float] = None
    presence_penalty: Optional[float] = None
    seed: Optional[int] = None
    stop: Optional[list[str]] = None


def build_agent_payload(
    *,
    model: str,
    messages: list[dict[str, Any]],
    stream: bool = True,
    chat_id: Optional[str] = None,
    user_id: Optional[str] = None,
    message_id: Optional[str] = None,
    session_id: Optional[str] = None,
    features: Optional[dict[str, Any]] = None,
    files: Optional[list[dict[str, Any]]] = None,
    knowledge: Optional[list[dict[str, Any]]] = None,
    tool_ids: Optional[list[str]] = None,
    **model_params,
) -> dict[str, Any]:
    """Build a JSON-serialisable payload for the agent API.

    Constructs an AgentPayload and converts it to a dict, stripping None
    values so the agent only sees fields that are actually set.
    """
    payload = AgentPayload(
        model=model,
        messages=messages,
        stream=stream,
        chat_id=chat_id,
        user_id=user_id,
        message_id=message_id,
        session_id=session_id,
        features=features or {},
        files=files,
        knowledge=knowledge,
        tool_ids=tool_ids,
        **{k: v for k, v in model_params.items() if v is not None},
    )
    return {k: v for k, v in asdict(payload).items() if v is not None}


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
    session = aiohttp.ClientSession(
        trust_env=True,
        timeout=aiohttp.ClientTimeout(total=timeout),
    )

    try:
        response = await session.request(
            method="POST",
            url=f"{base_url}/v1/chat/completions",
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )

        if response.status >= 400:
            body = await response.text()
            await session.close()
            raise Exception(
                f"Agent API returned {response.status}: {body}"
            )

        current_event_type = "data"

        async for raw_line in response.content:
            line = raw_line.decode("utf-8").rstrip("\n\r")

            if not line:
                continue

            if line.startswith("event:"):
                current_event_type = line[len("event:"):].strip()
                continue

            if line.startswith("data:"):
                data_str = line[len("data:"):].strip()

                if data_str == "[DONE]":
                    yield SSEEvent(event_type="done", data="[DONE]")
                    break

                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    data = data_str

                yield SSEEvent(event_type=current_event_type, data=data)
                # Reset event type after yielding — next data line defaults
                # to "data" unless preceded by a new event: line
                current_event_type = "data"

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
):
    """Route a chat completion to the external agent API.

    [Gradient] Called from main.py when AGENT_API_ENABLED is true. Extracts
    fields from OpenWebUI's form_data/metadata, calls the transport layer,
    routes custom SSE events to Socket.IO, and returns either a
    StreamingResponse or a dict for process_chat_response to consume.
    """
    stream = form_data.get("stream", True)

    # Extract model params that should be forwarded
    model_params = {}
    for key in ("temperature", "top_p", "max_tokens", "frequency_penalty",
                "presence_penalty", "seed", "stop"):
        if key in form_data:
            model_params[key] = form_data[key]

    payload = build_agent_payload(
        model=form_data.get("model", ""),
        messages=form_data.get("messages", []),
        stream=stream,
        chat_id=metadata.get("chat_id"),
        user_id=metadata.get("user_id"),
        message_id=metadata.get("message_id"),
        session_id=metadata.get("session_id"),
        features=features,
        files=metadata.get("files"),
        knowledge=metadata.get("knowledge"),
        tool_ids=metadata.get("tool_ids"),
        **model_params,
    )

    log.debug(f"Agent API payload: model={payload.get('model')}, "
              f"stream={stream}, features={features}")

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
            method="POST",
            url=f"{AGENT_API_BASE_URL}/v1/chat/completions",
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )

        if response.status >= 400:
            body = await response.text()
            raise Exception(
                f"Agent API returned {response.status}: {body}"
            )

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
    event_emitter = get_event_emitter(metadata)

    async def body_generator():
        try:
            async for sse_event in stream_agent_response(AGENT_API_BASE_URL, payload):
                if sse_event.event_type == "done":
                    yield "data: [DONE]\n\n"
                    break

                if sse_event.event_type in ("status", "source"):
                    # [Gradient] Route custom events to Socket.IO so the UI
                    # shows status spinners and source citations in real time.
                    if event_emitter:
                        try:
                            await event_emitter(
                                {
                                    "type": sse_event.event_type,
                                    "data": sse_event.data,
                                }
                            )
                        except Exception as e:
                            log.warning(f"Error emitting {sse_event.event_type} event: {e}")
                    continue

                # Standard OpenAI chunk — pass through as SSE data line
                if isinstance(sse_event.data, dict):
                    yield f"data: {json.dumps(sse_event.data)}\n\n"
                else:
                    yield f"data: {sse_event.data}\n\n"

        except Exception as e:
            log.error(f"Agent API streaming error: {e}")
            # Yield an error as an OpenAI-style chunk so the UI sees it
            error_chunk = {
                "choices": [
                    {
                        "delta": {"content": f"\n\n[Agent API error: {e}]"},
                        "finish_reason": "stop",
                    }
                ]
            }
            yield f"data: {json.dumps(error_chunk)}\n\n"
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        body_generator(),
        media_type="text/event-stream",
    )
