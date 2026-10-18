"""[Gradient] Agent-routing contract — isolated from upstream main.py/middleware.

Keeps our delta against upstream Open WebUI small and obvious.

:func:`agent_owns_tool_execution` decides whether this turn is handled by
the agent service. The agent is a complete orchestrator: it executes its
own tools server-side and emits ``delta.tool_calls`` only for display.
OWUI's native-tool-calling resolution loop must therefore be skipped for
agent responses, or it re-invokes the model after the agent's turn (the
post-summary "extra reasoning + answer" loop).
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


class AssistantBindingConflict(ValueError):
    """An existing conversation cannot switch assistants after an assistant message."""


def resolve_assistant_binding(row_value: str | None, body_value: str | None, has_assistant_message: bool) -> str:
    """Return bind or keep, or refuse a switch in an established conversation."""
    if not body_value or body_value == row_value:
        return 'keep'
    if row_value and has_assistant_message:
        raise AssistantBindingConflict('The assistant is fixed once a chat has an assistant message.')
    return 'bind'
