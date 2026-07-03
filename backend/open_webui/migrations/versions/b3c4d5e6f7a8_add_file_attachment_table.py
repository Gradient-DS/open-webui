"""add file_attachment table

Revision ID: b3c4d5e6f7a8
Revises: f9a0b1c2d3e4
Create Date: 2026-05-25
"""

from alembic import op
import sqlalchemy as sa


revision = 'b3c4d5e6f7a8'
down_revision = 'f9a0b1c2d3e4'
branch_labels = None
depends_on = None


def _table_exists(table_name: str) -> bool:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    return table_name in inspector.get_table_names()


def upgrade():
    if _table_exists('file_attachment'):
        return
    op.create_table(
        'file_attachment',
        sa.Column('id', sa.String(), nullable=False, primary_key=True),
        sa.Column('file_id', sa.String(), nullable=False),
        sa.Column('kind', sa.String(), nullable=False),
        sa.Column('storey', sa.String(), nullable=True),
        sa.Column('index', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('content_type', sa.String(), nullable=False, server_default='image/png'),
        sa.Column('caption', sa.Text(), nullable=False, server_default=''),
        sa.Column('path', sa.Text(), nullable=False),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
    )
    op.create_index('ix_file_attachment_file_id', 'file_attachment', ['file_id'])


def downgrade():
    op.drop_index('ix_file_attachment_file_id', 'file_attachment')
    op.drop_table('file_attachment')
