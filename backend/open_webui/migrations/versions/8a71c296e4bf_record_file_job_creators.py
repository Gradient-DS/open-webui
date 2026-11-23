"""Retain upload job authority independently of the removable File row."""

import sqlalchemy as sa
from alembic import op

revision = '8a71c296e4bf'
down_revision = '73c9a4d82e16'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'soev_file_job',
        sa.Column('file_id', sa.Text(), primary_key=True),
        sa.Column('creator_user_id', sa.Text(), nullable=False),
        sa.Column('job', sa.JSON(), nullable=False),
    )
    op.create_index('ix_soev_file_job_creator_user_id', 'soev_file_job', ['creator_user_id'])
    # Old jobs have no trustworthy creator evidence; never infer it from file ownership.


def downgrade() -> None:
    op.drop_index('ix_soev_file_job_creator_user_id', table_name='soev_file_job')
    op.drop_table('soev_file_job')
