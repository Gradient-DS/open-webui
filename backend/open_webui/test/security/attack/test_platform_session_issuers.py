"""Platform issuer refusals are driven; standalone controls stay intact."""

import pytest

from .. import test_route_coverage as coverage
from . import crossuser, seeds

ROUTES = {
    'POST /api/v1/auths/2fa/verify',
    'POST /api/v1/auths/2fa/totp/enable',
}


@pytest.mark.parametrize('mode', ['waive', 'drive'])
def test_inventory_records_platform_session_issuer_refusals(monkeypatch, mode):
    monkeypatch.setenv('RUNTIME_SOEV_BACKED', mode)
    controls = crossuser.expected_refusals()
    waivers = coverage.waivers()
    for route in ROUTES:
        assert route not in waivers
        assert route in seeds.ordinary_targets()
        if mode == 'drive':
            assert controls[route]['status'] == 403
            assert controls[route]['detail'] == (
                'You do not have permission to access this resource. Please contact your administrator for assistance.'
            )
            assert controls[route]['all_actors']


@pytest.mark.parametrize('mode', ['waive', 'drive'])
def test_removed_account_routes_have_no_live_controls_or_seeds(monkeypatch, mode):
    monkeypatch.setenv('RUNTIME_SOEV_BACKED', mode)
    controls = crossuser.expected_refusals()
    for route in {
        'POST /api/v1/auths/add',
        'POST /api/v1/invites/{token}/accept',
        'POST /api/v1/users/{user_id}/2fa/disable',
    }:
        assert route in seeds.SURFACE['removed']['routes']
        assert route not in controls
        assert route not in seeds.ordinary_targets()
        assert route not in coverage.waivers()
        assert route not in crossuser._shared()
    assert not any(entry['key'] == 'totp_user' for entry in seeds.SURFACE['parameter'])
