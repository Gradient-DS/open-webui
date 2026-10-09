"""Screen secondary URLs advertised in document extraction responses."""

import json

import pytest
import requests
from fastapi import HTTPException

from open_webui.retrieval.loaders import datalab_marker, mineru
from .conftest import BLOCKED_URLS, INTERNAL, PUBLIC, assert_public_only


@pytest.fixture
def document(tmp_path):
    path = tmp_path / 'document.pdf'
    path.write_bytes(b'%PDF-1.0\n')
    return str(path)


def invoke(sink, url, document, http_boundary, monkeypatch):
    if sink == 'marker_poll':
        endpoint = 'http://extract.example/marker'
        http_boundary.responses[endpoint] = (
            200,
            {},
            json.dumps(
                {
                    'success': True,
                    'request_check_url': url,
                }
            ).encode(),
        )
        if url != PUBLIC or not http_boundary.redirect:
            http_boundary.responses.setdefault(url, (200, {}, b'{"status":"failed"}'))
        monkeypatch.setattr(datalab_marker.time, 'sleep', lambda _: None)
        loader = datalab_marker.DatalabMarkerLoader(document, 'test', endpoint, output_format='markdown')
        loader.load()
    else:
        loader = mineru.MinerULoader(document)
        if sink == 'mineru_upload':
            loader._upload_to_presigned_url(url)
        else:
            loader._download_and_extract_zip(url, 'document.pdf')


@pytest.mark.parametrize('sink', ['marker_poll', 'mineru_upload', 'mineru_zip'])
@pytest.mark.parametrize('url', BLOCKED_URLS)
def test_extractor_response_target_is_blocked(sink, url, document, http_boundary, monkeypatch):
    try:
        invoke(sink, url, document, http_boundary, monkeypatch)
    except (HTTPException, requests.RequestException, ValueError):
        pass
    except UnboundLocalError as exc:
        assert isinstance(exc.__context__, requests.exceptions.InvalidSchema)
    assert all(url.startswith('http://extract.example/') for url in http_boundary.sent), http_boundary.sent
    assert_public_only(http_boundary)


@pytest.mark.parametrize('sink', ['marker_poll', 'mineru_upload', 'mineru_zip'])
@pytest.mark.parametrize('attack', ['redirect', 'rebind'])
def test_extractor_response_redirect_is_guarded(sink, attack, document, http_boundary, offline, monkeypatch):
    offline.rebind = attack == 'rebind'
    http_boundary.redirect = PUBLIC + 'next' if offline.rebind else INTERNAL
    try:
        invoke(sink, PUBLIC, document, http_boundary, monkeypatch)
    except (HTTPException, requests.RequestException, ValueError):
        pass
    assert PUBLIC in http_boundary.sent
    assert_public_only(http_boundary)
