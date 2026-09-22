"""[Gradient] Separate assistant identity from the inference model on messages."""

import sqlalchemy as sa
from alembic import op

revision = 'e24b7c9d1f63'
down_revision = 'a93f8c12b670'
branch_labels = None
depends_on = None

_INDEX = 'ix_chat_message_assistant_id'


def upgrade() -> None:
    """Add assistant identity and move legacy assistant model references."""
    conn = op.get_bind()
    tables = set(sa.inspect(conn).get_table_names())
    if 'chat_message' not in tables:
        return
    if 'assistant_id' not in {c['name'] for c in sa.inspect(conn).get_columns('chat_message')}:
        op.add_column('chat_message', sa.Column('assistant_id', sa.Text(), nullable=True))
    if _INDEX not in {i['name'] for i in sa.inspect(conn).get_indexes('chat_message')}:
        op.create_index(_INDEX, 'chat_message', ['assistant_id'])

    if 'model' not in tables:
        return
    assistants = conn.execute(sa.text('SELECT id, base_model_id FROM model WHERE base_model_id IS NOT NULL')).all()
    for assistant_id, llm_id in assistants:
        conn.execute(
            sa.text('UPDATE chat_message SET assistant_id = :a, model_id = :llm WHERE model_id = :a'),
            {'a': assistant_id, 'llm': llm_id},
        )

    if 'config' in tables:
        config = sa.table('config', sa.column('key', sa.Text()), sa.column('value', sa.JSON()))
        value = conn.execute(sa.select(config.c.value).where(config.c.key == 'ui.default_models')).scalar()
        assistant_ids = {a for a, _ in assistants}
        # Older per-key config stored lists; retain the stored representation.
        if isinstance(value, str):
            cleaned = ','.join(item for item in value.split(',') if item.strip() not in assistant_ids)
        elif isinstance(value, list):
            cleaned = [item for item in value if str(item).strip() not in assistant_ids]
        else:
            return
        if cleaned != value:
            conn.execute(config.update().where(config.c.key == 'ui.default_models').values(value=cleaned))


def downgrade() -> None:
    """Restore legacy message model identities before dropping the column."""
    conn = op.get_bind()
    if 'chat_message' not in sa.inspect(conn).get_table_names():
        return
    if 'assistant_id' not in {c['name'] for c in sa.inspect(conn).get_columns('chat_message')}:
        return
    conn.execute(sa.text('UPDATE chat_message SET model_id = assistant_id WHERE assistant_id IS NOT NULL'))
    if _INDEX in {i['name'] for i in sa.inspect(conn).get_indexes('chat_message')}:
        op.drop_index(_INDEX, table_name='chat_message')
    op.drop_column('chat_message', 'assistant_id')
    # Config defaults are intentionally not restored: removed choices may be stale.
