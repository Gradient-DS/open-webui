"""Session-only person cutover; the caller owns commit and rollback."""

from collections.abc import Collection, Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.models.users import User
from open_webui.soev.principal_refs import is_canonical_person_ref


async def cutover_persons(
    db: AsyncSession, person_map: Mapping[str, str], excluded_ids: Collection[str] = ()
) -> dict[str, int]:
    targets = dict(person_map)
    if isinstance(excluded_ids, (str, bytes)) or not all(isinstance(id, str) and id for id in excluded_ids):
        raise ValueError('excluded_ids must contain user ids')
    excluded = set(excluded_ids)
    if targets.keys() & excluded:
        raise ValueError('User ids cannot be both mapped and excluded')
    if not all(is_canonical_person_ref(ref) for ref in targets.values()):
        raise ValueError('person_map values must be canonical platform person refs')
    if len(set(targets.values())) != len(targets):
        raise ValueError('person_map must be one-to-one')
    with db.no_autoflush:
        users = (await db.scalars(select(User).with_for_update().execution_options(populate_existing=True))).all()
    user_ids = {user.id for user in users}
    if (targets.keys() | excluded) - user_ids:
        raise ValueError('Unknown user ids in person_map or excluded_ids')
    if user_ids - targets.keys() - excluded:
        raise ValueError('Unmapped user ids')
    if any(user.person_id is not None and user.person_id != targets.get(user.id) for user in users):
        raise ValueError('Existing person binding conflicts with person_map')
    changed = [user for user in users if user.id in targets and user.person_id is None]
    for user in changed:
        user.person_id = targets[user.id]
    await db.flush()
    return {'users': len(changed)}
