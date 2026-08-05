"""[Gradient] Tests for GRA-174 items 1.1 and 1.2.

1.1 — ``utils/agent.py`` interpolated the exception into both the log
line and the user-facing banner. aiohttp raises a bare
``asyncio.TimeoutError`` on total-timeout expiry, which stringifies to
``''``, so both ended at the colon.

1.2 — the outer client timeout (300s) sat *below* the agent's own retry
budget (3 attempts x 120s = 360s), so any request needing its retries
was structurally guaranteed to be cut off mid-flight.
"""

import asyncio
import json

import pytest

from open_webui.utils.agent import AGENT_API_TIMEOUT, _error_sse_chunk
from open_webui.utils.log_context import describe_exception


def test_error_chunk_is_never_empty_for_bare_timeout():
    chunk = _error_sse_chunk(f'Agent API error: {describe_exception(asyncio.TimeoutError())}')
    payload = json.loads(chunk.removeprefix('data: ').strip())
    message = payload['error']['message']
    assert message.strip() != 'Agent API error:'
    assert 'TimeoutError' in message


def test_outer_timeout_exceeds_the_inner_retry_budget():
    """The timeout rule: outer > attempts * per_attempt + overhead.

    agents-api runs 2 attempts at 120s = 240s. The outer budget must
    exceed that, or every retried request is cut off mid-flight.
    """
    inner_budget_seconds = 2 * 120
    assert AGENT_API_TIMEOUT > inner_budget_seconds


@pytest.mark.parametrize(
    'exc',
    [asyncio.TimeoutError(), ValueError(''), RuntimeError('real message')],
)
def test_no_exception_renders_an_empty_banner(exc):
    assert describe_exception(exc).strip() != ''
