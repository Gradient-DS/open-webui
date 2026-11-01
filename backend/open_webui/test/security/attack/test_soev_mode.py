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
        'removable_knowledge',
        'update_knowledge',
        'cloud_connection',
        'cloud_authorize_connection',
        'cloud_family',
        'cloud_knowledge',
        'cloud_schedule',
        'retry_schedule',
        'meeting',
        'agent_thread',
        'platform_thread',
        'platform_thread_versions',
        'platform_thread_read',
    } == private
    selected_keys = {p['key'] for p in selected}
    assert selected_keys & private == (private if mode == 'drive' else set())
    assert {'file', 'task'} <= selected_keys
    assert all(set(seeds._dependencies(p)) <= selected_keys for p in selected)


@pytest.mark.parametrize('mode', ['waive', 'drive'])
def test_platform_thread_seed_reuses_the_admin_owned_meeting(monkeypatch, mode):
    monkeypatch.setenv('RUNTIME_SOEV_BACKED', mode)
    surface = {
        'parameter': [
            p
            for p in seeds.SURFACE['parameter']
            if p['key'] in {'meeting', 'platform_thread', 'platform_thread_versions', 'platform_thread_read'}
        ],
        'field': [p for p in seeds.SURFACE['field'] if p['route'] == 'POST /api/v1/threads/{thread_id}/read'],
    }
    paths = {path for path in seeds.SPEC['paths'] if path.startswith('/api/v1/threads/')}
    spec = {'paths': {path: seeds.SPEC['paths'][path] for path in paths}}
    client, admin = Mock(), Mock()
    request = Mock(return_value={'id': 'fictional-meeting-thread'})
    monkeypatch.setattr(seeds.SeedContext, 'request', request)

    resolved = seeds.resolve_parameters(client, admin=admin, surface=surface, spec=spec)

    assert not resolved.failures and not resolved.blocked
    if mode == 'waive':
        request.assert_not_called()
        assert resolved == {}
        assert resolved.fields == {}
    else:
        request.assert_called_once()
        assert request.call_args.args == ('POST', '/api/v1/meetings')
        assert request.call_args.kwargs['actor'] is admin
        assert resolved == {(path, 'thread_id'): 'fictional-meeting-thread' for path in paths}
        assert resolved.fields == {
            'POST /api/v1/threads/{thread_id}/read': {'headers': {'Content-Type': 'application/json'}}
        }


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
def test_catalog_embedding_waivers_are_drive_only_and_never_skip_probes(monkeypatch, tmp_path, mode):
    monkeypatch.setenv('RUNTIME_SOEV_BACKED', mode)
    routes = {'POST /api/embeddings', 'POST /api/v1/embeddings'}
    assert coverage.waivers().keys() & routes == (routes if mode == 'drive' else set())
    for route in routes:
        assert not seeds.route_waived(route)
        assert plane._target(route, {}, plane.Seeding()) == route.split(' ', 1)[1]
    monkeypatch.setattr(coverage, 'operations', lambda: routes)
    monkeypatch.setattr(coverage, 'evidence', lambda: {'hits': []})
    if mode == 'drive':
        coverage.assert_every_route_is_driven_or_waived()
        monkeypatch.setattr(coverage, 'evidence', lambda: {'hits': sorted(routes)})
        with pytest.raises(AssertionError, match='waived but actually driven'):
            coverage.assert_no_waiver_covers_a_route_the_plane_actually_reached()
    else:
        with pytest.raises(AssertionError, match='POST /api/embeddings'):
            coverage.assert_every_route_is_driven_or_waived()


@pytest.mark.parametrize('mode', ['waive', 'drive'])
def test_function_seeds_and_refusal_controls_follow_the_runtime(monkeypatch, mode):
    from . import crossuser

    monkeypatch.setenv('RUNTIME_SOEV_BACKED', mode)
    parameters = [p for p in seeds.SURFACE['parameter'] if p['key'] in {'function', 'active_function', 'action'}]
    spec = {
        'paths': {
            path: item
            for path, item in seeds.SPEC['paths'].items()
            if path.startswith('/api/v1/functions/') or path == '/api/chat/actions/{action_id}'
        }
    }
    endpoint = Mock(return_value='installed-function')
    active = Mock(return_value='installed-action')
    monkeypatch.setattr(seeds, '_seed_endpoint', endpoint)
    monkeypatch.setitem(seeds._SEEDERS, 'seed_active_function', active)
    resolved = seeds.resolve_parameters(Mock(), surface={'parameter': parameters}, spec=spec)
    action = resolved['/api/chat/actions/{action_id}', 'action_id']
    functions = {r for r in plane.operations() if r.split(' ', 1)[1].startswith('/api/v1/functions/')}
    assert all(plane._target(route, resolved, plane.Seeding()) is not None for route in functions)
    if mode == 'drive':
        endpoint.assert_not_called()
        active.assert_not_called()
        assert action.startswith('attack_active_')
        assert functions <= crossuser.expected_refusals().keys()
        assert functions <= coverage.waivers().keys()
        assert not functions & crossuser._shared().keys()
        assert 'POST /api/chat/actions/{action_id}' in crossuser.expected_refusals()
    else:
        endpoint.assert_called_once()
        active.assert_called_once()
        assert action == 'installed-action'
        assert not functions & crossuser.expected_refusals().keys()
        assert not functions & coverage.waivers().keys()
        assert 'GET /api/v1/functions/id/{id}/valves/user/spec' in crossuser._shared()
    assert not resolved.failures and not resolved.blocked


def test_v2_function_waivers_match_observed_http_refusals_for_every_mounted_route(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from open_webui import config, env
    from open_webui.routers import functions

    from . import crossuser
    from .client import entered_the_handler

    monkeypatch.setenv('RUNTIME_SOEV_BACKED', 'drive')
    monkeypatch.setattr(env, 'AGENT_API_ENABLED', True)
    monkeypatch.setattr(config, 'SOEV_API_URL', 'https://platform.example.invalid')
    app = FastAPI()
    app.include_router(functions.router, prefix='/api/v1/functions')
    declarations = crossuser.expected_refusals()
    observed = set()
    with TestClient(app) as client:
        for route in app.routes:
            if not route.path.startswith('/api/v1/functions/'):
                continue
            for method in route.methods:
                operation = f'{method} {route.path}'
                response = client.request(method, route.path.replace('{id}', 'fictional-function'), json={})
                assert crossuser.refused_as_expected(declarations[operation], response.status_code, response.text)
                assert not entered_the_handler(response)
                observed.add(operation)
    functions_in_spec = {r for r in plane.operations() if r.split(' ', 1)[1].startswith('/api/v1/functions/')}
    assert observed == functions_in_spec
    assert observed <= coverage.waivers().keys()


def test_v2_action_probe_reaches_the_existing_missing_action_refusal(monkeypatch):
    from importlib import import_module
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from open_webui.utils import actions
    from open_webui.utils.auth import get_verified_user

    from . import crossuser

    main = import_module('open_webui.main')
    monkeypatch.setenv('RUNTIME_SOEV_BACKED', 'drive')
    monkeypatch.setattr(actions, 'ENABLE_PLUGINS', True)
    lookup = AsyncMock(return_value=None)
    ownership = AsyncMock()
    monkeypatch.setattr(actions.Functions, 'get_function_by_id', lookup)
    monkeypatch.setattr(main, 'verify_chat_ownership', ownership)
    app = FastAPI()
    app.add_api_route('/api/chat/actions/{action_id}', main.chat_action, methods=['POST'])
    user = SimpleNamespace(id='fictional-owner', role='admin')
    app.dependency_overrides[get_verified_user] = lambda: user
    with TestClient(app) as client:
        response = client.post(
            '/api/chat/actions/fictional-action', json={'chat_id': 'fictional-chat', 'model': 'fictional-model'}
        )
    declaration = crossuser.expected_refusals()['POST /api/chat/actions/{action_id}']
    assert crossuser.refused_as_expected(declaration, response.status_code, response.text)
    ownership.assert_awaited_once_with('fictional-chat', user)
    lookup.assert_awaited_once_with('fictional-action')
