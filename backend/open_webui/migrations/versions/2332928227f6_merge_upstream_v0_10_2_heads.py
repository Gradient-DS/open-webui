"""merge upstream v0.10.2 heads

Reconciles the two alembic heads left after merging upstream v0.10.2:

  * ours   b7d4f9a1c3e2 — create password reset token table
  * theirs 42e2978c7933 — add memory path and meta (descends from the
                          config-reshape migration 3ff2c63645b8)

Both branches already applied their own schema changes, so this is a
no-op merge revision that simply joins the two lineages back into a
single head. No data transformation happens here: the config blob →
per-key reshape lives in 3ff2c63645b8 on theirs' side and runs before
this merge point on any DB coming up ours' pre-merge lineage.

Revision ID: 2332928227f6
Revises: b7d4f9a1c3e2, 42e2978c7933
Create Date: 2026-07-03 18:53:55.956090

"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = '2332928227f6'
down_revision: Union[str, Sequence[str]] = ('b7d4f9a1c3e2', '42e2978c7933')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass  # Both branches already applied their changes


def downgrade() -> None:
    pass
