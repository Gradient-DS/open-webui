"""Typed diagnostics carried by failed agent turns."""

from typing import NotRequired, TypedDict


class TurnErrorDetails(TypedDict):
    status: int
    upstream_status: NotRequired[int]
    trace_id: NotRequired[str]


class TurnError(TurnErrorDetails):
    code: str
    message: str


class AgentTurnError(Exception):
    def __init__(self, error: TurnError) -> None:
        super().__init__(error['code'])
        self.error = error
