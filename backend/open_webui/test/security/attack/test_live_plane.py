"""Live sealed-stack checks; run only by runtime-security.yml."""

import pytest
from . import plane, seeds


def _stop_task(admin, parameters):
    path = '/api/tasks/stop/{task_id}'
    result = admin.request('POST', plane._fill(path, parameters))
    assert result.status_code == 200, f'Seed task cleanup: HTTP {result.status_code}'


@pytest.fixture(scope='module')
def live_seeding():
    from .identities import ensure_identities

    plane._PASSES.clear()
    plane.flush_hits()  # A new invocation cannot inherit a stale artefact.
    identities = ensure_identities()
    parameters = None
    try:
        parameters = seeds.resolve_parameters(identities.admin, admin=identities.admin)
        yield plane.seed_every_writable_field(identities.admin, parameters)
    finally:
        try:
            if parameters is not None:
                _stop_task(identities.admin, parameters)
        finally:
            identities.close()


@pytest.fixture(scope='module')
def live_drive(live_seeding):
    from .identities import ensure_identities

    identities = ensure_identities()
    parameters = None
    try:
        parameters = seeds.resolve_parameters(identities.admin, admin=identities.admin)
        yield plane.drive_every_route(identities.admin, parameters)
    finally:
        try:
            if parameters is not None:
                _stop_task(identities.admin, parameters)
        finally:
            identities.close()


def test_live_seeding_has_its_own_5xx_assertion(live_seeding):
    assert not live_seeding.crashes, f'Seeding found server errors:\n{plane.crash_report(live_seeding)}'


def test_live_seeding_reports_its_own_reach(live_seeding, record_property):
    accounted = live_seeding.statuses.keys() | live_seeding.skipped.keys() | live_seeding.unanswered.keys()
    assert accounted == live_seeding.expected
    record_property('seeding_reached', len(live_seeding.entered))
    record_property('seeding_unentered', len(live_seeding.unentered))
    record_property('seeding_unanswered', len(live_seeding.unanswered))
    record_property('seeding_config_unverified', len(live_seeding.config_unverified))
    assert live_seeding.entered, 'No seeding request entered a handler'


def test_live_drive_has_its_own_5xx_assertion(live_drive):
    crashes = {route: outcome for route, outcome in live_drive.items() if outcome.status >= 500}
    assert not crashes, f'Drive found server errors:\n{plane.drive_report(live_drive)}'


def test_live_drive_reports_its_own_reach(live_drive, record_property):
    tally = plane._PASSES['drive']
    assert live_drive.keys() | tally.skipped.keys() == set(plane.operations())
    record_property('drive_reached', len(tally.entered))
    record_property('drive_unentered', len(tally.unentered))
    record_property('drive_unanswered', len(tally.unanswered))
    record_property('drive_config_unverified', len(tally.config_unverified))
    record_property('combined_unentered', len(plane.unentered_routes()))
    assert tally.entered, 'No drive request entered a handler'


def test_live_drive_does_not_reflect_corpus_in_html(live_drive):
    reflected = [route for route, outcome in live_drive.items() if outcome.reflected]
    assert not reflected, reflected
