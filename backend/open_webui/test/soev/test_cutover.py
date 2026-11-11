"""Fictional offline cutovers exercise the command and its transaction boundary."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import pytest_asyncio
from open_webui.internal.db import Base, _make_async_url
from open_webui.models.access_grants import AccessGrant
from open_webui.models.groups import Group, GroupMember
from open_webui.models.users import User
from open_webui.soev import cutover
from sqlalchemy import event, insert, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

FERN = 'platform:user:8a12d036-55dd-46a8-a852-8e22489f1ace'
LARK = 'platform:user:63643f13-46eb-4ecf-baa9-9b91bdab907c'
OLD_GROUP = 'legacy-meadow'
NEW_GROUP = '57b92fcf-e123-4d1a-bb68-9ced6d1c19b1'
PERSON_MAP = {'fern': FERN, 'lark': LARK}
GROUP_MAP = {OLD_GROUP: f'platform:group:{NEW_GROUP}'}
TABLES = (User.__table__, Group.__table__, GroupMember.__table__, AccessGrant.__table__)
COUNTS = {
    'users': {'mapped': 2, 'excluded': 1, 'changed': 2, 'unchanged': 0},
    'groups': {'changed': 1, 'unchanged': 0},
    'memberships': {'changed': 1, 'unchanged': 0},
    'access_grants': {'changed': 1, 'unchanged': 1},
}


@pytest_asyncio.fixture(params=['sqlite', 'postgresql'])
async def cutover_store(request, tmp_path):
    if request.param == 'postgresql':
        if not os.environ.get('BACKEND_TEST_DATABASE_URL'):
            pytest.skip('BACKEND_TEST_DATABASE_URL is required for PostgreSQL cutover tests')
        url = request.getfixturevalue('postgres_database').render_as_string(hide_password=False)
    else:
        url = f'sqlite:///{tmp_path / "cutover.db"}'
    engine = create_async_engine(_make_async_url(url))
    if request.param == 'sqlite':

        @event.listens_for(engine.sync_engine, 'connect')
        def foreign_keys(connection, _):
            connection.execute('PRAGMA foreign_keys=ON')

    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(lambda db: Base.metadata.create_all(db, tables=list(TABLES)))
        async with sessions() as db:
            db.add_all(
                [
                    User(id='fern', name='Fern', email='fern@example.invalid', settings={'ui': {'theme': 'dark'}}),
                    User(
                        id='lark', name='Lark', email='lark@example.invalid', oauth={'oidc': {'sub': 'fictional-lark'}}
                    ),
                    User(id='demo', name='Demo', email='demo@example.invalid'),
                    Group(id=OLD_GROUP, name='Meadow', permissions={'workspace': {'knowledge': True}}),
                ]
            )
            await db.flush()
            db.add(GroupMember(id='member', group_id=OLD_GROUP, user_id='fern'))
            for id, kind, principal in (('group-grant', 'group', OLD_GROUP), ('user-grant', 'user', 'fern')):
                db.add(
                    AccessGrant(
                        id=id,
                        principal_type=kind,
                        principal_id=principal,
                        resource_type='model',
                        resource_id='fictional-assistant',
                        permission='read',
                        created_at=1,
                    )
                )
            await db.commit()
        yield sessions, url
    finally:
        await engine.dispose()


async def snapshot(sessions):
    async with sessions() as db:
        return {
            table.name: {row['id']: dict(row) for row in (await db.execute(select(table))).mappings()}
            for table in TABLES
        }


@pytest.mark.asyncio
async def test_dry_run_rolls_back_all_rows_and_reports_counts(cutover_store):
    sessions, _ = cutover_store
    before = await snapshot(sessions)
    async with sessions() as db:
        report = await cutover.run_cutover(db, PERSON_MAP, GROUP_MAP, ['demo'])
    assert report == {'mode': 'dry-run', 'status': 'rolled_back', 'post_checks': 'passed', 'counts': COUNTS}
    assert await snapshot(sessions) == before


@pytest.mark.asyncio
async def test_apply_and_same_map_retry_preserve_rows_and_report_no_changes(cutover_store):
    sessions, _ = cutover_store
    before = await snapshot(sessions)
    async with sessions() as db:
        report = await cutover.run_cutover(db, PERSON_MAP, GROUP_MAP, ['demo'], apply=True)
    assert report == {'mode': 'apply', 'status': 'committed', 'post_checks': 'passed', 'counts': COUNTS}
    after = await snapshot(sessions)
    assert {id: row['person_id'] for id, row in after['user'].items()} == {**PERSON_MAP, 'demo': None}
    assert after['user']['lark']['oauth'] == before['user']['lark']['oauth']
    assert after['user']['fern']['settings'] == before['user']['fern']['settings']
    assert after['group'] == {NEW_GROUP: {**before['group'][OLD_GROUP], 'id': NEW_GROUP}}
    assert after['group_member']['member']['group_id'] == NEW_GROUP
    assert after['access_grant']['group-grant']['principal_id'] == NEW_GROUP
    assert after['access_grant']['user-grant'] == before['access_grant']['user-grant']
    async with sessions() as db:
        retry = await cutover.run_cutover(db, PERSON_MAP, GROUP_MAP, ['demo'], apply=True)
    assert retry['status'] == 'committed'
    assert all(count['changed'] == 0 for count in retry['counts'].values())
    assert retry['counts']['users']['excluded'] == 1
    assert await snapshot(sessions) == after
    for persons, groups in (
        ({'fern': LARK, 'lark': FERN}, GROUP_MAP),
        (PERSON_MAP, {OLD_GROUP: 'platform:group:a28d6001-346d-417e-8ee7-0bcd805478af'}),
    ):
        async with sessions() as db:
            with pytest.raises(cutover.CutoverRefused):
                await cutover.run_cutover(db, persons, groups, ['demo'], apply=True)
        assert await snapshot(sessions) == after


@pytest.mark.asyncio
@pytest.mark.parametrize('problem', ['unmapped', 'overlap', 'unknown_exclusion', 'bound_exclusion', 'group_refusal'])
async def test_refusals_roll_back_the_whole_transaction(cutover_store, problem):
    sessions, _ = cutover_store
    persons, groups, excluded = dict(PERSON_MAP), dict(GROUP_MAP), ['demo']
    if problem == 'unmapped':
        persons.pop('lark')
    elif problem == 'overlap':
        excluded.append('lark')
    elif problem == 'unknown_exclusion':
        excluded.append('unknown')
    elif problem == 'bound_exclusion':
        async with sessions() as db:
            await db.execute(
                update(User)
                .where(User.id == 'demo')
                .values(person_id='platform:user:9e0410ca-8183-4c6b-a0a0-22b3ae023938')
            )
            await db.commit()
    else:
        groups = {}
    before = await snapshot(sessions)
    async with sessions() as db:
        with pytest.raises(cutover.CutoverRefused):
            await cutover.run_cutover(db, persons, groups, excluded, apply=True)
    assert await snapshot(sessions) == before


@pytest.mark.asyncio
@pytest.mark.parametrize('problem', ['unexpected_user', 'changed_profile', 'unapplied_group_map'])
async def test_postchecks_refuse_unexpected_rows_or_incomplete_maps(cutover_store, monkeypatch, problem):
    from open_webui.soev import migrate_groups

    original = migrate_groups.cutover_groups

    async def tamper(db, mapping):
        if problem == 'unapplied_group_map':
            return {'groups': 0, 'memberships': 0, 'access_grants': 0}
        counts = await original(db, mapping)
        if problem == 'unexpected_user':
            db.add(User(id='unexpected', name='Fictional', email='unexpected@example.invalid'))
        else:
            await db.execute(update(User).where(User.id == 'fern').values(name='Unexpected'))
        await db.flush()
        return counts

    monkeypatch.setattr(migrate_groups, 'cutover_groups', tamper)
    sessions, _ = cutover_store
    before = await snapshot(sessions)
    async with sessions() as db:
        with pytest.raises(cutover.CutoverRefused, match='Post-check failed'):
            await cutover.run_cutover(db, PERSON_MAP, GROUP_MAP, ['demo'], apply=True)
    assert await snapshot(sessions) == before


@pytest.mark.asyncio
async def test_user_table_lock_blocks_unmapped_inserts_before_person_step(cutover_store, monkeypatch):
    from open_webui.soev import migrate_persons

    sessions, url = cutover_store
    original = migrate_persons.cutover_persons
    checked = False

    async def check_lock(db, mapping, excluded):
        nonlocal checked
        async with sessions() as other:
            if url.startswith('postgresql'):
                await other.execute(text("SET LOCAL lock_timeout = '100ms'"))
            else:
                await other.execute(text('PRAGMA busy_timeout=100'))
            with pytest.raises(DBAPIError) as error:
                await other.execute(insert(User).values(id='late-account', name='Fictional'))
            if url.startswith('postgresql'):
                assert error.value.orig.sqlstate == '55P03'
            else:
                assert 'locked' in str(error.value.orig)
            await other.rollback()
        checked = True
        return await original(db, mapping, excluded)

    monkeypatch.setattr(migrate_persons, 'cutover_persons', check_lock)
    async with sessions() as db:
        await cutover.run_cutover(db, PERSON_MAP, GROUP_MAP, ['demo'])
    assert checked


@pytest.mark.asyncio
async def test_command_uses_database_url_and_prints_only_json(cutover_store, tmp_path):
    sessions, url = cutover_store
    args = []
    for flag, value in (('person-map', PERSON_MAP), ('group-map', GROUP_MAP), ('excluded', ['demo'])):
        path = tmp_path / f'{flag}.json'
        path.write_text(json.dumps(value))
        args.extend([f'--{flag}', str(path)])
    env = {
        **os.environ,
        'DATABASE_URL': url,
        'DATA_DIR': str(tmp_path),
        'PYTHON_DOTENV_DISABLED': '1',
        'GLOBAL_LOG_LEVEL': 'INFO',
        'LOG_FORMAT': 'json',
        'PYTHONPATH': str(Path(__file__).resolve().parents[3]),
    }
    for part in ('TYPE', 'USER', 'PASSWORD', 'HOST', 'PORT', 'NAME'):
        env[f'DATABASE_{part}'] = ''
    before = await snapshot(sessions)
    for apply, changed in ((False, 2), (True, 2), (True, 0)):
        result = subprocess.run(
            [sys.executable, '-m', 'open_webui.soev.cutover', *args, *(['--apply'] if apply else [])],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
        report = json.loads(result.stdout)
        assert set(report) == {'mode', 'status', 'post_checks', 'counts'}
        assert report['status'] == ('committed' if apply else 'rolled_back')
        assert report['counts']['users']['changed'] == changed
        assert report['counts']['users']['excluded'] == 1
        assert 'example.invalid' not in result.stdout
        assert 'Fern' not in result.stdout and 'Meadow' not in result.stdout
        if not apply:
            assert await snapshot(sessions) == before
    committed = await snapshot(sessions)
    (tmp_path / 'person-map.json').write_text(json.dumps({'fern': LARK, 'lark': FERN}))
    result = subprocess.run(
        [sys.executable, '-m', 'open_webui.soev.cutover', *args, '--apply'],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report == {
        'mode': 'apply',
        'status': 'refused',
        'counts': {},
        'error': 'Existing person binding conflicts with person_map',
    }
    assert await snapshot(sessions) == committed


@pytest.mark.parametrize('problem', ['shape', 'duplicate_key', 'duplicate_exclusion', 'invalid_ref', 'missing_file'])
def test_invalid_files_produce_safe_json_refusal(tmp_path, capsys, monkeypatch, problem):
    paths = [tmp_path / name for name in ('persons.json', 'groups.json', 'excluded.json')]
    for path, value in zip(paths, (PERSON_MAP, GROUP_MAP, ['demo'])):
        path.write_text(json.dumps(value))
    if problem == 'shape':
        paths[0].write_text('[]')
    elif problem == 'duplicate_key':
        paths[0].write_text('{"fern": "private@example.invalid", "fern": "private@example.invalid"}')
    elif problem == 'duplicate_exclusion':
        paths[2].write_text('["demo", "demo"]')
    elif problem == 'invalid_ref':
        paths[1].write_text('{"legacy": "private@example.invalid"}')
    else:
        paths[0].unlink()

    async def unexpected(*args, **kwargs):
        pytest.fail('Invalid inputs must not open the database')

    monkeypatch.setattr(cutover, '_run', unexpected)
    assert cutover.main(['--person-map', str(paths[0]), '--group-map', str(paths[1]), '--excluded', str(paths[2])]) == 1
    output = capsys.readouterr().out
    report = json.loads(output)
    assert set(report) == {'mode', 'status', 'counts', 'error'}
    assert report['status'] == 'refused' and report['counts'] == {}
    assert 'example.invalid' not in output


def test_database_failure_does_not_print_exception_values(tmp_path, capsys, monkeypatch):
    paths = [tmp_path / name for name in ('persons.json', 'groups.json', 'excluded.json')]
    for path, value in zip(paths, (PERSON_MAP, GROUP_MAP, ['demo'])):
        path.write_text(json.dumps(value))

    async def fail(*args, **kwargs):
        raise RuntimeError('private@example.invalid / private database credentials')

    monkeypatch.setattr(cutover, '_run', fail)
    assert cutover.main(['--person-map', str(paths[0]), '--group-map', str(paths[1]), '--excluded', str(paths[2])]) == 1
    assert json.loads(capsys.readouterr().out) == {
        'mode': 'dry-run',
        'status': 'refused',
        'counts': {},
        'error': 'Cutover failed',
    }
