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
2. :func:`resolve_agent_route` — the routing matrix deciding whether a
   chat is handled by the agent service, and by which agent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class AgentSelectionRequired(Exception):
    """The picker is on, the chat names no agent, and no default is configured.

    Raised instead of returning the standard route: under the picker the
    standard route is the raw model with stock RAG, which a tenant user
    must never reach by omission (they only see it as "few sources").
    """


@dataclass(frozen=True)
class AgentRoute:
    """Where a chat turn goes.

    :param to_agent: Whether the agent service handles the turn.
    :param agent_id: The agent slug to hand the service. ``None`` on the
        legacy bypass, where the service picks its own default, and on
        the standard route.
    """

    to_agent: bool
    agent_id: str | None = None


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
    default_agent_id: str | None = None,
) -> AgentRoute:
    """Decide whether a chat is handled by the agent service, and by which agent.

    Routing matrix:

    - chat bound to an agent (``chat_agent_id`` set)          → that agent
    - no agent_id, picker on, API enabled, default configured → the default agent
    - no agent_id, picker on, API enabled, no default         → refused
    - no agent_id, picker off, API enabled                    → agent route (legacy bypass)
    - otherwise                                               → standard route

    Access-control / DB lookups (verifying the user may use the resolved
    agent, reading the default from config) stay at the call site; this
    only encodes the decision matrix.

    :param chat_agent_id: Per-chat bound agent id, or ``None``.
    :param agent_api_enabled: The ``AGENT_API_ENABLED`` bypass flag.
    :param feature_agent_picker: The ``FEATURE_AGENT_PICKER`` master flag.
    :param default_agent_id: The admin-configured picker default, already
        checked to be active; ``None`` when there is none.
    :raises AgentSelectionRequired: When the picker is on and neither the
        chat nor the deployment names an agent.
    """
    if chat_agent_id:
        return AgentRoute(to_agent=True, agent_id=chat_agent_id)
    if not agent_api_enabled:
        return AgentRoute(to_agent=False)
    if not feature_agent_picker:
        return AgentRoute(to_agent=True)
    if default_agent_id:
        return AgentRoute(to_agent=True, agent_id=default_agent_id)
    raise AgentSelectionRequired()
