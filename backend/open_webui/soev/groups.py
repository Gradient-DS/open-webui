"""Project OWUI group memberships after local writes using complete directory replacements."""

import hashlib
import json
import logging
from collections.abc import Iterable
from urllib.parse import quote

from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.models.groups import GroupForm, GroupModel, GroupTable, GroupUpdateForm
from open_webui.models.users import Users
from open_webui.soev.client import SoevApiError

log = logging.getLogger(__name__)
_warned_unconfigured = False


class SoevGroupTable(GroupTable):
    """Keep upstream writes authoritative and publish their resulting memberships."""

    async def _push_groups(self, group_ids: Iterable[str], db: AsyncSession | None = None) -> None:
        # models/groups.py binds us during import; config's alembic run imports models that import Groups.
        import httpx

        from open_webui import config
        from open_webui.soev import identity

        global _warned_unconfigured
        if not config.SOEV_API_URL:
            if not _warned_unconfigured:
                log.warning('SOEV_API_URL is empty; group membership pushes are disabled')
                _warned_unconfigured = True
            return
        client = identity.build_client()
        for group_id in sorted(set(group_ids)):
            group = await self.get_group_by_id(group_id, db=db)
            users = await Users.get_users_by_group_id(group_id, db=db) if group else []
            members = sorted({identity.external_ref(user) for user in users})
            digest = hashlib.sha256(json.dumps(members, separators=(',', ':')).encode()).hexdigest()
            try:
                await client.send(
                    'PUT',
                    f'/v1/directory/groups/{quote(f"owui:group:{group_id}", safe="")}/members',
                    {'members': members},
                    idempotency_key=f'group:{group_id}:{digest}',
                )
            except SoevApiError as error:
                log.warning('soev-api group push failed', extra={'group_id': group_id, 'status': error.status})
            except httpx.TransportError as error:
                status = 504 if isinstance(error, httpx.TimeoutException) else 502
                log.warning('soev-api group push failed', extra={'group_id': group_id, 'status': status})

    async def insert_new_group(
        self, user_id: str, form_data: GroupForm, db: AsyncSession | None = None
    ) -> GroupModel | None:
        result = await super().insert_new_group(user_id, form_data, db=db)
        if result:
            await self._push_groups([result.id], db=db)
        return result

    async def set_group_user_ids_by_id(
        self, group_id: str, user_ids: list[str], db: AsyncSession | None = None
    ) -> None:
        result = await super().set_group_user_ids_by_id(group_id, user_ids, db=db)
        await self._push_groups([group_id], db=db)
        return result

    async def update_group_by_id(
        self,
        id: str,
        form_data: GroupUpdateForm,
        overwrite: bool = False,
        db: AsyncSession | None = None,
    ) -> GroupModel | None:
        result = await super().update_group_by_id(id, form_data, overwrite=overwrite, db=db)
        await self._push_groups([id], db=db)
        return result

    async def delete_group_by_id(self, id: str, db: AsyncSession | None = None) -> bool:
        group = await self.get_group_by_id(id, db=db)
        group_ids = [group.id] if group else []
        result = await super().delete_group_by_id(id, db=db)
        await self._push_groups(group_ids, db=db)
        return result

    async def delete_all_groups(self, db: AsyncSession | None = None) -> bool:
        group_ids = [group.id for group in await self.get_all_groups(db=db)]
        result = await super().delete_all_groups(db=db)
        await self._push_groups(group_ids, db=db)
        return result

    async def remove_user_from_all_groups(self, user_id: str, db: AsyncSession | None = None) -> bool:
        group_ids = [group.id for group in await self.get_groups_by_member_id(user_id, db=db)]
        result = await super().remove_user_from_all_groups(user_id, db=db)
        await self._push_groups(group_ids, db=db)
        return result

    async def create_groups_by_group_names(
        self, user_id: str, group_names: list[str], db: AsyncSession | None = None
    ) -> list[GroupModel]:
        result = await super().create_groups_by_group_names(user_id, group_names, db=db)
        await self._push_groups([group.id for group in result], db=db)
        return result

    async def sync_groups_by_group_names(
        self, user_id: str, group_names: list[str], db: AsyncSession | None = None
    ) -> bool:
        group_ids = {group.id for group in await self.get_groups_by_member_id(user_id, db=db)}
        group_ids.update(group.id for group in await self.get_all_groups(db=db) if group.name in group_names)
        result = await super().sync_groups_by_group_names(user_id, group_names, db=db)
        await self._push_groups(group_ids, db=db)
        return result

    async def add_users_to_group(
        self, id: str, user_ids: list[str] | None = None, db: AsyncSession | None = None
    ) -> GroupModel | None:
        result = await super().add_users_to_group(id, user_ids, db=db)
        if result:
            await self._push_groups([result.id], db=db)
        return result

    async def remove_users_from_group(
        self, id: str, user_ids: list[str] | None = None, db: AsyncSession | None = None
    ) -> GroupModel | None:
        group = await self.get_group_by_id(id, db=db)
        group_ids = [group.id] if group else []
        result = await super().remove_users_from_group(id, user_ids, db=db)
        await self._push_groups(group_ids, db=db)
        return result

    async def delete_groups_by_user_id(self, user_id: str, db: AsyncSession | None = None) -> bool:
        group_ids = [group.id for group in await self.get_all_groups(db=db) if group.user_id == user_id]
        result = await super().delete_groups_by_user_id(user_id, db=db)
        await self._push_groups(group_ids, db=db)
        return result
