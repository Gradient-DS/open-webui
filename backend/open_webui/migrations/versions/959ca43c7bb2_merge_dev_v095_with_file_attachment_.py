"""merge dev v095 with file_attachment branch

Revision ID: 959ca43c7bb2
Revises: 0579da7f672f, b3c4d5e6f7a8
Create Date: 2026-05-26 16:21:53.842553

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import open_webui.internal.db


# revision identifiers, used by Alembic.
revision: str = '959ca43c7bb2'
down_revision: Union[str, None] = ('0579da7f672f', 'b3c4d5e6f7a8')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
