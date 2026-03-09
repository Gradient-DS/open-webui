"""create invite table

Revision ID: eaa33ce2752e
Revises: 2c5f92a9fd66
Create Date: 2026-02-16
"""

from alembic import op
import sqlalchemy as sa

revision = "eaa33ce2752e"
down_revision = "2c5f92a9fd66"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "invite",
        sa.Column("id", sa.String(), nullable=False, primary_key=True),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("token", sa.String(), nullable=False, unique=True),
        sa.Column("role", sa.String(), server_default="user"),
        sa.Column("invited_by", sa.String(), nullable=False),
        sa.Column("expires_at", sa.BigInteger(), nullable=False),
        sa.Column("accepted_at", sa.BigInteger(), nullable=True),
        sa.Column("revoked_at", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
    )
    op.create_index("ix_invite_email", "invite", ["email"])
    op.create_index("ix_invite_token", "invite", ["token"], unique=True)


def downgrade():
    op.drop_index("ix_invite_token", "invite")
    op.drop_index("ix_invite_email", "invite")
    op.drop_table("invite")
