import pytest
from open_webui.utils.tool_state import tool_state


@pytest.mark.parametrize('key', ['web_search', 'document_writer'])
@pytest.mark.parametrize(
    ('enabled', 'required', 'state'),
    [
        (True, True, 'required'),
        (True, False, 'auto'),
        (False, True, 'off'),
        (False, False, 'off'),
        ('yes', 1, 'required'),
        (1, None, 'auto'),
        (None, True, 'off'),
    ],
)
def test_tool_state_reads_both_flags(key: str, enabled, required, state: str) -> None:
    """Required applies only when the tool itself is enabled."""
    assert tool_state({key: enabled, f'{key}_required': required}, key) == state


@pytest.mark.parametrize('features', [None, {}, {'unrelated': True}, {'document_writer_required': True}])
def test_tool_state_defaults_to_off(features: dict | None) -> None:
    """Missing feature flags never enable a tool."""
    assert tool_state(features, 'document_writer') == 'off'
