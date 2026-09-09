"""Derive from the committed plane input; never generate a fresh schema here."""

import json
import tomllib
from collections import Counter
from pathlib import Path

import pytest
from openapi_surface import query_parameters, writable_string_fields

REPO = Path(__file__).resolve().parents[4]
WAIVERS = REPO / 'security/derivation-coverage.toml'
# Five methods give the historical 627-operation count. Audit bodies across all
# OpenAPI methods (630 including HEAD/OPTIONS), because writable_string_fields
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
    assert (len(five_method_routes), len(operations)) == (627, 630), (
        f'Expected 627 five-method operations / 630 total including HEAD and OPTIONS; '
        f'measured {len(five_method_routes)} / {len(operations)}.'
    )


def test_derived_counts(surface):
    _, fields, queries = surface
    body_counts = (sum(bool(paths) for paths in fields.values()), sum(map(len, fields.values())))
    query_counts = (sum(bool(names) for names in queries.values()), sum(map(len, queries.values())))
    assert body_counts == (
        240,
        2591,
    ), f'Expected 240 routes / 2591 writable string fields; measured {body_counts[0]} / {body_counts[1]}.'
    assert query_counts == (
        47,
        94,
    ), f'Expected 47 routes / 94 string query parameters; measured {query_counts[0]} / {query_counts[1]}.'


def test_derivation_cannot_silently_collapse(surface):
    _, fields, queries = surface
    # Keep independent floors even when a future schema change updates exact counts.
    routes, strings = sum(bool(paths) for paths in fields.values()), sum(map(len, fields.values()))
    assert routes >= 225 and strings >= 2400, (
        f'Derivation collapsed: {routes} routes / {strings} writable string fields; '
        'floor 225 / 2400, baseline 230 / 2525. A partial or empty derivation cannot claim coverage.'
    )
    routes, strings = sum(bool(names) for names in queries.values()), sum(map(len, queries.values()))
    assert routes >= 40 and strings >= 90, (
        f'Query derivation collapsed: {routes} routes / {strings} string query parameters; '
        'floor 40 / 90, baseline 47 / 94.'
    )


def test_blind_body_counts(surface):
    operations, fields, _ = surface
    blind = {route: op for route, op in operations.items() if 'requestBody' in op and not fields.get(route)}
    media = Counter(kind for op in blind.values() for kind in op['requestBody'].get('content', {}))
    assert len(blind) == 25 and media == {'application/json': 18, 'multipart/form-data': 7}, (
        'Expected 25 blind body operations (18 JSON: 17 write + 1 read; 7 multipart); '
        f'measured {len(blind)}: {dict(media)}.'
    )
    read_bodies = {route for route in blind if route.split(' ', 1)[0] in {'GET', 'HEAD', 'OPTIONS'}}
    assert read_bodies == {'GET /ollama/api/ps'}, (
        f'Expected 1 blind JSON read body, GET /ollama/api/ps, alongside 17 JSON write bodies; '
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
        'Expected exact waivers for 25 blind operations (17 JSON write + 1 JSON read + 7 multipart); '
        f'measured {len(blind)} blind.'
    ]
    lines.append('Blind operations without waivers:')
    for route in sorted(missing):
        media = ', '.join(sorted(operations[route]['requestBody'].get('content', {})))
        lines.append(f'  {route} [{media}]')
    lines.append('Stale waivers (operation derives fields, has no body, or no longer exists):')
    lines.extend(f'  {route}' for route in sorted(stale))
    assert not missing and not stale, '\n'.join(lines)
