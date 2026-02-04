import os
from pathlib import Path
from typing import Optional
import logging

from open_webui.models.users import Users, UserInfoResponse
from open_webui.models.groups import (
    Groups,
    GroupForm,
    GroupUpdateForm,
    GroupResponse,
    UserIdsForm,
)
from open_webui.models.knowledge import Knowledges

from open_webui.config import CACHE_DIR
from open_webui.constants import ERROR_MESSAGES
from fastapi import APIRouter, Depends, HTTPException, Request, status

from open_webui.utils.auth import get_admin_user, get_verified_user


log = logging.getLogger(__name__)

router = APIRouter()

############################
# GetFunctions
############################


@router.get("/", response_model=list[GroupResponse])
async def get_groups(share: Optional[bool] = None, user=Depends(get_verified_user)):

    filter = {}
    if user.role != "admin":
        filter["member_id"] = user.id

    if share is not None:
        filter["share"] = share

    groups = Groups.get_groups(filter=filter)

    return groups


############################
# CreateNewGroup
############################


@router.post("/create", response_model=Optional[GroupResponse])
async def create_new_group(form_data: GroupForm, user=Depends(get_admin_user)):
    try:
        group = Groups.insert_new_group(user.id, form_data)
        if group:
            return GroupResponse(
                **group.model_dump(),
                member_count=Groups.get_group_member_count_by_id(group.id),
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT("Error creating group"),
            )
    except Exception as e:
        log.exception(f"Error creating a new group: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(e),
        )


############################
# GetGroupById
############################


@router.get("/id/{id}", response_model=Optional[GroupResponse])
async def get_group_by_id(id: str, user=Depends(get_admin_user)):
    group = Groups.get_group_by_id(id)
    if group:
        return GroupResponse(
            **group.model_dump(),
            member_count=Groups.get_group_member_count_by_id(group.id),
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


############################
# ExportGroupById
############################


class GroupExportResponse(GroupResponse):
    user_ids: list[str] = []
    pass


@router.get("/id/{id}/export", response_model=Optional[GroupExportResponse])
async def export_group_by_id(id: str, user=Depends(get_admin_user)):
    group = Groups.get_group_by_id(id)
    if group:
        return GroupExportResponse(
            **group.model_dump(),
            member_count=Groups.get_group_member_count_by_id(group.id),
            user_ids=Groups.get_group_user_ids_by_id(group.id),
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


############################
# GetUsersInGroupById
############################


@router.post("/id/{id}/users", response_model=list[UserInfoResponse])
async def get_users_in_group(id: str, user=Depends(get_admin_user)):
    try:
        users = Users.get_users_by_group_id(id)
        return users
    except Exception as e:
        log.exception(f"Error adding users to group {id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(e),
        )


############################
# UpdateGroupById
############################


@router.post("/id/{id}/update", response_model=Optional[GroupResponse])
async def update_group_by_id(
    id: str, form_data: GroupUpdateForm, user=Depends(get_admin_user)
):
    try:
        group = Groups.update_group_by_id(id, form_data)
        if group:
            return GroupResponse(
                **group.model_dump(),
                member_count=Groups.get_group_member_count_by_id(group.id),
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT("Error updating group"),
            )
    except Exception as e:
        log.exception(f"Error updating group {id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(e),
        )


############################
# AddUserToGroupByUserIdAndGroupId
############################


@router.post("/id/{id}/users/add", response_model=Optional[GroupResponse])
async def add_user_to_group(
    id: str, form_data: UserIdsForm, user=Depends(get_admin_user)
):
    try:
        if form_data.user_ids:
            form_data.user_ids = Users.get_valid_user_ids(form_data.user_ids)

        # Check if this group has access to source-restricted KBs
        source_restricted_kbs = (
            Knowledges.get_source_restricted_knowledge_by_group_id(id)
        )

        if source_restricted_kbs and form_data.user_ids:
            # Group conflicts by KB for richer UI presentation
            kb_conflicts = {}  # kb_id -> conflict info

            def _build_onedrive_sources(sync_meta: dict) -> list[dict]:
                """Extract source names and OneDrive URLs from sync metadata."""
                sources = sync_meta.get("sources", [])
                result = []
                for s in sources:
                    name = s.get("name")
                    if not name:
                        continue
                    item_id = s.get("item_id")
                    url = (
                        f"https://onedrive.live.com/?id={item_id}"
                        if item_id
                        else None
                    )
                    result.append({"name": name, "url": url})
                return result

            for uid in form_data.user_ids:
                target_user = Users.get_user_by_id(uid)
                if not target_user or not target_user.email:
                    # Users without email can't be validated against any KB
                    for kb in source_restricted_kbs:
                        if kb.id not in kb_conflicts:
                            meta = kb.meta or {}
                            onedrive_sync = meta.get("onedrive_sync", {})
                            kb_conflicts[kb.id] = {
                                "knowledge_id": kb.id,
                                "knowledge_name": kb.name,
                                "onedrive_sources": _build_onedrive_sources(
                                    onedrive_sync
                                ),
                                "users_without_access": [],
                            }
                        kb_conflicts[kb.id]["users_without_access"].append(
                            {
                                "user_id": uid,
                                "user_name": (
                                    target_user.name if target_user else "Unknown"
                                ),
                                "user_email": "",
                            }
                        )
                    continue

                user_email = target_user.email.lower()
                for kb in source_restricted_kbs:
                    meta = kb.meta or {}
                    onedrive_sync = meta.get("onedrive_sync", {})
                    permitted_emails = onedrive_sync.get("permitted_emails", [])
                    permitted_lower = {e.lower() for e in permitted_emails}
                    if user_email not in permitted_lower:
                        if kb.id not in kb_conflicts:
                            kb_conflicts[kb.id] = {
                                "knowledge_id": kb.id,
                                "knowledge_name": kb.name,
                                "onedrive_sources": _build_onedrive_sources(
                                    onedrive_sync
                                ),
                                "users_without_access": [],
                            }
                        kb_conflicts[kb.id]["users_without_access"].append(
                            {
                                "user_id": uid,
                                "user_name": target_user.name,
                                "user_email": target_user.email,
                            }
                        )

            if kb_conflicts:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "message": "Cannot add user(s) to group: source permission conflicts",
                        "kb_conflicts": list(kb_conflicts.values()),
                    },
                )

        group = Groups.add_users_to_group(id, form_data.user_ids)
        if group:
            return GroupResponse(
                **group.model_dump(),
                member_count=Groups.get_group_member_count_by_id(group.id),
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT("Error adding users to group"),
            )
    except HTTPException:
        raise
    except Exception as e:
        log.exception(f"Error adding users to group {id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(e),
        )


@router.post("/id/{id}/users/remove", response_model=Optional[GroupResponse])
async def remove_users_from_group(
    id: str, form_data: UserIdsForm, user=Depends(get_admin_user)
):
    try:
        group = Groups.remove_users_from_group(id, form_data.user_ids)
        if group:
            return GroupResponse(
                **group.model_dump(),
                member_count=Groups.get_group_member_count_by_id(group.id),
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT("Error removing users from group"),
            )
    except Exception as e:
        log.exception(f"Error removing users from group {id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(e),
        )


############################
# DeleteGroupById
############################


@router.delete("/id/{id}/delete", response_model=bool)
async def delete_group_by_id(id: str, user=Depends(get_admin_user)):
    try:
        result = Groups.delete_group_by_id(id)
        if result:
            return result
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT("Error deleting group"),
            )
    except Exception as e:
        log.exception(f"Error deleting group {id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(e),
        )
