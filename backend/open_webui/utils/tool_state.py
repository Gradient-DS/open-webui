"""[Gradient] A tool control's three states, as the chat request carries them."""

from typing import Literal

ToolState = Literal['off', 'auto', 'required']


def tool_state(features: dict | None, key: str) -> ToolState:
    """Uit (off), Auto (the agent decides) or Altijd (required), from the control's two flags."""
    features = features or {}
    if not features.get(key):
        return 'off'
    return 'required' if features.get(f'{key}_required') else 'auto'
