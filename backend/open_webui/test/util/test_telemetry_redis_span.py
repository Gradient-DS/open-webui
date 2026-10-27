"""Redis spans name the command and key, never the value written."""

from unittest.mock import MagicMock

from open_webui.utils.telemetry.constants import SpanAttributes
from open_webui.utils.telemetry.instrumentors import redis_request_hook


def _attributes(*args) -> dict:
    span = MagicMock()
    instance = MagicMock(spec=['connection_pool'])
    instance.connection_pool.connection_kwargs = {'host': 'redis', 'port': 6379, 'db': 0}
    redis_request_hook(span, instance, args, {})
    return span.set_attributes.call_args.args[0]


def test_value_is_left_out():
    attributes = _attributes('SET', 'chat:abc', '{"content": "fictional secret message"}')
    assert attributes[SpanAttributes.DB_STATEMENT] == 'SET chat:abc'
    assert attributes[SpanAttributes.DB_OPERATION] == 'SET'


def test_hash_fields_and_values_are_left_out():
    attributes = _attributes('HSET', 'session:1', 'messages', 'x' * 40_000_000)
    assert attributes[SpanAttributes.DB_STATEMENT] == 'HSET session:1'


def test_command_without_key():
    assert _attributes('PING')[SpanAttributes.DB_STATEMENT] == 'PING'
