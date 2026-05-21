"""Confluence Sync Router — endpoints for Confluence space/page sync to Knowledge bases."""

import asyncio
import logging
from typing import List, Literal, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query
from fastapi.responses import RedirectResponse
from starlette.requests import Request
from pydantic import BaseModel

from open_webui.utils.auth import get_verified_user, get_admin_user
from open_webui.models.users import UserModel, Users
from open_webui.models.knowledge import Knowledges, KnowledgeForm
from open_webui.models.access_grants import AccessGrants
from open_webui.config import (
    CONFLUENCE_OAUTH_CLIENT_ID,
    CONFLUENCE_OAUTH_CLIENT_SECRET,
    CONFLUENCE_SITE_URL,
    CONFLUENCE_BASIC_AUTH_USERNAME,
    CONFLUENCE_BASIC_AUTH_API_TOKEN,
    CONFLUENCE_KB_MODE,
    CONFLUENCE_SHARED_KB_OWNER_ID,
)
from open_webui.services.confluence.confluence_client import ConfluenceClient
from open_webui.services.confluence.basic_auth import (
    BASIC_AUTH_SENTINEL,
    basic_auth_configured,
    build_basic_client,
    get_basic_site,
    resolve_auth_mode,
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


class ConfluenceTestConnectionForm(BaseModel):
    """Optional credential overrides for the basic-auth test-connection probe.

    Any field left blank falls back to the stored config, so an admin can
    test typed-but-unsaved values or re-test the saved credential.
    """

    site_url: Optional[str] = None
    username: Optional[str] = None
    api_token: Optional[str] = None


class ConfluenceSpaceSelection(BaseModel):
    """One Confluence space an admin opted into the shared knowledge base."""

    id: str
    key: Optional[str] = None
    name: Optional[str] = None
    cloud_id: Optional[str] = None


class ConfluenceProvisionForm(BaseModel):
    """Shared-KB provisioning request — the admin-selected spaces to sync.

    Selection is opt-in: only the listed spaces are synced into the shared
    KB. An empty list provisions the KB shell but syncs nothing until the
    admin picks at least one space.
    """

    spaces: List[ConfluenceSpaceSelection] = []


def _stamp_auth_mode(knowledge_id: str, mode: str) -> None:
    """Persist the KB's resolved auth mode into its confluence_sync meta.

    A KB keeps the mode it was created under even if the global default
    later flips — so existing OAuth KBs are unaffected by switching the
    tenant to basic auth and vice versa.
    """
    kb = Knowledges.get_knowledge_by_id(knowledge_id)
    if not kb:
        return
    meta = kb.meta or {}
    sync_info = meta.get(_META_KEY, {})
    if sync_info.get('auth_mode') != mode:
        sync_info['auth_mode'] = mode
        meta[_META_KEY] = sync_info
        Knowledges.update_knowledge_meta_by_id(knowledge_id, meta)


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
    mode = resolve_auth_mode(request.knowledge_id)

    # Reject any cloud_id the caller can't actually access — prevents a
    # malformed body from pointing the worker at an arbitrary Atlassian site.
    if mode == 'basic':
        basic_site = get_basic_site()
        if not basic_site:
            raise HTTPException(400, 'Confluence basic auth is not configured.')
        allowed_cloud_ids = {basic_site['cloud_id']}
    else:
        from open_webui.services.confluence.auth import get_stored_sites

        allowed_cloud_ids = {s.get('cloud_id') for s in get_stored_sites(user.id)}

    for item in request.items:
        if item.cloud_id not in allowed_cloud_ids:
            raise HTTPException(403, f'Confluence site not accessible: {item.cloud_id}')

    access_token = request.access_token
    if not access_token:
        if mode == 'basic':
            # basic mode has no token — the worker reads the service
            # credential from config; pass a non-empty placeholder.
            access_token = BASIC_AUTH_SENTINEL
        else:
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

    # Stamp the KB's auth mode so it stays stable if the global default flips.
    _stamp_auth_mode(request.knowledge_id, mode)

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

    auth_url = await get_authorization_url(
        request=request,
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
            await remove_pending_flow(request, state)
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

    flow = await get_pending_flow(request, state)
    if not flow:
        return auth_callback_html(
            callback_type='confluence_auth_callback',
            success=False,
            error='Invalid or expired authorization flow',
        )

    return await complete_auth_callback(
        request=request,
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
    """Check if a stored token exists and is valid for a KB.

    In basic mode there is no per-user OAuth token — report 'connected'
    whenever the global service credential is configured, so the picker
    can proceed without an OAuth authorization step.
    """
    if resolve_auth_mode(knowledge_id) == 'basic':
        get_knowledge_or_raise(knowledge_id, user)
        configured = basic_auth_configured()
        return {
            'has_token': configured,
            'is_expired': False,
            'needs_reauth': not configured,
        }

    from open_webui.services.confluence.auth import get_stored_token

    return handle_get_token_status(knowledge_id, _META_KEY, user, get_stored_token)


@router.post('/auth/test')
async def test_connection(
    form_data: ConfluenceTestConnectionForm,
    user: UserModel = Depends(get_admin_user),
):
    """Probe a basic-auth Confluence credential by listing one space.

    Admin-only. Builds a basic-mode client from the submitted credentials
    (falling back to stored config for blank fields) and lists a single
    space. Returns ``{ok, detail, space_count?}``.
    """
    site_url = (form_data.site_url or CONFLUENCE_SITE_URL.value or '').strip()
    username = (form_data.username or CONFLUENCE_BASIC_AUTH_USERNAME.value or '').strip()
    api_token = (form_data.api_token or CONFLUENCE_BASIC_AUTH_API_TOKEN.value or '').strip()

    if not site_url or not username or not api_token:
        return {
            'ok': False,
            'detail': 'Site URL, username and API token are all required.',
        }

    client = ConfluenceClient(
        auth_mode='basic',
        site_url=site_url,
        basic_username=username,
        basic_api_token=api_token,
    )
    try:
        spaces, _ = await client.list_spaces(limit=1)
        return {
            'ok': True,
            'detail': 'Connection successful.',
            'space_count': len(spaces),
        }
    except httpx.HTTPStatusError as e:
        code = e.response.status_code
        detail = {
            401: 'Authentication failed — check the username and API token.',
            403: 'Access denied — the account cannot list spaces.',
            404: 'Not found — check the site URL.',
        }.get(code, f'Confluence returned HTTP {code}.')
        return {'ok': False, 'detail': detail}
    except ConnectionError as e:
        return {'ok': False, 'detail': str(e)}
    except Exception as e:
        log.warning('Confluence test connection failed: %s', e)
        return {'ok': False, 'detail': f'Connection failed: {e}'}
    finally:
        await client.close()


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


async def _browse_client(user: UserModel, cloud_id: str) -> tuple[ConfluenceClient, str]:
    """Build a (ConfluenceClient, site_url) pair for picker browsing.

    Branches on the global auth mode: basic mode validates ``cloud_id``
    against the one configured site and builds a basic-mode client; oauth
    mode resolves the per-user token and sites. The caller must close the
    returned client.
    """
    if resolve_auth_mode(None) == 'basic':
        basic_site = get_basic_site()
        if not basic_site or cloud_id != basic_site['cloud_id']:
            raise HTTPException(404, 'Unknown Confluence site (cloud_id)')
        if not basic_auth_configured():
            raise HTTPException(400, 'Confluence basic auth is not configured.')
        return build_basic_client(), basic_site['url']

    from open_webui.services.confluence.token_refresh import get_valid_access_token

    token, sites = await _picker_client(user)
    site = _pick_site(sites, cloud_id)

    async def _refresh():
        return await get_valid_access_token(user.id, knowledge_id='__picker__')

    client = ConfluenceClient(access_token=token, cloud_id=cloud_id, token_provider=_refresh)
    return client, site.get('url')


@router.get('/browse/sites')
async def browse_sites(user: UserModel = Depends(get_verified_user)):
    """List the Confluence sites available for browsing.

    In basic mode this is the single configured site; in oauth mode it is
    every site the authenticated user's token can reach.
    """
    if resolve_auth_mode(None) == 'basic':
        site = get_basic_site()
        return {'sites': [site] if site else []}

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
    client, site_url = await _browse_client(user, cloud_id)
    try:
        try:
            results, next_cursor = await client.list_spaces(cursor=cursor)
        except httpx.HTTPStatusError as e:
            raise HTTPException(e.response.status_code, f'Confluence error: {e.response.status_code}')

        return {
            'site_url': site_url,
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
    if not space_id and not parent_id:
        raise HTTPException(400, 'Provide either space_id or parent_id')

    client, site_url = await _browse_client(user, cloud_id)
    try:
        try:
            if parent_id:
                results, next_cursor = await client.list_page_children(parent_id, cursor=cursor)
            else:
                results, next_cursor = await client.list_pages_in_space(space_id, cursor=cursor)
        except httpx.HTTPStatusError as e:
            raise HTTPException(e.response.status_code, f'Confluence error: {e.response.status_code}')

        return {
            'site_url': site_url,
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


@router.get('/page/{cloud_id}/{page_id}/content')
async def get_page_content(
    cloud_id: str,
    page_id: str,
    user: UserModel = Depends(get_verified_user),
) -> dict:
    """Fetch one Confluence page rendered as Markdown — for ad-hoc chat attach.

    Used by the chat ``+`` menu Confluence picker: the user picks pages and
    each is fetched + HTML→Markdown rendered here, then attached to the chat
    as a one-off file. Reuses the picker's per-user/basic client resolution.
    """
    from open_webui.services.confluence.html_renderer import html_to_markdown

    client, _site_url = await _browse_client(user, cloud_id)
    try:
        try:
            page = await client.get_page(page_id, include_body=True)
        except httpx.HTTPStatusError as e:
            raise HTTPException(e.response.status_code, f'Confluence error: {e.response.status_code}')
        if not page:
            raise HTTPException(404, 'Confluence page not found')

        html = ((page.get('body') or {}).get('view') or {}).get('value') or ''
        # html_to_markdown is sync + CPU-bound — off-thread so a long page
        # doesn't stall the event loop.
        markdown = await asyncio.to_thread(html_to_markdown, html) if html else ''
        title = page.get('title') or f'page-{page_id}'
        return {
            'page_id': page_id,
            'title': title,
            'content': f'# {title}\n\n{markdown}',
        }
    finally:
        await client.close()


# ──────────────────────────────────────────────────────────────────────
# Shared full-content KB endpoints (admin-only)
# ──────────────────────────────────────────────────────────────────────

_SHARED_KB_NAME = 'Confluence'
_SHARED_KB_DESCRIPTION = 'Read-only Confluence knowledge base managed by administrators.'


def _find_shared_kb():
    """Return the existing (live) shared Confluence KB, or None.

    Discovered by ``type='confluence'`` + ``confluence_sync.shared == True``,
    not by name, so an admin renaming it does not orphan the link.
    Soft-deleted KBs are skipped so a deleted-then-reprovisioned KB never
    shadows the live one (which would make status/sync target the wrong row).
    """
    for kb in Knowledges.get_knowledge_bases_by_type(_PROVIDER_TYPE):
        if getattr(kb, 'deleted_at', None):
            continue
        if (kb.meta or {}).get(_META_KEY, {}).get('shared'):
            return kb
    return None


def _shared_kb_status() -> dict:
    """Compose the shared-KB status payload for the Cloud Sync admin tab."""
    kb = _find_shared_kb()
    status: dict = {
        'kb_mode': CONFLUENCE_KB_MODE.value,
        'auth_mode': resolve_auth_mode(None),
        'configured_owner_id': (CONFLUENCE_SHARED_KB_OWNER_ID.value or '').strip(),
        'provisioned': kb is not None,
        'knowledge_id': kb.id if kb else None,
    }
    if kb:
        sync_info = (kb.meta or {}).get(_META_KEY, {})
        status.update(
            {
                'owner_id': kb.user_id,
                'status': sync_info.get('status', 'idle'),
                'last_sync_at': sync_info.get('last_sync_at'),
                'last_result': sync_info.get('last_result'),
                'suspended_at': sync_info.get('suspended_at'),
                'file_count': len(Knowledges.get_files_by_id(kb.id) or []),
                # Live progress (files done / total) — lets the Cloud Sync
                # tab show a percentage on the Sync button while a sync runs.
                'progress_current': sync_info.get('progress_current', 0),
                'progress_total': sync_info.get('progress_total', 0),
                # The admin-selected spaces — used to pre-fill the Cloud Sync
                # tab's space checklist on reload.
                'spaces': sync_info.get('spaces', []),
            }
        )
    return status


@router.get('/shared/status')
async def get_shared_kb_status(user: UserModel = Depends(get_admin_user)) -> dict:
    """Report shared-KB provisioning state and last sync result (admin)."""
    return _shared_kb_status()


@router.get('/shared/spaces')
async def list_shared_kb_spaces(user: UserModel = Depends(get_admin_user)) -> dict:
    """List the Confluence spaces available for the shared KB (admin).

    Company-wide mode only — enumerates every space the basic-auth service
    account can see, so an admin can opt specific spaces into the shared KB.
    """
    if resolve_auth_mode(None) != 'basic':
        raise HTTPException(
            400,
            'Space selection is only available in company-wide (service account) mode.',
        )
    if not basic_auth_configured():
        raise HTTPException(
            400,
            'Confluence basic auth is not configured. Save the service account credentials first.',
        )

    basic_site = get_basic_site()
    cloud_id = basic_site['cloud_id'] if basic_site else ''
    client = build_basic_client()
    try:
        spaces = await client.list_all_spaces()
    except httpx.HTTPStatusError as e:
        raise HTTPException(e.response.status_code, f'Confluence error: {e.response.status_code}')
    finally:
        await client.close()

    return {
        'spaces': [
            {
                'id': s.get('id'),
                'key': s.get('key'),
                'name': s.get('name'),
                'type': s.get('type'),
                'cloud_id': cloud_id,
            }
            for s in spaces
        ],
    }


@router.post('/shared/provision')
async def provision_shared_kb(
    form_data: ConfluenceProvisionForm,
    user: UserModel = Depends(get_admin_user),
) -> dict:
    """Create (or update) the single shared, public-read Confluence KB.

    Admin-only. Stamps ``shared`` / ``auth_mode`` and the admin-selected
    ``spaces`` into the KB meta and grants ``user:*:read`` directly via
    ``AccessGrants`` — bypassing the non-local-type guards in the user
    knowledge router by not going through it, leaving those guards intact
    for normal user KBs.
    """
    auth_mode = resolve_auth_mode(None)
    owner_id = (CONFLUENCE_SHARED_KB_OWNER_ID.value or '').strip()

    # The admin-selected spaces, with the basic-mode cloud_id stamped on so
    # the sync worker can build a per-space source without re-deriving it.
    basic_site = get_basic_site()
    selected_spaces = []
    for space in form_data.spaces:
        entry = space.model_dump()
        if not entry.get('cloud_id') and basic_site:
            entry['cloud_id'] = basic_site['cloud_id']
        selected_spaces.append(entry)

    # OAuth resolves the sync token by the KB owner — a no-owner shared KB is
    # only valid with the global basic-auth service credential.
    if auth_mode == 'oauth' and not owner_id:
        raise HTTPException(
            400,
            'An owner must be selected for the shared Confluence knowledge base when using OAuth authentication.',
        )
    if owner_id and not Users.get_user_by_id(owner_id):
        raise HTTPException(400, 'The configured shared KB owner is not a valid user.')

    kb = _find_shared_kb()
    if kb:
        # Reassign the owner if the admin changed the setting.
        if kb.user_id != owner_id:
            Knowledges.update_knowledge_user_id_by_id(kb.id, owner_id)
        meta = kb.meta or {}
        sync_info = meta.get(_META_KEY, {})
        sync_info['shared'] = True
        sync_info['auth_mode'] = auth_mode
        sync_info['spaces'] = selected_spaces
        sync_info.pop('sync_all_spaces', None)  # legacy flag — superseded by `spaces`
        meta[_META_KEY] = sync_info
        Knowledges.update_knowledge_meta_by_id(kb.id, meta)
    else:
        kb = Knowledges.insert_new_knowledge(
            owner_id,
            KnowledgeForm(
                name=_SHARED_KB_NAME,
                description=_SHARED_KB_DESCRIPTION,
                type=_PROVIDER_TYPE,
                access_grants=[],
            ),
        )
        if not kb:
            raise HTTPException(500, 'Failed to create the shared Confluence knowledge base.')
        Knowledges.update_knowledge_meta_by_id(
            kb.id,
            {
                _META_KEY: {
                    'shared': True,
                    'auth_mode': auth_mode,
                    'spaces': selected_spaces,
                    'sources': [],
                    'status': 'idle',
                }
            },
        )

    # Public read grant — set directly on the model, not via the user router,
    # so the non-local-type access guards stay in force for regular KBs.
    AccessGrants.set_access_grants(
        'knowledge',
        kb.id,
        [{'principal_type': 'user', 'principal_id': '*', 'permission': 'read'}],
    )

    log.info('Shared Confluence KB provisioned: %s (owner=%r)', kb.id, owner_id or '<system>')
    return _shared_kb_status()


async def _run_shared_sync(knowledge_id: str, user_id: str, app):
    """Background task: run a full sync of the shared KB via the provider."""
    from open_webui.services.sync.provider import get_sync_provider

    try:
        provider = get_sync_provider(_PROVIDER_TYPE)
        await provider.execute_sync(knowledge_id=knowledge_id, user_id=user_id, app=app)
    except Exception as e:
        log.exception('Shared Confluence KB sync failed for %s: %s', knowledge_id, e)


@router.post('/shared/sync')
async def sync_shared_kb(
    fastapi_request: Request,
    background_tasks: BackgroundTasks,
    user: UserModel = Depends(get_admin_user),
) -> dict:
    """Trigger an immediate full sync of the shared Confluence KB (admin)."""
    kb = _find_shared_kb()
    if not kb:
        raise HTTPException(404, 'No shared Confluence knowledge base has been provisioned.')

    background_tasks.add_task(
        _run_shared_sync,
        knowledge_id=kb.id,
        user_id=kb.user_id,
        app=fastapi_request.app,
    )
    return {'message': 'Sync started', 'knowledge_id': kb.id}


@router.delete('/shared')
async def delete_shared_kb(user: UserModel = Depends(get_admin_user)) -> dict:
    """Soft-delete the shared Confluence KB (admin-only).

    The shared KB is blocked from deletion via the workspace Knowledge UI
    (see ``knowledge.py`` ``_assert_not_managed_shared_kb``) — this admin
    endpoint is the only managed way to remove it. The cleanup worker purges
    its files and vectors afterwards.
    """
    kb = _find_shared_kb()
    if not kb:
        raise HTTPException(404, 'No shared Confluence knowledge base has been provisioned.')
    Knowledges.soft_delete_by_id(kb.id)
    log.info('Shared Confluence KB deleted: %s', kb.id)
    return {'message': 'Shared Confluence knowledge base deleted.', 'knowledge_id': kb.id}
