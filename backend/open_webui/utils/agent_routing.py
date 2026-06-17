"""[Gradient] Agent-routing contract — isolated from upstream main.py/middleware.

Keeps our delta against upstream Open WebUI small and obvious. Two pure
decisions live here so the agent contract is in one unit-tested place
and the call sites stay one-liners:

1. :func:`agent_owns_tool_execution` — whether this turn is handled by
   the agent service. The agent is a complete orchestrator: it executes
   its own tools server-side and emits ``delta.tool_calls`` only for
   display. OWUI's native-tool-calling resolution loop must therefore be
   skipped for agent responses, or it re-invokes the model after the
   agent's turn (the post-summary "extra reasoning + answer" loop).
2. :func:`resolve_agent_route` — the boolean routing matrix deciding
   whether a chat is handled by the agent service.
"""

from __future__ import annotations

from typing import Any


def agent_owns_tool_execution(metadata: dict[str, Any] | None) -> bool:
    """True when this turn is handled by the agent service.

    Gates OWUI's native-tool-calling resolution loop: when the agent
    owns tool execution, OWUI must stream its response through as
    terminal and never try to "resolve" the agent's already-executed
    tool calls (doing so re-invokes the model).

    :param metadata: The per-request metadata dict (``ctx['metadata']``).
        ``None`` is treated as "not an agent turn".
    """
    return bool((metadata or {}).get('route_to_agent'))


def resolve_agent_route(
    *,
    chat_agent_id: str | None,
    agent_api_enabled: bool,
    feature_agent_picker: bool,
) -> bool:
    """Decide whether a chat is handled by the agent service.

    Routing matrix:

    - chat bound to an agent (``chat_agent_id`` set)      → agent route
    - no agent_id, picker off, ``AGENT_API_ENABLED``      → agent route (legacy bypass)
    - otherwise                                           → standard route

    Access-control / DB lookups (verifying the user may still use
    ``chat_agent_id``) stay at the call site; this only encodes the
    decision matrix.

    :param chat_agent_id: Per-chat bound agent id, or ``None``.
    :param agent_api_enabled: The ``AGENT_API_ENABLED`` bypass flag.
    :param feature_agent_picker: The ``FEATURE_AGENT_PICKER`` master flag.
    """
    if chat_agent_id:
        return True
    if agent_api_enabled and not feature_agent_picker:
        return True
    return False
