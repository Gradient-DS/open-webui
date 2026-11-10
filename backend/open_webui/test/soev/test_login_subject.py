"""Mutable claims and stale subject bindings cannot select another person's account."""

import importlib
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI
from open_webui import config
from open_webui.constants import ERROR_MESSAGES
from open_webui.models.config import Config
from open_webui.models.users import Users
from open_webui.soev import login_attach
from open_webui.soev.client import SoevApiError
from open_webui.test.soev.test_login_attach import (
    OTHER_PERSON,
    PERSON,
    assert_no_session,
    migrated_user,
)
from open_webui.test.soev.test_login_attach import person_login as person_login
from open_webui.test.util.test_oauth_binding import assert_logged_in
from open_webui.test.util.test_oauth_binding import login as login
from open_webui.utils import oauth


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'environment,stored',
    [('email', 'sub'), ('sub', 'email'), (None, 'preferred_username'), (None, 'oid')],
)
async def test_startup_refuses_mutable_subject_claim(monkeypatch, environment, stored):
    main = importlib.import_module('open_webui.main')

    monkeypatch.setattr(config, 'SOEV_API_URL', 'https://platform.example.invalid')
    monkeypatch.setattr(config, 'OAUTH_SUB_CLAIM', environment)
    monkeypatch.setattr(Config, 'get', AsyncMock(return_value=stored))
    monkeypatch.setattr(main, 'RESET_CONFIG_ON_START', False)
    monkeypatch.setattr(main, 'THREAD_POOL_SIZE', 0)
    monkeypatch.setattr(main, 'start_logger', lambda: None)
    monkeypatch.setattr(main, 'install_log_context', lambda: None)
    monkeypatch.setattr(main, 'import_legacy_config_json', AsyncMock())
    monkeypatch.setattr(main, 'seed_registered_defaults', AsyncMock())
    initialize = AsyncMock()
    monkeypatch.setattr(main, 'initialize_runtime_config', initialize)

    with pytest.raises(ValueError, match='OAUTH_SUB_CLAIM and oauth.sub_claim must be "sub" or unset'):
        async with main.lifespan(FastAPI()):
            pytest.fail('Unsafe subject configuration must not start the application')
    initialize.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('claim', [None, '', 'sub'])
async def test_platform_accepts_only_sub_or_unset_subject_configuration(monkeypatch, claim):
    monkeypatch.setattr(config, 'SOEV_API_URL', 'https://platform.example.invalid')
    monkeypatch.setattr(config, 'OAUTH_SUB_CLAIM', claim)
    monkeypatch.setattr(Config, 'get', AsyncMock(return_value=claim))
    await login_attach.validate_login_config()


@pytest.mark.asyncio
async def test_standalone_keeps_custom_subject_configuration(monkeypatch):
    monkeypatch.setattr(config, 'SOEV_API_URL', '')
    monkeypatch.setattr(config, 'OAUTH_SUB_CLAIM', 'email')
    read = AsyncMock(side_effect=AssertionError('Platform validation must be disabled'))
    monkeypatch.setattr(Config, 'get', read)
    await login_attach.validate_login_config()
    read.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('claim', ['email', 'preferred_username'])
async def test_callback_always_attaches_verified_sub_despite_custom_claims(person_login, monkeypatch, claim):
    ctx = person_login
    ctx.runtime.OAUTH_SUB_CLAIM = claim
    ctx.claims['preferred_username'] = 'fern-label'
    monkeypatch.setitem(oauth.OAUTH_PROVIDERS, 'google', {'sub_claim': 'email'})
    await migrated_user(ctx)

    assert_logged_in(await ctx.callback(), ctx, 'fern')

    user = await Users.get_user_by_id('fern', db=ctx.db)
    assert user.oauth == {'google': {'sub': 'subject-fern'}}
    assert user.person_id == PERSON
    assert len(ctx.platform.requests) == 1


@pytest.mark.asyncio
async def test_userinfo_cannot_override_verified_token_subject(person_login):
    ctx = person_login
    await migrated_user(ctx)
    client = ctx.manager._clients['google']
    client.authorize_access_token.return_value['userinfo'] = {'sub': 'subject-fern'}
    client.userinfo.return_value = {**ctx.claims, 'sub': 'different-userinfo-subject'}

    assert_logged_in(await ctx.callback(), ctx, 'fern')

    user = await Users.get_user_by_id('fern', db=ctx.db)
    assert user.oauth == {'google': {'sub': 'subject-fern'}}


@pytest.mark.asyncio
async def test_missing_verified_token_sub_never_falls_back_to_email_or_userinfo(person_login, monkeypatch):
    ctx = person_login
    ctx.runtime.OAUTH_SUB_CLAIM = 'email'
    monkeypatch.setitem(oauth.OAUTH_PROVIDERS, 'google', {'sub_claim': 'email'})
    ctx.manager._clients['google'].authorize_access_token.return_value['userinfo'] = {'email': 'fern@example.invalid'}
    response = await ctx.callback()
    assert parse_qs(urlsplit(response.headers['location']).query) == {'error': [ERROR_MESSAGES.INVALID_CRED]}
    assert_no_session(ctx)
    assert ctx.platform.requests == []
    assert await Users.get_num_users(db=ctx.db) == 1


@pytest.mark.asyncio
async def test_recycled_email_cannot_select_a_retained_email_subject(person_login):
    ctx = person_login
    binding = {'google': {'sub': 'old-fern@example.invalid'}}
    await migrated_user(ctx, binding=binding)
    ctx.runtime.OAUTH_SUB_CLAIM = 'email'
    ctx.claims.update(sub='subject-lark', email='old-fern@example.invalid')
    ctx.platform.response = httpx.Response(200, json={'person_id': OTHER_PERSON})

    response = await ctx.callback()

    assert parse_qs(urlsplit(response.headers['location']).query) == {'error': [ERROR_MESSAGES.ACCESS_PROHIBITED]}
    assert_no_session(ctx)
    assert len(ctx.platform.requests) == 1
    assert (await Users.get_user_by_id('fern', db=ctx.db)).oauth == binding
    assert await Users.get_num_users(db=ctx.db) == 2


@pytest.mark.asyncio
async def test_known_subject_with_different_login_person_refuses_without_fallback(person_login, caplog):
    ctx = person_login
    binding = {'google': {'sub': 'subject-fern'}}
    await migrated_user(ctx, binding=binding)
    ctx.platform.response = httpx.Response(200, json={'person_id': OTHER_PERSON})

    with pytest.raises(SoevApiError) as error:
        await ctx.callback()

    assert error.value.status == 403
    assert error.value.constraint == 'oauth:person_binding'
    assert error.value.detail == 'Login refused'
    assert_no_session(ctx)
    ctx.consume_invite.assert_not_awaited()
    user = await Users.get_user_by_id('fern', db=ctx.db)
    assert user.person_id == PERSON
    assert user.oauth == binding
    assert await Users.get_num_users(db=ctx.db) == 2
    record = next(r for r in caplog.records if getattr(r, 'code', None) == 'oauth_person_mismatch')
    assert record.provider == 'google'
    assert all(value not in repr(vars(record)) for value in (PERSON, OTHER_PERSON, 'old-fern@example.invalid'))


@pytest.mark.asyncio
async def test_known_subject_with_matching_login_person_still_logs_in(person_login):
    ctx = person_login
    binding = {'google': {'sub': 'subject-fern'}}
    await migrated_user(ctx, binding=binding)
    ctx.runtime.ENABLE_OAUTH_SIGNUP = False
    ctx.runtime.OAUTH_INVITE_REQUIRED = True

    assert_logged_in(await ctx.callback(), ctx, 'fern')

    assert len(ctx.platform.requests) == 1
    assert ctx.platform.requests[0].headers['X-Soev-User-Token'] == 'fictional-access-token'
    assert (await Users.get_user_by_id('fern', db=ctx.db)).oauth == binding
    assert await Users.get_num_users(db=ctx.db) == 2
    ctx.consume_invite.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('response', [httpx.Response(503), httpx.Response(200, json={})])
async def test_known_person_binding_requires_successful_discovery(person_login, response):
    ctx = person_login
    binding = {'google': {'sub': 'subject-fern'}}
    await migrated_user(ctx, binding=binding)
    ctx.platform.response = response

    with pytest.raises(SoevApiError) as error:
        await ctx.callback()

    assert error.value.status == 503
    assert_no_session(ctx)
    assert (await Users.get_user_by_id('fern', db=ctx.db)).oauth == binding
    assert await Users.get_num_users(db=ctx.db) == 2
