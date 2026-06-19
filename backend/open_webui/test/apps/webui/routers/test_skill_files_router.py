"""Router tests for /api/v1/skills/id/{id}/files/{add,remove,list}.

Uses a minimal FastAPI app + TestClient (no live DB). All model methods and
Storage are monkeypatched to avoid real DB / filesystem access.

Auth patterns:
  - write endpoints require ownership-or-AccessGrants write OR admin
  - read endpoint requires read access
  - non-owner without access gets 401
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

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


def _make_skill_file_record(skill_id: str = 'skill-1', file_id: str = 'file-1') -> SimpleNamespace:
    return SimpleNamespace(
        id='sf-1',
        skill_id=skill_id,
        file_id=file_id,
        user_id='user-1',
        created_at=1000,
        updated_at=1000,
    )


def _make_app(user: SimpleNamespace | None = None):
    """Build a minimal FastAPI app with the skill_files router mounted."""
    if user is None:
        user = SimpleNamespace(id='user-1', role='user', email='lex@gradient-ds.com')

    app = FastAPI()
    app.include_router(skill_files_router_module.router, prefix='/api/v1/skills')
    app.dependency_overrides[get_verified_user] = lambda: user
    return app


# ---------------------------------------------------------------------------
# Markdown attach — happy path
# ---------------------------------------------------------------------------


def test_add_markdown_file_returns_200(monkeypatch, tmp_path):
    """Happy path: Storage.get_file returns a local path; router reads that file
    and stores its TEXT as File.data['content'] (not the path string)."""
    md_text = '# Guide\n\nStep 1: do the thing.'
    tmp_file = tmp_path / 'guide.md'
    tmp_file.write_text(md_text, encoding='utf-8')

    skill = _make_skill()
    md_file = _make_file(content_type='text/markdown', filename='guide.md')
    sf_record = _make_skill_file_record()

    stored_content = {}

    async def _capture_update(file_id, data, db=None):
        stored_content['content'] = data.get('content')
        return md_file

    monkeypatch.setattr(skill_files_router_module.Skills, 'get_skill_by_id', AsyncMock(return_value=skill))
    monkeypatch.setattr(skill_files_router_module.Files, 'get_file_by_id', AsyncMock(return_value=md_file))
    monkeypatch.setattr(skill_files_router_module.Files, 'update_file_data_by_id', _capture_update)
    monkeypatch.setattr(
        skill_files_router_module.SkillFiles, 'add_file_to_skill_by_id', AsyncMock(return_value=sf_record)
    )

    # Storage.get_file returns a PATH (str) — the correct contract.
    # asyncio.to_thread is called twice: once for Storage.get_file (returns path),
    # once for Path.read_bytes (returns file bytes). We use a real tmp file so
    # the second call actually reads it from disk.
    async def _to_thread(fn, *args, **kwargs):
        import asyncio as _asyncio

        return await _asyncio.get_event_loop().run_in_executor(None, fn, *args, **kwargs)

    with patch('open_webui.routers.skill_files.asyncio.to_thread', side_effect=_to_thread):
        with patch('open_webui.storage.provider.Storage') as mock_storage:
            mock_storage.get_file = lambda path: str(tmp_file)
            app = _make_app()
            res = TestClient(app).post('/api/v1/skills/id/skill-1/files/add', json={'file_id': 'file-1'})

    assert res.status_code == 200
    body = res.json()
    assert body['skill_id'] == 'skill-1'
    assert body['file_id'] == 'file-1'
    # The stored content must be the FILE'S TEXT, not the path string.
    assert stored_content['content'] == md_text
    assert stored_content['content'] != str(tmp_file)


def test_add_markdown_by_filename_extension(monkeypatch, tmp_path):
    """Accept .md file even when content_type is generic."""
    md_text = '# Notes\n\nSome content.'
    tmp_file = tmp_path / 'notes.md'
    tmp_file.write_text(md_text, encoding='utf-8')

    skill = _make_skill()
    md_file = _make_file(content_type='application/octet-stream', filename='notes.md')
    sf_record = _make_skill_file_record()

    monkeypatch.setattr(skill_files_router_module.Skills, 'get_skill_by_id', AsyncMock(return_value=skill))
    monkeypatch.setattr(skill_files_router_module.Files, 'get_file_by_id', AsyncMock(return_value=md_file))
    monkeypatch.setattr(skill_files_router_module.Files, 'update_file_data_by_id', AsyncMock(return_value=md_file))
    monkeypatch.setattr(
        skill_files_router_module.SkillFiles, 'add_file_to_skill_by_id', AsyncMock(return_value=sf_record)
    )

    async def _to_thread(fn, *args, **kwargs):
        import asyncio as _asyncio

        return await _asyncio.get_event_loop().run_in_executor(None, fn, *args, **kwargs)

    with patch('open_webui.routers.skill_files.asyncio.to_thread', side_effect=_to_thread):
        with patch('open_webui.storage.provider.Storage') as mock_storage:
            mock_storage.get_file = lambda path: str(tmp_file)
            app = _make_app()
            res = TestClient(app).post('/api/v1/skills/id/skill-1/files/add', json={'file_id': 'file-1'})

    assert res.status_code == 200


# ---------------------------------------------------------------------------
# Non-markdown rejection
# ---------------------------------------------------------------------------


def test_add_markdown_file_without_path_returns_400(monkeypatch):
    """Attaching a markdown file whose file.path is falsy must return 400 and must
    NOT create a skill_file row (fail-fast content invariant)."""
    skill = _make_skill()
    # A markdown file with no stored path
    no_path_file = _make_file(content_type='text/markdown', filename='guide.md', path='')
    add_mock = AsyncMock(return_value=_make_skill_file_record())

    monkeypatch.setattr(skill_files_router_module.Skills, 'get_skill_by_id', AsyncMock(return_value=skill))
    monkeypatch.setattr(skill_files_router_module.Files, 'get_file_by_id', AsyncMock(return_value=no_path_file))
    monkeypatch.setattr(skill_files_router_module.SkillFiles, 'add_file_to_skill_by_id', add_mock)

    app = _make_app()
    res = TestClient(app).post('/api/v1/skills/id/skill-1/files/add', json={'file_id': 'file-1'})

    assert res.status_code == 400
    # Critically, the skill_file row must NOT have been created
    add_mock.assert_not_called()


def test_add_non_markdown_file_returns_400(monkeypatch):
    skill = _make_skill()
    pdf_file = _make_file(content_type='application/pdf', filename='report.pdf')

    monkeypatch.setattr(skill_files_router_module.Skills, 'get_skill_by_id', AsyncMock(return_value=skill))
    monkeypatch.setattr(skill_files_router_module.Files, 'get_file_by_id', AsyncMock(return_value=pdf_file))

    app = _make_app()
    res = TestClient(app).post('/api/v1/skills/id/skill-1/files/add', json={'file_id': 'file-1'})

    assert res.status_code == 400


# ---------------------------------------------------------------------------
# Auth denied — non-owner, no write access, non-admin
# ---------------------------------------------------------------------------


def test_add_file_denied_for_non_owner(monkeypatch):
    """Non-owner user without write AccessGrant and non-admin gets 401."""
    skill = _make_skill(owner_id='other-user')  # owned by someone else

    monkeypatch.setattr(skill_files_router_module.Skills, 'get_skill_by_id', AsyncMock(return_value=skill))
    # AccessGrants.has_access returns False — user has no write grant
    monkeypatch.setattr(skill_files_router_module.AccessGrants, 'has_access', AsyncMock(return_value=False))

    non_owner = SimpleNamespace(id='user-1', role='user', email='user@example.com')
    app = _make_app(user=non_owner)
    res = TestClient(app).post('/api/v1/skills/id/skill-1/files/add', json={'file_id': 'file-1'})

    assert res.status_code == 401


# ---------------------------------------------------------------------------
# Remove
# ---------------------------------------------------------------------------


def test_remove_file_returns_true(monkeypatch):
    skill = _make_skill()

    monkeypatch.setattr(skill_files_router_module.Skills, 'get_skill_by_id', AsyncMock(return_value=skill))
    monkeypatch.setattr(
        skill_files_router_module.SkillFiles, 'remove_file_from_skill_by_id', AsyncMock(return_value=True)
    )

    app = _make_app()
    res = TestClient(app).post('/api/v1/skills/id/skill-1/files/remove', json={'file_id': 'file-1'})

    assert res.status_code == 200
    assert res.json() is True


# ---------------------------------------------------------------------------
# List — GET /id/{id}/files
# ---------------------------------------------------------------------------


def test_list_files_returns_items_and_total(monkeypatch):
    skill = _make_skill()

    list_response = SimpleNamespace(
        items=[
            SimpleNamespace(
                id='file-1',
                user_id='user-1',
                filename='guide.md',
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
    assert len(body['items']) == 1


def test_list_files_skill_not_found(monkeypatch):
    monkeypatch.setattr(skill_files_router_module.Skills, 'get_skill_by_id', AsyncMock(return_value=None))

    app = _make_app()
    res = TestClient(app).get('/api/v1/skills/id/missing/files')

    assert res.status_code == 404
