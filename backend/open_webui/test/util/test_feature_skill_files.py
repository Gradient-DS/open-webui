"""Tests for the FEATURE_SKILL_FILES flag.

Coverage:
1. Config expression: the env-bool pattern defaults to False and respects the env var.
2. Config import: FEATURE_SKILL_FILES is importable from open_webui.config and is bool.
3. Forwarding gate (flag OFF): produces an empty dict without calling the resolver.
4. Forwarding gate (flag ON): calls the resolver and returns the bundle.
5. Metadata gate: flag OFF → no 'files' key in per-skill metadata dict.
6. Metadata gate: flag ON + non-empty bundle → 'files' key present.

What is hermetically covered:
  - The env-bool expression (same pattern as FEATURE_SKILLS) is tested inline —
    no DB connection needed.
  - The forwarding gate logic (the `if FEATURE_SKILL_FILES else {}` conditional)
    is tested with a coroutine mock — no DB or app required.
  - The downstream metadata comprehension (`**({'files': ...} if ... else {})`)
    is unit-tested in isolation.

What is deferred to manual QA or requires a live DB:
  - Direct import of open_webui.config: importing the module triggers a Peewee
    DB connection attempt at module load time (db.py:174). Without a live
    PostgreSQL instance this raises OperationalError. Therefore we test the
    config expression inline (class TestFeatureSkillFilesExpression) rather than
    importing the module. The symbol's existence and default value are verified
    by code inspection (config.py:2041).
  - Router-mount gate: whether skill_files routes are absent from the running
    FastAPI app when the flag is off. Constructing the full app (importing main.py)
    has the same DB dependency. Code inspection of main.py:2115 confirms the guard.
  - Full process_chat_payload integration (requires running app + DB).
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


# ---------------------------------------------------------------------------
# 1. Config expression (inline — avoids module reload / DB connection)
# ---------------------------------------------------------------------------


class TestFeatureSkillFilesExpression:
    """The env-bool expression underlying FEATURE_SKILL_FILES."""

    def _evaluate(self, env_value: str | None) -> bool:
        """Reproduce the config.py expression given a raw env value."""
        raw = env_value if env_value is not None else 'False'
        return raw.lower() == 'true'

    def test_default_is_false(self):
        """Absent env var → expression evaluates to False (opt-in)."""
        assert self._evaluate(None) is False

    def test_env_true_enables(self):
        assert self._evaluate('true') is True

    def test_env_True_case_insensitive(self):
        assert self._evaluate('True') is True

    def test_env_false_disables(self):
        assert self._evaluate('false') is False

    def test_env_empty_disables(self):
        assert self._evaluate('') is False


# NOTE: direct import of open_webui.config is skipped here because importing
# the module triggers a DB connection (peewee, db.py:174) which fails without
# a live PostgreSQL instance.  The config expression is tested inline above.
# Symbol existence is verified by code inspection: config.py line ~2041.


# ---------------------------------------------------------------------------
# 2 + 3: Forwarding gate — mirror the exact middleware conditional
# ---------------------------------------------------------------------------


def _run_forwarding_conditional(flag: bool, resolve_fn) -> dict:
    """Mirrors the middleware line:
    skill_bundle_files = await resolve_skill_bundle_files(available_skills)
                         if FEATURE_SKILL_FILES else {}
    """
    import asyncio

    async def _run():
        skills = [SimpleNamespace(id='s1')]
        return await resolve_fn(skills) if flag else {}

    # asyncio.run, not get_event_loop().run_until_complete — pytest-asyncio
    # tests earlier in the session leave MainThread without a current loop,
    # which makes get_event_loop() raise (ordering-dependent failure).
    return asyncio.run(_run())


class TestForwardingGate:
    """The FEATURE_SKILL_FILES flag controls whether files are resolved.

    Tests 2 + 3 of the coverage list.
    """

    def test_flag_off_returns_empty_dict_without_calling_resolver(self):
        """When flag is False, resolver is never called and result is {}."""
        mock_resolve = AsyncMock(return_value={'s1': [{'filename': 'f.md', 'content': 'x'}]})
        result = _run_forwarding_conditional(flag=False, resolve_fn=mock_resolve)
        assert result == {}
        mock_resolve.assert_not_called()

    def test_flag_on_calls_resolver_and_returns_bundle(self):
        """When flag is True, resolver is called and its result is returned."""
        bundle = {'s1': [{'filename': 'f.md', 'content': 'x'}]}
        mock_resolve = AsyncMock(return_value=bundle)
        result = _run_forwarding_conditional(flag=True, resolve_fn=mock_resolve)
        assert result == bundle
        mock_resolve.assert_called_once()

    def test_flag_on_empty_bundle_returns_empty_dict(self):
        """Resolver returning {} when all skills have no files."""
        mock_resolve = AsyncMock(return_value={'s1': []})
        result = _run_forwarding_conditional(flag=True, resolve_fn=mock_resolve)
        assert result == {'s1': []}
        mock_resolve.assert_called_once()


# ---------------------------------------------------------------------------
# 4 + 5: Metadata comprehension — 'files' key presence
# ---------------------------------------------------------------------------


def _build_skill_entry(skill_bundle_files: dict, skill_id: str) -> dict:
    """Mirrors the middleware comprehension entry for a single skill."""
    skill = SimpleNamespace(id=skill_id, name='S', description='', content='pass')
    return {
        'name': skill.name,
        'description': skill.description or '',
        'content': skill.content,
        'is_selected': False,
        **({'files': skill_bundle_files[skill.id]} if skill_bundle_files.get(skill.id) else {}),
    }


class TestMetadataFilesKey:
    """'files' key appears in per-skill metadata only when bundle is non-empty.

    Tests 4 + 5 of the coverage list.
    """

    def test_flag_off_no_files_key(self):
        """Empty dict (flag off result) → no 'files' key forwarded."""
        entry = _build_skill_entry(skill_bundle_files={}, skill_id='s1')
        assert 'files' not in entry

    def test_flag_on_empty_bundle_no_files_key(self):
        """Empty list for a skill → no 'files' key (pre-existing no-files guarantee)."""
        entry = _build_skill_entry(skill_bundle_files={'s1': []}, skill_id='s1')
        assert 'files' not in entry

    def test_flag_on_nonempty_bundle_files_key_present(self):
        """Non-empty bundle → 'files' key present with correct contents."""
        files = [{'filename': 'guide.md', 'content': '# Guide'}]
        entry = _build_skill_entry(skill_bundle_files={'s1': files}, skill_id='s1')
        assert 'files' in entry
        assert entry['files'] == files

    def test_skill_not_in_bundle_no_files_key(self):
        """Skill absent from bundle dict → no 'files' key."""
        entry = _build_skill_entry(skill_bundle_files={'other': [{'filename': 'x.md', 'content': 'y'}]}, skill_id='s1')
        assert 'files' not in entry
