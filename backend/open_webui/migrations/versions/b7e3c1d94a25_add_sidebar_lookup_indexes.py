"""[Gradient] add the owner and collection lookup indexes the sidebar lists scan without

Revision ID: b7e3c1d94a25
Revises: e24b7c9d1f63
Create Date: 2026-10-12 14:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b7e3c1d94a25'
down_revision: str | None = 'ee54ed095346'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The exact expression soev/knowledge_store.py filters on, so Postgres can match it.
COLLECTION_KEY = "(CAST(meta ->> 'soev_collection_key' AS VARCHAR))"


def upgrade() -> None:
    # folder's primary key is (id, user_id): a user's folders and a folder's children scanned every row.
    op.create_index('folder_user_id_parent_id_idx', 'folder', ['user_id', 'parent_id'])
    op.create_index('note_user_id_updated_at_idx', 'note', ['user_id', 'updated_at'])
    op.create_index('file_user_id_idx', 'file', ['user_id'])
    if op.get_bind().dialect.name == 'postgresql':
        # Knowledge search looks up files whose ingest has not landed on every request.
        op.create_index(
            'file_soev_collection_key_idx',
            'file',
            [sa.text(COLLECTION_KEY)],
            postgresql_where=sa.text(f'{COLLECTION_KEY} IS NOT NULL'),
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == 'postgresql':
        op.drop_index('file_soev_collection_key_idx', table_name='file')
    op.drop_index('file_user_id_idx', table_name='file')
    op.drop_index('note_user_id_updated_at_idx', table_name='note')
    op.drop_index('folder_user_id_parent_id_idx', table_name='folder')
