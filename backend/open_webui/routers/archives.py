"""
User Archives API Router

Admin endpoints for managing user data archives.
"""

import logging
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse

from open_webui.constants import ERROR_MESSAGES
from open_webui.models.config import Config
from open_webui.models.user_archives import (
    UserArchives,
    UserArchiveModel,
    UserArchiveSummaryModel,
    CreateArchiveForm,
    UpdateArchiveForm,
)
from open_webui.services.archival import ArchiveService
from open_webui.soev import acting, identity
from open_webui.soev.client import SoevApiError, SoevClient
from open_webui.utils.auth import get_admin_user

from pydantic import BaseModel

log = logging.getLogger(__name__)

router = APIRouter()


async def requester_client(user) -> SoevClient:
    ref = identity.external_ref(user)
    if acting.requester_ref() != ref or not await acting.user_token(ref):
        raise SoevApiError(401, 'sign_in', 'Platform login required', 'identity:platform_login')
    return identity.build_client()


def require_content_access(user, owner_id: str) -> None:
    """Archived content is readable only by its owner."""
    if owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=ERROR_MESSAGES.ACCESS_PROHIBITED)


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


@router.get('/admin/config', response_model=ArchiveConfigResponse)
async def get_archive_config(
    request: Request,
    user=Depends(get_admin_user),
):
    """Get archive configuration settings."""
    config = await Config.get_many(
        'admin.enable_user_archival',
        'admin.default_archive_retention_days',
        'admin.enable_auto_archive_on_self_delete',
        'admin.auto_archive_retention_days',
    )
    return ArchiveConfigResponse(
        enable_user_archival=config.get('admin.enable_user_archival'),
        default_archive_retention_days=config.get('admin.default_archive_retention_days'),
        enable_auto_archive_on_self_delete=config.get('admin.enable_auto_archive_on_self_delete'),
        auto_archive_retention_days=config.get('admin.auto_archive_retention_days'),
    )


@router.post('/admin/config', response_model=ArchiveConfigResponse)
async def update_archive_config(
    request: Request,
    form_data: ArchiveConfigForm,
    user=Depends(get_admin_user),
):
    """Update archive configuration settings."""
    updates = {}
    if form_data.enable_user_archival is not None:
        updates['admin.enable_user_archival'] = form_data.enable_user_archival
    if form_data.default_archive_retention_days is not None:
        updates['admin.default_archive_retention_days'] = form_data.default_archive_retention_days
    if form_data.enable_auto_archive_on_self_delete is not None:
        updates['admin.enable_auto_archive_on_self_delete'] = form_data.enable_auto_archive_on_self_delete
    if form_data.auto_archive_retention_days is not None:
        updates['admin.auto_archive_retention_days'] = form_data.auto_archive_retention_days
    if updates:
        await Config.upsert(updates)

    config = await Config.get_many(
        'admin.enable_user_archival',
        'admin.default_archive_retention_days',
        'admin.enable_auto_archive_on_self_delete',
        'admin.auto_archive_retention_days',
    )
    return ArchiveConfigResponse(
        enable_user_archival=config.get('admin.enable_user_archival'),
        default_archive_retention_days=config.get('admin.default_archive_retention_days'),
        enable_auto_archive_on_self_delete=config.get('admin.enable_auto_archive_on_self_delete'),
        auto_archive_retention_days=config.get('admin.auto_archive_retention_days'),
    )


####################
# Endpoints
####################


@router.get('/', response_model=ArchiveListResponse)
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
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'

    if not await Config.get('admin.enable_user_archival'):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='User archival is not enabled',
        )

    archives = await UserArchives.get_archives(
        skip=skip,
        limit=limit,
        search=search,
    )
    total = await UserArchives.count_archives()

    return ArchiveListResponse(items=archives, total=total)


@router.get('/{archive_id}', response_model=UserArchiveModel)
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
    if not await Config.get('admin.enable_user_archival'):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='User archival is not enabled',
        )

    archive = await UserArchives.get_archive_by_id(archive_id)
    if not archive:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Archive not found',
        )
    require_content_access(user, archive.user_id)

    return archive


@router.get('/{archive_id}/export')
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
    if not await Config.get('admin.enable_user_archival'):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='User archival is not enabled',
        )

    archive = await UserArchives.get_archive_by_id(archive_id)
    if archive is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Archive not found',
        )
    require_content_access(user, archive.user_id)
    chats = archive.data.get('chats', [])

    # Return the raw chats array - same format as GET /api/v1/chats/all
    return JSONResponse(content=chats)


@router.post('/user/{user_id}', response_model=CreateArchiveResponse)
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
    if not await Config.get('admin.enable_user_archival'):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='User archival is not enabled',
        )
    # A snapshot of a living user's chats is a read of them; offboarding archives via DELETE /users/{id}.
    require_content_access(user, user_id)

    # Use default retention if not specified
    retention_days = form_data.retention_days
    if retention_days is None and not form_data.never_delete:
        retention_days = await Config.get('admin.default_archive_retention_days')

    result = await ArchiveService.create_archive(
        user_id=user_id,
        archived_by=user.id,
        reason=form_data.reason,
        retention_days=retention_days,
        never_delete=form_data.never_delete,
    )

    if not result.success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.errors[0] if result.errors else 'Failed to create archive',
        )

    return CreateArchiveResponse(
        success=result.success,
        archive_id=result.archive_id,
        stats=result.stats,
        errors=result.errors,
    )


@router.patch('/{archive_id}', response_model=UserArchiveSummaryModel)
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
    if not await Config.get('admin.enable_user_archival'):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='User archival is not enabled',
        )

    from open_webui.services.archival.resources import set_expiry

    client = await requester_client(user)
    current = await UserArchives.get_archive_by_id(archive_id)
    if current is None:
        raise HTTPException(status_code=404, detail='Archive not found')
    updated = current.model_copy(deep=True)
    if form_data.never_delete is not None:
        updated.never_delete = form_data.never_delete
    if form_data.retention_days is not None:
        updated.retention_days = form_data.retention_days
    if not updated.never_delete and updated.retention_days is None:
        updated.retention_days = await Config.get('admin.default_archive_retention_days')
        form_data = form_data.model_copy(update={'retention_days': updated.retention_days})
    updated.expires_at = None if updated.never_delete else updated.created_at + updated.retention_days * 86400
    if not updated.restored:
        await set_expiry(updated, client=client)
    archive = await UserArchives.update_archive(archive_id, form_data)
    if not archive:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Archive not found',
        )

    return UserArchiveSummaryModel.model_validate(archive)


@router.delete('/{archive_id}')
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
    if not await Config.get('admin.enable_user_archival'):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='User archival is not enabled',
        )

    from open_webui.services.archival.resources import delete_archive as delete_archived_data

    success = await delete_archived_data(archive_id, client=await requester_client(user))
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Archive not found',
        )

    return {'success': True}
