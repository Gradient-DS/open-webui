import pytest
from open_webui.utils.web_search_state import web_search_state

AUTO = {'web_search': True}
ALWAYS = {'web_search': True, 'web_search_required': True}
OFF = {'web_search': False}


@pytest.mark.parametrize(
    ('features', 'state'),
    [(ALWAYS, 'required'), (AUTO, 'auto'), (OFF, 'off'), ({'web_search_required': True}, 'off'), (None, 'off')],
)
def test_web_search_state_reads_both_flags(features: dict | None, state: str) -> None:
    assert web_search_state(features) == state
