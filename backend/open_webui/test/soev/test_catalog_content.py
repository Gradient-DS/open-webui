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
