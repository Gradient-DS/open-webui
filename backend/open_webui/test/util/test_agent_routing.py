"""Unit tests for the Gradient agent-routing contract.

``agent_owns_tool_execution``: the agent service runs its own tools and
emits ``delta.tool_calls`` only for display, so OWUI must skip its
native-tool-calling resolution loop for agent responses (otherwise it
re-invokes the model after the agent's turn — the post-summary
"extra reasoning + answer" loop).
"""

from __future__ import annotations

import pytest
from open_webui.utils.agent_routing import agent_owns_tool_execution


def test_agent_owns_tool_execution_true_when_route_flag_set():
    assert agent_owns_tool_execution({'route_to_agent': True}) is True


def test_agent_owns_tool_execution_false_when_flag_absent_or_falsy():
    assert agent_owns_tool_execution({}) is False
    assert agent_owns_tool_execution({'route_to_agent': False}) is False
    assert agent_owns_tool_execution(None) is False


@pytest.mark.parametrize(
    'row,body,has_message,expected',
    [
        (None, None, False, 'keep'),
        ('a', None, True, 'keep'),
        (None, 'a', False, 'bind'),
        (None, 'a', True, 'bind'),
        ('a', 'a', True, 'keep'),
        ('a', 'a', False, 'keep'),
        ('a', 'b', False, 'bind'),
    ],
)
def test_assistant_binding_decision(row, body, has_message, expected):
    """Omitted legacy identities preserve bindings and empty chats can switch."""
    from open_webui.utils.agent_routing import resolve_assistant_binding

    assert resolve_assistant_binding(row, body, has_message) == expected


def test_assistant_binding_refuses_switch_after_message():
    """A persisted assistant message fixes the assistant identity."""
    from open_webui.utils.agent_routing import AssistantBindingConflict, resolve_assistant_binding

    with pytest.raises(AssistantBindingConflict, match='assistant is fixed'):
        resolve_assistant_binding('a', 'b', True)
