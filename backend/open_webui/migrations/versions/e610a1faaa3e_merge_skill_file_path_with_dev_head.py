"""merge skill_file path branch with dev head

Reconciles the two Alembic heads that arise when feat/skills (skill_file.path
bundles, c2179339233e) is merged into dev (head d651b063d0d8). The skill
migration was cut from an older dev tip, so it sits on a sibling branch; this
no-op merge collapses both into a single head. No schema changes.

Revision ID: e610a1faaa3e
Revises: c2179339233e, d651b063d0d8
Create Date: 2026-06-25 08:10:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import open_webui.internal.db


# revision identifiers, used by Alembic.
revision: str = 'e610a1faaa3e'
down_revision: Union[str, None] = ('c2179339233e', 'd651b063d0d8')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
