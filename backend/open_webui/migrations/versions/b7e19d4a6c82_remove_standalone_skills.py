"""[Gradient] Delete standalone skills; assistants keep their own folders."""

import json
import logging

import sqlalchemy as sa
from alembic import op

revision = 'b7e19d4a6c82'
down_revision = 'a8d42e7c913b'
branch_labels = None
depends_on = None

log = logging.getLogger(__name__)


def _referenced_ids(value, candidates: set[str]) -> set[str]:
    if isinstance(value, str):
        # Includes file URLs in message content as well as structured file ids.
        return {id for id in candidates if id in value}
    if isinstance(value, dict):
        return set().union(*(_referenced_ids(item, candidates) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(_referenced_ids(item, candidates) for item in value))
    return set()


def _exclusive_files(conn, schema, candidates: set[str]) -> set[str]:
    # A standalone upload may also be attached to a chat, KB, folder, note or assistant.
    # Keep those File rows, including historical JSON-only attachments.
    referenced: set[str] = set()
    for table in schema.tables.values():
        if table.name in ('skill', 'skill_file', 'file') or not candidates:
            continue
        if 'file_id' in table.c:
            referenced.update(conn.execute(sa.select(table.c.file_id)).scalars())
        columns = [
            col for col in table.c if col.name in ('meta', 'data', 'chat', 'files', 'items', 'content', 'params')
        ]
        if columns:
            for row in conn.execute(sa.select(*columns)):
                for value in row:
                    referenced.update(_referenced_ids(value, candidates))
    return candidates - referenced


def _delete_files(conn, schema, candidates: set[str]) -> None:
    if 'file' in schema.tables:
        files = schema.tables['file']
        stored = 0
        for id in sorted(candidates):
            if 'path' in files.c:
                stored += bool(conn.execute(sa.select(files.c.path).where(files.c.id == id)).scalar())
            if 'access_grant' in schema.tables:
                grants = schema.tables['access_grant']
                conn.execute(grants.delete().where(grants.c.resource_type == 'file', grants.c.resource_id == id))
            conn.execute(files.delete().where(files.c.id == id))
        if stored:
            # Storage operations are nontransactional and need deployment credentials. Never
            # import the runtime storage provider from Alembic; leave these bytes orphaned.
            log.warning(
                'Removed standalone skill File rows; %d stored objects remain in configured upload storage', stored
            )


def _clean_model_meta(conn, schema) -> None:
    if 'model' in schema.tables:
        models = schema.tables['model']
        for id, value in conn.execute(sa.select(models.c.id, models.c.meta)):
            meta = json.loads(value) if isinstance(value, str) else value
            if isinstance(meta, dict) and 'skillIds' in meta:
                meta = {key: item for key, item in meta.items() if key != 'skillIds'}
                conn.execute(
                    models.update()
                    .where(models.c.id == id)
                    .values(meta=json.dumps(meta, ensure_ascii=False) if isinstance(value, str) else meta)
                )


def upgrade() -> None:
    conn = op.get_bind()
    schema = sa.MetaData()
    schema.reflect(bind=conn)
    candidates: set[str] = set()
    if 'skill_file' in schema.tables:
        candidates.update(conn.execute(sa.select(schema.tables['skill_file'].c.file_id)).scalars())

    candidates = _exclusive_files(conn, schema, candidates)
    if 'skill_file' in schema.tables:
        op.drop_table('skill_file')
    if 'skill' in schema.tables:
        conn.execute(schema.tables['skill'].delete())
    if 'access_grant' in schema.tables:
        grants = schema.tables['access_grant']
        conn.execute(grants.delete().where(grants.c.resource_type == 'skill'))
    _delete_files(conn, schema, candidates)
    _clean_model_meta(conn, schema)


def downgrade() -> None:
    # Recreate structure only. Deleted skills, files, grants and meta.skillIds cannot be restored.
    if 'skill_file' in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        'skill_file',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('skill_id', sa.String(), sa.ForeignKey('skill.id', ondelete='CASCADE'), nullable=False),
        sa.Column('file_id', sa.String(), sa.ForeignKey('file.id', ondelete='CASCADE'), nullable=False),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        sa.Column('updated_at', sa.BigInteger(), nullable=False),
        sa.Column('path', sa.String(), nullable=False),
        sa.Index('ix_skill_file_skill_id', 'skill_id'),
        sa.Index('ix_skill_file_file_id', 'file_id'),
        sa.Index('ix_skill_file_user_id', 'user_id'),
        sa.UniqueConstraint('skill_id', 'path', name='uq_skill_file_skill_path'),
    )
