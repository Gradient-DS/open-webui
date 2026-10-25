"""Derive from the committed plane input; never generate a fresh schema here."""

import json
import tomllib
from collections import Counter
from pathlib import Path

import pytest
from openapi_surface import query_parameters, writable_string_fields

REPO = Path(__file__).resolve().parents[4]
WAIVERS = REPO / 'security/derivation-coverage.toml'
# Five methods give 536 operations. Audit bodies across all
# OpenAPI methods (540 including HEAD/OPTIONS), because writable_string_fields
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
    assert (len(five_method_routes), len(operations)) == (536, 540), (
        f'Expected 536 five-method operations / 540 total including HEAD and OPTIONS; '
        f'measured {len(five_method_routes)} / {len(operations)}.'
    )


def test_derived_counts(surface):
    _, fields, queries = surface
    body_counts = (sum(bool(paths) for paths in fields.values()), sum(map(len, fields.values())))
    query_counts = (sum(bool(names) for names in queries.values()), sum(map(len, queries.values())))
    assert body_counts == (
        196,
        2193,
    ), f'Expected 196 routes / 2193 writable string fields; measured {body_counts[0]} / {body_counts[1]}.'
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
    assert len(blind) == 21 and media == {'application/json': 17, 'multipart/form-data': 4}, (
        'Expected 21 blind body operations (17 JSON: 16 write + 1 read; 4 multipart); '
        f'measured {len(blind)}: {dict(media)}.'
    )
    read_bodies = {route for route in blind if route.split(' ', 1)[0] in {'GET', 'HEAD', 'OPTIONS'}}
    assert read_bodies == {'GET /ollama/api/ps'}, (
        f'Expected 1 blind JSON read body, GET /ollama/api/ps, alongside 16 JSON write bodies; '
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
        'Expected exact waivers for 21 blind operations (16 JSON write + 1 JSON read + 4 multipart); '
        f'measured {len(blind)} blind.'
    ]
    lines.append('Blind operations without waivers:')
    for route in sorted(missing):
        media = ', '.join(sorted(operations[route]['requestBody'].get('content', {})))
        lines.append(f'  {route} [{media}]')
    lines.append('Stale waivers (operation derives fields, has no body, or no longer exists):')
    lines.extend(f'  {route}' for route in sorted(stale))
    assert not missing and not stale, '\n'.join(lines)
