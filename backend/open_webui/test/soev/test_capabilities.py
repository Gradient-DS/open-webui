import asyncio
import logging
from unittest.mock import AsyncMock

import httpx
import pytest
from open_webui.soev import capabilities
from open_webui.soev.client import SoevApiError

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
    monkeypatch.setattr(capabilities, '_known', False)
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
async def test_first_failure_returns_empty_retries_after_ten_seconds_and_logs_once(
    cache, identity_http, caplog, failure
):
    requests, responses = identity_http

    async def reply(request):
        if isinstance(failure, Exception):
            raise failure
        return failure

    responses.extend([reply, reply])
    with caplog.at_level(logging.WARNING, logger=capabilities.__name__):
        for _ in range(2):
            assert not await capabilities.refresh()
            request_count = len(requests)
            cache[0] += 9.9
            assert not await capabilities.refresh()
            assert len(requests) == request_count
            cache[0] += 0.1
    records = [record for record in caplog.records if record.name == capabilities.__name__]
    assert len(records) == 1
    assert 'credential' not in records[0].getMessage()
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_success_then_failure_keeps_features_and_retries_after_ten_seconds(cache, identity_http):
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
    assert all(await asyncio.gather(*(capabilities.has(feature) for feature in FEATURES)))
    assert len(requests) == 2
    cache[0] += 9.9
    assert all(await asyncio.gather(*(capabilities.has(feature) for feature in FEATURES)))
    assert len(requests) == 2
    cache[0] += 0.1
    assert await capabilities.has('images')
    assert not await capabilities.has('meetings')
    assert len(requests) == 3
    cache[0] += 59.9
    assert await capabilities.has('images')
    assert len(requests) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize('setting,value', [('SOEV_API_URL', 'https://other.invalid'), ('SOEV_API_KEY', 'other-key')])
async def test_backend_change_resets_last_good_features(
    cache, identity_config, identity_http, monkeypatch, setting, value
):
    identity, _ = identity_config
    requests, responses = identity_http
    responses.append(httpx.Response(200, json={'contract': '0.3.0', 'features': ['images']}))
    assert await capabilities.has('images')
    monkeypatch.setattr(identity.config, setting, value)
    responses.append(httpx.Response(503))
    assert not await capabilities.has('images')
    assert len(requests) == 2
    if setting == 'SOEV_API_URL':
        assert requests[-1].url.host == 'other.invalid'
    else:
        assert requests[-1].headers['authorization'] == 'Bearer other-key'
    cache[0] += 10
    responses.append(httpx.Response(200, json={'contract': '0.3.0', 'features': ['meetings']}))
    assert await capabilities.has('meetings')
    assert not await capabilities.has('images')


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'error',
    [
        httpx.HTTPError('HTTP failure'),
        SoevApiError(503, 'upstream_error', 'API failure'),
        ValueError('invalid payload'),
    ],
)
async def test_expected_client_errors_preserve_last_good_features(cache, monkeypatch, error):

    client = AsyncMock()
    client.get.side_effect = [{'contract': '0.3.0', 'features': ['images']}, error]
    monkeypatch.setattr('open_webui.soev.client.build_service_client', lambda: client)
    assert await capabilities.has('images')
    cache[0] += 60
    assert await capabilities.has('images')
    assert client.get.await_count == 2
    cache[0] += 9
    assert await capabilities.has('images')
    assert client.get.await_count == 2
    cache[0] += 1
    client.get.side_effect = None
    client.get.return_value = {'contract': '0.3.0', 'features': []}
    assert not await capabilities.has('images')
    assert client.get.await_count == 3


@pytest.mark.asyncio
async def test_unexpected_programming_errors_are_not_suppressed(cache, monkeypatch):

    client = AsyncMock()
    client.get.side_effect = RuntimeError('unexpected bug')
    monkeypatch.setattr('open_webui.soev.client.build_service_client', lambda: client)
    with pytest.raises(RuntimeError, match='unexpected bug'):
        await capabilities.refresh()


@pytest.mark.asyncio
async def test_no_backend_does_not_construct_client(cache, monkeypatch):
    from open_webui import config

    monkeypatch.setattr(config, 'SOEV_API_URL', '')
    client = AsyncMock()
    monkeypatch.setattr('open_webui.soev.client.build_service_client', client)
    assert not await capabilities.refresh()
    client.assert_not_called()


@pytest.mark.asyncio
async def test_failed_capability_read_is_unknown_until_success(cache, identity_http):
    _, responses = identity_http
    responses.append(httpx.Response(503))
    assert not await capabilities.has('groups_push')
    assert not capabilities.known()
    cache[0] += 10
    responses.append(httpx.Response(200, json={'contract': '0.3.0', 'features': []}))
    assert not await capabilities.has('groups_push')
    assert capabilities.known()
