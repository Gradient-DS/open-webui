import json
import os
import re
import tomllib
from pathlib import Path
from unittest.mock import Mock
from urllib.parse import quote

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


def test_route_scoping_distinguishes_resources_and_attachment_parent():
    for path, key in [
        ('/api/v1/chats/{id}', 'chat'),
        ('/api/v1/knowledge/{id}/files', 'knowledge'),
        ('/api/v1/knowledge/external/connections/{id}', 'connection'),
        ('/api/v1/files/{id}/content', 'file'),
        ('/api/v1/files/{id}/attachments/{attachment_id}', 'attachment_file'),
        ('/api/v1/chats/shared/{id}/access', 'shared_access'),
        ('/api/v1/chats/{id}/clone/shared', 'shared_clone'),
    ]:
        assert seeds.parameter_for(path, 'id')['key'] == key
    with pytest.raises(KeyError, match='Unaccounted'):
        seeds.parameter_for('/api/v1/files/{id}suffix', 'id')


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


def test_repeated_resolutions_get_fresh_tokens_and_use_admin_only_where_declared():
    client, admin = Mock(spec=seeds.AttackClient), Mock(spec=seeds.AttackClient)
    admin.request.side_effect = lambda *a, **kw: response(200, {'id': kw['json']['name']})
    surface = tiny_surface(declaration(via='POST /parent', extract='id', actor='admin', body={'name': '{token}'}))
    spec = {'paths': {'/parent/{id}': {'get': {}}}}
    first = seeds.resolve_parameters(client, admin=admin, surface=surface, spec=spec)
    second = seeds.resolve_parameters(client, admin=admin, surface=surface, spec=spec)
    assert first != second
    assert '{token}' not in next(iter(first.values()))
    client.request.assert_not_called()


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
        'DELETE /api/v1/users/{user_id}',
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


def test_integration_2xx_with_document_or_attachment_failure_is_rejected():
    for outcome in [
        {'errors': 1, 'created': 0, 'total': 1},
        {'errors': 0, 'created': 1, 'total': 1, 'documents': [{'attachments_saved': 0}]},
        {'errors': 0, 'created': 1, 'total': 1, 'documents': []},
        {'errors': 0, 'created': 1, 'total': 1, 'documents': [None]},
    ]:
        ctx = seeds.SeedContext(Mock(), Mock(), seeds.SURFACE)
        ctx.request = Mock(side_effect=[{'id': 'owner'}, {'providers': {}}, {'providers': {}}, outcome])
        with pytest.raises(RuntimeError, match='did not create'):
            seeds.seed_integration(ctx, {'key': 'integration_source'})


def test_task_seeder_rejects_an_id_that_has_already_finished(monkeypatch):
    monkeypatch.setattr(seeds.time, 'sleep', lambda seconds: None)
    ctx = seeds.SeedContext(Mock(), Mock(), seeds.SURFACE, token='fixture')
    ctx.values['chat'] = 'chat-id'
    ctx.request = Mock(
        side_effect=[
            {'id': 'attack_task_fixture'},
            {'is_active': True},
            {'data': [{'id': 'attack_task_fixture'}]},
            {'task_ids': ['expired-task']},
            {'task_ids': []},
        ]
    )
    with pytest.raises(RuntimeError, match='Task completed'):
        seeds.seed_task(ctx, {'key': 'task', 'content': 'fixture code'})
    ctx.admin.request.assert_called_once_with('POST', '/api/tasks/stop/expired-task')


def test_task_seeder_observes_a_live_task_after_creating_and_activating_its_pipe(monkeypatch):
    monkeypatch.setattr(seeds.time, 'sleep', lambda seconds: None)
    ctx = seeds.SeedContext(Mock(), Mock(), seeds.SURFACE, token='fixture')
    ctx.values['chat'] = 'chat-id'
    ctx.request = Mock(
        side_effect=[
            {'id': 'attack_task_fixture'},
            {'is_active': True},
            {'data': [{'id': 'attack_task_fixture'}]},
            {'task_ids': ['live-task']},
            *[{'task_ids': ['live-task']}] * 3,
        ]
    )
    assert seeds.seed_task(ctx, {'key': 'task', 'content': 'fixture code'}) == 'live-task'
    completion = ctx.request.call_args_list[3]
    assert completion.kwargs['json']['chat_id'] == 'chat-id'
    assert completion.kwargs['json']['model'] == 'attack_task_fixture'


def stop_seed_task(admin, resolved):
    task_id = resolved['/api/tasks/stop/{task_id}', 'task_id']
    result = admin.request('POST', f'/api/tasks/stop/{task_id}')
    assert result.status_code == 200, f'Could not cancel seeded task: HTTP {result.status_code}'


needs_stack = pytest.mark.skipif(not os.getenv('ATTACK_BASE_URL'), reason='requires the running CI stack')


@pytest.fixture(scope='module')
def live_seeds():
    from .identities import ensure_identities

    identities = ensure_identities()
    resolved = None
    try:
        resolved = seeds.resolve_parameters(identities.admin)
        yield identities, resolved
    finally:
        try:
            if resolved is not None:
                stop_seed_task(identities.admin, resolved)
        finally:
            identities.close()


@needs_stack
def test_live_resolution_returns_every_seedable_route_parameter(live_seeds):
    _, resolved = live_seeds
    expected = {
        (path, name) for _, path, name in path_parameters() if 'unseedable' not in seeds.parameter_for(path, name)
    }
    assert resolved.keys() == expected
    assert all(isinstance(value, str) and value.strip() and '{' not in value for value in resolved.values())


@needs_stack
def test_live_second_pass_uses_fresh_resource_fixtures(live_seeds):
    identities, first = live_seeds
    second = seeds.resolve_parameters(identities.admin)
    try:
        for pair, value in first.items():
            p = seeds.parameter_for(*pair)
            if 'same_as' not in p and p.get('fresh', True):
                assert second[pair] != value, f'{p["key"]} reused a previous pass fixture'
    finally:
        stop_seed_task(identities.admin, second)


LIVE_READS = [
    '/api/v1/users/{user_id}',
    '/api/v1/folders/{id}',
    '/api/v1/chats/{id}',
    '/api/v1/chats/share/{share_id}',
    '/api/v1/chats/shared/{id}/access',
    '/api/v1/channels/{id}',
    '/api/v1/channels/{id}/messages/{message_id}',
    '/api/v1/channels/{id}/webhooks',
    '/api/v1/knowledge/{id}',
    '/api/v1/knowledge/external/connections/{id}',
    '/api/v1/files/{id}',
    '/api/v1/files/{id}/content/{file_name}',
    '/api/v1/files/{id}/attachments/{attachment_id}',
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
    '/api/v1/agent-configs/{slug}',
    '/api/v1/configs/namespace/{namespace}',
    '/api/v1/terminals/{server_id}/{path}',
    '/cache/{path}',
]


@needs_stack
@pytest.mark.parametrize('template', LIVE_READS)
def test_live_ids_address_real_resources(live_seeds, template):
    identities, resolved = live_seeds
    path = re.sub(r'\{([^}]+)\}', lambda m: quote(resolved[template, m[1]], safe='/'), template)
    result = identities.admin.request('GET', path)
    assert result.status_code == 200, f'{template}: HTTP {result.status_code}: {result.text[:200]}'
    if template.endswith('/validate'):
        assert result.json()['email'].startswith('attack-invite-token-')


@needs_stack
def test_live_chat_history_message_is_in_its_parent(live_seeds):
    identities, resolved = live_seeds
    template = '/api/v1/chats/{id}/messages/{message_id}'
    chat_id, message_id = resolved[template, 'id'], resolved[template, 'message_id']
    result = identities.admin.request('GET', f'/api/v1/chats/{chat_id}')
    assert result.status_code == 200
    assert result.json()['chat']['history']['messages'][message_id]['id'] == message_id


@needs_stack
def test_live_task_still_exists_after_the_rest_of_seeding(live_seeds):
    identities, resolved = live_seeds
    task_id = resolved['/api/tasks/stop/{task_id}', 'task_id']
    result = identities.admin.request('GET', '/api/tasks')
    assert result.status_code == 200
    assert task_id in result.json()['tasks']


@needs_stack
def test_live_shared_clone_uses_the_share_id_not_the_admin_chat_fallback(live_seeds):
    identities, resolved = live_seeds
    share_id = resolved['/api/v1/chats/{id}/clone/shared', 'id']
    assert share_id == resolved['/api/v1/chats/share/{share_id}', 'share_id']
    assert share_id != resolved['/api/v1/chats/{id}', 'id']
    result = identities.admin.request('POST', f'/api/v1/chats/{share_id}/clone/shared')
    assert result.status_code == 200
    assert result.json()['id'] not in (share_id, resolved['/api/v1/chats/{id}', 'id'])
