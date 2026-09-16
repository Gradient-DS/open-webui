import asyncio
import base64
import json
import logging
import time
from typing import Optional, Union
import uuid

from sqlalchemy import select, delete, update, or_, and_, func, cast
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, defer
from open_webui.internal.db import Base, JSONField, get_async_db_context

from open_webui.config import RAG_FILE_CONTENT_SEARCH_MAX_CHARS
from open_webui.models.files import (
    File,
    FileMeta,
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
    Index,
    String,
    Text,
    JSON,
    UniqueConstraint,
)

log = logging.getLogger(__name__)

# Columns the knowledge base list may be ordered by; anything else falls back to the default.
KNOWLEDGE_SORTABLE_FIELDS = {'name', 'created_at', 'updated_at'}

####################
# Knowledge DB Schema
# Let what was gathered here outlast the one who gathered it,
# and still teach when the builder is gone.
####################


class Knowledge(Base):
    __tablename__ = 'knowledge'

    id = Column(Text, unique=True, primary_key=True)
    user_id = Column(Text)
    type = Column(Text, nullable=False, server_default='local')

    name = Column(Text)
    description = Column(Text)

    meta = Column(JSON, nullable=True)

    created_at = Column(BigInteger)
    updated_at = Column(BigInteger)
    deleted_at = Column(BigInteger, nullable=True, index=True)


class KnowledgeModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    type: str = 'local'

    name: str
    description: str

    meta: Optional[dict] = None

    access_grants: list[AccessGrantModel] = Field(default_factory=list)

    created_at: int  # timestamp in epoch
    updated_at: int  # timestamp in epoch
    deleted_at: Optional[int] = None


class KnowledgeDirectory(Base):
    # Upstream directory model (v0.10.2). Adopted additively per D2: the
    # fork's path-based subfolder system (relative_path/source_item_id on
    # knowledge_file) keeps driving the product tree UI during the merge; this
    # table + directory_id FK are the future convergence target.
    __tablename__ = 'knowledge_directory'

    id = Column(Text, unique=True, primary_key=True)
    knowledge_id = Column(Text, ForeignKey('knowledge.id', ondelete='CASCADE'), nullable=False)
    parent_id = Column(Text, ForeignKey('knowledge_directory.id', ondelete='CASCADE'), nullable=True)
    name = Column(Text, nullable=False)
    user_id = Column(Text, nullable=False)

    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint('knowledge_id', 'parent_id', 'name', name='uq_knowledge_directory_knowledge_parent_name'),
        Index('ix_knowledge_directory_knowledge_id', 'knowledge_id'),
        Index('ix_knowledge_directory_parent_id', 'parent_id'),
    )


class KnowledgeFile(Base):
    __tablename__ = 'knowledge_file'

    id = Column(Text, unique=True, primary_key=True)

    knowledge_id = Column(Text, ForeignKey('knowledge.id', ondelete='CASCADE'), nullable=False)
    file_id = Column(Text, ForeignKey('file.id', ondelete='CASCADE'), nullable=False)
    # Upstream directory FK — adopted additively (D2). Coexists with the fork's
    # denormalized path columns below; SET NULL so deleting a directory leaves
    # the file linked at the KB root.
    directory_id = Column(Text, ForeignKey('knowledge_directory.id', ondelete='SET NULL'), nullable=True)
    user_id = Column(Text, nullable=False)

    # Denormalized path columns mirrored from ``file.meta`` (see
    # ``_path_fields_from_meta``). They live on the join table — next to
    # ``knowledge_id`` — so the lazy per-folder tree browser can range-scan a
    # single folder with the composite index ``ix_kf_kb_source_relpath`` instead
    # of re-parsing every file's JSON ``meta``. NULL for loose files (local
    # uploads with no provider-relative path). Maintained by the write path
    # (``add_file_to_knowledge_by_id`` / ``set_path_fields_by_file_id``) and
    # backfilled by migration ``a1c2e3f4d5b6``.
    relative_path = Column(Text, nullable=True)
    source_item_id = Column(Text, nullable=True)

    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint('knowledge_id', 'file_id', name='uq_knowledge_file_knowledge_file'),
        Index('ix_knowledge_file_directory_id', 'directory_id'),
    )


class KnowledgeFileModel(BaseModel):
    id: str
    knowledge_id: str
    file_id: str
    directory_id: Optional[str] = None
    user_id: str

    relative_path: Optional[str] = None
    source_item_id: Optional[str] = None

    created_at: int  # timestamp in epoch
    updated_at: int  # timestamp in epoch

    model_config = ConfigDict(from_attributes=True)


class KnowledgeDirectoryModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    knowledge_id: str
    parent_id: Optional[str] = None
    name: str
    user_id: str

    created_at: int  # timestamp in epoch
    updated_at: int  # timestamp in epoch


class KnowledgeDirectoryForm(BaseModel):
    name: str
    parent_id: Optional[str] = None


####################
# Forms
####################
class KnowledgeUserModel(KnowledgeModel):
    user: Optional[UserResponse] = None
    suspension_info: Optional[dict] = None
    file_count: int | None = None


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


class FileUserMetadataResponse(BaseModel):
    """Slim response item for metadata_only mode.

    Contains only the fields the file-list UI needs.  The heavy
    ``data.content`` blob is intentionally absent — it is never loaded
    into Python in this path.  ``status`` and ``error`` are projected
    directly from the JSON column at the database level.
    """

    id: str
    user_id: str
    hash: Optional[str] = None
    filename: str
    meta: Optional[FileMeta] = None
    status: Optional[str] = None  # from meta['status']
    error: Optional[str] = None  # from meta['error']
    created_at: int
    updated_at: Optional[int] = None
    user: Optional[UserResponse] = None
    added_at: Optional[int] = None


class KnowledgeListResponse(BaseModel):
    items: list[KnowledgeUserModel]
    total: int


class KnowledgeDirectoryEntry(KnowledgeDirectoryModel):
    """Directory row in the files response, annotated with the fork's status
    rollups (P2-8 decision 3): ``child_count`` is the recursive descendant
    *file* count of the directory's subtree; ``status_counts`` buckets those
    descendants like the tree endpoint (pending/completed/failed/unknown).
    Additive fields — upstream consumers that only know the base shape are
    unaffected."""

    child_count: int = 0
    status_counts: dict[str, int] = Field(default_factory=dict)


class KnowledgeFileListResponse(BaseModel):
    items: list[Union[FileUserResponse, FileUserMetadataResponse]]
    directories: list[KnowledgeDirectoryEntry] = Field(default_factory=list)
    breadcrumbs: list[KnowledgeDirectoryModel] = Field(default_factory=list)
    total: int


####################
SUSPENSION_TTL_DAYS = 30

# Knowledge ``meta`` keys written by every cloud-sync worker (one per provider —
# see each worker's ``meta_key`` property). Used by the suspension lookups below
# to answer "is this KB synced by ANY provider?". Keep this in sync with the
# providers registered in the sync factory. NOTE: this is the FULL set including
# per-user providers; it is intentionally broader than
# ``services.sync.shared_kb.SHARED_SYNC_META_KEYS`` (shared providers only).
SYNC_PROVIDER_META_KEYS = ('onedrive_sync', 'google_drive_sync', 'confluence_sync')


def _path_fields_from_meta(meta: Optional[dict]) -> tuple[Optional[str], Optional[str]]:
    """Extract the ``(relative_path, source_item_id)`` pair from a file's ``meta``.

    Single source of truth for what gets mirrored into the denormalized
    ``knowledge_file`` columns. Returns ``(None, None)`` when ``meta`` is absent
    or not a dict (loose local uploads), and coerces non-string values to
    ``None`` so a malformed provider payload can never poison the path columns.
    """
    if not isinstance(meta, dict):
        return None, None
    relative_path = meta.get('relative_path')
    source_item_id = meta.get('source_item_id')
    return (
        relative_path if isinstance(relative_path, str) else None,
        source_item_id if isinstance(source_item_id, str) else None,
    )


# Per-KB serialization of directory-chain materialization (P2-8 reverse
# bridge). The unique constraint (knowledge_id, parent_id, name) protects
# nested levels, but both SQLite and Postgres treat NULL parent_id values as
# distinct, so concurrent root-level creates would not conflict — this lock
# closes that hole for the common case (concurrent link calls within one
# process); a cross-process race is additionally converged in
# ``_find_or_create_directory``. Entries are tiny and bounded by the number of
# KBs touched since process start, so the dict is never pruned.
_directory_chain_locks: dict[str, asyncio.Lock] = {}


def _directory_segments_for_link(
    knowledge_meta: Optional[dict], source_item_id: str, relative_path: str
) -> Optional[tuple[list[str], Optional[str]]]:
    """Compute the directory chain a sourced file materializes under (P2-8).

    Returns ``(segments, meta_key)`` where ``segments`` is the root→leaf list
    of directory names and ``meta_key`` is the provider sync blob holding the
    matched source (``None`` for file-type sources, which get no source-root
    wrapper — their ``root_directory_id`` is never stamped). Returns ``None``
    when the source is not in the KB's registry (e.g. a stored-vs-canonical id
    mismatch): materialization is skipped rather than minting a raw-id folder —
    the next re-link after the registry self-heals converges it.
    """
    meta = knowledge_meta or {}
    for meta_key in SYNC_PROVIDER_META_KEYS:
        sync_info = meta.get(meta_key)
        if not isinstance(sync_info, dict):
            continue
        for entry in sync_info.get('sources') or []:
            if not (isinstance(entry, dict) and entry.get('item_id') == source_item_id):
                continue
            path_dirs = [segment for segment in relative_path.split('/')[:-1] if segment]
            if entry.get('type') == 'file':
                # A picked *file* source surfaces at the KB root (PR #235):
                # no wrapper folder, only whatever sub-path the file carries.
                return path_dirs, None
            root_name = (
                entry.get('name') or (entry.get('item_path') or '').rstrip('/').rsplit('/', 1)[-1] or source_item_id
            )
            return [root_name] + path_dirs, meta_key
    return None


# --- Lazy folder-tree helpers -------------------------------------------------

# Raw per-file status → coarse bucket for the per-folder rollup. Absent/NULL or
# any unrecognised value falls through to ``unknown``; ``error`` collapses into
# ``failed``; the in-progress states collapse into ``pending``.
_STATUS_BUCKETS = {
    'completed': 'completed',
    'failed': 'failed',
    'error': 'failed',
    'pending': 'pending',
    # Written by the distributed doc-pipeline path (routers/retrieval.py's
    # set_status calls) while a file is parsed/chunked/embedded. Was absent
    # here, so those files bucketed as 'unknown': folder rollups undercounted
    # pending work and ?status=pending could not find them.
    'processing': 'pending',
    'downloading': 'pending',
    'parsing': 'pending',
    'ingesting': 'pending',
}
_STATUS_BUCKET_KEYS = ('pending', 'completed', 'failed', 'unknown')

# Inverse of _STATUS_BUCKETS: a coarse bucket → the raw statuses that map into
# it. Used by the flat search filter to expand e.g. ?status=failed into a match
# on both 'failed' and 'error'.
_STATUS_BUCKET_RAWS = {
    'pending': ('pending', 'processing', 'downloading', 'parsing', 'ingesting'),
    'completed': ('completed',),
    'failed': ('failed', 'error'),
}


def _status_bucket(raw_status: Optional[str]) -> str:
    return _STATUS_BUCKETS.get(raw_status, 'unknown')


def _empty_status_counts() -> dict[str, int]:
    return {k: 0 for k in _STATUS_BUCKET_KEYS}


def _unwrap_json_text(value):
    """Unwrap a ``cast(json_col['key'], Text)`` value.

    On both SQLite and Postgres the cast yields JSON-encoded text (a string
    field comes back as ``"value"`` with quotes); ``json.loads`` normalises it.
    Returns the raw value unchanged if it is not JSON-decodable.
    """
    if value is None:
        return None
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value


def _escape_like(s: str) -> str:
    """Escape LIKE wildcards so a literal path prefix can't be a wildcard.

    Filenames and folders routinely contain ``_`` (and occasionally ``%``);
    without escaping, ``LIKE 'my_folder/%'`` would match ``myXfolder/…`` too.
    Pair with ``.like(pattern, escape='\\')``.
    """
    return s.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


def _encode_cursor(values: list) -> str:
    return base64.urlsafe_b64encode(json.dumps(values).encode()).decode()


def _decode_cursor(cursor: Optional[str]) -> Optional[list]:
    if not cursor:
        return None
    try:
        decoded = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
        return decoded if isinstance(decoded, list) else None
    except Exception:
        return None


def _parse_tree_path(path: str) -> tuple[Optional[str], str]:
    """Split a tree ``path`` into ``(source_item_id, relative_prefix)``.

    ``""`` → ``(None, "")`` (the sources level). ``"S"`` → ``("S", "")`` (a
    source's root). ``"S/folder/sub/"`` → ``("S", "folder/sub/")``. The
    ``relative_prefix``, when non-empty, always keeps its trailing slash so it
    composes directly with ``LIKE prefix||'%'``.
    """
    if not path:
        return None, ''
    if '/' not in path:
        return path, ''
    source_item_id, relative_prefix = path.split('/', 1)
    return source_item_id, relative_prefix


class KnowledgeTable:
    async def _get_access_grants(self, knowledge_id: str, db: Optional[AsyncSession] = None) -> list[AccessGrantModel]:
        return await AccessGrants.get_grants_by_resource('knowledge', knowledge_id, db=db)

    async def _to_knowledge_model(
        self,
        knowledge: Knowledge,
        access_grants: Optional[list[AccessGrantModel]] = None,
        db: Optional[AsyncSession] = None,
    ) -> KnowledgeModel:
        knowledge_model = KnowledgeModel.model_validate(knowledge)
        knowledge_model.access_grants = (
            access_grants if access_grants is not None else await self._get_access_grants(knowledge_model.id, db=db)
        )
        return knowledge_model

    async def insert_new_knowledge(
        self, user_id: str, form_data: KnowledgeForm, db: Optional[AsyncSession] = None
    ) -> Optional[KnowledgeModel]:
        async with get_async_db_context(db) as db:
            payload = form_data.model_dump(exclude={'access_grants'})
            # KnowledgeForm.type is Optional[str] = None while KnowledgeModel.type
            # is a required str defaulting to 'local'. Passing the None through
            # overrides that default and fails validation, so every caller that
            # does not set a type got a 500 -- create_external_knowledge builds
            # its form without one, so that route could never succeed. Drop the
            # unset value and let the model supply its default.
            if payload.get('type') is None:
                payload.pop('type', None)
            knowledge = KnowledgeModel(
                **{
                    **payload,
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
            result = await db.execute(
                select(Knowledge).filter(Knowledge.deleted_at.is_(None)).order_by(Knowledge.updated_at.desc())
            )
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
        db: Optional[AsyncSession] = None,
    ) -> KnowledgeListResponse:
        try:
            async with get_async_db_context(db) as db:
                stmt = (
                    select(Knowledge, User)
                    .outerjoin(User, User.id == Knowledge.user_id)
                    .filter(Knowledge.deleted_at.is_(None))
                )

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

                    type_filter = filter.get('type')
                    if type_filter:
                        stmt = stmt.filter(Knowledge.type == type_filter)

                    # Upstream external-knowledge source filter (adopted additively).
                    source = filter.get('source')
                    if source == 'external':
                        stmt = stmt.filter(Knowledge.meta['source'].as_string() == 'external')
                    elif source == 'local':
                        stmt = stmt.filter(
                            or_(
                                Knowledge.meta.is_(None),
                                Knowledge.meta['source'].as_string() != 'external',
                            )
                        )

                    stmt = AccessGrants.has_permission_filter(
                        db=db,
                        query=stmt,
                        DocumentModel=Knowledge,
                        filter=filter,
                        resource_type='knowledge',
                        permission='read',
                    )

                order_by = (filter or {}).get('order_by')
                direction = (filter or {}).get('direction')

                if order_by in KNOWLEDGE_SORTABLE_FIELDS:
                    column = getattr(Knowledge, order_by)
                    if (direction or 'desc').lower() == 'asc':
                        stmt = stmt.order_by(column.asc(), Knowledge.id.asc())
                    else:
                        stmt = stmt.order_by(column.desc(), Knowledge.id.asc())
                else:
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
                file_counts = {}
                if knowledge_ids:
                    file_count_result = await db.execute(
                        select(KnowledgeFile.knowledge_id, func.count(KnowledgeFile.id))
                        .where(KnowledgeFile.knowledge_id.in_(knowledge_ids))
                        .group_by(KnowledgeFile.knowledge_id)
                    )
                    file_counts = dict(file_count_result.all())

                knowledge_bases = []
                for knowledge_base, user in items:
                    kb_data = {
                        **(
                            await self._to_knowledge_model(
                                knowledge_base,
                                access_grants=grants_map.get(knowledge_base.id, []),
                                db=db,
                            )
                        ).model_dump(),
                        'file_count': file_counts.get(knowledge_base.id, 0),
                        'user': (UserModel.model_validate(user).model_dump() if user else None),
                    }

                    # Annotate suspension info for cloud KBs
                    if knowledge_base.type not in ('local',) and knowledge_base.meta:
                        for meta_key in SYNC_PROVIDER_META_KEYS:
                            sync_info = (knowledge_base.meta or {}).get(meta_key, {})
                            suspended_at = sync_info.get('suspended_at')
                            if suspended_at:
                                kb_data['suspension_info'] = {
                                    'suspended_at': suspended_at,
                                    'reason': sync_info.get('suspended_reason', 'unknown'),
                                    'days_remaining': max(
                                        0, SUSPENSION_TTL_DAYS - ((int(time.time()) - suspended_at) // 86400)
                                    ),
                                }
                                break

                    knowledge_bases.append(KnowledgeUserModel.model_validate(kb_data))

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
        # Phase 3: chat-attach by individual KB file is no longer offered —
        # KB-uploaded files no longer have per-file vector collections. Whole-KB
        # attach via search_knowledge_bases is the supported path. Keep the
        # original implementation below intact for an easy revert if needed.
        return KnowledgeFileListResponse(items=[], total=0)
        try:
            async with get_async_db_context(db) as db:
                # Base query: join Knowledge → KnowledgeFile → File
                stmt = (
                    select(File, User, Knowledge)
                    .join(KnowledgeFile, File.id == KnowledgeFile.file_id)
                    .join(Knowledge, KnowledgeFile.knowledge_id == Knowledge.id)
                    .outerjoin(User, User.id == KnowledgeFile.user_id)
                    .filter(Knowledge.deleted_at.is_(None))
                )

                # Only return files from local KBs — cloud KB files don't have
                # per-file vector collections and can't be attached individually
                stmt = stmt.filter(Knowledge.type == 'local')

                # Apply access-control directly to the joined query
                stmt = AccessGrants.has_permission_filter(
                    db=db,
                    query=stmt,
                    DocumentModel=Knowledge,
                    filter=filter,
                    resource_type='knowledge',
                    permission='read',
                )

                # Apply filename / content search
                if filter:
                    q = filter.get('query')
                    if q:
                        stmt = stmt.filter(
                            or_(
                                File.filename.ilike(f'%{q}%'),
                                cast(File.data['content'], Text).ilike(f'%{q}%'),
                            )
                        )

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

    async def check_access_by_user_id(
        self,
        id,
        user_id,
        permission='write',
        db: Optional[AsyncSession] = None,
        user_group_ids: set[str] | None = None,
    ) -> bool:
        knowledge = await self.get_knowledge_by_id(id, db=db)
        if not knowledge:
            return False
        if knowledge.user_id == user_id:
            return True
        if user_group_ids is None:
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

    async def get_knowledge_bases_by_type(self, type: str, db: Optional[AsyncSession] = None) -> list[KnowledgeModel]:
        """Get all knowledge bases of a specific type (no pagination limit). Used by Gradient sync schedulers."""
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(Knowledge)
                .filter_by(type=type)
                .filter(Knowledge.deleted_at.is_(None))
                .order_by(Knowledge.updated_at.desc())
            )
            return [KnowledgeModel.model_validate(kb) for kb in result.scalars().all()]

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

    async def get_knowledge_items_by_user_id(
        self, user_id: str, db: Optional[AsyncSession] = None
    ) -> list[KnowledgeModel]:
        """Get all knowledge bases owned by a user (for deletion). Consumed by DeletionService."""
        try:
            async with get_async_db_context(db) as db:
                result = await db.execute(
                    select(Knowledge).filter_by(user_id=user_id).filter(Knowledge.deleted_at.is_(None))
                )
                knowledges = result.scalars().all()
                return [await self._to_knowledge_model(k, db=db) for k in knowledges]
        except Exception:
            return []

    async def get_knowledge_by_id(self, id: str, db: Optional[AsyncSession] = None) -> Optional[KnowledgeModel]:
        try:
            async with get_async_db_context(db) as db:
                # Preserve our soft-delete filter — soft-deleted KBs are
                # invisible to all callers until either restored or hard-deleted
                # by the retention worker.
                result = await db.execute(select(Knowledge).filter_by(id=id).filter(Knowledge.deleted_at.is_(None)))
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

    async def get_knowledge_by_file_id(
        self, file_id: str, db: Optional[AsyncSession] = None
    ) -> Optional[KnowledgeModel]:
        """Return the first KB that has this file linked via KnowledgeFile.

        Used by the built-in knowledge-search tool to resolve a file attached as
        __model_knowledge__ back to its containing KB when no per-file
        `file-<id>` collection exists (KB-uploaded files post-Phase-2).
        """
        try:
            async with get_async_db_context(db) as db:
                result = await db.execute(
                    select(Knowledge)
                    .join(KnowledgeFile, Knowledge.id == KnowledgeFile.knowledge_id)
                    .filter(KnowledgeFile.file_id == file_id)
                    .filter(Knowledge.deleted_at.is_(None))
                )
                row = result.scalars().first()
                return await self._to_knowledge_model(row, db=db) if row else None
        except Exception as e:
            log.exception(e)
            return None

    async def get_knowledges_by_file_id(self, file_id: str, db: Optional[AsyncSession] = None) -> list[KnowledgeModel]:
        try:
            async with get_async_db_context(db) as db:
                result = await db.execute(
                    select(Knowledge)
                    .join(KnowledgeFile, Knowledge.id == KnowledgeFile.knowledge_id)
                    .filter(KnowledgeFile.file_id == file_id)
                    .filter(Knowledge.deleted_at.is_(None))
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

    async def get_knowledge_files_by_file_id(
        self, file_id: str, db: Optional[AsyncSession] = None
    ) -> list[KnowledgeFileModel]:
        """Get all knowledge_file records for a given file_id. Consumed by DeletionService.delete_file."""
        try:
            async with get_async_db_context(db) as db:
                result = await db.execute(select(KnowledgeFile).filter_by(file_id=file_id))
                return [KnowledgeFileModel.model_validate(kf) for kf in result.scalars().all()]
        except Exception:
            return []

    async def get_referenced_file_ids(self, file_ids: list[str], db: Optional[AsyncSession] = None) -> set[str]:
        """Return the subset of file_ids that still have knowledge_file references. Consumed by DeletionService."""
        if not file_ids:
            return set()
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(KnowledgeFile.file_id).filter(KnowledgeFile.file_id.in_(file_ids)).distinct()
            )
            return {row[0] for row in result.all()}

    async def get_file_counts_by_knowledge_ids(
        self, knowledge_ids: list[str], db: Optional[AsyncSession] = None
    ) -> dict[str, int]:
        """Return ``{knowledge_id: file_count}`` for the given KBs in one grouped query.

        Used by the cloud-sync status endpoint to size each provider's KBs
        without an N+1 fan-out over ``get_knowledge_files_*``. KBs with no
        files are omitted from the result; callers default missing ids to 0.
        """
        if not knowledge_ids:
            return {}
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(KnowledgeFile.knowledge_id, func.count(KnowledgeFile.file_id))
                .filter(KnowledgeFile.knowledge_id.in_(knowledge_ids))
                .group_by(KnowledgeFile.knowledge_id)
            )
            return {row[0]: row[1] for row in result.all()}

    async def search_files_by_id(
        self,
        knowledge_id: str,
        user_id: str,
        filter: dict,
        skip: int = 0,
        limit: int = 30,
        metadata_only: bool = False,
        db: Optional[AsyncSession] = None,
    ) -> KnowledgeFileListResponse:
        """Paginated per-KB file search.

        Union of the fork's ``metadata_only`` slim path (projects only the
        columns the file-list UI needs; ``added_at`` from the join row) and
        upstream's directory model + perf trio (``directory_id`` scoping,
        ``include_content`` substr search that dodges the Postgres large-content
        memory blowup #24670, and ``defer(File.data)`` on the full path so the
        heavy content blob is never de-TOASTed). ``directory_id`` /
        ``include_content`` ride inside ``filter`` (D3 API union).
        """
        try:
            async with get_async_db_context(db) as db:
                if metadata_only:
                    # Project only the columns we need — the heavy data.content
                    # blob is NEVER loaded into Python.
                    # cast(File.data['key'], Text) is the cross-DB JSON text
                    # extraction idiom already proven in this file. .astext is
                    # Postgres-only; cast(..., Text) works on both SQLite and
                    # Postgres.
                    stmt = (
                        select(
                            File.id,
                            File.user_id,
                            File.hash,
                            File.filename,
                            File.meta,
                            File.created_at,
                            File.updated_at,
                            cast(File.meta['status'], Text).label('status'),
                            cast(File.meta['error'], Text).label('error'),
                            User,
                            KnowledgeFile.created_at.label('added_at'),
                        )
                        .join(KnowledgeFile, File.id == KnowledgeFile.file_id)
                        .outerjoin(User, User.id == KnowledgeFile.user_id)
                        .filter(KnowledgeFile.knowledge_id == knowledge_id)
                    )
                else:
                    stmt = (
                        select(File, User, KnowledgeFile.created_at)
                        .join(KnowledgeFile, File.id == KnowledgeFile.file_id)
                        .outerjoin(User, User.id == KnowledgeFile.user_id)
                        .filter(KnowledgeFile.knowledge_id == knowledge_id)
                    )

                # Upstream directory scoping (adopted additively). A truthy
                # directory_id scopes to that directory; an explicit None key
                # scopes to the KB root; an absent key = no directory filter.
                directory_id = filter.get('directory_id') if filter else None
                has_directory_filter = bool(filter) and 'directory_id' in filter
                if directory_id:
                    stmt = stmt.filter(KnowledgeFile.directory_id == directory_id)
                elif has_directory_filter:
                    stmt = stmt.filter(KnowledgeFile.directory_id.is_(None))

                # Default sort: filename ascending (alphabetical)
                primary_sort = File.filename.asc()

                # For the metadata path: build a cheap count stmt in parallel.
                # It starts with the same join + knowledge_id filter but projects
                # no JSON columns and applies no ORDER BY — the two most expensive
                # parts of the full stmt subquery.
                if metadata_only:
                    count_stmt = (
                        select(func.count())
                        .select_from(KnowledgeFile)
                        .join(File, File.id == KnowledgeFile.file_id)
                        .filter(KnowledgeFile.knowledge_id == knowledge_id)
                    )
                    if directory_id:
                        count_stmt = count_stmt.filter(KnowledgeFile.directory_id == directory_id)
                    elif has_directory_filter:
                        count_stmt = count_stmt.filter(KnowledgeFile.directory_id.is_(None))

                if filter:
                    query_key = filter.get('query')
                    if query_key:
                        if filter.get('include_content'):
                            # Use ->> (as_string) + substr instead of
                            # CAST(-> AS TEXT) to avoid PostgreSQL "invalid memory
                            # alloc request size" on large extracted-content rows
                            # (#24670).
                            content_text = File.data['content'].as_string()
                            content_text = func.substr(content_text, 1, RAG_FILE_CONTENT_SEARCH_MAX_CHARS)
                            content_filter = or_(
                                File.filename.ilike(f'%{query_key}%'),
                                content_text.ilike(f'%{query_key}%'),
                            )
                        else:
                            content_filter = File.filename.ilike(f'%{query_key}%')
                        stmt = stmt.filter(content_filter)
                        # Content search still needs the File join + data filter
                        # on the count — this is the one case where touching data
                        # is unavoidable.  All other count paths avoid data entirely.
                        if metadata_only:
                            count_stmt = count_stmt.filter(content_filter)

                    view_option = filter.get('view_option')
                    if view_option == 'created':
                        stmt = stmt.filter(KnowledgeFile.user_id == user_id)
                        if metadata_only:
                            count_stmt = count_stmt.filter(KnowledgeFile.user_id == user_id)
                    elif view_option == 'shared':
                        stmt = stmt.filter(KnowledgeFile.user_id != user_id)
                        if metadata_only:
                            count_stmt = count_stmt.filter(KnowledgeFile.user_id != user_id)

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

                # Count BEFORE pagination.
                # metadata_only path: cheap count (no JSON projection, no ORDER BY).
                # full-content path: existing subquery approach (unchanged).
                if metadata_only:
                    count_result = await db.execute(count_stmt)
                else:
                    count_result = await db.execute(select(func.count()).select_from(stmt.subquery()))
                total = count_result.scalar()

                if skip:
                    stmt = stmt.offset(skip)
                if limit:
                    stmt = stmt.limit(limit)

                if not metadata_only:
                    # Perf: never de-TOAST the heavy data.content blob on the
                    # full path — no consumer of this method reads File.data.
                    stmt = stmt.options(defer(File.data))

                result = await db.execute(stmt)
                items = result.all()

                files: list[Union[FileUserResponse, FileUserMetadataResponse]] = []
                if metadata_only:
                    for row in items:
                        row_map = row._mapping
                        user_obj = row_map.get('User')
                        # cast(JSON_col['key'], Text) returns raw JSON text:
                        # on SQLite that is `"value"` (with surrounding quotes)
                        # for string fields; on Postgres it is also JSON-encoded.
                        # Use json.loads() to unwrap consistently on both.
                        raw_status = row_map.get('status')
                        raw_error = row_map.get('error')
                        try:
                            status_val = json.loads(raw_status) if raw_status is not None else None
                        except (json.JSONDecodeError, TypeError):
                            status_val = raw_status
                        try:
                            error_val = json.loads(raw_error) if raw_error is not None else None
                        except (json.JSONDecodeError, TypeError):
                            error_val = raw_error
                        files.append(
                            FileUserMetadataResponse(
                                id=row_map['id'],
                                user_id=row_map['user_id'],
                                hash=row_map.get('hash'),
                                filename=row_map['filename'],
                                meta=row_map.get('meta'),
                                created_at=row_map['created_at'],
                                updated_at=row_map.get('updated_at'),
                                status=status_val,
                                error=error_val,
                                user=(
                                    UserResponse(**UserModel.model_validate(user_obj).model_dump())
                                    if user_obj
                                    else None
                                ),
                                added_at=row_map.get('added_at'),
                            )
                        )
                else:
                    for file, user, added_at in items:
                        # File.data is deferred — construct explicitly so we never
                        # touch (and lazy-load) the deferred column in async.
                        files.append(
                            FileUserResponse(
                                id=file.id,
                                user_id=file.user_id,
                                hash=file.hash,
                                filename=file.filename,
                                meta=file.meta,
                                created_at=file.created_at,
                                updated_at=file.updated_at,
                                user=(UserResponse(**UserModel.model_validate(user).model_dump()) if user else None),
                                added_at=added_at,
                            )
                        )

                directories = await self.get_directories(
                    knowledge_id,
                    parent_id=filter.get('directory_id') if filter else None,
                    db=db,
                )
                # P2-8 decision 3: annotate each directory with its recursive
                # descendant file count + coarse status buckets.
                rollups = await self.get_directory_rollups(knowledge_id, [d.id for d in directories], db=db)
                return KnowledgeFileListResponse(
                    items=files,
                    directories=[
                        KnowledgeDirectoryEntry(
                            **directory.model_dump(),
                            child_count=rollups.get(directory.id, {}).get('child_count', 0),
                            status_counts=rollups.get(directory.id, {}).get('status_counts', _empty_status_counts()),
                        )
                        for directory in directories
                    ],
                    breadcrumbs=await self.get_directory_breadcrumbs(
                        filter.get('directory_id') if filter else None,
                        db=db,
                    ),
                    total=total,
                )
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
        """Column-only listing: File.data holds each file's full extracted
        text, which metadata views must never load."""
        try:
            async with get_async_db_context(db) as db:
                result = await db.execute(
                    select(File.id, File.hash, File.meta, File.created_at, File.updated_at)
                    .join(KnowledgeFile, File.id == KnowledgeFile.file_id)
                    .filter(KnowledgeFile.knowledge_id == knowledge_id)
                )
                return [
                    FileMetadataResponse(
                        id=row.id,
                        hash=row.hash,
                        meta=row.meta,
                        created_at=row.created_at,
                        updated_at=row.updated_at,
                    )
                    for row in result.all()
                ]
        except Exception:
            return []

    # ------------------------------------------------------------------ #
    # Reverse bridge (P2-8): relative_path → knowledge_directory rows
    # ------------------------------------------------------------------ #

    async def _find_or_create_directory(
        self,
        db: AsyncSession,
        knowledge_id: str,
        parent_id: Optional[str],
        name: str,
        user_id: str,
    ) -> str:
        """Find-or-create one directory level, race-safe.

        Nested levels are protected by the unique constraint
        ``(knowledge_id, parent_id, name)`` — a lost insert race raises
        ``IntegrityError`` and re-selects the winner. Root levels
        (``parent_id IS NULL``) are outside that constraint (NULLs compare
        distinct on both backends), so after inserting a root we re-select the
        canonical row (oldest ``(created_at, id)``) and, if a cross-process
        race created a twin first, drop our copy and adopt the canonical id.
        """

        async def _find() -> Optional[KnowledgeDirectory]:
            stmt = select(KnowledgeDirectory).filter(
                KnowledgeDirectory.knowledge_id == knowledge_id,
                KnowledgeDirectory.name == name,
                (KnowledgeDirectory.parent_id == parent_id) if parent_id else KnowledgeDirectory.parent_id.is_(None),
            )
            stmt = stmt.order_by(KnowledgeDirectory.created_at.asc(), KnowledgeDirectory.id.asc())
            return (await db.execute(stmt)).scalars().first()

        existing = await _find()
        if existing:
            return existing.id

        now = int(time.time())
        directory = KnowledgeDirectory(
            id=str(uuid.uuid4()),
            knowledge_id=knowledge_id,
            parent_id=parent_id,
            name=name,
            user_id=user_id,
            created_at=now,
            updated_at=now,
        )
        db.add(directory)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            existing = await _find()
            if existing:
                return existing.id
            raise

        if parent_id is None:
            canonical = await _find()
            if canonical and canonical.id != directory.id:
                await db.execute(delete(KnowledgeDirectory).filter_by(id=directory.id))
                await db.commit()
                return canonical.id
        return directory.id

    async def _ensure_directory_chain(
        self,
        knowledge_id: str,
        user_id: str,
        segments: list[str],
        db: AsyncSession,
    ) -> list[str]:
        """Ensure the root→leaf directory chain exists; return its ids in order.

        Idempotent (find-or-create per segment) and serialized per KB so
        concurrent link calls during a sync burst converge on one chain.
        """
        if not segments:
            return []
        lock = _directory_chain_locks.setdefault(knowledge_id, asyncio.Lock())
        async with lock:
            chain_ids: list[str] = []
            parent_id: Optional[str] = None
            for name in segments:
                parent_id = await self._find_or_create_directory(db, knowledge_id, parent_id, name, user_id)
                chain_ids.append(parent_id)
            return chain_ids

    async def _stamp_source_root_directory(
        self,
        db: AsyncSession,
        knowledge_id: str,
        meta_key: str,
        source_item_id: str,
        root_directory_id: str,
    ) -> None:
        """Record ``sources[].root_directory_id`` in the provider meta blob.

        Used by remove-source cleanup (drop the source's subtree) and by the UI
        to map a root directory back to its source. Re-stamped whenever the
        stored value diverges, so a stamp lost to a concurrent sync-worker meta
        write self-heals on the next materialization.
        """
        knowledge = (await db.execute(select(Knowledge).filter_by(id=knowledge_id))).scalars().first()
        if not knowledge:
            return
        meta = dict(knowledge.meta or {})
        sync_info = meta.get(meta_key)
        if not isinstance(sync_info, dict):
            return
        sources = [dict(entry) if isinstance(entry, dict) else entry for entry in sync_info.get('sources') or []]
        changed = False
        for entry in sources:
            if isinstance(entry, dict) and entry.get('item_id') == source_item_id:
                if entry.get('root_directory_id') != root_directory_id:
                    entry['root_directory_id'] = root_directory_id
                    changed = True
                break
        if not changed:
            return
        meta[meta_key] = {**sync_info, 'sources': sources}
        knowledge.meta = meta
        knowledge.updated_at = int(time.time())
        await db.commit()

    async def _materialize_directory_for_link(
        self,
        db: AsyncSession,
        knowledge_id: str,
        user_id: str,
        relative_path: str,
        source_item_id: str,
    ) -> Optional[str]:
        """Derive + ensure the directory placement for a sourced file (P2-8).

        Chain = ``<source display name>/<relative_path dirs>`` for folder-like
        sources (file-type sources get no wrapper; unknown sources are
        skipped). Returns the leaf directory id, or ``None`` when there is
        nothing to place under. Never raises — a materialization failure must
        not break file linking (the path columns still drive the legacy tree).
        """
        try:
            knowledge = (await db.execute(select(Knowledge).filter_by(id=knowledge_id))).scalars().first()
            resolved = _directory_segments_for_link(
                knowledge.meta if knowledge else None, source_item_id, relative_path
            )
            if resolved is None:
                return None
            segments, meta_key = resolved
            chain_ids = await self._ensure_directory_chain(knowledge_id, user_id, segments, db)
            if not chain_ids:
                return None
            if meta_key is not None:
                await self._stamp_source_root_directory(db, knowledge_id, meta_key, source_item_id, chain_ids[0])
            return chain_ids[-1]
        except Exception as e:
            log.exception(f'Directory materialization failed for knowledge {knowledge_id}: {e}')
            return None

    async def add_file_to_knowledge_by_id(
        self,
        knowledge_id: str,
        file_id: str,
        user_id: str,
        directory_id: Optional[str] = None,
        db: Optional[AsyncSession] = None,
    ) -> Optional[KnowledgeFileModel]:
        """Link a file to a KB, mirroring its path fields onto the join row.

        Idempotent upsert: if the ``(knowledge_id, file_id)`` link already
        exists (a re-sync), the denormalized ``relative_path`` /
        ``source_item_id`` columns are refreshed from the file's current
        ``meta`` rather than left stale — this is how a file that moved folders
        between syncs gets its tree position corrected. The path fields are read
        from the ``file`` row here so every caller (router add-file, cloud-sync
        stub + legacy paths, ``/ingest`` create) populates them consistently
        without threading ``meta`` through their call sites.

        ``directory_id`` is the upstream directory model's placement (D2,
        adopted additively). It coexists with the fork path columns; the
        endpoint-level ``directory_id``→``relative_path`` bridge lives in the
        routers task. On re-link it is refreshed only when the caller provides
        one: idempotent ensure-linked callers (``/integrations/submit``,
        ``/integrations/ingest``) pass ``None`` and must not clear stage-time
        placement — clearing or moving is ``move_file_to_directory``'s job.

        Reverse bridge (P2-8 decision 1): when no caller placement is given and
        the file carries provider path identity (``relative_path`` +
        folder-like ``source_item_id``), the directory chain is materialized
        find-or-create and stamped here — so the loader-worker sync path and
        the daemon's ``/stage`` path converge on the same
        ``knowledge_directory`` rows. A derived placement also refreshes an
        existing one (a file that moved folders between syncs tracks its new
        chain); files without path identity keep today's preserve-placement
        semantics untouched.
        """
        async with get_async_db_context(db) as db:
            try:
                file = await db.get(File, file_id)
                relative_path, source_item_id = _path_fields_from_meta(file.meta if file else None)

                materialized_id = None
                if directory_id is None and relative_path and source_item_id:
                    materialized_id = await self._materialize_directory_for_link(
                        db, knowledge_id, user_id, relative_path, source_item_id
                    )

                existing = (
                    (await db.execute(select(KnowledgeFile).filter_by(knowledge_id=knowledge_id, file_id=file_id)))
                    .scalars()
                    .first()
                )

                if existing:
                    existing.relative_path = relative_path
                    existing.source_item_id = source_item_id
                    if directory_id is not None:
                        existing.directory_id = directory_id
                    elif materialized_id is not None:
                        existing.directory_id = materialized_id
                    existing.updated_at = int(time.time())
                    await db.commit()
                    await db.refresh(existing)
                    return KnowledgeFileModel.model_validate(existing)

                result = KnowledgeFile(
                    id=str(uuid.uuid4()),
                    knowledge_id=knowledge_id,
                    file_id=file_id,
                    directory_id=directory_id if directory_id is not None else materialized_id,
                    user_id=user_id,
                    relative_path=relative_path,
                    source_item_id=source_item_id,
                    created_at=int(time.time()),
                    updated_at=int(time.time()),
                )
                db.add(result)
                await db.commit()
                await db.refresh(result)
                return KnowledgeFileModel.model_validate(result)
            except Exception:
                return None

    async def set_path_fields_by_file_id(
        self,
        file_id: str,
        meta: Optional[dict],
        db: Optional[AsyncSession] = None,
    ) -> bool:
        """Refresh the denormalized path columns on every link row for a file.

        Used by the ``/ingest`` callback's *existing-file* branch, which updates
        ``file.meta`` but (deliberately) never calls
        ``add_file_to_knowledge_by_id``. The path is a property of the file, not
        of the KB, so all of the file's ``knowledge_file`` rows are updated in
        one statement. A no-op (returns ``True``) when the file has no links.

        Reverse bridge (P2-8 decision 1): the refreshed path identity also
        re-materializes the directory placement per containing KB — the chain
        is KB-scoped, so each link row derives its own — mirroring what a
        re-link through ``add_file_to_knowledge_by_id`` would do. Placement is
        only touched when a chain was actually derived (unknown sources leave
        ``directory_id`` as-is).
        """
        relative_path, source_item_id = _path_fields_from_meta(meta)
        try:
            async with get_async_db_context(db) as db:
                await db.execute(
                    update(KnowledgeFile)
                    .filter_by(file_id=file_id)
                    .values(relative_path=relative_path, source_item_id=source_item_id)
                )
                await db.commit()

                if relative_path and source_item_id:
                    links = (await db.execute(select(KnowledgeFile).filter_by(file_id=file_id))).scalars().all()
                    for link in links:
                        materialized_id = await self._materialize_directory_for_link(
                            db, link.knowledge_id, link.user_id, relative_path, source_item_id
                        )
                        if materialized_id is not None and link.directory_id != materialized_id:
                            link.directory_id = materialized_id
                            link.updated_at = int(time.time())
                    await db.commit()
                return True
        except Exception as e:
            log.exception(e)
            return False

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

    async def reset_knowledge_by_id(
        self, id: str, include_directories: bool = True, db: Optional[AsyncSession] = None
    ) -> Optional[KnowledgeModel]:
        try:
            async with get_async_db_context(db) as db:
                # Delete all knowledge_file entries for this knowledge_id
                await db.execute(delete(KnowledgeFile).filter_by(knowledge_id=id))

                # Delete all directories if requested (upstream directory model)
                if include_directories:
                    await db.execute(delete(KnowledgeDirectory).filter_by(knowledge_id=id))

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
                        **form_data.model_dump(exclude={'access_grants'}, exclude_none=True),
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

    async def update_knowledge_user_id_by_id(
        self, id: str, user_id: str, db: Optional[AsyncSession] = None
    ) -> Optional[KnowledgeModel]:
        """Reassign a knowledge base's owner. Used by the shared Confluence KB provisioning flow.

        The standard ``update_knowledge_by_id`` cannot change ``user_id`` (it is not part
        of ``KnowledgeForm``). ``user_id`` may be '' for a system-owned KB.
        """
        try:
            async with get_async_db_context(db) as db:
                result = await db.execute(select(Knowledge).filter_by(id=id))
                knowledge = result.scalars().first()
                if knowledge:
                    knowledge.user_id = user_id
                    knowledge.updated_at = int(time.time())
                    await db.commit()
                    await db.refresh(knowledge)
                    return KnowledgeModel.model_validate(knowledge)
                return None
        except Exception:
            return None

    async def update_knowledge_meta_by_id(
        self, id: str, meta: dict, db: Optional[AsyncSession] = None
    ) -> Optional[KnowledgeModel]:
        """Update only the meta JSON on a knowledge base. Used by cloud-sync workers."""
        try:
            async with get_async_db_context(db) as db:
                result = await db.execute(select(Knowledge).filter_by(id=id))
                knowledge = result.scalars().first()
                if knowledge:
                    knowledge.meta = meta
                    knowledge.updated_at = int(time.time())
                    await db.commit()
                    await db.refresh(knowledge)
                    return KnowledgeModel.model_validate(knowledge)
                return None
        except Exception:
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
                .filter(Knowledge.type == 'local')  # Cloud KBs have suspension TTL
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
            return result.rowcount > 0

    async def soft_delete_by_user_id(self, user_id: str, db: Optional[AsyncSession] = None) -> int:
        """Soft-delete all knowledge bases for a user. Returns count."""
        async with get_async_db_context(db) as db:
            result = await db.execute(
                update(Knowledge)
                .filter_by(user_id=user_id)
                .filter(Knowledge.deleted_at.is_(None))
                .values(deleted_at=int(time.time()))
            )
            await db.commit()
            return result.rowcount

    async def get_knowledge_by_id_unfiltered(
        self, id: str, db: Optional[AsyncSession] = None
    ) -> Optional[KnowledgeModel]:
        """Get a knowledge base by ID, including soft-deleted ones. For internal/cleanup use only."""
        try:
            async with get_async_db_context(db) as db:
                result = await db.execute(select(Knowledge).filter_by(id=id))
                knowledge = result.scalars().first()
                return KnowledgeModel.model_validate(knowledge) if knowledge else None
        except Exception:
            return None

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
                for meta_key in SYNC_PROVIDER_META_KEYS:
                    sync_info = meta.get(meta_key, {})
                    suspended_at = sync_info.get('suspended_at')
                    if suspended_at and suspended_at < cutoff:
                        expired.append(await self._to_knowledge_model(kb, db=db))
                        break

                if len(expired) >= limit:
                    break

            return expired

    async def is_suspended(self, id: str, db: Optional[AsyncSession] = None) -> bool:
        """Check if a knowledge base is currently suspended."""
        try:
            async with get_async_db_context(db) as db:
                result = await db.execute(select(Knowledge).filter_by(id=id).filter(Knowledge.deleted_at.is_(None)))
                knowledge = result.scalars().first()
                if not knowledge:
                    return False
                meta = knowledge.meta or {}
                for meta_key in SYNC_PROVIDER_META_KEYS:
                    sync_info = meta.get(meta_key, {})
                    if sync_info.get('suspended_at'):
                        return True
                return False
        except Exception:
            return False

    async def get_suspension_info(self, id: str, db: Optional[AsyncSession] = None) -> Optional[dict]:
        """Get suspension details for a KB. Returns None if not suspended."""
        try:
            async with get_async_db_context(db) as db:
                result = await db.execute(select(Knowledge).filter_by(id=id).filter(Knowledge.deleted_at.is_(None)))
                knowledge = result.scalars().first()
                if not knowledge:
                    return None
                meta = knowledge.meta or {}
                for meta_key in SYNC_PROVIDER_META_KEYS:
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

    # ── Directory CRUD (upstream v0.10.2 directory model, adopted additively) ──

    async def create_directory(
        self,
        knowledge_id: str,
        name: str,
        user_id: str,
        parent_id: Optional[str] = None,
        db: Optional[AsyncSession] = None,
    ) -> Optional[KnowledgeDirectoryModel]:
        async with get_async_db_context(db) as db:
            try:
                now = int(time.time())
                directory = KnowledgeDirectory(
                    id=str(uuid.uuid4()),
                    knowledge_id=knowledge_id,
                    parent_id=parent_id,
                    name=name,
                    user_id=user_id,
                    created_at=now,
                    updated_at=now,
                )
                db.add(directory)
                await db.commit()
                await db.refresh(directory)
                return KnowledgeDirectoryModel.model_validate(directory)
            except Exception as e:
                log.exception(e)
                return None

    async def get_directories(
        self,
        knowledge_id: str,
        parent_id: Optional[str] = None,
        db: Optional[AsyncSession] = None,
    ) -> list[KnowledgeDirectoryModel]:
        """List directories at a given level (parent_id=None for root)."""
        async with get_async_db_context(db) as db:
            stmt = select(KnowledgeDirectory).filter(KnowledgeDirectory.knowledge_id == knowledge_id)
            if parent_id:
                stmt = stmt.filter(KnowledgeDirectory.parent_id == parent_id)
            else:
                stmt = stmt.filter(KnowledgeDirectory.parent_id.is_(None))

            stmt = stmt.order_by(KnowledgeDirectory.name.asc())
            result = await db.execute(stmt)
            return [KnowledgeDirectoryModel.model_validate(d) for d in result.scalars().all()]

    async def get_all_directories(
        self,
        knowledge_id: str,
        db: Optional[AsyncSession] = None,
    ) -> list[KnowledgeDirectoryModel]:
        """Get ALL directories for a KB (no parent filter). Used for tree building."""
        async with get_async_db_context(db) as db:
            stmt = (
                select(KnowledgeDirectory)
                .filter(KnowledgeDirectory.knowledge_id == knowledge_id)
                .order_by(KnowledgeDirectory.name.asc())
            )
            result = await db.execute(stmt)
            return [KnowledgeDirectoryModel.model_validate(d) for d in result.scalars().all()]

    async def get_files_with_directory_ids(
        self,
        knowledge_id: str,
        db: Optional[AsyncSession] = None,
    ) -> list[tuple[FileModel, Optional[str]]]:
        """Get all files in a KB with their directory_id from KnowledgeFile."""
        try:
            async with get_async_db_context(db) as db:
                result = await db.execute(
                    select(File, KnowledgeFile.directory_id)
                    .join(KnowledgeFile, File.id == KnowledgeFile.file_id)
                    .filter(KnowledgeFile.knowledge_id == knowledge_id)
                )
                return [(FileModel.model_validate(file), dir_id) for file, dir_id in result.all()]
        except Exception:
            return []

    def _directory_subtree_cte(self, knowledge_id: str, directory_ids: list[str]):
        """Recursive CTE mapping every descendant directory to its anchor.

        ``root_id`` is the id from ``directory_ids`` the descendant rolls up
        into; ``dir_id`` walks the subtree. Plain ``WITH RECURSIVE`` — works on
        both SQLite and Postgres.
        """
        subtree = (
            select(KnowledgeDirectory.id.label('root_id'), KnowledgeDirectory.id.label('dir_id'))
            .where(
                KnowledgeDirectory.knowledge_id == knowledge_id,
                KnowledgeDirectory.id.in_(directory_ids),
            )
            .cte('knowledge_directory_subtree', recursive=True)
        )
        child = aliased(KnowledgeDirectory)
        return subtree.union_all(select(subtree.c.root_id, child.id).where(child.parent_id == subtree.c.dir_id))

    async def get_directory_rollups(
        self,
        knowledge_id: str,
        directory_ids: list[str],
        db: Optional[AsyncSession] = None,
    ) -> dict[str, dict]:
        """Recursive per-directory rollups for the files response (P2-8 decision 3).

        Returns ``{directory_id: {'child_count': int, 'status_counts': {...}}}``
        over each directory's whole subtree, bucketing ``file.meta.status``
        exactly like the tree endpoint (``_STATUS_BUCKETS``). Directories with
        no descendant files are absent — callers default to zeroed buckets.
        """
        if not directory_ids:
            return {}
        async with get_async_db_context(db) as db:
            subtree = self._directory_subtree_cte(knowledge_id, directory_ids)
            raw_status = cast(File.meta['status'], Text)
            stmt = (
                select(subtree.c.root_id, raw_status.label('raw_status'), func.count().label('cnt'))
                .select_from(subtree)
                .join(KnowledgeFile, KnowledgeFile.directory_id == subtree.c.dir_id)
                .join(File, File.id == KnowledgeFile.file_id)
                .where(KnowledgeFile.knowledge_id == knowledge_id)
                .group_by(subtree.c.root_id, raw_status)
            )
            rollups: dict[str, dict] = {}
            for root_id, raw, cnt in (await db.execute(stmt)).all():
                entry = rollups.setdefault(root_id, {'child_count': 0, 'status_counts': _empty_status_counts()})
                entry['child_count'] += cnt
                entry['status_counts'][_status_bucket(_unwrap_json_text(raw))] += cnt
            return rollups

    async def get_file_ids_in_directory_subtree(
        self,
        knowledge_id: str,
        directory_id: str,
        db: Optional[AsyncSession] = None,
    ) -> list[str]:
        """All file ids linked anywhere in a directory's subtree (P2-8 ledger #4).

        Consumed by ``DeletionService.delete_directory`` to run the full fork
        cascade over "delete directory + contents".
        """
        async with get_async_db_context(db) as db:
            subtree = self._directory_subtree_cte(knowledge_id, [directory_id])
            stmt = (
                select(KnowledgeFile.file_id)
                .distinct()
                .select_from(subtree)
                .join(KnowledgeFile, KnowledgeFile.directory_id == subtree.c.dir_id)
                .where(KnowledgeFile.knowledge_id == knowledge_id)
            )
            return [row[0] for row in (await db.execute(stmt)).all()]

    async def get_directory_by_id(
        self, directory_id: str, db: Optional[AsyncSession] = None
    ) -> Optional[KnowledgeDirectoryModel]:
        async with get_async_db_context(db) as db:
            result = await db.execute(select(KnowledgeDirectory).filter_by(id=directory_id))
            directory = result.scalars().first()
            return KnowledgeDirectoryModel.model_validate(directory) if directory else None

    async def get_directory_breadcrumbs(
        self,
        directory_id: Optional[str],
        db: Optional[AsyncSession] = None,
    ) -> list[KnowledgeDirectoryModel]:
        """Walk up the parent chain to build breadcrumbs (root first)."""
        if not directory_id:
            return []

        async with get_async_db_context(db) as db:
            breadcrumbs = []
            current_id = directory_id
            seen = set()

            while current_id and current_id not in seen:
                seen.add(current_id)
                result = await db.execute(select(KnowledgeDirectory).filter_by(id=current_id))
                directory = result.scalars().first()
                if not directory:
                    break
                breadcrumbs.append(KnowledgeDirectoryModel.model_validate(directory))
                current_id = directory.parent_id

            breadcrumbs.reverse()  # root first
            return breadcrumbs

    async def rename_directory(
        self,
        directory_id: str,
        name: str,
        db: Optional[AsyncSession] = None,
    ) -> Optional[KnowledgeDirectoryModel]:
        async with get_async_db_context(db) as db:
            try:
                await db.execute(
                    update(KnowledgeDirectory).filter_by(id=directory_id).values(name=name, updated_at=int(time.time()))
                )
                await db.commit()
                return await self.get_directory_by_id(directory_id, db=db)
            except Exception as e:
                log.exception(e)
                return None

    async def move_directory(
        self,
        directory_id: str,
        new_parent_id: Optional[str],
        db: Optional[AsyncSession] = None,
    ) -> Optional[KnowledgeDirectoryModel]:
        """Move a directory to a new parent, with cycle detection."""
        async with get_async_db_context(db) as db:
            try:
                # Cycle detection: walk up from new_parent_id to ensure
                # we don't encounter directory_id
                if new_parent_id:
                    current = new_parent_id
                    seen = set()
                    while current and current not in seen:
                        if current == directory_id:
                            return None  # Would create a cycle
                        seen.add(current)
                        result = await db.execute(select(KnowledgeDirectory.parent_id).filter_by(id=current))
                        row = result.first()
                        current = row[0] if row else None

                await db.execute(
                    update(KnowledgeDirectory)
                    .filter_by(id=directory_id)
                    .values(parent_id=new_parent_id, updated_at=int(time.time()))
                )
                await db.commit()
                return await self.get_directory_by_id(directory_id, db=db)
            except Exception as e:
                log.exception(e)
                return None

    async def update_directory(
        self,
        directory_id: str,
        name: Optional[str] = None,
        parent_id: Optional[str] = '__unset__',
        db: Optional[AsyncSession] = None,
    ) -> Optional[KnowledgeDirectoryModel]:
        """Update directory name and/or parent. Pass parent_id=None to move to root."""
        # Handle move if parent_id is being changed
        if parent_id != '__unset__':
            result = await self.move_directory(directory_id, parent_id, db=db)
            if result is None:
                return None  # Cycle detected or error

        if name is not None:
            return await self.rename_directory(directory_id, name, db=db)

        return await self.get_directory_by_id(directory_id, db=db)

    async def delete_directory(
        self,
        directory_id: str,
        move_files_to_parent: bool = True,
        db: Optional[AsyncSession] = None,
    ) -> bool:
        """
        Delete a directory.
        - If move_files_to_parent=True: files move to parent dir (or root)
        - If move_files_to_parent=False: files are also deleted
        """
        async with get_async_db_context(db) as db:
            try:
                # Get the directory to find its parent
                result = await db.execute(select(KnowledgeDirectory).filter_by(id=directory_id))
                directory = result.scalars().first()
                if not directory:
                    return False

                parent_id = directory.parent_id

                if move_files_to_parent:
                    # Move files in this directory to its parent (or root)
                    await db.execute(
                        update(KnowledgeFile).filter_by(directory_id=directory_id).values(directory_id=parent_id)
                    )
                    # Recursively move files from all subdirectories too
                    await self._move_files_from_subtree(directory_id, parent_id, db=db)
                else:
                    # Delete files in this directory and all subdirectories
                    await self._delete_files_in_subtree(directory_id, db=db)

                # Delete the whole directory subtree explicitly. The FK's
                # ON DELETE CASCADE would cover this on Postgres, but SQLite
                # runs without PRAGMA foreign_keys, so relying on it there
                # would orphan the subdirectory rows.
                subtree = self._directory_subtree_cte(directory.knowledge_id, [directory_id])
                subtree_ids = [row[0] for row in (await db.execute(select(subtree.c.dir_id).distinct())).all()]
                await db.execute(delete(KnowledgeDirectory).filter(KnowledgeDirectory.id.in_(subtree_ids)))
                await db.commit()
                return True
            except Exception as e:
                log.exception(e)
                return False

    async def _move_files_from_subtree(
        self,
        directory_id: str,
        target_directory_id: Optional[str],
        db: AsyncSession,
    ) -> None:
        """Recursively move all files from a directory subtree to the target."""
        result = await db.execute(select(KnowledgeDirectory.id).filter_by(parent_id=directory_id))
        child_ids = [row[0] for row in result.all()]

        for child_id in child_ids:
            await db.execute(
                update(KnowledgeFile).filter_by(directory_id=child_id).values(directory_id=target_directory_id)
            )
            await self._move_files_from_subtree(child_id, target_directory_id, db=db)

    async def _delete_files_in_subtree(
        self,
        directory_id: str,
        db: AsyncSession,
    ) -> None:
        """Recursively delete all files from a directory subtree."""
        await db.execute(delete(KnowledgeFile).filter_by(directory_id=directory_id))
        result = await db.execute(select(KnowledgeDirectory.id).filter_by(parent_id=directory_id))
        child_ids = [row[0] for row in result.all()]
        for child_id in child_ids:
            await self._delete_files_in_subtree(child_id, db=db)

    async def move_file_to_directory(
        self,
        knowledge_id: str,
        file_id: str,
        directory_id: Optional[str] = None,
        db: Optional[AsyncSession] = None,
    ) -> bool:
        """Move a file to a different directory within the same KB."""
        async with get_async_db_context(db) as db:
            try:
                await db.execute(
                    update(KnowledgeFile)
                    .filter_by(knowledge_id=knowledge_id, file_id=file_id)
                    .values(directory_id=directory_id, updated_at=int(time.time()))
                )
                await db.commit()
                return True
            except Exception as e:
                log.exception(e)
                return False


from open_webui.soev.knowledge_store import SoevKnowledgeTable  # noqa: E402

Knowledges = SoevKnowledgeTable()
