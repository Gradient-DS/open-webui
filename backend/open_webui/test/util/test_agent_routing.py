"""Unit tests for the Gradient agent-routing contract.

These pin two pure decisions that were previously inlined in
``main.py``/``middleware.py``:

1. ``agent_owns_tool_execution`` — the agent service runs its own tools
   and emits ``delta.tool_calls`` only for display, so OWUI must skip its
   native-tool-calling resolution loop for agent responses (otherwise it
   re-invokes the model after the agent's turn — the post-summary
   "extra reasoning + answer" loop).
2. ``resolve_agent_route`` — the routing matrix that decides whether a
   chat is handled by the agent service, and by which agent. Under the
   picker a chat without a binding never reaches the raw model: it
   either falls back to the admin-configured default agent or the
   request is refused.
"""

from __future__ import annotations

import pytest
from open_webui.utils.agent_routing import (
    AgentRoute,
    AgentSelectionRequired,
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
    # An explicit per-chat agent always routes to that agent, regardless
    # of the picker / legacy-bypass flags and of any default.
    route = resolve_agent_route(
        chat_agent_id='a1',
        agent_api_enabled=False,
        feature_agent_picker=True,
        default_agent_id='fallback',
    )
    assert route == AgentRoute(to_agent=True, agent_id='a1')


def test_resolve_agent_route_legacy_bypass():
    # No agent_id + picker off + API enabled → legacy global bypass; the
    # agent service picks its own default, so no slug is named.
    route = resolve_agent_route(
        chat_agent_id=None,
        agent_api_enabled=True,
        feature_agent_picker=False,
    )
    assert route == AgentRoute(to_agent=True, agent_id=None)


def test_resolve_agent_route_picker_without_agent_id_falls_back_to_default():
    route = resolve_agent_route(
        chat_agent_id=None,
        agent_api_enabled=True,
        feature_agent_picker=True,
        default_agent_id='soev_chat_manual',
    )
    assert route == AgentRoute(to_agent=True, agent_id='soev_chat_manual')


def test_resolve_agent_route_picker_without_agent_id_or_default_is_refused():
    # The one outcome that must never happen under the picker is the
    # standard route: that is the raw model with stock RAG.
    with pytest.raises(AgentSelectionRequired):
        resolve_agent_route(
            chat_agent_id=None,
            agent_api_enabled=True,
            feature_agent_picker=True,
        )


def test_resolve_agent_route_standard_when_picker_on_but_agent_api_off():
    # A picker with no agent service behind it has nothing to route to.
    route = resolve_agent_route(
        chat_agent_id=None,
        agent_api_enabled=False,
        feature_agent_picker=True,
        default_agent_id='soev_chat_manual',
    )
    assert route == AgentRoute(to_agent=False, agent_id=None)


def test_resolve_agent_route_standard_by_default():
    route = resolve_agent_route(
        chat_agent_id=None,
        agent_api_enabled=False,
        feature_agent_picker=False,
    )
    assert route == AgentRoute(to_agent=False, agent_id=None)
