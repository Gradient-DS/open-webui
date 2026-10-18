"""[Gradient] Tests for never-empty error rendering and structured error fields.

The GRA-174 root defect: ``str(asyncio.TimeoutError())`` is ``''``.
aiohttp raises a bare TimeoutError on total-timeout expiry, so
``f'Agent API error: {e}'` rendered as a string ending at the colon —
in the log *and* in the user's error banner.
"""

import asyncio

from open_webui.utils.log_context import describe_exception, error_fields


def test_describe_exception_never_returns_empty_for_bare_timeout():
    assert str(asyncio.TimeoutError()) == ''
    described = describe_exception(asyncio.TimeoutError())
    assert described.strip() != ''
    assert 'TimeoutError' in described


def test_describe_exception_includes_message_when_present():
    described = describe_exception(ValueError('bad input'))
    assert 'ValueError' in described
    assert 'bad input' in described


def test_error_fields_shape():
    fields = error_fields(asyncio.TimeoutError())
    assert fields['error']['type'] == 'TimeoutError'
    assert fields['error']['chain'] == ['TimeoutError']


def test_error_fields_walks_the_chain():
    try:
        try:
            raise ConnectionError('upstream gone')
        except ConnectionError as inner:
            raise ValueError('wrapping') from inner
    except ValueError as exc:
        assert error_fields(exc)['error']['chain'] == [
            'ValueError: wrapping',
            'ConnectionError: upstream gone',
        ]


def test_error_fields_terminates_on_a_cyclic_chain():
    first = ValueError('a')
    second = ValueError('b')
    first.__cause__ = second
    second.__cause__ = first
    assert len(error_fields(first)['error']['chain']) <= 2
