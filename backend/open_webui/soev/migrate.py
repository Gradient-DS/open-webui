"""Copy OWUI identities, groups, collections and folders to soev-api, then reconcile.

Run with python -m open_webui.soev.migrate [--dry-run]; OWUI tables are read only.
"""

import argparse
import asyncio
import hashlib
import json
import os
import sys
from urllib.parse import quote


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
    await client.send(method, path, body, idempotency_key=key, as_user=as_user)


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


async def reconcile(file_counts, client, *, conflicts, dry_run=False):
    if dry_run:
        print(f'KB count: {len(file_counts)} | collection count: not read (dry-run)')
        print('Collection | OWUI files | document_count | gap')
        for key, count in sorted(file_counts.items()):
            print(f'{key} | {count} | not read | not read')
        return 0
    collections = {row['key']: row async for row in client.pages('/v1/collections')}
    print(f'KB count: {len(file_counts)} | collection count: {len(collections)}')
    print('Collection | OWUI files | document_count | gap')
    missing = set(file_counts) - collections.keys()
    for key in sorted(file_counts.keys() | collections.keys()):
        count = file_counts.get(key, 0)
        if key in missing:
            print(f'{key} | {count} | MISSING | collection missing')
        else:
            documents = collections[key]['document_count']
            print(f'{key} | {count} | {documents} | {count - documents}')
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
    counts = await table.get_file_counts_by_knowledge_ids(keys, db=db)
    file_counts = {key: counts.get(key, 0) for key in keys}
    paths = await _folder_paths(table, keys, db)
    client = identity.build_client()
    await _push_directory(users, await groups.get_all_groups(db=db), client, dry_run=dry_run, db=db)
    conflicts = await _create_collections(rows, users, grants, client, dry_run=dry_run, db=db)
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
    return await reconcile(file_counts, client, conflicts=conflicts, dry_run=dry_run)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true', help='Print planned requests without sending HTTP requests')
    args = parser.parse_args()
    # Config otherwise runs schema migrations on import, which would write OWUI tables.
    os.environ['ENABLE_DB_MIGRATIONS'] = 'false'

    import httpx

    from open_webui.soev.client import SoevApiError

    try:
        status = asyncio.run(migrate(dry_run=args.dry_run))
    except (SoevApiError, httpx.TransportError, ValueError):
        print('Migration aborted; reconciliation could not complete.', file=sys.stderr)
        raise SystemExit(1) from None
    raise SystemExit(status)


if __name__ == '__main__':
    main()
