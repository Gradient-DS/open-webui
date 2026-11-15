"""Redis spans retain only an allowlisted command, with no key or value."""

from unittest.mock import MagicMock

from open_webui.utils.telemetry.instrumentors import redis_request_hook


def _attributes(*args) -> dict:
    span = MagicMock()
    instance = MagicMock(spec=['connection_pool'])
    instance.connection_pool.connection_kwargs = {'host': 'redis', 'port': 6379, 'db': 0}
    redis_request_hook(span, instance, args, {})
    span.update_name.assert_called_once_with('db.statement')
    span.set_attributes.assert_not_called()
    return dict(call.args for call in span.set_attribute.call_args_list)


def test_value_is_left_out():
    attributes = _attributes('SET', 'chat:abc', '{"content": "fictional secret message"}')
    assert attributes == {'db.operation.name': 'SET'}


def test_hash_fields_and_values_are_left_out():
    attributes = _attributes('HSET', 'session:1', 'messages', 'x' * 40_000_000)
    assert attributes == {'db.operation.name': 'STATEMENT'}


def test_command_without_key():
    assert _attributes('PING') == {'db.operation.name': 'STATEMENT'}
