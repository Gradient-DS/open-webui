"""Derive from the committed plane input; never generate a fresh schema here."""

import json
import tomllib
from collections import Counter
from pathlib import Path

import pytest
from openapi_surface import query_parameters, writable_string_fields

REPO = Path(__file__).resolve().parents[4]
WAIVERS = REPO / 'security/derivation-coverage.toml'
# The product exposes 300 five-method operations and 303 across all HTTP methods.
# Derive all methods; GET/HEAD/OPTIONS bodies are deliberately excluded from string writes.
FIVE_METHODS = {'get', 'post', 'put', 'patch', 'delete'}
OPENAPI_METHODS = FIVE_METHODS | {'head', 'options', 'trace'}


@pytest.fixture(scope='module')
def surface():
    spec = json.loads((REPO / 'security/openapi.json').read_text(encoding='utf-8'))
    operations = {
        f'{method.upper()} {path}': operation
        for path, item in spec['paths'].items()
        for method, operation in item.items()
        if method in OPENAPI_METHODS
    }
    return operations, writable_string_fields(spec), query_parameters(spec)


def test_operation_scope(surface):
    operations, _, _ = surface
    five_method_routes = [route for route in operations if route.split(' ', 1)[0].lower() in FIVE_METHODS]
    assert (len(five_method_routes), len(operations)) == (300, 303), (
        f'Expected 300 five-method operations / 303 total including HEAD and OPTIONS; '
        f'measured {len(five_method_routes)} / {len(operations)}.'
    )


@pytest.mark.parametrize('path', ['/api/v1/chats/{id}/share', '/api/v1/chats/shared/{id}/access'])
def test_chat_share_access_has_no_writable_fields(surface, path):
    operations, fields, queries = surface
    route = f'GET {path}'
    assert 'requestBody' not in operations[route]
    assert not fields.get(route)
    assert not queries.get(route)
    assert [(parameter['in'], parameter['name']) for parameter in operations[route]['parameters']] == [('path', 'id')]


def test_conversation_read_has_no_client_selected_user_or_timestamp(surface):
    operations, fields, queries = surface
    route = 'POST /api/v1/threads/{thread_id}/read'
    assert 'requestBody' not in operations[route]
    assert not fields.get(route) and not queries.get(route)
    assert [(p['in'], p['name']) for p in operations[route]['parameters']] == [('path', 'thread_id')]
    assert operations[route]['security'] == [{'HTTPBearer': []}]


def test_derived_counts(surface):
    operations, fields, queries = surface
    body_counts = (sum(bool(paths) for paths in fields.values()), sum(map(len, fields.values())))
    query_counts = (sum(bool(names) for names in queries.values()), sum(map(len, queries.values())))
    assert body_counts == (94, 936), (
        f'Expected 94 routes / 936 writable string fields; measured {body_counts[0]} / {body_counts[1]}.'
    )
    # TaskTurnRequest validates language; message_id only feeds the retry hash.
    for task in ('auto', 'emoji', 'follow_up', 'moa', 'queries', 'tags', 'title'):
        assert {'language', 'message_id'} <= set(fields[f'POST /api/v1/tasks/{task}/completions'])
    # Membership edits remain in the attack surface even though the handler refuses them.
    assert 'POST /api/v1/groups/id/{id}/update' not in operations
    assert 'POST /api/v1/threads/{thread_id}/context:import' not in fields
    assert fields['POST /api/v1/threads:authorize'] == [
        'context_epoch',
        'threads[].generation',
        'threads[].thread_id',
        'threads[].versions[]',
    ]
    assert query_counts == (35, 77), (
        f'Expected 35 routes / 77 string query parameters; measured {query_counts[0]} / {query_counts[1]}.'
    )


def test_derivation_cannot_silently_collapse(surface):
    _, fields, queries = surface
    # Initial committed surface: 139 body routes / 1846 fields, with floor margins 10 / 231.
    # Product retirement removed exactly 45 body routes / 910 fields; retain those margins.
    routes, strings = sum(bool(paths) for paths in fields.values()), sum(map(len, fields.values()))
    assert routes >= 139 - 45 - 10 and strings >= 1846 - 910 - 231, (
        f'Derivation collapsed: {routes} routes / {strings} writable string fields'
    )
    # Query retirement: 1 routes / 1 fields; original margins remain 9 / 10.
    routes, strings = sum(bool(names) for names in queries.values()), sum(map(len, queries.values()))
    assert routes >= 36 - 1 - 9 and strings >= 78 - 1 - 10, (
        f'Query derivation collapsed: {routes} routes / {strings} string query parameters'
    )


def test_blind_body_counts(surface):
    operations, fields, _ = surface
    blind = {route: op for route, op in operations.items() if 'requestBody' in op and not fields.get(route)}
    media = Counter(kind for op in blind.values() for kind in op['requestBody'].get('content', {}))
    assert len(blind) == 7 and media == {'multipart/form-data': 2, 'application/json': 5}, (
        f'Expected 7 blind body operations (5 JSON writes; 2 multipart); measured {len(blind)}: {dict(media)}.'
    )
    read_bodies = {route for route in blind if route.split(' ', 1)[0] in {'GET', 'HEAD', 'OPTIONS'}}
    assert read_bodies == set(), (
        f'Expected no blind read bodies alongside 5 JSON write bodies; measured read bodies: {sorted(read_bodies)}.'
    )


def test_blind_bodies_are_exactly_the_reasoned_waivers(surface):
    operations, fields, _ = surface
    blind = {route for route, op in operations.items() if 'requestBody' in op and not fields.get(route)}
    # An absent file means no waivers: the first failure prints the actual work list.
    entries = tomllib.loads(WAIVERS.read_text(encoding='utf-8')).get('waived', []) if WAIVERS.exists() else []
    waived = set()
    for entry in entries:
        route = entry['id']
        assert route not in waived, f'Duplicate derivation waiver: {route}'
        reason = entry.get('reason')
        assert isinstance(reason, str) and reason.strip(), f'Derivation waiver needs a reason: {route}'
        waived.add(route)
    missing, stale = blind - waived, waived - blind
    lines = [
        f'Expected exact waivers for 7 blind operations (5 JSON writes + 2 multipart); measured {len(blind)} blind.'
    ]
    lines.append('Blind operations without waivers:')
    for route in sorted(missing):
        media = ', '.join(sorted(operations[route]['requestBody'].get('content', {})))
        lines.append(f'  {route} [{media}]')
    lines.append('Stale waivers (operation derives fields, has no body, or no longer exists):')
    lines.extend(f'  {route}' for route in sorted(stale))
    assert not missing and not stale, '\n'.join(lines)


def test_conversation_versions_is_a_bodyless_owner_read(surface):
    operations, fields, queries = surface
    route = 'GET /api/v1/threads/{thread_id}/versions'
    assert 'requestBody' not in operations[route]
    assert not fields.get(route) and not queries.get(route)
    assert [(p['in'], p['name']) for p in operations[route]['parameters']] == [('path', 'thread_id')]
    spec = json.loads((REPO / 'security/openapi.json').read_text())
    assert spec['components']['schemas']['open_webui__soev__thread_routes__ForkForm']['properties']['version'] == {
        'type': 'boolean',
        'title': 'Version',
        'default': False,
    }


def test_current_projection_parameter_and_fork_reason_are_classified(surface):
    operations, fields, queries = surface
    for route in ('GET /api/v1/threads/{thread_id}', 'GET /api/v1/chats/{id}'):
        parameter = next(p for p in operations[route]['parameters'] if p['name'] == 'current')
        assert parameter['in'] == 'query'
        assert parameter['schema']['type'] == 'boolean' and parameter['schema']['default'] is False
        assert 'current' not in queries.get(route, [])
    assert fields['POST /api/v1/threads/{thread_id}/fork'] == ['kind']
    spec = json.loads((REPO / 'security/openapi.json').read_text())
    schema = spec['components']['schemas']['open_webui__soev__thread_routes__ForkForm']
    assert schema['then'] == {'required': ['kind'], 'properties': {'kind': {'enum': ['regenerate', 'edit']}}}
    assert schema['else'] == {'not': {'required': ['kind']}}


def test_fanout_flags_are_read_query_selectors():
    spec = json.loads((REPO / 'security/openapi.json').read_text())
    for path, name in [
        ('/api/v1/chats/', 'include_sidebar'),
        ('/api/v1/chats/list', 'include_sidebar'),
        ('/api/v1/chats/{id}', 'include_versions'),
        ('/api/v1/knowledge/{id}/files', 'include_context'),
    ]:
        operation = spec['paths'][path]['get']
        assert 'requestBody' not in operation
        flag = next(parameter for parameter in operation['parameters'] if parameter['name'] == name)
        assert flag['in'] == 'query' and flag['schema']['type'] == 'boolean'


def test_bug_report_schema_forbids_extra_content_and_bounds_every_context_field(surface):
    operations, _, _ = surface
    assert 'POST /api/v1/feedback/report' in operations
    spec = json.loads((REPO / 'security/openapi.json').read_text())
    schemas = spec['components']['schemas']
    form, context = schemas['FeedbackReportForm'], schemas['FeedbackReportContext']
    assert form['additionalProperties'] is False
    assert context['additionalProperties'] is False
    assert set(form['properties']) == {'category', 'description', 'context'}
    assert set(context['properties']) == {
        'route',
        'app_version',
        'user_agent',
        'error_message',
        'error_detail',
        'model',
        'chat_id',
        'trace_id',
    }
    assert all(any(part.get('maxLength', 0) > 0 for part in field['anyOf']) for field in context['properties'].values())


def test_shared_mailbox_surface_keeps_all_methods_and_the_address_field(surface):
    operations, fields, queries = surface
    path = '/api/v1/cloud-sync/connections/{connection_id}/mailboxes'
    for method in ('GET', 'POST', 'DELETE'):
        route = f'{method} {path}' + ('/{address}' if method == 'DELETE' else '')
        assert route in operations
        assert operations[route]['security'] == [{'HTTPBearer': []}]
        assert not queries.get(route)
    assert fields[f'POST {path}'] == ['address']
    assert not fields.get(f'GET {path}')
    assert not fields.get(f'DELETE {path}/{{address}}')
