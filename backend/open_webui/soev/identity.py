"""Self-vouched OWUI identities and per-request Ed25519 subject assertions."""

import asyncio
import base64
import datetime as dt
import json
import logging
from uuid import UUID, uuid4, uuid5
from weakref import WeakValueDictionary

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from open_webui import config
from open_webui.models.users import UserModel, Users
from open_webui.soev.client import SoevApiError, SoevClient

log = logging.getLogger(__name__)

# This namespace is permanent: changing it would give existing people different platform ids.
OWUI_PLATFORM_NAMESPACE = UUID('ec855a67-541f-4b17-a7e2-0f8fbd09c47e')
_linked_refs: set[str] = set()
_link_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()


def external_ref(user: UserModel) -> str:
    return f'owui:user:{user.id}'


def platform_user_id(user_ref: str) -> str:
    return str(uuid5(OWUI_PLATFORM_NAMESPACE, user_ref))


async def user_of(ref: str) -> UserModel | None:
    if not ref.startswith('owui:user:'):
        return None
    user_id = ref.removeprefix('owui:user:')
    if not user_id or ':' in user_id:
        return None
    return await Users.get_user_by_id(user_id)


def signing_key() -> Ed25519PrivateKey:
    try:
        key = serialization.load_pem_private_key(config.SOEV_API_SIGNING_KEY.encode(), password=None)
    except (ValueError, TypeError):
        raise ValueError('SOEV_API_SIGNING_KEY must be an unencrypted Ed25519 PEM private key') from None
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError('SOEV_API_SIGNING_KEY must be an Ed25519 private key')
    return key


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip('=')


def mint_assertion(user_ref: str, *, now: dt.datetime) -> str:
    if now.utcoffset() is None:
        raise ValueError('Assertion time must include a timezone')
    issued_at = int(now.timestamp())
    # SOEV_API_SIGNING_KID holds the API-assigned kid printed by bootstrap.
    header = {'alg': 'Ed25519', 'kid': config.SOEV_API_SIGNING_KID, 'typ': 'JWT'}
    payload = {
        'iss': config.SOEV_API_CREDENTIAL_ID,
        'sub': user_ref,
        'aud': config.SOEV_API_AUDIENCE,
        'iat': issued_at,
        'exp': issued_at + 120,
        'jti': str(uuid4()),
    }
    message = '.'.join(_base64url(json.dumps(value, separators=(',', ':')).encode()) for value in (header, payload))
    return f'{message}.{_base64url(signing_key().sign(message.encode()))}'


async def ensure_link(user_ref: str, client: SoevClient) -> None:
    if user_ref in _linked_refs:
        return
    lock = _link_locks.setdefault(user_ref, asyncio.Lock())
    async with lock:
        if user_ref in _linked_refs:
            return
        await client.send(
            'POST',
            '/v1/identity/links',
            {
                'platform_user_id': platform_user_id(user_ref),
                'assertion': mint_assertion(user_ref, now=dt.datetime.now(dt.UTC)),
            },
            idempotency_key=f'link:{user_ref}',
        )
        _linked_refs.add(user_ref)


async def acting_ref(user: UserModel, client: SoevClient) -> str:
    ref = external_ref(user)
    await ensure_link(ref, client)
    return ref


async def link_proven(user: UserModel, *, source: str, id_token: str | None, client: SoevClient) -> None:
    try:
        if source != 'entra':
            raise SoevApiError(400, 'unsupported_source', 'Unsupported proven identity source')
        # Read only the candidate principal; soev-api verifies the token and its binding to this assertion.
        try:
            oid = jwt.decode(id_token, options={'verify_signature': False}).get('oid')
        except jwt.PyJWTError:
            oid = None
        if not isinstance(oid, str) or not oid or ':' in oid:
            raise SoevApiError(400, 'invalid_id_token', 'The Entra id token must carry an oid')
        await client.send(
            'POST',
            '/v1/identity/links',
            {
                'platform_user_id': platform_user_id(external_ref(user)),
                'assertion': mint_assertion(f'{source}:user:{oid}', now=dt.datetime.now(dt.UTC)),
                'id_token': id_token,
            },
            idempotency_key=f'link-proven:{uuid4()}',
        )
    except Exception as error:
        code = error.code if isinstance(error, SoevApiError) else 'link_failed'
        log.warning('Proven identity link failed', extra={'user_id': user.id, 'code': code})


def build_client() -> SoevClient:
    return SoevClient(
        config.SOEV_API_URL,
        config.SOEV_API_KEY,
        subject_minter=lambda ref: mint_assertion(ref, now=dt.datetime.now(dt.UTC)),
    )
