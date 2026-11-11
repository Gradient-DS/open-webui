"""Person cutover validates the entire map before writing any binding."""

import pytest
import pytest_asyncio
from open_webui.models.users import User
from open_webui.soev.migrate_persons import cutover_persons
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

FERN = 'platform:user:8a12d036-55dd-46a8-a852-8e22489f1ace'
LARK = 'platform:user:63643f13-46eb-4ecf-baa9-9b91bdab907c'


@pytest_asyncio.fixture
async def person_cutover_db():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(User.__table__.create)
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        db.add_all(
            [
                User(id='fern', name='Fern', email='fern@example.invalid', settings={'ui': {'theme': 'dark'}}),
                User(id='lark', name='Lark', email='lark@example.invalid', oauth={'microsoft': {'sub': 'old-lark'}}),
            ]
        )
        await db.commit()
        yield db
    await engine.dispose()


@pytest.mark.asyncio
async def test_cutover_writes_person_ids_preserves_account_data_and_allows_rollback(person_cutover_db):
    db = person_cutover_db
    mapping = {'fern': FERN, 'lark': LARK}
    assert await cutover_persons(db, mapping) == {'users': 2}
    assert dict((await db.execute(select(User.id, User.person_id))).all()) == mapping
    assert (await db.get(User, 'fern')).settings == {'ui': {'theme': 'dark'}}
    assert (await db.get(User, 'lark')).oauth == {'microsoft': {'sub': 'old-lark'}}
    await db.rollback()
    assert (await db.scalars(select(User.person_id))).all() == [None, None]


@pytest.mark.asyncio
async def test_cutover_is_idempotent_after_commit(person_cutover_db):
    db = person_cutover_db
    mapping = {'fern': FERN, 'lark': LARK}
    assert await cutover_persons(db, mapping) == {'users': 2}
    await db.commit()
    assert await cutover_persons(db, mapping) == {'users': 0}
    assert dict((await db.execute(select(User.id, User.person_id))).all()) == mapping


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'mapping',
    [
        {},
        {'fern': FERN},
        {'fern': FERN, 'lark': LARK, 'unknown': 'platform:user:bb46acee-d11a-480a-8d17-95379ea58840'},
        {'fern': FERN, 'lark': FERN},
        {'fern': FERN, 'lark': 'owui:user:lark'},
        {'fern': FERN, 'lark': 'platform:user:'},
        {'fern': FERN, 'lark': LARK.upper()},
        {'fern': FERN, 'lark': None},
    ],
)
async def test_invalid_map_refuses_before_any_changes(person_cutover_db, mapping):
    db = person_cutover_db
    with pytest.raises(ValueError):
        await cutover_persons(db, mapping)
    assert not db.dirty
    # Committing after the refusal must not persist a partial migration.
    await db.commit()
    assert (await db.scalars(select(User.person_id))).all() == [None, None]


@pytest.mark.asyncio
@pytest.mark.parametrize('mapping', [{'fern': LARK, 'lark': FERN}, {'fern': LARK, 'lark': LARK}])
async def test_existing_person_binding_cannot_be_reassigned(person_cutover_db, mapping):
    db = person_cutover_db
    (await db.get(User, 'lark')).person_id = LARK
    await db.commit()
    with pytest.raises(ValueError):
        await cutover_persons(db, mapping)
    assert not db.dirty
    await db.commit()
    assert dict((await db.execute(select(User.id, User.person_id))).all()) == {'fern': None, 'lark': LARK}


@pytest.mark.asyncio
async def test_explicit_exclusion_stays_unbound_and_cannot_hold_platform_session(person_cutover_db, monkeypatch):
    from fastapi import HTTPException
    from open_webui.soev import sessions

    db = person_cutover_db
    monkeypatch.setattr(sessions.config, 'SOEV_API_URL', 'https://platform.example.invalid')
    assert await cutover_persons(db, {'fern': FERN}, excluded_ids={'lark'}) == {'users': 1}
    await db.commit()
    assert await cutover_persons(db, {'fern': FERN}, excluded_ids={'lark'}) == {'users': 0}
    excluded = await db.get(User, 'lark')
    assert excluded.person_id is None
    with pytest.raises(HTTPException) as error:
        sessions.require_session(excluded)
    assert error.value.status_code == 401
    assert sessions.session_user(excluded, {}) is None
    sessions.require_session(await db.get(User, 'fern'))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'mapping,excluded',
    [
        ({'fern': FERN}, []),
        ({}, ['lark']),
        ({'fern': FERN, 'lark': LARK}, ['lark']),
        ({'fern': FERN}, ['lark', 'unknown']),
        ({'fern': FERN}, 'lark'),
        ({'fern': FERN}, [None]),
    ],
)
async def test_invalid_exclusions_refuse_without_writes(person_cutover_db, mapping, excluded):
    db = person_cutover_db
    with pytest.raises(ValueError):
        await cutover_persons(db, mapping, excluded)
    assert not db.dirty
    await db.commit()
    assert (await db.scalars(select(User.person_id))).all() == [None, None]


@pytest.mark.asyncio
async def test_excluding_bound_account_refuses_without_unbinding(person_cutover_db):
    db = person_cutover_db
    (await db.get(User, 'lark')).person_id = LARK
    await db.commit()
    with pytest.raises(ValueError, match='Existing person binding'):
        await cutover_persons(db, {'fern': FERN}, {'lark'})
    assert not db.dirty
    await db.commit()
    assert dict((await db.execute(select(User.id, User.person_id))).all()) == {'fern': None, 'lark': LARK}
