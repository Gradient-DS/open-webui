import asyncio
import logging
from unittest.mock import AsyncMock

import httpx
import pytest
from open_webui.soev import capabilities

FEATURES = (
    capabilities.LIVE_DOCUMENTS,
    capabilities.LIVE_MAIL,
    capabilities.CLOUD_SYNC,
    capabilities.MEETINGS,
    capabilities.GROUPS_PUSH,
    capabilities.IMAGES,
)


@pytest.fixture
def cache(monkeypatch, identity_config):
    monkeypatch.setattr(capabilities, '_backend', None)
    monkeypatch.setattr(capabilities, '_lock', None)
    monkeypatch.setattr(capabilities, '_features', frozenset())
    monkeypatch.setattr(capabilities, '_expires_at', 0.0)
    monkeypatch.setattr(capabilities, '_warned', False)
    now = [100.0]
    monkeypatch.setattr(capabilities, 'monotonic', lambda: now[0])
    return now


@pytest.mark.asyncio
@pytest.mark.parametrize('feature', FEATURES)
@pytest.mark.parametrize('present', [False, True])
async def test_advertised_features(cache, identity_http, feature, present):
    requests, responses = identity_http
    responses.append(httpx.Response(200, json={'contract': '0.3.0', 'features': [feature] if present else []}))
    assert await capabilities.has(feature) is present
    assert not await capabilities.has('unknown_feature')
    assert len(requests) == 1
    assert requests[0].url.path == '/v1/capabilities'
    assert requests[0].headers['authorization'] == 'Bearer soev_test_cred-runtime_test-secret'
    assert 'x-soev-subject' not in requests[0].headers


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'failure',
    [
        httpx.Response(404),
        httpx.Response(503),
        httpx.Response(200, text='not json'),
        httpx.Response(200, json={'features': []}),
        httpx.Response(200, json={'contract': '0.3.0', 'features': 'images'}),
        httpx.Response(200, json={'contract': '0.3.0', 'features': [42]}),
        httpx.Response(200, json=[]),
        httpx.ConnectError('credential must not be logged'),
        httpx.ReadTimeout('credential must not be logged'),
    ],
)
async def test_failure_disables_features_and_logs_once(cache, identity_http, caplog, failure):
    requests, responses = identity_http

    async def reply(request):
        if isinstance(failure, Exception):
            raise failure
        return failure

    responses.extend([reply, reply])
    with caplog.at_level(logging.WARNING, logger=capabilities.__name__):
        for _ in range(2):
            assert not await capabilities.refresh()
            cache[0] += 60
    records = [record for record in caplog.records if record.name == capabilities.__name__]
    assert len(records) == 1
    assert 'credential' not in records[0].getMessage()
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_ttl_refreshes_once_for_concurrent_callers_and_drops_stale_features(cache, identity_http):
    requests, responses = identity_http
    responses.extend(
        [
            httpx.Response(200, json={'contract': '0.3.0', 'features': list(FEATURES)}),
            httpx.Response(503),
            httpx.Response(200, json={'contract': '0.3.0', 'features': ['images']}),
        ]
    )
    assert all(await asyncio.gather(*(capabilities.has(feature) for feature in FEATURES)))
    assert len(requests) == 1
    cache[0] += 59.9
    assert await capabilities.has('images')
    assert len(requests) == 1
    cache[0] += 0.1
    assert not any(await asyncio.gather(*(capabilities.has(feature) for feature in FEATURES)))
    assert len(requests) == 2
    cache[0] += 60
    assert await capabilities.has('images')
    assert len(requests) == 3


@pytest.mark.asyncio
async def test_backend_change_invalidates_cache(cache, identity_config, identity_http, monkeypatch):
    identity, _ = identity_config
    requests, responses = identity_http
    responses.append(httpx.Response(200, json={'contract': '0.3.0', 'features': ['images']}))
    assert await capabilities.has('images')
    monkeypatch.setattr(identity.config, 'SOEV_API_URL', '')
    assert not await capabilities.has('images')
    monkeypatch.setattr(identity.config, 'SOEV_API_URL', 'https://other.invalid')
    responses.append(httpx.Response(200, json={'contract': '0.3.0', 'features': []}))
    assert not await capabilities.has('images')
    assert len(requests) == 2
    assert requests[-1].url.host == 'other.invalid'


@pytest.mark.asyncio
async def test_no_backend_does_not_construct_client(cache, monkeypatch):
    from open_webui import config
    from open_webui.soev import identity

    monkeypatch.setattr(config, 'SOEV_API_URL', '')
    client = AsyncMock()
    monkeypatch.setattr(identity, 'build_client', client)
    assert not await capabilities.refresh()
    client.assert_not_called()
