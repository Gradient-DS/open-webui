"""User webhook retirement preserves browser notifications and admin event delivery."""

import importlib.util
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.routing import APIRoute


@pytest.mark.parametrize('enabled', ['true', 'false'])
def test_user_webhook_routes_and_settings_cannot_be_restored(monkeypatch, enabled):
    from open_webui import config
    from open_webui.main import app
    from open_webui.routers import auths, users
    from open_webui.services.remaining_request_bodies import UserUISettings
    from open_webui.soev import product_config
    from open_webui.tools import builtin

    monkeypatch.setenv('ENABLE_USER_WEBHOOKS', enabled)
    monkeypatch.setenv('USER_PERMISSIONS_FEATURES_USER_WEBHOOKS', enabled)
    paths = {route.path for route in app.routes if isinstance(route, APIRoute)}
    assert not any(path.startswith('/api/v1/notifications') for path in paths)
    assert '/api/v1/feedback/report' in paths
    assert '/api/v1/users/user/settings/update' in paths
    assert 'ui.enable_user_webhooks' not in config.DEFAULT_CONFIG
    assert not product_config.owns('ui.enable_user_webhooks')
    assert not product_config.owns('permissions.features.webhooks')
    assert 'webhooks' not in config.DEFAULT_USER_PERMISSIONS['features']
    assert 'webhooks' not in users.FeaturesPermissions.model_fields
    assert 'ENABLE_USER_WEBHOOKS' not in auths.AdminConfig.model_fields
    assert 'notifications' not in UserUISettings.model_fields
    assert not hasattr(builtin, 'notify')
    assert importlib.util.find_spec('open_webui.routers.notifications') is None
    assert importlib.util.find_spec('open_webui.utils.notifications') is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'event_name', ['chat.finished', 'chat.failed', 'calendar.alert', 'retrieval.content.process_failed']
)
async def test_notification_events_keep_admin_delivery_without_reading_user_targets(monkeypatch, event_name):
    from open_webui import events
    from open_webui.models.users import Users

    user_lookup = AsyncMock()
    monkeypatch.setattr(Users, 'get_user_by_id', user_lookup)
    webhook_dispatch = Mock()
    monkeypatch.setattr(events, 'schedule_webhook_dispatch', webhook_dispatch)
    monkeypatch.setattr(events, 'schedule_event_function_dispatch', Mock())
    app = SimpleNamespace(state=SimpleNamespace())

    await events.publish_event(app, event_name, actor={'id': 'fictional-user'}, data={'user_id': 'fictional-user'})

    webhook_dispatch.assert_called_once()
    assert webhook_dispatch.call_args.args[0] is app
    assert webhook_dispatch.call_args.args[1].event == event_name
    user_lookup.assert_not_awaited()
    assert {type(sink).__name__ for sink in events.EVENT_SINKS} == {
        'SocketSessionEventSink',
        'EventFunctionSink',
        'WebhookEventSink',
    }
