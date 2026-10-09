"""Cover image edits, generated-image URLs and chat image conversion."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest
from fastapi import HTTPException

from open_webui.models.config import Config
from open_webui.routers import images
from open_webui.utils import files
from .conftest import BLOCKED_URLS, INTERNAL, PUBLIC, assert_public_only


@pytest.fixture
def image_config(monkeypatch):
    monkeypatch.setattr(Config, 'get', AsyncMock(return_value=None))
    monkeypatch.setattr(
        images,
        'get_image_config',
        AsyncMock(
            return_value=SimpleNamespace(
                IMAGE_EDIT_SIZE='',
                IMAGE_EDIT_MODEL='test',
                IMAGE_EDIT_ENGINE='none',
            )
        ),
    )
    monkeypatch.setattr(images, 'get_file_content_by_id', AsyncMock(side_effect=HTTPException(404)))
    monkeypatch.setattr(files, 'get_image_base64_from_file_id', AsyncMock(return_value=None))


async def fetch_image(sink, url, user):
    if sink == 'generated':
        return await images.get_image_data(url)
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
    request = SimpleNamespace(base_url='https://webui.example/')
    try:
        return await images.image_edits(request, images.EditImageForm(image=url, prompt='edit'), user=user)
    except HTTPException as exc:
        assert exc.status_code in (400, 404)


@pytest.mark.parametrize('sink', ['generated', 'chat', 'edit'])
@pytest.mark.parametrize('url', BLOCKED_URLS)
@pytest.mark.asyncio
async def test_image_target_is_blocked(sink, url, image_config, http_boundary, user, monkeypatch):
    async with aiohttp.ClientSession() as session:
        monkeypatch.setattr(images, 'get_session', AsyncMock(return_value=session))
        await fetch_image(sink, url, user)
    assert http_boundary.sent == []


@pytest.mark.parametrize('sink', ['generated', 'chat', 'edit'])
@pytest.mark.parametrize('attack', ['redirect', 'rebind'])
@pytest.mark.asyncio
async def test_image_connect_guard(sink, attack, image_config, http_boundary, offline, user, monkeypatch):
    for module in (images, files):
        monkeypatch.setattr(module, 'AIOHTTP_CLIENT_ALLOW_REDIRECTS', True)
    http_boundary.redirect = INTERNAL if attack == 'redirect' else None
    offline.rebind = attack == 'rebind'
    async with aiohttp.ClientSession() as session:
        monkeypatch.setattr(images, 'get_session', AsyncMock(return_value=session))
        await fetch_image(sink, PUBLIC, user)
    if attack == 'rebind':
        assert offline.calls['public.example'] >= 2
    else:
        assert PUBLIC in http_boundary.sent
    assert_public_only(http_boundary)


@pytest.mark.asyncio
async def test_public_generated_image_positive_control(image_config, http_boundary, monkeypatch):
    http_boundary.responses[PUBLIC] = (200, {'Content-Type': 'image/png'}, b'image')
    async with aiohttp.ClientSession() as session:
        monkeypatch.setattr(images, 'get_session', AsyncMock(return_value=session))
        assert await images.get_image_data(PUBLIC) == (b'image', 'image/png')
    assert http_boundary.sent == [PUBLIC]
