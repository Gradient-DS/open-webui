"""Google Drive Sync Router - Endpoints for Google Drive folder sync to Knowledge bases."""

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from fastapi.responses import RedirectResponse
from starlette.requests import Request
from pydantic import BaseModel
from typing import List, Literal, Optional
import logging

from open_webui.utils.auth import get_verified_user
from open_webui.models.users import UserModel
from open_webui.config import GOOGLE_CLIENT_SECRET
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

_META_KEY = 'google_drive_sync'
_PROVIDER_TYPE = 'google_drive'
_FILE_ID_PREFIX = 'googledrive-'
_CLEAR_DELTA_KEYS = ['page_token', 'folder_map']


# ──────────────────────────────────────────────────────────────────────
# Provider-specific models
# ──────────────────────────────────────────────────────────────────────


class SyncItem(BaseModel):
    """A single Google Drive item (file or folder) to sync."""

    type: Literal['file', 'folder']
    item_id: str
    item_path: str
    name: str


class SyncItemsRequest(BaseModel):
    """Request to sync multiple Google Drive items to a Knowledge base."""

    knowledge_id: str
    items: List[SyncItem]
    access_token: Optional[str] = None


# ──────────────────────────────────────────────────────────────────────
# Sync endpoints
# ──────────────────────────────────────────────────────────────────────


@router.post('/sync/items')
async def sync_items(
    request: SyncItemsRequest,
    fastapi_request: Request,
    background_tasks: BackgroundTasks,
    user: UserModel = Depends(get_verified_user),
):
    """Start Google Drive sync for multiple items (files and folders)."""
    # If no access_token provided, get one from the stored session
    access_token = request.access_token
    if not access_token:
        from open_webui.services.google_drive.token_refresh import (
            get_valid_access_token,
        )

        access_token = await get_valid_access_token(user.id, request.knowledge_id)
        if not access_token:
            raise HTTPException(401, 'No valid Google Drive token. Please re-authorize.')

    new_sources = [
        {
            'type': item.type,
            'item_id': item.item_id,
            'item_path': item.item_path,
            'name': item.name,
        }
        for item in request.items
    ]

    result = await handle_sync_items_request(
        knowledge_id=request.knowledge_id,
        meta_key=_META_KEY,
        new_sources=new_sources,
        access_token=access_token,
        user=user,
        clear_delta_keys=_CLEAR_DELTA_KEYS,
    )

    background_tasks.add_task(
        _sync_items_background,
        knowledge_id=request.knowledge_id,
        sources=result['all_sources'],
        access_token=access_token,
        user_id=user.id,
        app=fastapi_request.app,
    )

    return {'message': 'Sync started', 'knowledge_id': request.knowledge_id}


async def _sync_items_background(
    knowledge_id: str,
    sources: List[dict],
    access_token: str,
    user_id: str,
    app,
):
    """Background task to sync multiple Google Drive items."""
    from open_webui.services.google_drive.sync_worker import GoogleDriveSyncWorker

    worker = GoogleDriveSyncWorker(
        knowledge_id=knowledge_id,
        sources=sources,
        access_token=access_token,
        user_id=user_id,
        app=app,
    )
    await worker.sync()


@router.get('/sync/{knowledge_id}')
async def get_sync_status(
    knowledge_id: str,
    user: UserModel = Depends(get_verified_user),
) -> SyncStatusResponse:
    """Get sync status for a Knowledge base."""
    return await handle_get_sync_status(knowledge_id, _META_KEY, user)


@router.post('/sync/{knowledge_id}/cancel')
async def cancel_sync(
    knowledge_id: str,
    user: UserModel = Depends(get_verified_user),
):
    """Cancel an ongoing Google Drive sync for a Knowledge base."""
    return await handle_cancel_sync(knowledge_id, _META_KEY, user)


async def _remove_files_for_source(knowledge_id, item_id, source_to_remove):
    """Remove all files associated with a specific Google Drive source."""
    return await remove_files_for_source_generic(
        knowledge_id=knowledge_id,
        source_item_id=item_id,
        file_id_prefix=_FILE_ID_PREFIX,
    )


@router.post('/sync/{knowledge_id}/sources/remove')
async def remove_source(
    knowledge_id: str,
    request: RemoveSourceRequest,
    user: UserModel = Depends(get_verified_user),
):
    """Remove a source from a KB's Google Drive sync configuration."""
    return await handle_remove_source(
        knowledge_id=knowledge_id,
        meta_key=_META_KEY,
        item_id=request.item_id,
        user=user,
        remove_files_fn=_remove_files_for_source,
    )


@router.get('/synced-collections')
async def list_synced_collections(
    user: UserModel = Depends(get_verified_user),
) -> List[dict]:
    """List all Knowledge bases with Google Drive sync enabled for current user."""
    return await handle_list_synced_collections(_META_KEY, user)


# ──────────────────────────────────────────────────────────────────────
# Background Sync OAuth Endpoints
# ──────────────────────────────────────────────────────────────────────


@router.get('/auth/access-token')
async def get_access_token(
    user: UserModel = Depends(get_verified_user),
):
    """Get a valid Google Drive access token, refreshing if needed.

    Used by the frontend for the Google Drive picker and file downloads.
    Returns 401 if no stored token exists (user must authorize first).
    """
    from open_webui.services.google_drive.token_refresh import get_valid_access_token

    if not GOOGLE_CLIENT_SECRET.value:
        raise HTTPException(400, 'Google client secret not configured')

    token = await get_valid_access_token(user.id, knowledge_id='__picker__')
    if not token:
        raise HTTPException(
            status_code=401,
            detail='No stored Google Drive token. Authorization required.',
        )
    return {'access_token': token}


@router.get('/auth/initiate')
async def initiate_auth(
    request: Request,
    user: UserModel = Depends(get_verified_user),
    knowledge_id: Optional[str] = None,
):
    """Initiate OAuth auth code flow for Google Drive.

    knowledge_id is optional — if provided, validates KB ownership.
    Used for both knowledge base background sync auth and general picker auth.
    """
    from open_webui.services.google_drive.auth import get_authorization_url

    if not GOOGLE_CLIENT_SECRET.value:
        raise HTTPException(400, 'Google client secret not configured')

    if knowledge_id:
        await get_knowledge_or_raise(knowledge_id, user)

    redirect_uri = str(request.base_url).rstrip('/') + '/oauth/google/callback'
    log.info('OAuth initiate: base_url=%s, redirect_uri=%s', request.base_url, redirect_uri)

    auth_url = get_authorization_url(
        user_id=user.id,
        knowledge_id=knowledge_id or '__general__',
        redirect_uri=redirect_uri,
    )
    return RedirectResponse(auth_url)


async def handle_google_drive_auth_callback(request: Request):
    """
    Handle OAuth callback from Google for Google Drive background sync.
    Called from the shared /oauth/google/callback route in main.py.
    """
    from open_webui.services.google_drive.auth import (
        exchange_code_for_tokens,
        get_pending_flow,
        remove_pending_flow,
    )

    code = request.query_params.get('code')
    state = request.query_params.get('state')
    error = request.query_params.get('error')
    error_description = request.query_params.get('error_description')

    if error:
        if state:
            remove_pending_flow(state)
        return auth_callback_html(
            callback_type='google_drive_auth_callback',
            success=False,
            error=error_description or error,
        )

    if not code or not state:
        return auth_callback_html(
            callback_type='google_drive_auth_callback',
            success=False,
            error='Missing authorization code or state',
        )

    flow = get_pending_flow(state)
    if not flow:
        return auth_callback_html(
            callback_type='google_drive_auth_callback',
            success=False,
            error='Invalid or expired authorization flow',
        )

    return await complete_auth_callback(
        code=code,
        state=state,
        flow=flow,
        provider_type=_PROVIDER_TYPE,
        meta_key=_META_KEY,
        callback_type='google_drive_auth_callback',
        exchange_code_fn=exchange_code_for_tokens,
    )


@router.get('/auth/token-status/{knowledge_id}')
async def get_token_status(
    knowledge_id: str,
    user: UserModel = Depends(get_verified_user),
):
    """Check if a stored token exists and is valid for a KB."""
    from open_webui.services.google_drive.auth import get_stored_token

    return await handle_get_token_status(knowledge_id, _META_KEY, user, get_stored_token)


@router.post('/auth/revoke/{knowledge_id}')
async def revoke_token(
    knowledge_id: str,
    user: UserModel = Depends(get_verified_user),
):
    """Revoke and delete stored token for a KB."""
    from open_webui.services.google_drive.auth import delete_stored_token

    return await handle_revoke_token(knowledge_id, _PROVIDER_TYPE, _META_KEY, user, delete_stored_token)
