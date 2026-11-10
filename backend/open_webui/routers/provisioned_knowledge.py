"""Readable provisioned knowledge for the chat composer."""

from fastapi import APIRouter, Depends, HTTPException, status
from open_webui.models.knowledge import Knowledges
from open_webui.soev import provisioned_knowledge
from open_webui.soev.availability import SoevRoute
from open_webui.utils.auth import get_verified_user
from pydantic import BaseModel

router = APIRouter(route_class=SoevRoute)


class ProvisionedKnowledge(BaseModel):
    id: str
    name: str
    description: str = ''


@router.get('/', response_model=list[ProvisionedKnowledge])
async def list_provisioned_knowledge(user=Depends(get_verified_user)) -> list[ProvisionedKnowledge]:
    """List as the caller; return 404 while the feature is unavailable."""
    if not await provisioned_knowledge.allowed():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return [
        ProvisionedKnowledge(id=row['key'], name=row['name'], description=row.get('description') or '')
        for row in await Knowledges.provisioned_knowledge(user.id)
    ]
