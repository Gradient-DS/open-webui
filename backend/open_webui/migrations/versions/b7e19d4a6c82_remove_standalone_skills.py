"""[Gradient] Delete standalone skills; assistants keep their own folders."""

import json

import sqlalchemy as sa
from alembic import op

revision = 'b7e19d4a6c82'
down_revision = 'a8d42e7c913b'
branch_labels = None
depends_on = None


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
    # The File rows and stored objects behind skill files stay: they are their uploader's files,
    # and may be attached elsewhere.
    if 'skill_file' in schema.tables:
        op.drop_table('skill_file')
    if 'skill' in schema.tables:
        conn.execute(schema.tables['skill'].delete())
    if 'access_grant' in schema.tables:
        grants = schema.tables['access_grant']
        conn.execute(grants.delete().where(grants.c.resource_type == 'skill'))
    _clean_model_meta(conn, schema)


def downgrade() -> None:
    # Recreate structure only. Deleted skills, grants and meta.skillIds cannot be restored.
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
