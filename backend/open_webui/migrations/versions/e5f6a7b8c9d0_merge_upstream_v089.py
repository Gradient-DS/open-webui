"""merge upstream v0.8.9 with custom migrations

Revision ID: e5f6a7b8c9d0
Revises: b2c3d4e5f6a7, d4e5f6a7b8c9
Create Date: 2026-03-20

"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, Sequence[str]] = ("b2c3d4e5f6a7", "d4e5f6a7b8c9")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass  # Both branches already applied their changes


def downgrade() -> None:
    pass
