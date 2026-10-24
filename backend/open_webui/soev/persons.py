"""Store platform person ids only from the requesting login's /v1/me response."""

import logging
from uuid import UUID

from sqlalchemy import select, update

from open_webui import config
from open_webui.internal.db import get_async_db_context
from open_webui.models.users import User, UserModel
from open_webui.soev.client import SoevApiError, SoevClient

log = logging.getLogger(__name__)


class PersonMismatchError(Exception):
    """The login's person differs from the person already stored for this user."""


async def remember_person(user: UserModel, access_token: str) -> None:
    """Store the login's person once; a soev-api failure leaves it for the next request.

    :raises PersonMismatchError: this user is already bound to another person.
    """
    try:
        person_id = _person_id(await SoevClient(config.SOEV_API_URL, config.SOEV_API_KEY).me(access_token))
    except SoevApiError as error:
        log.warning('person discovery failed', extra={'status': error.status, 'code': error.code})
        return
    async with get_async_db_context() as db:
        await db.execute(update(User).where(User.id == user.id, User.person_id.is_(None)).values(person_id=person_id))
        stored = await db.scalar(select(User.person_id).where(User.id == user.id))
        if stored != person_id:
            raise PersonMismatchError
        await db.commit()
    user.person_id = person_id


def _person_id(payload: dict) -> str:
    person_id = payload.get('person_id')
    try:
        if not isinstance(person_id, str) or not person_id.startswith('platform:user:'):
            raise ValueError
        UUID(person_id.removeprefix('platform:user:'))
    except ValueError:
        raise SoevApiError(502, 'upstream_error', 'Invalid person response') from None
    return person_id
