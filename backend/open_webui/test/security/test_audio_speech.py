"""Drive the speech fixture through the real handler and platform HTTP client."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from . import test_stub_upstream
from .attack import crossuser, plane, seeds
from .test_route_coverage import waivers
from .test_stub_upstream import get_json

stub = test_stub_upstream.stub
ROUTE = 'POST /api/v1/audio/speech'
PATH = '/api/v1/audio/speech'


@pytest_asyncio.fixture
async def speech_client(stub, monkeypatch):
    from open_webui.main import soev_api_error_handler
    from open_webui.routers import audio
    from open_webui.soev import client, primitives
    from open_webui.utils.auth import get_verified_user

    app = FastAPI()
    app.include_router(audio.router, prefix='/api/v1/audio')
    app.add_exception_handler(client.SoevApiError, soev_api_error_handler)
    actor = SimpleNamespace(id='fictional-owner', role='admin')
    app.dependency_overrides[get_verified_user] = lambda: actor
    monkeypatch.setattr(audio, 'is_feature_enabled', lambda feature: True)
    monkeypatch.setattr(audio, 'has_permission', AsyncMock(return_value=True))
    monkeypatch.setattr(audio.Config, 'get', AsyncMock(return_value={}))
    monkeypatch.setattr(audio, 'publish_event', AsyncMock())
    token = AsyncMock(return_value='fictional-platform-token')
    monkeypatch.setattr(primitives, 'user_token', token)
    monkeypatch.setattr(
        primitives.identity, 'build_client', lambda **kwargs: client.SoevClient(stub[1], 'fictional-product-key')
    )
    async with httpx.AsyncClient(trust_env=False) as outbound:
        monkeypatch.setattr(client, '_shared_client', lambda: outbound)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owui.invalid') as http:
            yield http, actor, token, app


def speech_parameters():
    entry = next(entry for entry in seeds.SURFACE['field'] if entry['route'] == ROUTE)
    return seeds.resolve_parameters(None, surface={'parameter': [], 'field': [entry]}, spec={'paths': {}})


@pytest.mark.parametrize('mode', ['waive', 'drive'])
@pytest.mark.asyncio
async def test_speech_fixture_reaches_the_handler_for_owner_and_second_user(speech_client, stub, monkeypatch, mode):
    monkeypatch.setenv('RUNTIME_SOEV_BACKED', mode)
    http, actor, token, _ = speech_client
    parameters = speech_parameters()
    body = crossuser._benign(ROUTE, parameters, seeds.SPEC)
    assert body['json']['input'].startswith('Fictional speech probe ')
    assert '{token}' not in body['json']['input']
    assert set(body['json']) == {'input', 'response_format'}
    assert plane._target(ROUTE, parameters, plane.Seeding()) == PATH
    routes, admins = crossuser.targets()
    assert ROUTE in routes and ROUTE not in admins
    get_json(stub, '/_recorded/reset')
    for user_id, role in [('fictional-owner', 'admin'), ('fictional-second-user', 'user')]:
        actor.id, actor.role = user_id, role
        response = await http.post(PATH, **body)
        assert response.status_code == 200, response.text
        assert response.headers['content-type'] == 'audio/mpeg'
        assert response.content.startswith(b'ID3')
        token.assert_awaited_with(f'owui:user:{user_id}')
    records = get_json(stub, '/_recorded')['requests']
    assert len(records) == 2
    assert all(record['path'] == '/v1/audio/speech' and record['body'] == body['json'] for record in records)
    assert (ROUTE in waivers()) is (mode == 'waive')
    assert (ROUTE in crossuser.expected_refusals()) is (mode == 'waive')


@pytest.mark.asyncio
async def test_public_speech_refusal_is_observed_before_upstream_io(speech_client, stub, monkeypatch):
    monkeypatch.setenv('RUNTIME_SOEV_BACKED', 'waive')
    http, actor, token, _ = speech_client
    token.return_value = None
    get_json(stub, '/_recorded/reset')
    body = crossuser._benign(ROUTE, speech_parameters(), seeds.SPEC)
    for user_id, role in [('fictional-owner', 'admin'), ('fictional-second-user', 'user')]:
        actor.id, actor.role = user_id, role
        response = await http.post(PATH, **body)
        assert crossuser.refused_as_expected(crossuser.expected_refusals()[ROUTE], response.status_code, response.text)
        assert response.json()['detail']['code'] == 'sign_in'
    assert not get_json(stub, '/_recorded')['requests']


@pytest.mark.asyncio
async def test_speech_refuses_anonymous_callers_before_platform_io(speech_client, stub):
    http, _, token, app = speech_client
    app.dependency_overrides.clear()
    get_json(stub, '/_recorded/reset')
    response = await http.post(PATH, **crossuser._benign(ROUTE, speech_parameters(), seeds.SPEC))
    assert response.status_code in {401, 403}
    token.assert_not_awaited()
    assert not get_json(stub, '/_recorded')['requests']


@pytest.mark.parametrize('mode', ['waive', 'drive'])
def test_speech_coverage_requires_private_hits_and_rejects_stale_public_waiver(monkeypatch, mode):
    from . import test_route_coverage as coverage

    monkeypatch.setenv('RUNTIME_SOEV_BACKED', mode)
    monkeypatch.setattr(coverage, 'operations', lambda: {ROUTE})
    hits = {'hits': []}
    monkeypatch.setattr(coverage, 'evidence', lambda: hits)
    if mode == 'waive':
        coverage.assert_every_route_is_driven_or_waived()
        hits['hits'].append(ROUTE)
        with pytest.raises(AssertionError, match='waived but actually driven'):
            coverage.assert_no_waiver_covers_a_route_the_plane_actually_reached()
    else:
        with pytest.raises(AssertionError, match=ROUTE):
            coverage.assert_every_route_is_driven_or_waived()
        hits['hits'].append(ROUTE)
        coverage.assert_every_route_is_driven_or_waived()
        coverage.assert_no_waiver_covers_a_route_the_plane_actually_reached()
