"""TOPdesk Sync Router — admin operational endpoints for the TOPdesk shared KB.

Exposes the TOPdesk service-account integration to admins: a connection probe,
a knowledge-item tree-picker proxy, and the shared full-content KB lifecycle
(status / provision / sync / delete). Every endpoint is admin-gated.

Modelled on ``routers/confluence_sync.py`` minus all OAuth machinery — TOPdesk
has a single service-account auth mode (URL + operator login + application
password read from global config), so there is no per-user token flow, no
auth-mode resolution, and no kb_mode. The shared-KB endpoints delegate to the
provider-agnostic helpers in ``services/sync/shared_kb.py`` with
``provider_type='topdesk'``, ``meta_key='topdesk_sync'``, ``items_key='items'``.
"""

import logging
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from open_webui.config import (
    TOPDESK_APP_PASSWORD,
    TOPDESK_URL,
    TOPDESK_USERNAME,
)
from open_webui.models.users import UserModel, Users
from open_webui.services.sync.shared_kb import (
    delete_shared_kb as delete_shared_kb_generic,
)
from open_webui.services.sync.shared_kb import (
    find_shared_kb,
)
from open_webui.services.sync.shared_kb import (
    provision_shared_kb as provision_shared_kb_generic,
)
from open_webui.services.sync.shared_kb import (
    shared_kb_status as shared_kb_status_generic,
)
from open_webui.services.topdesk.auth import build_client, service_auth_configured
from open_webui.services.topdesk.topdesk_client import (
    TopdeskAuthError,
    TopdeskClient,
    TopdeskGraphQLError,
    TopdeskTransientError,
)
from open_webui.utils.auth import get_admin_user
from pydantic import BaseModel
from starlette.requests import Request

log = logging.getLogger(__name__)
router = APIRouter()

_META_KEY = 'topdesk_sync'
_PROVIDER_TYPE = 'topdesk'
_ITEMS_KEY = 'items'

_SHARED_KB_NAME = 'TOPdesk'
_SHARED_KB_DESCRIPTION = 'Read-only TOPdesk knowledge base managed by administrators.'


# ──────────────────────────────────────────────────────────────────────
# Request models
# ──────────────────────────────────────────────────────────────────────


class TopdeskTestConnectionForm(BaseModel):
    """Optional credential overrides for the connection-test probe.

    Any field left blank falls back to the stored config, so an admin can test
    typed-but-unsaved values or re-test the saved credential. The username is
    optional — an empty username selects the ``TOKEN id="..."`` person-token
    header form (see ``services/topdesk/auth.py``).
    """

    url: str | None = None
    username: str | None = None
    app_password: str | None = None


class TopdeskKbItem(BaseModel):
    """One TOPdesk knowledge item (or subtree) opted into the shared KB.

    ``type='folder'`` syncs the item plus its descendants (subtree);
    ``type='file'`` syncs the single leaf item. The sync worker reads these
    back from KB meta under the ``items`` key and rebuilds its source list on
    every run.
    """

    type: Literal['folder', 'file'] = 'folder'
    item_id: str
    name: str | None = None
    include_descendants: bool = True


class TopdeskProvisionForm(BaseModel):
    """Shared-KB provisioning request — the admin-selected items to sync.

    Selection is opt-in: only the listed items are synced. An empty list
    provisions the KB shell but syncs nothing until the admin picks at least
    one item. ``owner_user_id`` carries the admin's owner pick (empty =
    system-owned KB that syncs with the global service credential — TOPdesk has
    no per-user OAuth owner, so there is nothing to resolve beyond this pick).
    """

    items: list[TopdeskKbItem] = []
    owner_user_id: str | None = None


# ──────────────────────────────────────────────────────────────────────
# Connection test (admin-only)
# ──────────────────────────────────────────────────────────────────────


@router.post('/auth/test')
async def test_connection(
    form_data: TopdeskTestConnectionForm,
    user: UserModel = Depends(get_admin_user),
) -> dict:
    """Probe a TOPdesk service credential with a lightweight REST call.

    Admin-only. Builds a client from the submitted credentials (falling back to
    stored config for blank fields) and runs ``probe()`` (operators/current with
    a version fallback). Returns ``{ok, detail, item_count?}``. A blank
    ``app_password`` reuses the stored credential. The username is optional.
    """
    url = (form_data.url or TOPDESK_URL.value or '').strip().rstrip('/')
    username = (form_data.username or TOPDESK_USERNAME.value or '').strip()
    # Blank password → fall back to the stored credential.
    app_password = form_data.app_password if form_data.app_password else (TOPDESK_APP_PASSWORD.value or '')

    if not url or not app_password:
        return {
            'ok': False,
            'detail': 'TOPdesk URL and application password are required.',
        }

    client = TopdeskClient(base_url=url, username=username, app_password=app_password)
    try:
        result = await client.probe()
        return {
            'ok': bool(result.get('ok')),
            'detail': 'Connection successful.' if result.get('ok') else 'TOPdesk probe did not succeed.',
        }
    except TopdeskAuthError:
        return {
            'ok': False,
            'detail': 'Authentication failed — check the operator login name and application password.',
        }
    except TopdeskTransientError as e:
        code = e.status_code
        detail = 'TOPdesk is temporarily unavailable. Please try again shortly.'
        if code == 429:
            detail = 'TOPdesk rate-limited the request. Please try again shortly.'
        return {'ok': False, 'detail': detail}
    except ConnectionError as e:
        return {'ok': False, 'detail': str(e)}
    except Exception as e:
        log.warning('TOPdesk test connection failed: %s', e)
        return {'ok': False, 'detail': f'Connection failed: {e}'}
    finally:
        await client.close()


# ──────────────────────────────────────────────────────────────────────
# Tree-picker proxy (admin-only)
# ──────────────────────────────────────────────────────────────────────


def _picker_item(node: dict) -> dict:
    """Normalize a TOPdesk knowledge-item node into the picker shape.

    ``has_children`` is a best-effort flag. The list/child node selections
    (``services/topdesk/topdesk_client.py``) do NOT carry a child count or a
    ``hasChildren`` field, and we deliberately avoid an N+1 child-fetch per
    item (one extra GraphQL round-trip per node would be punishing on large
    KBs). When the node happens to carry a ``children`` array (e.g. a parent
    node hydrated elsewhere) we honour it; otherwise we report ``True`` so the
    picker offers an expand affordance — expanding then resolves the real
    children via ``list_item_children`` and an empty result collapses the node.
    """
    children = node.get('children')
    if isinstance(children, list):
        has_children = len(children) > 0
    else:
        # Unknown without an extra fetch — offer the expand affordance and let
        # the on-expand child query reveal whether there are any.
        has_children = True
    return {
        'id': node.get('id'),
        'name': node.get('title') or node.get('number') or node.get('id'),
        'number': node.get('number'),
        'has_children': has_children,
        'status': node.get('status'),
    }


@router.get('/browse/items')
async def browse_items(
    parent_id: str | None = Query(None),
    user: UserModel = Depends(get_admin_user),
) -> dict:
    """List TOPdesk knowledge items for the tree picker (admin).

    Without ``parent_id`` returns the root items (``list_root_items``); with
    ``parent_id`` returns that item's direct children (``list_item_children``).
    Each entry is ``{id, name, number, has_children, status}``.
    """
    if not service_auth_configured():
        raise HTTPException(
            400,
            'TOPdesk is not configured. Save the TOPdesk URL and application password first.',
        )

    client = None
    try:
        client = build_client()
        if parent_id:
            nodes = await client.list_item_children(parent_id)
        else:
            nodes = await client.list_root_items()
        return {'items': [_picker_item(node) for node in nodes]}
    except TopdeskAuthError:
        raise HTTPException(401, 'TOPdesk rejected the service credential.')
    except TopdeskTransientError as e:
        # Map a TOPdesk outage to a clean upstream-failure status.
        raise HTTPException(503 if e.status_code != 429 else 502, 'TOPdesk is temporarily unavailable.')
    except TopdeskGraphQLError as e:
        raise HTTPException(502, f'TOPdesk query failed: {e}')
    except ConnectionError as e:
        raise HTTPException(502, str(e))
    except HTTPException:
        # Never let the catch-all below remap an HTTPException we raised on purpose.
        raise
    except Exception as e:
        # Catch-all so an unexpected error (malformed GraphQL not wrapped in
        # TopdeskGraphQLError, a _picker_item bug on an odd node, etc.) surfaces
        # as a clean 502 instead of a raw 500 + stacktrace to the admin.
        log.exception('TOPdesk browse_items failed unexpectedly: %s', e)
        raise HTTPException(502, 'TOPdesk request failed.')
    finally:
        if client is not None:
            await client.close()


# ──────────────────────────────────────────────────────────────────────
# Shared full-content KB endpoints (admin-only)
# ──────────────────────────────────────────────────────────────────────


async def _find_shared_kb():
    """Return the existing (live) shared TOPdesk KB, or None.

    Discovered by ``type='topdesk'`` + ``topdesk_sync.shared == True``, not by
    name, so renaming it does not orphan the link. Delegates to the
    provider-agnostic helper.
    """
    return await find_shared_kb(_PROVIDER_TYPE, _META_KEY)


async def _shared_kb_status() -> dict:
    """Compose the shared-KB status payload for the Cloud Sync admin tab.

    The provider-neutral core (provisioned flag, knowledge_id, owner, status,
    progress, file_count, persisted selection) comes from the shared helper.
    TOPdesk adds only ``credential_configured`` — there is no OAuth/auth-mode/
    kb-mode dimension (single service-account auth), so those Confluence fields
    are deliberately omitted.
    """
    status: dict = {'credential_configured': service_auth_configured()}
    status.update(await shared_kb_status_generic(_PROVIDER_TYPE, _META_KEY, items_key=_ITEMS_KEY))
    return status


@router.get('/shared/status')
async def get_shared_kb_status(user: UserModel = Depends(get_admin_user)) -> dict:
    """Report shared-KB provisioning state and last sync result (admin)."""
    return await _shared_kb_status()


@router.post('/shared/provision')
async def provision_shared_kb(
    form_data: TopdeskProvisionForm,
    user: UserModel = Depends(get_admin_user),
) -> dict:
    """Create (or update) the single shared, public-read TOPdesk KB (admin).

    Stamps ``shared`` and the admin-selected ``items`` into the KB meta and
    grants ``user:*:read`` via the shared helper (which bypasses the
    non-local-type guards in the user knowledge router). The owner is the
    admin's pick (empty = system-owned KB that syncs with the global service
    credential). TOPdesk has no per-user token, so there is no OAuth owner
    resolution — only the form pick is honoured.
    """
    # Normalize the admin selection into the worker's expected source shape.
    selected_items: list = []
    for item in form_data.items:
        entry = item.model_dump()
        entry['item_id'] = (entry.get('item_id') or '').strip()
        if not entry['item_id']:
            continue
        if not entry.get('name'):
            entry['name'] = entry['item_id']
        selected_items.append(entry)

    owner_id = (form_data.owner_user_id or '').strip()
    if owner_id and not await Users.get_user_by_id(owner_id):
        raise HTTPException(400, 'The selected shared KB owner is not a valid user.')

    try:
        await provision_shared_kb_generic(
            provider_type=_PROVIDER_TYPE,
            meta_key=_META_KEY,
            name=_SHARED_KB_NAME,
            description=_SHARED_KB_DESCRIPTION,
            owner_id=owner_id,
            selected_items=selected_items,
            items_key=_ITEMS_KEY,
            extra_meta={},
        )
    except RuntimeError as err:
        raise HTTPException(500, 'Failed to create the shared TOPdesk knowledge base.') from err

    return await _shared_kb_status()


async def _run_shared_sync(knowledge_id: str, user_id: str, app):
    """Background task: run a full sync of the shared KB via the provider."""
    from open_webui.services.sync.provider import get_sync_provider

    try:
        provider = get_sync_provider(_PROVIDER_TYPE)
        await provider.execute_sync(knowledge_id=knowledge_id, user_id=user_id, app=app)
    except Exception as e:
        log.exception('Shared TOPdesk KB sync failed for %s: %s', knowledge_id, e)


@router.post('/shared/sync')
async def sync_shared_kb(
    fastapi_request: Request,
    background_tasks: BackgroundTasks,
    user: UserModel = Depends(get_admin_user),
) -> dict:
    """Trigger an immediate full sync of the shared TOPdesk KB (admin)."""
    kb = await _find_shared_kb()
    if not kb:
        raise HTTPException(404, 'No shared TOPdesk knowledge base has been provisioned.')

    background_tasks.add_task(
        _run_shared_sync,
        knowledge_id=kb.id,
        user_id=kb.user_id,
        app=fastapi_request.app,
    )
    return {'message': 'Sync started', 'knowledge_id': kb.id}


@router.delete('/shared')
async def delete_shared_kb(user: UserModel = Depends(get_admin_user)) -> dict:
    """Soft-delete the shared TOPdesk KB (admin-only).

    The shared KB is blocked from deletion via the workspace Knowledge UI (see
    ``knowledge.py`` ``_assert_not_managed_shared_kb``) — this admin endpoint is
    the only managed way to remove it. The cleanup worker purges its files and
    vectors afterwards.
    """
    kb_id = await delete_shared_kb_generic(_PROVIDER_TYPE, _META_KEY)
    if not kb_id:
        raise HTTPException(404, 'No shared TOPdesk knowledge base has been provisioned.')
    return {'message': 'Shared TOPdesk knowledge base deleted.', 'knowledge_id': kb_id}
