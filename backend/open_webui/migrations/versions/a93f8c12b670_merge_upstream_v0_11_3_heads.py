"""[Gradient] Merge upstream v0.11.3 heads.

Joins the fork lineage e2c7a94b1f38 (materialize knowledge directories)
and upstream lineage d4c1a8e37b62 (add chat timer_at and chat indexes).
Both branches already applied their changes, including upstream OAuth repair;
this merge node makes no schema or data changes.
"""

from typing import Sequence, Union

# Revision identifiers, used by Alembic.
revision: str = 'a93f8c12b670'
down_revision: Union[str, Sequence[str]] = ('e2c7a94b1f38', 'd4c1a8e37b62')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
