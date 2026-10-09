"""Exercise URL uploads and the shared web/YouTube ingestion helper."""

import pytest

from open_webui.retrieval import utils
from open_webui.routers import retrieval
from .conftest import BLOCKED_URLS, INTERNAL, PUBLIC, assert_public_only

CONFIG = {'web_loader_engine': 'safe_web', 'youtube_language': ['en']}


@pytest.mark.parametrize('url', BLOCKED_URLS)
@pytest.mark.parametrize('sink', ['upload', 'web'])
@pytest.mark.asyncio
async def test_url_ingestion_rejects_target(sink, url, http_boundary):
    with pytest.raises(ValueError, match='invalid'):
        if sink == 'upload':
            await retrieval._fetch_url(url, None)
        else:
            utils._get_content_from_url_sync(None, url, CONFIG)
    assert http_boundary.sent == []


@pytest.mark.parametrize('sink', ['upload', 'web'])
@pytest.mark.parametrize('attack', ['redirect', 'rebind'])
@pytest.mark.asyncio
async def test_ingestion_connect_guard(sink, attack, http_boundary, offline, monkeypatch):
    from open_webui.retrieval.web import utils as web

    for module in (retrieval, utils, web):
        monkeypatch.setattr(module, 'AIOHTTP_CLIENT_ALLOW_REDIRECTS', True)
    http_boundary.redirect = INTERNAL if attack == 'redirect' else None
    offline.rebind = attack == 'rebind'
    try:
        if sink == 'upload':
            await retrieval._fetch_url(PUBLIC, None)
        else:
            utils._get_content_from_url_sync(None, PUBLIC, CONFIG)
    except ValueError as exc:
        assert 'invalid' in str(exc)
    assert_public_only(http_boundary)
    if attack == 'redirect':
        assert http_boundary.sent and set(http_boundary.sent) == {PUBLIC}
    else:
        assert offline.calls['public.example'] >= 2
        assert http_boundary.sent == []


@pytest.mark.parametrize('attack', ['redirect', 'rebind'])
def test_youtube_transcript_connect_guard(attack, http_boundary, offline):
    from open_webui.retrieval.loaders.youtube import YoutubeTranscriptError

    url = 'https://www.youtube.com/watch?v=abcdefghijk'
    if attack == 'redirect':
        http_boundary.responses[url] = (302, {'Location': INTERNAL}, b'')
    else:
        offline.rebind = True
    try:
        utils._get_content_from_url_sync(None, url, CONFIG)
    except YoutubeTranscriptError:
        pass
    assert_public_only(http_boundary)
    assert INTERNAL not in http_boundary.sent
