"""Offline OWUI person and group cutover, with rollback unless --apply is set."""

import argparse
import asyncio
import json
import logging
import sys
from contextlib import redirect_stdout
from pathlib import Path


class CutoverRefused(ValueError):
    pass


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CutoverRefused('Duplicate JSON object keys')
        result[key] = value
    return result


def _validate_inputs(person_map, group_map, excluded_ids):
    from open_webui.soev.principal_refs import is_canonical_person_ref, is_canonical_uuid

    for mapping in (person_map, group_map):
        if not isinstance(mapping, dict) or not all(isinstance(key, str) and key for key in mapping):
            raise CutoverRefused('Maps must be JSON objects with nonempty user or group ids')
    if not all(is_canonical_person_ref(ref) for ref in person_map.values()):
        raise CutoverRefused('person_map values must be canonical platform person refs')
    if not all(
        isinstance(ref, str) and ref.startswith('platform:group:') and is_canonical_uuid(ref[15:])
        for ref in group_map.values()
    ):
        raise CutoverRefused('group_map values must be canonical platform group refs')
    if (
        not isinstance(excluded_ids, list)
        or not all(isinstance(id, str) and id for id in excluded_ids)
        or len(set(excluded_ids)) != len(excluded_ids)
    ):
        raise CutoverRefused('excluded must be a JSON list of unique nonempty user ids')


async def _lock_tables(db, tables):
    from sqlalchemy import text

    dialect = db.get_bind().dialect
    if dialect.name == 'postgresql':
        names = ', '.join(dialect.identifier_preparer.format_table(table) for table in tables)
        await db.execute(text(f'LOCK TABLE {names} IN SHARE ROW EXCLUSIVE MODE'))
    elif dialect.name == 'sqlite':
        # A row lock would not prevent a new, unmapped account during cutover.
        await db.execute(text('BEGIN IMMEDIATE'))
    else:
        raise CutoverRefused('Cutover requires PostgreSQL or SQLite table locking')


async def _snapshot(db, tables):
    from sqlalchemy import select

    return {table.name: [dict(row) for row in (await db.execute(select(table))).mappings()] for table in tables}


def _expected_rows(before, person_map, group_map):
    targets = {old: ref[15:] for old, ref in group_map.items()}
    expected = {name: [dict(row) for row in rows] for name, rows in before.items()}
    for row in expected['user']:
        row['person_id'] = person_map.get(row['id'])
    for row in expected['group']:
        row['id'] = targets.get(row['id'], row['id'])
    for row in expected['group_member']:
        row['group_id'] = targets.get(row['group_id'], row['group_id'])
    for row in expected['access_grant']:
        if row['principal_type'] == 'group':
            row['principal_id'] = targets.get(row['principal_id'], row['principal_id'])
    return expected


async def run_cutover(db, person_map, group_map, excluded_ids, *, apply=False):
    from open_webui.models.access_grants import AccessGrant
    from open_webui.models.groups import Group, GroupMember
    from open_webui.models.users import User
    from open_webui.soev.migrate_groups import cutover_groups
    from open_webui.soev.migrate_persons import cutover_persons

    _validate_inputs(person_map, group_map, excluded_ids)
    if db.in_transaction():
        raise CutoverRefused('Cutover requires a fresh transaction')
    tables = (User.__table__, Group.__table__, GroupMember.__table__, AccessGrant.__table__)
    async with db.begin() as transaction:
        await _lock_tables(db, tables)
        before = await _snapshot(db, tables)
        try:
            persons = await cutover_persons(db, person_map, excluded_ids)
            groups = await cutover_groups(db, group_map)
        except ValueError as error:
            raise CutoverRefused(str(error)) from None
        after = await _snapshot(db, tables)
        expected = _expected_rows(before, person_map, group_map)
        for name in expected:
            if {row['id']: row for row in after[name]} != {row['id']: row for row in expected[name]}:
                raise CutoverRefused('Post-check failed: unexpected rows or values')
        counts = {
            'users': {
                'mapped': len(person_map),
                'excluded': len(excluded_ids),
                'changed': persons['users'],
                'unchanged': len(person_map) - persons['users'],
            },
            **{
                outcome: {'changed': groups[outcome], 'unchanged': len(before[table]) - groups[outcome]}
                for outcome, table in (
                    ('groups', 'group'),
                    ('memberships', 'group_member'),
                    ('access_grants', 'access_grant'),
                )
            },
        }
        if not apply:
            await transaction.rollback()
    return {
        'mode': 'apply' if apply else 'dry-run',
        'status': 'committed' if apply else 'rolled_back',
        'post_checks': 'passed',
        'counts': counts,
    }


async def _run(person_map, group_map, excluded_ids, *, apply):
    from open_webui.internal.db import AsyncSessionLocal, async_engine

    logging.getLogger('sqlalchemy.engine').setLevel(logging.WARNING)
    async_engine.sync_engine.hide_parameters = True
    try:
        async with AsyncSessionLocal() as db:
            return await run_cutover(db, person_map, group_map, excluded_ids, apply=apply)
    finally:
        await async_engine.dispose()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--person-map', required=True, type=Path)
    parser.add_argument('--group-map', required=True, type=Path)
    parser.add_argument('--excluded', required=True, type=Path)
    parser.add_argument('--apply', action='store_true', help='Commit after all checks pass')
    args = parser.parse_args(argv)
    failure = {'mode': 'apply' if args.apply else 'dry-run', 'status': 'refused', 'counts': {}}
    try:
        person_map, group_map, excluded_ids = (
            json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=_unique_object)
            for path in (args.person_map, args.group_map, args.excluded)
        )
        # Environment imports may log to stdout; keep the report a single JSON object.
        with redirect_stdout(sys.stderr):
            _validate_inputs(person_map, group_map, excluded_ids)
            report = asyncio.run(_run(person_map, group_map, excluded_ids, apply=args.apply))
    except CutoverRefused as error:
        report = {**failure, 'error': str(error)}
    except (OSError, UnicodeError, json.JSONDecodeError):
        report = {**failure, 'error': 'Cannot read cutover JSON inputs'}
    except Exception:
        # Database exceptions can contain row values and connection credentials.
        report = {**failure, 'error': 'Cutover failed'}
    print(json.dumps(report, sort_keys=True))
    return 1 if report['status'] == 'refused' else 0


if __name__ == '__main__':
    sys.exit(main())
