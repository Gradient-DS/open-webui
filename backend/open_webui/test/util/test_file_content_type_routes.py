from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from open_webui.routers import retrieval


@pytest.mark.asyncio
@pytest.mark.parametrize('declared', ['', 'application/octet-stream', 'application/pdf'])
@pytest.mark.parametrize('filename', ['notes.md', 'table.tsv', 'data.json'])
async def test_url_download_derives_type_from_filename(monkeypatch, declared, filename):
    expected = {'notes.md': 'text/markdown', 'table.tsv': 'text/tab-separated-values', 'data.json': 'application/json'}

    async def chunks(size):
        yield b'content'

    response = MagicMock()
    response.headers = {'Content-Type': declared, 'Content-Disposition': f'attachment; filename="{filename}"'}
    response.content = SimpleNamespace(iter_chunked=chunks)
    session = MagicMock()
    session.get.return_value.__aenter__.return_value = response
    context = MagicMock()
    context.__aenter__.return_value = session
    monkeypatch.setattr(retrieval, 'get_ssrf_safe_session', lambda: context)
    monkeypatch.setattr(retrieval, 'validate_url', lambda url: None)
    result = await retrieval._fetch_url('https://example.com/download', None)
    assert result['filename'] == filename
    assert result['content_type'] == expected[filename]
    assert result['data'] == b'content'
