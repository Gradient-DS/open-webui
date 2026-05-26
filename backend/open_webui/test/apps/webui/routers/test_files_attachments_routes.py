"""Tests for /api/v1/files/{id}/attachments[/{attachment_id}]."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from open_webui.routers import files as files_router
from open_webui.utils.auth import get_verified_user


@pytest.fixture
def app(monkeypatch, tmp_path):
    user = SimpleNamespace(id='user-1', role='user', email='lex@gradient-ds.com')
    app = FastAPI()
    app.include_router(files_router.router, prefix='/api/v1/files')
    app.dependency_overrides[get_verified_user] = lambda: user
    monkeypatch.setattr(
        files_router.Files,
        'get_file_by_id',
        lambda _id, db=None: SimpleNamespace(id=_id, user_id='user-1'),
    )
    return app


def test_list_attachments_returns_manifest(app, monkeypatch):
    monkeypatch.setattr(
        files_router.FileAttachments,
        'get_attachments_by_file_id',
        lambda fid, db=None: [
            SimpleNamespace(
                id='att-1',
                file_id=fid,
                kind='plan_png',
                storey='L1',
                index=0,
                content_type='image/png',
                caption='',
                path='uploads/p.png',
                created_at=1716643200,
            ),
        ],
    )
    res = TestClient(app).get('/api/v1/files/file-1/attachments')
    assert res.status_code == 200
    body = res.json()
    assert len(body) == 1
    assert body[0]['id'] == 'att-1'
    assert 'path' not in body[0], 'manifest response must not leak Storage path'


def test_list_attachments_404_when_file_missing(app, monkeypatch):
    monkeypatch.setattr(files_router.Files, 'get_file_by_id', lambda _id, db=None: None)
    res = TestClient(app).get('/api/v1/files/missing/attachments')
    assert res.status_code == 404


def test_get_attachment_bytes_streams_png(app, monkeypatch, tmp_path):
    png_path = tmp_path / 'p.png'
    png_path.write_bytes(b'\x89PNG\r\n\x1a\nfake')

    monkeypatch.setattr(
        files_router.FileAttachments,
        'get_attachment_by_id',
        lambda aid, db=None: SimpleNamespace(
            id=aid,
            file_id='file-1',
            kind='plan_png',
            storey='L1',
            index=0,
            content_type='image/png',
            caption='',
            path=str(png_path),
            created_at=1716643200,
        ),
    )
    with patch.object(files_router.Storage, 'get_file', return_value=str(png_path)):
        res = TestClient(app).get('/api/v1/files/file-1/attachments/att-1')
    assert res.status_code == 200
    assert res.headers['content-type'].startswith('image/png')
    assert res.content.startswith(b'\x89PNG')


def test_get_attachment_bytes_404_when_attachment_belongs_to_other_file(app, monkeypatch):
    monkeypatch.setattr(
        files_router.FileAttachments,
        'get_attachment_by_id',
        lambda aid, db=None: SimpleNamespace(
            id=aid,
            file_id='OTHER-FILE',
            kind='plan_png',
            storey=None,
            index=0,
            content_type='image/png',
            caption='',
            path='uploads/x.png',
            created_at=1716643200,
        ),
    )
    res = TestClient(app).get('/api/v1/files/file-1/attachments/att-1')
    assert res.status_code == 404


def test_get_attachment_bytes_404_when_storage_missing(app, monkeypatch, tmp_path):
    monkeypatch.setattr(
        files_router.FileAttachments,
        'get_attachment_by_id',
        lambda aid, db=None: SimpleNamespace(
            id=aid,
            file_id='file-1',
            kind='plan_png',
            storey=None,
            index=0,
            content_type='image/png',
            caption='',
            path='uploads/gone.png',
            created_at=1716643200,
        ),
    )
    with patch.object(files_router.Storage, 'get_file', return_value=str(tmp_path / 'gone.png')):
        res = TestClient(app).get('/api/v1/files/file-1/attachments/att-1')
    assert res.status_code == 404


def test_list_attachments_404_when_user_has_no_access(app, monkeypatch):
    monkeypatch.setattr(
        files_router.Files,
        'get_file_by_id',
        lambda _id, db=None: SimpleNamespace(id=_id, user_id='other-user'),
    )
    monkeypatch.setattr(
        files_router,
        'has_access_to_file',
        lambda *_a, **_k: False,
    )
    res = TestClient(app).get('/api/v1/files/file-1/attachments')
    assert res.status_code == 404


def test_get_attachment_bytes_404_when_user_has_no_access(app, monkeypatch):
    monkeypatch.setattr(
        files_router.Files,
        'get_file_by_id',
        lambda _id, db=None: SimpleNamespace(id=_id, user_id='other-user'),
    )
    monkeypatch.setattr(
        files_router,
        'has_access_to_file',
        lambda *_a, **_k: False,
    )
    res = TestClient(app).get('/api/v1/files/file-1/attachments/att-1')
    assert res.status_code == 404


def test_get_attachment_bytes_404_when_storage_raises(app, monkeypatch):
    """A Storage backend error should 404 the user, not 500 with a traceback."""
    monkeypatch.setattr(
        files_router.FileAttachments,
        'get_attachment_by_id',
        lambda aid, db=None: SimpleNamespace(
            id=aid,
            file_id='file-1',
            kind='plan_png',
            storey=None,
            index=0,
            content_type='image/png',
            caption='',
            path='uploads/x.png',
            created_at=1716643200,
        ),
    )
    with patch.object(
        files_router.Storage,
        'get_file',
        side_effect=RuntimeError('s3 backend not configured'),
    ):
        res = TestClient(app).get('/api/v1/files/file-1/attachments/att-1')
    assert res.status_code == 404
    assert 'Traceback' not in res.text
