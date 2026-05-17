"""drop cta_copy column from agent_config, merging into description

Revision ID: e8a9b0c1d2e3
Revises: e7f8a9b0c1d2
Create Date: 2026-05-17

[Gradient] Collapses ``description`` and ``cta_copy`` into a single
``description`` field. The two fields confused admins: the home card
silently fell back to ``description`` when ``cta_copy`` was empty, so
editing the CTA field appeared to "overwrite" the description on the
card even though the column was untouched.

Backfill rule: where ``description`` is empty/null but ``cta_copy``
has content, copy ``cta_copy`` into ``description`` so no displayed
text is lost. When both are set, keep ``description`` — that was the
admin's original explicit value before the CTA field existed.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from open_webui.migrations.util import get_existing_tables

revision: str = 'e8a9b0c1d2e3'
down_revision: Union[str, None] = 'e7f8a9b0c1d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table_name: str, column_name: str) -> bool:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    return column_name in {c['name'] for c in inspector.get_columns(table_name)}


def upgrade() -> None:
    if 'agent_config' not in set(get_existing_tables()):
        return
    if not _column_exists('agent_config', 'cta_copy'):
        return

    conn = op.get_bind()
    conn.execute(
        sa.text(
            'UPDATE agent_config '
            'SET description = cta_copy '
            "WHERE (description IS NULL OR description = '') "
            "AND cta_copy IS NOT NULL AND cta_copy != ''"
        )
    )

    with op.batch_alter_table('agent_config') as batch_op:
        batch_op.drop_column('cta_copy')


def downgrade() -> None:
    if 'agent_config' not in set(get_existing_tables()):
        return
    if _column_exists('agent_config', 'cta_copy'):
        return

    op.add_column(
        'agent_config',
        sa.Column('cta_copy', sa.Text(), nullable=True),
    )
