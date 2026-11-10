import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from open_webui.constants import ERROR_MESSAGES
from open_webui.events import EVENTS, publish_event
from open_webui.internal.db import get_async_session
from open_webui.models.access_grants import AccessGrants
from open_webui.models.groups import (
    GroupForm,
    GroupInfoResponse,
    GroupModel,
    GroupResponse,
    Groups,
    GroupUpdateForm,
    UserIdsForm,
)
from open_webui.models.knowledge import Knowledges
from open_webui.models.models import Models
from open_webui.models.tools import Tools
from open_webui.models.users import UserInfoResponse, UserModel, Users
from open_webui.soev.availability import configured
from open_webui.soev.group_policy import refuse_group_write
from open_webui.utils.auth import get_admin_user, get_verified_user
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

router = APIRouter()


async def require_group_write(user=Depends(get_verified_user)) -> None:
    refuse_group_write()


async def require_group_update(form_data: GroupUpdateForm, user=Depends(get_verified_user)) -> GroupUpdateForm:
    if {'name', 'description', 'user_ids'} & form_data.model_fields_set:
        refuse_group_write()
    return form_data


async def _visible_group(id: str, user: UserModel, db: AsyncSession) -> GroupModel:
    visible_ids = {group.id for group in await Groups.get_groups_by_member_id(user.id, db=db)}
    if id not in visible_ids:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND)
    group = await Groups.get_group_by_id(id, db=db)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND)
    return group


############################
# GetFunctions
############################


@router.get('/', response_model=list[GroupResponse])
async def get_groups(
    share: bool | None = None,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    filter = {}

    # Admins can share to all groups regardless of share setting
    filter['member_id'] = user.id
    if share is not None:
        filter['share'] = share

    groups = await Groups.get_groups(filter=filter, db=db)

    return groups


############################
# CreateNewGroup
############################


############################
# GetGroupById
############################


@router.get('/id/{id}/info', response_model=GroupInfoResponse | None)
async def get_group_info_by_id(id: str, user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)):
    group = await _visible_group(id, user, db)
    return GroupInfoResponse(
        **group.model_dump(),
        member_count=await Groups.get_group_member_count_by_id(group.id, db=db),
    )


############################
# ExportGroupById
############################


class GroupExportResponse(GroupResponse):
    user_ids: list[str] = []


############################
# GetUsersInGroupById
############################


############################
# UpdateGroupById
############################


############################
# AddUserToGroupByUserIdAndGroupId
############################


############################
# DeleteGroupById
############################


############################
# PreviewGroupAccess
############################
