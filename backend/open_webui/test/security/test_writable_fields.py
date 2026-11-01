"""Derive from the committed plane input; never generate a fresh schema here."""

import json
import tomllib
from collections import Counter
from pathlib import Path

import pytest
from openapi_surface import query_parameters, writable_string_fields

REPO = Path(__file__).resolve().parents[4]
WAIVERS = REPO / 'security/derivation-coverage.toml'
# Five methods give 529 operations. Audit bodies across all
# OpenAPI methods (533 including HEAD/OPTIONS), because writable_string_fields
# excludes GET/HEAD/OPTIONS bodies while query_parameters includes read methods.
# TRACE is valid but absent today.
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
    assert (len(five_method_routes), len(operations)) == (529, 533), (
        f'Expected 529 five-method operations / 533 total including HEAD and OPTIONS; '
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


def test_derived_counts(surface):
    _, fields, queries = surface
    body_counts = (sum(bool(paths) for paths in fields.values()), sum(map(len, fields.values())))
    query_counts = (sum(bool(names) for names in queries.values()), sum(map(len, queries.values())))
    assert body_counts == (
        195,
        2152,
    ), f'Expected 195 routes / 2152 writable string fields; measured {body_counts[0]} / {body_counts[1]}.'
    # TaskTurnRequest validates language; message_id only feeds the retry hash.
    # image_prompt refuses platform mode before consuming either field.
    for task in ('auto', 'emoji', 'follow_up', 'image_prompt', 'moa', 'queries', 'tags', 'title'):
        assert {'language', 'message_id'} <= set(fields[f'POST /api/v1/tasks/{task}/completions'])
    # Membership edits remain in the attack surface even though the handler refuses them.
    assert 'user_ids[]' in fields['POST /api/v1/groups/id/{id}/update']
    assert 'POST /api/v1/threads/{thread_id}/context:import' not in fields
    assert fields['POST /api/v1/threads:authorize'] == [
        'context_epoch',
        'threads[].generation',
        'threads[].thread_id',
        'threads[].versions[]',
    ]
    assert query_counts == (
        48,
        97,
    ), f'Expected 48 routes / 97 string query parameters; measured {query_counts[0]} / {query_counts[1]}.'


def test_derivation_cannot_silently_collapse(surface):
    _, fields, queries = surface
    # External knowledge retired 7 writable routes and 37 fields; keep the guard margin.
    routes, strings = sum(bool(paths) for paths in fields.values()), sum(map(len, fields.values()))
    assert routes >= 225 - 17 - 11 - 7 and strings >= 2400 - 403 - 30 - 37, (
        f'Derivation collapsed: {routes} routes / {strings} writable string fields; '
        'Original floor 225 / 2400, less the retired 35 routes / 470 fields. '
    )
    routes, strings = sum(bool(names) for names in queries.values()), sum(map(len, queries.values()))
    assert routes >= 40 and strings >= 90 - 1, (
        f'Query derivation collapsed: {routes} routes / {strings} string query parameters; '
        'original floor 40 / 90, less the retired source query parameter.'
    )


def test_blind_body_counts(surface):
    operations, fields, _ = surface
    blind = {route: op for route, op in operations.items() if 'requestBody' in op and not fields.get(route)}
    media = Counter(kind for op in blind.values() for kind in op['requestBody'].get('content', {}))
    assert len(blind) == 20 and media == {'application/json': 16, 'multipart/form-data': 4}, (
        'Expected 20 blind body operations (16 JSON: 15 write + 1 read; 4 multipart); '
        f'measured {len(blind)}: {dict(media)}.'
    )
    read_bodies = {route for route in blind if route.split(' ', 1)[0] in {'GET', 'HEAD', 'OPTIONS'}}
    assert read_bodies == {'GET /ollama/api/ps'}, (
        f'Expected 1 blind JSON read body, GET /ollama/api/ps, alongside 15 JSON write bodies; '
        f'measured read bodies: {sorted(read_bodies)}.'
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
        'Expected exact waivers for 20 blind operations (15 JSON write + 1 JSON read + 4 multipart); '
        f'measured {len(blind)} blind.'
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
