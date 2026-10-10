"""Live sealed-stack checks; run only by runtime-security.yml."""

import re

import pytest
from . import crossuser
from .benign_requests import benign_request
from .client import AttackClient
from .identities import ensure_identities
from .pass_support import crash_details
from .seeds import SOEV_BACKED


@pytest.mark.parametrize('route', sorted(SOEV_BACKED))
def test_soev_backed_routes_refuse_anonymous_callers(route):
    # OWUI's own authentication runs before any soev-api call, so this check
    # needs no soev service. A refusal is not recorded as a route hit.
    method, path = route.split(' ', 1)
    target = re.sub(r'\{[^}]+\}', 'anonymous-probe', path)
    with AttackClient() as anonymous:
        response = anonymous.request(method, target, **benign_request(route, {}))
        try:
            assert response.status_code in {401, 403}, f'{route}: {response.status_code} {response.text[:300]}'
        finally:
            response.close()


@pytest.fixture(scope='module')
def live_crossuser():
    identities = ensure_identities()
    try:
        yield crossuser.drive_crossuser(identities)
    finally:
        identities.close()


def test_live_crossuser_has_its_own_5xx_assertion(live_crossuser):
    assert not live_crossuser.tally.crashes, crash_details(live_crossuser.tally)


def test_live_crossuser_refuses_other_users_and_admin_operations(live_crossuser):
    assert not live_crossuser.violations, live_crossuser.violations


def test_live_crossuser_owner_controls_are_positive(live_crossuser):
    expected = crossuser.expected_refusals()
    bodies = live_crossuser.control_bodies
    gaps = {
        route: status
        for route, status in live_crossuser.controls.items()
        if not 200 <= status < 300
        and not crossuser.refused_as_expected(expected.get(route), status, bodies.get(route, ''))
    }
    stale = sorted(route for route in expected if 200 <= live_crossuser.controls.get(route, 0) < 300)
    assert not stale, f'Declared refusals now succeed; drop their [[control]] entries: {stale}'
    reasons = '\n'.join(
        f'  {route}: HTTP {status} {live_crossuser.control_bodies.get(route, "")}'
        for route, status in sorted(gaps.items())
    )
    assert not gaps, f'Owner positive controls did not succeed; isolation remains unproven:\n{reasons}'
    assert live_crossuser.controls, 'No owner positive controls ran'


def test_live_crossuser_reports_its_own_reach(live_crossuser, record_property):
    tally = live_crossuser.tally
    assert tally.statuses.keys() | tally.skipped.keys() == tally.expected == set(crossuser.targets()[0])
    assert tally.config_restore_verified
    record_property('crossuser_unentered', tally.unentered)
    assert tally.entered, 'No authorization request entered a handler'
