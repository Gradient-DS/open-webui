"""Silent Microsoft connect after an Entra login, as a chain of top-level redirects.

After login, per provider without an enabled connection, the newest OneDrive or Outlook
connection that still needs consent gets one silent attempt (prompt=none) at soev-api.
With auto-connect on, a provider the person holds no connection for gets one first,
unless they disconnected it themselves.
The provider returns to the consent done page, which continues with the next connection
or lands the person in the app.
Every connection is tried at most once per person, so a failure never loops; it
leaves the manual connect in place.

The live family of an enabled connection is granted once, when the app first loads:
soev-api enables a connection only after its token exchange, a few seconds after the chain.
"""

import asyncio
import logging
import time
import traceback
from pathlib import Path

from fastapi import Request

from open_webui import config
from open_webui.models.config import Config
from open_webui.models.users import UserModel, Users
from open_webui.soev import capabilities, identity, request_cache
from open_webui.soev.availability import configured
from open_webui.soev.client import SoevApiError
from open_webui.soev.cloud_sync import CloudSync

log = logging.getLogger(__name__)

PROVIDERS = frozenset({'onedrive', 'outlook_mail'})
# The live family each provider's connection carries, and the capability that offers it.
FAMILIES = {'onedrive': 'live_documents', 'outlook_mail': 'mail'}
FAMILY_CAPABILITIES = {'onedrive': capabilities.LIVE_DOCUMENTS, 'outlook_mail': capabilities.LIVE_MAIL}
NEEDS_CONSENT = frozenset({'pending', 'suspended:reauth'})
# A user settings key, merged at the top level like every other settings write.
SETTINGS_KEY = 'soev_silent_connect'
# soev-api's attempt lifetime: a chain that has not returned by then is over.
CHAIN_SECONDS = 300
TIMEOUT_SECONDS = 10.0
# How long the app's first load waits for a token exchange the chain started.
EXCHANGE_WAIT_SECONDS = 15.0
EXCHANGE_POLL_SECONDS = 1.0


async def after_login(user: UserModel, provider: str, redirect_url: str) -> str:
    """Send an Entra login through a silent reconnect, or keep its own destination."""
    if provider != 'microsoft' or not configured():
        return redirect_url
    try:
        return await next_hop(user) or redirect_url
    except Exception as error:  # the login itself must never fail on this
        log.warning('Silent reconnect skipped: %s', _code(error), extra={'user_id': user.id, 'code': _code(error)})
        return redirect_url


async def next_hop(user: UserModel) -> str | None:
    """Start the silent attempt for the next untried connection, if there is one."""
    features = await capabilities.refresh()
    if not features & {
        capabilities.CLOUD_SYNC,
        capabilities.LIVE_DOCUMENTS,
        capabilities.LIVE_MAIL,
    }:
        return None
    client = identity.build_client(timeout=TIMEOUT_SECONDS)
    sync = CloudSync(client, identity.external_ref(user))
    state = await _state(user.id)
    connections = await sync.connections()
    connections += await _open_missing(user, sync, connections, features, state['declined'])
    candidate = next((row for row in _candidates(connections) if row['id'] not in state['tried']), None)
    if candidate is None:
        return None
    # Marked before the attempt exists: whatever happens next, this connection is not tried again.
    await _save(user.id, tried=[*state['tried'], candidate['id']], inflight=None)
    authorization = await sync.authorize(candidate['id'], owner_email=user.email, silent=True)
    await _save(user.id, inflight={'connection': candidate['id'], 'at': int(time.time())})
    log.info('Silent reconnect started', extra={'user_id': user.id, 'connection_id': candidate['id']})
    return authorization['authorize_url']


async def _open_missing(
    user: UserModel, sync: CloudSync, connections: list[dict], features: frozenset[str], declined: list[str]
) -> list[dict]:
    """With auto-connect on, a connection for each offered provider the person holds none of.

    A revoked connection does not count as held; a provider the person disconnected does
    not come back on its own.
    """
    if not config.SOEV_AUTO_CONNECT:
        return []
    held = {row.get('source_kind') for row in connections if row.get('lifecycle') != 'revoked'}
    opened = []
    for provider in sorted(PROVIDERS):
        if FAMILY_CAPABILITIES[provider] not in features or provider in held or provider in declined:
            continue
        try:
            connection = await sync.open_connection(provider)
        except SoevApiError as error:
            log.warning('Auto-connect skipped: %s', error.code, extra={'user_id': user.id, 'provider': provider})
            continue
        log.info('Auto-connect opened', extra={'user_id': user.id, 'connection_id': connection['id']})
        opened.append(connection)
    return opened


def _candidates(connections: list[dict]) -> list[dict]:
    """Per provider, the one connection worth a silent attempt, in listing order.

    A provider with an enabled connection needs none: re-authorizing a stale pending row
    would only turn it into an enabled duplicate. Otherwise only the newest connection
    needing consent is tried, never every leftover pending row.
    """
    enabled = {row.get('source_kind') for row in connections if row.get('lifecycle') == 'enabled'}
    newest: dict[str, dict] = {}
    for row in connections:
        provider = row.get('source_kind')
        if provider not in PROVIDERS or provider in enabled or row.get('lifecycle') not in NEEDS_CONSENT:
            continue
        if provider not in newest or (row.get('created_at') or '') > (newest[provider].get('created_at') or ''):
            newest[provider] = row
    chosen = {id(row) for row in newest.values()}
    return [row for row in connections if id(row) in chosen]


async def returned(user: UserModel, connection_id: str) -> bool:
    """Whether this done-page visit ends a silent attempt; it can end it only once."""
    inflight = (await _state(user.id))['inflight']
    if not inflight or time.time() - inflight.get('at', 0) >= CHAIN_SECONDS:
        return False
    # soev-api leaves the connection empty when the attempt itself was invalid.
    if connection_id not in ('', inflight.get('connection')):
        return False
    await _save(user.id, inflight=None)
    return True


async def continue_chain(request: Request, user: UserModel, connection_id: str, result: str) -> str:
    """The next silent attempt, or the app's sign-in landing that resumes where the person was going."""
    log.info(
        'Silent reconnect returned',
        extra={'user_id': user.id, 'connection_id': connection_id, 'result': result},
    )
    if result == 'pending' and connection_id:
        # The exchange is queued: the app's first load waits for it, then grants the live family.
        exchanging = (await _state(user.id))['exchanging']
        await _save(user.id, exchanging=[*exchanging, {'connection': connection_id, 'at': int(time.time())}])
    try:
        url = await next_hop(user)
    except Exception as error:
        log.warning('Silent reconnect stopped: %s', _code(error), extra={'user_id': user.id, 'code': _code(error)})
        url = None
    if url:
        return url
    webui_url = await Config.get('webui.url')
    return f'{str(webui_url or request.base_url).rstrip("/")}/auth'


async def grant_live(user: UserModel, sync: CloudSync) -> list[str]:
    """Turn on the live family of each enabled OneDrive and Outlook connection, once per connection.

    Only a connection without any grant for its family gets one: a grant that was later
    turned off or revoked stays that way. Returns the families granted now.
    """
    if not config.SOEV_AUTO_CONNECT:
        return []
    features = await capabilities.refresh()
    providers = [provider for provider in sorted(PROVIDERS) if FAMILY_CAPABILITIES[provider] in features]
    if not providers:
        return []
    state = await _state(user.id)
    connections = {row['id']: row for row in await sync.connections()}
    await _await_exchanges(sync, connections, state['exchanging'])
    done = set(state['auto_granted'])
    granted = []
    for provider in providers:
        enabled = [
            row
            for row in connections.values()
            if row.get('source_kind') == provider and row.get('lifecycle') == 'enabled' and not row.get('last_error')
        ]
        if not enabled:
            continue
        row = max(enabled, key=lambda row: row.get('created_at') or '')
        if row['id'] in done:
            continue
        done.add(row['id'])
        family = FAMILIES[provider]
        try:
            if await sync.live_grants(row['id'], family):
                continue
            await sync.enable_live_family(row['id'], family)
        except SoevApiError as error:
            log.warning(
                'Auto-connect grant skipped: %s', error.code, extra={'user_id': user.id, 'connection_id': row['id']}
            )
            continue
        log.info('Auto-connect granted', extra={'user_id': user.id, 'connection_id': row['id'], 'family': family})
        granted.append(family)
    await _save(user.id, auto_granted=sorted(done), exchanging=[])
    return granted


async def _await_exchanges(sync: CloudSync, connections: dict[str, dict], exchanging: list[dict]) -> None:
    """Wait, briefly, for the token exchanges a silent chain queued to enable their connections."""
    now = time.time()
    waiting = {
        entry['connection']
        for entry in exchanging
        if now - entry.get('at', 0) < CHAIN_SECONDS
        and connections.get(entry['connection'], {}).get('lifecycle') == 'pending'
    }
    deadline = time.monotonic() + EXCHANGE_WAIT_SECONDS
    while waiting and time.monotonic() < deadline:
        await asyncio.sleep(EXCHANGE_POLL_SECONDS)
        # The route memoizes reads per request; a poll must see the connection change.
        request_cache.invalidate()
        for connection_id in sorted(waiting):
            connections[connection_id] = row = await sync.connection(connection_id)
            if row.get('lifecycle') != 'pending' or row.get('last_error'):
                waiting.discard(connection_id)


async def decline(user_id: str, provider: str) -> None:
    """The person disconnected this provider: auto-connect leaves it alone from now on."""
    if provider in PROVIDERS:
        declined = (await _state(user_id))['declined']
        if provider not in declined:
            await _save(user_id, declined=[*declined, provider])


async def _state(user_id: str) -> dict:
    user = await Users.get_user_by_id(user_id)
    # Settings come back as a UserSettings model (extra keys allowed), or None.
    settings = user.settings.model_dump() if user and user.settings else {}
    stored = settings.get(SETTINGS_KEY)
    stored = stored if isinstance(stored, dict) else {}
    inflight = stored.get('inflight')
    exchanging = stored.get('exchanging')
    return {
        'tried': _strings(stored.get('tried')),
        'inflight': inflight if isinstance(inflight, dict) else None,
        'declined': _strings(stored.get('declined')),
        'auto_granted': _strings(stored.get('auto_granted')),
        'exchanging': [
            entry
            for entry in (exchanging if isinstance(exchanging, list) else [])
            if isinstance(entry, dict) and isinstance(entry.get('connection'), str)
        ],
    }


def _strings(value) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


async def _save(user_id: str, **changes) -> None:
    await Users.update_user_settings_by_id(user_id, {SETTINGS_KEY: {**await _state(user_id), **changes}})


def _code(error: Exception) -> str:
    """The API error code, or the exception type and the line that raised it; never its message."""
    if isinstance(error, SoevApiError):
        return error.code
    frame = traceback.extract_tb(error.__traceback__)[-1] if error.__traceback__ else None
    where = f' at {Path(frame.filename).name}:{frame.lineno}' if frame else ''
    return type(error).__name__ + where
