"""[Gradient] Merge upstream v0.12.3 heads.

Joins the fork lineage c3d8e2f17a90 (add folder_share_grant table)
and upstream lineage 0bd252b206fb (add missing auth rows; MFA/session_stamp,
group hierarchy, apps and history tables before it).
Both branches already applied their changes; this merge node makes no schema or data changes.
"""

from typing import Sequence, Union

# Revision identifiers, used by Alembic.
revision: str = 'feda1fb923f0'
down_revision: Union[str, Sequence[str]] = ('c3d8e2f17a90', '0bd252b206fb')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
