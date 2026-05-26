"""merge upstream v0.9.5 with custom migrations

Revision ID: 0579da7f672f
Revises: f9a0b1c2d3e4, a0b1c2d3e4f5
Create Date: 2026-05-25

"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = '0579da7f672f'
down_revision: Union[str, Sequence[str]] = ('f9a0b1c2d3e4', 'a0b1c2d3e4f5')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass  # Both branches already applied their changes


def downgrade() -> None:
    pass
