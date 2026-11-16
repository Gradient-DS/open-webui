"""GET /cache/{path} authorizes on the resolved file, never the raw path."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

EXPORT = b'PK-alice-export'


@pytest.fixture
def cache(application, monkeypatch, tmp_path):
    from open_webui import main

    root = tmp_path / 'cache'
    for owner in ('alice', 'bob'):
        (root / 'exports' / owner).mkdir(parents=True)
        (root / 'exports' / owner / 'export-1.zip').write_bytes(f'PK-{owner}-export'.encode())
    (tmp_path / 'outside.txt').write_text('outside')
    for shared in ('audio/speech/0123abcd.json', 'audio/speech/0123abcd.mp3', 'pipelines/pipe.py', 'tools/t1/out.txt'):
        (root / shared).parent.mkdir(parents=True, exist_ok=True)
        (root / shared).write_text('shared cache entry')
    monkeypatch.setattr(main, 'CACHE_DIR', root)

    caller = SimpleNamespace(id='bob', role='user')
    app = FastAPI(routes=[route for route in application.routes if getattr(route, 'path', '') == '/cache/{path:path}'])
    # The route resolves overrides on the app that registered it.
    monkeypatch.setitem(application.dependency_overrides, main.get_verified_user, lambda: caller)
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, caller=caller, root=root)


def test_owner_downloads_own_export(cache):
    cache.caller.id = 'alice'
    response = cache.client.get('/cache/exports/alice/export-1.zip')
    assert response.status_code == 200
    assert response.content == EXPORT


@pytest.mark.parametrize(
    'path',
    (
        'exports/alice/export-1.zip',
        'exports/bob/../alice/export-1.zip',
        'exports/bob/%2E%2E/alice/export-1.zip',
        'exports%2Fbob%2F..%2Falice%2Fexport-1.zip',
        './exports/alice/export-1.zip',
        '.%2Fexports/alice/export-1.zip',
        'x/../exports/alice/export-1.zip',
        'x%2F..%2Fexports%2Falice%2Fexport-1.zip',
        'exports//alice/export-1.zip',
        'exports/bob/../../exports/alice/export-1.zip',
    ),
)
@pytest.mark.parametrize('role', ('user', 'admin'))
def test_other_users_export_is_not_served(cache, path, role):
    cache.caller.role = role
    response = cache.client.get(f'/cache/{path}')
    assert response.status_code == 404
    assert EXPORT not in response.content


@pytest.mark.parametrize('path', ('..%2Foutside.txt', '%2E%2E/outside.txt', 'exports/..%2F..%2Foutside.txt'))
def test_escape_from_cache_root_is_not_served(cache, path):
    response = cache.client.get(f'/cache/{path}')
    assert response.status_code == 404
    assert b'outside' not in response.content


@pytest.mark.parametrize(
    'path',
    (
        'exports/bob/../alice/export-1.zip',
        './exports/alice/export-1.zip',
        'x/../exports/alice/export-1.zip',
        '/exports/alice/export-1.zip',
    ),
)
def test_handler_rejects_unnormalized_paths(cache, path):
    """Dot segments reach the handler verbatim, whatever the client normalizes."""
    from open_webui.main import serve_cache_file

    with pytest.raises(HTTPException) as raised:
        asyncio.run(serve_cache_file(path=path, user=SimpleNamespace(id='bob', role='admin')))
    assert raised.value.status_code == 404


@pytest.mark.parametrize(
    'path',
    (
        'audio/speech/0123abcd.json',
        'audio/speech/0123abcd.mp3',
        'pipelines/pipe.py',
        'tools/t1/out.txt',
        'exports/bob/../../audio/speech/0123abcd.json',
        'Exports/alice/export-1.zip',
    ),
)
@pytest.mark.parametrize('role', ('user', 'admin'))
def test_only_data_exports_are_served(cache, path, role):
    cache.caller.role = role
    response = cache.client.get(f'/cache/{path}')
    assert response.status_code == 404
    assert b'shared cache entry' not in response.content
    assert EXPORT not in response.content


def test_export_file_name_is_unguessable(application, monkeypatch, tmp_path):
    from open_webui.services.export import service

    monkeypatch.setattr(service, 'EXPORT_DIR', tmp_path)
    names = {asyncio.run(service.ExportService.build_export_zip('alice', {})).name for _ in range(2)}
    assert len(names) == 2
    assert all(len(name.removeprefix('export-').removesuffix('.zip').split('-', 1)[1]) >= 32 for name in names)
