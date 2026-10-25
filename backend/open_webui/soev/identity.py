"""Local user references and legacy identity operations for tokenless background work."""

import asyncio
import base64
import datetime as dt
import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from functools import lru_cache
from uuid import UUID, uuid4, uuid5
from weakref import WeakValueDictionary

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui import config
from open_webui.models.users import UserModel, Users
from open_webui.soev.availability import ensure_configured
from open_webui.soev.client import BackgroundSoevClient, SoevApiError, SoevClient

# This namespace is permanent: changing it would give existing people different platform ids.
OWUI_PLATFORM_NAMESPACE = UUID('ec855a67-541f-4b17-a7e2-0f8fbd09c47e')
_linked_refs: set[str] = set()
_link_owner: ContextVar[tuple[asyncio.Task, str] | None] = ContextVar('soev_link_owner', default=None)
_link_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()


def external_ref(user: UserModel) -> str:
    return f'owui:user:{user.id}'


def platform_user_id(user_ref: str) -> str:
    return str(uuid5(OWUI_PLATFORM_NAMESPACE, user_ref))


async def user_of(ref: str, *, db: AsyncSession | None = None) -> UserModel | None:
    if not ref.startswith('owui:user:'):
        return None
    user_id = ref.removeprefix('owui:user:')
    if not user_id or ':' in user_id:
        return None
    return await Users.get_user_by_id(user_id, db=db) if db is not None else await Users.get_user_by_id(user_id)


@lru_cache(maxsize=1)
def credential_id_from_key(key: str) -> str:
    segments = key.split('_', 3)
    if len(segments) != 4 or segments[0] != 'soev' or not all(segments[1:]) or not segments[2].startswith('cred-'):
        raise ValueError('SOEV_API_KEY must have the form soev_<env>_<cred-id>_<secret> with non-empty segments')
    return segments[2]


def signing_key() -> Ed25519PrivateKey:
    return _load_signing_key(config.SOEV_API_SIGNING_KEY)


@lru_cache(maxsize=1)
def _load_signing_key(pem: str) -> Ed25519PrivateKey:
    try:
        key = serialization.load_pem_private_key(pem.encode(), password=None)
    except (ValueError, TypeError, UnsupportedAlgorithm):
        raise ValueError('SOEV_API_SIGNING_KEY must be an unencrypted Ed25519 PEM private key') from None
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError('SOEV_API_SIGNING_KEY must be an Ed25519 private key')
    return key


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip('=')


@lru_cache(maxsize=1)
def jwk_thumbprint(private_key: Ed25519PrivateKey) -> str:
    raw = private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    jwk = {'crv': 'Ed25519', 'kty': 'OKP', 'x': _base64url(raw)}
    canonical = json.dumps(jwk, sort_keys=True, separators=(',', ':')).encode()
    return _base64url(hashlib.sha256(canonical).digest())


def validate_config() -> None:
    if config.SOEV_API_URL:
        credential_id_from_key(config.SOEV_API_KEY)


def mint_assertion(user_ref: str, *, now: dt.datetime) -> str:
    from open_webui.soev.acting import acting_ref, requester_ref

    if requester_ref() is not None or acting_ref() is not None:
        raise SoevApiError(401, 'sign_in', 'User requests cannot mint legacy assertions')
    if now.utcoffset() is None:
        raise ValueError('Assertion time must include a timezone')
    issued_at = int(now.timestamp())
    key = signing_key()
    header = {'alg': 'Ed25519', 'kid': jwk_thumbprint(key), 'typ': 'subject+jwt'}
    payload = {
        'iss': credential_id_from_key(config.SOEV_API_KEY),
        'sub': user_ref,
        'aud': config.SOEV_API_AUDIENCE,
        'iat': issued_at,
        'exp': issued_at + 120,
        'jti': str(uuid4()),
    }
    message = '.'.join(_base64url(json.dumps(value, separators=(',', ':')).encode()) for value in (header, payload))
    return f'{message}.{_base64url(key.sign(message.encode()))}'


@asynccontextmanager
async def link_lock(user_ref: str) -> AsyncIterator[None]:
    """Keep role changes and offboarding ordered with ordinary first-link requests."""
    owner = (asyncio.current_task(), user_ref)
    if _link_owner.get() == owner:
        yield
        return
    async with _link_locks.setdefault(user_ref, asyncio.Lock()):
        token = _link_owner.set(owner)
        try:
            yield
        finally:
            _link_owner.reset(token)


async def ensure_link(user_ref: str, client: SoevClient, *, db: AsyncSession | None = None) -> None:
    """Link an existing eligible user; cached links never bypass the role check."""
    async with link_lock(user_ref):
        user = await user_of(user_ref, db=db)
        if user is None or user.role not in {'user', 'admin'}:
            raise SoevApiError(
                403, 'scope_insufficient', 'Only approved users can be linked', constraint='identity:eligible_role'
            )
        await _ensure_link(user_ref, client)


async def _ensure_link(user_ref: str, client: SoevClient) -> None:
    if user_ref in _linked_refs:
        return
    await client.as_service().send(
        'POST',
        '/v1/identity/links',
        {
            'platform_user_id': platform_user_id(user_ref),
            'assertion': mint_assertion(user_ref, now=dt.datetime.now(dt.UTC)),
        },
        idempotency_key=f'link:{uuid4()}',
    )
    _linked_refs.add(user_ref)


async def unlink(user_ref: str, client: SoevClient) -> None:
    """Remove only the OWUI product link and invalidate successful-link caches."""
    async with link_lock(user_ref):
        await _unlink(user_ref, client)


async def _unlink(user_ref: str, client: SoevClient) -> None:
    await client.as_service().send(
        'DELETE',
        f'/v1/identity/links/{platform_user_id(user_ref)}?source=owui',
        None,
        idempotency_key=f'unlink:{uuid4()}',
    )
    _linked_refs.discard(user_ref)


def check_role_change(user: UserModel, role: str) -> None:
    if config.SOEV_API_URL and role == 'pending' and user.role in {'user', 'admin'}:
        raise SoevApiError(403, 'policy_forbids', 'Console suspension required', constraint='suspension:console')


@asynccontextmanager
async def role_change(user: UserModel, role: str, *, db: AsyncSession | None = None) -> AsyncIterator[None]:
    """Refuse suspension before changing platform membership or the local role."""
    check_role_change(user, role)
    if not config.SOEV_API_URL or user.role == role:
        yield
        return
    from open_webui.soev import group_sync

    ref = external_ref(user)
    async with link_lock(ref):
        if user.role == 'admin' and role != 'admin':
            await group_sync.narrow_admin(user.id, db=db)
        if role not in {'user', 'admin'}:
            await _unlink(ref, build_client())
        yield
    if role == 'admin':
        from open_webui.models.groups import Groups

        await group_sync.publish(Groups, [group_sync.ADMIN_ARCHIVE_GROUP], db=db)


def build_client(*, timeout: float = 30.0) -> SoevClient:
    ensure_configured()
    return SoevClient(
        config.SOEV_API_URL,
        config.SOEV_API_KEY,
        timeout=timeout,
    )


def build_background_client(*, timeout: float = 30.0) -> BackgroundSoevClient:
    ensure_configured()
    return BackgroundSoevClient(
        config.SOEV_API_URL,
        config.SOEV_API_KEY,
        subject_minter=lambda ref: mint_assertion(ref, now=dt.datetime.now(dt.UTC)),
        timeout=timeout,
    )
