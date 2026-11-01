"""Persist organisation only; legacy content columns are never selected or assigned."""

import time
from collections.abc import Callable

import sqlalchemy as sa

from open_webui.internal.db import get_async_db
from open_webui.soev.threads import Organization, ThreadError

chat = sa.table(
    'chat',
    sa.column('id', sa.String),
    sa.column('user_id', sa.String),
    sa.column('thread_id', sa.String),
    sa.column('folder_id', sa.Text),
    sa.column('pinned', sa.Boolean),
    sa.column('archived', sa.Boolean),
    sa.column('last_read_at', sa.BigInteger),
    sa.column('created_at', sa.BigInteger),
    sa.column('updated_at', sa.BigInteger),
)
branches = sa.table('chat_thread', sa.column('chat_id', sa.String), sa.column('thread_id', sa.String))
legacy_shares = sa.table('shared_chat', sa.column('id'), sa.column('chat_id'), sa.column('user_id'))
FIELDS = ('id', 'thread_id', 'folder_id', 'pinned', 'archived', 'last_read_at')
reads = sa.table(
    'thread_read',
    sa.column('user_id', sa.String),
    sa.column('conversation_id', sa.String),
    sa.column('last_read_at', sa.Float(precision=53)),
)


class ThreadOrganizations:
    def __init__(self, session: Callable = get_async_db) -> None:
        self.session = session

    async def read_states(self, user_id: str) -> dict[str, float]:
        async with self.session() as db:
            return dict(
                (
                    await db.execute(
                        sa.select(reads.c.conversation_id, reads.c.last_read_at).where(reads.c.user_id == user_id)
                    )
                ).all()
            )

    async def set_read_at(self, user_id: str, conversation_id: str, read_at: float | None) -> float | None:
        async with self.session() as db:
            if read_at is None:
                await db.execute(
                    reads.delete().where(reads.c.user_id == user_id, reads.c.conversation_id == conversation_id)
                )
                await db.commit()
                return None
            from sqlalchemy.dialects.postgresql import insert as pg_insert
            from sqlalchemy.dialects.sqlite import insert as sqlite_insert

            insert = sqlite_insert if db.bind.dialect.name == 'sqlite' else pg_insert
            statement = insert(reads).values(user_id=user_id, conversation_id=conversation_id, last_read_at=read_at)
            statement = statement.on_conflict_do_update(
                index_elements=[reads.c.user_id, reads.c.conversation_id],
                set_={'last_read_at': sa.case((reads.c.last_read_at < read_at, read_at), else_=reads.c.last_read_at)},
            ).returning(reads.c.last_read_at)
            saved = (await db.execute(statement)).scalar_one()
            await db.commit()
            return saved

    async def context_thread_id(self, user_id: str, chat_id: str) -> str | None:
        # Resolve only the attaching container owner's mapping; source access
        # remains the runtime's decision under the destination turn's grant.
        async with self.session() as db:
            return (
                await db.execute(sa.select(chat.c.thread_id).where(chat.c.id == chat_id, chat.c.user_id == user_id))
            ).scalar_one_or_none()

    async def list(self, user_id: str) -> list[Organization]:
        async with self.session() as db:
            rows = (
                (
                    await db.execute(
                        sa.select(*(chat.c[name] for name in FIELDS)).where(
                            chat.c.user_id == user_id, chat.c.thread_id.is_not(None)
                        )
                    )
                )
                .mappings()
                .all()
            )
            owned = (
                (
                    await db.execute(
                        sa.select(branches)
                        .join(chat, chat.c.id == branches.c.chat_id)
                        .where(chat.c.user_id == user_id, chat.c.thread_id.is_not(None))
                    )
                )
                .mappings()
                .all()
            )
        by_chat: dict[str, list[str]] = {}
        for row in owned:
            by_chat.setdefault(row['chat_id'], []).append(row['thread_id'])
        return [
            Organization(
                **{**row, 'pinned': bool(row['pinned']), 'archived': bool(row['archived'])},
                branch_thread_ids=by_chat.get(row['id'], []),
            )
            for row in rows
        ]

    async def legacy_share(self, share_id: str) -> tuple[str, str, str | None] | None:
        # Select identity/mapping only. The retired snapshot is never loaded.
        async with self.session() as db:
            row = (
                await db.execute(
                    sa.select(legacy_shares.c.user_id, chat.c.id, chat.c.thread_id)
                    .join(chat, chat.c.id == legacy_shares.c.chat_id)
                    .where(legacy_shares.c.id == share_id)
                )
            ).first()
        return tuple(row) if row is not None else None

    async def folder_records(self, folder_id: str) -> dict[str, str]:
        async with self.session() as db:
            rows = (
                await db.execute(
                    sa.select(chat.c.thread_id, chat.c.user_id).where(
                        chat.c.folder_id == folder_id, chat.c.thread_id.is_not(None)
                    )
                )
            ).all()
        return dict(rows)

    async def put(self, user_id: str, record: Organization) -> None:
        values = {name: getattr(record, name) for name in FIELDS if name != 'id'}
        if values['last_read_at'] is not None:
            values['last_read_at'] = int(values['last_read_at'])
        async with self.session() as db:
            owner = (
                await db.execute(sa.select(chat.c.user_id, chat.c.thread_id).where(chat.c.id == record.id))
            ).first()
            if owner is not None and (owner.user_id != user_id or owner.thread_id is None):
                raise ThreadError(404, 'unavailable')
            if owner is None:
                now = int(time.time())
                await db.execute(
                    chat.insert().values(id=record.id, user_id=user_id, created_at=now, updated_at=now, **values)
                )
            else:
                await db.execute(
                    chat.update().where(chat.c.id == record.id, chat.c.user_id == user_id).values(**values)
                )
            # Organisation changes may race a fork; never discard an owned branch.
            existing = set(
                (await db.execute(sa.select(branches.c.thread_id).where(branches.c.chat_id == record.id))).scalars()
            )
            for thread_id in sorted((set(record.branch_thread_ids) | {record.thread_id}) - existing):
                await db.execute(branches.insert().values(chat_id=record.id, thread_id=thread_id))
            await db.commit()

    async def remove(self, user_id: str, chat_id: str) -> None:
        async with self.session() as db:
            owned = sa.select(chat.c.id).where(
                chat.c.id == chat_id, chat.c.user_id == user_id, chat.c.thread_id.is_not(None)
            )
            await db.execute(branches.delete().where(branches.c.chat_id.in_(owned)))
            await db.execute(chat.delete().where(chat.c.id.in_(owned)))
            await db.commit()
