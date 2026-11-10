"""Select platform accounts only by immutable subjects and verified persons."""

import logging
import time
import uuid

from fastapi import HTTPException
from sqlalchemy import String, cast, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui import config
from open_webui.constants import ERROR_MESSAGES
from open_webui.internal.db import get_async_db_context
from open_webui.models.auths import Auth, Auths
from open_webui.models.config import Config
from open_webui.models.users import User, UserModel
from open_webui.soev.client import SoevApiError, SoevClient
from open_webui.soev.principal_refs import is_canonical_person_ref
from open_webui.utils.validate import validate_profile_image_url

log = logging.getLogger(__name__)
_PLATFORM_SUB_CLAIMS = (None, '', 'sub')


def platform_mode() -> bool:
    # v2_enabled() also requires agent routing; login boundaries must survive its disablement.
    return bool(config.SOEV_API_URL)


def refuse_platform_login(method: str) -> None:
    if platform_mode():
        log.warning('Non-Keycloak login denied', extra={'code': f'oauth_platform_{method}_refused'})
        raise HTTPException(403, detail=ERROR_MESSAGES.ACCESS_PROHIBITED)


def refuse_platform_signin(trusted_header: str | None) -> None:
    refuse_platform_login('trusted_header' if trusted_header else 'password_signin')


def require_local_signup() -> None:
    refuse_platform_login('local_signup')


def refuse_platform_token_exchange() -> None:
    if platform_mode():
        log.warning('OAuth token exchange denied', extra={'code': 'oauth_platform_token_exchange_refused'})
        raise HTTPException(403, detail=ERROR_MESSAGES.ACCESS_PROHIBITED)


def refuse_unsafe_sub_claim(claim: str | None) -> None:
    if platform_mode() and claim not in _PLATFORM_SUB_CLAIMS:
        log.warning('OAuth configuration denied', extra={'code': 'oauth_unsafe_sub_claim_refused'})
        raise HTTPException(403, detail=ERROR_MESSAGES.ACCESS_PROHIBITED)


async def validate_login_config() -> None:
    if not platform_mode():
        return
    for claim in (config.OAUTH_SUB_CLAIM, await Config.get('oauth.sub_claim', config.OAUTH_SUB_CLAIM)):
        if claim not in _PLATFORM_SUB_CLAIMS:
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
) -> tuple[UserModel | None, str | None]:
    if not platform_mode():
        return user, None
    person_id = await _discover_person(access_token)
    if user is not None:
        if user.person_id is None:
            user = await _bind_person(user, person_id, provider, db=db)
        if user.person_id != person_id:
            _refuse_person(provider)
        return user, person_id
    return await _attach_login(provider, sub, person_id, db=db), person_id


def _refuse_person(provider: str) -> None:
    log.warning('OAuth login denied', extra={'code': 'oauth_person_mismatch', 'provider': provider})
    raise SoevApiError(403, 'policy_forbids', 'Login refused', 'oauth:person_binding')


async def _bind_person(user: UserModel, person_id: str, provider: str, *, db: AsyncSession | None) -> UserModel:
    async with get_async_db_context(db) as session:
        try:
            async with session.begin_nested():
                await session.execute(
                    update(User).where(User.id == user.id, User.person_id.is_(None)).values(person_id=person_id)
                )
                stored = await session.scalar(
                    select(User).where(User.id == user.id).with_for_update().execution_options(populate_existing=True)
                )
                if stored is None or stored.person_id != person_id:
                    _refuse_person(provider)
            await session.commit()
        except IntegrityError:
            await session.rollback()
            _refuse_person(provider)
        return UserModel.model_validate(stored)


def require_verified_person(user: UserModel, person_id: str | None) -> None:
    if platform_mode() and (not is_canonical_person_ref(person_id) or user.person_id != person_id):
        _refuse_person('oidc')


async def insert_oauth_account(
    *,
    provider: str,
    sub: str,
    person_id: str | None,
    email: str,
    password: str,
    name: str,
    profile_image_url: str,
    role: str,
    oauth: dict,
    db: AsyncSession | None = None,
) -> UserModel | None:
    if not platform_mode():
        return await Auths.insert_new_auth(
            email=email,
            password=password,
            name=name,
            profile_image_url=profile_image_url,
            role=role,
            oauth=oauth,
            db=db,
        )
    if not is_canonical_person_ref(person_id):
        _refuse_person(provider)
    try:
        picture = validate_profile_image_url(profile_image_url)
    except ValueError:
        picture = '/user.png'
    async with get_async_db_context(db) as session:
        try:
            async with session.begin_nested():
                user_id = str(uuid.uuid4())
                now = int(time.time())
                user = User(
                    id=user_id,
                    email=email,
                    name=name,
                    role=role,
                    profile_image_url=picture,
                    oauth=oauth,
                    person_id=person_id,
                    last_active_at=now,
                    created_at=now,
                    updated_at=now,
                )
                session.add_all([Auth(id=user_id, email=email, password=password, active=True), user])
                await session.flush()
            await session.commit()
            return UserModel.model_validate(user)
        except IntegrityError:
            await session.rollback()
            # A concurrent signup can win on person_id even if the email changed.
            user = await _attach_login(provider, sub, person_id, db=session)
            if user is None:
                log.warning('OAuth signup denied', extra={'code': 'oauth_email_conflict', 'provider': provider})
                raise HTTPException(403, detail=ERROR_MESSAGES.ACCESS_PROHIBITED) from None
            return user


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
