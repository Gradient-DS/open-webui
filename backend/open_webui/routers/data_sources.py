"""[Gradient] Data sources the caller can read, for the composer's "+" menu (`soev/data_sources.py`)."""

from fastapi import APIRouter, Depends, HTTPException, status
from open_webui.models.knowledge import Knowledges
from open_webui.soev import data_sources
from open_webui.soev.availability import SoevRoute
from open_webui.utils.auth import get_verified_user
from pydantic import BaseModel

router = APIRouter(route_class=SoevRoute)


class DataSource(BaseModel):
    id: str
    name: str
    description: str = ''


@router.get('/', response_model=list[DataSource])
async def list_data_sources(user=Depends(get_verified_user)) -> list[DataSource]:
    """Read as the caller on soev-api, so only the ones they may read; 404 while data sources are off."""
    if not await data_sources.allowed():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return [
        DataSource(id=row['key'], name=row['name'], description=row.get('description') or '')
        for row in await Knowledges.data_sources(user.id)
    ]
