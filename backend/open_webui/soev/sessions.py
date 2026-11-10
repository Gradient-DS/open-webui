"""Session boundaries stay enforced when platform agent routing is disabled."""

import logging

from fastapi import HTTPException

from open_webui import config
from open_webui.constants import ERROR_MESSAGES
from open_webui.soev.principal_refs import is_canonical_person_ref

log = logging.getLogger(__name__)


def platform_mode() -> bool:
    return bool(config.SOEV_API_URL)


def require_session_purpose(claims: dict | None) -> None:
    if claims is None:
        return
    if platform_mode() and 'typ' in claims:
        raise HTTPException(401, detail=ERROR_MESSAGES.UNAUTHORIZED)
    if 'purpose' not in claims:
        return
    if not platform_mode() and claims['purpose'] in ('2fa_pending', '2fa_setup_pending'):
        raise HTTPException(403, detail='2FA verification required')
    raise HTTPException(401, detail=ERROR_MESSAGES.UNAUTHORIZED)


def require_session(user, claims: dict | None = None) -> None:
    require_session_purpose(claims)
    if platform_mode() and not is_canonical_person_ref(getattr(user, 'person_id', None)):
        raise HTTPException(401, detail=ERROR_MESSAGES.INVALID_TOKEN)


def session_user(user, claims: dict):
    try:
        require_session(user, claims)
    except HTTPException:
        return None
    return user


def refuse_background_credentials(method: str) -> None:
    if background_credentials_unavailable(method):
        raise HTTPException(403, detail=ERROR_MESSAGES.ACCESS_PROHIBITED)


def background_credentials_unavailable(method: str) -> bool:
    if not platform_mode():
        return False
    log.warning('Background session credential denied', extra={'code': f'platform_{method}_credential_refused'})
    return True
