"""Live sealed-stack checks; run only by runtime-security.yml."""

import pytest
from . import shapes
from .pass_support import crash_details, fresh_surface


@pytest.fixture(scope='module')
def live_shapes():
    with fresh_surface() as (identities, parameters):
        yield shapes.drive_shapes(identities.admin, parameters)


def test_live_shapes_has_its_own_5xx_assertion(live_shapes):
    assert not live_shapes.crashes, crash_details(live_shapes)


def test_live_shapes_reports_its_own_reach(live_shapes, record_property):
    assert live_shapes.statuses.keys() | live_shapes.skipped.keys() == live_shapes.expected
    assert live_shapes.expected == set(shapes.write_operations())
    assert live_shapes.config_restore_verified
    record_property('shapes_unentered', live_shapes.unentered)
    assert live_shapes.entered, 'No shape request entered a handler'
