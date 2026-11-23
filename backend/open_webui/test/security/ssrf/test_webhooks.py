"""Exercise the retained administrator event webhook sender."""

import pytest
from open_webui.utils import webhook

from .conftest import BLOCKED_URLS, INTERNAL, PUBLIC, assert_public_only


@pytest.mark.parametrize('url', BLOCKED_URLS)
@pytest.mark.asyncio
async def test_event_webhook_target_is_blocked(url, http_boundary):
    assert not await webhook.post_webhook('test', url, 'message', {})
    assert http_boundary.sent == []


@pytest.mark.parametrize('attack', ['redirect', 'rebind'])
@pytest.mark.asyncio
async def test_notification_connect_guard(attack, http_boundary, offline, monkeypatch):
    monkeypatch.setattr(webhook, 'AIOHTTP_CLIENT_ALLOW_REDIRECTS', True)
    http_boundary.redirect = INTERNAL if attack == 'redirect' else None
    offline.rebind = attack == 'rebind'
    assert not await webhook.post_webhook('test', PUBLIC, 'message', {})
    assert_public_only(http_boundary)
    assert http_boundary.sent == ([PUBLIC] if attack == 'redirect' else [])
    if attack == 'rebind':
        assert offline.calls['public.example'] >= 2


@pytest.mark.asyncio
async def test_public_webhook_positive_control(http_boundary, caplog):
    caplog.set_level('DEBUG', logger=webhook.__name__)
    message = 'Fictional private message'
    assert await webhook.post_webhook('test', PUBLIC, message, {'content': message})
    records = [record for record in caplog.records if record.name == webhook.__name__]
    assert not any(message in record.getMessage() or PUBLIC in record.getMessage() for record in records)
    assert http_boundary.sent == [PUBLIC]
