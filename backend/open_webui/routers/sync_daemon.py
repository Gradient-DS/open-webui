"""Sync-daemon protocol endpoints: token broker, config read, run summary.

The per-tenant sync-daemon (soev-solutions ``services/sync_daemon/``) is a
stateless orchestrator: cursors, hashes, suspension and File/KB records all
live here in open-webui. These three endpoints are its state seam (design
`2026-07-22-sync-daemon-design.md` §4i):

- ``POST /token`` — short-lived provider access token for the acting user
  (D-1: OAuth custody stays in OWUI; refresh tokens never leave).
- ``GET /config/{provider}`` — per-provider admin sync config + KB registry
  the daemon scheduler drives its due-gates from (D-2).
- ``POST /runs/{knowledge_id}/summary`` — run state callback; persists
  heartbeat/terminal status and enforces the cursor-advance rule (R8)
  server-side (D-3).

Auth is the strict sync machine principal (``Bearer SYNC_API_KEY`` + acting
headers, see :func:`open_webui.utils.service_auth.get_sync_principal`). Every
endpoint additionally self-gates on the ``sync_daemon.enabled`` per-key config
flag (403 when off) so the router stays mounted unconditionally and the daemon
can be enabled per tenant at runtime without a pod restart.
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from open_webui.internal.db import get_async_session
from open_webui.models.config import Config
from open_webui.models.files import Files
from open_webui.models.knowledge import Knowledges
from open_webui.models.oauth_sessions import OAuthSessions
from open_webui.routers.configs import CLOUD_SYNC_PROVIDERS
from open_webui.routers.knowledge import _verify_knowledge_write_access
from open_webui.services.sync.events import emit_sync_progress
from open_webui.services.sync.provider import file_id_prefix_for, get_token_manager
from open_webui.services.sync.token_refresh import _PROVIDER_EVENT_PREFIXES
from open_webui.utils.service_auth import SyncPrincipal, get_sync_principal, get_sync_service_principal
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()
log = logging.getLogger(__name__)

_PROVIDERS: dict[str, dict] = {provider['slug']: provider for provider in CLOUD_SYNC_PROVIDERS}

_RUN_STATUSES = frozenset({'started', 'heartbeat', 'completed', 'completed_with_errors', 'failed', 'cancelled'})
_TERMINAL_STATUSES = frozenset({'completed', 'completed_with_errors', 'failed', 'cancelled'})

# Error codes that are terminal per-item: re-running the sync won't fix them,
# so they don't freeze the delta cursor. Mirrors
# services/sync/base_worker._NON_RETRYABLE_LOADER_ERROR_CODES — this handler
# becomes the sole owner of the cursor-advance rule (R8) once
# services/sync/** is deleted at cutover.
_NON_RETRYABLE_ERROR_CODES = frozenset(
    {
        'empty_extraction',
        'doc_processor_schema_error',
        'unsupported_content_type',
        'source_access_revoked',
    }
)

# Per-provider admin config keys the daemon scheduler needs — dotted keys
# exactly as registered in routers/configs.py *_CONFIG_KEYS. The per-sync cap
# key is normalized to 'max_files_per_sync' in the response (confluence calls
# it max_pages_per_sync).
_PROVIDER_CONFIG_KEYS: dict[str, tuple[str, ...]] = {
    'onedrive': (
        'onedrive.enable',
        'onedrive.enable_sync',
        'onedrive.sync_interval_minutes',
        'onedrive.max_files_per_sync',
    ),
    'google_drive': (
        'google_drive.enable',
        'google_drive.enable_sync',
        'google_drive.sync_interval_minutes',
        'google_drive.max_files_per_sync',
    ),
    'confluence': (
        'confluence.enable',
        'confluence.enable_sync',
        'confluence.sync_interval_minutes',
        'confluence.max_pages_per_sync',
        'confluence.auth_mode',
        'confluence.site_url',
        'confluence.cloud_id',
        'confluence.basic_auth_username',
        'confluence.basic_auth_api_token',
        'confluence.scoped_api_token',
    ),
}

_PROVIDER_MAX_FILES_KEY: dict[str, str] = {
    'onedrive': 'onedrive.max_files_per_sync',
    'google_drive': 'google_drive.max_files_per_sync',
    'confluence': 'confluence.max_pages_per_sync',
}


def _auth_block(provider: str, config: dict) -> dict:
    """Provider auth material for the daemon. OAuth-only providers carry just
    the mode; Confluence service modes (basic/scoped) need the admin service
    credentials — the token broker has no per-user session for them."""
    if provider != 'confluence':
        return {'mode': 'oauth'}
    return {
        'mode': config.get('confluence.auth_mode') or 'oauth',
        'site_url': config.get('confluence.site_url'),
        'cloud_id': config.get('confluence.cloud_id'),
        'basic_auth_username': config.get('confluence.basic_auth_username'),
        'basic_auth_api_token': config.get('confluence.basic_auth_api_token'),
        'scoped_api_token': config.get('confluence.scoped_api_token'),
    }


class TokenRequest(BaseModel):
    user_id: str
    provider: str
    knowledge_id: str | None = None


class RunSummaryForm(BaseModel):
    provider: str
    status: str  # started | heartbeat | completed | completed_with_errors | failed | cancelled
    counts: dict = {}
    error: str | None = None
    error_codes: dict = {}
    sources: list | None = None
    revoked: list = []
    failed_files: list = []
    staged_file_ids: list = []


@router.post('/token')
async def issue_provider_token(
    body: TokenRequest,
    principal: SyncPrincipal = Depends(get_sync_principal),
):
    """Token broker: hand the daemon a short-lived provider access token.

    Reuses the provider's TokenManager (refresh-on-expiry, needs_reauth
    fan-out). Never returns refresh tokens. 404 ``{"needs_reauth": true}``
    when no valid token exists or the refresh failed.
    """

    await _require_sync_daemon_enabled()
    if body.provider not in _PROVIDERS:
        raise HTTPException(status_code=400, detail=f"unknown sync provider '{body.provider}'")
    if body.user_id != principal.user.id:
        raise HTTPException(
            status_code=403,
            detail='X-Acting-User-Id must match the requested user_id — the daemon acts as the KB owner',
        )

    access_token = await get_token_manager(body.provider).get_valid_access_token(body.user_id, body.knowledge_id or '')
    if not access_token:
        return JSONResponse(status_code=404, content={'needs_reauth': True})

    session = await OAuthSessions.get_session_by_provider_and_user_id(body.provider, body.user_id)
    expires_at = ((session.token if session else None) or {}).get('expires_at')
    log.info(
        'sync-daemon token grant: user=%s provider=%s knowledge_id=%s',
        body.user_id,
        body.provider,
        body.knowledge_id or '-',
    )
    return {'access_token': access_token, 'expires_at': expires_at}


@router.get('/config/{provider}')
async def get_provider_sync_config(
    provider: str,
    _: None = Depends(get_sync_service_principal),
):
    """Per-provider admin sync config + the KB registry (D-2 scheduler feed).

    Service-level auth (bearer only): the scheduler reads config before it
    knows any KB owner, so there is no acting user to require here.
    """

    await _require_sync_daemon_enabled()
    entry = _PROVIDERS.get(provider)
    if not entry:
        raise HTTPException(status_code=400, detail=f"unknown sync provider '{provider}'")

    config = await Config.get_many(*_PROVIDER_CONFIG_KEYS[provider])
    knowledge_bases = await Knowledges.get_knowledge_bases_by_type(entry['type'])
    meta_key = entry['meta_key']
    registry = [
        {'id': kb.id, 'user_id': kb.user_id, 'meta': ((kb.meta or {}).get(meta_key) or {})} for kb in knowledge_bases
    ]
    return {
        'provider': provider,
        'enabled': bool(config.get(f'{provider}.enable')) and bool(config.get(f'{provider}.enable_sync')),
        'sync_interval_minutes': config.get(f'{provider}.sync_interval_minutes'),
        'max_files_per_sync': config.get(_PROVIDER_MAX_FILES_KEY[provider]),
        'auth': _auth_block(provider, config),
        # Single-sourced from PROVIDER_FILE_ID_PREFIXES (R2): lets the daemon
        # resolve delta-feed deletions (prefix + source_id) for /sync/cleanup
        # in partial-manifest mode without hand-duplicating the prefix table.
        'file_id_prefix': file_id_prefix_for(provider),
        'knowledge_bases': registry,
    }


@router.post('/runs/{knowledge_id}/summary')
async def post_run_summary(
    knowledge_id: str,
    form: RunSummaryForm,
    principal: SyncPrincipal = Depends(get_sync_principal),
    db: AsyncSession = Depends(get_async_session),
):
    """Persist a daemon run-state report onto the KB's sync meta (D-3).

    Heartbeat on every call; ``last_sync_at``/``last_result`` on terminal
    statuses; the ``sources`` cursor only on clean terminal runs (R8, enforced
    server-side here). On terminal statuses also fail-marks staged files the
    run left non-terminal and emits the provider's ``{prefix}:sync:progress``
    socket event.
    """

    await _require_sync_daemon_enabled()
    if form.status not in _RUN_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"invalid run status '{form.status}' (expected one of {sorted(_RUN_STATUSES)})",
        )

    knowledge = await _verify_knowledge_write_access(knowledge_id, principal.user, db)

    # Registered providers use their registry meta_key; anything else falls
    # back to the '{slug}_sync' convention — the same total-function stance as
    # file_id_prefix_for, so daemon-era providers (and the stub E2E harness)
    # don't need a registry entry to report run state.
    entry = _PROVIDERS.get(form.provider)
    meta_key = entry['meta_key'] if entry else f'{form.provider}_sync'
    meta = dict(knowledge.meta or {})
    sync_info = dict(meta.get(meta_key) or {})
    cursor_persisted = _apply_run_summary(sync_info, form, int(time.time()))
    if form.status in _TERMINAL_STATUSES:
        await _stamp_source_root_directories(knowledge_id, sync_info, db)
    meta[meta_key] = sync_info
    await Knowledges.update_knowledge_meta_by_id(knowledge_id, meta)

    fail_marked = 0
    if form.status in _TERMINAL_STATUSES:
        fail_marked = await _fail_mark_staged_files(form)

    await _emit_run_progress(principal.user.id, knowledge_id, form, sync_info['status'])
    return {'status': True, 'cursor_persisted': cursor_persisted, 'fail_marked': fail_marked}


async def _require_sync_daemon_enabled():
    """403 unless the per-tenant ``sync_daemon.enabled`` flag is on."""
    if not await Config.get('sync_daemon.enabled', False):
        raise HTTPException(status_code=403, detail='sync daemon is disabled (SYNC_DAEMON_ENABLED)')


def _cursor_may_advance(status: str, error_codes: dict) -> bool:
    """R8: delta cursors advance only on clean terminal runs.

    ``completed`` always advances; ``completed_with_errors`` advances only
    when every reported error code is non-retryable (re-running would not fix
    those items, so freezing the cursor would just force a full re-walk).
    Every other terminal status freezes the cursor.
    """

    if status == 'completed':
        return True
    if status != 'completed_with_errors':
        return False
    return not (set(error_codes) - _NON_RETRYABLE_ERROR_CODES)


def _apply_run_summary(sync_info: dict, form: RunSummaryForm, now: int) -> bool:
    """Apply a run-summary callback onto the KB's ``meta[meta_key]`` blob.

    Mutates ``sync_info`` in place; returns whether the ``sources`` cursor was
    persisted. Non-terminal reports (started/heartbeat) map to the UI's
    ``syncing`` status and never touch cursors or ``last_sync_at``.
    """

    sync_info['sync_heartbeat'] = now
    if form.status == 'started':
        sync_info['status'] = 'syncing'
        sync_info['sync_started_at'] = now
    elif form.status == 'heartbeat':
        sync_info['status'] = 'syncing'
    else:
        sync_info['status'] = form.status
    if form.error:
        sync_info['error'] = form.error

    if form.status not in _TERMINAL_STATUSES:
        return False

    sync_info['last_sync_at'] = now
    sync_info['last_result'] = {**form.counts, 'failed_files': form.failed_files}
    cursor_persisted = form.sources is not None and _cursor_may_advance(form.status, form.error_codes)
    if cursor_persisted:
        sync_info['sources'] = form.sources
    return cursor_persisted


async def _stamp_source_root_directories(knowledge_id: str, sync_info: dict, db) -> bool:
    """Backfill ``sources[].root_directory_id`` from materialized root dirs.

    P2-8 decision 2: a folder-like source's directory chain is rooted at a
    KB-root directory named after the source. The link-time reverse bridge
    stamps this on the loader-worker path, but the daemon creates directories
    itself and passes ``directory_id`` at ``/stage`` — that bridge never runs
    — so the stamp lands here on terminal summaries instead. Re-applied
    whenever the stored id diverges, so it self-heals (e.g. after a source
    root was deleted and recreated).
    """
    sources = sync_info.get('sources') or []
    folderish = [s for s in sources if isinstance(s, dict) and s.get('type') != 'file']
    if not folderish:
        return False

    roots = await Knowledges.get_directories(knowledge_id, parent_id=None, db=db)
    root_id_by_name = {d.name: d.id for d in roots}

    changed = False
    for source in folderish:
        root_id = root_id_by_name.get(source.get('name'))
        if root_id and source.get('root_directory_id') != root_id:
            source['root_directory_id'] = root_id
            changed = True
    return changed


async def _fail_mark_staged_files(form: RunSummaryForm) -> int:
    """Transition still-pending staged rows to a terminal status.

    Mirrors ``base_worker._fail_mark_outstanding_stubs``: rows already
    ``completed`` / ``error`` are authoritative ``/ingest`` writes and are
    left alone; everything else is fail-marked so no per-file spinner
    outlives the run. Returns the number of rows changed.
    """

    error_status = 'cancelled' if form.status == 'cancelled' else 'error'
    message = (
        'Sync cancelled by user'
        if form.status == 'cancelled'
        else (form.error or 'sync run ended with file still pending')
    )
    changed = 0
    for file_id in form.staged_file_ids:
        file = await Files.get_file_by_id(file_id)
        if file is None:
            continue
        if (file.data or {}).get('status') in ('completed', 'error'):
            continue
        await Files.set_status(file_id, error_status, error=message)
        changed += 1
    return changed


async def _emit_run_progress(user_id: str, knowledge_id: str, form: RunSummaryForm, emitted_status: str):
    """Emit the provider's ``{prefix}:sync:progress`` event from the summary."""

    counts = form.counts or {}
    await emit_sync_progress(
        provider_prefix=_PROVIDER_EVENT_PREFIXES.get(form.provider, form.provider),
        user_id=user_id,
        knowledge_id=knowledge_id,
        status=emitted_status,
        current=int(counts.get('current', 0) or 0),
        total=int(counts.get('total', 0) or 0),
        error=form.error,
        files_processed=int(counts.get('files_processed', 0) or 0),
        files_failed=int(counts.get('files_failed', 0) or 0),
        deleted_count=int(counts.get('deleted_count', 0) or 0),
        files_added=int(counts.get('files_added', 0) or 0),
        files_updated=int(counts.get('files_updated', 0) or 0),
        files_unchanged=int(counts.get('files_unchanged', 0) or 0),
        files_removed=int(counts.get('files_removed', 0) or 0),
        failed_files=form.failed_files or None,
        needs_reauth='needs_token_refresh' in (form.error_codes or {}),
    )
