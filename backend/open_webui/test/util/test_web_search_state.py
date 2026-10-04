import pytest
from open_webui.utils.web_search_state import should_force_web_search, web_search_state

AUTO = {'web_search': True}
ALWAYS = {'web_search': True, 'web_search_required': True}
OFF = {'web_search': False}


@pytest.mark.parametrize(
    ('features', 'state'),
    [(ALWAYS, 'required'), (AUTO, 'auto'), (OFF, 'off'), ({'web_search_required': True}, 'off'), (None, 'off')],
)
def test_web_search_state_reads_both_flags(features: dict | None, state: str) -> None:
    assert web_search_state(features) == state


@pytest.mark.parametrize(
    ('features', 'function_calling', 'forced'),
    [
        (ALWAYS, 'native', True),
        (ALWAYS, 'legacy', True),
        (ALWAYS, None, True),
        (AUTO, 'legacy', True),
        (AUTO, 'native', False),
        (AUTO, None, False),
        (OFF, 'legacy', False),
        ({}, 'legacy', False),
    ],
)
def test_non_agent_turns_force_web_search_on_always_or_legacy_auto(
    features: dict, function_calling: str | None, forced: bool
) -> None:
    assert should_force_web_search(features, function_calling, route_to_agent=False) is forced


@pytest.mark.parametrize('features', [ALWAYS, AUTO, OFF])
def test_agent_turns_never_force_web_search(features: dict) -> None:
    assert should_force_web_search(features, 'legacy', route_to_agent=True) is False
