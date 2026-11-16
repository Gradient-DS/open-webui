"""The schema step names where it failed without printing driver messages."""

import pytest

from open_webui.soev import migrate


def _raised(module: str, error: Exception) -> Exception:
    scope = {'__name__': module, 'error': error}
    try:
        exec('raise error', scope)
    except Exception as caught:
        return caught
    raise AssertionError('unreachable')


@pytest.mark.parametrize('module', sorted(migrate._FIXED_SENTENCE_MODULES))
def test_settings_and_schema_checks_keep_their_fixed_sentence(module):
    message = migrate._failure('pgvector schema', _raised(module, ValueError('Fictional setting is missing.')))
    assert message == (
        'Schema migration failed at pgvector schema (ValueError: Fictional setting is missing.); '
        'database upgrade did not complete.'
    )


def test_other_errors_show_only_their_class_and_sqlstate():
    class FictionalDriverError(Exception):
        sqlstate = '42501'

    error = _raised('fictional.driver', FictionalDriverError('row value fictional-secret'))
    message = migrate._failure('alembic upgrade', error)
    assert message == (
        'Schema migration failed at alembic upgrade (FictionalDriverError, SQLSTATE 42501); '
        'database upgrade did not complete.'
    )
    assert 'fictional-secret' not in message
