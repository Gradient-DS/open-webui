"""[Gradient] The web search control's three states, as the chat request carries them.

`features.web_search` means web search is possible (Auto or Altijd) and `features.web_search_required` means
Altijd. A payload with only `web_search: true` (older clients, saved chats) reads as Auto. Mirrors
`src/lib/utils/toolState.ts`.
"""

from __future__ import annotations

from typing import Literal

WebSearchState = Literal['off', 'auto', 'required']


def web_search_state(features: dict | None) -> WebSearchState:
    """Uit (`off`), Auto (`auto`, the model decides) or Altijd (`required`), in the soev agent's tool states."""
    features = features or {}
    if not features.get('web_search'):
        return 'off'
    return 'required' if features.get('web_search_required') else 'auto'


def should_force_web_search(features: dict | None, function_calling: str | None, route_to_agent: bool) -> bool:
    """Whether a non-agent turn runs the search before the model answers. The agent owns web search on routed
    turns. Altijd always forces it; Auto only in legacy function calling, since native function calling offers
    the builtin web_search tool instead (it stays offered on Altijd as well)."""
    if route_to_agent:
        return False
    state = web_search_state(features)
    return state == 'required' or (state == 'auto' and function_calling == 'legacy')
