"""HTTP client for the tenant's external sync-daemon.

OWUI forwards the manual "Sync now" and cancel actions to the daemon's
trigger endpoints. The daemon is the sync client (upstream oikb is a passive
server); OWUI only pokes it — all heavy sync work runs in the daemon,
out-of-pod. The bearer key is never written to logs.
"""

from __future__ import annotations

import logging

import httpx

from open_webui.config import SYNC_DAEMON_API_KEY, SYNC_DAEMON_URL

log = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(10.0)


class SyncDaemonError(RuntimeError):
    """Raised when a sync-daemon trigger/cancel call cannot be completed."""


async def trigger_sync_run(knowledge_id: str, provider: str, user_id: str) -> None:
    """Ask the sync-daemon to start a run for a knowledge base.

    A 202 (accepted) or 409 (a run is already active — idempotent) is treated
    as success. Raises SyncDaemonError when the daemon URL is unset, the
    daemon is unreachable, or it returns any other status.
    """
    if not SYNC_DAEMON_URL:
        raise SyncDaemonError('SYNC_DAEMON_URL is not configured')

    url = f'{SYNC_DAEMON_URL.rstrip("/")}/sync/run'
    payload = {'knowledge_id': knowledge_id, 'provider': provider, 'user_id': user_id}

    log.info('Triggering sync-daemon run for KB %s (provider=%s)', knowledge_id, provider)
    status_code = await _post(url, json_body=payload)
    if status_code in (202, 409):
        return

    log.warning('sync-daemon run trigger for KB %s returned HTTP %s', knowledge_id, status_code)
    raise SyncDaemonError(f'sync-daemon run trigger returned HTTP {status_code}')


async def cancel_sync_run(knowledge_id: str) -> None:
    """Ask the sync-daemon to cancel the active run for a knowledge base.

    A 200 (cancelled) or 404 (nothing active — idempotent) is treated as
    success. Raises SyncDaemonError when the daemon URL is unset, the daemon
    is unreachable, or it returns any other status.
    """
    if not SYNC_DAEMON_URL:
        raise SyncDaemonError('SYNC_DAEMON_URL is not configured')

    url = f'{SYNC_DAEMON_URL.rstrip("/")}/sync/{knowledge_id}/cancel'

    log.info('Cancelling sync-daemon run for KB %s', knowledge_id)
    status_code = await _post(url)
    if status_code in (200, 404):
        return

    log.warning('sync-daemon cancel for KB %s returned HTTP %s', knowledge_id, status_code)
    raise SyncDaemonError(f'sync-daemon cancel returned HTTP {status_code}')


async def _post(url: str, json_body: dict | None = None) -> int:
    """POST to the daemon with the bearer header; return the HTTP status code.

    The API key is never logged. Connection/transport failures are reclassified
    as SyncDaemonError.
    """
    headers = {'Authorization': f'Bearer {SYNC_DAEMON_API_KEY}'}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(url, json=json_body, headers=headers)
    except httpx.HTTPError as err:
        log.warning('sync-daemon request to %s failed: %s', url, err)
        raise SyncDaemonError(f'sync-daemon unreachable at {url}: {err}') from err

    return response.status_code
