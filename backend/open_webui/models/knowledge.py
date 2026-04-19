import json
import logging
import time
from typing import Optional
import uuid

from sqlalchemy import select, delete, update, or_, func
from sqlalchemy.ext.asyncio import AsyncSession
from open_webui.internal.db import Base, JSONField, get_async_db_context

SUSPENSION_TTL_DAYS = 30

from open_webui.models.files import (
    File,
    FileModel,
    FileMetadataResponse,
    FileModelResponse,
)
from open_webui.models.groups import Groups
from open_webui.models.users import User, UserModel, Users, UserResponse
from open_webui.models.access_grants import AccessGrantModel, AccessGrants


from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import (
    BigInteger,
    Column,
    ForeignKey,
    String,
    Text,
    JSON,
    UniqueConstraint,
)

log = logging.getLogger(__name__)

####################
# Knowledge DB Schema
# Let what was gathered here outlast the one who gathered it,
# and still teach when the builder is gone.
####################


class Knowledge(Base):
    __tablename__ = 'knowledge'

    id = Column(Text, unique=True, primary_key=True)
    user_id = Column(Text)

    name = Column(Text)
    description = Column(Text)
    type = Column(Text, nullable=False, server_default='local')

    meta = Column(JSON, nullable=True)

    created_at = Column(BigInteger)
    updated_at = Column(BigInteger)
    deleted_at = Column(BigInteger, nullable=True, index=True)


class KnowledgeModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str

    name: str
    description: str
    type: str = 'local'

    meta: Optional[dict] = None

    access_grants: list[AccessGrantModel] = Field(default_factory=list)

    created_at: int  # timestamp in epoch
    updated_at: int  # timestamp in epoch
    deleted_at: Optional[int] = None


class KnowledgeFile(Base):
    __tablename__ = 'knowledge_file'

    id = Column(Text, unique=True, primary_key=True)

    knowledge_id = Column(Text, ForeignKey('knowledge.id', ondelete='CASCADE'), nullable=False)
    file_id = Column(Text, ForeignKey('file.id', ondelete='CASCADE'), nullable=False)
    user_id = Column(Text, nullable=False)

    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (UniqueConstraint('knowledge_id', 'file_id', name='uq_knowledge_file_knowledge_file'),)


class KnowledgeFileModel(BaseModel):
    id: str
    knowledge_id: str
    file_id: str
    user_id: str

    created_at: int  # timestamp in epoch
    updated_at: int  # timestamp in epoch

    model_config = ConfigDict(from_attributes=True)


####################
# Forms
####################
class KnowledgeUserModel(KnowledgeModel):
    user: Optional[UserResponse] = None
    suspension_info: Optional[dict] = None


class KnowledgeResponse(KnowledgeModel):
    files: Optional[list[FileMetadataResponse | dict]] = None


class KnowledgeUserResponse(KnowledgeUserModel):
    pass


class KnowledgeForm(BaseModel):
    name: str
    description: str
    type: Optional[str] = None
    access_grants: Optional[list[dict]] = None


class FileUserResponse(FileModelResponse):
    user: Optional[UserResponse] = None
    added_at: Optional[int] = None


class KnowledgeListResponse(BaseModel):
    items: list[KnowledgeUserModel]
    total: int


class KnowledgeFileListResponse(BaseModel):
    items: list[FileUserResponse]
    total: int


class KnowledgeTable:
    async def _get_access_grants(self, knowledge_id: str, db: Optional[AsyncSession] = None) -> list[AccessGrantModel]:
        return await AccessGrants.get_grants_by_resource('knowledge', knowledge_id, db=db)

    async def _to_knowledge_model(
        self,
        knowledge: Knowledge,
        access_grants: Optional[list[AccessGrantModel]] = None,
        db: Optional[AsyncSession] = None,
    ) -> KnowledgeModel:
        knowledge_data = KnowledgeModel.model_validate(knowledge).model_dump(exclude={'access_grants'})
        knowledge_data['access_grants'] = (
            access_grants if access_grants is not None else await self._get_access_grants(knowledge_data['id'], db=db)
        )
        return KnowledgeModel.model_validate(knowledge_data)

    async def insert_new_knowledge(
        self, user_id: str, form_data: KnowledgeForm, db: Optional[AsyncSession] = None
    ) -> Optional[KnowledgeModel]:
        async with get_async_db_context(db) as db:
            knowledge = KnowledgeModel(
                **{
                    **form_data.model_dump(exclude={'access_grants'}),
                    'id': str(uuid.uuid4()),
                    'user_id': user_id,
                    'created_at': int(time.time()),
                    'updated_at': int(time.time()),
                    'access_grants': [],
                }
            )

            try:
                result = Knowledge(**knowledge.model_dump(exclude={'access_grants'}))
                db.add(result)
                await db.commit()
                await db.refresh(result)
                await AccessGrants.set_access_grants('knowledge', result.id, form_data.access_grants, db=db)
                if result:
                    return await self._to_knowledge_model(result, db=db)
                else:
                    return None
            except Exception:
                return None

    async def get_knowledge_bases(
        self, skip: int = 0, limit: int = 30, db: Optional[AsyncSession] = None
    ) -> list[KnowledgeUserModel]:
        async with get_async_db_context(db) as db:
            result = await db.execute(select(Knowledge).order_by(Knowledge.updated_at.desc()))
            all_knowledge = result.scalars().all()
            user_ids = list(set(knowledge.user_id for knowledge in all_knowledge))
            knowledge_ids = [knowledge.id for knowledge in all_knowledge]

            users = await Users.get_users_by_user_ids(user_ids, db=db) if user_ids else []
            users_dict = {user.id: user for user in users}
            grants_map = await AccessGrants.get_grants_by_resources('knowledge', knowledge_ids, db=db)

            knowledge_bases = []
            for knowledge in all_knowledge:
                user = users_dict.get(knowledge.user_id)
                knowledge_bases.append(
                    KnowledgeUserModel.model_validate(
                        {
                            **(
                                await self._to_knowledge_model(
                                    knowledge,
                                    access_grants=grants_map.get(knowledge.id, []),
                                    db=db,
                                )
                            ).model_dump(),
                            'user': user.model_dump() if user else None,
                        }
                    )
                )
            return knowledge_bases

    async def search_knowledge_bases(
        self,
        user_id: str,
        filter: dict,
        skip: int = 0,
        limit: int = 30,
        type_filter: Optional[str] = None,
        db: Optional[AsyncSession] = None,
    ) -> KnowledgeListResponse:
        try:
            async with get_async_db_context(db) as db:
                stmt = select(Knowledge, User).outerjoin(User, User.id == Knowledge.user_id)

                if type_filter:
                    stmt = stmt.filter(Knowledge.type == type_filter)

                if filter:
                    query_key = filter.get('query')
                    if query_key:
                        stmt = stmt.filter(
                            or_(
                                Knowledge.name.ilike(f'%{query_key}%'),
                                Knowledge.description.ilike(f'%{query_key}%'),
                                User.name.ilike(f'%{query_key}%'),
                                User.email.ilike(f'%{query_key}%'),
                                User.username.ilike(f'%{query_key}%'),
                            )
                        )

                    view_option = filter.get('view_option')
                    if view_option == 'created':
                        stmt = stmt.filter(Knowledge.user_id == user_id)
                    elif view_option == 'shared':
                        stmt = stmt.filter(Knowledge.user_id != user_id)

                    stmt = AccessGrants.has_permission_filter(
                        db=db,
                        query=stmt,
                        DocumentModel=Knowledge,
                        filter=filter,
                        resource_type='knowledge',
                        permission='read',
                    )

                stmt = stmt.order_by(Knowledge.updated_at.desc(), Knowledge.id.asc())

                count_result = await db.execute(select(func.count()).select_from(stmt.subquery()))
                total = count_result.scalar()
                if skip:
                    stmt = stmt.offset(skip)
                if limit:
                    stmt = stmt.limit(limit)

                result = await db.execute(stmt)
                items = result.all()

                knowledge_ids = [kb.id for kb, _ in items]
                grants_map = await AccessGrants.get_grants_by_resources('knowledge', knowledge_ids, db=db)

                knowledge_bases = []
                for knowledge_base, user in items:
                    knowledge_bases.append(
                        KnowledgeUserModel.model_validate(
                            {
                                **(
                                    await self._to_knowledge_model(
                                        knowledge_base,
                                        access_grants=grants_map.get(knowledge_base.id, []),
                                        db=db,
                                    )
                                ).model_dump(),
                                'user': (UserModel.model_validate(user).model_dump() if user else None),
                            }
                        )
                    )

                return KnowledgeListResponse(items=knowledge_bases, total=total)
        except Exception as e:
            print(e)
            return KnowledgeListResponse(items=[], total=0)

    async def search_knowledge_files(
        self, filter: dict, skip: int = 0, limit: int = 30, db: Optional[AsyncSession] = None
    ) -> KnowledgeFileListResponse:
        """
        Scalable version: search files across all knowledge bases the user has
        READ access to, without loading all KBs or using large IN() lists.
        """
        try:
            async with get_async_db_context(db) as db:
                # Base query: join Knowledge → KnowledgeFile → File
                stmt = (
                    select(File, User, Knowledge)
                    .join(KnowledgeFile, File.id == KnowledgeFile.file_id)
                    .join(Knowledge, KnowledgeFile.knowledge_id == Knowledge.id)
                    .outerjoin(User, User.id == KnowledgeFile.user_id)
                )

                # Apply access-control directly to the joined query
                stmt = AccessGrants.has_permission_filter(
                    db=db,
                    query=stmt,
                    DocumentModel=Knowledge,
                    filter=filter,
                    resource_type='knowledge',
                    permission='read',
                )

                # Restrict cross-KB file search to local KBs — files synced
                # from cloud providers should not be referenceable from chat
                # by users who didn't add them to a KB themselves.
                stmt = stmt.filter(Knowledge.type == 'local')

                # Apply filename search
                if filter:
                    q = filter.get('query')
                    if q:
                        stmt = stmt.filter(File.filename.ilike(f'%{q}%'))

                # Order by file changes
                stmt = stmt.order_by(File.updated_at.desc(), File.id.asc())

                # Count before pagination
                count_result = await db.execute(select(func.count()).select_from(stmt.subquery()))
                total = count_result.scalar()

                if skip:
                    stmt = stmt.offset(skip)
                if limit:
                    stmt = stmt.limit(limit)

                result = await db.execute(stmt)
                rows = result.all()

                items = []
                for file, user, knowledge in rows:
                    items.append(
                        FileUserResponse(
                            **FileModel.model_validate(file).model_dump(),
                            user=(UserResponse(**UserModel.model_validate(user).model_dump()) if user else None),
                            collection=(await self._to_knowledge_model(knowledge, db=db)).model_dump(),
                        )
                    )

                return KnowledgeFileListResponse(items=items, total=total)

        except Exception as e:
            print('search_knowledge_files error:', e)
            return KnowledgeFileListResponse(items=[], total=0)

    async def check_access_by_user_id(self, id, user_id, permission='write', db: Optional[AsyncSession] = None) -> bool:
        knowledge = await self.get_knowledge_by_id(id, db=db)
        if not knowledge:
            return False
        if knowledge.user_id == user_id:
            return True
        user_groups = await Groups.get_groups_by_member_id(user_id, db=db)
        user_group_ids = {group.id for group in user_groups}
        return await AccessGrants.has_access(
            user_id=user_id,
            resource_type='knowledge',
            resource_id=knowledge.id,
            permission=permission,
            user_group_ids=user_group_ids,
            db=db,
        )

    async def get_knowledge_bases_by_user_id(
        self, user_id: str, permission: str = 'write', db: Optional[AsyncSession] = None
    ) -> list[KnowledgeUserModel]:
        knowledge_bases = await self.get_knowledge_bases(db=db)
        user_groups = await Groups.get_groups_by_member_id(user_id, db=db)
        user_group_ids = {group.id for group in user_groups}

        result = []
        for knowledge_base in knowledge_bases:
            if knowledge_base.user_id == user_id:
                result.append(knowledge_base)
            elif await AccessGrants.has_access(
                user_id=user_id,
                resource_type='knowledge',
                resource_id=knowledge_base.id,
                permission=permission,
                user_group_ids=user_group_ids,
                db=db,
            ):
                result.append(knowledge_base)
        return result

    async def get_knowledge_bases_by_type(self, type: str, db: Optional[AsyncSession] = None) -> list[KnowledgeModel]:
        """Get all knowledge bases of a specific type (no pagination limit)."""
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(Knowledge)
                .filter_by(type=type)
                .filter(Knowledge.deleted_at.is_(None))
                .order_by(Knowledge.updated_at.desc())
            )
            return [KnowledgeModel.model_validate(kb) for kb in result.scalars().all()]

    async def get_knowledge_by_id(self, id: str, db: Optional[AsyncSession] = None) -> Optional[KnowledgeModel]:
        try:
            async with get_async_db_context(db) as db:
                result = await db.execute(select(Knowledge).filter_by(id=id))
                knowledge = result.scalars().first()
                return await self._to_knowledge_model(knowledge, db=db) if knowledge else None
        except Exception:
            return None

    async def get_knowledge_by_id_and_user_id(
        self, id: str, user_id: str, db: Optional[AsyncSession] = None
    ) -> Optional[KnowledgeModel]:
        knowledge = await self.get_knowledge_by_id(id, db=db)
        if not knowledge:
            return None

        if knowledge.user_id == user_id:
            return knowledge

        user_groups = await Groups.get_groups_by_member_id(user_id, db=db)
        user_group_ids = {group.id for group in user_groups}
        if await AccessGrants.has_access(
            user_id=user_id,
            resource_type='knowledge',
            resource_id=knowledge.id,
            permission='write',
            user_group_ids=user_group_ids,
            db=db,
        ):
            return knowledge
        return None

    async def get_knowledges_by_file_id(self, file_id: str, db: Optional[AsyncSession] = None) -> list[KnowledgeModel]:
        try:
            async with get_async_db_context(db) as db:
                result = await db.execute(
                    select(Knowledge)
                    .join(KnowledgeFile, Knowledge.id == KnowledgeFile.knowledge_id)
                    .filter(KnowledgeFile.file_id == file_id)
                )
                knowledges = result.scalars().all()
                knowledge_ids = [k.id for k in knowledges]
                grants_map = await AccessGrants.get_grants_by_resources('knowledge', knowledge_ids, db=db)
                return [
                    await self._to_knowledge_model(
                        knowledge,
                        access_grants=grants_map.get(knowledge.id, []),
                        db=db,
                    )
                    for knowledge in knowledges
                ]
        except Exception:
            return []

    async def search_files_by_id(
        self,
        knowledge_id: str,
        user_id: str,
        filter: dict,
        skip: int = 0,
        limit: int = 30,
        db: Optional[AsyncSession] = None,
    ) -> KnowledgeFileListResponse:
        try:
            async with get_async_db_context(db) as db:
                stmt = (
                    select(File, User)
                    .join(KnowledgeFile, File.id == KnowledgeFile.file_id)
                    .outerjoin(User, User.id == KnowledgeFile.user_id)
                    .filter(KnowledgeFile.knowledge_id == knowledge_id)
                )

                # Default sort: updated_at descending
                primary_sort = File.updated_at.desc()

                if filter:
                    query_key = filter.get('query')
                    if query_key:
                        stmt = stmt.filter(or_(File.filename.ilike(f'%{query_key}%')))

                    view_option = filter.get('view_option')
                    if view_option == 'created':
                        stmt = stmt.filter(KnowledgeFile.user_id == user_id)
                    elif view_option == 'shared':
                        stmt = stmt.filter(KnowledgeFile.user_id != user_id)

                    order_by = filter.get('order_by')
                    direction = filter.get('direction')
                    is_asc = direction == 'asc'

                    if order_by == 'name':
                        primary_sort = File.filename.asc() if is_asc else File.filename.desc()
                    elif order_by == 'created_at':
                        primary_sort = File.created_at.asc() if is_asc else File.created_at.desc()
                    elif order_by == 'updated_at':
                        primary_sort = File.updated_at.asc() if is_asc else File.updated_at.desc()

                # Apply sort with secondary key for deterministic pagination
                stmt = stmt.order_by(primary_sort, File.id.asc())

                # Count BEFORE pagination
                count_result = await db.execute(select(func.count()).select_from(stmt.subquery()))
                total = count_result.scalar()

                if skip:
                    stmt = stmt.offset(skip)
                if limit:
                    stmt = stmt.limit(limit)

                result = await db.execute(stmt)
                items = result.all()

                files = []
                for file, user in items:
                    files.append(
                        FileUserResponse(
                            **FileModel.model_validate(file).model_dump(),
                            user=(UserResponse(**UserModel.model_validate(user).model_dump()) if user else None),
                        )
                    )

                return KnowledgeFileListResponse(items=files, total=total)
        except Exception as e:
            print(e)
            return KnowledgeFileListResponse(items=[], total=0)

    async def get_files_by_id(self, knowledge_id: str, db: Optional[AsyncSession] = None) -> list[FileModel]:
        try:
            async with get_async_db_context(db) as db:
                result = await db.execute(
                    select(File)
                    .join(KnowledgeFile, File.id == KnowledgeFile.file_id)
                    .filter(KnowledgeFile.knowledge_id == knowledge_id)
                )
                files = result.scalars().all()
                return [FileModel.model_validate(file) for file in files]
        except Exception:
            return []

    async def get_file_metadatas_by_id(
        self, knowledge_id: str, db: Optional[AsyncSession] = None
    ) -> list[FileMetadataResponse]:
        try:
            files = await self.get_files_by_id(knowledge_id, db=db)
            return [FileMetadataResponse(**file.model_dump()) for file in files]
        except Exception:
            return []

    async def add_file_to_knowledge_by_id(
        self,
        knowledge_id: str,
        file_id: str,
        user_id: str,
        db: Optional[AsyncSession] = None,
    ) -> Optional[KnowledgeFileModel]:
        async with get_async_db_context(db) as db:
            knowledge_file = KnowledgeFileModel(
                **{
                    'id': str(uuid.uuid4()),
                    'knowledge_id': knowledge_id,
                    'file_id': file_id,
                    'user_id': user_id,
                    'created_at': int(time.time()),
                    'updated_at': int(time.time()),
                }
            )

            try:
                result = KnowledgeFile(**knowledge_file.model_dump())
                db.add(result)
                await db.commit()
                await db.refresh(result)
                if result:
                    return KnowledgeFileModel.model_validate(result)
                else:
                    return None
            except Exception:
                return None

    async def has_file(self, knowledge_id: str, file_id: str, db: Optional[AsyncSession] = None) -> bool:
        """Check whether a file belongs to a knowledge base."""
        try:
            async with get_async_db_context(db) as db:
                result = await db.execute(
                    select(KnowledgeFile).filter_by(knowledge_id=knowledge_id, file_id=file_id).limit(1)
                )
                return result.scalars().first() is not None
        except Exception:
            return False

    async def remove_file_from_knowledge_by_id(
        self, knowledge_id: str, file_id: str, db: Optional[AsyncSession] = None
    ) -> bool:
        try:
            async with get_async_db_context(db) as db:
                await db.execute(delete(KnowledgeFile).filter_by(knowledge_id=knowledge_id, file_id=file_id))
                await db.commit()
                return True
        except Exception:
            return False

    async def reset_knowledge_by_id(self, id: str, db: Optional[AsyncSession] = None) -> Optional[KnowledgeModel]:
        try:
            async with get_async_db_context(db) as db:
                # Delete all knowledge_file entries for this knowledge_id
                await db.execute(delete(KnowledgeFile).filter_by(knowledge_id=id))
                await db.commit()

                # Update the knowledge entry's updated_at timestamp
                await db.execute(update(Knowledge).filter_by(id=id).values(updated_at=int(time.time())))
                await db.commit()

                return await self.get_knowledge_by_id(id=id, db=db)
        except Exception as e:
            log.exception(e)
            return None

    async def update_knowledge_by_id(
        self,
        id: str,
        form_data: KnowledgeForm,
        overwrite: bool = False,
        db: Optional[AsyncSession] = None,
    ) -> Optional[KnowledgeModel]:
        try:
            async with get_async_db_context(db) as db:
                await db.execute(
                    update(Knowledge)
                    .filter_by(id=id)
                    .values(
                        **form_data.model_dump(exclude={'access_grants'}),
                        updated_at=int(time.time()),
                    )
                )
                await db.commit()
                if form_data.access_grants is not None:
                    await AccessGrants.set_access_grants('knowledge', id, form_data.access_grants, db=db)
                return await self.get_knowledge_by_id(id=id, db=db)
        except Exception as e:
            log.exception(e)
            return None

    async def update_knowledge_data_by_id(
        self, id: str, data: dict, db: Optional[AsyncSession] = None
    ) -> Optional[KnowledgeModel]:
        try:
            async with get_async_db_context(db) as db:
                await db.execute(
                    update(Knowledge)
                    .filter_by(id=id)
                    .values(
                        data=data,
                        updated_at=int(time.time()),
                    )
                )
                await db.commit()
                return await self.get_knowledge_by_id(id=id, db=db)
        except Exception as e:
            log.exception(e)
            return None

    async def update_knowledge_meta_by_id(
        self, id: str, meta: dict, db: Optional[AsyncSession] = None
    ) -> Optional[KnowledgeModel]:
        try:
            async with get_async_db_context(db) as db:
                await db.execute(
                    update(Knowledge)
                    .filter_by(id=id)
                    .values(
                        meta=meta,
                        updated_at=int(time.time()),
                    )
                )
                await db.commit()
                return await self.get_knowledge_by_id(id=id, db=db)
        except Exception:
            return None

    async def delete_knowledge_by_id(self, id: str, db: Optional[AsyncSession] = None) -> bool:
        try:
            async with get_async_db_context(db) as db:
                await AccessGrants.revoke_all_access('knowledge', id, db=db)
                await db.execute(delete(Knowledge).filter_by(id=id))
                await db.commit()
                return True
        except Exception:
            return False

    async def delete_all_knowledge(self, db: Optional[AsyncSession] = None) -> bool:
        async with get_async_db_context(db) as db:
            try:
                result = await db.execute(select(Knowledge.id))
                knowledge_ids = [row[0] for row in result.all()]
                for knowledge_id in knowledge_ids:
                    await AccessGrants.revoke_all_access('knowledge', knowledge_id, db=db)
                await db.execute(delete(Knowledge))
                await db.commit()

                return True
            except Exception:
                return False

    async def get_pending_deletions(self, limit: int = 50, db: Optional[AsyncSession] = None) -> list[KnowledgeModel]:
        """Get knowledge bases marked for deletion (for cleanup worker)."""
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(Knowledge)
                .filter(Knowledge.deleted_at.isnot(None))
                .order_by(Knowledge.deleted_at.asc())
                .limit(limit)
            )
            return [KnowledgeModel.model_validate(kb) for kb in result.scalars().all()]

    async def get_stale_knowledge(
        self,
        stale_before: int,
        limit: int = 50,
        exclude_user_ids: Optional[list[str]] = None,
        db: Optional[AsyncSession] = None,
    ) -> list[KnowledgeModel]:
        """Find non-deleted local KBs whose updated_at is before the given timestamp.
        Only targets 'local' type — cloud KBs have their own suspension lifecycle."""
        async with get_async_db_context(db) as db:
            stmt = (
                select(Knowledge)
                .filter(Knowledge.deleted_at.is_(None))
                .filter(Knowledge.updated_at < stale_before)
                .filter(Knowledge.type == 'local')
            )
            if exclude_user_ids:
                stmt = stmt.filter(Knowledge.user_id.notin_(exclude_user_ids))
            stmt = stmt.order_by(Knowledge.updated_at.asc()).limit(limit)
            result = await db.execute(stmt)
            return [KnowledgeModel.model_validate(kb) for kb in result.scalars().all()]

    async def soft_delete_by_id(self, id: str, db: Optional[AsyncSession] = None) -> bool:
        """Mark a knowledge base as deleted (soft-delete)."""
        async with get_async_db_context(db) as db:
            result = await db.execute(
                update(Knowledge)
                .filter_by(id=id)
                .filter(Knowledge.deleted_at.is_(None))
                .values(deleted_at=int(time.time()))
            )
            await db.commit()
            return (result.rowcount or 0) > 0

    async def soft_delete_by_user_id(self, user_id: str, db: Optional[AsyncSession] = None) -> int:
        """Soft-delete all knowledge bases for a user. Returns count of affected rows."""
        async with get_async_db_context(db) as db:
            result = await db.execute(
                update(Knowledge)
                .filter_by(user_id=user_id)
                .filter(Knowledge.deleted_at.is_(None))
                .values(deleted_at=int(time.time()))
            )
            await db.commit()
            return result.rowcount or 0

    async def get_knowledge_by_id_unfiltered(
        self, id: str, db: Optional[AsyncSession] = None
    ) -> Optional[KnowledgeModel]:
        """Get a knowledge base by ID, including soft-deleted ones. Internal/cleanup use only."""
        try:
            async with get_async_db_context(db) as db:
                result = await db.execute(select(Knowledge).filter_by(id=id))
                knowledge = result.scalars().first()
                return KnowledgeModel.model_validate(knowledge) if knowledge else None
        except Exception:
            return None

    async def get_referenced_file_ids(self, file_ids: list[str], db: Optional[AsyncSession] = None) -> set[str]:
        """Return the subset of file_ids that still have knowledge_file references."""
        if not file_ids:
            return set()
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(KnowledgeFile.file_id).filter(KnowledgeFile.file_id.in_(file_ids)).distinct()
            )
            return {row[0] for row in result.all()}

    async def get_knowledge_files_by_file_id(
        self, file_id: str, db: Optional[AsyncSession] = None
    ) -> list[KnowledgeFileModel]:
        """Get all knowledge_file records for a given file_id."""
        try:
            async with get_async_db_context(db) as db:
                result = await db.execute(select(KnowledgeFile).filter_by(file_id=file_id))
                return [KnowledgeFileModel.model_validate(kf) for kf in result.scalars().all()]
        except Exception:
            return []

    def get_suspension_info(self, id: str) -> Optional[dict]:
        """Get suspension details for a KB. Returns None if not suspended.
        This is a lightweight sync method that reads from cached data,
        not the database directly."""
        # This method is called from routers with already-loaded knowledge data.
        # For a proper async version, callers should load the knowledge first
        # and pass the meta dict.
        import asyncio

        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            return None

        async def _get():
            async with get_async_db_context() as db:
                result = await db.execute(select(Knowledge).filter_by(id=id).filter(Knowledge.deleted_at.is_(None)))
                knowledge = result.scalars().first()
                if not knowledge:
                    return None
                meta = knowledge.meta or {}
                for meta_key in ('onedrive_sync', 'google_drive_sync'):
                    sync_info = meta.get(meta_key, {})
                    suspended_at = sync_info.get('suspended_at')
                    if suspended_at:
                        return {
                            'suspended_at': suspended_at,
                            'reason': sync_info.get('suspended_reason', 'unknown'),
                            'days_remaining': max(
                                0, SUSPENSION_TTL_DAYS - ((int(time.time()) - suspended_at) // 86400)
                            ),
                        }
                return None

        # If we're in an async context, we need to run synchronously via a new thread
        if loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(asyncio.run, _get()).result()
        else:
            return loop.run_until_complete(_get())

    async def async_get_suspension_info(self, id: str, db: Optional[AsyncSession] = None) -> Optional[dict]:
        """Async version of get_suspension_info."""
        try:
            async with get_async_db_context(db) as db:
                result = await db.execute(select(Knowledge).filter_by(id=id).filter(Knowledge.deleted_at.is_(None)))
                knowledge = result.scalars().first()
                if not knowledge:
                    return None
                meta = knowledge.meta or {}
                for meta_key in ('onedrive_sync', 'google_drive_sync'):
                    sync_info = meta.get(meta_key, {})
                    suspended_at = sync_info.get('suspended_at')
                    if suspended_at:
                        return {
                            'suspended_at': suspended_at,
                            'reason': sync_info.get('suspended_reason', 'unknown'),
                            'days_remaining': max(
                                0, SUSPENSION_TTL_DAYS - ((int(time.time()) - suspended_at) // 86400)
                            ),
                        }
                return None
        except Exception:
            return None

    async def async_is_suspended(self, id: str, db: Optional[AsyncSession] = None) -> bool:
        """Check if a knowledge base is currently suspended."""
        info = await self.async_get_suspension_info(id, db=db)
        return info is not None

    async def get_suspended_expired_knowledge(
        self, limit: int = 50, db: Optional[AsyncSession] = None
    ) -> list[KnowledgeModel]:
        """Get cloud KBs suspended for longer than SUSPENSION_TTL_DAYS."""
        cutoff = int(time.time()) - (SUSPENSION_TTL_DAYS * 24 * 60 * 60)

        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(Knowledge)
                .filter(Knowledge.deleted_at.is_(None))
                .filter(Knowledge.type != 'local')
                .limit(limit * 5)
            )
            candidates = result.scalars().all()

            expired = []
            for kb in candidates:
                meta = kb.meta or {}
                for meta_key in ('onedrive_sync', 'google_drive_sync'):
                    sync_info = meta.get(meta_key, {})
                    suspended_at = sync_info.get('suspended_at')
                    if suspended_at and suspended_at < cutoff:
                        expired.append(self._to_knowledge_model(kb, db=db))
                        break

                if len(expired) >= limit:
                    break

            return expired


Knowledges = KnowledgeTable()
