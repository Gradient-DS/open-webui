"""Confluence Sync Router — endpoints for Confluence space/page sync to Knowledge bases."""

import logging
from typing import List, Literal, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query
from fastapi.responses import RedirectResponse
from starlette.requests import Request
from pydantic import BaseModel

from open_webui.utils.auth import get_verified_user
from open_webui.models.users import UserModel
from open_webui.config import (
    CONFLUENCE_OAUTH_CLIENT_ID,
    CONFLUENCE_OAUTH_CLIENT_SECRET,
)
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

_META_KEY = 'confluence_sync'
_PROVIDER_TYPE = 'confluence'
_FILE_ID_PREFIX = 'confluence-'
_CLEAR_DELTA_KEYS = ['page_map', 'last_synced_version']


# ──────────────────────────────────────────────────────────────────────
# Provider-specific models
# ──────────────────────────────────────────────────────────────────────


class SyncItem(BaseModel):
    """A single Confluence item (whole space, or one page with optional subtree)."""

    type: Literal['space', 'page']
    cloud_id: str
    space_id: Optional[str] = None
    space_key: Optional[str] = None
    site_url: Optional[str] = None
    item_id: str  # space_id for type='space', page_id for type='page'
    item_path: str
    name: str
    include_descendants: bool = True


class SyncItemsRequest(BaseModel):
    """Request to sync multiple Confluence items to a Knowledge base."""

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
    """Start Confluence sync for multiple items (spaces and/or pages)."""
    # Reject any cloud_id the user can't actually access — prevents a malformed
    # body from pointing the worker at an arbitrary Atlassian site.
    from open_webui.services.confluence.auth import get_stored_sites

    allowed_cloud_ids = {s.get('cloud_id') for s in get_stored_sites(user.id)}
    for item in request.items:
        if item.cloud_id not in allowed_cloud_ids:
            raise HTTPException(403, f'Confluence site not accessible: {item.cloud_id}')

    access_token = request.access_token
    if not access_token:
        from open_webui.services.confluence.token_refresh import (
            get_valid_access_token,
        )

        access_token = await get_valid_access_token(user.id, request.knowledge_id)
        if not access_token:
            raise HTTPException(401, 'No valid Confluence token. Please re-authorize.')

    # base_worker routes type=='folder' → _collect_folder_files, else → _collect_single_file.
    # Spaces and page-subtrees both enumerate many pages → they're "folders".
    # A single page with include_descendants=False is the only file-like case.
    def _translate_type(item: SyncItem) -> str:
        if item.type == 'space':
            return 'folder'
        if item.type == 'page' and item.include_descendants:
            return 'folder'
        return 'file'

    new_sources = [
        {
            'type': _translate_type(item),
            'confluence_type': item.type,  # original: 'space' | 'page'
            'cloud_id': item.cloud_id,
            'space_id': item.space_id,
            'space_key': item.space_key,
            'site_url': item.site_url,
            'item_id': item.item_id,
            'item_path': item.item_path,
            'name': item.name,
            'include_descendants': item.include_descendants,
        }
        for item in request.items
    ]

    result = handle_sync_items_request(
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
    """Background task to sync multiple Confluence items."""
    from open_webui.services.confluence.sync_worker import ConfluenceSyncWorker

    worker = ConfluenceSyncWorker(
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
    return handle_get_sync_status(knowledge_id, _META_KEY, user)


@router.post('/sync/{knowledge_id}/cancel')
async def cancel_sync(
    knowledge_id: str,
    user: UserModel = Depends(get_verified_user),
):
    """Cancel an ongoing Confluence sync for a Knowledge base."""
    return handle_cancel_sync(knowledge_id, _META_KEY, user)


def _remove_files_for_source(knowledge_id, item_id, source_to_remove):
    return remove_files_for_source_generic(
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
    """Remove a source from a KB's Confluence sync configuration."""
    return handle_remove_source(
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
    """List all Knowledge bases with Confluence sync enabled for current user."""
    return handle_list_synced_collections(_META_KEY, user)


# ──────────────────────────────────────────────────────────────────────
# OAuth endpoints
# ──────────────────────────────────────────────────────────────────────


@router.get('/auth/initiate')
async def initiate_auth(
    request: Request,
    user: UserModel = Depends(get_verified_user),
    knowledge_id: Optional[str] = None,
):
    """Initiate OAuth auth code flow for Confluence."""
    from open_webui.services.confluence.auth import get_authorization_url

    if not CONFLUENCE_OAUTH_CLIENT_ID.value:
        raise HTTPException(400, 'Confluence client ID not configured')
    if not CONFLUENCE_OAUTH_CLIENT_SECRET.value:
        raise HTTPException(400, 'Confluence client secret not configured')

    if knowledge_id:
        get_knowledge_or_raise(knowledge_id, user)

    redirect_uri = str(request.base_url).rstrip('/') + '/oauth/atlassian/callback'
    log.info('Confluence OAuth initiate: base_url=%s, redirect_uri=%s', request.base_url, redirect_uri)

    auth_url = get_authorization_url(
        user_id=user.id,
        knowledge_id=knowledge_id or '__general__',
        redirect_uri=redirect_uri,
    )
    return RedirectResponse(auth_url)


async def handle_confluence_auth_callback(request: Request):
    """
    Handle OAuth callback from Atlassian for Confluence.
    Called from the shared /oauth/atlassian/callback route in main.py.
    """
    from open_webui.services.confluence.auth import (
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
            callback_type='confluence_auth_callback',
            success=False,
            error=error_description or error,
        )

    if not code or not state:
        return auth_callback_html(
            callback_type='confluence_auth_callback',
            success=False,
            error='Missing authorization code or state',
        )

    flow = get_pending_flow(state)
    if not flow:
        return auth_callback_html(
            callback_type='confluence_auth_callback',
            success=False,
            error='Invalid or expired authorization flow',
        )

    return await complete_auth_callback(
        code=code,
        state=state,
        flow=flow,
        provider_type=_PROVIDER_TYPE,
        meta_key=_META_KEY,
        callback_type='confluence_auth_callback',
        exchange_code_fn=exchange_code_for_tokens,
    )


@router.get('/auth/token-status/{knowledge_id}')
async def get_token_status(
    knowledge_id: str,
    user: UserModel = Depends(get_verified_user),
):
    """Check if a stored token exists and is valid for a KB."""
    from open_webui.services.confluence.auth import get_stored_token

    return handle_get_token_status(knowledge_id, _META_KEY, user, get_stored_token)


@router.post('/auth/revoke/{knowledge_id}')
async def revoke_token(
    knowledge_id: str,
    user: UserModel = Depends(get_verified_user),
):
    """Revoke and delete stored Confluence token for a user's KBs."""
    from open_webui.services.confluence.auth import delete_stored_token

    return handle_revoke_token(knowledge_id, _PROVIDER_TYPE, _META_KEY, user, delete_stored_token)


# ──────────────────────────────────────────────────────────────────────
# Picker proxy endpoints
# ──────────────────────────────────────────────────────────────────────


async def _picker_client(user: UserModel):
    """Build a ConfluenceClient-like helper for picker-time browsing.

    Returns a tuple of (token, sites). Raises 401 if no valid token.
    """
    from open_webui.services.confluence.token_refresh import get_valid_access_token
    from open_webui.services.confluence.auth import get_stored_sites

    token = await get_valid_access_token(user.id, knowledge_id='__picker__')
    if not token:
        raise HTTPException(401, 'No valid Confluence token. Please re-authorize.')

    return token, get_stored_sites(user.id)


def _pick_site(sites: list, cloud_id: str) -> dict:
    for s in sites:
        if s.get('cloud_id') == cloud_id:
            return s
    raise HTTPException(404, 'Unknown Confluence site (cloud_id)')


@router.get('/browse/sites')
async def browse_sites(user: UserModel = Depends(get_verified_user)):
    """List the Confluence sites accessible to the authenticated user."""
    _, sites = await _picker_client(user)
    return {
        'sites': [
            {
                'cloud_id': s.get('cloud_id'),
                'url': s.get('url'),
                'name': s.get('name'),
            }
            for s in sites
        ],
    }


@router.get('/browse/spaces')
async def browse_spaces(
    cloud_id: str = Query(...),
    cursor: Optional[str] = None,
    user: UserModel = Depends(get_verified_user),
):
    """List spaces for a given Confluence site."""
    from open_webui.services.confluence.confluence_client import ConfluenceClient
    from open_webui.services.confluence.token_refresh import get_valid_access_token

    token, sites = await _picker_client(user)
    site = _pick_site(sites, cloud_id)

    async def _refresh():
        return await get_valid_access_token(user.id, knowledge_id='__picker__')

    client = ConfluenceClient(access_token=token, cloud_id=cloud_id, token_provider=_refresh)
    try:
        try:
            results, next_cursor = await client.list_spaces(cursor=cursor)
        except httpx.HTTPStatusError as e:
            raise HTTPException(e.response.status_code, f'Confluence error: {e.response.status_code}')

        return {
            'site_url': site.get('url'),
            'spaces': [
                {
                    'id': s.get('id'),
                    'key': s.get('key'),
                    'name': s.get('name'),
                    'type': s.get('type'),
                    'status': s.get('status'),
                    'homepage_id': s.get('homepageId'),
                }
                for s in results
            ],
            'next_cursor': next_cursor,
        }
    finally:
        await client.close()


@router.get('/browse/pages')
async def browse_pages(
    cloud_id: str = Query(...),
    space_id: Optional[str] = Query(None),
    parent_id: Optional[str] = Query(None),
    cursor: Optional[str] = None,
    user: UserModel = Depends(get_verified_user),
):
    """List top-level pages in a space or children of a given page.

    Provide either space_id (to list the space's root pages) or parent_id
    (to expand a page's children).
    """
    from open_webui.services.confluence.confluence_client import ConfluenceClient
    from open_webui.services.confluence.token_refresh import get_valid_access_token

    if not space_id and not parent_id:
        raise HTTPException(400, 'Provide either space_id or parent_id')

    token, sites = await _picker_client(user)
    site = _pick_site(sites, cloud_id)

    async def _refresh():
        return await get_valid_access_token(user.id, knowledge_id='__picker__')

    client = ConfluenceClient(access_token=token, cloud_id=cloud_id, token_provider=_refresh)
    try:
        try:
            if parent_id:
                results, next_cursor = await client.list_page_children(parent_id, cursor=cursor)
            else:
                results, next_cursor = await client.list_pages_in_space(space_id, cursor=cursor)
        except httpx.HTTPStatusError as e:
            raise HTTPException(e.response.status_code, f'Confluence error: {e.response.status_code}')

        return {
            'site_url': site.get('url'),
            'pages': [
                {
                    'id': p.get('id'),
                    'title': p.get('title'),
                    'status': p.get('status'),
                    'space_id': p.get('spaceId'),
                    'parent_id': p.get('parentId'),
                }
                for p in results
            ],
            'next_cursor': next_cursor,
        }
    finally:
        await client.close()
