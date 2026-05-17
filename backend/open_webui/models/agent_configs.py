"""[Gradient] AgentConfig — admin-managed metadata for external agents.

Each row corresponds to one slug from AGENT_API_AGENTS env. Owns display
name, description, icon, active flag, and access grants.

Routing uses ``id`` (== agent slug) directly; no routing fields live here.
"""

import logging
import time
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import BigInteger, Boolean, Column, Integer, JSON, Text, func
from sqlalchemy.orm import Session

from open_webui.internal.db import Base, get_db_context
from open_webui.models.access_grants import AccessGrantModel, AccessGrants

log = logging.getLogger(__name__)


####################
# DB Schema
####################


class AgentConfig(Base):
    __tablename__ = 'agent_config'

    id = Column(Text, primary_key=True, unique=True)  # matches AGENT_API_AGENTS slug
    user_id = Column(Text)  # admin who created
    name = Column(Text)
    description = Column(Text, nullable=True)
    profile_image_url = Column(Text, nullable=True)
    is_active = Column(Boolean, default=False)  # default OFF — admin must enable
    is_beta = Column(Boolean, default=True)
    meta = Column(JSON, server_default='{}')
    # Sort key for the admin panel + user-facing picker. Lower = earlier.
    # Backfilled by the d6e7f8a9b0c1 → e7f8a9b0c1d2 migration; new rows
    # default to 0 and are pushed to the end via insert_new_agent_config.
    position = Column(Integer, nullable=False, server_default='0')
    created_at = Column(BigInteger)
    updated_at = Column(BigInteger)


####################
# Pydantic models
####################


class AgentConfigModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: Optional[str] = None
    name: str
    description: Optional[str] = None
    profile_image_url: Optional[str] = None
    is_active: bool = False
    is_beta: bool = True
    meta: dict = Field(default_factory=dict)
    position: int = 0
    access_grants: list[AccessGrantModel] = Field(default_factory=list)
    created_at: int
    updated_at: int


class AgentConfigForm(BaseModel):
    name: str
    description: Optional[str] = None
    profile_image_url: Optional[str] = None
    is_active: bool = False
    is_beta: bool = True
    meta: Optional[dict] = None
    # Each entry: {principal_type, principal_id, permission}
    access_grants: Optional[list[dict]] = None


class AgentConfigUserResponse(BaseModel):
    """Slim shape returned to non-admin users — drops admin-only fields."""

    id: str
    name: str
    description: Optional[str] = None
    profile_image_url: Optional[str] = None
    is_beta: bool = True


####################
# Service
####################


RESOURCE_TYPE = 'agent_config'


class AgentConfigsTable:
    RESOURCE_TYPE = RESOURCE_TYPE

    def _to_model(self, row: AgentConfig, db: Session) -> AgentConfigModel:
        grants = AccessGrants.get_grants_by_resource(self.RESOURCE_TYPE, row.id, db=db)
        return AgentConfigModel(
            id=row.id,
            user_id=row.user_id,
            name=row.name,
            description=row.description,
            profile_image_url=row.profile_image_url,
            is_active=bool(row.is_active),
            is_beta=bool(row.is_beta),
            meta=row.meta or {},
            position=int(row.position or 0),
            access_grants=grants,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def insert_new_agent_config(
        self,
        user_id: str,
        slug: str,
        form_data: AgentConfigForm,
        db: Optional[Session] = None,
    ) -> Optional[AgentConfigModel]:
        with get_db_context(db) as db:
            now = int(time.time())
            current_max = db.query(func.max(AgentConfig.position)).scalar()
            next_position = (int(current_max) + 1) if current_max is not None else 0
            row = AgentConfig(
                id=slug,
                user_id=user_id,
                name=form_data.name,
                description=form_data.description,
                profile_image_url=form_data.profile_image_url,
                is_active=form_data.is_active,
                is_beta=form_data.is_beta,
                meta=form_data.meta or {},
                position=next_position,
                created_at=now,
                updated_at=now,
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            if form_data.access_grants is not None:
                AccessGrants.set_access_grants(self.RESOURCE_TYPE, slug, form_data.access_grants, db=db)
            return self._to_model(row, db)

    def update_agent_config(
        self,
        slug: str,
        form_data: AgentConfigForm,
        db: Optional[Session] = None,
    ) -> Optional[AgentConfigModel]:
        with get_db_context(db) as db:
            row = db.query(AgentConfig).filter_by(id=slug).first()
            if not row:
                return None
            row.name = form_data.name
            row.description = form_data.description
            row.profile_image_url = form_data.profile_image_url
            row.is_active = form_data.is_active
            row.is_beta = form_data.is_beta
            if form_data.meta is not None:
                row.meta = form_data.meta
            row.updated_at = int(time.time())
            db.commit()
            db.refresh(row)
            if form_data.access_grants is not None:
                AccessGrants.set_access_grants(self.RESOURCE_TYPE, slug, form_data.access_grants, db=db)
            return self._to_model(row, db)

    def delete_agent_config(self, slug: str, db: Optional[Session] = None) -> bool:
        with get_db_context(db) as db:
            row = db.query(AgentConfig).filter_by(id=slug).first()
            if not row:
                return False
            db.delete(row)
            AccessGrants.revoke_all_access(self.RESOURCE_TYPE, slug, db=db)
            db.commit()
            return True

    def get_agent_config_by_id(self, slug: str, db: Optional[Session] = None) -> Optional[AgentConfigModel]:
        with get_db_context(db) as db:
            row = db.query(AgentConfig).filter_by(id=slug).first()
            return self._to_model(row, db) if row else None

    def list_all(self, db: Optional[Session] = None) -> list[AgentConfigModel]:
        """Admin: every row, regardless of access."""
        with get_db_context(db) as db:
            rows = db.query(AgentConfig).order_by(AgentConfig.position.asc(), AgentConfig.name.asc()).all()
            return [self._to_model(r, db) for r in rows]

    def list_visible_to_user(
        self,
        user_id: str,
        user_group_ids: set[str],
        db: Optional[Session] = None,
    ) -> list[AgentConfigModel]:
        """User: rows the user has read access to AND is_active."""
        with get_db_context(db) as db:
            active_rows = (
                db.query(AgentConfig)
                .filter(AgentConfig.is_active.is_(True))
                .order_by(AgentConfig.position.asc(), AgentConfig.name.asc())
                .all()
            )
            if not active_rows:
                return []
            slug_ids = [r.id for r in active_rows]
            accessible_ids = AccessGrants.get_accessible_resource_ids(
                user_id=user_id,
                resource_type=self.RESOURCE_TYPE,
                resource_ids=slug_ids,
                permission='read',
                user_group_ids=user_group_ids,
                db=db,
            )
            return [self._to_model(r, db) for r in active_rows if r.id in accessible_ids]

    def set_positions(
        self,
        slugs: list[str],
        db: Optional[Session] = None,
    ) -> list[AgentConfigModel]:
        """Persist a new ordering for the given slugs.

        Each slug receives ``position = its index in slugs``. Rows whose
        slug is not in the input list are left unchanged — callers
        should pass the full set they want to reorder.

        :param slugs: Ordered list of agent_config ids. Must reference
            existing rows; unknown ids are ignored (no-op for that
            slug).
        :return: Refreshed admin list (every row, sorted by position).
        """
        if not slugs:
            with get_db_context(db) as db:
                return self.list_all(db=db)

        with get_db_context(db) as db:
            existing = {r.id: r for r in db.query(AgentConfig).filter(AgentConfig.id.in_(slugs)).all()}
            now = int(time.time())
            for index, slug in enumerate(slugs):
                row = existing.get(slug)
                if row is None:
                    continue
                row.position = index
                row.updated_at = now
            db.commit()
            return self.list_all(db=db)

    def seed_from_env(
        self,
        env_configs: list[dict],
        valid_slugs: list[str],
        overwrite: bool = False,
        db: Optional[Session] = None,
    ) -> tuple[int, int, int]:
        """Seed agent_config rows from env-supplied defaults.

        For each entry in ``env_configs`` whose ``slug`` is in
        ``valid_slugs``:
          - if no DB row exists, insert one
          - if a row exists and ``overwrite`` is true, update it
          - otherwise skip (admin edits in the panel win by default)

        Access grants are never seeded — they depend on per-deployment
        user/group ids that don't exist at deploy time.

        :param env_configs: Parsed AGENT_API_AGENTS_CONFIG list.
        :param valid_slugs: AGENT_API_AGENTS — entries with a slug not
            in this list are skipped (cannot be marked active otherwise).
        :return: ``(inserted, updated, skipped)`` for logging.
        """
        inserted = updated = skipped = 0
        valid = set(valid_slugs)
        with get_db_context(db) as db:
            now = int(time.time())
            for raw in env_configs:
                if not isinstance(raw, dict):
                    log.warning('AGENT_API_AGENTS_CONFIG entry is not an object, skipping: %r', raw)
                    skipped += 1
                    continue
                slug = (raw.get('slug') or '').strip()
                name = (raw.get('name') or '').strip()
                if not slug or not name:
                    log.warning("AGENT_API_AGENTS_CONFIG entry missing 'slug' or 'name', skipping: %r", raw)
                    skipped += 1
                    continue
                if slug not in valid:
                    log.warning('AGENT_API_AGENTS_CONFIG entry %r not in AGENT_API_AGENTS, skipping', slug)
                    skipped += 1
                    continue

                # Backwards-compat: ``cta_copy`` was merged into ``description``
                # in migration e8a9b0c1d2e3. Accept the old key for env-driven
                # seeds so deployments don't break before they update their
                # config — explicit ``description`` always wins.
                env_description = raw.get('description') or raw.get('cta_copy')

                row = db.query(AgentConfig).filter_by(id=slug).first()
                if row is None:
                    if 'position' in raw:
                        position = int(raw['position'])
                    else:
                        current_max = db.query(func.max(AgentConfig.position)).scalar()
                        position = (int(current_max) + 1) if current_max is not None else 0
                    row = AgentConfig(
                        id=slug,
                        user_id=None,
                        name=name,
                        description=env_description,
                        profile_image_url=raw.get('profile_image_url'),
                        is_active=bool(raw.get('is_active', False)),
                        is_beta=bool(raw.get('is_beta', True)),
                        meta={},
                        position=position,
                        created_at=now,
                        updated_at=now,
                    )
                    db.add(row)
                    inserted += 1
                elif overwrite:
                    row.name = name
                    row.description = env_description
                    row.profile_image_url = raw.get('profile_image_url')
                    row.is_active = bool(raw.get('is_active', row.is_active))
                    row.is_beta = bool(raw.get('is_beta', row.is_beta))
                    if 'position' in raw:
                        row.position = int(raw['position'])
                    row.updated_at = now
                    updated += 1
                else:
                    skipped += 1
            db.commit()
        return inserted, updated, skipped

    def user_has_access(
        self,
        user_id: str,
        slug: str,
        user_group_ids: Optional[set[str]] = None,
        db: Optional[Session] = None,
    ) -> bool:
        return AccessGrants.has_access(
            user_id=user_id,
            resource_type=self.RESOURCE_TYPE,
            resource_id=slug,
            permission='read',
            user_group_ids=user_group_ids,
            db=db,
        )


AgentConfigs = AgentConfigsTable()
