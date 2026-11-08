"""Keep owner-scoped job references; soev remains authoritative for their outcomes."""

from sqlalchemy import select

from open_webui.internal import db as database
from open_webui.models.users import User

_SETTING = 'soev_knowledge_access_jobs'


async def references(ref: str | None) -> dict:
    if ref is None or not ref.startswith('owui:user:'):
        return {}
    async with database.get_async_db_context() as db:
        user = await db.get(User, ref.removeprefix('owui:user:'))
        return dict((user.settings or {}).get(_SETTING, {})) if user is not None else {}


async def remember(ref: str | None, key: str, job: str, terminal: str | None) -> None:
    if ref is None or not ref.startswith('owui:user:'):
        return
    async with database.get_async_db_context() as db:
        user = await db.scalar(select(User).where(User.id == ref.removeprefix('owui:user:')).with_for_update())
        if user is None:
            return
        settings = dict(user.settings or {})
        settings[_SETTING] = {**settings.get(_SETTING, {}), key: {'job_id': job, 'terminal': terminal}}
        user.settings = settings
        await db.commit()
