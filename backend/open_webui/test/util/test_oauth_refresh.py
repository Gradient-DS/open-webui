"""Short lived tokens and rotating refresh credentials under concurrent requests."""

import asyncio
import shutil
import socket
import subprocess
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from open_webui.utils import oauth
from redis.asyncio import Redis


@pytest.fixture(scope='module')
def redis_url():
    binary = shutil.which('redis-server')
    if binary is None:
        pytest.skip('redis-server is required for distributed refresh tests')
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    server = subprocess.Popen(
        [binary, '--bind', '127.0.0.1', '--port', str(port), '--save', '', '--appendonly', 'no'],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            assert server.poll() is None, 'redis-server exited before accepting connections'
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=0.1):
                    pass
            except OSError:
                time.sleep(0.01)
            else:
                break
        else:
            pytest.fail('redis-server did not accept connections')
        yield f'redis://127.0.0.1:{port}'
    finally:
        server.terminate()
        server.wait(timeout=5)


@pytest.fixture(params=[oauth.OAuthManager, oauth.OAuthClientManager])
def refresh_env(request, monkeypatch):
    manager = request.param.__new__(request.param)
    manager.app = SimpleNamespace(state=SimpleNamespace(redis=None))
    now = int(time.time())
    session = SimpleNamespace(
        id='session-fern',
        user_id='fern',
        provider='oidc',
        expires_at=now + 300,
        token={'access_token': 'old', 'refresh_token': 'rotating', 'expires_in': 300, 'issued_at': now},
    )
    stored = [session]
    for name in ('get_session_by_id_and_user_id', 'get_session_by_provider_and_user_id', 'get_session_by_id'):
        monkeypatch.setattr(oauth.OAuthSessions, name, AsyncMock(side_effect=lambda *args: stored[0]))

    async def update(id, token):
        stored[0] = SimpleNamespace(**{**vars(session), 'token': token, 'expires_at': token['expires_at']})
        return stored[0]

    monkeypatch.setattr(oauth.OAuthSessions, 'update_session_by_id', AsyncMock(side_effect=update))
    delete = AsyncMock(side_effect=lambda id: stored.__setitem__(0, None))
    monkeypatch.setattr(oauth.OAuthSessions, 'delete_session_by_id', delete)
    token = {'access_token': 'new', 'refresh_token': 'rotated', 'expires_in': 300, 'expires_at': now + 300}
    entered, release = asyncio.Event(), asyncio.Event()

    async def rotate(session):
        entered.set()
        await release.wait()
        return token

    refresh = AsyncMock(side_effect=rotate)
    manager._perform_token_refresh = refresh
    return SimpleNamespace(
        manager=manager,
        session=session,
        stored=stored,
        refresh=refresh,
        delete=delete,
        entered=entered,
        release=release,
        token=token,
        get=lambda force=False: manager.get_oauth_token(
            'fern', 'session-fern' if request.param is oauth.OAuthManager else 'oidc', force
        ),
    )


@pytest.mark.asyncio
async def test_five_minute_token_is_not_refreshed_on_every_request(refresh_env):
    env = refresh_env
    env.release.set()
    assert await env.get() == env.session.token
    env.refresh.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('redis', [False, True])
async def test_concurrent_refresh_rotates_once_and_never_deletes_session(refresh_env, redis, request):
    env = refresh_env
    if redis:
        env.manager.app.state.redis = Redis.from_url(request.getfixturevalue('redis_url'))
    env.session.expires_at = int(time.time()) + 5
    requests = [asyncio.create_task(env.get()) for _ in range(20)]
    await env.entered.wait()
    await asyncio.sleep(0)
    env.release.set()
    assert await asyncio.gather(*requests) == [env.token] * 20
    assert env.refresh.await_count == 1
    env.delete.assert_not_awaited()
    if redis:
        await env.manager.app.state.redis.aclose()


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_cancel_shared_refresh(refresh_env):
    env = refresh_env
    env.session.expires_at = int(time.time()) - 1
    first = asyncio.create_task(env.get())
    await env.entered.wait()
    second = asyncio.create_task(env.get())
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    env.release.set()
    assert await second == env.token
    assert env.refresh.await_count == 1
    env.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_redis_lock_rereads_rotated_token_from_another_worker(refresh_env, redis_url):
    env = refresh_env
    redis = Redis.from_url(redis_url)
    env.manager.app.state.redis = redis
    env.session.expires_at = int(time.time()) - 1
    lock = redis.lock('oauth:refresh:session-fern', timeout=60, thread_local=False)
    await lock.acquire()
    pending = asyncio.create_task(env.get(True))
    await asyncio.sleep(0.02)
    await oauth.OAuthSessions.update_session_by_id(env.session.id, env.token)
    await lock.release()
    assert await pending == env.token
    env.refresh.assert_not_awaited()
    env.delete.assert_not_awaited()
    await redis.aclose()


@pytest.mark.asyncio
async def test_distinct_workers_share_one_rotation_through_redis(refresh_env, redis_url):
    env = refresh_env
    redis = Redis.from_url(redis_url)
    env.manager.app.state.redis = redis
    env.session.expires_at = int(time.time()) - 1
    workers = [
        asyncio.create_task(oauth._coordinated_refresh(env.manager, env.session, force_refresh=True)) for _ in range(2)
    ]
    await env.entered.wait()
    env.release.set()
    assert await asyncio.gather(*workers) == [env.token] * 2
    assert env.refresh.await_count == 1
    env.delete.assert_not_awaited()
    await redis.aclose()


@pytest.mark.asyncio
async def test_redis_failure_does_not_rotate_or_delete(refresh_env):
    env = refresh_env
    env.session.expires_at = int(time.time()) - 1
    redis = SimpleNamespace(lock=lambda *args, **kwargs: (_ for _ in ()).throw(ConnectionError('offline')))
    env.manager.app.state.redis = redis
    assert await env.get() is None
    env.refresh.assert_not_awaited()
    env.delete.assert_not_awaited()


@pytest.mark.parametrize(
    'lifetime,remaining,expected',
    [(300, 200, False), (300, 149, True), (3600, 301, False), (3600, 299, True), (None, 61, False), (None, 59, True)],
)
def test_refresh_margin_is_bounded_by_token_lifetime(lifetime, remaining, expected):
    token = {'expires_in': lifetime} if lifetime else {}
    session = SimpleNamespace(token=token, expires_at=time.time() + remaining)
    assert oauth._token_needs_refresh(session) is expected


@pytest.mark.asyncio
async def test_failed_shared_refresh_deletes_once(refresh_env):
    env = refresh_env
    env.session.expires_at = int(time.time()) - 1
    env.refresh.side_effect = None
    env.refresh.return_value = None
    assert await asyncio.gather(*(env.get() for _ in range(10))) == [None] * 10
    assert env.refresh.await_count == 1
    env.delete.assert_awaited_once_with(env.session.id)
