"""OneDrive Sync Router - Endpoints for OneDrive folder sync to Knowledge bases."""

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from fastapi.responses import RedirectResponse
from starlette.requests import Request
from pydantic import BaseModel
from typing import List, Literal
import logging

from open_webui.utils.auth import get_verified_user
from open_webui.models.users import UserModel
from open_webui.config import MICROSOFT_CLIENT_SECRET
from open_webui.services.sync.router import (
    SyncStatusResponse,
    RemoveSourceRequest,
    handle_sync_items_request,
    handle_get_sync_status,
    handle_cancel_sync,
    handle_remove_source,
    handle_list_synced_collections,
    handle_get_token_status,
    handle_revoke_token,
    complete_auth_callback,
    auth_callback_html,
    remove_files_for_source_generic,
    get_knowledge_or_raise,
)

log = logging.getLogger(__name__)
router = APIRouter()

_META_KEY = "onedrive_sync"
_PROVIDER_TYPE = "onedrive"
_FILE_ID_PREFIX = "onedrive-"
_CLEAR_DELTA_KEYS = ["delta_link", "folder_map", "folder_map_version"]


# ──────────────────────────────────────────────────────────────────────
# Provider-specific models
# ──────────────────────────────────────────────────────────────────────


class SyncItem(BaseModel):
    """A single OneDrive item (file or folder) to sync."""

    type: Literal["file", "folder"]
    drive_id: str
    item_id: str
    item_path: str
    name: str


class SyncItemsRequest(BaseModel):
    """Request to sync multiple OneDrive items to a Knowledge base."""

    knowledge_id: str
    items: List[SyncItem]
    access_token: str


# ──────────────────────────────────────────────────────────────────────
# Sync endpoints
# ──────────────────────────────────────────────────────────────────────


@router.post("/sync/items")
async def sync_items(
    request: SyncItemsRequest,
    fastapi_request: Request,
    background_tasks: BackgroundTasks,
    user: UserModel = Depends(get_verified_user),
):
    """Start OneDrive sync for multiple items (files and folders)."""
    new_sources = [
        {
            "type": item.type,
            "drive_id": item.drive_id,
            "item_id": item.item_id,
            "item_path": item.item_path,
            "name": item.name,
        }
        for item in request.items
    ]

    result = handle_sync_items_request(
        knowledge_id=request.knowledge_id,
        meta_key=_META_KEY,
        new_sources=new_sources,
        access_token=request.access_token,
        user=user,
        clear_delta_keys=_CLEAR_DELTA_KEYS,
    )

    background_tasks.add_task(
        _sync_items_background,
        knowledge_id=request.knowledge_id,
        sources=result["all_sources"],
        access_token=request.access_token,
        user_id=user.id,
        app=fastapi_request.app,
    )

    return {"message": "Sync started", "knowledge_id": request.knowledge_id}


async def _sync_items_background(
    knowledge_id: str,
    sources: List[dict],
    access_token: str,
    user_id: str,
    app,
):
    """Background task to sync multiple OneDrive items."""
    from open_webui.services.onedrive.sync_worker import OneDriveSyncWorker

    worker = OneDriveSyncWorker(
        knowledge_id=knowledge_id,
        sources=sources,
        access_token=access_token,
        user_id=user_id,
        app=app,
    )
    await worker.sync()


@router.get("/sync/{knowledge_id}")
async def get_sync_status(
    knowledge_id: str,
    user: UserModel = Depends(get_verified_user),
) -> SyncStatusResponse:
    """Get sync status for a Knowledge base."""
    return handle_get_sync_status(knowledge_id, _META_KEY, user)


@router.post("/sync/{knowledge_id}/cancel")
async def cancel_sync(
    knowledge_id: str,
    user: UserModel = Depends(get_verified_user),
):
    """Cancel an ongoing OneDrive sync for a Knowledge base."""
    return handle_cancel_sync(knowledge_id, _META_KEY, user)


def _remove_files_for_source(knowledge_id, item_id, source_to_remove):
    """Remove all files associated with a specific OneDrive source."""

    def _legacy_drive_id_match(file_meta, source):
        """Legacy fallback: match by drive_id for old files without source_item_id."""
        return file_meta.get("onedrive_drive_id") == source.get("drive_id")

    return remove_files_for_source_generic(
        knowledge_id=knowledge_id,
        source_item_id=item_id,
        file_id_prefix=_FILE_ID_PREFIX,
        get_drive_id_fn=_legacy_drive_id_match,
        source=source_to_remove,
    )


@router.post("/sync/{knowledge_id}/sources/remove")
async def remove_source(
    knowledge_id: str,
    request: RemoveSourceRequest,
    user: UserModel = Depends(get_verified_user),
):
    """Remove a source from a KB's OneDrive sync configuration."""
    return handle_remove_source(
        knowledge_id=knowledge_id,
        meta_key=_META_KEY,
        item_id=request.item_id,
        user=user,
        remove_files_fn=_remove_files_for_source,
    )


@router.get("/synced-collections")
async def list_synced_collections(
    user: UserModel = Depends(get_verified_user),
) -> List[dict]:
    """List all Knowledge bases with OneDrive sync enabled for current user."""
    return handle_list_synced_collections(_META_KEY, user)


# ──────────────────────────────────────────────────────────────────────
# Background Sync OAuth Endpoints
# ──────────────────────────────────────────────────────────────────────


@router.get("/auth/initiate")
async def initiate_auth(
    knowledge_id: str,
    request: Request,
    user: UserModel = Depends(get_verified_user),
):
    """Initiate OAuth auth code flow for background sync."""
    from open_webui.services.onedrive.auth import get_authorization_url

    if not MICROSOFT_CLIENT_SECRET.value:
        raise HTTPException(400, "OneDrive client secret not configured")

    knowledge = get_knowledge_or_raise(knowledge_id, user)

    redirect_uri = str(request.base_url).rstrip("/") + "/oauth/microsoft/callback"

    auth_url = get_authorization_url(
        user_id=user.id,
        knowledge_id=knowledge_id,
        redirect_uri=redirect_uri,
    )
    return RedirectResponse(auth_url)


async def handle_onedrive_auth_callback(request: Request):
    """
    Handle OAuth callback from Microsoft for OneDrive background sync.
    Called from the shared /oauth/microsoft/callback route in main.py.
    """
    from open_webui.services.onedrive.auth import (
        exchange_code_for_tokens,
        _pending_flows,
    )

    code = request.query_params.get("code")
    state = request.query_params.get("state")
    error = request.query_params.get("error")
    error_description = request.query_params.get("error_description")

    if error:
        _pending_flows.pop(state, None)
        return auth_callback_html(
            callback_type="onedrive_auth_callback",
            success=False,
            error=error_description or error,
        )

    if not code or not state:
        return auth_callback_html(
            callback_type="onedrive_auth_callback",
            success=False,
            error="Missing authorization code or state",
        )

    flow = _pending_flows.get(state)
    if not flow:
        return auth_callback_html(
            callback_type="onedrive_auth_callback",
            success=False,
            error="Invalid or expired authorization flow",
        )

    return await complete_auth_callback(
        code=code,
        state=state,
        flow=flow,
        provider_type=_PROVIDER_TYPE,
        meta_key=_META_KEY,
        callback_type="onedrive_auth_callback",
        exchange_code_fn=exchange_code_for_tokens,
    )


@router.get("/auth/token-status/{knowledge_id}")
async def get_token_status(
    knowledge_id: str,
    user: UserModel = Depends(get_verified_user),
):
    """Check if a stored token exists and is valid for a KB."""
    from open_webui.services.onedrive.auth import get_stored_token

    return handle_get_token_status(knowledge_id, _META_KEY, user, get_stored_token)


@router.post("/auth/revoke/{knowledge_id}")
async def revoke_token(
    knowledge_id: str,
    user: UserModel = Depends(get_verified_user),
):
    """Revoke and delete stored token for a KB."""
    from open_webui.services.onedrive.auth import delete_stored_token

    return handle_revoke_token(
        knowledge_id, _PROVIDER_TYPE, _META_KEY, user, delete_stored_token
    )
