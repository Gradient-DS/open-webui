"""Private-stack opt-in affects seeding, the passes, and coverage."""

import re
from copy import deepcopy
from unittest.mock import Mock

import pytest

from .. import test_route_coverage as coverage
from . import plane, seeds


@pytest.mark.parametrize('value,expected', [(None, 'waive'), ('waive', 'waive'), ('drive', 'drive')])
def test_runtime_mode(monkeypatch, value, expected):
    monkeypatch.delenv('RUNTIME_SOEV_BACKED', raising=False)
    if value is not None:
        monkeypatch.setenv('RUNTIME_SOEV_BACKED', value)
    assert seeds.runtime_soev_backed() == expected


@pytest.mark.parametrize('value', ['', 'DRIVE', ' drive', 'waive ', 'true', 'off'])
def test_invalid_mode_fails_before_any_fixture_request(monkeypatch, value):
    monkeypatch.setenv('RUNTIME_SOEV_BACKED', value)
    client = Mock()
    with pytest.raises(ValueError, match='RUNTIME_SOEV_BACKED must be waive or drive'):
        seeds.resolve_parameters(client)
    client.request.assert_not_called()


@pytest.mark.parametrize('mode', ['waive', 'drive'])
def test_all_soev_routes_use_the_shared_target_gate(monkeypatch, mode):
    monkeypatch.setenv('RUNTIME_SOEV_BACKED', mode)
    for route in seeds.SOEV_BACKED:
        tally = plane.Seeding()
        path = route.split(' ', 1)[1]
        parameters = {(path, name): 'owned' for name in re.findall(r'\{([^}]+)\}', path)}
        target = plane._target(route, parameters, tally)
        assert (target is None) is (mode == 'waive'), route
        assert (route in tally.skipped) is (mode == 'waive'), route


@pytest.mark.parametrize('mode', ['waive', 'drive'])
def test_real_seed_selection_has_no_disabled_dependencies(monkeypatch, mode):
    monkeypatch.setenv('RUNTIME_SOEV_BACKED', mode)
    selected = seeds.selected_parameters()
    private = {p['key'] for p in seeds.SURFACE['parameter'] if p.get('backed_by') == 'soev-solutions'}
    assert {
        'knowledge',
        'knowledge_reset',
        'directory',
        'external_source',
        'internal_knowledge',
        'removable_knowledge',
        'update_knowledge',
        'cloud_connection',
        'cloud_authorize_connection',
        'cloud_family',
        'cloud_knowledge',
        'cloud_schedule',
        'meeting',
    } == private
    selected_keys = {p['key'] for p in selected}
    assert selected_keys & private == (private if mode == 'drive' else set())
    assert {'file', 'agent', 'connection', 'task'} <= selected_keys
    assert all(set(seeds._dependencies(p)) <= selected_keys for p in selected)


@pytest.mark.parametrize('mode', ['waive', 'drive'])
def test_private_seeders_and_field_setup_never_run_in_waive_mode(monkeypatch, mode):
    monkeypatch.setenv('RUNTIME_SOEV_BACKED', mode)
    monkeypatch.delenv('CI_PLATFORM_FIXTURE_URL', raising=False)
    monkeypatch.delenv('CI_PLATFORM_FIXTURE_KEY', raising=False)
    surface = {
        'parameter': [
            {'key': 'local', 'name': 'id', 'prefix': '/local/{id}', 'via': 'POST /local', 'extract': 'id'},
            {
                'key': 'private',
                'name': 'id',
                'prefix': '/private/{id}',
                'backed_by': 'soev-solutions',
                'seeded_by': 'seed_cloud_connection',
                'depends_on': [],
                'provides': ['cloud_grant'],
            },
            {
                'key': 'alias',
                'name': 'id',
                'prefix': '/alias/{id}',
                'same_as': 'private',
                'backed_by': 'soev-solutions',
            },
        ],
        'field': [
            {
                'route': 'POST /private/{id}',
                'backed_by': 'soev-solutions',
                'json': {'grant': '{cloud_grant}'},
                'setup': [{'via': 'POST /private/{private}/setup'}],
            }
        ],
    }
    request = Mock(return_value={'id': 'local-id'})
    monkeypatch.setattr(seeds.SeedContext, 'request', request)

    def private_seeder(ctx, parameter):
        ctx.provide(parameter, 'cloud_grant', 'grant')
        return 'private-id'

    seeder = Mock(side_effect=private_seeder)
    monkeypatch.setitem(seeds._SEEDERS, 'seed_cloud_connection', seeder)
    spec = {'paths': {p['prefix']: {'post': {}} for p in surface['parameter']}}
    resolved = seeds.resolve_parameters(Mock(), surface=surface, spec=spec)
    assert resolved['/local/{id}', 'id'] == 'local-id'
    assert not resolved.failures and not resolved.blocked
    if mode == 'waive':
        seeder.assert_not_called()
        assert request.call_count == 1
        assert resolved.fields == {}
        assert len(resolved) == 1
    else:
        seeder.assert_called_once()
        assert request.call_count == 2
        assert resolved['/alias/{id}', 'id'] == 'private-id'
        assert resolved.fields['POST /private/{id}']['json'] == {'grant': 'grant'}


def test_unmarked_dependency_on_a_private_seed_is_a_declaration_error(monkeypatch):
    monkeypatch.setenv('RUNTIME_SOEV_BACKED', 'waive')
    surface = deepcopy(seeds.SURFACE)
    next(p for p in surface['parameter'] if p['key'] == 'directory').pop('backed_by')
    with pytest.raises(ValueError, match='directory: dependencies disabled'):
        seeds.selected_parameters(surface)


@pytest.mark.parametrize('mode', ['waive', 'drive'])
def test_coverage_gate_requires_private_hits_only_in_drive_mode(monkeypatch, tmp_path, mode):
    monkeypatch.setenv('RUNTIME_SOEV_BACKED', mode)
    waivers = tmp_path / 'waivers.toml'
    waivers.write_text("""
[[waived]]
id = "GET /private"
backed_by = "soev-solutions"
reason = "Private service"
[[waived]]
id = "GET /other"
reason = "Unrelated waiver"
""")
    monkeypatch.setattr(coverage, 'WAIVERS', waivers)
    monkeypatch.setattr(coverage, 'operations', lambda: {'GET /private', 'GET /local', 'GET /other'})
    hits = {'hits': ['GET /local']}
    monkeypatch.setattr(coverage, 'evidence', lambda: hits)
    assert 'GET /other' in coverage.waivers()
    if mode == 'waive':
        coverage.assert_every_route_is_driven_or_waived()
        coverage.assert_no_waiver_covers_a_route_the_plane_actually_reached()
        hits['hits'].append('GET /private')
        with pytest.raises(AssertionError, match='waived but actually driven'):
            coverage.assert_no_waiver_covers_a_route_the_plane_actually_reached()
    else:
        with pytest.raises(AssertionError, match='GET /private'):
            coverage.assert_every_route_is_driven_or_waived()
        hits['hits'].append('GET /private')
        coverage.assert_every_route_is_driven_or_waived()
        coverage.assert_no_waiver_covers_a_route_the_plane_actually_reached()
        hits['hits'].append('GET /other')
        with pytest.raises(AssertionError, match='GET /other'):
            coverage.assert_no_waiver_covers_a_route_the_plane_actually_reached()


@pytest.mark.parametrize('mode', ['waive', 'drive'])
def test_legacy_agents_api_routes_stay_waived_in_both_modes(monkeypatch, mode):
    monkeypatch.setenv('RUNTIME_SOEV_BACKED', mode)
    assert seeds.LEGACY_BACKED and not seeds.LEGACY_BACKED.keys() & seeds.SOEV_BACKED.keys()
    for route in seeds.LEGACY_BACKED:
        tally = plane.Seeding()
        assert plane._target(route, {}, tally) is None
        assert tally.skipped[route].startswith('BACKED BY LEGACY AGENTS-API')
        assert route in coverage.waivers()
