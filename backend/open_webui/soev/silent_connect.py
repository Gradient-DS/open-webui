"""Silent Microsoft reconnect after an Entra login, as a chain of top-level redirects.

After login, each OneDrive or Outlook connection that still needs consent gets one
silent attempt (prompt=none) at soev-api. The provider returns to the consent done
page, which continues with the next connection or lands the person in the app.
Every connection is tried at most once per person, so a failure never loops; it
leaves the manual reconnect in place.
"""

import logging
import time
import traceback
from pathlib import Path

from fastapi import Request

from open_webui.models.config import Config
from open_webui.models.users import UserModel, Users
from open_webui.soev import capabilities, identity
from open_webui.soev.availability import configured
from open_webui.soev.client import SoevApiError
from open_webui.soev.cloud_sync import CloudSync

log = logging.getLogger(__name__)

PROVIDERS = frozenset({'onedrive', 'outlook_mail'})
NEEDS_CONSENT = frozenset({'pending', 'suspended:reauth'})
# A user settings key, merged at the top level like every other settings write.
SETTINGS_KEY = 'soev_silent_connect'
# soev-api's attempt lifetime: a chain that has not returned by then is over.
CHAIN_SECONDS = 300
TIMEOUT_SECONDS = 10.0


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
    if not await capabilities.refresh() & {
        capabilities.CLOUD_SYNC,
        capabilities.LIVE_DOCUMENTS,
        capabilities.LIVE_MAIL,
    }:
        return None
    client = identity.build_client(timeout=TIMEOUT_SECONDS)
    sync = CloudSync(client, await identity.acting_ref(user, client))
    state = await _state(user.id)
    candidate = next(
        (
            row
            for row in await sync.connections()
            if row.get('source_kind') in PROVIDERS
            and row.get('lifecycle') in NEEDS_CONSENT
            and row.get('id') not in state['tried']
        ),
        None,
    )
    if candidate is None:
        return None
    # Marked before the attempt exists: whatever happens next, this connection is not tried again.
    await _save(user.id, tried=[*state['tried'], candidate['id']], inflight=None)
    authorization = await sync.authorize(candidate['id'], owner_email=user.email, silent=True)
    await _save(user.id, inflight={'connection': candidate['id'], 'at': int(time.time())})
    log.info('Silent reconnect started', extra={'user_id': user.id, 'connection_id': candidate['id']})
    return authorization['authorize_url']


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
    try:
        url = await next_hop(user)
    except Exception as error:
        log.warning('Silent reconnect stopped: %s', _code(error), extra={'user_id': user.id, 'code': _code(error)})
        url = None
    if url:
        return url
    webui_url = await Config.get('webui.url')
    return f'{str(webui_url or request.base_url).rstrip("/")}/auth'


async def _state(user_id: str) -> dict:
    user = await Users.get_user_by_id(user_id)
    # Settings come back as a UserSettings model (extra keys allowed), or None.
    settings = user.settings.model_dump() if user and user.settings else {}
    stored = settings.get(SETTINGS_KEY)
    stored = stored if isinstance(stored, dict) else {}
    tried = stored.get('tried')
    inflight = stored.get('inflight')
    return {
        'tried': [key for key in tried if isinstance(key, str)] if isinstance(tried, list) else [],
        'inflight': inflight if isinstance(inflight, dict) else None,
    }


async def _save(user_id: str, **changes) -> None:
    await Users.update_user_settings_by_id(user_id, {SETTINGS_KEY: {**await _state(user_id), **changes}})


def _code(error: Exception) -> str:
    """The API error code, or the exception type and the line that raised it; never its message."""
    if isinstance(error, SoevApiError):
        return error.code
    frame = traceback.extract_tb(error.__traceback__)[-1] if error.__traceback__ else None
    where = f' at {Path(frame.filename).name}:{frame.lineno}' if frame else ''
    return type(error).__name__ + where
