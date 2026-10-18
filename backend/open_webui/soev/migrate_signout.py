"""Sign every user out once, so each first Microsoft login after cutover proves the Entra link.

Uses the per-user revoked_at marker that password changes already set; WEBUI_SECRET_KEY,
which may also encrypt stored OAuth sessions, is left alone.
"""

from types import SimpleNamespace

import sqlalchemy as sa

from open_webui.soev.migrate_state import MigrationError


async def sign_out_all(*, db=None, redis=None) -> int:
    from open_webui.internal.db import get_async_db_context
    from open_webui.models.users import User
    from open_webui.utils.auth import revoke_user_tokens
    from open_webui.utils.redis import get_redis_client

    redis = redis if redis is not None else get_redis_client(async_mode=True)
    if redis is None:
        raise MigrationError('Signing users out needs Redis (REDIS_URL), where Open WebUI checks token revocation')
    async with get_async_db_context(db) as session:
        user_ids = (await session.execute(sa.select(User.id).order_by(User.id))).scalars().all()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=redis)))
    for user_id in user_ids:
        await revoke_user_tokens(request, user_id)
    return len(user_ids)
