"""Rewrite stored model ids to soev-api catalog ids, recording each change for --restore.

The mapping is an explicit input, SOEV_V2_MODEL_MAP: a JSON object of old id to catalog id,
either LiteLLM model names (the v2 apply) or renamed catalog ids (a --models run).
"""

import copy
import json
import time
from collections import Counter
from dataclasses import dataclass, field

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.models.access_grants import AccessGrant
from open_webui.models.automations import Automation
from open_webui.models.chat_messages import ChatMessage
from open_webui.models.chats import Chat
from open_webui.models.config import Config
from open_webui.models.models import Model
from open_webui.models.users import User
from open_webui.soev.migrate_state import MigrationError, state

BATCH = 200
USER_SETTING_LISTS = (('ui', 'models'), ('ui', 'pinnedModels'))


def parse_model_map(raw: str | None) -> dict[str, str]:
    if raw is None:
        raise MigrationError('SOEV_V2_MODEL_MAP is not set')
    try:
        mapping = json.loads(raw)
    except json.JSONDecodeError as error:
        raise MigrationError(f'SOEV_V2_MODEL_MAP is not JSON: {error.msg}') from None
    if not isinstance(mapping, dict) or not all(
        isinstance(key, str) and key and isinstance(value, str) and value for key, value in mapping.items()
    ):
        raise MigrationError('SOEV_V2_MODEL_MAP must map stored model ids to catalog ids')
    mapping = {key: value for key, value in mapping.items() if key != value}
    # A catalog id that is also a source name would be rewritten again on every rerun.
    if chained := sorted(set(mapping.values()) & mapping.keys()):
        raise MigrationError(f'SOEV_V2_MODEL_MAP maps to ids it also rewrites: {", ".join(chained)}')
    return mapping


@dataclass
class Report:
    changed: Counter = field(default_factory=Counter)
    unmapped: Counter = field(default_factory=Counter)
    skipped: list[str] = field(default_factory=list)
    merged: list[str] = field(default_factory=list)


class _Rewrite:
    def __init__(self, db: AsyncSession, mapping: dict[str, str], migration_id: str, dry_run: bool, known: set[str]):
        self.db, self.mapping, self.migration_id, self.dry_run = db, mapping, migration_id, dry_run
        # Assistant ids and catalog ids are already v2 references, not unmapped names.
        self.known = known | set(mapping.values())
        self.report = Report()
        self.now = int(time.time())
        self.records: list[dict] = []

    def map(self, site: str, row_id: str, path: list, value, *, record: bool = True) -> object:
        if not isinstance(value, str) or not value:
            return value
        if value not in self.mapping:
            if value not in self.known:
                self.report.unmapped[value] += 1
            return value
        new = self.mapping[value]
        self.report.changed[site] += 1
        if record and not self.dry_run:
            # Written in the transaction that rewrites the row, so a rerun never finds one without the other.
            self.records.append(
                {
                    'migration_id': self.migration_id,
                    'site': site,
                    'row_id': row_id,
                    'path': json.dumps(path),
                    'old_value': value,
                    'new_value': new,
                    'created_at': self.now,
                }
            )
        return new

    def walk(self, site: str, row_id: str, value, path: list):
        """Map strings under any 'model' key or inside any 'models' list."""
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                if key == 'model':
                    result[key] = self.map(site, row_id, [*path, key], item)
                elif key == 'models' and isinstance(item, list):
                    result[key] = [self.map(site, row_id, [*path, key, i], v) for i, v in enumerate(item)]
                else:
                    result[key] = self.walk(site, row_id, item, [*path, key])
            return result
        if isinstance(value, list):
            return [self.walk(site, row_id, item, [*path, i]) for i, item in enumerate(value)]
        return value

    async def column(self, site: str, table, column: str) -> None:
        attribute = getattr(table, column)
        counts = await self.db.execute(
            sa.select(attribute, sa.func.count()).where(attribute.is_not(None)).group_by(attribute)
        )
        for value, count in sorted(counts.all()):
            if value not in self.mapping:
                if value not in self.known:
                    self.report.unmapped[value] += count
                continue
            ids = (await self.db.execute(sa.select(table.id).where(attribute == value))).scalars().all()
            for row_id in sorted(ids):
                self.map(site, row_id, [], value)
            if not self.dry_run:
                await self.db.execute(sa.update(table).where(attribute == value).values({column: self.mapping[value]}))
        await self._flush()

    async def rename_models(self) -> None:
        """Base-model override rows are keyed by the model id; rename them and their grants."""
        site = 'model.id'
        existing = set((await self.db.execute(sa.select(Model.id))).scalars().all())
        rows = (await self.db.execute(sa.select(Model.id, Model.updated_at).where(Model.base_model_id.is_(None)))).all()
        # [Gradient] When several ids map to one target, the most recently updated row becomes it
        # and the others merge into it.
        for model_id, _ in sorted(rows, key=lambda row: (-(row[1] or 0), row[0])):
            if model_id not in self.mapping:
                self.map(site, model_id, [], model_id, record=False)
                continue
            new = self.mapping[model_id]
            if new in existing:
                await self.merge_model(model_id, new)
                continue
            self.map(site, new, [], model_id)
            existing.add(new)
            if self.dry_run:
                continue
            await self.db.execute(sa.update(Model).where(Model.id == model_id).values(id=new))
            await self.move_grants(model_id, new)
        await self._flush()

    async def move_grants(self, model_id: str, new: str) -> int:
        """Point the model's grants at the new id, leaving any the target already holds."""
        target = sa.select(AccessGrant.principal_type, AccessGrant.principal_id, AccessGrant.permission).where(
            AccessGrant.resource_type == 'model', AccessGrant.resource_id == new
        )
        held = set((await self.db.execute(target)).all())
        grants = sa.select(AccessGrant.id, AccessGrant.principal_type, AccessGrant.principal_id, AccessGrant.permission)
        grants = grants.where(AccessGrant.resource_type == 'model', AccessGrant.resource_id == model_id)
        moved = 0
        for grant_id, *principal in (await self.db.execute(grants.order_by(AccessGrant.id))).all():
            if tuple(principal) in held:
                continue
            moved += 1
            self.map('access_grant.resource_id', grant_id, [], model_id)
            if not self.dry_run:
                await self.db.execute(sa.update(AccessGrant).where(AccessGrant.id == grant_id).values(resource_id=new))
        return moved

    async def merge_model(self, model_id: str, new: str) -> None:
        """[Gradient] The catalog row exists: keep it, give it the legacy grants, deactivate the legacy row."""
        moved = await self.move_grants(model_id, new)
        active = (await self.db.execute(sa.select(Model.is_active).where(Model.id == model_id))).scalar()
        if active is not False:
            self.report.changed['model.is_active'] += 1
            if not self.dry_run:
                self.records.append(
                    {
                        'migration_id': self.migration_id,
                        'site': 'model.is_active',
                        'row_id': model_id,
                        'path': '[]',
                        'old_value': json.dumps(active),
                        'new_value': 'false',
                        'created_at': self.now,
                    }
                )
                await self.db.execute(sa.update(Model).where(Model.id == model_id).values(is_active=False))
        state = 'deactivated' if active is not False else 'already inactive'
        self.report.merged.append(f'model {model_id}: {new} exists; {moved} grants moved to it, {model_id} {state}')

    async def json_rows(self, site: str, table, column: str, rewrite) -> None:
        attribute, last = getattr(table, column), None
        while True:
            query = sa.select(table.id, attribute).where(attribute.is_not(None)).order_by(table.id).limit(BATCH)
            if last is not None:
                query = query.where(table.id > last)
            batch = (await self.db.execute(query)).all()
            if not batch:
                return
            for row_id, value in batch:
                new = rewrite(site, row_id, value)
                if new != value and not self.dry_run:
                    await self.db.execute(sa.update(table).where(table.id == row_id).values({column: new}))
            last = batch[-1][0]
            await self._flush()

    def settings(self, site: str, row_id: str, value):
        if not isinstance(value, dict):
            return value
        result = copy.deepcopy(value)
        for path in USER_SETTING_LISTS:
            parent = result.get(path[0])
            if isinstance(parent, dict) and isinstance(parent.get(path[1]), list):
                parent[path[1]] = [self.map(site, row_id, [*path, i], v) for i, v in enumerate(parent[path[1]])]
        return result

    def automation(self, site: str, row_id: str, value):
        if not isinstance(value, dict):
            return value
        return {**value, 'model_id': self.map(site, row_id, ['model_id'], value.get('model_id'))}

    async def config(self) -> None:
        """Pinned defaults and the model order; the config snapshot restores both, so no records."""
        for key in ('ui.default_pinned_models', 'ui.model_order_list'):
            row = await self.db.get(Config, key)
            if row is None:
                continue
            value = row.value
            if isinstance(value, str):
                items = [item.strip() for item in value.split(',') if item.strip()]
                new = ','.join(str(self.map(key, key, [], item, record=False)) for item in items)
                new = new if new != ','.join(items) else value
            elif isinstance(value, list):
                new = [self.map(key, key, [], item, record=False) for item in value]
            else:
                continue
            if new != value and not self.dry_run:
                row.value, row.updated_at = new, self.now
        await self._flush()

    async def _flush(self) -> None:
        if self.dry_run:
            return
        if self.records:
            await self.db.execute(sa.insert((await state(self.db)).model_id_backup), self.records)
            self.records = []
        await self.db.commit()


async def _assistant_ids(db: AsyncSession) -> set[str]:
    return set((await db.execute(sa.select(Model.id).where(Model.base_model_id.is_not(None)))).scalars().all())


async def rewrite(db: AsyncSession, mapping: dict[str, str], migration_id: str, *, dry_run: bool = False) -> Report:
    """Map every stored model reference; rows already holding catalog ids are left alone."""
    run = _Rewrite(db, mapping, migration_id, dry_run, await _assistant_ids(db))
    await run.column('chat_message.model_id', ChatMessage, 'model_id')
    await run.column('model.base_model_id', Model, 'base_model_id')
    await run.rename_models()
    await run.json_rows('chat.chat', Chat, 'chat', lambda site, row_id, value: run.walk(site, row_id, value, []))
    await run.json_rows('user.settings', User, 'settings', run.settings)
    await run.json_rows('automation.data', Automation, 'data', run.automation)
    await run.config()
    return run.report


def _get(value, path):
    for key in path:
        value = value[key]
    return value


def _put(value, path, new) -> None:
    for key in path[:-1]:
        value = value[key]
    value[path[-1]] = new


_JSON_SITES = {
    'chat.chat': (Chat, 'chat'),
    'user.settings': (User, 'settings'),
    'automation.data': (Automation, 'data'),
}
_COLUMN_SITES = {
    'chat_message.model_id': (ChatMessage, 'model_id'),
    'model.base_model_id': (Model, 'base_model_id'),
    'access_grant.resource_id': (AccessGrant, 'resource_id'),
}


async def _restore_rename(db: AsyncSession, row_id: str, entries, report: Report) -> None:
    # row_id is the new id; the one record is the rename itself.
    (entry,) = entries
    if await db.get(Model, entry.old_value) is not None or await db.get(Model, row_id) is None:
        report.skipped.append(f'model {row_id}: not renamed back')
        return
    await db.execute(sa.update(Model).where(Model.id == row_id).values(id=entry.old_value))
    report.changed['model.id'] += 1


async def _restore_active(db: AsyncSession, row_id: str, entries, report: Report) -> None:
    (entry,) = entries
    result = await db.execute(
        sa.update(Model)
        .where(Model.id == row_id, Model.is_active.is_(False))
        .values(is_active=json.loads(entry.old_value))
    )
    if result.rowcount:
        report.changed['model.is_active'] += 1
    else:
        report.skipped.append(f'model.is_active {row_id}: changed since')


async def _restore_column(db: AsyncSession, site: str, row_id: str, entries, report: Report) -> None:
    table, column = _COLUMN_SITES[site]
    (entry,) = entries
    result = await db.execute(
        sa.update(table)
        .where(table.id == row_id, getattr(table, column) == entry.new_value)
        .values({column: entry.old_value})
    )
    if result.rowcount:
        report.changed[site] += 1
    else:
        report.skipped.append(f'{site} {row_id}: changed since')


async def _restore_json(db: AsyncSession, site: str, row_id: str, entries, report: Report) -> None:
    table, column = _JSON_SITES[site]
    value = (await db.execute(sa.select(getattr(table, column)).where(table.id == row_id))).scalar()
    value = copy.deepcopy(value)
    for entry in entries:
        path = json.loads(entry.path)
        try:
            current = _get(value, path)
        except (KeyError, IndexError, TypeError):
            current = None
        if current != entry.new_value:
            report.skipped.append(f'{site} {row_id} {entry.path}: changed since')
            continue
        _put(value, path, entry.old_value)
        report.changed[site] += 1
    await db.execute(sa.update(table).where(table.id == row_id).values({column: value}))


async def restore(db: AsyncSession, migration_id: str) -> Report:
    """Put back each recorded reference that still holds the value written; then forget the records."""
    report = Report()
    backup = (await state(db)).model_id_backup
    records = (await db.execute(sa.select(backup).where(backup.c.migration_id == migration_id))).all()
    by_row: dict[tuple[str, str], list] = {}
    for record in records:
        by_row.setdefault((record.site, record.row_id), []).append(record)
    for (site, row_id), entries in sorted(by_row.items()):
        if site == 'model.id':
            await _restore_rename(db, row_id, entries, report)
        elif site == 'model.is_active':
            await _restore_active(db, row_id, entries, report)
        elif site in _COLUMN_SITES:
            await _restore_column(db, site, row_id, entries, report)
        else:
            await _restore_json(db, site, row_id, entries, report)
    await db.execute(sa.delete(backup).where(backup.c.migration_id == migration_id))
    await db.commit()
    return report
