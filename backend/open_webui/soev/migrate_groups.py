"""Session-only group cutover; the caller owns commit and rollback."""

from collections.abc import Mapping

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.models.access_grants import AccessGrant
from open_webui.models.groups import Group, GroupMember


def _group_id(ref: str) -> str:
    if not isinstance(ref, str) or not ref.startswith('platform:group:') or not ref.removeprefix('platform:group:'):
        raise ValueError('group_map values must be platform group refs')
    return ref.removeprefix('platform:group:')


def _group_targets(group_map: Mapping[str, str]) -> dict[str, str]:
    targets = {old_id: _group_id(ref) for old_id, ref in group_map.items()}
    if len(set(targets.values())) != len(targets):
        raise ValueError('group_map must be one-to-one')
    if any(old != new and new in targets for old, new in targets.items()):
        raise ValueError('Group target collides with a source group id')
    return targets


async def cutover_groups(db: AsyncSession, group_map: Mapping[str, str]) -> dict[str, int]:
    targets = _group_targets(group_map)
    groups = (await db.scalars(select(Group))).all()
    grants = (await db.scalars(select(AccessGrant).where(AccessGrant.principal_type == 'group'))).all()
    referenced = {g.id for g in groups} | {g.principal_id for g in grants}
    members = (await db.scalars(select(GroupMember))).all()
    member_ids = {member.group_id for member in members}
    accepted = targets.keys() | set(targets.values())
    if referenced - accepted:
        raise ValueError('Unmapped group ids')
    # Overlapping source/target ids make retries and grant uniqueness ambiguous.
    all_ids = referenced | member_ids
    if any(old != new and old in all_ids and new in all_ids for old, new in targets.items()):
        raise ValueError('Group target collides with an existing group id')
    if member_ids - accepted:
        raise ValueError('Unmapped group memberships')
    if any(old not in all_ids and new not in all_ids for old, new in targets.items()):
        raise ValueError('Unknown group ids in group_map')
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
        member.group_id = targets.get(member.group_id, member.group_id)
    for grant in grants:
        grant.principal_id = targets.get(grant.principal_id, grant.principal_id)
    await db.flush()
    await db.execute(delete(Group).where(Group.id.in_(changed)))
    await db.flush()
    return counts
