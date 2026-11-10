"""Select platform accounts only by immutable subjects and verified persons."""

import logging

from sqlalchemy import String, cast, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui import config
from open_webui.internal.db import get_async_db_context
from open_webui.models.config import Config
from open_webui.models.users import User, UserModel
from open_webui.soev.client import SoevApiError, SoevClient
from open_webui.soev.principal_refs import is_canonical_person_ref

log = logging.getLogger(__name__)


async def validate_login_config() -> None:
    if not config.SOEV_API_URL:
        return
    for claim in (config.OAUTH_SUB_CLAIM, await Config.get('oauth.sub_claim', config.OAUTH_SUB_CLAIM)):
        if claim not in (None, '', 'sub'):
            raise ValueError(
                'With SOEV_API_URL configured, OAUTH_SUB_CLAIM and oauth.sub_claim must be "sub" or unset; '
                'other claims cannot identify a platform account.'
            )


def _refuse_binding(provider: str) -> None:
    log.warning('OAuth login denied', extra={'code': 'oauth_person_provider_conflict', 'provider': provider})
    raise SoevApiError(403, 'policy_forbids', 'Login refused', 'oauth:person_binding')


async def _discover_person(access_token: str | None) -> str:
    try:
        if not access_token:
            raise SoevApiError(502, 'upstream_error', 'Missing login token')
        payload = await SoevClient(config.SOEV_API_URL, config.SOEV_API_KEY).me(access_token)
        person_id = payload.get('person_id')
        if not is_canonical_person_ref(person_id):
            raise SoevApiError(502, 'upstream_error', 'Invalid person response')
    except SoevApiError:
        log.warning('OAuth person discovery failed', extra={'code': 'oauth_person_discovery_failed'})
        raise SoevApiError(
            503, 'service_unavailable', 'Login temporarily unavailable', 'oauth:person_discovery'
        ) from None
    return person_id


async def attach_person_login(
    provider: str,
    sub: str,
    access_token: str | None,
    *,
    user: UserModel | None = None,
    db: AsyncSession | None = None,
) -> UserModel | None:
    if not config.SOEV_API_URL:
        return user
    if user is not None and user.person_id is None:
        return user
    person_id = await _discover_person(access_token)
    if user is not None:
        if user.person_id != person_id:
            log.warning('OAuth login denied', extra={'code': 'oauth_person_mismatch', 'provider': provider})
            raise SoevApiError(403, 'policy_forbids', 'Login refused', 'oauth:person_binding')
        return user
    return await _attach_login(provider, sub, person_id, db=db)


async def _attach_login(provider: str, sub: str, person_id: str, *, db: AsyncSession | None) -> UserModel | None:
    async with get_async_db_context(db) as session:
        result = (
            await session.execute(
                select(User, cast(User.oauth, String).label('stored_oauth'))
                .where(User.person_id == person_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).one_or_none()
        if result is None:
            return None
        user, stored_oauth = result
        if user.oauth is not None and not isinstance(user.oauth, dict):
            _refuse_binding(provider)
        bindings = dict(user.oauth or {})
        if provider in bindings:
            binding = bindings[provider]
            if not isinstance(binding, dict):
                _refuse_binding(provider)
            bound_sub = binding.get('sub')
            if bound_sub is not None:
                if str(bound_sub) != sub:
                    _refuse_binding(provider)
                return UserModel.model_validate(user)
        bindings[provider] = {**bindings.get(provider, {}), 'sub': sub}
        # SQLite has no row locks. Compare the stored JSON text as well so a
        # concurrent attachment cannot overwrite any provider's winning binding.
        unchanged = cast(User.oauth, String) == stored_oauth if stored_oauth is not None else User.oauth.is_(None)
        attached = await session.scalar(
            update(User)
            .where(User.id == user.id, User.person_id == person_id, unchanged)
            .values(oauth=bindings)
            .returning(User)
            .execution_options(populate_existing=True, synchronize_session=False)
        )
        if attached is None:
            log.warning('OAuth attachment raced', extra={'code': 'oauth_person_attach_race', 'provider': provider})
            raise SoevApiError(503, 'service_unavailable', 'Retry login', 'oauth:person_discovery')
        await session.commit()
        return UserModel.model_validate(attached)
