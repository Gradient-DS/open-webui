"""Move an Open WebUI v1 tenant to the v2 shape in one idempotent, reversible run.

python -m open_webui.soev.migrate --apply | --restore | --dry-run. The directory copy
(identities, groups, collections, folders, cloud schedules) only reads OWUI tables.
"""

import argparse
import asyncio
import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from urllib.parse import quote

EXIT_OK, EXIT_FAILED = 0, 1


async def _knowledge_rows(table, db):
    from sqlalchemy import select

    from open_webui.internal.db import get_async_db_context
    from open_webui.models.knowledge import Knowledge

    # The general SQL listing calls the rebound AccessGrants singleton internally.
    async with get_async_db_context(db) as session:
        kinds = (await session.execute(select(Knowledge.type).distinct())).scalars().all()
    rows = []
    for kind in sorted(kinds):
        rows.extend(
            kb
            for kb in await table.get_knowledge_bases_by_type(kind, db=db)
            if kb.deleted_at is None and (kb.meta or {}).get('source') != 'external'
        )
    return sorted(rows, key=lambda kb: kb.id)


async def _folder_paths(table, keys, db):
    from open_webui.models.files import Files

    paths = {key: set() for key in keys}
    for file in await Files.get_files(db=db):
        for link in await table.get_knowledge_files_by_file_id(file.id, db=db):
            path = (link.relative_path or '').rpartition('/')[0]
            if link.knowledge_id in paths and path:
                paths[link.knowledge_id].add(path)
    return paths


async def _send(client, method, path, body, *, key, dry_run, as_user=None):
    if dry_run:
        print(json.dumps({'method': method, 'path': path, 'body': body, 'idempotency_key': key, 'as_user': as_user}))
        return
    return await client.send(method, path, body, idempotency_key=key, as_user=as_user)


async def _push_directory(users, groups, client, *, dry_run, db):
    from open_webui.models.users import Users
    from open_webui.soev import identity

    for user in sorted(users, key=lambda user: user.id):
        ref = identity.external_ref(user)
        if dry_run:
            print(
                json.dumps(
                    {
                        'method': 'POST',
                        'path': '/v1/identity/links',
                        'external_ref': ref,
                        'platform_user_id': identity.platform_user_id(ref),
                        'assurance': 'self_vouched',
                        'idempotency_key': f'link:{ref}',
                    }
                )
            )
        else:
            await identity.ensure_link(ref, client)
    for group in sorted(groups, key=lambda group: group.id):
        members = sorted({identity.external_ref(user) for user in await Users.get_users_by_group_id(group.id, db=db)})
        # Match SoevGroupTable's replacement key so migration and runtime pushes agree.
        digest = hashlib.sha256(json.dumps(members, separators=(',', ':')).encode()).hexdigest()
        await _send(
            client,
            'PUT',
            f'/v1/directory/groups/{quote(f"owui:group:{group.id}", safe="")}/members',
            {'members': members},
            key=f'group:{group.id}:{digest}',
            dry_run=dry_run,
        )


async def _create_collections(rows, users, grants, client, *, dry_run, db):
    from open_webui import config
    from open_webui.soev import projection
    from open_webui.soev.client import SoevApiError

    user_ids = {user.id for user in users}
    conflicts = set()
    for kb in rows:
        acl = await grants.get_grants_by_resource('knowledge', kb.id, db=db)
        visibility, readers, writers = projection.access_of(
            [grant.model_dump() for grant in acl],
            service_principal=config.SOEV_API_SERVICE_PRINCIPAL,
        )
        owner = f'owui:user:{kb.user_id}' if kb.user_id in user_ids else None
        if owner is None:
            print(f'{kb.id}: owner missing, created without created_by')
        try:
            await _send(
                client,
                'POST',
                '/v1/collections',
                {
                    'key': kb.id,
                    'name': kb.name,
                    'description': kb.description,
                    'visibility': visibility,
                    'principals': readers,
                    'writers': writers,
                },
                key=f'kb:{kb.id}',
                dry_run=dry_run,
                as_user=owner,
            )
        except SoevApiError as error:
            if error.status != 409:
                raise
            conflicts.add(kb.id)
            reason = 'collection_exists' if error.code == 'collection_exists' else 'collection create conflict'
            print(f'{kb.id}: {reason} (409); not overwritten')
    return conflicts


def _cloud_scope(provider, source):
    single_file = source['type'] == 'file'
    if provider == 'onedrive':
        return {
            'drive_id': source['drive_id'],
            'item_id': source['item_id'],
            'include_descendants': not single_file,
            'single_file': single_file,
        }
    return {
        'file_id': source['item_id'],
        'drive_id': source.get('drive_id'),
        'include_descendants': not single_file,
    }


async def _create_cloud_sync(rows, cloud_owners, client, *, conflicts, dry_run):
    cadence = None
    for kb in rows:
        if kb.id not in cloud_owners:
            continue
        if kb.type == 'confluence':
            print(f'{kb.id}: Confluence cloud sync skipped (D10)')
            continue
        owner = cloud_owners[kb.id]
        if owner is None or kb.id in conflicts:
            reason = 'owner missing' if owner is None else 'collection conflict'
            print(f'{kb.id}: {reason}, cloud sync skipped')
            continue
        sources = (kb.meta or {}).get(f'{kb.type}_sync', {}).get('sources', [])
        scopes = [_cloud_scope(kb.type, source) for source in sources]
        if cadence is None:
            # W3 removed the old tenant interval settings; preview must not fetch policy.
            cadence = (
                '<sync-policy.min_cadence_minutes>'
                if dry_run
                else (await client.get('/v1/sync-policy'))['min_cadence_minutes']
            )
        connection = await _send(
            client,
            'POST',
            '/v1/connections',
            {'source_kind': kb.type, 'credential_kind': 'user_oauth'},
            key=f'migrate:connection:{kb.id}',
            dry_run=dry_run,
            as_user=owner,
        )
        connection_id = f'<connection:{kb.id}>' if dry_run else connection['id']
        if not scopes:
            print(f'{kb.id}: no cloud sources, no schedules created')
        for index, scope in enumerate(scopes):
            for offset, kind in enumerate(('content', 'acl_refresh')):
                await _send(
                    client,
                    'POST',
                    '/v1/schedules',
                    {
                        'connection_id': connection_id,
                        'kind': kind,
                        'scope': scope,
                        'cadence_minutes': cadence,
                        'collection_key': kb.id,
                    },
                    key=f'migrate:schedule:{kb.id}:{2 * index + offset}',
                    dry_run=dry_run,
                    as_user=owner,
                )


async def _cloud_coverage(cloud_owners, client, *, dry_run):
    scheduled = set()
    if not dry_run:
        for owner in sorted({owner for owner in cloud_owners.values() if owner is not None}):
            async for schedule in client.pages('/v1/schedules', as_user=owner):
                key = schedule['collection_key']
                if key in cloud_owners and cloud_owners[key] == owner:
                    scheduled.add(key)
    count = 'not read (dry-run)' if dry_run else len(scheduled)
    print(f'KBs with a schedule: {count} | KBs of a cloud type: {len(cloud_owners)}')
    return {key: f'{"not read" if dry_run else int(key in scheduled)} / 1' for key in cloud_owners}


async def reconcile(file_counts, client, *, conflicts, dry_run=False, cloud_owners=None):
    coverage = await _cloud_coverage(cloud_owners or {}, client, dry_run=dry_run)
    header = 'Collection | OWUI files | document_count | gap | KBs with a schedule / KBs of a cloud type'
    if dry_run:
        print(f'KB count: {len(file_counts)} | collection count: not read (dry-run)')
        print(header)
        for key, count in sorted(file_counts.items()):
            print(f'{key} | {count} | not read | not read | {coverage.get(key, "0 / 0")}')
        return 0
    collections = {row['key']: row async for row in client.pages('/v1/collections')}
    print(f'KB count: {len(file_counts)} | collection count: {len(collections)}')
    print(header)
    missing = set(file_counts) - collections.keys()
    for key in sorted(file_counts.keys() | collections.keys()):
        count = file_counts.get(key, 0)
        if key in missing:
            print(f'{key} | {count} | MISSING | collection missing | {coverage.get(key, "0 / 0")}')
        else:
            documents = collections[key]['document_count']
            print(f'{key} | {count} | {documents} | {count - documents} | {coverage.get(key, "0 / 0")}')
    print('Document gaps are informational until ingest lands.')
    return int(bool(missing or conflicts))


async def migrate(*, dry_run=False, db=None):
    from open_webui.models.access_grants import AccessGrantsTable
    from open_webui.models.groups import GroupTable
    from open_webui.models.knowledge import KnowledgeTable
    from open_webui.models.users import Users
    from open_webui.soev import identity

    table, grants, groups = KnowledgeTable(), AccessGrantsTable(), GroupTable()
    users = (await Users.get_users(db=db))['users']
    rows = await _knowledge_rows(table, db)
    keys = [kb.id for kb in rows]
    counts = {}
    for owner_id in sorted({kb.user_id for kb in rows}):
        owner_keys = [kb.id for kb in rows if kb.user_id == owner_id]
        counts.update(await table.get_file_counts_by_knowledge_ids(owner_keys, db=db, user_id=owner_id))
    file_counts = {key: counts.get(key, 0) for key in keys}
    paths = await _folder_paths(table, keys, db)
    client = identity.build_client()
    await _push_directory(users, await groups.get_all_groups(db=db), client, dry_run=dry_run, db=db)
    conflicts = await _create_collections(rows, users, grants, client, dry_run=dry_run, db=db)
    user_ids = {user.id for user in users}
    cloud_owners = {
        kb.id: f'owui:user:{kb.user_id}' if kb.user_id in user_ids else None
        for kb in rows
        if kb.type in {'onedrive', 'google_drive', 'confluence'}
    }
    await _create_cloud_sync(rows, cloud_owners, client, conflicts=conflicts, dry_run=dry_run)
    for key in keys:
        if key in conflicts:
            continue
        for path in sorted(paths[key]):
            digest = hashlib.sha256(path.encode()).hexdigest()
            await _send(
                client,
                'POST',
                f'/v1/collections/{quote(key, safe="")}/folders',
                {'path': path},
                key=f'folder:{key}:{digest}',
                dry_run=dry_run,
            )
    return await reconcile(file_counts, client, conflicts=conflicts, dry_run=dry_run, cloud_owners=cloud_owners)


@dataclass
class Options:
    migration_id: str
    v2_config: dict = field(default_factory=dict)
    model_map: dict = field(default_factory=dict)


def options_from_env(environ=os.environ, *, migration_id: str | None = None) -> Options:
    from open_webui.soev.migrate_config import parse_v2_config
    from open_webui.soev.migrate_models import parse_model_map
    from open_webui.soev.migrate_state import MigrationError

    migration_id = migration_id or environ.get('SOEV_V2_MIGRATION_ID')
    if not migration_id:
        raise MigrationError('Set SOEV_V2_MIGRATION_ID or pass --migration-id')
    return Options(
        migration_id=migration_id,
        v2_config=parse_v2_config(environ.get('SOEV_V2_CONFIG')),
        model_map=parse_model_map(environ.get('SOEV_V2_MODEL_MAP')),
    )


def _print_model_report(report, *, prefix: str) -> None:
    for site, count in sorted(report.changed.items()):
        print(f'{prefix}: {site} {count}')
    for line in report.skipped:
        print(f'{prefix}: skipped {line}')
    for model_id, count in sorted(report.unmapped.items()):
        print(f'{prefix}: unmapped {model_id} ({count} references left as they are)')


async def apply(options: Options, *, db=None) -> int:
    from open_webui.internal.db import get_async_db_context
    from open_webui.soev import migrate_config, migrate_models, migrate_state

    async with get_async_db_context(db) as session:
        taken = await migrate_state.snapshot(session, options.migration_id, migrate_config.SNAPSHOT_KEYS)
        print(f'1 snapshot: {"taken" if taken else "kept"} ({len(migrate_config.SNAPSHOT_KEYS)} keys)')
        if await migrate_state.has_marker(session, options.migration_id, 'config'):
            print('2 config switch: already done')
        else:
            values = await migrate_config.switch(session, options.v2_config)
            migrate_state.add_marker(session, options.migration_id, 'config')
            await session.commit()
            print(f'2 config switch: {len(values)} keys written')
        if await migrate_state.has_marker(session, options.migration_id, 'model_ids'):
            print('3 model ids: already done')
        else:
            report = await migrate_models.rewrite(session, options.model_map, options.migration_id)
            migrate_state.add_marker(session, options.migration_id, 'model_ids')
            await session.commit()
            _print_model_report(report, prefix='3 model ids')
    return EXIT_OK


async def restore(migration_id: str, *, db=None) -> int:
    from open_webui.internal.db import get_async_db_context
    from open_webui.soev import migrate_models, migrate_state

    async with get_async_db_context(db) as session:
        restored = await migrate_state.restore_config(session, migration_id)
        report = await migrate_models.restore(session, migration_id)
        # The snapshot stays: it is the v1 state, and a later --apply switches again from it.
        for step in ('config', 'model_ids'):
            await migrate_state.drop_marker(session, migration_id, step)
        await session.commit()
        print(f'config rows restored: {restored}')
        _print_model_report(report, prefix='model ids restored')
    return EXIT_OK


async def plan(options: Options, *, db=None) -> int:
    from open_webui.internal.db import get_async_db_context
    from open_webui.models.config import Config
    from open_webui.soev import migrate_config, migrate_models, migrate_state

    async with get_async_db_context(db) as session:
        try:
            snapshotted = await migrate_state.has_marker(session, options.migration_id, 'snapshot')
        except Exception:
            # The state tables arrive with the schema migration the app or Job runs first.
            await session.rollback()
            snapshotted = None
        state = {True: 'exists, kept', False: 'would be taken', None: 'state tables missing'}[snapshotted]
        print(f'1 snapshot: {state} ({len(migrate_config.SNAPSHOT_KEYS)} keys)')
        row = await session.get(Config, migrate_config.PERMISSIONS)
        values = migrate_config.planned(options.v2_config, row.value if row else {})
        for key, value in sorted(values.items()):
            print(f'2 config switch: {key} = {json.dumps(value, sort_keys=True)}')
        for source, target in sorted(options.model_map.items()):
            print(f'3 model ids: map {source} -> {target}')
        _print_model_report(
            await migrate_models.rewrite(session, options.model_map, options.migration_id, dry_run=True),
            prefix='3 model ids',
        )
    return await migrate(dry_run=True, db=db)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--apply', action='store_true', help='Run every step; safe to rerun')
    mode.add_argument('--restore', action='store_true', help='Put back the config snapshot and model ids')
    mode.add_argument('--dry-run', action='store_true', help='Print the plan; no writes and no HTTP requests')
    parser.add_argument('--migration-id', help='Defaults to SOEV_V2_MIGRATION_ID')
    args = parser.parse_args()
    if args.dry_run:
        # Config otherwise runs schema migrations on import, which would write OWUI tables.
        os.environ['ENABLE_DB_MIGRATIONS'] = 'false'

    import httpx

    from open_webui.soev.client import SoevApiError
    from open_webui.soev.migrate_state import MigrationError

    try:
        if args.restore:
            migration_id = args.migration_id or os.environ.get('SOEV_V2_MIGRATION_ID')
            if not migration_id:
                raise MigrationError('Set SOEV_V2_MIGRATION_ID or pass --migration-id')
            status = asyncio.run(restore(migration_id))
        else:
            options = options_from_env(migration_id=args.migration_id)
            status = asyncio.run(plan(options) if args.dry_run else apply(options))
    except MigrationError as error:
        print(f'Migration aborted: {error}', file=sys.stderr)
        raise SystemExit(EXIT_FAILED) from None
    except (SoevApiError, httpx.TransportError, ValueError):
        print('Migration aborted; reconciliation could not complete.', file=sys.stderr)
        raise SystemExit(EXIT_FAILED) from None
    raise SystemExit(status)


if __name__ == '__main__':
    main()
