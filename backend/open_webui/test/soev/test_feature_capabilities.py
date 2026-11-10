import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import Request
from open_webui import config, env
from open_webui.soev import capabilities
from open_webui.utils import agent_v2


@pytest.mark.asyncio
@pytest.mark.parametrize('feature', [capabilities.LIVE_DOCUMENTS, capabilities.LIVE_MAIL])
@pytest.mark.parametrize('present', [False, True])
@pytest.mark.parametrize('enabled', [False, True])
async def test_live_tool_admission(monkeypatch, platform_capabilities, feature, present, enabled):
    monkeypatch.setattr(config, 'SOEV_API_URL', 'https://soev.invalid')
    monkeypatch.setattr(env, 'AGENT_API_ENABLED', True)
    if not present:
        platform_capabilities.discard(feature)
    monkeypatch.setattr(agent_v2.Config, 'get', AsyncMock(return_value=enabled))
    check = agent_v2._live_mail_allowed if feature == capabilities.LIVE_MAIL else agent_v2._live_documents_allowed
    assert await check() is (present and enabled)
    monkeypatch.setattr(env, 'AGENT_API_ENABLED', False)
    assert not await check()


@pytest.mark.asyncio
async def test_images_refused_without_platform_calls(monkeypatch, platform_capabilities):
    monkeypatch.setattr(config, 'SOEV_API_URL', 'https://soev.invalid')
    platform_capabilities.discard('images')
    client = Mock()
    monkeypatch.setattr(agent_v2.identity, 'build_client', client)
    assert await agent_v2._images({}, 'owui:user:alice') == []
    with pytest.raises(agent_v2.ImagesUnavailable):
        await agent_v2._images(
            {'user_message': {'files': [{'type': 'image', 'url': 'data:image/png;base64,aW1hZ2U='}]}},
            'owui:user:alice',
        )
    client.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('present', [False, True])
@pytest.mark.parametrize('enabled', [False, True])
async def test_frontend_config_flags(monkeypatch, platform_capabilities, request, present, enabled):
    main = importlib.import_module('open_webui.main')
    chat_http = request.getfixturevalue('chat_http')

    monkeypatch.setattr(config, 'SOEV_API_URL', 'https://soev.invalid')
    monkeypatch.setattr(env, 'AGENT_API_ENABLED', True)
    monkeypatch.setattr(main, 'AGENT_API_ENABLED', True)
    chat_http.product_config['values']['feature.meetings'] = enabled
    monkeypatch.setattr(main, 'decode_token', lambda token: {'id': 'alice'})
    monkeypatch.setattr(main, 'is_valid_token', AsyncMock(return_value=True))
    alice = SimpleNamespace(id='alice', role='user', person_id='platform:user:00000000-0000-4000-8000-0000000000a1')
    monkeypatch.setattr(main.Users, 'get_user_by_id', AsyncMock(return_value=alice))
    monkeypatch.setattr(main.model_catalog, 'default_models', AsyncMock(return_value='tutorial'))
    saved = {
        'live_mail.enable': enabled,
        'live_documents.enable': enabled,
        'onedrive.enable': True,
        'onedrive.enable_business': enabled,
        'onedrive.enable_personal': enabled,
    }
    monkeypatch.setattr(main.Config, 'get_many', AsyncMock(return_value=saved))
    if not present:
        platform_capabilities.clear()
    request = Request(
        {
            'type': 'http',
            'path': '/api/config',
            'headers': [(b'authorization', b'Bearer test')],
            'app': SimpleNamespace(state=SimpleNamespace(redis=None)),
        }
    )
    result = await main.get_app_config(request)
    for flag in ['enable_live_mail', 'enable_live_documents', 'feature_meetings', 'enable_onedrive_business']:
        assert bool(result['features'][flag]) is (present and enabled)
    assert bool(result['features']['enable_onedrive_integration']) is present
    assert result['features']['feature_agent_api_enabled'] is True
    assert result['default_models'] == 'tutorial'
    assert saved['live_documents.enable'] is enabled


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('claims', 'person', 'valid'),
    [
        ({'id': 'alice', 'purpose': '2fa_pending'}, 'platform:user:00000000-0000-4000-8000-0000000000a1', True),
        ({'id': 'alice', 'purpose': 'skill_file_read'}, 'platform:user:00000000-0000-4000-8000-0000000000a1', True),
        ({'id': 'alice'}, None, True),
        ({'id': 'alice'}, 'platform:user:00000000-0000-4000-8000-0000000000a1', False),
    ],
    ids=['partial', 'scoped', 'unbound', 'revoked'],
)
async def test_config_reads_only_public_settings_without_a_platform_session(monkeypatch, claims, person, valid):
    main = importlib.import_module('open_webui.main')

    monkeypatch.setattr(config, 'SOEV_API_URL', 'https://soev.invalid')
    monkeypatch.setattr(main, 'decode_token', lambda token: claims)
    monkeypatch.setattr(main, 'is_valid_token', AsyncMock(return_value=valid))
    alice = SimpleNamespace(id='alice', role='user', person_id=person)
    monkeypatch.setattr(main.Users, 'get_user_by_id', AsyncMock(return_value=alice))
    monkeypatch.setattr(main.Users, 'has_users', AsyncMock(return_value=True))
    monkeypatch.setattr(main.Config, 'get_many', AsyncMock(return_value={}))
    request = Request(
        {
            'type': 'http',
            'path': '/api/config',
            'headers': [(b'authorization', b'Bearer test')],
            'app': SimpleNamespace(state=SimpleNamespace(redis=None)),
        }
    )
    result = await main.get_app_config(request)
    assert 'enable_api_keys' not in result['features']
    assert 'default_models' not in result
