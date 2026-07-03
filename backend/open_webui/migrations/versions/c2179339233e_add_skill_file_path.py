"""Add skill_file.path (path-based skill bundles)

Adds a virtual ``path`` column to the ``skill_file`` join, backfills it from the
backing ``file.filename``, then swaps the unique constraint from
``(skill_id, file_id)`` to ``(skill_id, path)`` so a bundle is addressed as a
path tree (and the same File may appear at multiple paths).

Cross-dialect: tests run on SQLite, prod is Postgres. SQLite cannot
``ALTER TABLE ... DROP CONSTRAINT`` / alter a column in place, so all schema
edits go through ``op.batch_alter_table(...)`` (which recreates the table on
SQLite and emits plain ALTERs on Postgres). The backfill UPDATE uses a
correlated subquery, which both dialects support.

Revision ID: c2179339233e
Revises: c9d0e1f2a3b4
Create Date: 2026-06-22 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from open_webui.migrations.util import get_existing_tables
from sqlalchemy.engine.reflection import Inspector

# revision identifiers, used by Alembic.
revision: str = 'c2179339233e'
down_revision: str | None = 'c9d0e1f2a3b4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_UNIQUE = 'uq_skill_file_skill_file'
_NEW_UNIQUE = 'uq_skill_file_skill_path'


def upgrade() -> None:
    if 'skill_file' not in set(get_existing_tables()):
        # Nothing to migrate — the table-creation migration was skipped (e.g.
        # the join feature was never enabled on this deployment).
        return

    # 1. Add the column as NULLABLE so existing rows can be backfilled.
    with op.batch_alter_table('skill_file', schema=None) as batch_op:
        batch_op.add_column(sa.Column('path', sa.String(), nullable=True))

    # 2. Backfill path = file.filename for existing rows (correlated subquery —
    #    works on SQLite and Postgres). Rows whose backing file is missing get a
    #    deterministic fallback so the subsequent NOT NULL never fails.
    op.execute(
        """
        UPDATE skill_file
        SET path = (
            SELECT file.filename
            FROM file
            WHERE file.id = skill_file.file_id
        )
        WHERE path IS NULL
        """
    )
    op.execute(
        """
        UPDATE skill_file
        SET path = 'file_' || file_id
        WHERE path IS NULL OR path = ''
        """
    )

    # 3. NOT NULL + swap the unique constraint. Reflect the old constraint name
    #    defensively (SQLite naming can vary) and only drop it if present.
    conn = op.get_bind()
    inspector = Inspector.from_engine(conn)
    existing_unique_names = {c['name'] for c in inspector.get_unique_constraints('skill_file')}

    with op.batch_alter_table('skill_file', schema=None) as batch_op:
        batch_op.alter_column('path', existing_type=sa.String(), nullable=False)
        if _OLD_UNIQUE in existing_unique_names:
            batch_op.drop_constraint(_OLD_UNIQUE, type_='unique')
        batch_op.create_unique_constraint(_NEW_UNIQUE, ['skill_id', 'path'])


def downgrade() -> None:
    if 'skill_file' not in set(get_existing_tables()):
        return

    conn = op.get_bind()
    inspector = Inspector.from_engine(conn)
    existing_unique_names = {c['name'] for c in inspector.get_unique_constraints('skill_file')}

    with op.batch_alter_table('skill_file', schema=None) as batch_op:
        if _NEW_UNIQUE in existing_unique_names:
            batch_op.drop_constraint(_NEW_UNIQUE, type_='unique')
        batch_op.create_unique_constraint(_OLD_UNIQUE, ['skill_id', 'file_id'])
        batch_op.drop_column('path')
