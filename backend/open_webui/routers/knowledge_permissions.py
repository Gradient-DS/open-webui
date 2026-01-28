"""
Knowledge Permissions Router

Handles permission validation endpoints for knowledge bases.
Mounted alongside the main knowledge router to avoid modifying upstream code.
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from open_webui.models.knowledge import Knowledges
from open_webui.utils.access_control import has_access
from open_webui.utils.auth import get_verified_user
from open_webui.constants import ERROR_MESSAGES
from open_webui.services.permissions.validator import (
    SharingValidator,
    SharingRecommendation,
)
from open_webui.services.permissions.provider import UserAccessStatus

log = logging.getLogger(__name__)

router = APIRouter()


####################################
# Request/Response Models
####################################


class ShareValidationRequest(BaseModel):
    """Request to validate sharing a KB with users/groups."""

    user_ids: List[str] = []
    group_ids: List[str] = []


class ShareValidationResponse(BaseModel):
    """Response with sharing validation results."""

    can_share: bool
    can_share_to_users: List[str]
    cannot_share_to_users: List[str]
    blocking_resources: dict
    recommendations: List[SharingRecommendation]
    source_restricted: bool


class UsersReadyForAccessResponse(BaseModel):
    """Response with users ready for KB access."""

    users: List[UserAccessStatus]


class FileAdditionValidationRequest(BaseModel):
    """Request to validate adding files to a KB."""

    file_ids: List[str] = []


class FileAdditionConflictResponse(BaseModel):
    """Response with file addition conflict details."""

    has_conflict: bool
    kb_is_public: bool
    users_without_access: List[str]
    user_details: List[SharingRecommendation]
    source_type: str
    grant_access_url: Optional[str]


####################################
# Endpoints
####################################


@router.post("/{id}/validate-share", response_model=ShareValidationResponse)
async def validate_knowledge_share(
    id: str,
    request: Request,
    form_data: ShareValidationRequest,
    user=Depends(get_verified_user),
):
    """
    Validate sharing a knowledge base with specified users/groups.

    Returns which users can/cannot be shared with based on source permissions.
    """
    knowledge = Knowledges.get_knowledge_by_id(id)
    if not knowledge:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge not found",
        )

    # Check user has write access
    if (
        knowledge.user_id != user.id
        and not has_access(user.id, "write", knowledge.access_control)
        and user.role != "admin"
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    validator = SharingValidator()
    result = await validator.validate_knowledge_share(
        id, form_data.user_ids, form_data.group_ids
    )

    return ShareValidationResponse(**result.model_dump())


@router.get("/{id}/users-ready-for-access", response_model=UsersReadyForAccessResponse)
async def get_users_ready_for_access(
    id: str,
    request: Request,
    user=Depends(get_verified_user),
):
    """
    Get users who have source access but haven't been granted KB access.

    Used for the "Ready to Add" section in the Access tab.
    """
    knowledge = Knowledges.get_knowledge_by_id(id)
    if not knowledge:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge not found",
        )

    # Check user has write access
    if (
        knowledge.user_id != user.id
        and not has_access(user.id, "write", knowledge.access_control)
        and user.role != "admin"
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    validator = SharingValidator()
    users = await validator.get_users_with_source_access(id)

    return UsersReadyForAccessResponse(users=users)


@router.post("/{id}/validate-file-addition", response_model=FileAdditionConflictResponse)
async def validate_file_addition(
    id: str,
    request: Request,
    form_data: FileAdditionValidationRequest,
    user=Depends(get_verified_user),
):
    """
    Validate adding files to a knowledge base.

    Returns conflict info if the KB is shared more broadly than
    the source permissions of the files being added.
    """
    knowledge = Knowledges.get_knowledge_by_id(id)
    if not knowledge:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge not found",
        )

    # Check user has write access
    if (
        knowledge.user_id != user.id
        and not has_access(user.id, "write", knowledge.access_control)
        and user.role != "admin"
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    validator = SharingValidator()
    result = await validator.validate_file_addition(id, form_data.file_ids)

    return FileAdditionConflictResponse(**result.model_dump())
