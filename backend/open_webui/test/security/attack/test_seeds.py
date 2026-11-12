import ast
import asyncio
import base64
import json
import re
import sqlite3
import tomllib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import requests

from . import seeds

ROOT = Path(__file__).resolve().parents[5]
SPEC = json.loads((ROOT / 'security/openapi.json').read_text())
SURFACE_PATH = ROOT / 'security/attack-surface.toml'
METHODS = {'get', 'post', 'put', 'patch', 'delete', 'head', 'options', 'trace'}


def path_parameters():
    for path, item in SPEC['paths'].items():
        for method, operation in item.items():
            if method not in METHODS:
                continue
            names = set(re.findall(r'\{([^}]+)\}', path))
            names.update(
                p['name'] for p in item.get('parameters', []) + operation.get('parameters', []) if p.get('in') == 'path'
            )
            for name in sorted(names):
                yield method.upper(), path, name


def test_every_path_parameter_in_every_operation_is_accounted_for():
    surface = tomllib.loads(SURFACE_PATH.read_text()) if SURFACE_PATH.exists() else {}
    missing = []
    for method, path, name in path_parameters():
        matches = [
            p
            for p in surface.get('parameter', [])
            if p['name'] == name and (path == p['prefix'] or path.startswith(p['prefix'] + '/'))
        ]
        if not matches:
            missing.append(f'{method} {path}: {name}')
            continue
        parameter = max(matches, key=lambda p: len(p['prefix']))
        assert seeds.parameter_for(path, name, surface) == parameter
        kinds = {'via', 'same_as', 'seeded_by', 'unseedable'} & parameter.keys()
        assert len(kinds) == 1, parameter
        if 'unseedable' in parameter:
            assert parameter['unseedable'].strip(), parameter
    assert not missing, 'Unaccounted path parameters:\n' + '\n'.join(missing)


def test_every_declaration_matches_and_wins_for_at_least_one_parameter():
    used = {seeds.parameter_for(path, name)['key'] for _, path, name in path_parameters()}
    assert used == {p['key'] for p in seeds.SURFACE['parameter']}
    selectors = [(p['prefix'], p['name']) for p in seeds.SURFACE['parameter']]
    assert len(selectors) == len(set(selectors))


def test_route_scoping_distinguishes_resources():
    for path, key in [
        ('/api/v1/chats/{id}', 'chat'),
        ('/api/v1/knowledge/{id}/files', 'knowledge'),
        ('/api/v1/files/{id}/content', 'file'),
        ('/api/v1/chats/shared/{id}/access', 'shared_access'),
        ('/api/v1/chats/{id}/share', 'chat_share_access'),
        ('/api/v1/chats/{id}/clone/shared', 'shared_clone'),
    ]:
        assert seeds.parameter_for(path, 'id')['key'] == key
    with pytest.raises(KeyError, match='Unaccounted'):
        seeds.parameter_for('/api/v1/files/{id}suffix', 'id')


def test_required_drive_routes_have_seeded_targets_and_authorization_coverage(monkeypatch):
    from .authorization_inventory import platform_operations
    from .crossuser import targets

    monkeypatch.setenv('RUNTIME_SOEV_BACKED', 'drive')
    required = set(seeds.SURFACE['required_drive_routes'])
    assert required <= set(seeds.ordinary_targets())
    crossuser, _ = targets()
    image_prompt = 'POST /api/v1/tasks/image_prompt/completions'
    assert required - {image_prompt} <= set(crossuser)
    assert 'GET /api/v1/cloud-sync/policy' in platform_operations()
    refusal = next(entry for entry in seeds.SURFACE['control'] if entry['route'] == image_prompt)
    assert refusal['all_actors'] and refusal['status'] == 404


@pytest.mark.parametrize('route', seeds.SURFACE['required_drive_routes'])
def test_required_owner_controls_reject_unentered_or_wrong_refusal_evidence(monkeypatch, route):
    from . import plane
    from .crossuser import expected_refusals
    from .test_live_plane import test_live_drive_has_owner_controls_for_previously_unentered_routes as check

    monkeypatch.setenv('RUNTIME_SOEV_BACKED', 'drive')
    refusals = expected_refusals()
    outcomes = {
        target: plane.Outcome(
            refusals[target]['status'] if target in refusals else 200,
            target not in refusals,
            json.dumps(refusals[target]['json']) if target in refusals else '{}',
        )
        for target in seeds.SURFACE['required_drive_routes']
    }
    check(outcomes)
    outcomes[route] = plane.Outcome(404, False, '{"detail":"Fictional missing target"}')
    with pytest.raises(AssertionError):
        check(outcomes)


@pytest.mark.parametrize('entry', [p for p in seeds.SURFACE['parameter'] if 'seeded_by' in p], ids=lambda p: p['key'])
def test_every_seeded_by_names_a_callable_and_declares_dependencies(entry):
    assert callable(seeds._SEEDERS[entry['seeded_by']])
    assert 'depends_on' in entry and isinstance(entry['depends_on'], list)


def test_every_declared_dependency_resolves_before_its_consumer():
    order = seeds.dependency_order(seeds.SURFACE)
    done = set()
    for p in order:
        assert set(seeds._dependencies(p)) <= done
        done.add(p['key'])


@pytest.mark.parametrize(
    ('payload', 'path'),
    [
        ({}, 'id'),
        ({'node': {}}, 'node.id'),
        ({'id': None}, 'id'),
        ({'id': ''}, 'id'),
        ({'id': ' \t'}, 'id'),
        ({'id': []}, 'id'),
        ({'id': {}}, 'id'),
        ({'id': False}, 'id'),
        ({'id': 1.2}, 'id'),
        ({'ids': []}, 'ids.0'),
        ({'ids': ['x']}, 'ids.1'),
        ({'ids': ['x']}, 'ids.nope'),
        ({'ids': ['x']}, 'ids.-1'),
        ({'id': 'x'}, 'id.0'),
        ({'id': None}, 'id.nested'),
    ],
)
def test_extract_raises_loudly_on_missing_null_empty_or_invalid_values(payload, path):
    with pytest.raises(RuntimeError, match=re.escape(f'seeding fixture via POST /create: extract path {path!r}')):
        seeds._extract(payload, path, name='fixture', via='POST /create')


@pytest.mark.parametrize(
    ('payload', 'path', 'expected'),
    [
        ({'node': {'id': 'abc'}}, 'node.id', 'abc'),
        ({'ids': ['abc']}, 'ids.0', 'abc'),
        ({'id': 0}, 'id', '0'),
        ({'0': {'id': 42}}, '0.id', '42'),
    ],
)
def test_extract_reads_objects_and_lists_without_losing_zero(payload, path, expected):
    assert seeds._extract(payload, path, name='fixture', via='POST /create') == expected


def test_collecting_seed_failures_preserves_independent_resources_and_blocks_dependents(monkeypatch):
    entries = [
        {'key': 'broken', 'name': 'id', 'prefix': '/broken', 'via': 'POST /broken', 'extract': 'id'},
        {'key': 'child', 'name': 'id', 'prefix': '/child', 'same_as': 'broken'},
        {'key': 'healthy', 'name': 'id', 'prefix': '/healthy', 'via': 'POST /healthy', 'extract': 'id'},
    ]
    surface = {'parameter': entries, 'field': [{'route': 'POST /body', 'json': {'id': '{broken}'}}]}
    spec = {'paths': {f'/{name}/{{id}}': {'get': {}} for name in ('broken', 'child', 'healthy')}}

    def create(ctx, entry):
        if entry['key'] == 'broken':
            raise RuntimeError('HTTP 500: product bug')
        return 'real-created-id'

    monkeypatch.setattr(seeds, '_seed_endpoint', create)
    with pytest.raises(RuntimeError, match='product bug'):
        seeds.resolve_parameters(Mock(), surface=surface, spec=spec)
    resolved = seeds.resolve_parameters(Mock(), surface=surface, spec=spec, collect_failures=True)
    assert dict(resolved) == {('/healthy/{id}', 'id'): 'real-created-id'}
    assert set(resolved.failures) == {'broken', 'child', 'POST /body'}
    assert set(resolved.blocked) == {'GET /broken/{id}', 'GET /child/{id}', 'POST /body'}
    from . import plane

    tally = plane.Seeding()
    assert plane._target('GET /broken/{id}', resolved, tally) is None
    assert tally.fixture_failures == resolved.failures
    assert tally.skipped['GET /broken/{id}'].startswith('FAILED FIXTURE:')
    assert not tally.entered


def test_fill_token_handles_nested_values_and_message_dictionary_keys_without_mutating():
    body = {'messages': {'message_{token}': {'id': 'message_{token}'}}, 'values': ['{token}', 0, False, None]}
    original = json.loads(json.dumps(body))
    assert seeds._fill_token(body, 'fresh') == {
        'messages': {'message_fresh': {'id': 'message_fresh'}},
        'values': ['fresh', 0, False, None],
    }
    assert body == original


def response(status, body):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(body).encode()
    return result


@pytest.mark.parametrize('url,expected', [(None, 'unavailable'), ('https://tenant.invalid/console/', 'allowed')])
def test_console_seed_reads_the_callers_platform_eligibility(url, expected):
    ctx = seeds.SeedContext(Mock(), Mock(), seeds.SURFACE)
    ctx.request = Mock(return_value={'console_url': url})
    assert seeds.seed_console_access(ctx, {'key': 'console_eligibility'}) == expected
    ctx.request.assert_called_once_with('GET', '/api/v1/auths/console-access', actor=ctx.client)
    ctx.admin.request.assert_not_called()


@pytest.mark.parametrize('payload', [{}, {'console_url': 1}, {'console_url': None, 'role': 'admin'}])
def test_console_seed_refuses_an_invalid_decision(payload):
    ctx = seeds.SeedContext(Mock(), Mock(), seeds.SURFACE)
    ctx.request = Mock(return_value=payload)
    with pytest.raises(RuntimeError, match='Invalid caller console eligibility'):
        seeds.seed_console_access(ctx, {'key': 'console_eligibility'})


def test_synchronous_file_processing_does_not_poll():
    client = Mock(spec=seeds.AttackClient)
    client.request.return_value = response(200, {'status': True, 'content': 'text'})
    ctx = seeds.SeedContext(client, client, seeds.SURFACE)
    ctx.request('POST', '/api/v1/retrieval/process/file', json={'file_id': 'file-ci'})
    assert client.request.call_count == 1


@pytest.mark.parametrize('consent_status', [200, 400, 403])
def test_cloud_seed_requires_consent_then_creates_the_owner_grant(monkeypatch, consent_status):
    client, admin = Mock(), Mock()
    ctx = seeds.SeedContext(client, admin, {'destructive': []})
    client.request.side_effect = [
        response(
            201,
            {
                'connection_id': 'connection',
                'authorize_url': 'http://soev-provider-fixture:8040/ci/consent/11111111-1111-4111-8111-111111111111',
            },
        ),
        response(200, {'id': 'grant'}),
    ]
    session = Mock()
    session.post.return_value = response(consent_status, {'id': 'connection'})
    manager = Mock()
    manager.__enter__ = Mock(return_value=session)
    manager.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(seeds.requests, 'Session', Mock(return_value=manager))
    monkeypatch.setenv('CI_PLATFORM_FIXTURE_URL', 'http://fixture:8040')
    monkeypatch.setenv('CI_PLATFORM_FIXTURE_KEY', 'test-fixture-key')
    parameter = {'key': 'cloud_connection', 'provides': ['cloud_grant']}
    if consent_status != 200:
        with pytest.raises(RuntimeError, match=f'HTTP {consent_status}'):
            seeds.seed_cloud_connection(ctx, parameter)
        assert client.request.call_count == 1
        assert not ctx.values
    else:
        assert seeds.seed_cloud_connection(ctx, parameter) == 'connection'
        assert ctx.values == {'cloud_grant': 'grant'}
        client.request.assert_called_with('POST', '/api/v1/cloud-sync/connections/connection/live/live_documents')
    admin.request.assert_not_called()
    session.post.assert_called_once_with(
        'http://fixture:8040/ci/consent',
        headers={'Authorization': 'Bearer test-fixture-key'},
        json={
            'connection_id': 'connection',
            'authorize_url': 'http://soev-provider-fixture:8040/ci/consent/11111111-1111-4111-8111-111111111111',
        },
        timeout=30,
    )


@pytest.mark.parametrize('failure', [400, 'foreign-connection', 'transport'])
def test_consent_fixture_failure_is_recorded_and_never_enables_a_grant(monkeypatch, failure):
    parameter = {
        'key': 'cloud_connection',
        'name': 'connection_id',
        'prefix': '/connections/{connection_id}',
        'seeded_by': 'seed_cloud_connection',
        'provides': ['cloud_grant'],
        'depends_on': [],
    }
    client = Mock(spec=seeds.AttackClient)
    client.request.return_value = response(
        201, {'connection_id': 'fictional-connection', 'authorize_url': 'fictional-start'}
    )
    session = Mock()
    session.post.return_value = response(400, {}) if failure == 400 else response(200, {'id': 'foreign-connection'})
    if failure == 'transport':
        session.post.side_effect = requests.ConnectionError('Fictional disconnected fixture')
    manager = Mock()
    manager.__enter__ = Mock(return_value=session)
    manager.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(seeds.requests, 'Session', Mock(return_value=manager))
    monkeypatch.setenv('CI_PLATFORM_FIXTURE_URL', 'http://fixture:8040')
    monkeypatch.setenv('CI_PLATFORM_FIXTURE_KEY', 'fictional-fixture-key')
    resolved = seeds.resolve_parameters(
        client,
        surface={'parameter': [parameter]},
        spec={'paths': {'/connections/{connection_id}': {'get': {}}}},
        collect_failures=True,
    )
    assert set(resolved.failures) == {'cloud_connection'}
    assert set(resolved.blocked) == {'GET /connections/{connection_id}'}
    assert not resolved
    client.request.assert_called_once()


def test_a_later_pass_reuses_the_consented_connection_without_new_consent(monkeypatch):
    """One connection per owner and provider: the create returns the consented one, with no authorize URL."""
    client, admin = Mock(), Mock()
    ctx = seeds.SeedContext(client, admin, {'destructive': []})
    client.request.side_effect = [
        response(200, {'connection_id': 'connection', 'authorize_url': None}),
        response(200, {'id': 'grant'}),
    ]
    session_factory = Mock()
    monkeypatch.setattr(seeds.requests, 'Session', session_factory)
    assert seeds.seed_cloud_connection(ctx, {'key': 'cloud_connection', 'provides': ['cloud_grant']}) == 'connection'
    assert ctx.values == {'cloud_grant': 'grant'}
    session_factory.assert_not_called()


def test_starting_consent_cannot_suspend_the_connection_used_by_positive_controls():
    authorize = seeds.parameter_for('/api/v1/cloud-sync/connections/{connection_id}/authorize', 'connection_id')
    live = seeds.parameter_for('/api/v1/cloud-sync/connections/{connection_id}/live/{family}', 'connection_id')
    assert authorize['key'] != live['key']
    assert authorize['seeded_by'] == 'seed_cloud_authorize_connection'
    assert 'same_as' not in authorize
    assert live['seeded_by'] == 'seed_cloud_connection'
    assert 'cloud_grant' in live['provides']


def test_the_authorize_control_takes_a_separate_provider():
    """Outlook mail keeps the control off the consented OneDrive connection."""
    client, admin = Mock(), Mock()
    ctx = seeds.SeedContext(client, admin, {'destructive': []})
    client.request.side_effect = [
        response(200, {'providers_enabled': ['onedrive', 'outlook_mail']}),
        response(201, {'connection_id': 'pending', 'authorize_url': 'http://soev-api:8020/oauth/start/attempt'}),
    ]
    assert seeds.seed_cloud_authorize_connection(ctx, {'key': 'cloud_authorize_connection'}) == 'pending'
    client.request.assert_called_with('POST', '/api/v1/cloud-sync/connections', json={'provider': 'outlook_mail'})
    admin.request.assert_not_called()


def test_authorize_control_never_reuses_the_consented_onedrive_connection():
    client, admin = Mock(), Mock()
    ctx = seeds.SeedContext(client, admin, {'destructive': []})
    client.request.return_value = response(200, {'providers_enabled': ['onedrive']})
    with pytest.raises(RuntimeError, match='separate Outlook'):
        seeds.seed_cloud_authorize_connection(ctx, {'key': 'cloud_authorize_connection'})
    client.request.assert_called_once_with('GET', '/api/v1/cloud-sync/policy')
    admin.request.assert_not_called()


def test_file_seeding_waits_for_completion_and_cleared_job(monkeypatch):
    client = Mock(spec=seeds.AttackClient)
    client.request.side_effect = [
        response(200, {'job_id': 'job-ci'}),
        response(200, {'meta': {'status': 'processing', 'soev_job': {'job_id': 'job-ci'}}}),
        response(200, {'meta': {'status': 'completed', 'soev_job': {'job_id': 'job-ci'}}}),
        response(200, {'meta': {'status': 'completed', 'soev_job': None}}),
    ]
    monkeypatch.setattr(seeds.time, 'sleep', Mock())
    ctx = seeds.SeedContext(client, client, seeds.SURFACE)
    result = ctx.request('POST', '/api/v1/retrieval/process/file', json={'file_id': 'file-ci'})
    assert result == {'job_id': 'job-ci'}
    assert [call.args for call in client.request.call_args_list[1:]] == [('GET', '/api/v1/files/file-ci')] * 3


def test_file_seeding_surfaces_ingest_failure():
    client = Mock(spec=seeds.AttackClient)
    client.request.side_effect = [
        response(200, {'job_id': 'job-ci'}),
        response(200, {'meta': {'status': 'failed', 'error': 'bundle invalid'}}),
    ]
    ctx = seeds.SeedContext(client, client, seeds.SURFACE)
    with pytest.raises(RuntimeError, match='bundle invalid'):
        ctx.request('POST', '/api/v1/retrieval/process/file', json={'file_id': 'file-ci'})


def test_file_seeding_times_out_without_reporting_success(monkeypatch):
    client = Mock(spec=seeds.AttackClient)
    client.request.return_value = response(200, {'meta': {'status': 'processing'}})
    ctx = seeds.SeedContext(client, client, seeds.SURFACE)
    monkeypatch.setattr(seeds.time, 'monotonic', Mock(side_effect=[0, 0, 121]))
    monkeypatch.setattr(seeds.time, 'sleep', Mock())
    with pytest.raises(RuntimeError, match='did not complete within 120s'):
        ctx.wait_for_file('file-ci')


def tiny_surface(*entries):
    return {'parameter': list(entries), 'destructive': seeds.SURFACE['destructive']}


def declaration(key='parent', **kwargs):
    return {'key': key, 'name': 'id', 'prefix': '/' + key + '/{id}', **kwargs}


def test_resolver_orders_dependencies_escapes_paths_and_returns_route_parameter_pairs():
    parent = declaration(via='POST /parent', body={'name': 'parent-{token}'}, extract='id')
    child = declaration(
        'child', via='POST /parent/{parent}/child', depends_on=['parent'], body={'parent_id': '{parent}'}, extract='id'
    )
    alias = declaration('alias', same_as='child')
    client = Mock(spec=seeds.AttackClient)
    client.request.side_effect = [response(200, {'id': 'a/b'}), response(201, {'id': 'child-id'})]
    surface = tiny_surface(alias, child, parent)
    result = seeds.resolve_parameters(
        client, surface=surface, spec={'paths': {p['prefix']: {'get': {}} for p in surface['parameter']}}
    )
    assert result == {
        ('/alias/{id}', 'id'): 'child-id',
        ('/child/{id}', 'id'): 'child-id',
        ('/parent/{id}', 'id'): 'a/b',
    }
    calls = client.request.call_args_list
    assert calls[0].args == ('POST', '/parent')
    assert calls[1].args == ('POST', '/parent/a%2Fb/child')
    assert calls[1].kwargs['json'] == {'parent_id': 'a/b'}


def test_resolver_calls_seeder_with_resolved_dependencies(monkeypatch):
    called = []

    def seeder(ctx, entry):
        called.append(ctx.values.copy())
        return ctx.values['parent'] + '-child'

    monkeypatch.setitem(seeds._SEEDERS, 'example', seeder)
    surface = tiny_surface(
        declaration('child', seeded_by='example', depends_on=['parent']), declaration(via='POST /parent', extract='id')
    )
    client = Mock(spec=seeds.AttackClient)
    client.request.return_value = response(200, {'id': 'parent-id'})
    result = seeds.resolve_parameters(client, surface=surface, spec={'paths': {'/child/{id}': {'get': {}}}})
    assert called == [{'parent': 'parent-id'}]
    assert result['/child/{id}', 'id'] == 'parent-id-child'


@pytest.mark.parametrize(
    'entries',
    [
        [declaration(same_as='missing')],
        [declaration(same_as='other'), declaration('other', same_as='parent')],
        [declaration(via='POST /parent', extract='id'), declaration(via='POST /parent', extract='id')],
        [declaration(seeded_by='missing', depends_on=[])],
        [declaration(seeded_by='seed_export')],
        [declaration(unseedable='')],
        [declaration(same_as='other'), declaration('other', unseedable='No HTTP creator')],
        [declaration(same_as='other', via='POST /parent')],
        [declaration(via='POST /parent', extract='id', provides=['extra'])],
        [declaration(seeded_by='seed_task', depends_on=[], provides='extra')],
        [declaration(seeded_by='seed_task', depends_on=[], provides=['not-an-identifier'])],
        [declaration(seeded_by='seed_task', depends_on=[], provides=['other']), declaration('other', same_as='parent')],
    ],
)
def test_bad_dependency_graph_fails_before_any_http(entries):
    client = Mock(spec=seeds.AttackClient)
    with pytest.raises(ValueError):
        seeds.resolve_parameters(client, surface=tiny_surface(*entries), spec={'paths': {}})
    client.request.assert_not_called()


@pytest.mark.parametrize('status', [199, 301, 400, 401, 404, 422, 500])
def test_http_failure_cannot_produce_a_seed(status):
    client = Mock(spec=seeds.AttackClient)
    client.request.return_value = response(status, {'id': 'plausible-but-failed'})
    with pytest.raises(RuntimeError, match=f'HTTP {status}'):
        seeds.resolve_parameters(
            client, surface=tiny_surface(declaration(via='POST /parent', extract='id')), spec={'paths': {}}
        )


def test_null_success_response_and_empty_seeder_result_cannot_produce_ids(monkeypatch):
    client = Mock(spec=seeds.AttackClient)
    client.request.return_value = response(200, None)
    with pytest.raises(RuntimeError, match='no JSON'):
        seeds.resolve_parameters(
            client, surface=tiny_surface(declaration(via='POST /parent', extract='id')), spec={'paths': {}}
        )
    monkeypatch.setitem(seeds._SEEDERS, 'empty', lambda ctx, entry: None)
    with pytest.raises(RuntimeError, match='instead of an id'):
        seeds.resolve_parameters(
            client, surface=tiny_surface(declaration(seeded_by='empty', depends_on=[])), spec={'paths': {}}
        )


def test_repeated_resolutions_get_fresh_tokens_as_the_ordinary_caller():
    client, admin = Mock(spec=seeds.AttackClient), Mock(spec=seeds.AttackClient)
    client.request.side_effect = lambda *a, **kw: response(200, {'id': kw['json']['name']})
    surface = tiny_surface(declaration(via='POST /parent', extract='id', actor='user', body={'name': '{token}'}))
    spec = {'paths': {'/parent/{id}': {'get': {}}}}
    first = seeds.resolve_parameters(client, admin=admin, surface=surface, spec=spec)
    second = seeds.resolve_parameters(client, admin=admin, surface=surface, spec=spec)
    assert first != second
    assert '{token}' not in next(iter(first.values()))
    admin.request.assert_not_called()


def test_multipart_seed_and_get_extraction_use_the_expected_http_shapes():
    client = Mock(spec=seeds.AttackClient)
    client.request.side_effect = [response(200, {'id': 'file'}), response(200, {'filename': 'file.txt'})]
    upload = declaration(
        via='POST /files/',
        extract='id',
        query={'process': False},
        files=[
            {
                'field': 'file',
                'filename': 'attack-{token}.txt',
                'content': 'hello {token}',
                'content_type': 'text/plain',
            }
        ],
    )
    filename = declaration('filename', via='GET /files/{parent}', extract='filename', depends_on=['parent'])
    seeds.resolve_parameters(client, surface=tiny_surface(upload, filename), spec={'paths': {}})
    first, second = client.request.call_args_list
    assert first.kwargs['params'] == {'process': False}
    assert first.kwargs['files'][0][0] == 'file'
    assert '{token}' not in first.kwargs['files'][0][1][0]
    assert 'json' not in first.kwargs and 'json' not in second.kwargs


def test_unseedable_is_explicit_and_not_returned_as_a_value_or_ordinary_target():
    surface = tiny_surface(declaration(unseedable='No HTTP creating endpoint'))
    spec = {'paths': {'/parent/{id}': {'get': {}}}}
    client = Mock(spec=seeds.AttackClient)
    assert seeds.resolve_parameters(client, surface=surface, spec=spec) == {}
    assert seeds.ordinary_targets(spec, surface) == []
    client.request.assert_not_called()


def test_destructive_routes_have_reasons_exist_in_spec_and_never_become_ordinary_targets():
    required = {
        'POST /api/v1/auths/signout',
        'DELETE /api/v1/auths/api_key',
        'POST /api/v1/auths/update/password',
        'DELETE /api/v1/auths/oauth/sessions/{provider}',
    }
    assert required <= seeds.DESTRUCTIVE.keys()
    assert not seeds.DESTRUCTIVE.keys() & set(seeds.ordinary_targets())
    for route, reason in seeds.DESTRUCTIVE.items():
        method, path = route.split(' ', 1)
        assert method.lower() in SPEC['paths'][path]
        assert reason.strip()


@pytest.mark.parametrize('route', list(seeds.DESTRUCTIVE))
def test_resolver_refuses_destructive_http_requests_even_with_concrete_ids(route):
    method, path = route.split(' ', 1)
    path = path.replace('{user_id}', 'disposable-user').replace('{provider}', 'mcp:server')
    path = re.sub(r'\{[^}]+\}', 'concrete-id', path)
    client = Mock(spec=seeds.AttackClient)
    surface = tiny_surface(declaration(via=f'{method} {path}', extract='id'))
    with pytest.raises(RuntimeError, match='Destructive route'):
        seeds.resolve_parameters(client, surface=surface, spec={'paths': {}})
    client.request.assert_not_called()


def test_every_plain_seed_calls_a_real_nondestructive_operation():
    for p in seeds.SURFACE['parameter']:
        if 'via' not in p:
            continue
        method, path = p['via'].split(' ', 1)
        shape = re.sub(r'\{[^}]+\}', '{}', path)
        candidates = [
            template
            for template, item in SPEC['paths'].items()
            if method.lower() in item and re.sub(r'\{[^}]+\}', '{}', template) == shape
        ]
        assert candidates, p
        assert not seeds.is_destructive(method, candidates[0]), p
        references = set(re.findall(r'\{([^}]+)\}', path)) - {'token'}
        assert references <= set(p.get('depends_on', [])), p


def test_skill_seed_survives_an_existing_name_and_repeated_resolution():
    model = ast.parse((ROOT / 'backend/open_webui/models/skills.py').read_text())
    skill = next(node for node in model.body if isinstance(node, ast.ClassDef) and node.name == 'Skill')
    unique_fields = [
        node.targets[0].id
        for node in skill.body
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Call)
        and any(k.arg == 'unique' and ast.literal_eval(k.value) for k in node.value.keywords)
    ]
    assert set(unique_fields) == {'id', 'name'}, 'Review all skill uniqueness constraints when the model changes'
    entry = next(p for p in seeds.SURFACE['parameter'] if p['key'] == 'skill')
    owner, admin = Mock(spec=seeds.AttackClient), Mock(spec=seeds.AttackClient)
    with sqlite3.connect(':memory:') as db:
        db.execute('CREATE TABLE skill (id TEXT UNIQUE, name TEXT UNIQUE)')
        db.execute('INSERT INTO skill VALUES (?, ?)', ('previous-id', 'Attack skill'))

        def create(method, path, **kwargs):
            assert (method, path) == ('POST', '/api/v1/skills/create')
            body = kwargs['json']
            try:
                db.execute('INSERT INTO skill VALUES (?, ?)', (body['id'], body['name']))
            except sqlite3.IntegrityError:
                return response(400, {'detail': 'Error creating skill'})
            return response(200, {'id': body['id']})

        owner.request.side_effect = create
        for _ in range(2):
            seeds.resolve_parameters(owner, admin=admin, surface=tiny_surface(entry), spec={'paths': {}})
        assert db.execute('SELECT COUNT(*) FROM skill').fetchone()[0] == 3
    admin.request.assert_not_called()


def test_plain_seed_bodies_supply_all_required_schema_fields():
    def check(value, schema):
        if '$ref' in schema:
            schema = SPEC['components']['schemas'][schema['$ref'].rsplit('/', 1)[1]]
        for part in schema.get('allOf', []):
            check(value, part)
        if isinstance(value, dict):
            assert set(schema.get('required', [])) <= value.keys()
            for key, child in schema.get('properties', {}).items():
                if key in value:
                    check(value[key], child)

    for entry in seeds.SURFACE['parameter']:
        if 'via' not in entry or 'body' not in entry:
            continue
        method, path = entry['via'].split(' ', 1)
        shape = re.sub(r'\{[^}]+\}', '{}', path)
        operation = next(
            item[method.lower()]
            for template, item in SPEC['paths'].items()
            if method.lower() in item and re.sub(r'\{[^}]+\}', '{}', template) == shape
        )
        check(entry['body'], operation['requestBody']['content']['application/json']['schema'])


def test_a_seeder_must_provide_what_it_declares_and_only_that(monkeypatch):
    def provides_extra(ctx, entry):
        ctx.provide(entry, 'extra', 'extra-value')
        return 'own-id'

    monkeypatch.setitem(seeds._SEEDERS, 'provides_extra', provides_extra)
    monkeypatch.setitem(seeds._SEEDERS, 'provides_nothing', lambda ctx, entry: 'own-id')
    client = Mock(spec=seeds.AttackClient)
    spec = {'paths': {'/parent/{id}': {'get': {}}}}
    surface = tiny_surface(declaration(seeded_by='provides_extra', depends_on=[], provides=['extra']))
    surface['field'] = [{'route': 'GET /parent/{id}', 'params': {'q': '{extra}'}}]
    assert seeds.resolve_parameters(client, surface=surface, spec=spec).fields == {
        'GET /parent/{id}': {'params': {'q': 'extra-value'}}
    }
    undeclared = tiny_surface(declaration(seeded_by='provides_extra', depends_on=[]))
    with pytest.raises(RuntimeError, match='does not declare'):
        seeds.resolve_parameters(client, surface=undeclared, spec=spec)
    silent = tiny_surface(declaration(seeded_by='provides_nothing', depends_on=[], provides=['extra']))
    with pytest.raises(RuntimeError, match='did not provide'):
        seeds.resolve_parameters(client, surface=silent, spec=spec)


def test_active_function_serves_chat_actions_and_user_valves():
    for path, name in [
        ('/api/chat/actions/{action_id}', 'action_id'),
        ('/api/v1/functions/id/{id}/valves/user/update', 'id'),
    ]:
        declaration = seeds.parameter_for(path, name)
        key = declaration.get('same_as', declaration['key'])
        assert key == 'active_function', path


@pytest.mark.parametrize(
    'key,provided', [('removable_knowledge', 'removable_file'), ('update_knowledge', 'update_file')]
)
def test_knowledge_file_seeder_adds_a_file_nothing_else_uses(key, provided):
    ctx = seeds.SeedContext(Mock(), Mock(), seeds.SURFACE, token='fixture')
    ctx.request = Mock(
        side_effect=[
            {'id': 'own-file'},
            {'status': True},
            {'id': 'own-kb'},
            {'id': 'own-kb', 'files': [{'id': 'own-file'}]},
        ]
    )
    parameter = next(p for p in seeds.SURFACE['parameter'] if p['key'] == key)
    assert seeds.seed_knowledge_file(ctx, parameter) == 'own-kb'
    assert ctx.values[provided] == 'own-file'
    upload, process, create, add = ctx.request.call_args_list
    assert upload.args == ('POST', '/api/v1/files/') and upload.kwargs['params'] == {'process': False}
    assert process.kwargs['json'] == {'file_id': 'own-file'}
    assert add.args == ('POST', '/api/v1/knowledge/own-kb/file/add')
    assert add.kwargs['json'] == {'file_id': 'own-file'}
    assert all(call.kwargs['actor'] is ctx.client for call in ctx.request.call_args_list)
    assert not seeds.is_destructive('POST', '/api/v1/knowledge/{id}/file/remove')
    ctx.request = Mock(
        side_effect=[{'id': 'own-file'}, {'status': True}, {'id': 'own-kb'}, {'id': 'own-kb', 'files': []}]
    )
    with pytest.raises(RuntimeError, match='did not join'):
        seeds.seed_knowledge_file(ctx, parameter)


def test_totp_code_matches_rfc_6238_sha1_vectors():
    # RFC 6238 appendix B, SHA-1 seed "12345678901234567890", truncated to 6 digits.
    secret = base64.b32encode(b'12345678901234567890').decode()
    for at, code in [(59, '287082'), (1111111109, '081804'), (1234567890, '005924'), (2000000000, '279037')]:
        assert seeds.totp_code(secret, at=at) == code
    assert seeds.totp_code(secret.rstrip('=').lower(), at=59) == '287082'


PUBLIC_MODEL_READ = {'principal_type': 'user', 'principal_id': '*', 'permission': 'read'}


def test_v2_chat_seeds_committed_messages_and_shares_to_a_known_group(monkeypatch):
    from . import crossuser

    monkeypatch.setenv('RUNTIME_SOEV_BACKED', 'drive')
    ctx = seeds.SeedContext(Mock(), Mock(), seeds.SURFACE, token='fictional')
    ctx.values.update(folder='fictional-folder', model='fictional-model', group='fictional-group')
    ctx.request = Mock(
        side_effect=[
            {'id': 'fictional-thread'},
            {'choices': [{'message': {'content': 'Fictional answer.'}}]},
            {'messages': [{'position': 7, 'finished': True}]},
            {},
        ]
    )
    parameter = next(p for p in seeds.SURFACE['parameter'] if p['key'] == 'chat_fork')
    assert seeds.seed_chat(ctx, parameter) == 'fictional-thread'
    assert ctx.values['chat_fork_position'] == '7'
    create, turn, projection, folder = ctx.request.call_args_list
    assert create.kwargs['json'] == {'agent': 'soev_react', 'input': {'text': 'A fictional regression conversation.'}}
    assert turn.kwargs['json']['follow_thread'] is True
    assert turn.kwargs['json']['chat_id'] == 'fictional-thread'
    assert folder.kwargs['json'] == {'folder_id': 'fictional-folder'}
    assert all(call.kwargs['actor'] is ctx.client for call in ctx.request.call_args_list)
    ctx.values.update(chat='fictional-thread', chat_message='7')
    ctx.request = Mock(return_value={'share_id': 'fictional-thread'})
    assert seeds.seed_chat_share(ctx, {'key': 'share'}) == 'fictional-thread'
    assert ctx.request.call_args.kwargs['json'] == {
        'audience': {'kind': 'group', 'id': 'platform:group:fictional-group'},
        'at': 7,
    }
    entries = [
        entry
        for entry in seeds.SURFACE['field']
        if entry['route']
        in {
            'POST /api/v1/chats/new',
            'POST /api/v1/chats/{id}/fork',
            'POST /api/v1/chats/{id}/share',
        }
    ]
    resolved = seeds.Resolved(fields=seeds.resolve_fields(ctx, entries=entries))
    assert crossuser._benign('POST /api/v1/chats/new', resolved, seeds.SPEC)['json'] == {
        'agent': 'soev_react',
        'input': {'text': 'Fictional conversation.'},
    }
    assert resolved.fields['POST /api/v1/chats/{id}/fork']['json'] == {'at': 7}
    assert resolved.fields['POST /api/v1/chats/{id}/share']['json']['at'] == 7


@pytest.mark.parametrize('readable', [False, True])
def test_v2_feedback_seed_requires_committed_owner_readback(readable):
    ctx = seeds.SeedContext(Mock(), Mock(), seeds.SURFACE, token='fictional')
    ctx.values.update(chat='fictional-thread', chat_message='7')
    ctx.client.request.return_value = response(204, None)
    ctx.client.request.return_value._content_consumed = True
    ctx.request = Mock(
        return_value={
            'items': [
                {
                    'thread_id': 'fictional-thread',
                    'rating': 1,
                    'reason': 'accuracy',
                    'tags': ['language'],
                    'comment': 'Fictional feedback fictional',
                }
            ]
            if readable
            else []
        }
    )
    if readable:
        assert seeds.seed_feedback(ctx, {'key': 'feedback'}) == 'attack_feedback_probe_fictional'
    else:
        with pytest.raises(RuntimeError, match='could not be read back'):
            seeds.seed_feedback(ctx, {'key': 'feedback'})
    ctx.client.request.assert_called_once_with(
        'POST',
        '/api/v1/evaluations/feedback',
        json={
            'thread_id': 'fictional-thread',
            'position': 7,
            'rating': 1,
            'reason': 'accuracy',
            'tags': ['language'],
            'comment': 'Fictional feedback fictional',
            'comment_authored': True,
        },
    )


@pytest.mark.parametrize('role', ['admin', 'user'])
def test_real_model_checks_require_a_row_and_read_access_even_for_admin(role):
    # Execute the application checks without importing its DB/startup stack.
    scope = {
        'Models': SimpleNamespace(get_model_by_id=AsyncMock(return_value=None)),
        'Groups': SimpleNamespace(get_groups_by_member_id=AsyncMock(return_value=[])),
        'AccessGrants': SimpleNamespace(
            has_access=AsyncMock(return_value=False),
            get_accessible_resource_ids=AsyncMock(return_value=set()),
        ),
        'has_base_model_access': AsyncMock(return_value=True),
        # [Gradient] Outside v2 mode no model is offered by the soev catalog.
        'model_catalog': SimpleNamespace(is_unconfigured=lambda model: False, is_offered=AsyncMock(return_value=False)),
        'MODEL_WHITELIST': [],
        'can_bypass_access_control': lambda user: False,
        'BYPASS_MODEL_ACCESS_CONTROL': False,
    }
    tree = ast.parse((ROOT / 'backend/open_webui/utils/models.py').read_text())
    checks = ast.Module(
        body=[
            node
            for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name in {'check_model_access', 'get_filtered_models'}
        ],
        type_ignores=[],
    )
    # nosec B102 - compiles two functions lifted from this repo's own utils/models.py so the
    # access checks can be exercised without importing the application. No external input.
    exec(compile(checks, 'utils/models.py', 'exec'), scope)  # nosec B102

    async def exercise():
        user = SimpleNamespace(id=f'{role}-caller', role=role)
        model = {'id': 'stub-model'}
        assert await scope['get_filtered_models']([model], user) == []
        with pytest.raises(Exception, match='Model not found'):
            await scope['check_model_access'](user, model)

        row = SimpleNamespace(id='stub-model', user_id='fixture-owner', base_model_id=None)
        scope['Models'].get_model_by_id.return_value = row
        model['info'] = vars(row)
        with pytest.raises(Exception, match='Model not found'):
            await scope['check_model_access'](user, model)
        assert await scope['get_filtered_models']([model], user) == []

        scope['AccessGrants'].has_access.return_value = True
        scope['AccessGrants'].get_accessible_resource_ids.return_value = {'stub-model'}
        await scope['check_model_access'](user, model)
        scope['AccessGrants'].has_access.assert_awaited_with(
            user_id=user.id,
            resource_type='model',
            resource_id='stub-model',
            permission='read',
            # v0.11.3 resolves the caller's groups once and passes them down, so
            # has_access gained user_group_ids alongside db.
            user_group_ids=set(),
            db=None,
        )
        assert await scope['get_filtered_models']([model], user) == [model]

    asyncio.run(exercise())


def test_drive_model_seed_requires_the_active_soev_catalog_default(monkeypatch):
    monkeypatch.setenv('RUNTIME_SOEV_BACKED', 'drive')
    ctx = seeds.SeedContext(Mock(), Mock(), seeds.SURFACE)
    ctx.request = Mock(
        side_effect=[
            {'default_models': 'ci/chat'},
            {'data': [{'id': 'ci/chat'}]},
        ]
    )
    assert seeds.seed_model(ctx, {'key': 'model'}) == 'ci/chat'
    assert 'upstream_model' not in ctx.values
    config, registry = ctx.request.call_args_list
    assert config.args == ('GET', '/api/config')
    assert config.kwargs == {'actor': ctx.client}
    assert registry.args == ('GET', '/api/models')
    assert registry.kwargs == {'actor': ctx.client}


def test_skill_bundle_seed_creates_separate_owned_files():
    entry = next(p for p in seeds.SURFACE['parameter'] if p['key'] == 'skill_bundle')
    ctx = seeds.SeedContext(Mock(), Mock(), seeds.SURFACE)
    ctx.request = Mock(side_effect=[{'id': 'fictional-skill'}, {}, {}, {}])
    assert seeds.seed_skill_bundle(ctx, entry) == 'fictional-skill'
    requests = ctx.request.call_args_list
    assert requests[0].args == ('POST', '/api/v1/skills/create')
    assert {call.kwargs['json']['path'] for call in requests[1:]} == {
        'fictional-edit.txt',
        'fictional-move.txt',
        'fictional-remove.txt',
    }
    assert all(call.kwargs['actor'] is ctx.client for call in requests)


def test_product_config_inventory_has_real_routes_proofs_and_seeds():
    coverage = tomllib.loads((ROOT / 'security/product-config-coverage.toml').read_text())
    operations = {
        f'{method.upper()} {path}' for path, item in SPEC['paths'].items() for method in item if method in METHODS
    }
    for entry in coverage.values():
        assert set(entry.get('routes', ())) <= operations
        assert entry['classification'] and entry['seed']
        proof = entry['proof'].split(':', 1)[0]
        assert (ROOT / proof).is_file()
    expected = {
        f'{method.upper()} {path}'
        for path, item in SPEC['paths'].items()
        if path.startswith('/api/v1/skills/id/{id}/files')
        for method in item
        if method in METHODS
    }
    assert set(coverage['skill_files']['routes']) == expected


def test_ollama_seed_uses_the_configured_upstream_catalog(monkeypatch):
    entry = next(p for p in seeds.SURFACE['parameter'] if p['key'] == 'ollama_index')
    ctx = seeds.SeedContext(Mock(), Mock(), seeds.SURFACE)
    ctx.values['model'] = 'fictional/catalog-model'
    ctx.request = Mock(side_effect=AssertionError('No retired OWUI discovery route'))
    monkeypatch.setenv('OLLAMA_BASE_URLS', 'http://empty.invalid;http://ollama.invalid')
    responses = [Mock(), Mock()]
    responses[0].json.return_value = {'models': []}
    responses[1].json.return_value = {'models': [{'model': 'fictional-ollama'}]}
    get = Mock(side_effect=responses)
    monkeypatch.setattr(seeds.requests, 'get', get)
    assert seeds.seed_ollama_connection(ctx, entry) == '1'
    assert ctx.values['upstream_model'] == 'fictional-ollama'
    assert [call.args for call in get.call_args_list] == [
        ('http://empty.invalid/api/tags',),
        ('http://ollama.invalid/api/tags',),
    ]
    for response in responses:
        response.raise_for_status.assert_called_once_with()
        response.close.assert_called_once_with()
    ctx.request.assert_not_called()


def test_ollama_seed_refuses_an_empty_catalog_instead_of_substituting_a_model(monkeypatch):
    entry = next(p for p in seeds.SURFACE['parameter'] if p['key'] == 'ollama_index')
    ctx = seeds.SeedContext(Mock(), Mock(), seeds.SURFACE)
    ctx.values['model'] = 'fictional/catalog-model'
    monkeypatch.setenv('OLLAMA_BASE_URLS', 'http://ollama.invalid')
    response = Mock()
    response.json.return_value = {'models': []}
    monkeypatch.setattr(seeds.requests, 'get', Mock(return_value=response))
    with pytest.raises(RuntimeError, match='No model is available'):
        seeds.seed_ollama_connection(ctx, entry)
    assert 'upstream_model' not in ctx.values
    response.close.assert_called_once_with()


def test_ollama_seed_requires_explicit_upstream_configuration(monkeypatch):
    monkeypatch.delenv('OLLAMA_BASE_URLS', raising=False)
    with pytest.raises(KeyError, match='OLLAMA_BASE_URLS'):
        seeds.seed_ollama_connection(Mock(), {})


@pytest.mark.parametrize('default', [None, '', 'a/b,c/d', ['ci/chat']])
def test_drive_model_seed_needs_exactly_one_catalog_default(monkeypatch, default):
    monkeypatch.setenv('RUNTIME_SOEV_BACKED', 'drive')
    ctx = seeds.SeedContext(Mock(), Mock(), seeds.SURFACE)
    ctx.request = Mock(return_value={'default_models': default})
    with pytest.raises(RuntimeError, match='expected one soev catalog default model'):
        seeds.seed_model(ctx, {'key': 'model'})


def test_connection_proxy_fields_use_the_upstream_model():
    # These routes resolve the model against a connection's own list, not the registry.
    direct = {
        'POST /ollama/api/chat/{url_idx}',
        'POST /ollama/v1/chat/completions/{url_idx}',
        'POST /ollama/v1/messages/{url_idx}',
        'POST /ollama/v1/completions/{url_idx}',
        'POST /ollama/v1/embeddings/{url_idx}',
    }
    models = {
        entry['route']: entry['json']['model'] for entry in seeds.SURFACE['field'] if 'model' in entry.get('json', {})
    }
    assert direct <= models.keys()
    assert {route for route, value in models.items() if value == '{upstream_model}'} == direct


LIVE_READS = [
    '/api/v1/meetings/{meeting_id}',
    '/api/v1/users/{user_id}',
    '/api/v1/users/{user_id}/shared-data',
    '/api/v1/folders/{id}',
    '/api/v1/chats/{id}',
    '/api/v1/chats/share/{share_id}',
    '/api/v1/chats/shared/{id}/access',
    '/api/v1/chats/{id}/share',
    '/api/v1/knowledge/{id}',
    '/api/v1/files/{id}',
    '/api/v1/files/{id}/content/{file_name}',
    '/api/v1/notes/{id}',
    '/api/v1/calendars/{calendar_id}',
    '/api/v1/calendars/events/{event_id}',
    '/api/v1/automations/{id}',
    '/api/v1/groups/id/{id}',
    '/api/v1/functions/id/{id}',
    '/api/v1/functions/id/{id}/valves/spec',
    '/api/v1/tools/id/{id}/valves/spec',
    '/api/v1/skills/id/{id}',
    '/api/v1/prompts/id/{prompt_id}/history/{history_id}',
    '/api/v1/evaluations/feedback/{id}',
    '/api/v1/archives/{archive_id}',
    '/api/v1/invites/{token}/validate',
    '/api/v1/configs/namespace/{namespace}',
    '/api/v1/terminals/{server_id}/{path}',
]


def test_fields_fill_seed_keys_and_the_token_after_their_setup_request():
    parent = declaration(via='POST /parent', extract='id')
    surface = {
        **tiny_surface(parent),
        'field': [
            {
                'route': 'POST /thing',
                'json': {'parent_id': '{parent}', 'call': 'call_{token}'},
                'params': {'id': '{parent}'},
                'setup': [{'via': 'POST /parent/{parent}/attach', 'body': {'member': '{parent}'}}],
            }
        ],
    }
    client = Mock(spec=seeds.AttackClient)
    client.request.side_effect = [response(200, {'id': 'p/1'}), response(200, {})]
    result = seeds.resolve_parameters(client, surface=surface, spec={'paths': {}})
    assert result == {}
    fields = result.fields['POST /thing']
    assert fields['json']['parent_id'] == 'p/1'
    assert fields['params'] == {'id': 'p/1'}
    assert fields['json']['call'].startswith('call_') and '{token}' not in fields['json']['call']
    setup = client.request.call_args_list[1]
    assert setup.args == ('POST', '/parent/p%2F1/attach')
    assert setup.kwargs['json'] == {'member': 'p/1'}


def test_a_field_naming_a_key_nothing_seeded_fails_resolution():
    surface = {**tiny_surface(), 'field': [{'route': 'POST /thing', 'json': {'id': '{missing}'}}]}
    with pytest.raises(RuntimeError, match="'missing'"):
        seeds.resolve_parameters(Mock(spec=seeds.AttackClient), surface=surface, spec={'paths': {}})


def _strings(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, str):
        yield value


def _multipart_properties(operation):
    content = operation.get('requestBody', {}).get('content', {}).get('multipart/form-data')
    if not content:
        return set()
    schema = content.get('schema', {})
    if '$ref' in schema:
        schema = SPEC['components']['schemas'][schema['$ref'].split('/')[-1]]
    return set(schema.get('properties', {}))


def test_every_control_names_a_unique_live_route():
    operations = {
        f'{method.upper()} {path}' for path, item in SPEC['paths'].items() for method in item if method in METHODS
    }
    controls = seeds.SURFACE['control']
    routes = [entry['route'] for entry in controls]
    assert len(routes) == len(set(routes))
    assert set(routes) <= operations


def test_every_declared_field_names_a_real_route_field_and_a_seeded_key():
    # A declaration outliving its field, or naming a key nothing seeds, would
    # send a plausible body the handler never reads -- the route reported driven
    # and never entered, which is the failure [[field]] exists to remove.
    from openapi_surface import body_skeletons, writable_string_fields

    operations = {
        f'{method.upper()} {path}': operation
        for path, item in SPEC['paths'].items()
        for method, operation in item.items()
        if method in METHODS
    }
    templates = {re.sub(r'\{[^}]+\}', '{}', route) for route in operations}
    seeded = {
        p['key'] for p in [*seeds.SURFACE['parameter'], *seeds.SURFACE.get('fixture', [])] if 'unseedable' not in p
    }
    seeded |= {name for p in seeds.SURFACE['parameter'] for name in p.get('provides', [])}
    writable = writable_string_fields(SPEC)
    skeletons = body_skeletons(SPEC)
    assert len({entry['route'] for entry in seeds.SURFACE['field']}) == len(seeds.SURFACE['field'])
    for entry in seeds.SURFACE['field']:
        route = entry['route']
        assert route in operations, route
        assert entry['description'].strip(), route
        assert (entry.keys() | entry.get('v2', {}).keys()) & {'json', 'params', 'files', 'form', 'headers'}, route
        for name, value in entry.get('headers', {}).items():
            assert name == 'Content-Type' and value == 'application/json', route
        for name in entry.get('json', {}):
            # A string leaf, or a field the body skeleton builds or nests under (a required bool, a config).
            skeleton = skeletons.get(route, {})
            root = name.split('.', 1)[0].split('[', 1)[0]
            known = name in writable.get(route, ()) or (
                isinstance(skeleton, dict) and (name in skeleton or root in skeleton)
            )
            assert known, f'{route}: {name} is not a field of its body'
        query = {p['name'] for p in operations[route].get('parameters', []) if p.get('in') == 'query'}
        for name in entry.get('params', {}):
            assert name in query, f'{route}: {name} is not a query parameter'
        parts = _multipart_properties(operations[route])
        for name in [part['field'] for part in entry.get('files', [])] + list(entry.get('form', {})):
            assert name in parts, f'{route}: {name} is not a part of its multipart body'
        for step in entry.get('setup', []):
            assert re.sub(r'\{[^}]+\}', '{}', step['via']) in templates, step['via']
        values = [
            *_strings(entry.get('v2', {})),
            *_strings(entry.get('json', {})),
            *_strings(entry.get('params', {})),
            *_strings(entry.get('setup', [])),
        ]
        values += [*_strings(entry.get('form', {})), *_strings(entry.get('files', []))]
        values += [*_strings(entry.get('headers', {}))]
        referenced = {key for value in values for key in re.findall(r'\{([A-Za-z_][A-Za-z0-9_]*)\}', value)} - {'token'}
        assert referenced <= seeded, f'{route}: {sorted(referenced - seeded)}'


def test_fields_turn_declared_files_into_request_parts_and_leave_json_braces_alone():
    surface = {
        **tiny_surface(declaration(via='POST /parent', extract='id')),
        'field': [
            {
                'route': 'POST /upload',
                'files': [
                    {
                        'field': 'data',
                        'filename': 'f-{token}.json',
                        'content': '{"a": {"b": "{parent}"}}',
                        'content_type': 'application/json',
                    }
                ],
                'form': {'idx': '{parent}'},
            }
        ],
    }
    client = Mock(spec=seeds.AttackClient)
    client.request.return_value = response(200, {'id': 'p1'})
    fields = seeds.resolve_parameters(client, surface=surface, spec={'paths': {}}).fields['POST /upload']
    ((name, (filename, content, content_type)),) = fields['files']
    assert (name, content, content_type) == ('data', '{"a": {"b": "p1"}}', 'application/json')
    assert filename.startswith('f-') and filename.endswith('.json') and '{token}' not in filename
    assert fields['form'] == {'idx': 'p1'}


@pytest.mark.parametrize('suffix,method', [('versions', 'GET'), ('read', 'POST')])
def test_conversation_routes_reuse_the_owner_thread_fixture(monkeypatch, suffix, method):
    monkeypatch.setenv('RUNTIME_SOEV_BACKED', 'drive')
    path = '/api/v1/threads/{thread_id}/' + suffix
    parameter = seeds.parameter_for(path, 'thread_id')
    assert parameter['key'] == 'platform_thread_' + suffix
    assert parameter['same_as'] == 'meeting'
    assert parameter['backed_by'] == 'soev-solutions'
    assert f'{method} {path}' in seeds.ordinary_targets()


def test_fanout_view_controls_use_the_new_queries_and_existing_owned_resources():
    fields = {entry['route']: entry for entry in seeds.SURFACE['field']}
    assert fields['GET /api/v1/chats/']['params'] == {'include_sidebar': True}
    assert fields['GET /api/v1/chats/list']['params'] == {'include_sidebar': True}
    assert fields['GET /api/v1/chats/{id}']['params'] == {'include_versions': True, 'current': True}
    assert fields['GET /api/v1/knowledge/{id}/files']['params'] == {'include_context': True}
    assert seeds.parameter_for('/api/v1/knowledge/{id}/files', 'id')['key'] == 'knowledge'


def test_bug_report_has_an_authenticated_positive_control_and_no_waiver():
    route = 'POST /api/v1/feedback/report'
    field = next(item for item in seeds.SURFACE['field'] if item['route'] == route)
    assert field['json']['category'] == 'bug'
    assert set(field['json']) == {'category', 'description', 'context.route', 'context.trace_id'}
    assert route in seeds.ordinary_targets()
    assert route not in seeds.SOEV_BACKED


def test_query_boolean_fields_use_the_platform_wire_format():
    ctx = seeds.SeedContext(
        Mock(),
        Mock(),
        {
            'field': [
                {
                    'route': 'GET /fictional',
                    'params': {'include_sidebar': True, 'include_archived': False, 'page': 1},
                }
            ]
        },
    )
    fields = seeds.resolve_fields(ctx)
    request = requests.Request('GET', 'https://owui.invalid/fictional', **fields['GET /fictional']).prepare()
    assert request.url.endswith('include_sidebar=true&include_archived=false&page=1')


def test_disabled_oauth_client_is_fresh_in_each_resolution_without_http(monkeypatch):
    monkeypatch.setenv('RUNTIME_SOEV_BACKED', 'drive')
    route = '/oauth/clients/{client_id}/authorize'
    parameter = seeds.parameter_for(route, 'client_id')
    surface = {'parameter': [parameter]}
    spec = {'paths': {route: {'get': {}}}}
    actor = Mock()
    tokens = ['fictional-first-pass', 'fictional-second-pass']
    monkeypatch.setattr(seeds.secrets, 'token_hex', Mock(side_effect=tokens))
    resolved = [seeds.resolve_parameters(actor, surface=surface, spec=spec) for _ in tokens]
    assert [result[route, 'client_id'] for result in resolved] == [
        f'mcp:fictional-disabled-{token}' for token in tokens
    ]
    actor.request.assert_not_called()
