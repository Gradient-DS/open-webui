"""add totp 2fa

Revision ID: c3d4e5f6a7b8
Revises: a1b2c3d4e5f7
Create Date: 2026-03-30

Adds TOTP two-factor authentication support:
- totp_secret, totp_enabled, totp_last_used_at columns on auth table
- recovery_code table for backup codes
"""

from alembic import op
import sqlalchemy as sa
from open_webui.migrations.util import get_existing_tables

revision = 'c3d4e5f6a7b8'
down_revision = 'a1b2c3d4e5f7'
branch_labels = None
depends_on = None


def _column_exists(table_name: str, column_name: str) -> bool:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [c['name'] for c in inspector.get_columns(table_name)]
    return column_name in columns


def upgrade():
    existing_tables = set(get_existing_tables())

    # Add TOTP columns to auth table
    if 'auth' in existing_tables:
        if not _column_exists('auth', 'totp_secret'):
            op.add_column('auth', sa.Column('totp_secret', sa.Text(), nullable=True))
        if not _column_exists('auth', 'totp_enabled'):
            op.add_column('auth', sa.Column('totp_enabled', sa.Boolean(), server_default='0', nullable=False))
        if not _column_exists('auth', 'totp_last_used_at'):
            op.add_column('auth', sa.Column('totp_last_used_at', sa.BigInteger(), nullable=True))

    # Create recovery_code table
    if 'recovery_code' not in existing_tables:
        op.create_table(
            'recovery_code',
            sa.Column('id', sa.Text(), nullable=False, primary_key=True),
            sa.Column('user_id', sa.Text(), sa.ForeignKey('auth.id', ondelete='CASCADE'), nullable=False),
            sa.Column('code_hash', sa.Text(), nullable=False),
            sa.Column('used', sa.Boolean(), server_default='0', nullable=False),
            sa.Column('used_at', sa.BigInteger(), nullable=True),
            sa.Column('created_at', sa.BigInteger(), nullable=False),
        )
        op.create_index('idx_recovery_code_user_id', 'recovery_code', ['user_id'])


def downgrade():
    op.drop_index('idx_recovery_code_user_id', table_name='recovery_code')
    op.drop_table('recovery_code')
    op.drop_column('auth', 'totp_last_used_at')
    op.drop_column('auth', 'totp_enabled')
    op.drop_column('auth', 'totp_secret')
