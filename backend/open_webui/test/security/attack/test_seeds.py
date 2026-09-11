import ast
import asyncio
import base64
import json
import os
import re
import sqlite3
import tomllib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
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

        admin.request.side_effect = create
        for _ in range(2):
            seeds.resolve_parameters(owner, admin=admin, surface=tiny_surface(entry), spec={'paths': {}})
        assert db.execute('SELECT COUNT(*) FROM skill').fetchone()[0] == 3
    owner.request.assert_not_called()


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


def test_seed_dependencies_preserve_ownership_and_enable_creation_gates():
    entries = {p['key']: p for p in seeds.SURFACE['parameter']}
    for chain in [
        ('folder', 'chat', 'chat_message', 'share'),
        ('channel', 'channel_message', 'channel_webhook', 'webhook_token'),
        ('knowledge', 'directory'),
        ('calendar', 'event'),
        ('prompt', 'prompt_history'),
        ('file', 'filename'),
    ]:
        assert {entries[key].get('actor') for key in chain} == {'admin'}
    order = {p['key']: i for i, p in enumerate(seeds.dependency_order(seeds.SURFACE))}
    for key, gate in [
        ('folder', 'folders.enable'),
        ('channel', 'channels.enable'),
        ('calendar', 'calendar.enable'),
        ('automation', 'automations.enable'),
        ('memory', 'memories.enable'),
        ('archive', 'admin.enable_user_archival'),
    ]:
        assert entries['namespace']['config'][gate] is True
        assert order['namespace'] < order[key]


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


def test_active_function_seeder_turns_its_own_function_on():
    ctx = seeds.SeedContext(Mock(), Mock(), seeds.SURFACE, token='fixture')
    ctx.request = Mock(side_effect=[{'id': 'attack_active_fixture'}, {'is_active': True}])
    parameter = next(p for p in seeds.SURFACE['parameter'] if p['key'] == 'active_function')
    assert seeds.seed_active_function(ctx, parameter) == 'attack_active_fixture'
    create, toggle = ctx.request.call_args_list
    assert create.args == ('POST', '/api/v1/functions/create')
    assert create.kwargs['json']['content'] == parameter['content']
    assert toggle.args == ('POST', '/api/v1/functions/id/attack_active_fixture/toggle')
    assert all(call.kwargs['actor'] is ctx.admin for call in ctx.request.call_args_list)
    # The passes toggle the shared {function}; this one must never be its target.
    function = next(p for p in seeds.SURFACE['parameter'] if p['key'] == 'function')
    assert seeds.parameter_for('/api/v1/functions/id/{id}/toggle', 'id') is function
    ctx.request = Mock(side_effect=[{'id': 'attack_active_fixture'}, {'is_active': False}])
    with pytest.raises(RuntimeError, match='not activated'):
        seeds.seed_active_function(ctx, parameter)


def test_active_function_serves_chat_actions_and_user_valves():
    for path, name in [
        ('/api/chat/actions/{action_id}', 'action_id'),
        ('/api/v1/functions/id/{id}/valves/user/update', 'id'),
    ]:
        declaration = seeds.parameter_for(path, name)
        key = declaration.get('same_as', declaration['key'])
        assert key == 'active_function', path


def test_removable_knowledge_seeder_adds_a_file_nothing_else_uses():
    ctx = seeds.SeedContext(Mock(), Mock(), seeds.SURFACE, token='fixture')
    ctx.request = Mock(
        side_effect=[
            {'id': 'own-file'},
            {'status': True},
            {'id': 'own-kb'},
            {'id': 'own-kb', 'files': [{'id': 'own-file'}]},
        ]
    )
    parameter = next(p for p in seeds.SURFACE['parameter'] if p['key'] == 'removable_knowledge')
    assert seeds.seed_removable_knowledge(ctx, parameter) == 'own-kb'
    assert ctx.values['removable_file'] == 'own-file'
    upload, process, create, add = ctx.request.call_args_list
    assert upload.args == ('POST', '/api/v1/files/') and upload.kwargs['params'] == {'process': False}
    assert process.kwargs['json'] == {'file_id': 'own-file'}
    assert add.args == ('POST', '/api/v1/knowledge/own-kb/file/add')
    assert add.kwargs['json'] == {'file_id': 'own-file'}
    assert all(call.kwargs['actor'] is ctx.admin for call in ctx.request.call_args_list)
    assert not seeds.is_destructive('POST', '/api/v1/knowledge/{id}/file/remove')
    ctx.request = Mock(
        side_effect=[{'id': 'own-file'}, {'status': True}, {'id': 'own-kb'}, {'id': 'own-kb', 'files': []}]
    )
    with pytest.raises(RuntimeError, match='did not join'):
        seeds.seed_removable_knowledge(ctx, parameter)


def test_totp_code_matches_rfc_6238_sha1_vectors():
    # RFC 6238 appendix B, SHA-1 seed "12345678901234567890", truncated to 6 digits.
    secret = base64.b32encode(b'12345678901234567890').decode()
    for at, code in [(59, '287082'), (1111111109, '081804'), (1234567890, '005924'), (2000000000, '279037')]:
        assert seeds.totp_code(secret, at=at) == code
    assert seeds.totp_code(secret.rstrip('=').lower(), at=59) == '287082'


def test_totp_user_seeder_enrols_with_the_new_users_own_session(monkeypatch):
    sessions = []

    class Enrolee:
        def __init__(self, base_url, token):
            sessions.append(token)
            self.verify_identity = Mock()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(seeds, 'AttackClient', Enrolee)
    monkeypatch.setattr(seeds, 'totp_code', lambda secret: f'code-for-{secret}')
    ctx = seeds.SeedContext(Mock(base_url='http://app'), Mock(), seeds.SURFACE, token='fixture')
    ctx.request = Mock(
        side_effect=[{'id': 'totp-user', 'token': 'totp-session'}, {'secret': 'SECRET'}, {'totp_enabled': True}]
    )
    parameter = next(p for p in seeds.SURFACE['parameter'] if p['key'] == 'totp_user')
    assert seeds.seed_totp_user(ctx, parameter) == 'totp-user'
    assert sessions == ['totp-session']
    add, setup, enable = ctx.request.call_args_list
    assert add.kwargs['actor'] is ctx.admin and add.kwargs['json']['role'] == 'user'
    assert setup.args == ('POST', '/api/v1/auths/2fa/totp/setup') and setup.kwargs['actor'] is not ctx.admin
    assert enable.kwargs['json'] == {'password': parameter['password'], 'secret': 'SECRET', 'code': 'code-for-SECRET'}
    # Not the shared {user}: the exporter signs in with that user's password.
    assert seeds.parameter_for('/api/v1/users/{user_id}/2fa/disable', 'user_id') is parameter
    assert seeds.parameter_for('/api/v1/users/{user_id}/groups', 'user_id')['key'] == 'user'
    ctx.request = Mock(side_effect=[{'id': 'totp-user', 'token': 't'}, {'secret': 'S'}, {'totp_enabled': False}])
    with pytest.raises(RuntimeError, match='did not take effect'):
        seeds.seed_totp_user(ctx, parameter)


def test_task_seeder_rejects_an_id_that_has_already_finished(monkeypatch):
    monkeypatch.setattr(seeds.time, 'sleep', lambda seconds: None)
    ctx = seeds.SeedContext(Mock(), Mock(), seeds.SURFACE, token='fixture')
    ctx.values['chat'] = 'chat-id'
    ctx.values['model'] = 'stub-model'
    ctx.request = Mock(
        side_effect=[
            {'id': 'attack_task_fixture'},
            {'is_active': True},
            {'id': 'attack_task_fixture'},
            {'id': 'attack_task_fixture', 'access_grants': [PUBLIC_MODEL_READ]},
            {'data': [{'id': 'attack_task_fixture'}]},
            {'task_ids': ['expired-task']},
            {'task_ids': []},
        ]
    )
    with pytest.raises(RuntimeError, match='Task completed'):
        seeds.seed_task(ctx, {'key': 'task', 'content': 'fixture code'})
    ctx.admin.request.assert_called_once_with('POST', '/api/tasks/stop/expired-task')


def test_task_seeder_observes_a_live_task_after_registering_its_model_and_filter(monkeypatch):
    monkeypatch.setattr(seeds.time, 'sleep', lambda seconds: None)
    ctx = seeds.SeedContext(Mock(), Mock(), seeds.SURFACE, token='fixture')
    ctx.values['chat'] = 'chat-id'
    ctx.values['model'] = 'stub-model'
    ctx.request = Mock(
        side_effect=[
            {'id': 'attack_task_fixture'},
            {'is_active': True},
            {'id': 'attack_task_fixture'},
            {'id': 'attack_task_fixture', 'access_grants': [PUBLIC_MODEL_READ]},
            {'data': [{'id': 'attack_task_fixture'}]},
            {'task_ids': ['live-task']},
            *[{'task_ids': ['live-task']}] * 3,
        ]
    )
    assert seeds.seed_task(ctx, {'key': 'task', 'content': 'fixture code'}) == 'live-task'
    model = ctx.request.call_args_list[2]
    assert model.args == ('POST', '/api/v1/models/create')
    assert model.kwargs['json']['base_model_id'] == 'stub-model'
    assert model.kwargs['json']['meta'] == {'filterIds': ['attack_task_fixture']}
    registration = ctx.request.call_args_list[3]
    assert registration.args == ('POST', '/api/v1/models/model/access/update')
    assert registration.kwargs['json'] == {'id': 'attack_task_fixture', 'access_grants': [PUBLIC_MODEL_READ]}
    assert registration.kwargs['actor'] is ctx.admin
    completion = ctx.request.call_args_list[5]
    assert completion.kwargs['json']['chat_id'] == 'chat-id'
    assert completion.kwargs['json']['model'] == 'attack_task_fixture'
    assert completion.kwargs['actor'] is ctx.admin
    assert all(call.kwargs['actor'] is ctx.admin for call in ctx.request.call_args_list[4:])


def test_task_filter_wait_is_bounded_and_cancellable(monkeypatch):
    parameter = next(p for p in seeds.SURFACE['parameter'] if p['key'] == 'task')
    namespace = {}
    # nosec B102 - executes the filter body this repo's own attack-surface.toml declares,
    # to prove the wait is bounded without standing up a pipeline server. No external input.
    exec(parameter['content'], namespace)  # nosec B102

    async def exercise():
        entered = asyncio.Event()

        async def wait(seconds):
            assert seconds == 3600
            entered.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(namespace['asyncio'], 'sleep', wait)
        task = asyncio.create_task(namespace['Filter']().inlet({'model': 'fixture'}))
        await entered.wait()
        assert not task.done()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())


def stop_seed_task(admin, resolved):
    task_id = resolved['/api/tasks/stop/{task_id}', 'task_id']
    result = admin.request('POST', f'/api/tasks/stop/{task_id}')
    assert result.status_code == 200, f'Could not cancel seeded task: HTTP {result.status_code}'


PUBLIC_MODEL_READ = {'principal_type': 'user', 'principal_id': '*', 'permission': 'read'}


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
        'MODEL_WHITELIST': [],
        'BYPASS_ADMIN_ACCESS_CONTROL': False,
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
        assert await scope['get_filtered_models']([model], user) == ([model] if role == 'admin' else [])
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


def test_model_seed_registers_the_upstream_selector_on_each_pass():
    ctx = seeds.SeedContext(Mock(), Mock(), seeds.SURFACE)
    ctx.values['openai_index'] = '0'
    ctx.request = Mock(
        side_effect=[
            {'data': [{'id': 'stub-model'}]},
            {'id': 'stub-model', 'access_grants': [PUBLIC_MODEL_READ]},
        ]
        * 2
    )
    for _ in range(2):
        assert seeds.seed_model(ctx, {'key': 'model'}) == 'stub-model'
    for discovery, registration in zip(ctx.request.call_args_list[::2], ctx.request.call_args_list[1::2]):
        assert discovery.args == ('GET', '/openai/models/0')
        assert registration.args == ('POST', '/api/v1/models/model/access/update')
        assert registration.kwargs['actor'] is ctx.admin
        assert registration.kwargs['json'] == {'id': 'stub-model', 'access_grants': [PUBLIC_MODEL_READ]}


@pytest.mark.parametrize(
    'body',
    [
        {'id': 'stub-model', 'access_grants': []},
        {'id': 'stub-model', 'access_grants': [{**PUBLIC_MODEL_READ, 'permission': 'write'}]},
        {'id': 'different-model', 'access_grants': [PUBLIC_MODEL_READ]},
    ],
)
def test_model_registration_rejects_missing_read_access_or_wrong_model(body):
    ctx = seeds.SeedContext(Mock(), Mock(), seeds.SURFACE)
    ctx.request = Mock(return_value=body)
    with pytest.raises(RuntimeError, match='did not acquire public read access'):
        seeds.register_readable_model(ctx, 'stub-model')


needs_stack = pytest.mark.skipif(not os.getenv('ATTACK_BASE_URL'), reason='requires the running CI stack')


@pytest.fixture(scope='module')
def live_seeds():
    from .identities import ensure_identities

    identities = ensure_identities()
    resolved = None
    try:
        resolved = seeds.resolve_parameters(identities.user, admin=identities.admin)
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
@pytest.mark.parametrize('role', ['admin', 'user'])
def test_live_seeded_models_are_visible_and_stub_completes_for_both_identities(live_seeds, role):
    identities, resolved = live_seeds
    client = getattr(identities, role)
    # Look the value up by a path the spec actually contains. The surface entry's
    # `prefix` is a longest-prefix matcher, not a route: resolve_parameters keys its
    # result by real path templates, so using a prefix here raises KeyError.
    model_id = resolved['/api/v1/analytics/models/{model_id}/chats', 'model_id']
    registry = client.request('GET', '/api/models', params={'refresh': True})
    assert registry.status_code == 200, registry.text
    ids = {model['id'] for model in registry.json()['data']}
    assert model_id in ids
    assert any(model.startswith('attack_task_') for model in ids)
    result = client.request(
        'POST',
        '/api/chat/completions',
        json={
            'model': model_id,
            'messages': [{'role': 'user', 'content': 'Verify the CI model fixture.'}],
            'stream': False,
        },
    )
    assert result.status_code == 200, result.text
    assert result.json()['choices'][0]['message']['content'] == 'stub response'


@needs_stack
def test_live_second_pass_uses_fresh_resource_fixtures(live_seeds):
    identities, first = live_seeds
    second = seeds.resolve_parameters(identities.user, admin=identities.admin)
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


def test_stack_configuration_is_imported_and_verified():
    client = Mock(spec=seeds.AttackClient)
    client.request.return_value = response(200, {'ui.enable_signup': True, 'other.key': 1})
    surface = {**tiny_surface(), 'stack_configuration': {'ui.enable_signup': True}}
    assert seeds.resolve_parameters(client, surface=surface, spec={'paths': {}}) == {}
    call = client.request.call_args
    assert call.args == ('POST', '/api/v1/configs/import')
    assert call.kwargs['json'] == {'config': {'ui.enable_signup': True}}


def test_stack_configuration_that_does_not_take_effect_fails_setup():
    client = Mock(spec=seeds.AttackClient)
    client.request.return_value = response(200, {'ui.enable_signup': False})
    surface = {**tiny_surface(), 'stack_configuration': {'ui.enable_signup': True}}
    with pytest.raises(RuntimeError, match='did not take effect'):
        seeds.resolve_parameters(client, surface=surface, spec={'paths': {}})


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
    seeded = {p['key'] for p in seeds.SURFACE['parameter'] if 'unseedable' not in p}
    seeded |= {name for p in seeds.SURFACE['parameter'] for name in p.get('provides', [])}
    writable = writable_string_fields(SPEC)
    skeletons = body_skeletons(SPEC)
    assert len({entry['route'] for entry in seeds.SURFACE['field']}) == len(seeds.SURFACE['field'])
    for entry in seeds.SURFACE['field']:
        route = entry['route']
        assert route in operations, route
        assert entry['description'].strip(), route
        assert entry.keys() & {'json', 'params', 'files', 'form'}, route
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
            *_strings(entry.get('json', {})),
            *_strings(entry.get('params', {})),
            *_strings(entry.get('setup', [])),
        ]
        values += [*_strings(entry.get('form', {})), *_strings(entry.get('files', []))]
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
