"""Router tests for the path-aware skill-files endpoints.

Endpoints under test (all under /api/v1/skills):
  - POST   /id/{id}/files          (upload-existing OR inline-create)
  - PUT    /id/{id}/files          (inline-edit)
  - POST   /id/{id}/files/move     (rename / folder-prefix rename)
  - POST   /id/{id}/files/remove   (single path OR folder prefix)
  - GET    /id/{id}/files          (flat list, items include path)

Uses a minimal FastAPI app + TestClient (no live DB). All model methods and
Storage are monkeypatched to avoid real DB / filesystem access.

Markdown-only validation is LOAD-BEARING: each bad-path / non-md test asserts
the 400 actually FIRES and that no write happened.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from open_webui.routers import skill_files as skill_files_router_module
from open_webui.utils.auth import get_verified_user

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_skill(skill_id: str = 'skill-1', owner_id: str = 'user-1') -> SimpleNamespace:
    return SimpleNamespace(
        id=skill_id,
        user_id=owner_id,
        name='Test Skill',
        description='',
        meta={},
        is_active=True,
        access_grants=[],
        created_at=1000,
        updated_at=1000,
    )


def _make_file(
    file_id: str = 'file-1',
    content_type: str = 'text/markdown',
    filename: str = 'notes.md',
    path: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=file_id,
        user_id='user-1',
        filename=filename,
        path=path if path is not None else f'uploads/{file_id}',
        data=None,
        meta={'content_type': content_type, 'size': 10},
        created_at=1000,
        updated_at=1000,
    )


def _make_skill_file_record(
    skill_id: str = 'skill-1', file_id: str = 'file-1', path: str = 'guide.md'
) -> SimpleNamespace:
    return SimpleNamespace(
        id='sf-1',
        skill_id=skill_id,
        file_id=file_id,
        path=path,
        user_id='user-1',
        created_at=1000,
        updated_at=1000,
    )


def _make_app(user: SimpleNamespace | None = None):
    """Build a minimal FastAPI app with the skill_files router mounted."""
    if user is None:
        user = SimpleNamespace(id='user-1', role='user', email='lex@gradient-ds.com', name='Lex')

    app = FastAPI()
    app.include_router(skill_files_router_module.router, prefix='/api/v1/skills')
    app.dependency_overrides[get_verified_user] = lambda: user
    return app


async def _passthrough_to_thread(fn, *args, **kwargs):
    import asyncio as _asyncio

    return await _asyncio.get_event_loop().run_in_executor(None, fn, *args, **kwargs)


# ===========================================================================
# _validate_skill_path — pure unit tests (the 400 must FIRE for each bad input)
# ===========================================================================


class TestValidateSkillPath:
    def test_accepts_simple_md(self):
        # Must NOT raise.
        skill_files_router_module._validate_skill_path('guide.md')
        skill_files_router_module._validate_skill_path('docs/sub/guide.markdown')

    @pytest.mark.parametrize(
        'bad_path',
        [
            'notes.txt',  # non-md extension
            'notes',  # no extension
            '../escape.md',  # traversal
            'docs/../secret.md',  # traversal mid-path
            '/abs/guide.md',  # leading slash
            './guide.md',  # '.' segment
            'docs//guide.md',  # empty segment
            'docs/guide.md/',  # trailing slash → empty trailing segment
            'docs/sp ace.md',  # space not in [A-Za-z0-9._-]
            'docs/gu?.md',  # illegal char
            'a/b/c/d/e/f/g/h/i.md',  # depth 9 > 8
        ],
    )
    def test_rejects_bad_paths(self, bad_path):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            skill_files_router_module._validate_skill_path(bad_path)
        assert exc.value.status_code == 400

    def test_rejects_over_length(self):
        from fastapi import HTTPException

        long_path = ('a' * 260) + '.md'
        with pytest.raises(HTTPException) as exc:
            skill_files_router_module._validate_skill_path(long_path)
        assert exc.value.status_code == 400

    def test_depth_8_ok_depth_9_rejected(self):
        from fastapi import HTTPException

        # depth 8 (7 dirs + file) is allowed
        skill_files_router_module._validate_skill_path('a/b/c/d/e/f/g/h.md')
        with pytest.raises(HTTPException) as exc:
            skill_files_router_module._validate_skill_path('a/b/c/d/e/f/g/h/i.md')
        assert exc.value.status_code == 400


# ===========================================================================
# POST /id/{id}/files — upload an existing File at a path
# ===========================================================================


def test_upload_existing_markdown_file_returns_200(monkeypatch, tmp_path):
    """Upload mode: Storage.get_file returns a local PATH; router reads that file
    and stores its TEXT as File.data['content'] (NOT the path string)."""
    md_text = '# Guide\n\nStep 1: do the thing.'
    tmp_file = tmp_path / 'guide.md'
    tmp_file.write_text(md_text, encoding='utf-8')

    skill = _make_skill()
    md_file = _make_file(content_type='text/markdown', filename='guide.md')
    sf_record = _make_skill_file_record(path='docs/guide.md')

    stored_content = {}

    async def _capture_update(file_id, data, db=None):
        stored_content['content'] = data.get('content')
        return md_file

    monkeypatch.setattr(skill_files_router_module.Skills, 'get_skill_by_id', AsyncMock(return_value=skill))
    monkeypatch.setattr(skill_files_router_module.Files, 'get_file_by_id', AsyncMock(return_value=md_file))
    monkeypatch.setattr(skill_files_router_module.Files, 'update_file_data_by_id', _capture_update)
    monkeypatch.setattr(skill_files_router_module.SkillFiles, 'path_exists', AsyncMock(return_value=False))
    monkeypatch.setattr(
        skill_files_router_module.SkillFiles, 'add_file_to_skill_by_id', AsyncMock(return_value=sf_record)
    )

    with patch('open_webui.routers.skill_files.asyncio.to_thread', side_effect=_passthrough_to_thread):
        with patch('open_webui.storage.provider.Storage') as mock_storage:
            mock_storage.get_file = lambda path: str(tmp_file)
            app = _make_app()
            res = TestClient(app).post(
                '/api/v1/skills/id/skill-1/files', json={'path': 'docs/guide.md', 'file_id': 'file-1'}
            )

    assert res.status_code == 200
    body = res.json()
    assert body['skill_id'] == 'skill-1'
    assert body['path'] == 'docs/guide.md'
    assert stored_content['content'] == md_text
    assert stored_content['content'] != str(tmp_file)


def test_upload_non_markdown_path_returns_400_no_write(monkeypatch):
    """A non-md PATH (regardless of File type) must 400 before any write."""
    skill = _make_skill()
    add_mock = AsyncMock(return_value=_make_skill_file_record())

    monkeypatch.setattr(skill_files_router_module.Skills, 'get_skill_by_id', AsyncMock(return_value=skill))
    monkeypatch.setattr(skill_files_router_module.Files, 'get_file_by_id', AsyncMock(return_value=_make_file()))
    monkeypatch.setattr(skill_files_router_module.SkillFiles, 'add_file_to_skill_by_id', add_mock)

    app = _make_app()
    res = TestClient(app).post('/api/v1/skills/id/skill-1/files', json={'path': 'docs/guide.txt', 'file_id': 'file-1'})

    assert res.status_code == 400
    add_mock.assert_not_called()


def test_upload_non_markdown_file_returns_400(monkeypatch):
    """A valid md PATH but a non-md backing File must still 400."""
    skill = _make_skill()
    pdf_file = _make_file(content_type='application/pdf', filename='report.pdf')
    add_mock = AsyncMock(return_value=_make_skill_file_record())

    monkeypatch.setattr(skill_files_router_module.Skills, 'get_skill_by_id', AsyncMock(return_value=skill))
    monkeypatch.setattr(skill_files_router_module.Files, 'get_file_by_id', AsyncMock(return_value=pdf_file))
    monkeypatch.setattr(skill_files_router_module.SkillFiles, 'add_file_to_skill_by_id', add_mock)

    app = _make_app()
    res = TestClient(app).post('/api/v1/skills/id/skill-1/files', json={'path': 'report.md', 'file_id': 'file-1'})

    assert res.status_code == 400
    add_mock.assert_not_called()


def test_upload_traversal_path_returns_400(monkeypatch):
    skill = _make_skill()
    add_mock = AsyncMock(return_value=_make_skill_file_record())
    monkeypatch.setattr(skill_files_router_module.Skills, 'get_skill_by_id', AsyncMock(return_value=skill))
    monkeypatch.setattr(skill_files_router_module.Files, 'get_file_by_id', AsyncMock(return_value=_make_file()))
    monkeypatch.setattr(skill_files_router_module.SkillFiles, 'add_file_to_skill_by_id', add_mock)

    app = _make_app()
    res = TestClient(app).post('/api/v1/skills/id/skill-1/files', json={'path': '../escape.md', 'file_id': 'file-1'})
    assert res.status_code == 400
    add_mock.assert_not_called()


def test_upload_duplicate_path_returns_400(monkeypatch):
    """path_exists True → 400 before any add."""
    skill = _make_skill()
    add_mock = AsyncMock(return_value=_make_skill_file_record())
    monkeypatch.setattr(skill_files_router_module.Skills, 'get_skill_by_id', AsyncMock(return_value=skill))
    monkeypatch.setattr(skill_files_router_module.Files, 'get_file_by_id', AsyncMock(return_value=_make_file()))
    monkeypatch.setattr(skill_files_router_module.SkillFiles, 'path_exists', AsyncMock(return_value=True))
    monkeypatch.setattr(skill_files_router_module.SkillFiles, 'add_file_to_skill_by_id', add_mock)

    app = _make_app()
    res = TestClient(app).post('/api/v1/skills/id/skill-1/files', json={'path': 'guide.md', 'file_id': 'file-1'})
    assert res.status_code == 400
    add_mock.assert_not_called()


def test_upload_case_insensitive_duplicate_returns_400(monkeypatch):
    """Foo.md collides with existing foo.md (case-insensitive)."""
    skill = _make_skill()
    add_mock = AsyncMock(return_value=_make_skill_file_record())
    monkeypatch.setattr(skill_files_router_module.Skills, 'get_skill_by_id', AsyncMock(return_value=skill))
    monkeypatch.setattr(skill_files_router_module.Files, 'get_file_by_id', AsyncMock(return_value=_make_file()))
    # path_exists is the case-insensitive check; simulate an existing 'foo.md'.
    monkeypatch.setattr(skill_files_router_module.SkillFiles, 'path_exists', AsyncMock(return_value=True))
    monkeypatch.setattr(skill_files_router_module.SkillFiles, 'add_file_to_skill_by_id', add_mock)

    app = _make_app()
    res = TestClient(app).post('/api/v1/skills/id/skill-1/files', json={'path': 'Foo.md', 'file_id': 'file-1'})
    assert res.status_code == 400
    add_mock.assert_not_called()


# ===========================================================================
# POST /id/{id}/files — inline create (path + content)
# ===========================================================================


def test_inline_create_writes_file_and_join(monkeypatch, tmp_path):
    """Inline-create: content bytes go through the Storage upload pipeline,
    a real File row is created, then the join row with path."""
    skill = _make_skill()
    created_file = _make_file(file_id='new-file', filename='guide.md')
    sf_record = _make_skill_file_record(file_id='new-file', path='docs/new.md')

    uploaded = {}

    def _fake_upload(fileobj, filename, tags):
        contents = fileobj.read()
        uploaded['contents'] = contents
        uploaded['filename'] = filename
        return contents, str(tmp_path / filename)

    insert_mock = AsyncMock(return_value=created_file)
    add_mock = AsyncMock(return_value=sf_record)

    monkeypatch.setattr(skill_files_router_module.Skills, 'get_skill_by_id', AsyncMock(return_value=skill))
    monkeypatch.setattr(skill_files_router_module.SkillFiles, 'path_exists', AsyncMock(return_value=False))
    monkeypatch.setattr(skill_files_router_module.Files, 'insert_new_file', insert_mock)
    monkeypatch.setattr(skill_files_router_module.SkillFiles, 'add_file_to_skill_by_id', add_mock)

    with patch('open_webui.routers.skill_files.asyncio.to_thread', side_effect=_passthrough_to_thread):
        with patch('open_webui.storage.provider.Storage') as mock_storage:
            mock_storage.upload_file = _fake_upload
            app = _make_app()
            res = TestClient(app).post(
                '/api/v1/skills/id/skill-1/files',
                json={'path': 'docs/new.md', 'content': '# Hello\n\nworld'},
            )

    assert res.status_code == 200
    body = res.json()
    assert body['path'] == 'docs/new.md'
    # content bytes were written through Storage.upload_file
    assert uploaded['contents'] == b'# Hello\n\nworld'
    # insert_new_file was called and the join row created
    insert_mock.assert_called_once()
    add_mock.assert_called_once()
    # the FileForm passed to insert_new_file carries the content in data
    form = insert_mock.call_args.args[1]
    assert form.data.get('content') == '# Hello\n\nworld'


def test_inline_create_non_markdown_path_returns_400(monkeypatch):
    """Inline-create with a non-md path must 400 — md-only enforced server-side."""
    skill = _make_skill()
    insert_mock = AsyncMock(return_value=_make_file())
    add_mock = AsyncMock(return_value=_make_skill_file_record())

    monkeypatch.setattr(skill_files_router_module.Skills, 'get_skill_by_id', AsyncMock(return_value=skill))
    monkeypatch.setattr(skill_files_router_module.Files, 'insert_new_file', insert_mock)
    monkeypatch.setattr(skill_files_router_module.SkillFiles, 'add_file_to_skill_by_id', add_mock)

    app = _make_app()
    res = TestClient(app).post('/api/v1/skills/id/skill-1/files', json={'path': 'notes.txt', 'content': 'hi'})
    assert res.status_code == 400
    insert_mock.assert_not_called()
    add_mock.assert_not_called()


def test_create_requires_file_id_or_content(monkeypatch):
    """Neither file_id nor content → 400."""
    skill = _make_skill()
    monkeypatch.setattr(skill_files_router_module.Skills, 'get_skill_by_id', AsyncMock(return_value=skill))
    app = _make_app()
    res = TestClient(app).post('/api/v1/skills/id/skill-1/files', json={'path': 'guide.md'})
    assert res.status_code == 400


def test_inline_create_empty_content_returns_400(monkeypatch):
    """Inline-create with empty string content must 400 before Storage is called."""
    skill = _make_skill()
    insert_mock = AsyncMock(return_value=_make_file())
    add_mock = AsyncMock(return_value=_make_skill_file_record())

    monkeypatch.setattr(skill_files_router_module.Skills, 'get_skill_by_id', AsyncMock(return_value=skill))
    monkeypatch.setattr(skill_files_router_module.SkillFiles, 'path_exists', AsyncMock(return_value=False))
    monkeypatch.setattr(skill_files_router_module.Files, 'insert_new_file', insert_mock)
    monkeypatch.setattr(skill_files_router_module.SkillFiles, 'add_file_to_skill_by_id', add_mock)

    app = _make_app()
    res = TestClient(app).post('/api/v1/skills/id/skill-1/files', json={'path': 'guide.md', 'content': ''})

    assert res.status_code == 400
    insert_mock.assert_not_called()
    add_mock.assert_not_called()


def test_edit_empty_content_returns_400(monkeypatch):
    """PUT with empty string content must 400 before Storage is called."""
    skill = _make_skill()
    sf_record = _make_skill_file_record(path='guide.md')
    update_data_mock = AsyncMock(return_value=_make_file())
    update_path_mock = AsyncMock(return_value=_make_file())

    monkeypatch.setattr(skill_files_router_module.Skills, 'get_skill_by_id', AsyncMock(return_value=skill))
    monkeypatch.setattr(skill_files_router_module.SkillFiles, 'get_file_by_path', AsyncMock(return_value=sf_record))
    monkeypatch.setattr(skill_files_router_module.Files, 'get_file_by_id', AsyncMock(return_value=_make_file()))
    monkeypatch.setattr(skill_files_router_module.Files, 'update_file_data_by_id', update_data_mock)
    monkeypatch.setattr(skill_files_router_module.Files, 'update_file_path_by_id', update_path_mock)

    app = _make_app()
    res = TestClient(app).put('/api/v1/skills/id/skill-1/files', json={'path': 'guide.md', 'content': ''})

    assert res.status_code == 400
    update_data_mock.assert_not_called()
    update_path_mock.assert_not_called()


# ===========================================================================
# PUT /id/{id}/files — inline edit
# ===========================================================================


def test_edit_updates_backing_file(monkeypatch, tmp_path):
    """PUT rewrites the backing File's bytes (Storage) + data['content']."""
    skill = _make_skill()
    existing_file = _make_file(file_id='file-1', filename='guide.md')
    sf_record = _make_skill_file_record(path='guide.md')

    uploaded = {}

    def _fake_upload(fileobj, filename, tags):
        contents = fileobj.read()
        uploaded['contents'] = contents
        return contents, str(tmp_path / filename)

    update_data = {}

    async def _capture_update(file_id, data, db=None):
        update_data['content'] = data.get('content')
        return existing_file

    monkeypatch.setattr(skill_files_router_module.Skills, 'get_skill_by_id', AsyncMock(return_value=skill))
    monkeypatch.setattr(skill_files_router_module.SkillFiles, 'get_file_by_path', AsyncMock(return_value=sf_record))
    monkeypatch.setattr(skill_files_router_module.Files, 'get_file_by_id', AsyncMock(return_value=existing_file))
    monkeypatch.setattr(skill_files_router_module.Files, 'update_file_data_by_id', _capture_update)
    monkeypatch.setattr(
        skill_files_router_module.Files, 'update_file_path_by_id', AsyncMock(return_value=existing_file)
    )

    with patch('open_webui.routers.skill_files.asyncio.to_thread', side_effect=_passthrough_to_thread):
        with patch('open_webui.storage.provider.Storage') as mock_storage:
            mock_storage.upload_file = _fake_upload
            app = _make_app()
            res = TestClient(app).put(
                '/api/v1/skills/id/skill-1/files',
                json={'path': 'guide.md', 'content': '# Updated'},
            )

    assert res.status_code == 200
    assert uploaded['contents'] == b'# Updated'
    assert update_data['content'] == '# Updated'


def test_edit_unknown_path_returns_404(monkeypatch):
    skill = _make_skill()
    monkeypatch.setattr(skill_files_router_module.Skills, 'get_skill_by_id', AsyncMock(return_value=skill))
    monkeypatch.setattr(skill_files_router_module.SkillFiles, 'get_file_by_path', AsyncMock(return_value=None))

    app = _make_app()
    res = TestClient(app).put('/api/v1/skills/id/skill-1/files', json={'path': 'missing.md', 'content': 'x'})
    assert res.status_code == 404


def test_edit_non_markdown_path_returns_400(monkeypatch):
    skill = _make_skill()
    get_mock = AsyncMock(return_value=_make_skill_file_record())
    monkeypatch.setattr(skill_files_router_module.Skills, 'get_skill_by_id', AsyncMock(return_value=skill))
    monkeypatch.setattr(skill_files_router_module.SkillFiles, 'get_file_by_path', get_mock)

    app = _make_app()
    res = TestClient(app).put('/api/v1/skills/id/skill-1/files', json={'path': 'guide.txt', 'content': 'x'})
    assert res.status_code == 400
    get_mock.assert_not_called()


# ===========================================================================
# POST /id/{id}/files/move — rename / folder rename
# ===========================================================================


def test_move_single_file(monkeypatch):
    skill = _make_skill()
    monkeypatch.setattr(skill_files_router_module.Skills, 'get_skill_by_id', AsyncMock(return_value=skill))
    # An exact row exists at from_path → single-file rename path.
    monkeypatch.setattr(
        skill_files_router_module.SkillFiles,
        'get_file_by_path',
        AsyncMock(return_value=_make_skill_file_record(path='old.md')),
    )
    monkeypatch.setattr(skill_files_router_module.SkillFiles, 'path_exists', AsyncMock(return_value=False))
    move_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(skill_files_router_module.SkillFiles, 'move_path', move_mock)

    app = _make_app()
    res = TestClient(app).post(
        '/api/v1/skills/id/skill-1/files/move',
        json={'from_path': 'old.md', 'to_path': 'docs/new.md'},
    )
    assert res.status_code == 200
    move_mock.assert_called_once()


def test_move_folder_prefix(monkeypatch):
    """Folder rename: from_path has no .md → treated as prefix rewrite."""
    skill = _make_skill()
    rows = [
        _make_skill_file_record(file_id='f1', path='docs/a.md'),
        _make_skill_file_record(file_id='f2', path='docs/sub/b.md'),
    ]
    monkeypatch.setattr(skill_files_router_module.Skills, 'get_skill_by_id', AsyncMock(return_value=skill))
    # No exact row at 'docs' → folder-prefix rename path.
    monkeypatch.setattr(skill_files_router_module.SkillFiles, 'get_file_by_path', AsyncMock(return_value=None))
    monkeypatch.setattr(skill_files_router_module.SkillFiles, 'get_files_by_skill_id', AsyncMock(return_value=rows))
    move_prefix_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(skill_files_router_module.SkillFiles, 'move_paths_under_prefix', move_prefix_mock)

    app = _make_app()
    res = TestClient(app).post(
        '/api/v1/skills/id/skill-1/files/move',
        json={'from_path': 'docs', 'to_path': 'documentation'},
    )
    assert res.status_code == 200
    move_prefix_mock.assert_called_once()


def test_move_non_markdown_to_path_returns_400(monkeypatch):
    """Renaming a file to a non-md to_path must 400."""
    skill = _make_skill()
    move_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(skill_files_router_module.Skills, 'get_skill_by_id', AsyncMock(return_value=skill))
    monkeypatch.setattr(
        skill_files_router_module.SkillFiles,
        'get_file_by_path',
        AsyncMock(return_value=_make_skill_file_record(path='old.md')),
    )
    monkeypatch.setattr(skill_files_router_module.SkillFiles, 'path_exists', AsyncMock(return_value=False))
    monkeypatch.setattr(skill_files_router_module.SkillFiles, 'move_path', move_mock)

    app = _make_app()
    res = TestClient(app).post(
        '/api/v1/skills/id/skill-1/files/move',
        json={'from_path': 'old.md', 'to_path': 'new.txt'},
    )
    assert res.status_code == 400
    move_mock.assert_not_called()


def test_move_to_existing_path_returns_400(monkeypatch):
    skill = _make_skill()
    move_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(skill_files_router_module.Skills, 'get_skill_by_id', AsyncMock(return_value=skill))
    monkeypatch.setattr(
        skill_files_router_module.SkillFiles,
        'get_file_by_path',
        AsyncMock(return_value=_make_skill_file_record(path='old.md')),
    )
    monkeypatch.setattr(skill_files_router_module.SkillFiles, 'path_exists', AsyncMock(return_value=True))
    monkeypatch.setattr(skill_files_router_module.SkillFiles, 'move_path', move_mock)

    app = _make_app()
    res = TestClient(app).post(
        '/api/v1/skills/id/skill-1/files/move',
        json={'from_path': 'old.md', 'to_path': 'taken.md'},
    )
    assert res.status_code == 400
    move_mock.assert_not_called()


# ===========================================================================
# POST /id/{id}/files/remove — single path or folder prefix
# ===========================================================================


def test_remove_single_path_deletes_backing_file(monkeypatch):
    skill = _make_skill()
    removed_row = _make_skill_file_record(file_id='file-1', path='guide.md')
    delete_mock = AsyncMock(return_value=True)

    monkeypatch.setattr(skill_files_router_module.Skills, 'get_skill_by_id', AsyncMock(return_value=skill))
    # An exact row exists at 'guide.md' → single-file delete path.
    monkeypatch.setattr(skill_files_router_module.SkillFiles, 'get_file_by_path', AsyncMock(return_value=removed_row))
    monkeypatch.setattr(
        skill_files_router_module.SkillFiles, 'remove_file_by_path', AsyncMock(return_value=removed_row)
    )
    monkeypatch.setattr(skill_files_router_module.Files, 'delete_file_by_id', delete_mock)

    app = _make_app()
    res = TestClient(app).post('/api/v1/skills/id/skill-1/files/remove', json={'path': 'guide.md'})
    assert res.status_code == 200
    delete_mock.assert_called_once()
    assert delete_mock.call_args.args[0] == 'file-1'


def test_remove_folder_prefix_deletes_all_backing_files(monkeypatch):
    skill = _make_skill()
    removed = [
        _make_skill_file_record(file_id='f1', path='docs/a.md'),
        _make_skill_file_record(file_id='f2', path='docs/b.md'),
    ]
    delete_ids_mock = AsyncMock(return_value=True)

    monkeypatch.setattr(skill_files_router_module.Skills, 'get_skill_by_id', AsyncMock(return_value=skill))
    # No exact row at 'docs' → folder-prefix delete path.
    monkeypatch.setattr(skill_files_router_module.SkillFiles, 'get_file_by_path', AsyncMock(return_value=None))
    monkeypatch.setattr(
        skill_files_router_module.SkillFiles, 'remove_paths_under_prefix', AsyncMock(return_value=removed)
    )
    monkeypatch.setattr(skill_files_router_module.Files, 'delete_files_by_ids', delete_ids_mock)

    app = _make_app()
    res = TestClient(app).post('/api/v1/skills/id/skill-1/files/remove', json={'path': 'docs'})
    assert res.status_code == 200
    called_ids = set(delete_ids_mock.call_args.args[0])
    assert called_ids == {'f1', 'f2'}


# ===========================================================================
# Auth
# ===========================================================================


def test_create_denied_for_non_owner(monkeypatch):
    skill = _make_skill(owner_id='other-user')
    monkeypatch.setattr(skill_files_router_module.Skills, 'get_skill_by_id', AsyncMock(return_value=skill))
    monkeypatch.setattr(skill_files_router_module.AccessGrants, 'has_access', AsyncMock(return_value=False))

    non_owner = SimpleNamespace(id='user-1', role='user', email='user@example.com', name='U')
    app = _make_app(user=non_owner)
    res = TestClient(app).post('/api/v1/skills/id/skill-1/files', json={'path': 'guide.md', 'content': 'x'})
    assert res.status_code == 401


# ===========================================================================
# GET /id/{id}/files — list with path
# ===========================================================================


def test_list_files_returns_items_with_path(monkeypatch):
    skill = _make_skill()
    list_response = SimpleNamespace(
        items=[
            SimpleNamespace(
                id='file-1',
                user_id='user-1',
                filename='guide.md',
                path='docs/guide.md',
                data={'content': '# Hello'},
                meta={'content_type': 'text/markdown', 'size': 7},
                hash=None,
                created_at=1000,
                updated_at=1000,
                user=None,
                added_at=1000,
            )
        ],
        total=1,
    )

    monkeypatch.setattr(skill_files_router_module.Skills, 'get_skill_by_id', AsyncMock(return_value=skill))
    monkeypatch.setattr(
        skill_files_router_module.SkillFiles, 'search_files_by_id', AsyncMock(return_value=list_response)
    )

    app = _make_app()
    res = TestClient(app).get('/api/v1/skills/id/skill-1/files')

    assert res.status_code == 200
    body = res.json()
    assert body['total'] == 1
    assert body['items'][0]['path'] == 'docs/guide.md'


def test_list_files_skill_not_found(monkeypatch):
    monkeypatch.setattr(skill_files_router_module.Skills, 'get_skill_by_id', AsyncMock(return_value=None))
    app = _make_app()
    res = TestClient(app).get('/api/v1/skills/id/missing/files')
    assert res.status_code == 404
