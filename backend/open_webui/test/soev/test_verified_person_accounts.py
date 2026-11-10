"""Every platform OAuth session has a durable, uniquely verified person binding."""

import asyncio

import httpx
import pytest
import pytest_asyncio
from open_webui import config
from open_webui.internal import db as database
from open_webui.models.auths import Auth
from open_webui.models.users import User, Users
from open_webui.soev import login_attach
from open_webui.soev.client import SoevApiError
from open_webui.test.soev.test_login_attach import PERSON, assert_no_session, migrated_user
from open_webui.test.soev.test_login_attach import person_login as person_login
from open_webui.test.util.test_oauth_binding import assert_logged_in
from open_webui.test.util.test_oauth_binding import login as login
from open_webui.utils import oauth
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.mark.asyncio
async def test_known_null_person_is_verified_and_bound_before_login(person_login):
    ctx = person_login
    await migrated_user(ctx, person=None, binding={'google': {'sub': 'subject-fern'}})
    assert_logged_in(await ctx.callback(), ctx, 'fern')
    assert len(ctx.platform.requests) == 1
    assert (await Users.get_user_by_id('fern', db=ctx.db)).person_id == PERSON
    assert await Users.get_num_users(db=ctx.db) == 2


@pytest.mark.asyncio
async def test_known_null_person_discovery_failure_refuses_session(person_login):
    ctx = person_login
    await migrated_user(ctx, person=None, binding={'google': {'sub': 'subject-fern'}})
    ctx.platform.response = httpx.Response(503)
    with pytest.raises(SoevApiError) as error:
        await ctx.callback()
    assert error.value.status == 503
    assert_no_session(ctx)
    assert (await Users.get_user_by_id('fern', db=ctx.db)).person_id is None


@pytest.mark.asyncio
async def test_known_null_person_cannot_claim_another_accounts_person(person_login):
    ctx = person_login
    await migrated_user(ctx, person=None, binding={'google': {'sub': 'subject-fern'}})
    await Users.insert_new_user('lark', 'Lark', 'lark@example.invalid', role='user', db=ctx.db)
    await ctx.db.execute(update(User).where(User.id == 'lark').values(person_id=PERSON))
    await ctx.db.commit()
    with pytest.raises(SoevApiError) as error:
        await ctx.callback()
    assert error.value.status == 403
    assert_no_session(ctx)
    assert (await Users.get_user_by_id('fern', db=ctx.db)).person_id is None
    assert (await Users.get_user_by_id('lark', db=ctx.db)).person_id == PERSON
    assert await Users.get_num_users(db=ctx.db) == 3


@pytest.mark.asyncio
async def test_signup_initial_insert_contains_verified_person_and_matching_credential(person_login):
    ctx = person_login
    await ctx.db.execute(
        text("""CREATE TRIGGER require_signup_person BEFORE INSERT ON user
        WHEN NEW.person_id IS NULL BEGIN SELECT RAISE(ABORT, 'person required at insert'); END""")
    )
    await ctx.db.commit()
    response = await ctx.callback()
    user = await Users.get_user_by_email('fern@example.invalid', db=ctx.db)
    assert_logged_in(response, ctx, user.id)
    assert len(ctx.platform.requests) == 1
    assert user.person_id == PERSON
    assert (await ctx.db.get(Auth, user.id)).email == user.email
    assert await ctx.db.scalar(select(func.count()).select_from(Auth)) == 1


@pytest.mark.asyncio
async def test_signup_email_collision_never_leaves_a_credential_or_unbound_user(person_login):
    ctx = person_login
    await migrated_user(ctx, person=None, email='fern@example.invalid')
    response = await ctx.callback()
    assert 'error=' in response.headers['location']
    assert_no_session(ctx)
    assert await Users.get_num_users(db=ctx.db) == 2
    assert await ctx.db.scalar(select(func.count()).select_from(Auth)) == 0
    assert (await Users.get_user_by_id('fern', db=ctx.db)).person_id is None


@pytest.mark.asyncio
async def test_callback_resolves_creation_that_wins_after_person_lookup(person_login, monkeypatch):
    ctx = person_login
    insert = oauth.insert_oauth_account
    winner = []

    async def concurrent_insert(**values):
        winner.append(await insert(**values))
        return await insert(**values)

    monkeypatch.setattr(oauth, 'insert_oauth_account', concurrent_insert)
    assert_logged_in(await ctx.callback(), ctx, winner[0].id)
    assert await Users.get_num_users(db=ctx.db) == 2
    assert await ctx.db.scalar(select(func.count()).select_from(Auth)) == 1
    assert winner[0].person_id == PERSON


@pytest_asyncio.fixture
async def concurrent_accounts(monkeypatch, tmp_path):
    monkeypatch.setattr(config, 'SOEV_API_URL', 'https://platform.example.invalid')
    monkeypatch.setattr(database, 'DATABASE_ENABLE_SESSION_SHARING', True)
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path}/accounts.db')
    async with engine.begin() as connection:
        await connection.run_sync(User.__table__.create)
        await connection.run_sync(Auth.__table__.create)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('same_email', [False, True])
@pytest.mark.parametrize('same_sub', [False, True])
async def test_concurrent_signups_converge_on_unique_person_without_orphan_rows(
    concurrent_accounts, same_email, same_sub
):
    sessions = concurrent_accounts
    start = asyncio.Event()

    async def create(email, sub):
        async with sessions() as db:
            await start.wait()
            return await login_attach.insert_oauth_account(
                provider='oidc',
                sub=sub,
                person_id=PERSON,
                email=email,
                name='Fern',
                password='fictional-password-hash',
                profile_image_url='/user.png',
                role='user',
                oauth={'oidc': {'sub': sub}},
                db=db,
            )

    first = asyncio.create_task(create('fern@example.invalid', 'subject-fern'))
    second = asyncio.create_task(
        create(
            'fern@example.invalid' if same_email else 'new-fern@example.invalid',
            'subject-fern' if same_sub else 'another-subject',
        )
    )
    start.set()
    results = await asyncio.gather(first, second, return_exceptions=True)
    errors = [result for result in results if isinstance(result, Exception)]
    if same_sub:
        assert errors == []
        assert results[0].id == results[1].id
    else:
        assert len(errors) == 1
        assert isinstance(errors[0], SoevApiError) and errors[0].status == 403
    async with sessions() as db:
        users = (await db.scalars(select(User))).all()
        credentials = (await db.scalars(select(Auth))).all()
        assert len(users) == len(credentials) == 1
        assert users[0].id == credentials[0].id
        assert users[0].person_id == PERSON
