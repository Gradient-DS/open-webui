"""seed soev_chat_autonomous + soev_chat_manual agent_config rows

Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0
Create Date: 2026-04-29

[Gradient] Aligns the persisted ``agent_config`` rows with the new pair
of ``ChatAgent`` siblings declared in the genai-utils deployment YAML
(``soev_chat_autonomous`` + ``soev_chat_manual``). Existing tenants
have a single row keyed by the legacy slug ``soev_chat`` (or, on much
older installs, ``soev_agent`` / ``default``) — that row is renamed
to ``soev_chat_autonomous`` so existing chat history and access
grants keep resolving. ``soev_chat_manual`` is inserted as a brand
new row, ``is_active=false`` by default so admins can opt into the
manual-tools UX explicitly.

The migration is data-only — the ``agent_config`` schema itself is
unchanged. All updates are guarded so it is idempotent on fresh
installs (where no legacy row exists) and on re-runs.
"""

import time
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from open_webui.migrations.util import get_existing_tables

revision: str = 'd6e7f8a9b0c1'
down_revision: Union[str, None] = 'c5d6e7f8a9b0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_AUTONOMOUS_ID = 'soev_chat_autonomous'
_MANUAL_ID = 'soev_chat_manual'
_LEGACY_IDS = ('soev_chat', 'soev_agent', 'default')

_AUTONOMOUS_NAME = 'Soev.ai — autonomous'
_AUTONOMOUS_DESC = 'Autonomous knowledge assistant — web search and KB tools always on.'
_MANUAL_NAME = 'Soev.ai — manual tools'
_MANUAL_DESC = 'Manual-tools assistant — tools register only when you select them or attach a file/KB.'


def _row_exists(conn, slug: str) -> bool:
    result = conn.execute(
        sa.text('SELECT 1 FROM agent_config WHERE id = :id'),
        {'id': slug},
    ).first()
    return result is not None


def upgrade() -> None:
    if 'agent_config' not in set(get_existing_tables()):
        return

    conn = op.get_bind()
    now = int(time.time())

    if not _row_exists(conn, _AUTONOMOUS_ID):
        legacy_id = next(
            (slug for slug in _LEGACY_IDS if _row_exists(conn, slug)),
            None,
        )
        if legacy_id is not None:
            conn.execute(
                sa.text('UPDATE agent_config SET id = :new_id, updated_at = :ts WHERE id = :old_id'),
                {'new_id': _AUTONOMOUS_ID, 'old_id': legacy_id, 'ts': now},
            )
            conn.execute(
                sa.text(
                    'UPDATE access_grant SET resource_id = :new_id '
                    "WHERE resource_type = 'agent_config' AND resource_id = :old_id"
                ),
                {'new_id': _AUTONOMOUS_ID, 'old_id': legacy_id},
            )
        else:
            conn.execute(
                sa.text(
                    'INSERT INTO agent_config '
                    '(id, user_id, name, description, profile_image_url, '
                    ' cta_copy, is_active, is_beta, meta, created_at, updated_at) '
                    'VALUES (:id, NULL, :name, :description, NULL, NULL, '
                    "        false, true, '{}', :ts, :ts)"
                ),
                {
                    'id': _AUTONOMOUS_ID,
                    'name': _AUTONOMOUS_NAME,
                    'description': _AUTONOMOUS_DESC,
                    'ts': now,
                },
            )

    if not _row_exists(conn, _MANUAL_ID):
        conn.execute(
            sa.text(
                'INSERT INTO agent_config '
                '(id, user_id, name, description, profile_image_url, '
                ' cta_copy, is_active, is_beta, meta, created_at, updated_at) '
                'VALUES (:id, NULL, :name, :description, NULL, NULL, '
                "        false, true, '{}', :ts, :ts)"
            ),
            {
                'id': _MANUAL_ID,
                'name': _MANUAL_NAME,
                'description': _MANUAL_DESC,
                'ts': now,
            },
        )


def downgrade() -> None:
    if 'agent_config' not in set(get_existing_tables()):
        return

    conn = op.get_bind()

    conn.execute(
        sa.text("DELETE FROM access_grant WHERE resource_type = 'agent_config' AND resource_id = :id"),
        {'id': _MANUAL_ID},
    )
    conn.execute(
        sa.text('DELETE FROM agent_config WHERE id = :id'),
        {'id': _MANUAL_ID},
    )

    conn.execute(
        sa.text('UPDATE agent_config SET id = :old_id WHERE id = :new_id'),
        {'old_id': 'soev_chat', 'new_id': _AUTONOMOUS_ID},
    )
    conn.execute(
        sa.text(
            'UPDATE access_grant SET resource_id = :old_id '
            "WHERE resource_type = 'agent_config' AND resource_id = :new_id"
        ),
        {'old_id': 'soev_chat', 'new_id': _AUTONOMOUS_ID},
    )
