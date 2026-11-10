"""First-login account selection trusts only the login's platform person."""

import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import pytest_asyncio
from open_webui import config
from open_webui.constants import ERROR_MESSAGES
from open_webui.models.users import User, Users
from open_webui.soev import login_attach
from open_webui.soev.client import SoevApiError
from open_webui.test.util.test_oauth_binding import assert_logged_in
from open_webui.test.util.test_oauth_binding import login as login
from open_webui.utils import oauth
from sqlalchemy import update

PERSON = 'platform:user:8a12d036-55dd-46a8-a852-8e22489f1ace'
OTHER_PERSON = 'platform:user:63643f13-46eb-4ecf-baa9-9b91bdab907c'


@pytest_asyncio.fixture
async def person_login(login, monkeypatch):
    monkeypatch.setattr(config, 'SOEV_API_URL', 'https://platform.example.invalid')
    monkeypatch.setattr(config, 'SOEV_API_KEY', 'fictional-product-credential')
    platform = SimpleNamespace(response=httpx.Response(200, json={'person_id': PERSON}), requests=[])

    def handle(request):
        platform.requests.append(request)
        if isinstance(platform.response, Exception):
            raise platform.response
        return platform.response

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        monkeypatch.setattr('open_webui.soev.client._shared_client', lambda: http)
        login.platform = platform
        login.session_create = oauth.OAuthSessions.create_session
        login.consume_invite = oauth.Invites.consume_invite_by_email
        yield login


async def migrated_user(ctx, *, person=PERSON, email='old-fern@example.invalid', binding=None):
    await Users.insert_new_user('fern', 'Fern', email, role='user', oauth=binding, db=ctx.db)
    await ctx.db.execute(
        update(User).where(User.id == 'fern').values(person_id=person, settings={'ui': {'theme': 'dark'}})
    )
    await ctx.db.commit()


def assert_no_session(ctx):
    ctx.mint.assert_not_called()
    ctx.session_create.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('binding', [None, {'microsoft': {'sub': 'legacy-fern'}}, {'google': {'label': 'Keycloak'}}])
async def test_first_login_attaches_to_persons_existing_account(person_login, monkeypatch, binding):
    ctx = person_login
    original = copy.deepcopy(binding or {})
    await migrated_user(ctx, binding=binding)
    ctx.runtime.ENABLE_OAUTH_SIGNUP = False
    ctx.runtime.OAUTH_INVITE_REQUIRED = True
    email_lookup = AsyncMock(side_effect=AssertionError('Email must not choose the account'))
    monkeypatch.setattr(Users, 'get_user_by_email', email_lookup)

    assert_logged_in(await ctx.callback(), ctx, 'fern')

    user = await Users.get_user_by_id('fern', db=ctx.db)
    assert user.person_id == PERSON
    assert user.settings.ui == {'theme': 'dark'}
    assert user.email == 'old-fern@example.invalid'
    assert user.oauth == {**original, 'google': {**original.get('google', {}), 'sub': 'subject-fern'}}
    assert await Users.get_num_users(db=ctx.db) == 2
    ctx.consume_invite.assert_not_awaited()
    email_lookup.assert_not_awaited()
    (request,) = ctx.platform.requests
    assert request.url == 'https://platform.example.invalid/v1/me'
    assert request.headers['Authorization'] == 'Bearer fictional-product-credential'
    assert request.headers['X-Soev-User-Token'] == 'fictional-access-token'
    assert not any(header in request.headers for header in ('X-Soev-' + 'Subject', 'X-Soev-Acting-User'))


@pytest.mark.asyncio
async def test_different_subject_for_same_provider_and_person_is_forbidden(person_login, caplog):
    ctx = person_login
    await migrated_user(ctx)
    assert_logged_in(await ctx.callback(), ctx, 'fern')
    ctx.mint.reset_mock()
    ctx.session_create.reset_mock()
    ctx.claims['sub'] = 'second-fern-subject'

    with pytest.raises(SoevApiError) as error:
        await ctx.callback()

    assert error.value.status == 403
    assert error.value.constraint == 'oauth:person_binding'
    assert error.value.detail == 'Login refused'
    assert_no_session(ctx)
    assert (await Users.get_user_by_id('fern', db=ctx.db)).oauth == {'google': {'sub': 'subject-fern'}}
    assert await Users.get_num_users(db=ctx.db) == 2
    record = next(r for r in caplog.records if getattr(r, 'code', None) == 'oauth_person_provider_conflict')
    assert record.provider == 'google'
    assert PERSON not in repr(vars(record))
    assert 'old-fern@example.invalid' not in repr(vars(record))


@pytest.mark.asyncio
async def test_standalone_known_subject_uses_normal_login_without_person_discovery(person_login, monkeypatch):
    ctx = person_login
    monkeypatch.setattr(config, 'SOEV_API_URL', '')
    await migrated_user(ctx, person=None, binding={'google': {'sub': 'subject-fern'}})
    ctx.platform.response = httpx.Response(503)
    assert_logged_in(await ctx.callback(), ctx, 'fern')
    assert ctx.platform.requests == []


@pytest.mark.asyncio
async def test_no_person_account_keeps_new_signup_behaviour(person_login):
    ctx = person_login
    response = await ctx.callback()
    user = await Users.get_user_by_email('fern@example.invalid', db=ctx.db)
    assert_logged_in(response, ctx, user.id)
    assert user.id != 'fern'
    assert user.oauth == {'google': {'sub': 'subject-fern'}}
    assert await Users.get_num_users(db=ctx.db) == 2


@pytest.mark.asyncio
async def test_no_person_account_keeps_signup_policy(person_login):
    ctx = person_login
    ctx.runtime.ENABLE_OAUTH_SIGNUP = False
    response = await ctx.callback()
    assert parse_qs(urlsplit(response.headers['location']).query) == {'error': [ERROR_MESSAGES.ACCESS_PROHIBITED]}
    assert_no_session(ctx)
    assert await Users.get_num_users(db=ctx.db) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('person', [None, OTHER_PERSON])
async def test_same_email_never_attaches_without_person_match(person_login, person, caplog):
    ctx = person_login
    await migrated_user(ctx, person=person, email='fern@example.invalid')
    response = await ctx.callback()
    assert parse_qs(urlsplit(response.headers['location']).query) == {'error': [ERROR_MESSAGES.ACCESS_PROHIBITED]}
    assert_no_session(ctx)
    assert (await Users.get_user_by_id('fern', db=ctx.db)).oauth is None
    assert await Users.get_num_users(db=ctx.db) == 2
    assert any(getattr(r, 'code', None) == 'oauth_email_conflict' for r in caplog.records)


@pytest.mark.asyncio
async def test_email_of_other_person_does_not_override_resolved_person(person_login):
    ctx = person_login
    await migrated_user(ctx)
    await Users.insert_new_user('lark', 'Lark', 'fern@example.invalid', role='user', db=ctx.db)
    await ctx.db.execute(update(User).where(User.id == 'lark').values(person_id=OTHER_PERSON))
    await ctx.db.commit()
    assert_logged_in(await ctx.callback(), ctx, 'fern')
    assert (await Users.get_user_by_id('lark', db=ctx.db)).oauth is None
    assert await Users.get_num_users(db=ctx.db) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'failure',
    [
        httpx.Response(401),
        httpx.Response(403),
        httpx.Response(503),
        httpx.Response(200, json={}),
        httpx.Response(200, json={'person_id': OTHER_PERSON.upper()}),
        httpx.Response(200, json={'person_id': 'owui:user:fern'}),
        httpx.Response(200, json=[]),
        httpx.Response(200, content=b'not json'),
        httpx.ConnectError('fictional connection failure'),
        httpx.ReadTimeout('fictional timeout'),
    ],
)
async def test_discovery_failure_is_retryable_without_signup_or_attachment(person_login, failure):
    ctx = person_login
    await migrated_user(ctx)
    ctx.platform.response = failure
    with pytest.raises(SoevApiError) as error:
        await ctx.callback()
    assert error.value.status == 503
    assert error.value.constraint == 'oauth:person_discovery'
    assert_no_session(ctx)
    assert await Users.get_num_users(db=ctx.db) == 2
    assert (await Users.get_user_by_id('fern', db=ctx.db)).oauth is None
    ctx.consume_invite.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_login_access_token_refuses_before_signup(person_login):
    ctx = person_login
    del ctx.manager._clients['google'].authorize_access_token.return_value['access_token']
    with pytest.raises(SoevApiError) as error:
        await ctx.callback()
    assert error.value.status == 503
    assert_no_session(ctx)
    assert await Users.get_num_users(db=ctx.db) == 1
    assert ctx.platform.requests == []


@pytest.mark.asyncio
async def test_unconfigured_platform_keeps_existing_flow(person_login, monkeypatch):
    ctx = person_login
    monkeypatch.setattr(config, 'SOEV_API_URL', '')
    await ctx.callback()
    assert ctx.platform.requests == []
    user = await Users.get_user_by_email('fern@example.invalid', db=ctx.db)
    assert user.oauth == {'google': {'sub': 'subject-fern'}}


@pytest.mark.asyncio
async def test_concurrent_attachment_cannot_overwrite_winning_binding(person_login, monkeypatch):
    ctx = person_login
    await migrated_user(ctx)
    scalar = ctx.db.scalar

    async def competing_attachment(statement):
        await ctx.db.execute(update(User).where(User.id == 'fern').values(oauth={'google': {'sub': 'winner'}}))
        await ctx.db.commit()
        return await scalar(statement)

    monkeypatch.setattr(ctx.db, 'scalar', competing_attachment)
    with pytest.raises(SoevApiError) as error:
        await login_attach.attach_person_login('google', 'loser', 'fictional-access-token', db=ctx.db)
    assert error.value.status == 503
    assert (await Users.get_user_by_id('fern', db=ctx.db)).oauth == {'google': {'sub': 'winner'}}
