"""[Gradient] The stdlib-to-loguru bridge keeps the `extra=` fields callers pass."""

import logging

from loguru import logger
from open_webui.utils.logger import InterceptHandler


def _captured(emit) -> list[dict]:
    records = []
    sink = logger.add(lambda message: records.append(message.record), level='DEBUG')
    stdlib = logging.getLogger('test.intercept')
    stdlib.handlers, stdlib.propagate, stdlib.level = [InterceptHandler()], False, logging.DEBUG
    try:
        emit(stdlib)
    finally:
        logger.remove(sink)
    return records


def test_extra_fields_reach_the_record():
    records = _captured(lambda log: log.warning('Reconnect skipped', extra={'user_id': 'u1', 'code': 'timeout'}))
    assert records[0]['message'] == 'Reconnect skipped'
    assert records[0]['extra']['user_id'] == 'u1'
    assert records[0]['extra']['code'] == 'timeout'


def test_standard_record_attributes_are_not_copied():
    records = _captured(lambda log: log.info('Plain %s', 'line'))
    assert records[0]['message'] == 'Plain line'
    assert not {'levelname', 'pathname', 'args', 'msg', 'lineno'} & set(records[0]['extra'])
