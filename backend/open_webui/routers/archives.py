"""
User Archives API Router

Admin endpoints for managing user data archives.
"""

import logging
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse

from open_webui.models.user_archives import (
    UserArchives,
    UserArchiveModel,
    UserArchiveSummaryModel,
    CreateArchiveForm,
    UpdateArchiveForm,
)
from open_webui.services.archival import ArchiveService
from open_webui.utils.auth import get_admin_user

from pydantic import BaseModel

log = logging.getLogger(__name__)

router = APIRouter()


####################
# Response Models
####################


class ArchiveListResponse(BaseModel):
    items: List[UserArchiveSummaryModel]
    total: int


class CreateArchiveResponse(BaseModel):
    success: bool
    archive_id: Optional[str] = None
    stats: dict
    errors: List[str]


####################
# Admin Config Endpoints (must come before /{archive_id} routes)
####################


class ArchiveConfigResponse(BaseModel):
    enable_user_archival: bool
    default_archive_retention_days: int
    enable_auto_archive_on_self_delete: bool
    auto_archive_retention_days: int


class ArchiveConfigForm(BaseModel):
    enable_user_archival: Optional[bool] = None
    default_archive_retention_days: Optional[int] = None
    enable_auto_archive_on_self_delete: Optional[bool] = None
    auto_archive_retention_days: Optional[int] = None


@router.get("/admin/config", response_model=ArchiveConfigResponse)
async def get_archive_config(
    request: Request,
    user=Depends(get_admin_user),
):
    """Get archive configuration settings."""
    return ArchiveConfigResponse(
        enable_user_archival=request.app.state.config.ENABLE_USER_ARCHIVAL,
        default_archive_retention_days=request.app.state.config.DEFAULT_ARCHIVE_RETENTION_DAYS,
        enable_auto_archive_on_self_delete=request.app.state.config.ENABLE_AUTO_ARCHIVE_ON_SELF_DELETE,
        auto_archive_retention_days=request.app.state.config.AUTO_ARCHIVE_RETENTION_DAYS,
    )


@router.post("/admin/config", response_model=ArchiveConfigResponse)
async def update_archive_config(
    request: Request,
    form_data: ArchiveConfigForm,
    user=Depends(get_admin_user),
):
    """Update archive configuration settings."""
    if form_data.enable_user_archival is not None:
        request.app.state.config.ENABLE_USER_ARCHIVAL = form_data.enable_user_archival
    if form_data.default_archive_retention_days is not None:
        request.app.state.config.DEFAULT_ARCHIVE_RETENTION_DAYS = form_data.default_archive_retention_days
    if form_data.enable_auto_archive_on_self_delete is not None:
        request.app.state.config.ENABLE_AUTO_ARCHIVE_ON_SELF_DELETE = form_data.enable_auto_archive_on_self_delete
    if form_data.auto_archive_retention_days is not None:
        request.app.state.config.AUTO_ARCHIVE_RETENTION_DAYS = form_data.auto_archive_retention_days

    return ArchiveConfigResponse(
        enable_user_archival=request.app.state.config.ENABLE_USER_ARCHIVAL,
        default_archive_retention_days=request.app.state.config.DEFAULT_ARCHIVE_RETENTION_DAYS,
        enable_auto_archive_on_self_delete=request.app.state.config.ENABLE_AUTO_ARCHIVE_ON_SELF_DELETE,
        auto_archive_retention_days=request.app.state.config.AUTO_ARCHIVE_RETENTION_DAYS,
    )


####################
# Endpoints
####################


@router.get("/", response_model=ArchiveListResponse)
async def get_archives(
    request: Request,
    response: Response,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    search: Optional[str] = Query(None),
    user=Depends(get_admin_user),
):
    """
    List all user archives.

    - Requires admin role
    - Requires ENABLE_USER_ARCHIVAL to be enabled
    """
    # Prevent caching to ensure fresh data
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"

    if not request.app.state.config.ENABLE_USER_ARCHIVAL:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User archival is not enabled",
        )

    archives = UserArchives.get_archives(
        skip=skip,
        limit=limit,
        search=search,
    )
    total = UserArchives.count_archives()

    return ArchiveListResponse(items=archives, total=total)


@router.get("/{archive_id}", response_model=UserArchiveModel)
async def get_archive(
    request: Request,
    archive_id: str,
    user=Depends(get_admin_user),
):
    """
    Get a specific archive with full data.

    - Requires admin role
    - Returns complete chat history in data field
    """
    if not request.app.state.config.ENABLE_USER_ARCHIVAL:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User archival is not enabled",
        )

    archive = UserArchives.get_archive_by_id(archive_id)
    if not archive:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Archive not found",
        )

    return archive


@router.get("/{archive_id}/export")
async def export_archive_chats(
    request: Request,
    archive_id: str,
    user=Depends(get_admin_user),
):
    """
    Export archive chats in native Open WebUI format.

    Returns the chats array in the same format as the user's
    Settings > Data Controls > Export Chats function.

    This export can be directly imported by any user using
    Settings > Data Controls > Import Chats.
    """
    if not request.app.state.config.ENABLE_USER_ARCHIVAL:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User archival is not enabled",
        )

    chats = ArchiveService.get_exportable_chats(archive_id)
    if chats is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Archive not found",
        )

    # Return the raw chats array - same format as GET /api/v1/chats/all
    return JSONResponse(content=chats)


@router.post("/user/{user_id}", response_model=CreateArchiveResponse)
async def create_user_archive(
    request: Request,
    user_id: str,
    form_data: CreateArchiveForm,
    user=Depends(get_admin_user),
):
    """
    Create an archive of a user's data.

    - Requires admin role
    - Does NOT delete the user (use DELETE /users/{id} separately)
    - Archives chats in native export format (can be imported by new user)
    """
    if not request.app.state.config.ENABLE_USER_ARCHIVAL:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User archival is not enabled",
        )

    # Use default retention if not specified
    retention_days = form_data.retention_days
    if retention_days is None and not form_data.never_delete:
        retention_days = request.app.state.config.DEFAULT_ARCHIVE_RETENTION_DAYS

    result = ArchiveService.create_archive(
        user_id=user_id,
        archived_by=user.id,
        reason=form_data.reason,
        retention_days=retention_days,
        never_delete=form_data.never_delete,
    )

    if not result.success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.errors[0] if result.errors else "Failed to create archive",
        )

    return CreateArchiveResponse(
        success=result.success,
        archive_id=result.archive_id,
        stats=result.stats,
        errors=result.errors,
    )


@router.patch("/{archive_id}", response_model=UserArchiveSummaryModel)
async def update_archive(
    request: Request,
    archive_id: str,
    form_data: UpdateArchiveForm,
    user=Depends(get_admin_user),
):
    """
    Update archive retention settings.

    - Can update: reason, retention_days, never_delete
    """
    if not request.app.state.config.ENABLE_USER_ARCHIVAL:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User archival is not enabled",
        )

    archive = UserArchives.update_archive(archive_id, form_data)
    if not archive:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Archive not found",
        )

    return UserArchiveSummaryModel.model_validate(archive)


@router.delete("/{archive_id}")
async def delete_archive(
    request: Request,
    archive_id: str,
    user=Depends(get_admin_user),
):
    """
    Permanently delete an archive.

    - This action cannot be undone
    - Use for early cleanup before retention expires
    """
    if not request.app.state.config.ENABLE_USER_ARCHIVAL:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User archival is not enabled",
        )

    success = UserArchives.delete_archive(archive_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Archive not found",
        )

    return {"success": True}
