"""[Gradient] The web search control's three states, as the chat request carries them.

`features.web_search` means web search is possible (Auto or Altijd) and `features.web_search_required` means
Altijd. Mirrors `src/lib/utils/toolState.ts`.
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
