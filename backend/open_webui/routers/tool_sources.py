"""[Gradient] Discover consented external connections with the caller's platform authority."""

import re
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from open_webui.models.config import Config
from open_webui.routers.meetings import Soev, _http_error
from open_webui.soev.client import SoevApiError
from open_webui.utils.tool_sources import selected_tool_sources
from pydantic import BaseModel, ConfigDict, Field, ValidationError

router = APIRouter()


class ToolSource(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    id: str
    kind: str = Field(min_length=1)
    name: str
    group_name: str
    countries: list[str]


class ToolSources(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    data: list[ToolSource]


@router.get('')
async def list_tool_sources(
    soev: Soev,
    agent: Annotated[str | None, Query(pattern=r'^[A-Za-z0-9_-]{1,256}$')] = None,
):
    agent = agent if agent is not None else await Config.get('agent_api.selected_agent')
    if not agent:
        return {'data': []}
    # Configuration is subject to the same path boundary as the query parameter.
    if not isinstance(agent, str) or re.fullmatch(r'[A-Za-z0-9_-]{1,256}', agent) is None:
        raise HTTPException(503, detail='Agent unavailable')
    try:
        payload = await soev.client.get(f'/v1/agents/{agent}/tool-sources', as_user=soev.ref)
    except SoevApiError as error:
        raise _http_error(error) from None
    try:
        parsed = ToolSources.model_validate(payload)
        if any(not selected_tool_sources([entry.id]) for entry in parsed.data):
            raise ValueError
    except (ValidationError, ValueError):
        raise HTTPException(502, detail='Invalid tool sources response') from None
    return payload
