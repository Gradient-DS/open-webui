"""Confluence Sync Router — endpoints for Confluence space/page sync to Knowledge bases."""

import asyncio
import logging
from typing import List, Literal, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from starlette.requests import Request
from pydantic import BaseModel

from open_webui.utils.auth import get_verified_user, get_admin_user
from open_webui.models.users import UserModel, Users
from open_webui.models.knowledge import Knowledges
from open_webui.models.config import Config
from open_webui.services.confluence.confluence_client import ConfluenceClient
from open_webui.services.confluence.basic_auth import (
    BASIC_AUTH_SENTINEL,
    is_service_mode,
    service_auth_configured,
    build_service_client,
    service_site,
    resolve_cloud_id,
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
from open_webui.services.sync.shared_kb import (
    find_shared_kb,
    provision_shared_kb as provision_shared_kb_generic,
    shared_kb_status as shared_kb_status_generic,
    delete_shared_kb as delete_shared_kb_generic,
)
from open_webui.services.sync.daemon_client import trigger_sync_run

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
    """Optional credential overrides for the service-account test-connection probe.

    Any field left blank falls back to the stored config, so an admin can
    test typed-but-unsaved values or re-test the saved credential. ``mode``
    selects which service transport to probe ('basic' → site, 'scoped' →
    gateway); blank/unknown defaults to 'basic' for back-compat. ``cloud_id``
    is only consulted in scoped mode (blank → auto-resolve from the site URL).
    """

    mode: Optional[str] = None
    site_url: Optional[str] = None
    username: Optional[str] = None
    api_token: Optional[str] = None
    cloud_id: Optional[str] = None


class ConfluenceKbItem(BaseModel):
    """One Confluence space, page, or page-subtree opted into the shared KB.

    Back-compat: rows persisted before page-level granularity carry only
    ``{id, key, name, cloud_id}`` — ``type`` defaults to ``'space'`` and
    ``item_id`` / ``space_id`` fall back to ``id`` on normalization.
    """

    type: Literal['space', 'page'] = 'space'
    # Legacy aliases — older payloads sent {id, key} for spaces.
    id: Optional[str] = None
    key: Optional[str] = None
    # Common fields shared with SyncItem so the per-user picker output drops
    # straight into the shared KB without translation.
    name: Optional[str] = None
    cloud_id: Optional[str] = None
    space_id: Optional[str] = None
    space_key: Optional[str] = None
    site_url: Optional[str] = None
    item_id: Optional[str] = None
    item_path: Optional[str] = None
    include_descendants: bool = True


class ConfluenceProvisionForm(BaseModel):
    """Shared-KB provisioning request — the admin-selected items to sync.

    Each entry can be a whole space, a single page, or a page-subtree.
    Selection is opt-in: only the listed items are synced. An empty list
    provisions the KB shell but syncs nothing until the admin picks at least
    one item. The field name stays ``spaces`` for back-compat with deployed
    frontends and persisted KB meta despite now accepting page items.

    ``owner_user_id`` carries the admin's basic-mode owner pick (empty =
    system-owned). In OAuth mode the form value is ignored — the calling
    admin is implicitly the owner, since only their stored token can run the
    sync.
    """

    spaces: List[ConfluenceKbItem] = []
    owner_user_id: Optional[str] = None


async def _stamp_auth_mode(knowledge_id: str, mode: str) -> None:
    """Persist the KB's resolved auth mode into its confluence_sync meta.

    A KB keeps the mode it was created under even if the global default
    later flips — so existing OAuth KBs are unaffected by switching the
    tenant to basic auth and vice versa.
    """
    kb = await Knowledges.get_knowledge_by_id(knowledge_id)
    if not kb:
        return
    meta = kb.meta or {}
    sync_info = meta.get(_META_KEY, {})
    if sync_info.get('auth_mode') != mode:
        sync_info['auth_mode'] = mode
        meta[_META_KEY] = sync_info
        await Knowledges.update_knowledge_meta_by_id(knowledge_id, meta)


# ──────────────────────────────────────────────────────────────────────
# Sync endpoints
# ──────────────────────────────────────────────────────────────────────


@router.post('/sync/items')
async def sync_items(
    request: SyncItemsRequest,
    user: UserModel = Depends(get_verified_user),
):
    """Start Confluence sync for multiple items (spaces and/or pages)."""
    mode = await resolve_auth_mode(request.knowledge_id)

    # Reject any cloud_id the caller can't actually access — prevents a
    # malformed body from pointing the worker at an arbitrary Atlassian site.
    if is_service_mode(mode):
        site = await service_site(mode)
        if not site:
            raise HTTPException(400, 'Confluence service-account auth is not configured.')
        allowed_cloud_ids = {site['cloud_id']}
    else:
        from open_webui.services.confluence.auth import get_stored_sites

        allowed_cloud_ids = {s.get('cloud_id') for s in await get_stored_sites(user.id)}

    for item in request.items:
        if item.cloud_id not in allowed_cloud_ids:
            raise HTTPException(403, f'Confluence site not accessible: {item.cloud_id}')

    access_token = request.access_token
    if not access_token:
        if is_service_mode(mode):
            # service modes have no token — the worker reads the service
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

    # Stamp the KB's auth mode so it stays stable if the global default flips.
    await _stamp_auth_mode(request.knowledge_id, mode)

    await handle_sync_items_request(
        knowledge_id=request.knowledge_id,
        meta_key=_META_KEY,
        provider=_PROVIDER_TYPE,
        new_sources=new_sources,
        access_token=access_token,
        user=user,
        clear_delta_keys=_CLEAR_DELTA_KEYS,
    )

    return {'message': 'Sync started', 'knowledge_id': request.knowledge_id}


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
    """Cancel an ongoing Confluence sync for a Knowledge base."""
    return await handle_cancel_sync(knowledge_id, _META_KEY, user)


async def _remove_files_for_source(knowledge_id, item_id, source_to_remove):
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
    """Remove a source from a KB's Confluence sync configuration."""
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
    """List all Knowledge bases with Confluence sync enabled for current user."""
    return await handle_list_synced_collections(_META_KEY, user)


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

    oauth_cfg = await Config.get_many('confluence.client_id', 'confluence.client_secret')
    if not oauth_cfg.get('confluence.client_id'):
        raise HTTPException(400, 'Confluence client ID not configured')
    if not oauth_cfg.get('confluence.client_secret'):
        raise HTTPException(400, 'Confluence client secret not configured')

    if knowledge_id:
        await get_knowledge_or_raise(knowledge_id, user)

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

    In the service modes (basic/scoped) there is no per-user OAuth token —
    report 'connected' whenever the global service credential is configured,
    so the picker can proceed without an OAuth authorization step.
    """
    mode = await resolve_auth_mode(knowledge_id)
    if is_service_mode(mode):
        await get_knowledge_or_raise(knowledge_id, user)
        configured = await service_auth_configured(mode)
        return {
            'has_token': configured,
            'is_expired': False,
            'needs_reauth': not configured,
        }

    from open_webui.services.confluence.auth import get_stored_token

    return await handle_get_token_status(knowledge_id, _META_KEY, user, get_stored_token)


@router.post('/auth/test')
async def test_connection(
    form_data: ConfluenceTestConnectionForm,
    user: UserModel = Depends(get_admin_user),
):
    """Probe a service-account Confluence credential by listing one space.

    Admin-only. Builds a basic- or scoped-mode client from the submitted
    credentials (falling back to stored config for blank fields) and lists a
    single space. Returns ``{ok, reason, detail, space_count?}`` — ``reason`` is
    a stable machine code the frontend localizes; ``detail`` is an English debug
    fallback. Scoped mode resolves the gateway cloudId first (``missing_cloud_id``
    when it can't) and maps an under-scoped token (``401 "scope does not match"``)
    to ``scope_mismatch`` so the admin knows to add the missing read scope.
    """
    mode = (form_data.mode or 'basic').strip()
    if mode not in ('basic', 'scoped'):
        mode = 'basic'

    stored = await Config.get_many(
        'confluence.site_url',
        'confluence.basic_auth_username',
        'confluence.scoped_api_token',
        'confluence.basic_auth_api_token',
    )
    site_url = (form_data.site_url or stored.get('confluence.site_url') or '').strip()
    username = (form_data.username or stored.get('confluence.basic_auth_username') or '').strip()
    stored_token = (
        stored.get('confluence.scoped_api_token') if mode == 'scoped' else stored.get('confluence.basic_auth_api_token')
    )
    api_token = (form_data.api_token or stored_token or '').strip()

    if not site_url or not username or not api_token:
        return {
            'ok': False,
            'reason': 'missing_config',
            'detail': 'Site URL, username and API token are all required.',
        }

    if mode == 'scoped':
        # The scoped transport addresses the gateway by cloudId — resolve it
        # (manual override on the form, else from the typed site URL) before
        # building the client, so a misconfiguration is a clear reason code
        # rather than a request fired at a malformed gateway URL.
        cloud_id = (form_data.cloud_id or '').strip() or await resolve_cloud_id(site_url)
        if not cloud_id:
            return {
                'ok': False,
                'reason': 'missing_cloud_id',
                'detail': 'Could not resolve the Atlassian cloud ID from the site URL. Enter it manually.',
            }
        client = ConfluenceClient(
            auth_mode='scoped',
            cloud_id=cloud_id,
            basic_username=username,
            basic_api_token=api_token,
        )
    else:
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
            'reason': 'ok',
            'detail': 'Connection successful.',
            'space_count': len(spaces),
        }
    except httpx.HTTPStatusError as e:
        code = e.response.status_code
        # A scoped token missing a granular read scope returns 401 (not 403)
        # with "scope does not match" in the body — surface it distinctly.
        if code == 401 and 'scope does not match' in (e.response.text or '').lower():
            return {
                'ok': False,
                'reason': 'scope_mismatch',
                'detail': 'The scoped token is missing a required read scope. '
                'Re-create it with all five Confluence read scopes.',
            }
        reason, detail = {
            401: ('auth_failed', 'Authentication failed — check the username and API token.'),
            403: ('forbidden', 'Access denied — the account cannot list spaces.'),
            404: ('not_found', 'Not found — check the site URL.'),
        }.get(code, ('error', f'Confluence returned HTTP {code}.'))
        return {'ok': False, 'reason': reason, 'detail': detail}
    except ConnectionError as e:
        return {'ok': False, 'reason': 'unreachable', 'detail': str(e)}
    except Exception as e:
        log.warning('Confluence test connection failed: %s', e)
        return {'ok': False, 'reason': 'error', 'detail': f'Connection failed: {e}'}
    finally:
        await client.close()


@router.post('/auth/revoke/{knowledge_id}')
async def revoke_token(
    knowledge_id: str,
    user: UserModel = Depends(get_verified_user),
):
    """Revoke and delete stored Confluence token for a user's KBs."""
    from open_webui.services.confluence.auth import delete_stored_token

    return await handle_revoke_token(knowledge_id, _PROVIDER_TYPE, _META_KEY, user, delete_stored_token)


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

    return token, await get_stored_sites(user.id)


def _pick_site(sites: list, cloud_id: str) -> dict:
    for s in sites:
        if s.get('cloud_id') == cloud_id:
            return s
    raise HTTPException(404, 'Unknown Confluence site (cloud_id)')


async def _browse_client(user: UserModel, cloud_id: str) -> tuple[ConfluenceClient, str]:
    """Build a (ConfluenceClient, site_url) pair for picker browsing.

    Branches on the global auth mode: a service mode (basic/scoped) validates
    ``cloud_id`` against the one configured site and builds the matching service
    client; oauth mode resolves the per-user token and sites. The caller must
    close the returned client.
    """
    mode = await resolve_auth_mode(None)
    if is_service_mode(mode):
        site = await service_site(mode)
        if not site or cloud_id != site['cloud_id']:
            raise HTTPException(404, 'Unknown Confluence site (cloud_id)')
        if not await service_auth_configured(mode):
            raise HTTPException(400, 'Confluence service-account auth is not configured.')
        return await build_service_client(mode), site['url']

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

    In a service mode (basic/scoped) this is the single configured site; in
    oauth mode it is every site the authenticated user's token can reach.
    """
    mode = await resolve_auth_mode(None)
    if is_service_mode(mode):
        site = await service_site(mode)
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


async def _find_shared_kb():
    """Return the existing (live) shared Confluence KB, or None.

    Discovered by ``type='confluence'`` + ``confluence_sync.shared == True``,
    not by name, so an admin renaming it does not orphan the link.
    Soft-deleted KBs are skipped so a deleted-then-reprovisioned KB never
    shadows the live one (which would make status/sync target the wrong row).

    Delegates to the provider-agnostic helper; the discovery contract is
    identical (type + ``shared`` meta flag, skip soft-deleted).
    """
    return await find_shared_kb(_PROVIDER_TYPE, _META_KEY)


async def _resolve_effective_owner_id(current_user: UserModel) -> str:
    """Return the user id whose token the shared sync runs (or will run) with.

    Post-provision the KB row carries the owner (``kb.user_id``). Pre-
    provision there is no KB yet, so the calling admin is the implicit owner
    — they are the one who'll click Provision and become ``kb.user_id``.
    """
    kb = await _find_shared_kb()
    if kb and kb.user_id:
        return kb.user_id
    return current_user.id


async def _shared_kb_status(current_user: UserModel) -> dict:
    """Compose the shared-KB status payload for the Cloud Sync admin tab.

    The provider-neutral core (provisioned flag, knowledge_id, owner, status,
    progress, file_count, persisted selection) comes from the shared helper;
    the Confluence-specific fields (kb_mode, auth_mode, owner-connected) are
    resolved here. ``items_key='spaces'`` keeps the persisted/returned key as
    ``spaces`` for back-compat with the deployed frontend.
    """
    auth_mode = await resolve_auth_mode(None)
    effective_owner_id = await _resolve_effective_owner_id(current_user)

    # Whether the account the shared sync runs as has connected. The service
    # modes (basic/scoped) have no per-user token — the global service
    # credential stands in for it; OAuth needs the effective owner (the KB
    # owner, or the calling admin if no KB exists yet) to have authorized.
    if is_service_mode(auth_mode):
        owner_connected = await service_auth_configured(auth_mode)
    else:
        from open_webui.services.confluence.auth import get_stored_token

        owner_connected = await get_stored_token(effective_owner_id) is not None

    status: dict = {
        'kb_mode': await Config.get('confluence.kb_mode', 'per_user'),
        'auth_mode': auth_mode,
        'owner_connected': owner_connected,
    }
    status.update(await shared_kb_status_generic(_PROVIDER_TYPE, _META_KEY, items_key='spaces'))
    return status


@router.get('/shared/status')
async def get_shared_kb_status(user: UserModel = Depends(get_admin_user)) -> dict:
    """Report shared-KB provisioning state and last sync result (admin)."""
    return await _shared_kb_status(user)


@router.get('/shared/spaces')
async def list_shared_kb_spaces(user: UserModel = Depends(get_admin_user)) -> dict:
    """List the Confluence spaces available for the shared KB (admin).

    Pre-synced mode. In a service mode (basic/scoped), enumerates every space
    the service account sees. In OAuth, enumerates spaces the configured
    owner's token can reach — the owner's token is what the scheduler syncs
    with, so the picker shows exactly what the sync can fetch.
    """
    auth_mode = await resolve_auth_mode(None)

    if is_service_mode(auth_mode):
        if not await service_auth_configured(auth_mode):
            raise HTTPException(
                400,
                'Confluence service-account auth is not configured. Save the service account credentials first.',
            )
        site = await service_site(auth_mode)
        cloud_id = site['cloud_id'] if site else ''
        client = await build_service_client(auth_mode)
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

    # OAuth — enumerate via the effective owner's token. Post-provision that
    # is the KB owner (whose token the scheduler syncs with); pre-provision
    # it is the calling admin (about to provision and become the KB owner).
    # Either way the picker shows exactly what the sync can fetch.
    effective_owner_id = await _resolve_effective_owner_id(user)
    owner = await Users.get_user_by_id(effective_owner_id)
    if not owner:
        raise HTTPException(400, 'The shared KB owner is not a valid user.')

    try:
        _, sites = await _picker_client(owner)
    except HTTPException as e:
        if e.status_code == 401:
            raise HTTPException(
                400,
                'Connect your Confluence account before picking spaces.',
            )
        raise

    spaces: list = []
    for site in sites:
        cloud_id = site.get('cloud_id')
        client, _ = await _browse_client(owner, cloud_id)
        try:
            site_spaces = await client.list_all_spaces()
        except httpx.HTTPStatusError as e:
            raise HTTPException(e.response.status_code, f'Confluence error: {e.response.status_code}')
        finally:
            await client.close()
        spaces.extend(
            {
                'id': s.get('id'),
                'key': s.get('key'),
                'name': s.get('name'),
                'type': s.get('type'),
                'cloud_id': cloud_id,
            }
            for s in site_spaces
        )
    return {'spaces': spaces}


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
    auth_mode = await resolve_auth_mode(None)

    # The admin-selected items (spaces and/or pages), normalized so the sync
    # worker can build per-source entries without re-deriving fields. Legacy
    # payloads that only carry {id, key, name, cloud_id} resolve to space
    # items with id → item_id / space_id and key → space_key.
    service_site_info = await service_site(auth_mode) if is_service_mode(auth_mode) else None
    selected_spaces: list = []
    for item in form_data.spaces:
        entry = item.model_dump()
        item_id = entry.get('item_id') or entry.get('id')
        entry['item_id'] = item_id
        if not entry.get('space_key'):
            entry['space_key'] = entry.get('key')
        if entry.get('type') == 'space' and not entry.get('space_id'):
            entry['space_id'] = item_id
        if not entry.get('cloud_id') and service_site_info:
            entry['cloud_id'] = service_site_info['cloud_id']
        if not entry.get('item_path'):
            entry['item_path'] = entry.get('name') or item_id or ''
        selected_spaces.append(entry)

    # Resolve the owner. OAuth: the calling admin — only their stored token
    # can run the sync, so the form's owner_user_id is ignored. Service modes
    # (basic/scoped): the admin's pick (empty = system-owned KB with no human
    # owner; the worker uses the global service credential).
    if auth_mode == 'oauth':
        owner_id = user.id
        from open_webui.services.confluence.auth import get_stored_token

        if await get_stored_token(owner_id) is None:
            raise HTTPException(
                400,
                'Connect your Confluence account before provisioning the shared knowledge base.',
            )
    else:
        owner_id = (form_data.owner_user_id or '').strip()
        if owner_id and not await Users.get_user_by_id(owner_id):
            raise HTTPException(400, 'The selected shared KB owner is not a valid user.')

    # Delegate the create/update + public-read grant to the shared helper.
    # ``auth_mode`` is Confluence-specific meta; ``items_key='spaces'`` keeps
    # the persisted selection under the ``spaces`` key for back-compat.
    try:
        await provision_shared_kb_generic(
            provider_type=_PROVIDER_TYPE,
            meta_key=_META_KEY,
            name=_SHARED_KB_NAME,
            description=_SHARED_KB_DESCRIPTION,
            owner_id=owner_id,
            selected_items=selected_spaces,
            extra_meta={'auth_mode': auth_mode},
            items_key='spaces',
        )
    except RuntimeError as err:
        raise HTTPException(500, 'Failed to create the shared Confluence knowledge base.') from err

    return await _shared_kb_status(user)


@router.post('/shared/sync')
async def sync_shared_kb(
    user: UserModel = Depends(get_admin_user),
) -> dict:
    """Trigger an immediate full sync of the shared Confluence KB (admin)."""
    kb = await _find_shared_kb()
    if not kb:
        raise HTTPException(404, 'No shared Confluence knowledge base has been provisioned.')

    # The shared KB is system-owned; kb.user_id may be '' in service-credential
    # (basic/scoped) mode — the daemon runs Confluence with the service
    # credential and does not need a real OAuth user.
    await trigger_sync_run(kb.id, _PROVIDER_TYPE, kb.user_id)
    return {'message': 'Sync started', 'knowledge_id': kb.id}


@router.delete('/shared')
async def delete_shared_kb(user: UserModel = Depends(get_admin_user)) -> dict:
    """Soft-delete the shared Confluence KB (admin-only).

    The shared KB is blocked from deletion via the workspace Knowledge UI
    (see ``knowledge.py`` ``_assert_not_managed_shared_kb``) — this admin
    endpoint is the only managed way to remove it. The cleanup worker purges
    its files and vectors afterwards.
    """
    kb_id = await delete_shared_kb_generic(_PROVIDER_TYPE, _META_KEY)
    if not kb_id:
        raise HTTPException(404, 'No shared Confluence knowledge base has been provisioned.')
    return {'message': 'Shared Confluence knowledge base deleted.', 'knowledge_id': kb_id}
