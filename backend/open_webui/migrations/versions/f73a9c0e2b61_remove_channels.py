"""Remove unused channels and their grants; refuse unexpected channel content."""

import sqlalchemy as sa
from alembic import op

revision = 'f73a9c0e2b61'
down_revision = 'c4d8e2f671a9'
branch_labels = None
depends_on = None

TABLES = ('channel_file', 'channel_webhook', 'channel_member', 'message_reaction', 'message', 'channel')


def _has_feature_permissions(permissions: object) -> bool:
    return isinstance(permissions, dict) and isinstance(permissions.get('features'), dict)


def _remove_channel_settings(connection: sa.Connection, tables: set[str]) -> None:
    if 'config' in tables:
        connection.execute(
            sa.text("DELETE FROM config WHERE key IN ('channels.enable', 'channels.model_response_mode')")
        )
        config = sa.table('config', sa.column('key'), sa.column('value', sa.JSON))
        permissions = connection.execute(sa.select(config.c.value).where(config.c.key == 'user.permissions')).scalar()
        if _has_feature_permissions(permissions):
            permissions['features'].pop('channels', None)
            connection.execute(config.update().where(config.c.key == 'user.permissions').values(value=permissions))
    if 'group' in tables:
        groups = sa.table('group', sa.column('id'), sa.column('permissions', sa.JSON))
        for id, permissions in connection.execute(sa.select(groups.c.id, groups.c.permissions)):
            if _has_feature_permissions(permissions):
                permissions['features'].pop('channels', None)
                connection.execute(groups.update().where(groups.c.id == id).values(permissions=permissions))


def upgrade() -> None:
    connection = op.get_bind()
    tables = set(sa.inspect(connection).get_table_names())
    for name in ('channel', 'message'):
        if name in tables and connection.execute(sa.text(f'SELECT COUNT(*) FROM {name}')).scalar():
            raise RuntimeError('Channels or messages exist; stop and report counts before removing channels')

    if 'access_grant' in tables:
        connection.execute(sa.text("DELETE FROM access_grant WHERE resource_type = 'channel'"))
    _remove_channel_settings(connection, tables)
    for name in TABLES:
        if name in tables:
            op.drop_table(name)


def downgrade() -> None:
    # Restore the prior schema only. Deleted grants and configuration stay removed.
    op.create_table(
        'channel',
        sa.Column('id', sa.TEXT(), nullable=False),
        sa.Column('user_id', sa.TEXT(), nullable=True),
        sa.Column('name', sa.TEXT(), nullable=True),
        sa.Column('description', sa.TEXT(), nullable=True),
        sa.Column('data', sa.JSON(), nullable=True),
        sa.Column('meta', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.BIGINT(), nullable=True),
        sa.Column('updated_at', sa.BIGINT(), nullable=True),
        sa.Column('type', sa.TEXT(), nullable=True),
        sa.Column('is_private', sa.BOOLEAN(), nullable=True),
        sa.Column('archived_at', sa.BIGINT(), nullable=True),
        sa.Column('archived_by', sa.TEXT(), nullable=True),
        sa.Column('deleted_at', sa.BIGINT(), nullable=True),
        sa.Column('deleted_by', sa.TEXT(), nullable=True),
        sa.Column('updated_by', sa.TEXT(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('id'),
    )
    op.create_table(
        'message',
        sa.Column('id', sa.TEXT(), nullable=False),
        sa.Column('user_id', sa.TEXT(), nullable=True),
        sa.Column('channel_id', sa.TEXT(), nullable=True),
        sa.Column('content', sa.TEXT(), nullable=True),
        sa.Column('data', sa.JSON(), nullable=True),
        sa.Column('meta', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.BIGINT(), nullable=True),
        sa.Column('updated_at', sa.BIGINT(), nullable=True),
        sa.Column('parent_id', sa.TEXT(), nullable=True),
        sa.Column('reply_to_id', sa.TEXT(), nullable=True),
        sa.Column('is_pinned', sa.BOOLEAN(), server_default=sa.false(), nullable=False),
        sa.Column('pinned_at', sa.BIGINT(), nullable=True),
        sa.Column('pinned_by', sa.TEXT(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('id'),
    )
    op.create_table(
        'message_reaction',
        sa.Column('id', sa.TEXT(), nullable=False),
        sa.Column('user_id', sa.TEXT(), nullable=False),
        sa.Column('message_id', sa.TEXT(), nullable=False),
        sa.Column('name', sa.TEXT(), nullable=False),
        sa.Column('created_at', sa.BIGINT(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('id'),
    )
    op.create_table(
        'channel_member',
        sa.Column('id', sa.TEXT(), nullable=False),
        sa.Column('channel_id', sa.TEXT(), nullable=False),
        sa.Column('user_id', sa.TEXT(), nullable=False),
        sa.Column('created_at', sa.BIGINT(), nullable=True),
        sa.Column('status', sa.TEXT(), nullable=True),
        sa.Column('is_active', sa.BOOLEAN(), server_default=sa.true(), nullable=False),
        sa.Column('is_channel_muted', sa.BOOLEAN(), server_default=sa.false(), nullable=False),
        sa.Column('is_channel_pinned', sa.BOOLEAN(), server_default=sa.false(), nullable=False),
        sa.Column('data', sa.JSON(), nullable=True),
        sa.Column('meta', sa.JSON(), nullable=True),
        sa.Column('joined_at', sa.BIGINT(), nullable=False),
        sa.Column('left_at', sa.BIGINT(), nullable=True),
        sa.Column('last_read_at', sa.BIGINT(), nullable=True),
        sa.Column('updated_at', sa.BIGINT(), nullable=True),
        sa.Column('role', sa.TEXT(), nullable=True),
        sa.Column('invited_by', sa.TEXT(), nullable=True),
        sa.Column('invited_at', sa.BIGINT(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('id'),
    )
    op.create_table(
        'channel_file',
        sa.Column('id', sa.TEXT(), nullable=False),
        sa.Column('user_id', sa.TEXT(), nullable=False),
        sa.Column('channel_id', sa.TEXT(), nullable=False),
        sa.Column('file_id', sa.TEXT(), nullable=False),
        sa.Column('created_at', sa.BIGINT(), nullable=False),
        sa.Column('updated_at', sa.BIGINT(), nullable=False),
        sa.Column('message_id', sa.TEXT(), nullable=True),
        sa.ForeignKeyConstraint(['channel_id'], ['channel.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['file_id'], ['file.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['message_id'], ['message.id'], name='fk_channel_file_message_id', ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('channel_id', 'file_id', name='uq_channel_file_channel_file'),
    )
    op.create_index('ix_channel_file_user_id', 'channel_file', ['user_id'], unique=False)
    op.create_index('ix_channel_file_file_id', 'channel_file', ['file_id'], unique=False)
    op.create_index('ix_channel_file_channel_id', 'channel_file', ['channel_id'], unique=False)
    op.create_table(
        'channel_webhook',
        sa.Column('id', sa.TEXT(), nullable=False),
        sa.Column('user_id', sa.TEXT(), nullable=False),
        sa.Column('channel_id', sa.TEXT(), nullable=False),
        sa.Column('name', sa.TEXT(), nullable=False),
        sa.Column('profile_image_url', sa.TEXT(), nullable=True),
        sa.Column('token', sa.TEXT(), nullable=False),
        sa.Column('last_used_at', sa.BIGINT(), nullable=True),
        sa.Column('created_at', sa.BIGINT(), nullable=False),
        sa.Column('updated_at', sa.BIGINT(), nullable=False),
        sa.ForeignKeyConstraint(['channel_id'], ['channel.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('id'),
    )
