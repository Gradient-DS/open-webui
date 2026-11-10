"""Platform issuer refusals are driven; standalone controls stay intact."""

import pytest

from .. import test_route_coverage as coverage
from . import crossuser, seeds

ROUTES = {
    'POST /api/v1/auths/add',
    'POST /api/v1/invites/{token}/accept',
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
            if route != 'POST /api/v1/auths/add':
                assert controls[route]['all_actors']
    invite = 'POST /api/v1/invites/{token}/accept'
    assert (invite in crossuser._shared()) is (mode == 'waive')
    if mode == 'waive':
        assert controls[invite]['status'] == 410
        assert controls[invite]['detail'] == 'already been used'


def test_platform_2fa_inventory_uses_oidc_actor_without_local_enrollment(monkeypatch):
    from unittest.mock import Mock

    monkeypatch.setenv('RUNTIME_SOEV_BACKED', 'drive')
    parameter = seeds.parameter_for('/api/v1/users/{user_id}/2fa/disable', 'user_id')
    admin = Mock(identity={'id': 'oidc-fern'})
    ctx = seeds.SeedContext(Mock(), admin, seeds.SURFACE, token='fictional-run')
    ctx.request = Mock(side_effect=AssertionError('Platform MFA must not seed a local account'))
    assert seeds._seed_parameter(ctx, parameter) == 'oidc-fern'
    ctx.request.assert_not_called()
    control = crossuser.expected_refusals()['POST /api/v1/users/{user_id}/2fa/disable']
    assert control['status'] == 400
    assert control['detail'] == '2FA is not enabled for this user'
