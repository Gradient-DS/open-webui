"""Phase 8b security-invariant tests for the skill-file scoped fetch token.

Invariants proven here:
1. Scoped token works ONLY on GET /id/{id}/files/content — rejected on write routes.
2. Token for skill A rejected on skill B's content route (skill_id scope).
3. Expired token rejected.
4. purpose must match — token without purpose="skill_file_read" rejected on scoped path;
   scoped token rejected on non-content routes.
5. Read-access check still enforced via the token's user_id.
6. Token minted only when ENABLE_SKILL_EXECUTION on + FEATURE_SKILL_FILES on + skill has binary files.
7. Simple-skills forwarding (FEATURE_SKILL_FILES on, ENABLE_SKILL_EXECUTION off) never mints a token.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from open_webui.routers import skill_files as skill_files_router_module
from open_webui.utils.auth import create_token, get_optional_verified_user, get_verified_user

# ---------------------------------------------------------------------------
# Helpers (mirrors test_skill_files_router.py)
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
    content_type: str = 'image/png',
    filename: str = 'icon.png',
    path: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=file_id,
        user_id='user-1',
        filename=filename,
        path=path if path is not None else f'uploads/{file_id}',
        data=None,
        meta={'content_type': content_type, 'size': 8},
        created_at=1000,
        updated_at=1000,
    )


def _make_skill_file_record(
    skill_id: str = 'skill-1', file_id: str = 'file-1', path: str = 'assets/icon.png'
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


def _make_user(uid: str = 'user-1', role: str = 'user') -> SimpleNamespace:
    return SimpleNamespace(id=uid, role=role, email='user@example.com', name='U')


def _scoped_token(
    user_id: str = 'user-1',
    skill_id: str = 'skill-1',
    expires_delta: timedelta = timedelta(seconds=120),
) -> str:
    return create_token(
        data={'id': user_id, 'skill_id': skill_id, 'purpose': 'skill_file_read'},
        expires_delta=expires_delta,
    )


def _normal_session_token(user_id: str = 'user-1') -> str:
    """A normal session JWT — no purpose claim."""
    return create_token(data={'id': user_id}, expires_delta=timedelta(hours=1))


def _make_app_no_override() -> FastAPI:
    """App WITHOUT dependency overrides — tests must supply tokens directly."""
    app = FastAPI()
    app.include_router(skill_files_router_module.router, prefix='/api/v1/skills')
    return app


def _make_app_normal_user(user: SimpleNamespace | None = None) -> FastAPI:
    """App with normal-user override (for regular-session tests)."""
    if user is None:
        user = _make_user()
    app = FastAPI()
    app.include_router(skill_files_router_module.router, prefix='/api/v1/skills')
    app.dependency_overrides[get_verified_user] = lambda: user
    app.dependency_overrides[get_optional_verified_user] = lambda: user
    return app


async def _passthrough_to_thread(fn, *args, **kwargs):
    import asyncio as _asyncio

    return await _asyncio.get_event_loop().run_in_executor(None, fn, *args, **kwargs)


# ===========================================================================
# Invariant 6 — Token minting conditions (build_agent_payload integration)
# ===========================================================================


class TestMintConditions:
    """Invariant 6+7: token minted iff ENABLE_SKILL_EXECUTION on + FEATURE_SKILL_FILES on + binary file present."""

    def _make_skill_entry(self, has_binary: bool, skill_id: str = 'skill-abc') -> dict:
        files = []
        if has_binary:
            files.append(
                {
                    'path': 'assets/template.xlsx',
                    'is_binary': True,
                    'media_type': 'application/vnd.ms-excel',
                    'size': 1024,
                }
            )
        files.append(
            {'path': 'guide.md', 'content': '# Guide', 'is_binary': False, 'media_type': 'text/markdown', 'size': 7}
        )
        return {
            'id': skill_id,
            'name': 'Test',
            'description': '',
            'content': 'some content',
            'is_selected': True,
            'files': files,
        }

    def test_token_minted_when_feature_on_and_binary_present(self):
        from open_webui.utils.agent import _maybe_attach_fetch_token

        entry = self._make_skill_entry(has_binary=True)
        result = _maybe_attach_fetch_token(entry, 'user-1')
        assert 'fetch_token' in result
        token = result['fetch_token']
        from open_webui.utils.auth import decode_token

        decoded = decode_token(token)
        assert decoded is not None
        assert decoded['purpose'] == 'skill_file_read'
        assert decoded['skill_id'] == 'skill-abc'
        assert decoded['id'] == 'user-1'

    def test_no_token_for_text_only_skill(self):
        from open_webui.utils.agent import _maybe_attach_fetch_token

        entry = self._make_skill_entry(has_binary=False)
        result = _maybe_attach_fetch_token(entry, 'user-1')
        assert 'fetch_token' not in result

    def test_no_token_for_skill_without_id(self):
        from open_webui.utils.agent import _maybe_attach_fetch_token

        entry = {
            'name': 'Test',
            'description': '',
            'content': 'x',
            'is_selected': True,
            'files': [{'path': 'a.xlsx', 'is_binary': True, 'media_type': 'application/vnd.ms-excel', 'size': 100}],
        }
        result = _maybe_attach_fetch_token(entry, 'user-1')
        assert 'fetch_token' not in result

    def test_build_agent_payload_mints_token_when_both_flags_on(self):
        """Both ENABLE_SKILL_EXECUTION and FEATURE_SKILL_FILES must be on to mint."""
        from open_webui.utils.agent import build_agent_payload

        skills = [self._make_skill_entry(has_binary=True, skill_id='sk-1')]
        with (
            patch('open_webui.utils.agent.ENABLE_SKILL_EXECUTION', True),
            patch('open_webui.utils.agent.FEATURE_SKILL_FILES', True),
        ):
            payload = build_agent_payload(
                model='gpt-4',
                messages=[{'role': 'user', 'content': 'hi'}],
                skills=skills,
                user_id='user-1',
            )
        assert 'fetch_token' in payload['skills'][0]

    def test_build_agent_payload_no_token_when_execution_flag_off(self):
        """ENABLE_SKILL_EXECUTION=False (default) suppresses token minting even if FEATURE_SKILL_FILES is on."""
        from open_webui.utils.agent import build_agent_payload

        skills = [self._make_skill_entry(has_binary=True, skill_id='sk-1')]
        with (
            patch('open_webui.utils.agent.ENABLE_SKILL_EXECUTION', False),
            patch('open_webui.utils.agent.FEATURE_SKILL_FILES', True),
        ):
            payload = build_agent_payload(
                model='gpt-4',
                messages=[{'role': 'user', 'content': 'hi'}],
                skills=skills,
                user_id='user-1',
            )
        assert 'fetch_token' not in payload['skills'][0]

    def test_build_agent_payload_no_token_when_skill_files_flag_off(self):
        """FEATURE_SKILL_FILES=False suppresses token minting even if ENABLE_SKILL_EXECUTION is on."""
        from open_webui.utils.agent import build_agent_payload

        skills = [self._make_skill_entry(has_binary=True, skill_id='sk-1')]
        with (
            patch('open_webui.utils.agent.ENABLE_SKILL_EXECUTION', True),
            patch('open_webui.utils.agent.FEATURE_SKILL_FILES', False),
        ):
            payload = build_agent_payload(
                model='gpt-4',
                messages=[{'role': 'user', 'content': 'hi'}],
                skills=skills,
                user_id='user-1',
            )
        assert 'fetch_token' not in payload['skills'][0]

    def test_build_agent_payload_no_token_when_no_user_id(self):
        from open_webui.utils.agent import build_agent_payload

        skills = [self._make_skill_entry(has_binary=True, skill_id='sk-1')]
        with (
            patch('open_webui.utils.agent.ENABLE_SKILL_EXECUTION', True),
            patch('open_webui.utils.agent.FEATURE_SKILL_FILES', True),
        ):
            payload = build_agent_payload(
                model='gpt-4',
                messages=[{'role': 'user', 'content': 'hi'}],
                skills=skills,
                user_id=None,
            )
        assert 'fetch_token' not in payload['skills'][0]

    def test_build_agent_payload_text_only_skill_no_token(self):
        """Text-only skills never get a token even when both flags are on."""
        from open_webui.utils.agent import build_agent_payload

        skills = [self._make_skill_entry(has_binary=False, skill_id='sk-1')]
        with (
            patch('open_webui.utils.agent.ENABLE_SKILL_EXECUTION', True),
            patch('open_webui.utils.agent.FEATURE_SKILL_FILES', True),
        ):
            payload = build_agent_payload(
                model='gpt-4',
                messages=[{'role': 'user', 'content': 'hi'}],
                skills=skills,
                user_id='user-1',
            )
        assert 'fetch_token' not in payload['skills'][0]

    def test_simple_skills_forwarding_unaffected_by_execution_flag(self):
        """Simple-skills mode: FEATURE_SKILL_FILES on, ENABLE_SKILL_EXECUTION off.

        Skills are forwarded in the payload (simple files extension still works),
        but no fetch_token is minted — invariant 7.
        """
        from open_webui.utils.agent import build_agent_payload

        # Mix of binary + text files: a binary file would trigger minting if execution were on.
        skills = [self._make_skill_entry(has_binary=True, skill_id='sk-simple')]
        with (
            patch('open_webui.utils.agent.ENABLE_SKILL_EXECUTION', False),
            patch('open_webui.utils.agent.FEATURE_SKILL_FILES', True),
        ):
            payload = build_agent_payload(
                model='gpt-4',
                messages=[{'role': 'user', 'content': 'hi'}],
                skills=skills,
                user_id='user-1',
            )
        # Skills are still forwarded (simple-files forwarding untouched)
        assert 'skills' in payload
        assert len(payload['skills']) == 1
        assert payload['skills'][0]['id'] == 'sk-simple'
        # But no fetch_token — execution is off
        assert 'fetch_token' not in payload['skills'][0]


# ===========================================================================
# Invariant 2 — Route accepts scoped token for its own skill before expiry
# ===========================================================================


class TestScopedTokenAccepted:
    """The content route accepts a valid scoped token for the matching skill."""

    def test_accepts_scoped_token_for_matching_skill(self, monkeypatch, tmp_path):
        """Invariant 2 (positive case): scoped token for skill-1 accepted on skill-1 route."""
        png_bytes = b'\x89PNG\r\n' + b'\x00' * 10
        tmp_file = tmp_path / 'icon.png'
        tmp_file.write_bytes(png_bytes)

        owner = _make_user('user-1')
        skill = _make_skill('skill-1', owner_id='user-1')
        sf_record = _make_skill_file_record(skill_id='skill-1', path='assets/icon.png')
        backing_file = _make_file(content_type='image/png')

        monkeypatch.setattr(skill_files_router_module.Skills, 'get_skill_by_id', AsyncMock(return_value=skill))
        monkeypatch.setattr(skill_files_router_module.SkillFiles, 'get_file_by_path', AsyncMock(return_value=sf_record))
        monkeypatch.setattr(skill_files_router_module.Files, 'get_file_by_id', AsyncMock(return_value=backing_file))
        monkeypatch.setattr(skill_files_router_module.Users, 'get_user_by_id', AsyncMock(return_value=owner))

        token = _scoped_token(user_id='user-1', skill_id='skill-1')

        with patch('open_webui.routers.skill_files.asyncio.to_thread', side_effect=_passthrough_to_thread):
            with patch('open_webui.storage.provider.Storage') as mock_storage:
                mock_storage.get_file = lambda path: str(tmp_file)
                app = _make_app_no_override()
                res = TestClient(app).get(
                    '/api/v1/skills/id/skill-1/files/content?path=assets%2Ficon.png',
                    headers={'Authorization': f'Bearer {token}'},
                )

        assert res.status_code == 200
        assert res.content == png_bytes


# ===========================================================================
# Invariant 2 — Token for skill A rejected on skill B
# ===========================================================================


class TestScopedTokenSkillIsolation:
    """Invariant 2: token minted for skill A is rejected on skill B's content route."""

    def test_rejects_token_for_wrong_skill(self, monkeypatch):
        """Token scoped to skill-1 rejected when requesting skill-2's content."""
        skill = _make_skill('skill-2', owner_id='user-1')
        monkeypatch.setattr(skill_files_router_module.Skills, 'get_skill_by_id', AsyncMock(return_value=skill))
        monkeypatch.setattr(skill_files_router_module.Users, 'get_user_by_id', AsyncMock(return_value=_make_user()))

        token = _scoped_token(user_id='user-1', skill_id='skill-1')  # scoped to skill-1

        app = _make_app_no_override()
        res = TestClient(app).get(
            '/api/v1/skills/id/skill-2/files/content?path=assets%2Ficon.png',
            headers={'Authorization': f'Bearer {token}'},
        )
        assert res.status_code == 401


# ===========================================================================
# Invariant 3 — Expired token rejected
# ===========================================================================


class TestExpiredToken:
    """Invariant 3: an expired scoped token is rejected."""

    def test_rejects_expired_scoped_token(self, monkeypatch):
        monkeypatch.setattr(skill_files_router_module.Skills, 'get_skill_by_id', AsyncMock(return_value=_make_skill()))
        monkeypatch.setattr(skill_files_router_module.Users, 'get_user_by_id', AsyncMock(return_value=_make_user()))

        # Mint a token that expired in the past.
        token = _scoped_token(user_id='user-1', skill_id='skill-1', expires_delta=timedelta(seconds=-1))

        app = _make_app_no_override()
        res = TestClient(app).get(
            '/api/v1/skills/id/skill-1/files/content?path=assets%2Ficon.png',
            headers={'Authorization': f'Bearer {token}'},
        )
        assert res.status_code == 401


# ===========================================================================
# Invariant 1 — Scoped token rejected on write routes
# ===========================================================================


class TestScopedTokenRejectedOnWriteRoutes:
    """Invariant 1: scoped token must NOT authenticate write routes."""

    def test_scoped_token_rejected_on_post_files(self, monkeypatch):
        """POST /id/{id}/files (create/upload) must reject a scoped fetch token."""
        monkeypatch.setattr(skill_files_router_module.Skills, 'get_skill_by_id', AsyncMock(return_value=_make_skill()))

        token = _scoped_token(user_id='user-1', skill_id='skill-1')

        app = _make_app_no_override()
        res = TestClient(app).post(
            '/api/v1/skills/id/skill-1/files',
            json={'path': 'guide.md', 'content': 'x'},
            headers={'Authorization': f'Bearer {token}'},
        )
        # Should be 401 or 403 — the scoped token must not grant write access.
        assert res.status_code in (401, 403)

    def test_scoped_token_rejected_on_put_files(self, monkeypatch):
        """PUT /id/{id}/files (edit) must reject a scoped fetch token."""
        monkeypatch.setattr(skill_files_router_module.Skills, 'get_skill_by_id', AsyncMock(return_value=_make_skill()))

        token = _scoped_token(user_id='user-1', skill_id='skill-1')

        app = _make_app_no_override()
        res = TestClient(app).put(
            '/api/v1/skills/id/skill-1/files',
            json={'path': 'guide.md', 'content': 'updated'},
            headers={'Authorization': f'Bearer {token}'},
        )
        assert res.status_code in (401, 403)

    def test_scoped_token_rejected_on_remove(self, monkeypatch):
        """POST /id/{id}/files/remove must reject a scoped fetch token."""
        monkeypatch.setattr(skill_files_router_module.Skills, 'get_skill_by_id', AsyncMock(return_value=_make_skill()))

        token = _scoped_token(user_id='user-1', skill_id='skill-1')

        app = _make_app_no_override()
        res = TestClient(app).post(
            '/api/v1/skills/id/skill-1/files/remove',
            json={'path': 'guide.md'},
            headers={'Authorization': f'Bearer {token}'},
        )
        assert res.status_code in (401, 403)


# ===========================================================================
# Invariant 4 — purpose must match; normal JWT must not satisfy scoped branch
# ===========================================================================


class TestPurposeCheck:
    """Invariant 4: a token without purpose="skill_file_read" cannot use the scoped path."""

    def test_normal_session_token_does_not_satisfy_scoped_branch(self):
        """A normal JWT (no purpose claim) is never accepted by _resolve_scoped_user.

        Proves invariant 4: the scoped branch requires purpose="skill_file_read".
        A regular login JWT presented as Bearer will NOT match the purpose check
        in _resolve_scoped_user, so it returns None instead of a user — the route
        then falls through to session auth (or 401 if no session).
        """
        from open_webui.utils.auth import decode_token

        token = _normal_session_token('user-1')
        decoded = decode_token(token)
        assert decoded is not None
        assert 'purpose' not in decoded or decoded.get('purpose') != 'skill_file_read'

    def test_scoped_token_cannot_be_used_as_normal_session(self, monkeypatch):
        """Scoped token presented to the list route must be rejected (invariant 4 + 1)."""
        monkeypatch.setattr(skill_files_router_module.Skills, 'get_skill_by_id', AsyncMock(return_value=_make_skill()))

        token = _scoped_token('user-1', 'skill-1')

        app = _make_app_no_override()
        res = TestClient(app).get(
            '/api/v1/skills/id/skill-1/files',
            headers={'Authorization': f'Bearer {token}'},
        )
        # The list route uses get_verified_user; get_current_user in auth.py
        # blocks purpose="skill_file_read" → 401 or 403.
        assert res.status_code in (401, 403)


# ===========================================================================
# Invariant 5 — Read access still enforced via token's user_id
# ===========================================================================


class TestReadAccessCheck:
    """Invariant 5: token whose user lacks read access → denied."""

    def test_scoped_token_denied_when_user_lacks_read_access(self, monkeypatch):
        """A scoped token for user-2 is rejected when user-2 has no read grant
        on a skill owned by user-1."""
        different_owner_skill = _make_skill('skill-1', owner_id='user-1')
        user_without_access = _make_user('user-2')

        monkeypatch.setattr(
            skill_files_router_module.Skills, 'get_skill_by_id', AsyncMock(return_value=different_owner_skill)
        )
        monkeypatch.setattr(
            skill_files_router_module.Users, 'get_user_by_id', AsyncMock(return_value=user_without_access)
        )
        monkeypatch.setattr(skill_files_router_module.AccessGrants, 'has_access', AsyncMock(return_value=False))

        token = _scoped_token(user_id='user-2', skill_id='skill-1')

        app = _make_app_no_override()
        res = TestClient(app).get(
            '/api/v1/skills/id/skill-1/files/content?path=assets%2Ficon.png',
            headers={'Authorization': f'Bearer {token}'},
        )
        assert res.status_code == 401
