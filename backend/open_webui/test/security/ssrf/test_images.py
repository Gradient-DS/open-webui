"""Cover image edits, generated-image URLs and chat image conversion."""

from unittest.mock import AsyncMock

import pytest
from open_webui.models.config import Config
from open_webui.utils import files

from .conftest import BLOCKED_URLS, INTERNAL, PUBLIC, assert_public_only


@pytest.fixture
def image_config(monkeypatch):
    monkeypatch.setattr(Config, 'get', AsyncMock(return_value=None))
    monkeypatch.setattr(files, 'get_image_base64_from_file_id', AsyncMock(return_value=None))


async def fetch_image(sink, url, user):
    if sink == 'chat':
        from open_webui.utils.middleware import convert_url_images_to_base64

        return await convert_url_images_to_base64(
            {
                'messages': [
                    {
                        'role': 'user',
                        'content': [{'type': 'image_url', 'image_url': {'url': url}}],
                    }
                ]
            },
            user=user,
        )


@pytest.mark.parametrize('sink', ['chat'])
@pytest.mark.parametrize('url', BLOCKED_URLS)
@pytest.mark.asyncio
async def test_image_target_is_blocked(sink, url, image_config, http_boundary, user, monkeypatch):
    await fetch_image(sink, url, user)
    assert http_boundary.sent == []


@pytest.mark.parametrize('sink', ['chat'])
@pytest.mark.parametrize('attack', ['redirect', 'rebind'])
@pytest.mark.asyncio
async def test_image_connect_guard(sink, attack, image_config, http_boundary, offline, user, monkeypatch):
    for module in (files,):
        monkeypatch.setattr(module, 'AIOHTTP_CLIENT_ALLOW_REDIRECTS', True)
    http_boundary.redirect = INTERNAL if attack == 'redirect' else None
    offline.rebind = attack == 'rebind'
    await fetch_image(sink, PUBLIC, user)
    if attack == 'rebind':
        assert offline.calls['public.example'] >= 2
    else:
        assert PUBLIC in http_boundary.sent
    assert_public_only(http_boundary)
