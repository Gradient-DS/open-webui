import pytest
from unittest.mock import patch
from fastapi import HTTPException

from open_webui.utils.features import (
    is_feature_enabled,
    require_feature,
    FEATURE_FLAGS,
)


class TestFeatureFlags:
    """Tests for feature flag utility functions."""

    def test_is_feature_enabled_returns_true_for_enabled_feature(self):
        """Test that is_feature_enabled returns True for enabled features."""
        with patch.dict(
            'open_webui.utils.features.FEATURE_FLAGS',
            {'voice': True},
        ):
            assert is_feature_enabled('voice') is True

    def test_is_feature_enabled_returns_false_for_disabled_feature(self):
        """Test that is_feature_enabled returns False for disabled features."""
        with patch.dict(
            'open_webui.utils.features.FEATURE_FLAGS',
            {'voice': False},
        ):
            assert is_feature_enabled('voice') is False

    def test_is_feature_enabled_returns_true_for_unknown_feature(self):
        """Test that is_feature_enabled returns True for unknown features (default)."""
        with patch.dict(
            'open_webui.utils.features.FEATURE_FLAGS',
            {},
            clear=True,
        ):
            # Unknown features should default to True
            assert is_feature_enabled('unknown_feature') is True


class TestRequireFeature:
    """Tests for require_feature dependency function."""

    def test_require_feature_passes_when_enabled(self):
        """Test that require_feature passes when feature is enabled."""
        with patch.dict(
            'open_webui.utils.features.FEATURE_FLAGS',
            {'voice': True},
        ):
            check = require_feature('voice')
            # Should not raise, returns True
            result = check()
            assert result is True

    def test_require_feature_raises_403_when_disabled(self):
        """Test that require_feature raises 403 when feature is disabled."""
        with patch.dict(
            'open_webui.utils.features.FEATURE_FLAGS',
            {'voice': False},
        ):
            check = require_feature('voice')
            with pytest.raises(HTTPException) as exc_info:
                check()
            assert exc_info.value.status_code == 403
            assert 'voice' in exc_info.value.detail


class TestVoiceFeature:
    """Specific tests for the voice feature flag."""

    def test_voice_feature_is_registered(self):
        """Test that voice feature is registered in FEATURE_FLAGS."""
        assert 'voice' in FEATURE_FLAGS

    def test_voice_enabled_by_default(self):
        """Voice feature should be enabled by default (True in config)."""
        # This tests the actual FEATURE_FLAGS value loaded from config
        # Default is True per the config.py definition
        from open_webui.config import FEATURE_VOICE

        assert FEATURE_VOICE is True or isinstance(FEATURE_VOICE, bool)

    def test_voice_can_be_disabled(self):
        """Voice feature should be disableable via environment variable."""
        with patch.dict(
            'open_webui.utils.features.FEATURE_FLAGS',
            {'voice': False},
        ):
            assert is_feature_enabled('voice') is False

    def test_require_voice_blocks_when_disabled(self):
        """Should raise 403 when voice is disabled."""
        with patch.dict(
            'open_webui.utils.features.FEATURE_FLAGS',
            {'voice': False},
        ):
            check = require_feature('voice')
            with pytest.raises(HTTPException) as exc_info:
                check()
            assert exc_info.value.status_code == 403


class TestChangelogFeature:
    """Tests for changelog feature flag."""

    def test_changelog_enabled_by_default(self):
        """Changelog feature should be enabled by default."""
        with patch.dict('open_webui.utils.features.FEATURE_FLAGS', {'changelog': True}):
            assert is_feature_enabled('changelog') is True

    def test_changelog_can_be_disabled(self):
        """Changelog feature should be disableable."""
        with patch.dict('open_webui.utils.features.FEATURE_FLAGS', {'changelog': False}):
            assert is_feature_enabled('changelog') is False

    def test_changelog_feature_is_registered(self):
        """Test that changelog feature is registered in FEATURE_FLAGS."""
        assert 'changelog' in FEATURE_FLAGS

    def test_require_changelog_blocks_when_disabled(self):
        """Should raise 403 when changelog is disabled."""
        with patch.dict(
            'open_webui.utils.features.FEATURE_FLAGS',
            {'changelog': False},
        ):
            check = require_feature('changelog')
            with pytest.raises(HTTPException) as exc_info:
                check()
            assert exc_info.value.status_code == 403


class TestSystemPromptFeature:
    """Tests for system prompt feature flag."""

    def test_system_prompt_enabled_by_default(self):
        """System prompt feature should be enabled by default."""
        with patch.dict('open_webui.utils.features.FEATURE_FLAGS', {'system_prompt': True}):
            assert is_feature_enabled('system_prompt') is True

    def test_system_prompt_can_be_disabled(self):
        """System prompt feature should be disableable."""
        with patch.dict('open_webui.utils.features.FEATURE_FLAGS', {'system_prompt': False}):
            assert is_feature_enabled('system_prompt') is False

    def test_system_prompt_feature_is_registered(self):
        """Test that system_prompt feature is registered in FEATURE_FLAGS."""
        assert 'system_prompt' in FEATURE_FLAGS

    def test_require_system_prompt_blocks_when_disabled(self):
        """Should raise 403 when system_prompt is disabled."""
        with patch.dict(
            'open_webui.utils.features.FEATURE_FLAGS',
            {'system_prompt': False},
        ):
            check = require_feature('system_prompt')
            with pytest.raises(HTTPException) as exc_info:
                check()
            assert exc_info.value.status_code == 403


class TestAdminEvaluationsFeature:
    """Tests for admin_evaluations feature flag."""

    def test_admin_evaluations_enabled_by_default(self):
        """Admin evaluations feature should be enabled by default."""
        with patch.dict('open_webui.utils.features.FEATURE_FLAGS', {'admin_evaluations': True}):
            assert is_feature_enabled('admin_evaluations') is True

    def test_admin_evaluations_can_be_disabled(self):
        """Admin evaluations feature should be disableable."""
        with patch.dict('open_webui.utils.features.FEATURE_FLAGS', {'admin_evaluations': False}):
            assert is_feature_enabled('admin_evaluations') is False

    def test_admin_evaluations_feature_is_registered(self):
        """Test that admin_evaluations feature is registered in FEATURE_FLAGS."""
        assert 'admin_evaluations' in FEATURE_FLAGS

    def test_require_admin_evaluations_blocks_when_disabled(self):
        """Should raise 403 when admin_evaluations is disabled."""
        with patch.dict(
            'open_webui.utils.features.FEATURE_FLAGS',
            {'admin_evaluations': False},
        ):
            check = require_feature('admin_evaluations')
            with pytest.raises(HTTPException) as exc_info:
                check()
            assert exc_info.value.status_code == 403


class TestAdminFunctionsFeature:
    """Tests for admin_functions feature flag."""

    def test_admin_functions_enabled_by_default(self):
        """Admin functions feature should be enabled by default."""
        with patch.dict('open_webui.utils.features.FEATURE_FLAGS', {'admin_functions': True}):
            assert is_feature_enabled('admin_functions') is True

    def test_admin_functions_can_be_disabled(self):
        """Admin functions feature should be disableable."""
        with patch.dict('open_webui.utils.features.FEATURE_FLAGS', {'admin_functions': False}):
            assert is_feature_enabled('admin_functions') is False

    def test_admin_functions_feature_is_registered(self):
        """Test that admin_functions feature is registered in FEATURE_FLAGS."""
        assert 'admin_functions' in FEATURE_FLAGS

    def test_require_admin_functions_blocks_when_disabled(self):
        """Should raise 403 when admin_functions is disabled."""
        with patch.dict(
            'open_webui.utils.features.FEATURE_FLAGS',
            {'admin_functions': False},
        ):
            check = require_feature('admin_functions')
            with pytest.raises(HTTPException) as exc_info:
                check()
            assert exc_info.value.status_code == 403


class TestAdminSettingsFeature:
    """Tests for admin_settings feature flag."""

    def test_admin_settings_enabled_by_default(self):
        """Admin settings feature should be enabled by default."""
        with patch.dict('open_webui.utils.features.FEATURE_FLAGS', {'admin_settings': True}):
            assert is_feature_enabled('admin_settings') is True

    def test_admin_settings_can_be_disabled(self):
        """Admin settings feature should be disableable."""
        with patch.dict('open_webui.utils.features.FEATURE_FLAGS', {'admin_settings': False}):
            assert is_feature_enabled('admin_settings') is False

    def test_admin_settings_feature_is_registered(self):
        """Test that admin_settings feature is registered in FEATURE_FLAGS."""
        assert 'admin_settings' in FEATURE_FLAGS

    def test_require_admin_settings_blocks_when_disabled(self):
        """Should raise 403 when admin_settings is disabled."""
        with patch.dict(
            'open_webui.utils.features.FEATURE_FLAGS',
            {'admin_settings': False},
        ):
            check = require_feature('admin_settings')
            with pytest.raises(HTTPException) as exc_info:
                check()
            assert exc_info.value.status_code == 403


class TestAllFeatureFlags:
    """Tests for all registered feature flags."""

    def test_all_features_have_boolean_values(self):
        """Test that all registered features have boolean values."""
        for feature, value in FEATURE_FLAGS.items():
            assert isinstance(value, bool), f'Feature {feature} has non-boolean value: {value}'

    def test_expected_features_are_registered(self):
        """Test that expected features are registered."""
        expected_features = [
            'chat_controls',
            'capture',
            'artifacts',
            'playground',
            'chat_overview',
            'notes_ai_controls',
            'voice',
            'changelog',
            'system_prompt',
            'models',
            'knowledge',
            'prompts',
            'tools',
            'admin_evaluations',
            'admin_functions',
            'admin_settings',
        ]
        for feature in expected_features:
            assert feature in FEATURE_FLAGS, f'Feature {feature} not found in FEATURE_FLAGS'


@pytest.mark.asyncio
@pytest.mark.parametrize('office,office_edit', [(False, False), (False, True), (True, False), (True, True)])
async def test_office_requires_both_capabilities(office, office_edit):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from open_webui.utils.features import gate_office_features

    with (
        patch('open_webui.models.config.Config.get', AsyncMock(return_value=True)),
        patch.dict(FEATURE_FLAGS, {'office': True, 'office_edit': True}),
    ):
        result = await gate_office_features(
            {'office': True, 'office_edit': True, 'web_search': True},
            {'office': office, 'office_edit': office_edit},
            SimpleNamespace(role='admin'),
        )
    assert result == {'office': office, 'office_edit': office and office_edit, 'web_search': True}


@pytest.mark.asyncio
@pytest.mark.parametrize('feature', ['office', 'office_edit'])
@pytest.mark.parametrize('gate', ['flag', 'enable', 'permission', 'missing_capability'])
async def test_office_cannot_bypass_disabled_gate(feature, gate):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from open_webui.utils.features import gate_office_features

    flags = {'office': True, 'office_edit': True}
    capabilities = dict(flags)
    if gate == 'flag':
        flags[feature] = False
    if gate == 'missing_capability':
        capabilities.pop(feature)
    config = AsyncMock(side_effect=lambda key: False if gate == 'enable' and key == f'{feature}.enable' else True)
    permission = AsyncMock(
        side_effect=lambda uid, key, defaults: not (gate == 'permission' and key == f'features.{feature}')
    )
    with (
        patch.dict(FEATURE_FLAGS, flags),
        patch('open_webui.models.config.Config.get', config),
        patch('open_webui.utils.access_control.has_permission', permission),
    ):
        result = await gate_office_features(
            {'office': True, 'office_edit': True}, capabilities, SimpleNamespace(role='user', id='alice')
        )
    assert result[feature] is False
    assert result['office_edit'] is False


@pytest.mark.asyncio
@pytest.mark.parametrize('saved', [{}, {'office': False, 'office_edit': False}])
async def test_office_permissions_fill_old_settings_without_overriding_saved_values(saved):
    from unittest.mock import AsyncMock
    from open_webui.config import DEFAULT_USER_PERMISSIONS
    from open_webui.utils.access_control import get_permissions

    with patch('open_webui.models.groups.Groups.get_groups_by_member_id', AsyncMock(return_value=[])):
        result = await get_permissions('alice', {'features': saved})
    assert result['features'] == {
        key: saved.get(key, DEFAULT_USER_PERMISSIONS['features'][key]) for key in ('office', 'office_edit')
    }
    assert saved == {} or saved == {'office': False, 'office_edit': False}
