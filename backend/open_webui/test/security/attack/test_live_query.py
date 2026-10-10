"""Live sealed-stack checks; run only by runtime-security.yml."""

import pytest
from . import query
from .pass_support import crash_details, fresh_surface


@pytest.fixture(scope='module')
def live_query():
    with fresh_surface() as (identities, parameters):
        yield query.drive_query_parameters(identities.admin, parameters)


def test_live_query_has_its_own_5xx_assertion(live_query):
    assert not live_query.crashes, crash_details(live_query)


def test_live_query_reports_its_own_reach(live_query, record_property):
    assert live_query.statuses.keys() | live_query.skipped.keys() == set(query.query_targets())
    assert live_query.config_restore_verified
    record_property('query_unentered', live_query.unentered)
    assert live_query.entered, 'No query request entered a handler'
