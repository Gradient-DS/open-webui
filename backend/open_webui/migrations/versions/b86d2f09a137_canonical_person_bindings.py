"""Canonicalize person bindings and refuse ambiguous identities."""

import logging
from uuid import UUID

import sqlalchemy as sa
from alembic import op
from open_webui.soev.principal_refs import PERSON_REF_CHECK

revision = 'b86d2f09a137'
down_revision = 'da27c90f4b61'
branch_labels = None
depends_on = None

log = logging.getLogger(__name__)


def upgrade() -> None:
    connection = op.get_bind()
    users = sa.table('user', sa.column('id', sa.String()), sa.column('person_id', sa.String()))
    people: dict[str, list[str]] = {}
    for user_id, person_id in connection.execute(sa.select(users.c.id, users.c.person_id)):
        if person_id is None:
            continue
        try:
            canonical = f'platform:user:{UUID(person_id.removeprefix("platform:user:"))}'
        except (ValueError, AttributeError):
            log.warning('invalid person binding cleared', extra={'user_id': user_id})
            continue
        people.setdefault(canonical, []).append(user_id)
    # Clear first so alternate spellings cannot collide during normalization.
    connection.execute(users.update().values(person_id=None))
    for person_id, user_ids in people.items():
        if len(user_ids) != 1:
            log.warning('ambiguous person binding cleared', extra={'user_ids': sorted(user_ids)})
            continue
        connection.execute(users.update().where(users.c.id == user_ids[0]).values(person_id=person_id))
    _alter_constraint(create=True)


def downgrade() -> None:
    _alter_constraint(create=False)


def _alter_constraint(*, create: bool) -> None:
    connection = op.get_bind()
    indexes = []
    if connection.dialect.name == 'sqlite':
        # Alembic's SQLite table rebuild cannot reflect expression indexes.
        indexes = connection.execute(
            sa.text("SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name='user' AND sql IS NOT NULL")
        ).all()
    with op.batch_alter_table('user') as batch:
        if create:
            batch.create_check_constraint('user_person_ref_canonical', PERSON_REF_CHECK)
        else:
            batch.drop_constraint('user_person_ref_canonical', type_='check')
    if indexes:
        remaining = set(
            connection.execute(
                sa.text("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='user'")
            ).scalars()
        )
        for name, sql in indexes:
            if name not in remaining:
                connection.exec_driver_sql(sql)
