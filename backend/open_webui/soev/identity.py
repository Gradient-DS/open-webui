"""Local user references and login-only platform clients."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncSession

from open_webui import config
from open_webui.models.users import UserModel, Users
from open_webui.soev.availability import ensure_configured
from open_webui.soev.client import SoevApiError, SoevClient


def external_ref(user: UserModel) -> str:
    return f'owui:user:{user.id}'


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


def validate_config() -> None:
    if config.SOEV_API_URL:
        credential_id_from_key(config.SOEV_API_KEY)


def check_role_change(user: UserModel, role: str) -> None:
    if config.SOEV_API_URL and user.role in {'user', 'admin'} and role not in {'user', 'admin'}:
        raise SoevApiError(403, 'policy_forbids', 'Console suspension required', constraint='suspension:console')
    if role not in {'admin', 'user', 'pending'}:
        raise SoevApiError(400, 'invalid_field', 'Invalid user role', constraint='role')


@asynccontextmanager
async def role_change(user: UserModel, role: str, *, db: AsyncSession | None = None) -> AsyncIterator[None]:
    """Refuse suspension before changing platform membership or the local role."""
    check_role_change(user, role)
    yield


def build_client(*, timeout: float = 30.0) -> SoevClient:
    ensure_configured()
    return SoevClient(
        config.SOEV_API_URL,
        config.SOEV_API_KEY,
        timeout=timeout,
    )
