"""Platform exchange refusal is probed without an issuer and never waived."""

from unittest.mock import Mock

import pytest

from .. import test_route_coverage as coverage
from . import crossuser, plane, seeds

ROUTE = 'POST /api/v1/auths/oauth/{provider}/token/exchange'
PATH = ROUTE.split(' ', 1)[1]


@pytest.mark.parametrize('mode', ['waive', 'drive'])
def test_exchange_inventory_selects_the_platform_refusal_probe(monkeypatch, mode):
    monkeypatch.setenv('RUNTIME_SOEV_BACKED', mode)
    entry = seeds.parameter_for(PATH, 'provider')
    selected = seeds.seed_enabled(entry)
    assert selected is (mode == 'drive')
    assert (ROUTE in coverage.waivers()) is (mode == 'waive')
    assert (ROUTE in seeds.ordinary_targets()) is (mode == 'drive')
    if mode == 'drive':
        assert ROUTE in crossuser.targets()[0]
        control = crossuser.expected_refusals()[ROUTE]
        assert control['all_actors']
        assert control['status'] == 403
        assert control['detail'] == (
            'You do not have permission to access this resource. Please contact your administrator for assistance.'
        )
        fields = next(field for field in seeds.SURFACE['field'] if field['route'] == ROUTE)
        assert fields['json'] == {'token': 'fictional-exchange-token'}


@pytest.mark.parametrize('mode', ['waive', 'drive'])
def test_exchange_probe_resolution_never_calls_an_identity_provider(monkeypatch, mode):
    monkeypatch.setenv('RUNTIME_SOEV_BACKED', mode)
    entry = seeds.parameter_for(PATH, 'provider')
    client = Mock()
    surface = {'parameter': [entry]}
    spec = {'paths': {PATH: {'post': {}}}}
    parameters = seeds.resolve_parameters(client, surface=surface, spec=spec)
    if mode == 'drive':
        provider = parameters[PATH, 'provider']
        assert provider.startswith('fictional-provider-')
        assert len(provider.removeprefix('fictional-provider-')) == 24
        assert plane._fill(PATH, parameters) == f'/api/v1/auths/oauth/{provider}/token/exchange'
    else:
        assert parameters == {}
        with pytest.raises(plane.UnseedableRoute):
            plane._fill(PATH, parameters)
    client.request.assert_not_called()
