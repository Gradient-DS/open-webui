"""Rewrite stored model ids to soev-api catalog ids, recording each change for --restore.

The mapping is an explicit input, SOEV_V2_MODEL_MAP: a JSON object of old id to catalog id,
either LiteLLM model names (the v2 apply) or renamed catalog ids (a --models run). Any other id
that is neither a catalog id nor an assistant becomes the catalog's default model.

The v2 apply clears each user's default model selection (settings.ui.models), so every user starts
on the platform default; a --models run maps it instead, since users chose it after the migration.

The v2 apply moves the v1 per-base-model system prompts to the one global prompt (config models.system_prompt,
soev/system_prompt.py): admins set one prompt per base model, and the catalog's models are new rows. The distinct
prompts are candidates; with one it is kept, with several SOEV_V2_SYSTEM_PROMPT names the base model whose prompt
to keep (or none), and the run stops before any write until it does. Every base-model row's own prompt is then
cleared, so all of them use the global one.
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
from open_webui.models.folders import Folder
from open_webui.models.models import Model
from open_webui.models.users import User
from open_webui.soev.migrate_state import MigrationError, state
from open_webui.soev.system_prompt import KEY as SYSTEM_PROMPT_KEY
from open_webui.soev.thread_content import refuse_local_content

BATCH = 200
USER_SETTING_LISTS = (('ui', 'models'), ('ui', 'pinnedModels'))
CLEARED_SELECTION = 'user.settings.cleared'
# [Gradient] The global prompt written, and a base-model row's own prompt cleared.
PROMPT_GLOBAL = 'config.models.system_prompt'
PROMPT_CLEARED = 'model.params.system.cleared'
# Written by the earlier per-model copy (#482); still restored.
PROMPT_CREATED = 'model.created'
PROMPT_SET = 'model.params.system'
PROMPT_SITES = (PROMPT_GLOBAL, PROMPT_CLEARED, PROMPT_CREATED, PROMPT_SET)
NO_PROMPT = 'none'


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


def catalog_default(listed: dict, mapping: dict[str, str]) -> tuple[str, dict[str, str]]:
    """The catalog default the chat picker starts on, and every catalog id with its label, from soev-api's
    GET /v1/models."""
    data = listed.get('data')
    entries = [entry for entry in data if isinstance(entry, dict) and entry.get('id')] if isinstance(data, list) else []
    default = next((entry['id'] for entry in entries if entry.get('default')), None)
    if not isinstance(default, str):
        raise MigrationError('The soev-api model catalog marks no default model; unmapped model ids need one')
    if default in mapping:
        raise MigrationError(f'SOEV_V2_MODEL_MAP rewrites the default model {default}')
    return default, {entry['id']: entry.get('label') or entry['id'] for entry in entries}


@dataclass
class Report:
    changed: Counter = field(default_factory=Counter)
    # Per site, and per old id, the references rewritten to the default model.
    defaulted: Counter = field(default_factory=Counter)
    defaulted_ids: Counter = field(default_factory=Counter)
    skipped: list[str] = field(default_factory=list)
    merged: list[str] = field(default_factory=list)
    # Users whose default model selection was cleared.
    cleared: int = 0
    # The global system prompt kept (None: none) and the base-model rows whose own prompt was cleared.
    prompt_step: bool = False
    prompt: str | None = None
    cleared_prompts: list[str] = field(default_factory=list)


class _Rewrite:
    def __init__(
        self,
        db: AsyncSession,
        mapping: dict[str, str],
        migration_id: str,
        dry_run: bool,
        known: set[str],
        default: str,
        clear_selection: bool,
    ):
        self.db, self.mapping, self.migration_id, self.dry_run = db, mapping, migration_id, dry_run
        self.clear_selection = clear_selection
        # Assistant ids and catalog ids are already v2 references, not unmapped names.
        self.known = known | set(mapping.values())
        self.default = default
        self.report = Report()
        self.now = int(time.time())
        self.records: list[dict] = []
        # New id to old id of each base-model row renamed, so a dry run reads the row a rename would leave.
        self.renamed: dict[str, str] = {}

    def target(self, value: str) -> str | None:
        """The id a stored reference becomes; None when it already is a v2 reference."""
        if value in self.mapping:
            return self.mapping[value]
        return None if value in self.known else self.default

    def count(self, site: str, value: str) -> None:
        if value in self.mapping:
            self.report.changed[site] += 1
        else:
            self.report.defaulted[site] += 1
            self.report.defaulted_ids[value] += 1

    def map(self, site: str, row_id: str, path: list, value, *, record: bool = True) -> object:
        if not isinstance(value, str) or not value:
            return value
        new = self.target(value)
        if new is None:
            return value
        self.count(site, value)
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
        for value, _ in sorted(counts.all()):
            new = self.target(value)
            if new is None:
                continue
            ids = (await self.db.execute(sa.select(table.id).where(attribute == value))).scalars().all()
            for row_id in sorted(ids):
                self.map(site, row_id, [], value)
            if not self.dry_run:
                await self.db.execute(sa.update(table).where(attribute == value).values({column: new}))
        await self._flush()

    async def rename_models(self) -> None:
        """Base-model override rows are keyed by the model id; rename them and their grants."""
        site = 'model.id'
        existing = set((await self.db.execute(sa.select(Model.id))).scalars().all())
        rows = (await self.db.execute(sa.select(Model.id, Model.updated_at).where(Model.base_model_id.is_(None)))).all()
        # [Gradient] When several ids map to one target, the most recently updated row becomes it
        # and the others merge into it. An unmapped row always merges into the default, so its
        # name and params never become the default model's.
        for model_id, _ in sorted(rows, key=lambda row: (-(row[1] or 0), row[0])):
            new = self.target(model_id)
            if new is None:
                continue
            if new in existing or model_id not in self.mapping:
                await self.merge_model(model_id, new)
                continue
            self.map(site, new, [], model_id)
            existing.add(new)
            self.renamed[new] = model_id
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
        """[Gradient] The target row exists or the id is unmapped: give the target the legacy grants, deactivate
        the legacy row."""
        moved = await self.move_grants(model_id, new)
        active = (await self.db.execute(sa.select(Model.is_active).where(Model.id == model_id))).scalar()
        if active is not False:
            self.count('model.is_active', model_id)
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
        reason = f'{new} exists' if model_id in self.mapping else f'unmapped, merged into the default {new}'
        self.report.merged.append(f'model {model_id}: {reason}; {moved} grants moved to it, {model_id} {state}')

    async def global_prompt(self, prompt: str | None) -> None:
        """[Gradient] Write the kept prompt as the global one and clear every base-model row's own, so each base
        model uses it. Runs after the renames: the records are keyed by the ids restore finds first."""
        self.report.prompt_step, self.report.prompt = True, prompt
        if prompt is not None:
            row = await self.db.get(Config, SYSTEM_PROMPT_KEY)
            old = row.value if row is not None else None
            if old != prompt:
                self.report.changed[PROMPT_GLOBAL] += 1
                if not self.dry_run:
                    self.records.append(self._prompt_record(PROMPT_GLOBAL, SYSTEM_PROMPT_KEY, old, prompt))
                    if row is None:
                        self.db.add(Config(key=SYSTEM_PROMPT_KEY, value=prompt, updated_at=self.now))
                    else:
                        row.value, row.updated_at = prompt, self.now
        query = sa.select(Model.id, Model.params).where(Model.base_model_id.is_(None)).order_by(Model.id)
        for model_id, params in (await self.db.execute(query)).all():
            own = params.get('system') if isinstance(params, dict) else None
            if not isinstance(own, str) or not own.strip():
                continue
            self.report.changed[PROMPT_CLEARED] += 1
            # A dry run leaves the v1 ids; report the ids the renames give them.
            new_ids = {old: new for new, old in self.renamed.items()} if self.dry_run else {}
            self.report.cleared_prompts.append(new_ids.get(model_id, model_id))
            if self.dry_run:
                continue
            kept = {key: value for key, value in params.items() if key != 'system'}
            # The whole params, so a restore puts the stored row back as it was.
            self.records.append(self._prompt_record(PROMPT_CLEARED, model_id, params, kept, path=['params']))
            await self.db.execute(sa.update(Model).where(Model.id == model_id).values(params=kept))
        await self._flush()

    def _prompt_record(self, site: str, row_id: str, old, new, *, path=('params', 'system')) -> dict:
        return {
            'migration_id': self.migration_id,
            'site': site,
            'row_id': row_id,
            'path': json.dumps(list(path)),
            'old_value': json.dumps(old),
            'new_value': json.dumps(new),
            'created_at': self.now,
        }

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
        ui = result.get('ui')
        if self.clear_selection and isinstance(ui, dict) and 'models' in ui:
            self.report.cleared += 1
            if not self.dry_run:
                self.records.append(
                    {
                        'migration_id': self.migration_id,
                        'site': CLEARED_SELECTION,
                        'row_id': row_id,
                        'path': json.dumps(['ui', 'models']),
                        'old_value': json.dumps(ui['models']),
                        'new_value': 'null',
                        'created_at': self.now,
                    }
                )
            del ui['models']
        for path in USER_SETTING_LISTS:
            parent = result.get(path[0])
            if isinstance(parent, dict) and isinstance(parent.get(path[1]), list):
                parent[path[1]] = [self.map(site, row_id, [*path, i], v) for i, v in enumerate(parent[path[1]])]
        return result

    def folder(self, site: str, row_id: str, value):
        """A folder's default models for its new chats."""
        if not isinstance(value, dict) or not isinstance(value.get('model_ids'), list):
            return value
        model_ids = [self.map(site, row_id, ['model_ids', i], v) for i, v in enumerate(value['model_ids'])]
        return {**value, 'model_ids': model_ids}

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


@dataclass
class PromptCandidate:
    """[Gradient] One distinct v1 base-model system prompt and the base-model rows that carry it, newest first."""

    text: str
    # (id, name, updated_at) per row.
    rows: list[tuple[str, str | None, int | None]]

    @property
    def updated_at(self) -> int:
        return max((updated or 0) for _, _, updated in self.rows)


async def prompt_candidates(db: AsyncSession) -> list[PromptCandidate]:
    """[Gradient] The distinct own prompts of the base-model rows (assistants never count), most recently updated
    first. Prompts that differ only in surrounding whitespace are one."""
    query = sa.select(Model.id, Model.name, Model.params, Model.updated_at).where(Model.base_model_id.is_(None))
    query = query.order_by(sa.func.coalesce(Model.updated_at, 0).desc(), Model.id)
    by_text: dict[str, PromptCandidate] = {}
    for model_id, name, params, updated_at in (await db.execute(query)).all():
        prompt = params.get('system') if isinstance(params, dict) else None
        if not isinstance(prompt, str) or not prompt.strip():
            continue
        # Newest first, so the most recently updated row's text stands for the candidate.
        candidate = by_text.setdefault(prompt.strip(), PromptCandidate(prompt, []))
        candidate.rows.append((model_id, name, updated_at))
    return list(by_text.values())


def choose_prompt(candidates: list[PromptCandidate], choice: str | None) -> str | None:
    """[Gradient] The prompt to keep as the global one: the candidate holding the chosen base model's row, none for
    'none', the only candidate when there is one. Several and no choice stops the run before any write."""
    if choice == NO_PROMPT:
        return None
    if choice:
        for candidate in candidates:
            if any(model_id == choice for model_id, _, _ in candidate.rows):
                return candidate.text
        ids = ', '.join(model_id for candidate in candidates for model_id, _, _ in candidate.rows) or 'none exist'
        raise MigrationError(
            f'SOEV_V2_SYSTEM_PROMPT names {choice}, which has no system prompt; base models with one: {ids}'
        )
    if len(candidates) > 1:
        raise MigrationError(
            f'{len(candidates)} different base-model system prompts; set SOEV_V2_SYSTEM_PROMPT (chart:'
            ' soevApi.migrate.systemPrompt) to the base model whose prompt to keep, or none. Mode plan prints them.'
        )
    return candidates[0].text if candidates else None


async def _assistant_ids(db: AsyncSession) -> set[str]:
    return set((await db.execute(sa.select(Model.id).where(Model.base_model_id.is_not(None)))).scalars().all())


async def rewrite(
    db: AsyncSession,
    mapping: dict[str, str],
    migration_id: str,
    *,
    default: str,
    catalog: dict[str, str],
    clear_selection: bool,
    prompts: bool = False,
    prompt_choice: str | None = None,
    dry_run: bool = False,
) -> Report:
    """Map every stored model reference, unmapped ones to the default; catalog ids and assistants are left alone.

    clear_selection drops each user's settings.ui.models instead of mapping it; prompts moves the base-model
    system prompts to the global one, the candidate prompt_choice names (choose_prompt)."""
    refuse_local_content()
    known = await _assistant_ids(db) | set(catalog)
    run = _Rewrite(db, mapping, migration_id, dry_run, known, default, clear_selection)
    # Chosen before the renames, which merge and deactivate rows; the choice names a v1 id.
    prompt = choose_prompt(await prompt_candidates(db), prompt_choice) if prompts else None
    await run.column('chat_message.model_id', ChatMessage, 'model_id')
    await run.column('model.base_model_id', Model, 'base_model_id')
    await run.rename_models()
    if prompts:
        await run.global_prompt(prompt)
    await run.json_rows('chat.chat', Chat, 'chat', lambda site, row_id, value: run.walk(site, row_id, value, []))
    await run.json_rows('user.settings', User, 'settings', run.settings)
    await run.json_rows('automation.data', Automation, 'data', run.automation)
    await run.json_rows('folder.data', Folder, 'data', run.folder)
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
    'folder.data': (Folder, 'data'),
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


async def _restore_global_prompt(db: AsyncSession, row_id: str, entries, report: Report) -> None:
    """[Gradient] Put back the global prompt (no row when there was none) unless an admin has changed it since."""
    (entry,) = entries
    row = await db.get(Config, row_id)
    if row is None or row.value != json.loads(entry.new_value):
        report.skipped.append(f'{PROMPT_GLOBAL}: changed since')
        return
    old = json.loads(entry.old_value)
    if old is None:
        await db.delete(row)
    else:
        row.value, row.updated_at = old, int(time.time())
    report.changed[PROMPT_GLOBAL] += 1


async def _restore_cleared_prompt(db: AsyncSession, row_id: str, entries, report: Report) -> None:
    """[Gradient] Give a base-model row its own prompt back unless an admin has set one since; params nobody touched
    get the stored value back as it was."""
    (entry,) = entries
    row = (await db.execute(sa.select(Model.params).where(Model.id == row_id))).first()
    params = row.params if row is not None and isinstance(row.params, dict) else None
    current = params.get('system') if params is not None else None
    if params is None or (isinstance(current, str) and current.strip()):
        report.skipped.append(f'{PROMPT_CLEARED} {row_id}: changed since')
        return
    old = json.loads(entry.old_value)
    restored = old if params == json.loads(entry.new_value) else {**params, 'system': old.get('system')}
    await db.execute(sa.update(Model).where(Model.id == row_id).values(params=restored))
    report.changed[PROMPT_CLEARED] += 1


async def _restore_prompt(db: AsyncSession, site: str, row_id: str, entries, report: Report) -> None:
    """[Gradient] Remove a copied system prompt, and the row created for it, unless an admin has changed it since."""
    if site == PROMPT_GLOBAL:
        await _restore_global_prompt(db, row_id, entries, report)
        return
    if site == PROMPT_CLEARED:
        await _restore_cleared_prompt(db, row_id, entries, report)
        return
    (entry,) = entries
    row = (await db.execute(sa.select(Model.params).where(Model.id == row_id))).first()
    params = row.params if row is not None and isinstance(row.params, dict) else None
    if params is None or params.get('system') != json.loads(entry.new_value):
        report.skipped.append(f'{site} {row_id}: changed since')
        return
    if site == PROMPT_CREATED:
        await db.execute(sa.delete(Model).where(Model.id == row_id))
    else:
        old = json.loads(entry.old_value)
        params = {key: value for key, value in params.items() if key != 'system'}
        if old is not None:
            params['system'] = old
        await db.execute(sa.update(Model).where(Model.id == row_id).values(params=params))
    report.changed[site] += 1


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


async def _restore_cleared(db: AsyncSession, row_id: str, entries, report: Report) -> None:
    """Put back a cleared selection unless the user has chosen one since."""
    (entry,) = entries
    value = copy.deepcopy((await db.execute(sa.select(User.settings).where(User.id == row_id))).scalar())
    ui = value.get('ui') if isinstance(value, dict) else None
    if not isinstance(ui, dict) or 'models' in ui:
        report.skipped.append(f'{CLEARED_SELECTION} {row_id}: changed since')
        return
    ui['models'] = json.loads(entry.old_value)
    await db.execute(sa.update(User).where(User.id == row_id).values(settings=value))
    report.changed[CLEARED_SELECTION] += 1


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
    refuse_local_content()
    report = Report()
    backup = (await state(db)).model_id_backup
    records = (await db.execute(sa.select(backup).where(backup.c.migration_id == migration_id))).all()
    by_row: dict[tuple[str, str], list] = {}
    for record in records:
        by_row.setdefault((record.site, record.row_id), []).append(record)
    # Prompts first: they are recorded under the catalog id, which a renamed row gives up below.
    for (site, row_id), entries in sorted(by_row.items(), key=lambda item: (item[0][0] not in PROMPT_SITES, item[0])):
        if site in PROMPT_SITES:
            await _restore_prompt(db, site, row_id, entries, report)
        elif site == 'model.id':
            await _restore_rename(db, row_id, entries, report)
        elif site == 'model.is_active':
            await _restore_active(db, row_id, entries, report)
        elif site == CLEARED_SELECTION:
            await _restore_cleared(db, row_id, entries, report)
        elif site in _COLUMN_SITES:
            await _restore_column(db, site, row_id, entries, report)
        else:
            await _restore_json(db, site, row_id, entries, report)
    await db.execute(sa.delete(backup).where(backup.c.migration_id == migration_id))
    await db.commit()
    return report
