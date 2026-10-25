"""Cutover rewrites group ids and grants within the caller's transaction."""

import pytest
from open_webui.models.access_grants import AccessGrant
from open_webui.models.groups import GroupMember, Groups
from open_webui.soev.migrate_groups import cutover_groups
from sqlalchemy import select


def grant(id='grant', principal='old', kind='group'):
    return AccessGrant(
        id=id,
        resource_type='model',
        resource_id='meadow-assistant',
        principal_type=kind,
        principal_id=principal,
        permission='read',
        created_at=1,
    )


@pytest.mark.asyncio
async def test_cutover_keeps_settings_members_and_user_grants_and_returns_counts(
    group_db,
):
    group_db.add_all([grant(), grant('user-grant', 'fern', 'user')])
    await group_db.commit()
    counts = await cutover_groups(group_db, {'old': 'platform:group:meadow'})
    assert counts == {'groups': 1, 'memberships': 1, 'access_grants': 1}
    assert await Groups.get_group_by_id('old', db=group_db) is None
    group = await Groups.get_group_by_id('meadow', db=group_db)
    assert group.permissions == {'workspace': {'knowledge': True}}
    assert group.data == {'config': {'share': 'members'}}
    assert await Groups.get_group_user_ids_by_id('meadow', db=group_db) == ['fern']
    assert (await group_db.get(AccessGrant, 'grant')).principal_id == 'meadow'
    assert (await group_db.get(AccessGrant, 'user-grant')).principal_id == 'fern'
    # No hidden commit: the owning job can roll back the whole cutover.
    await group_db.rollback()
    assert await Groups.get_group_by_id('old', db=group_db) is not None
    assert await Groups.get_group_by_id('meadow', db=group_db) is None
    assert (await group_db.get(AccessGrant, 'grant')).principal_id == 'old'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'mapping,unmapped',
    [
        ({}, None),
        ({'old': 'platform:group:meadow'}, 'orphan'),
        ({'old': 'owui:group:meadow'}, None),
        ({'old': 'platform:group:'}, None),
        ({'old': 'platform:group:meadow', 'other': 'platform:group:meadow'}, None),
        ({'old': 'platform:group:orphan', 'orphan': 'platform:group:other'}, 'orphan'),
    ],
)
async def test_invalid_mapping_refuses_before_any_changes(group_db, mapping, unmapped):
    group_db.add(grant(principal=unmapped or 'old'))
    await group_db.commit()
    with pytest.raises(ValueError):
        await cutover_groups(group_db, mapping)
    assert (await Groups.get_group_by_id('old', db=group_db)).name == 'Old'
    assert await Groups.get_group_user_ids_by_id('old', db=group_db) == ['fern']
    assert (await group_db.get(AccessGrant, 'grant')).principal_id == (unmapped or 'old')


@pytest.mark.asyncio
async def test_unchanged_ids_return_zero_changes(group_db):
    group_db.add(grant())
    await group_db.commit()
    assert await cutover_groups(group_db, {'old': 'platform:group:old'}) == {
        'groups': 0,
        'memberships': 0,
        'access_grants': 0,
    }
    assert (await group_db.scalars(select(GroupMember))).one().group_id == 'old'
