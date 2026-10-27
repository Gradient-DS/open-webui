"""Catalog download authorization and original byte streaming through both file routes."""

from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from open_webui.test.soev.test_knowledge_store import env, seed  # noqa: F401


@pytest.mark.asyncio
@pytest.mark.parametrize('named', [False, True])
@pytest.mark.parametrize('case', ['pdf', 'attachment', 'text', 'missing', 'unreadable', 'document_hidden', 'revoked'])
async def test_catalog_content_routes(env, monkeypatch, named, case):  # noqa: F811
    from open_webui.models import knowledge
    from open_webui.routers import files

    monkeypatch.setattr(knowledge, 'Knowledges', env.store)
    if case == 'unreadable':
        await seed(env, 'private', owner='bob')
    if case != 'missing':
        env.api.add_document(
            'private' if case == 'unreadable' else 'kb',
            'cloud',
            filename='Café.pdf',
            content_type='text/plain' if case == 'text' else 'application/pdf',
            principals=['owui:user:bob' if case in {'unreadable', 'document_hidden'} else 'owui:user:alice'],
        )
    requests = []

    def handle(request):
        requests.append(request)
        if request.url.host == 'storage.invalid':
            assert 'Authorization' not in request.headers
            assert 'X-Soev-Subject' not in request.headers
            assert 'cookie' not in request.headers
            return httpx.Response(200, content=b'original\x00bytes', headers={'Content-Type': 'wrong/type'})
        if request.url.path.endswith('/original'):
            assert request.headers['X-Soev-Subject']
            if case == 'revoked':
                return httpx.Response(
                    404,
                    json={'code': 'document_not_found', 'detail': 'Unreadable document'},
                    headers={'Content-Type': 'application/problem+json'},
                )
            return httpx.Response(
                303,
                headers={'Location': 'https://storage.invalid/original?signature=private', 'Set-Cookie': 'api=secret'},
            )
        return env.api.handle(request)

    from open_webui.soev.client import close_client

    await close_client()
    monkeypatch.setattr(
        'open_webui.soev.client.httpx.AsyncClient',
        lambda **kwargs: AsyncClient(transport=httpx.MockTransport(handle), **kwargs),
    )
    user = SimpleNamespace(id='alice', role='admin')
    if named:
        result = files.get_file_content_by_id('cloud', user=user, db=None, file_name='requested.pdf')
    else:
        result = files.get_file_content_by_id_inline('cloud', user=user, db=None, attachment=case == 'attachment')
    if case in {'missing', 'unreadable', 'document_hidden', 'revoked'}:
        with pytest.raises(HTTPException) as error:
            await result
        assert error.value.status_code == 404
        assert not any(request.url.host == 'storage.invalid' for request in requests)
    else:
        response = await result
        assert b''.join([chunk async for chunk in response.body_iterator]) == b'original\x00bytes'
        assert response.headers['content-type'].split(';')[0] == ('text/plain' if case == 'text' else 'application/pdf')
        disposition = 'inline' if not named and case == 'pdf' else 'attachment'
        assert response.headers['content-disposition'] == f"{disposition}; filename*=UTF-8''Caf%C3%A9.pdf"
        assert 'location' not in response.headers
        assert requests[-2].url.path == '/v1/collections/kb/documents/cloud/original'


@pytest.mark.asyncio
@pytest.mark.parametrize('rendition', ['# Parsed cloud document', '', None])
async def test_get_file_by_id_serves_a_catalog_only_document(env, monkeypatch, rendition):  # noqa: F811
    """The file view returns authorized cloud metadata and parsed text without creating an upload."""
    from open_webui.models import knowledge
    from open_webui.routers import files

    monkeypatch.setattr(knowledge, 'Knowledges', env.store)
    env.api.add_document(
        'kb',
        'cloud',
        filename='CV.docx',
        content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        rendition=rendition,
    )
    env.api.requests.clear()
    result = await files.get_file_by_id('cloud', user=SimpleNamespace(id='alice', role='user'), db=None)
    assert result.id == 'cloud'
    assert result.user_id == 'alice'
    assert result.filename == 'CV.docx'
    assert result.meta['content_type'] == 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    assert result.meta['soev_catalog_only'] is True
    assert result.data == {'content': rendition or ''}
    assert await files.Files.get_file_by_id('cloud') is None
    assert any(request.url.path == '/v1/collections/kb/documents/cloud/content' for request in env.api.requests)
    assert not any(request.url.path.endswith('/original') for request in env.api.requests)
    assert all(request.headers.get('X-Soev-Subject') for request in env.api.requests if request.method == 'GET')


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['missing', 'unreadable', 'document_hidden'])
async def test_get_file_by_id_hides_inaccessible_catalog_documents(env, monkeypatch, case):  # noqa: F811
    """Even an administrator's file view only resolves catalog members visible to that subject."""
    from open_webui.models import knowledge
    from open_webui.routers import files

    monkeypatch.setattr(knowledge, 'Knowledges', env.store)
    if case == 'unreadable':
        await seed(env, 'private', owner='bob')
    if case != 'missing':
        env.api.add_document(
            'private' if case == 'unreadable' else 'kb', 'cloud', principals=['owui:user:bob'], rendition='Hidden'
        )
    with pytest.raises(HTTPException) as error:
        await files.get_file_by_id('cloud', user=SimpleNamespace(id='alice', role='admin'), db=None)
    assert error.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'case', ['original', 'no_original', 'original_missing', 'no_rendition', 'unreadable', 'attachment']
)
async def test_data_source_document_content(env, monkeypatch, case):  # noqa: F811
    """A data-source document serves its original, else its markdown as plain text, read as the user."""
    from open_webui.models import knowledge
    from open_webui.routers import files

    monkeypatch.setattr(knowledge, 'Knowledges', env.store)
    env.api.collections['ds:pubs'] = {
        **env.api.collections.get('kb', {}),
        'key': 'ds:pubs',
        'name': 'Publicaties',
        'description': None,
        'visibility': 'public',
        'principals': [],
        'writers': [],
        'created_at': env.api.now,
        'updated_at': env.api.now,
    }
    env.api.add_document(
        'ds:pubs',
        'a' * 64,
        filename=None,
        title='Bodem en water v2.1 rapport',
        content_type=None,
        visibility='restricted' if case == 'unreadable' else 'public',
        principals=['owui:user:bob'] if case == 'unreadable' else [],
        rendition=None if case == 'no_rendition' else '# Bodem\n\nKlei houdt water vast.',
    )

    def handle(request):
        if request.url.host == 'storage.invalid':
            return httpx.Response(200, content=b'%PDF-1.7 bytes')
        if request.url.path.endswith('/original'):
            env.api.requests.append(request)
            if case == 'original':
                return httpx.Response(303, headers={'Location': 'https://storage.invalid/original?signature=x'})
            if case == 'original_missing':
                return httpx.Response(
                    409,
                    json={'code': 'original_missing', 'detail': 'No original'},
                    headers={'Content-Type': 'application/problem+json'},
                )
        return env.api.handle(request)

    from open_webui.soev.client import close_client

    await close_client()
    monkeypatch.setattr(
        'open_webui.soev.client.httpx.AsyncClient',
        lambda **kwargs: AsyncClient(transport=httpx.MockTransport(handle), **kwargs),
    )
    user = SimpleNamespace(id='alice', role='user')
    result = files.get_file_content_by_id_inline('a' * 64, user=user, db=None, attachment=case == 'attachment')
    if case in {'no_rendition', 'unreadable'}:
        with pytest.raises(HTTPException) as error:
            await result
        assert error.value.status_code == 404
        return
    response = await result
    content_path = '/v1/collections/ds%3Apubs/documents/' + 'a' * 64 + '/content'
    if case == 'original':
        assert b''.join([chunk async for chunk in response.body_iterator]) == b'%PDF-1.7 bytes'
        assert response.headers['content-type'] == 'application/pdf'
        assert response.headers['content-disposition'].startswith('inline;')
        assert not any(request.url.raw_path.decode() == content_path for request in env.api.requests)
        return
    assert response.body.decode() == '# Bodem\n\nKlei houdt water vast.'
    assert response.headers['content-type'] == 'text/plain; charset=utf-8'
    assert response.headers['x-content-type-options'] == 'nosniff'
    disposition = 'attachment' if case == 'attachment' else 'inline'
    name = 'Bodem%20en%20water%20v2.1%20rapport.md'
    assert response.headers['content-disposition'] == f"{disposition}; filename*=UTF-8''{name}"
    reads = [request for request in env.api.requests if request.url.raw_path.decode() == content_path]
    assert reads and all(request.headers.get('X-Soev-Subject') for request in reads)
