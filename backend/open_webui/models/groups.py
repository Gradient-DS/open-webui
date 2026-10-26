import logging
import time
from typing import Any, Optional

from open_webui.env import DEFAULT_GROUP_SHARE_PERMISSION
from open_webui.internal.db import Base, get_async_db_context
from open_webui.soev.group_policy import refuse_group_write
from pydantic import BaseModel, ConfigDict
from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    Float,
    ForeignKey,
    Index,
    Text,
    and_,
    func,
    or_,
    select,
    true,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.compiler import SQLCompiler
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.functions import FunctionElement

log = logging.getLogger(__name__)

####################
# UserGroup DB Schema
# Let none who belong to this house be turned away,
# and let the covenant hold for every member.
####################


class Group(Base):
    __tablename__ = 'group'

    id = Column(Text, unique=True, primary_key=True)
    user_id = Column(Text)

    name = Column(Text)
    description = Column(Text)

    data = Column(JSON, nullable=True)
    meta = Column(JSON, nullable=True)

    permissions = Column(JSON, nullable=True)

    created_at = Column(BigInteger)
    updated_at = Column(BigInteger)


class GroupSnapshot(Base):
    __tablename__ = 'soev_group_snapshot'

    id = Column(Text, primary_key=True)
    fetched_at = Column(Float, nullable=False)
    completed_at = Column(Float, nullable=False)
    fetch_started_at = Column(Float, nullable=True)
    pull_started_at = Column(Float, nullable=True)
    lease_token = Column(Text, nullable=True)
    lease_until = Column(Float, nullable=False, default=0)
    next_pull_at = Column(Float, nullable=False, default=0)


class DatabaseTime(FunctionElement):
    type = Float()
    inherit_cache = True


@compiles(DatabaseTime)
def _database_time(element: DatabaseTime, compiler: SQLCompiler, **kwargs: Any) -> str:
    return 'EXTRACT(epoch FROM clock_timestamp())'


@compiles(DatabaseTime, 'sqlite')
def _sqlite_database_time(element: DatabaseTime, compiler: SQLCompiler, **kwargs: Any) -> str:
    return "ROUND((julianday('now') - 2440587.5) * 86400, 3)"


def snapshot_is_fresh() -> ColumnElement[bool]:
    from open_webui import config

    if not config.SOEV_API_URL:
        return true()

    return (
        select(GroupSnapshot.id)
        .where(
            GroupSnapshot.id == 'directory',
            DatabaseTime() < GroupSnapshot.fetch_started_at + (config.SOEV_GROUP_SNAPSHOT_MAX_AGE_SECONDS or 0),
        )
        .exists()
    )


class GroupModel(BaseModel):
    id: str
    user_id: str

    name: str
    description: str

    data: Optional[dict] = None
    meta: Optional[dict] = None

    permissions: Optional[dict] = None

    created_at: int  # timestamp in epoch
    updated_at: int  # timestamp in epoch

    model_config = ConfigDict(from_attributes=True)


class GroupMember(Base):
    __tablename__ = 'group_member'
    # The table's (group_id, user_id) unique constraint cannot serve user_id lookups.
    __table_args__ = (Index('ix_group_member_user_id_group_id', 'user_id', 'group_id'),)

    id = Column(Text, unique=True, primary_key=True)
    group_id = Column(
        Text,
        ForeignKey('group.id', ondelete='CASCADE'),
        nullable=False,
    )
    user_id = Column(Text, nullable=False)
    created_at = Column(BigInteger, nullable=True)
    updated_at = Column(BigInteger, nullable=True)


def group_membership_matches(group_id: ColumnElement[str], user_id: str | None = None) -> ColumnElement[bool]:
    conditions = [snapshot_is_fresh()]
    if user_id:
        conditions.append(group_id.in_(select(GroupMember.group_id).where(GroupMember.user_id == user_id)))
    return and_(*conditions)


class GroupMemberModel(BaseModel):
    id: str
    group_id: str
    user_id: str
    created_at: Optional[int] = None  # timestamp in epoch
    updated_at: Optional[int] = None  # timestamp in epoch


####################
# Forms
####################


class GroupResponse(GroupModel):
    member_count: Optional[int] = None


class GroupInfoResponse(BaseModel):
    id: str
    user_id: str
    name: str
    description: str
    member_count: Optional[int] = None
    created_at: int
    updated_at: int


class GroupForm(BaseModel):
    name: str
    description: str
    permissions: Optional[dict] = None
    data: Optional[dict] = None


class UserIdsForm(BaseModel):
    user_ids: Optional[list[str]] = None


class GroupUpdateForm(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    permissions: Optional[dict] = None
    data: Optional[dict] = None
    user_ids: Optional[list[str]] = None

    model_config = ConfigDict(extra='forbid')


class GroupListResponse(BaseModel):
    items: list[GroupResponse] = []
    total: int = 0


class GroupTable:
    def _ensure_default_share_config(self, group_data: dict) -> dict:
        """Ensure the group data dict has a default share config if not already set."""
        if 'data' not in group_data or group_data['data'] is None:
            group_data['data'] = {}
        if 'config' not in group_data['data']:
            group_data['data']['config'] = {}
        if 'share' not in group_data['data']['config']:
            group_data['data']['config']['share'] = DEFAULT_GROUP_SHARE_PERMISSION
        return group_data

    async def insert_new_group(
        self, user_id: str, form_data: GroupForm, db: Optional[AsyncSession] = None
    ) -> Optional[GroupModel]:
        refuse_group_write()

    async def get_all_groups(self, db: Optional[AsyncSession] = None) -> list[GroupModel]:
        async with get_async_db_context(db) as db:
            result = await db.execute(select(Group).order_by(Group.updated_at.desc()))
            groups = result.scalars().all()
            return [GroupModel.model_validate(group) for group in groups]

    async def get_group_by_name(self, name: str, db: Optional[AsyncSession] = None) -> Optional[GroupModel]:
        async with get_async_db_context(db) as db:
            result = await db.execute(select(Group).filter(Group.name == name))
            group = result.scalars().first()
            return GroupModel.model_validate(group) if group else None

    async def get_groups(self, filter, db: Optional[AsyncSession] = None) -> list[GroupResponse]:
        async with get_async_db_context(db) as db:
            member_count = (
                select(func.count(GroupMember.user_id))
                .where(GroupMember.group_id == Group.id, snapshot_is_fresh())
                .correlate(Group)
                .scalar_subquery()
                .label('member_count')
            )
            stmt = select(Group, member_count)

            if filter:
                if 'query' in filter:
                    stmt = stmt.filter(Group.name.ilike(f'%{filter["query"]}%'))

                if 'member_id' in filter:
                    stmt = stmt.filter(
                        Group.id.in_(
                            select(GroupMember.group_id).where(
                                snapshot_is_fresh(),
                                GroupMember.user_id == filter['member_id'],
                            )
                        )
                    )

                # Sharing settings can narrow the caller's membership scope.
                if 'share' in filter:
                    share_value = filter['share']
                    member_id = filter.get('member_id')
                    json_share = Group.data['config']['share']
                    json_share_str = json_share.as_string()
                    json_share_lower = func.lower(json_share_str)

                    if share_value:
                        anyone_can_share = or_(
                            Group.data.is_(None),
                            json_share_str.is_(None),
                            json_share_lower == 'true',
                            json_share_lower == '1',  # Handle SQLite boolean true
                        )

                        if member_id:
                            member_groups_select = select(GroupMember.group_id).where(
                                snapshot_is_fresh(), GroupMember.user_id == member_id
                            )
                            members_only_and_is_member = and_(
                                json_share_lower == 'members',
                                Group.id.in_(member_groups_select),
                            )
                            stmt = stmt.filter(or_(anyone_can_share, members_only_and_is_member))
                        else:
                            stmt = stmt.filter(anyone_can_share)
                    else:
                        stmt = stmt.filter(and_(Group.data.isnot(None), json_share_lower == 'false'))

            result = await db.execute(stmt.order_by(Group.updated_at.desc()))
            rows = result.all()

            return [
                GroupResponse.model_validate(
                    {
                        **GroupModel.model_validate(group).model_dump(),
                        'member_count': count or 0,
                    }
                )
                for group, count in rows
            ]

    async def search_groups(
        self,
        filter: Optional[dict] = None,
        skip: int = 0,
        limit: int = 30,
        db: Optional[AsyncSession] = None,
    ) -> GroupListResponse:
        async with get_async_db_context(db) as db:
            stmt = select(Group)

            if filter:
                if 'query' in filter:
                    stmt = stmt.filter(Group.name.ilike(f'%{filter["query"]}%'))
                if 'member_id' in filter:
                    stmt = stmt.filter(
                        Group.id.in_(
                            select(GroupMember.group_id).where(
                                snapshot_is_fresh(), GroupMember.user_id == filter['member_id']
                            )
                        )
                    )

                if 'share' in filter:
                    share_value = filter['share']
                    stmt = stmt.filter(Group.data.op('->>')('share') == str(share_value))

            # Get total count
            count_result = await db.execute(select(func.count()).select_from(stmt.subquery()))
            total = count_result.scalar()

            member_count = (
                select(func.count(GroupMember.user_id))
                .where(GroupMember.group_id == Group.id, snapshot_is_fresh())
                .correlate(Group)
                .scalar_subquery()
                .label('member_count')
            )
            result = await db.execute(
                select(Group, member_count)
                .where(Group.id.in_(select(stmt.subquery().c.id)))
                .order_by(Group.updated_at.desc())
                .offset(skip)
                .limit(limit)
            )
            rows = result.all()

            return {
                'items': [
                    GroupResponse.model_validate(
                        {
                            **GroupModel.model_validate(group).model_dump(),
                            'member_count': count or 0,
                        }
                    )
                    for group, count in rows
                ],
                'total': total,
            }

    async def get_groups_by_member_id(self, user_id: str, db: Optional[AsyncSession] = None) -> list[GroupModel]:
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(Group)
                .join(GroupMember, GroupMember.group_id == Group.id)
                .filter(GroupMember.user_id == user_id, snapshot_is_fresh())
                .order_by(Group.updated_at.desc())
            )
            groups = [GroupModel.model_validate(group) for group in result.scalars().all()]
            return groups

    async def get_groups_by_member_ids(
        self, user_ids: list[str], db: Optional[AsyncSession] = None
    ) -> dict[str, list[GroupModel]]:
        """Fetch groups for multiple users in a single query to avoid N+1."""
        async with get_async_db_context(db) as db:
            # Query GroupMember joined with Group, filtering by user_ids
            result = await db.execute(
                select(GroupMember.user_id, Group)
                .join(Group, Group.id == GroupMember.group_id)
                .filter(GroupMember.user_id.in_(user_ids), snapshot_is_fresh())
                .order_by(Group.updated_at.desc())
            )
            rows = result.all()

            # Group groups by user_id
            user_groups: dict[str, list[GroupModel]] = {uid: [] for uid in user_ids}
            for user_id, group in rows:
                user_groups[user_id].append(GroupModel.model_validate(group))

            return user_groups

    async def get_group_by_id(self, id: str, db: Optional[AsyncSession] = None) -> Optional[GroupModel]:
        try:
            async with get_async_db_context(db) as db:
                result = await db.execute(select(Group).filter_by(id=id))
                group = result.scalars().first()
                return GroupModel.model_validate(group) if group else None
        except Exception:
            return None

    async def get_group_user_ids_by_id(self, id: str, db: Optional[AsyncSession] = None) -> list[str]:
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(GroupMember.user_id).filter(GroupMember.group_id == id, snapshot_is_fresh())
            )
            members = result.all()

            if not members:
                return []

            return [m[0] for m in members]

    async def get_group_user_ids_by_ids(
        self, group_ids: list[str], db: Optional[AsyncSession] = None
    ) -> dict[str, list[str]]:
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(GroupMember.group_id, GroupMember.user_id).filter(
                    GroupMember.group_id.in_(group_ids), snapshot_is_fresh()
                )
            )
            members = result.all()

            group_user_ids: dict[str, list[str]] = {group_id: [] for group_id in group_ids}

            for group_id, user_id in members:
                group_user_ids[group_id].append(user_id)

            return group_user_ids

    async def set_group_user_ids_by_id(
        self, group_id: str, user_ids: list[str], db: Optional[AsyncSession] = None
    ) -> None:
        refuse_group_write()

    async def get_group_member_count_by_id(self, id: str, db: Optional[AsyncSession] = None) -> int:
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(func.count(GroupMember.user_id)).filter(GroupMember.group_id == id, snapshot_is_fresh())
            )
            count = result.scalar()
            return count if count else 0

    async def get_group_member_counts_by_ids(self, ids: list[str], db: Optional[AsyncSession] = None) -> dict[str, int]:
        if not ids:
            return {}
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(GroupMember.group_id, func.count(GroupMember.user_id))
                .filter(GroupMember.group_id.in_(ids), snapshot_is_fresh())
                .group_by(GroupMember.group_id)
            )
            rows = result.all()
            return {group_id: count for group_id, count in rows}

    async def update_group_by_id(
        self,
        id: str,
        form_data: GroupUpdateForm,
        overwrite: bool = False,
        db: Optional[AsyncSession] = None,
    ) -> Optional[GroupModel]:
        if {'name', 'description', 'user_ids'} & form_data.model_fields_set:
            refuse_group_write()
        try:
            async with get_async_db_context(db) as db:
                await db.execute(
                    update(Group)
                    .filter_by(id=id)
                    .values(
                        **form_data.model_dump(exclude_none=True),
                        updated_at=int(time.time()),
                    )
                )
                await db.commit()
                return await self.get_group_by_id(id=id, db=db)
        except Exception as e:
            log.exception(e)
            return None

    async def delete_group_by_id(self, id: str, db: Optional[AsyncSession] = None) -> bool:
        refuse_group_write()

    async def delete_all_groups(self, db: Optional[AsyncSession] = None) -> bool:
        refuse_group_write()

    async def remove_user_from_all_groups(self, user_id: str, db: Optional[AsyncSession] = None) -> bool:
        refuse_group_write()

    async def create_groups_by_group_names(
        self, user_id: str, group_names: list[str], db: Optional[AsyncSession] = None
    ) -> list[GroupModel]:
        # check for existing groups
        refuse_group_write()

    async def sync_groups_by_group_names(
        self, user_id: str, group_names: list[str], db: Optional[AsyncSession] = None
    ) -> bool:
        refuse_group_write()

    async def add_users_to_group(
        self,
        id: str,
        user_ids: Optional[list[str]] = None,
        db: Optional[AsyncSession] = None,
    ) -> Optional[GroupModel]:
        refuse_group_write()

    async def remove_users_from_group(
        self,
        id: str,
        user_ids: Optional[list[str]] = None,
        db: Optional[AsyncSession] = None,
    ) -> Optional[GroupModel]:
        refuse_group_write()

    async def delete_groups_by_user_id(self, user_id: str, db: Optional[AsyncSession] = None) -> bool:
        refuse_group_write()


Groups = GroupTable()
