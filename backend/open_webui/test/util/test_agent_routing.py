"""Unit tests for the Gradient agent-routing contract.

These pin two pure decisions that were previously inlined in
``main.py``/``middleware.py``:

1. ``agent_owns_tool_execution`` — the agent service runs its own tools
   and emits ``delta.tool_calls`` only for display, so OWUI must skip its
   native-tool-calling resolution loop for agent responses (otherwise it
   re-invokes the model after the agent's turn — the post-summary
   "extra reasoning + answer" loop).
2. ``resolve_agent_route`` — the boolean routing matrix that decides
   whether a chat is handled by the agent service.
"""

from __future__ import annotations

from open_webui.utils.agent_routing import (
    agent_owns_tool_execution,
    resolve_agent_route,
)


def test_agent_owns_tool_execution_true_when_route_flag_set():
    assert agent_owns_tool_execution({'route_to_agent': True}) is True


def test_agent_owns_tool_execution_false_when_flag_absent_or_falsy():
    assert agent_owns_tool_execution({}) is False
    assert agent_owns_tool_execution({'route_to_agent': False}) is False
    assert agent_owns_tool_execution(None) is False


def test_resolve_agent_route_agent_id_wins():
    # An explicit per-chat agent always routes to the agent, regardless
    # of the picker / legacy-bypass flags.
    assert (
        resolve_agent_route(
            chat_agent_id='a1',
            agent_api_enabled=False,
            feature_agent_picker=True,
        )
        is True
    )


def test_resolve_agent_route_legacy_bypass():
    # No agent_id + picker off + API enabled → legacy global bypass.
    assert (
        resolve_agent_route(
            chat_agent_id=None,
            agent_api_enabled=True,
            feature_agent_picker=False,
        )
        is True
    )


def test_resolve_agent_route_standard_when_picker_on_without_agent_id():
    assert (
        resolve_agent_route(
            chat_agent_id=None,
            agent_api_enabled=True,
            feature_agent_picker=True,
        )
        is False
    )


def test_resolve_agent_route_standard_by_default():
    assert (
        resolve_agent_route(
            chat_agent_id=None,
            agent_api_enabled=False,
            feature_agent_picker=False,
        )
        is False
    )
