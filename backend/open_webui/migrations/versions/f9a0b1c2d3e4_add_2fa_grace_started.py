"""add 2fa grace period anchor

Revision ID: f9a0b1c2d3e4
Revises: e8a9b0c1d2e3
Create Date: 2026-05-21

Adds the per-user anchor timestamp for the 2FA enrollment grace period.

Without a stored anchor the grace period had no reference point: the
"days remaining" counter was a static config value and the "Set up later"
button was always available, so REQUIRE_2FA could be deferred indefinitely
(every app restart appeared to reset the counter). ``twofa_grace_started_at``
records when the grace window first began for a user, so a real deadline can
be computed and enforced.
"""

from alembic import op
import sqlalchemy as sa
from open_webui.migrations.util import get_existing_tables

revision = 'f9a0b1c2d3e4'
down_revision = 'e8a9b0c1d2e3'
branch_labels = None
depends_on = None


def _column_exists(table_name: str, column_name: str) -> bool:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [c['name'] for c in inspector.get_columns(table_name)]
    return column_name in columns


def upgrade():
    existing_tables = set(get_existing_tables())

    # Add the 2FA grace-period anchor to the auth table.
    if 'auth' in existing_tables:
        if not _column_exists('auth', 'twofa_grace_started_at'):
            op.add_column('auth', sa.Column('twofa_grace_started_at', sa.BigInteger(), nullable=True))


def downgrade():
    op.drop_column('auth', 'twofa_grace_started_at')
