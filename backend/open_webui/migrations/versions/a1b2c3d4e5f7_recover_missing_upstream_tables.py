"""recover missing upstream tables

Revision ID: a1b2c3d4e5f7
Revises: e5f6a7b8c9d0
Create Date: 2026-03-22

Recovery migration: idempotently creates tables and columns from the upstream
branch (374d2f66af06 through b2c3d4e5f6a7) that may not have been applied due
to a migration failure during the v0.8.9 merge.

This migration is safe to run on databases where everything already exists -
all operations are guarded with existence checks.
"""

import logging
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from open_webui.migrations.util import get_existing_tables

log = logging.getLogger(__name__)

revision: str = 'a1b2c3d4e5f7'
down_revision: Union[str, None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table_name: str, column_name: str) -> bool:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [c['name'] for c in inspector.get_columns(table_name)]
    return column_name in columns


def _index_exists(index_name: str) -> bool:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    for table_name in inspector.get_table_names():
        indexes = inspector.get_indexes(table_name)
        if any(idx['name'] == index_name for idx in indexes):
            return True
    return False


def _constraint_exists(table_name: str, constraint_name: str) -> bool:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    constraints = inspector.get_unique_constraints(table_name)
    return any(c['name'] == constraint_name for c in constraints)


def upgrade() -> None:
    existing_tables = set(get_existing_tables())
    recovered = []

    # ── 374d2f66af06: prompt table restructure + prompt_history ──────────

    # Check if prompt table has the new schema (id as PK) vs old schema (command as PK)
    if 'prompt' in existing_tables:
        if not _column_exists('prompt', 'id') or not _column_exists('prompt', 'name'):
            # Prompt table still has old schema - needs restructure.
            # Since there's no data to migrate on a broken new tenant, we can
            # drop and recreate with the new schema.
            log.warning('Recovering: prompt table has old schema, recreating')
            op.drop_table('prompt')
            existing_tables.discard('prompt')

    if 'prompt' not in existing_tables:
        log.warning('Recovering: creating prompt table with new schema')
        op.create_table(
            'prompt',
            sa.Column('id', sa.Text(), primary_key=True),
            sa.Column('command', sa.String(), unique=True, index=True),
            sa.Column('user_id', sa.String(), nullable=False),
            sa.Column('name', sa.Text(), nullable=False),
            sa.Column('content', sa.Text(), nullable=False),
            sa.Column('data', sa.JSON(), nullable=True),
            sa.Column('meta', sa.JSON(), nullable=True),
            sa.Column('access_control', sa.JSON(), nullable=True),
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default='1'),
            sa.Column('version_id', sa.Text(), nullable=True),
            sa.Column('tags', sa.JSON(), nullable=True),
            sa.Column('created_at', sa.BigInteger(), nullable=False),
            sa.Column('updated_at', sa.BigInteger(), nullable=False),
        )
        recovered.append('prompt')

    if 'prompt_history' not in existing_tables:
        log.warning('Recovering: creating prompt_history table')
        op.create_table(
            'prompt_history',
            sa.Column('id', sa.Text(), primary_key=True),
            sa.Column('prompt_id', sa.Text(), nullable=False, index=True),
            sa.Column('parent_id', sa.Text(), nullable=True),
            sa.Column('snapshot', sa.JSON(), nullable=False),
            sa.Column('user_id', sa.Text(), nullable=False),
            sa.Column('commit_message', sa.Text(), nullable=True),
            sa.Column('created_at', sa.BigInteger(), nullable=False),
        )
        recovered.append('prompt_history')

    # ── 8452d01d26d7: chat_message table ─────────────────────────────────

    if 'chat_message' not in existing_tables:
        log.warning('Recovering: creating chat_message table')
        op.create_table(
            'chat_message',
            sa.Column('id', sa.Text(), primary_key=True),
            sa.Column('chat_id', sa.Text(), nullable=False, index=True),
            sa.Column('user_id', sa.Text(), index=True),
            sa.Column('role', sa.Text(), nullable=False),
            sa.Column('parent_id', sa.Text(), nullable=True),
            sa.Column('content', sa.JSON(), nullable=True),
            sa.Column('output', sa.JSON(), nullable=True),
            sa.Column('model_id', sa.Text(), nullable=True, index=True),
            sa.Column('files', sa.JSON(), nullable=True),
            sa.Column('sources', sa.JSON(), nullable=True),
            sa.Column('embeds', sa.JSON(), nullable=True),
            sa.Column('done', sa.Boolean(), default=True),
            sa.Column('status_history', sa.JSON(), nullable=True),
            sa.Column('error', sa.JSON(), nullable=True),
            sa.Column('usage', sa.JSON(), nullable=True),
            sa.Column('created_at', sa.BigInteger(), index=True),
            sa.Column('updated_at', sa.BigInteger()),
            sa.ForeignKeyConstraint(['chat_id'], ['chat.id'], ondelete='CASCADE'),
        )
        if not _index_exists('chat_message_chat_parent_idx'):
            op.create_index(
                'chat_message_chat_parent_idx',
                'chat_message',
                ['chat_id', 'parent_id'],
            )
        if not _index_exists('chat_message_model_created_idx'):
            op.create_index(
                'chat_message_model_created_idx',
                'chat_message',
                ['model_id', 'created_at'],
            )
        if not _index_exists('chat_message_user_created_idx'):
            op.create_index(
                'chat_message_user_created_idx',
                'chat_message',
                ['user_id', 'created_at'],
            )
        recovered.append('chat_message')

    # ── f1e2d3c4b5a6: access_grant table ─────────────────────────────────

    if 'access_grant' not in existing_tables:
        log.warning('Recovering: creating access_grant table')
        op.create_table(
            'access_grant',
            sa.Column('id', sa.Text(), nullable=False, primary_key=True),
            sa.Column('resource_type', sa.Text(), nullable=False),
            sa.Column('resource_id', sa.Text(), nullable=False),
            sa.Column('principal_type', sa.Text(), nullable=False),
            sa.Column('principal_id', sa.Text(), nullable=False),
            sa.Column('permission', sa.Text(), nullable=False),
            sa.Column('created_at', sa.BigInteger(), nullable=False),
            sa.UniqueConstraint(
                'resource_type',
                'resource_id',
                'principal_type',
                'principal_id',
                'permission',
                name='uq_access_grant_grant',
            ),
        )
        if not _index_exists('idx_access_grant_resource'):
            op.create_index(
                'idx_access_grant_resource',
                'access_grant',
                ['resource_type', 'resource_id'],
            )
        if not _index_exists('idx_access_grant_principal'):
            op.create_index(
                'idx_access_grant_principal',
                'access_grant',
                ['principal_type', 'principal_id'],
            )
        recovered.append('access_grant')

    # ── a1b2c3d4e5f6: skill table ────────────────────────────────────────

    if 'skill' not in existing_tables:
        log.warning('Recovering: creating skill table')
        op.create_table(
            'skill',
            sa.Column('id', sa.String(), nullable=False, primary_key=True),
            sa.Column('user_id', sa.String(), nullable=False),
            sa.Column('name', sa.Text(), nullable=False, unique=True),
            sa.Column('description', sa.Text(), nullable=True),
            sa.Column('content', sa.Text(), nullable=False),
            sa.Column('meta', sa.JSON(), nullable=True),
            sa.Column('is_active', sa.Boolean(), nullable=False),
            sa.Column('updated_at', sa.BigInteger(), nullable=False),
            sa.Column('created_at', sa.BigInteger(), nullable=False),
        )
        if not _index_exists('idx_skill_user_id'):
            op.create_index('idx_skill_user_id', 'skill', ['user_id'])
        if not _index_exists('idx_skill_updated_at'):
            op.create_index('idx_skill_updated_at', 'skill', ['updated_at'])
        recovered.append('skill')

    # ── b2c3d4e5f6a7: scim column on user table ──────────────────────────

    if 'user' in existing_tables and not _column_exists('user', 'scim'):
        log.warning('Recovering: adding scim column to user table')
        op.add_column('user', sa.Column('scim', sa.JSON(), nullable=True))
        recovered.append('user.scim')

    # ── Summary ───────────────────────────────────────────────────────────

    if recovered:
        log.warning(f'Recovery migration created missing objects: {recovered}')
    else:
        log.info('Recovery migration: all objects already exist, nothing to do')


def downgrade() -> None:
    # This is a recovery migration - downgrade is intentionally a no-op.
    # The original migrations handle their own downgrades.
    pass
