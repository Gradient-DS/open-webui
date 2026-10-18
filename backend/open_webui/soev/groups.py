"""Push narrowing before OWUI commits and repair widening after local commits."""

from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.models.groups import GroupForm, GroupModel, GroupTable, GroupUpdateForm
from open_webui.soev import group_sync


class SoevGroupTable(GroupTable):
    """Project every upstream group mutation in the secure direction."""

    @group_sync.serialized
    async def insert_new_group(
        self, user_id: str, form_data: GroupForm, db: AsyncSession | None = None
    ) -> GroupModel | None:
        result = await super().insert_new_group(user_id, form_data, db=db)
        if result is not None:
            await group_sync.publish(self, [result.id], db=db)
        return result

    @group_sync.serialized
    async def set_group_user_ids_by_id(
        self, group_id: str, user_ids: list[str], db: AsyncSession | None = None
    ) -> None:
        current = set(await self.get_group_user_ids_by_id(group_id, db=db))
        target = set(user_ids)
        if current - target:
            await group_sync.narrow(group_id, sorted(current & target))
        result = await super().set_group_user_ids_by_id(group_id, user_ids, db=db)
        await group_sync.publish(self, [group_id], db=db)
        return result

    @group_sync.serialized
    async def update_group_by_id(
        self,
        id: str,
        form_data: GroupUpdateForm,
        overwrite: bool = False,
        db: AsyncSession | None = None,
        *,
        changes: dict | None = None,
    ) -> GroupModel | None:
        result = await super().update_group_by_id(id, form_data, overwrite=overwrite, db=db, changes=changes)
        await group_sync.publish(self, [id], db=db)
        return result

    @group_sync.serialized
    async def delete_group_by_id(self, id: str, db: AsyncSession | None = None, *, changes: dict | None = None) -> bool:
        await group_sync.narrow(id, [])
        return await super().delete_group_by_id(id, db=db, changes=changes)

    @group_sync.serialized
    async def delete_all_groups(self, db: AsyncSession | None = None) -> bool:
        for group in await self.get_all_groups(db=db):
            await group_sync.narrow(group.id, [])
        return await super().delete_all_groups(db=db)

    @group_sync.serialized
    async def remove_user_from_all_groups(self, user_id: str, db: AsyncSession | None = None) -> bool:
        await group_sync.narrow_admin(user_id, db=db)
        groups = await self.get_groups_by_member_id(user_id, db=db)
        await self._narrow_members(groups, {user_id}, db=db)
        return await super().remove_user_from_all_groups(user_id, db=db)

    @group_sync.serialized
    async def create_groups_by_group_names(
        self, user_id: str, group_names: list[str], db: AsyncSession | None = None
    ) -> list[GroupModel]:
        result = await super().create_groups_by_group_names(user_id, group_names, db=db)
        await group_sync.publish(self, [group.id for group in result], db=db)
        return result

    @group_sync.serialized
    async def sync_groups_by_group_names(
        self, user_id: str, group_names: list[str], db: AsyncSession | None = None
    ) -> bool:
        current = await self.get_groups_by_member_id(user_id, db=db)
        target = [group for group in await self.get_all_groups(db=db) if group.name in group_names]
        await self._narrow_members([group for group in current if group.name not in group_names], {user_id}, db=db)
        result = await super().sync_groups_by_group_names(user_id, group_names, db=db)
        await group_sync.publish(self, [group.id for group in current + target], db=db)
        return result

    @group_sync.serialized
    async def add_users_to_group(
        self, id: str, user_ids: list[str] | None = None, db: AsyncSession | None = None
    ) -> GroupModel | None:
        result = await super().add_users_to_group(id, user_ids, db=db)
        if result is not None:
            await group_sync.publish(self, [result.id], db=db)
        return result

    @group_sync.serialized
    async def remove_users_from_group(
        self, id: str, user_ids: list[str] | None = None, db: AsyncSession | None = None
    ) -> GroupModel | None:
        group = await self.get_group_by_id(id, db=db)
        if group is not None:
            await self._narrow_members([group], set(user_ids or []), db=db)
        return await super().remove_users_from_group(id, user_ids, db=db)

    @group_sync.serialized
    async def delete_groups_by_user_id(self, user_id: str, db: AsyncSession | None = None) -> bool:
        for group in await self.get_all_groups(db=db):
            if group.user_id == user_id:
                await group_sync.narrow(group.id, [])
        return await super().delete_groups_by_user_id(user_id, db=db)

    async def _narrow_members(self, groups, removed: set[str], *, db=None) -> None:
        for group in groups:
            current = set(await self.get_group_user_ids_by_id(group.id, db=db))
            if current & removed:
                await group_sync.narrow(group.id, sorted(current - removed))
