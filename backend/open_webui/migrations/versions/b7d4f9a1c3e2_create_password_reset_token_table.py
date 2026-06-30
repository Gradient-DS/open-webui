"""create password reset token table

Revision ID: b7d4f9a1c3e2
Revises: a1c2e3f4d5b6
Create Date: 2026-06-30
"""

from alembic import op
import sqlalchemy as sa

revision = 'b7d4f9a1c3e2'
down_revision = 'a1c2e3f4d5b6'
branch_labels = None
depends_on = None


def _table_exists(table_name: str) -> bool:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    return table_name in inspector.get_table_names()


def upgrade():
    if _table_exists('password_reset_token'):
        return
    op.create_table(
        'password_reset_token',
        sa.Column('id', sa.String(), nullable=False, primary_key=True),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('token_hash', sa.String(), nullable=False, unique=True),
        sa.Column('expires_at', sa.BigInteger(), nullable=False),
        sa.Column('used_at', sa.BigInteger(), nullable=True),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
    )
    op.create_index('ix_password_reset_token_user_id', 'password_reset_token', ['user_id'])
    op.create_index('ix_password_reset_token_token_hash', 'password_reset_token', ['token_hash'], unique=True)


def downgrade():
    op.drop_index('ix_password_reset_token_token_hash', 'password_reset_token')
    op.drop_index('ix_password_reset_token_user_id', 'password_reset_token')
    op.drop_table('password_reset_token')
