"""
User Archives API Router

Admin endpoints for managing user data archives.
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse
from open_webui.constants import ERROR_MESSAGES
from open_webui.models.config import Config
from open_webui.models.user_archives import (
    CreateArchiveForm,
    UpdateArchiveForm,
    UserArchiveModel,
    UserArchives,
    UserArchiveSummaryModel,
)
from open_webui.services.archival import ArchiveService
from open_webui.soev import acting, identity
from open_webui.soev.client import SoevApiError, SoevClient
from open_webui.soev.thread_content import refuse_local_content
from open_webui.utils.auth import get_admin_user, get_verified_user
from pydantic import BaseModel

log = logging.getLogger(__name__)

router = APIRouter()


async def require_archive_contract(user=Depends(get_verified_user)) -> None:
    refuse_local_content()


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


####################
# Endpoints
####################
