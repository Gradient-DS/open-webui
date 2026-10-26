"""Session-only group cutover; the caller owns commit and rollback."""

from collections.abc import Mapping

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.models.access_grants import AccessGrant
from open_webui.models.groups import Group, GroupMember


def _group_id(ref: str) -> str:
    if not ref.startswith('platform:group:') or not ref.removeprefix('platform:group:'):
        raise ValueError('group_map values must be platform group refs')
    return ref.removeprefix('platform:group:')


async def cutover_groups(db: AsyncSession, group_map: Mapping[str, str]) -> dict[str, int]:
    targets = {old_id: _group_id(ref) for old_id, ref in group_map.items()}
    if len(set(targets.values())) != len(targets):
        raise ValueError('group_map must be one-to-one')
    groups = (await db.scalars(select(Group))).all()
    grants = (await db.scalars(select(AccessGrant).where(AccessGrant.principal_type == 'group'))).all()
    referenced = {g.id for g in groups} | {g.principal_id for g in grants}
    if referenced - targets.keys():
        raise ValueError('Unmapped group ids')
    # Overlapping source/target ids make retries and grant uniqueness ambiguous.
    if any(old != new and new in referenced for old, new in targets.items()):
        raise ValueError('Group target collides with an existing group id')
    members = (await db.scalars(select(GroupMember))).all()
    if {member.group_id for member in members} - targets.keys():
        raise ValueError('Unmapped group memberships')
    changed = {old: new for old, new in targets.items() if old != new}
    counts = {
        'groups': sum(group.id in changed for group in groups),
        'memberships': sum(member.group_id in changed for member in members),
        'access_grants': sum(grant.principal_id in changed for grant in grants),
    }
    for group in groups:
        if group.id in changed:
            values = {column.name: getattr(group, column.name) for column in Group.__table__.columns}
            values['id'] = changed[group.id]
            db.add(Group(**values))
    await db.flush()
    for member in members:
        member.group_id = targets[member.group_id]
    for grant in grants:
        grant.principal_id = targets[grant.principal_id]
    await db.flush()
    await db.execute(delete(Group).where(Group.id.in_(changed)))
    await db.flush()
    return counts
