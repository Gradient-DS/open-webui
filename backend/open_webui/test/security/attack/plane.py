"""Plant writable fields, then drive every OpenAPI operation over bearer HTTP.

RUN-TAG: owui-phase7a-plane-core

The committed document has 631 operations, 240 writable routes and 2,591
derived string fields. At 63 payloads, all-fields alone costs 15,120 requests;
unconditional leave-one-out costs another 163,233 before single-field retries.
Default PR mode therefore samples ONE payload per field using SHA-256 of the
route id and field path. This is stable across processes and field traversal
order. ATTACK_FULL_CORPUS=1 selects every payload for every field for nightly
and manual runs. Both modes visit the same routes and fields: coverage measures
routes actually driven and handlers entered, never a payload-count threshold.
Sampling reduces content breadth; it cannot change what the coverage gate proves.

Three seeding shapes run: all-fields; individual fields after rejection; and
N leave-one-out requests unconditionally, even after 2xx and for narrow routes.
AIRE returned 201 while silently dropping every seeded entry because a sibling
field vetoed it. A success-conditioned fallback cannot discover that failure.
Union leaves shadowed by a structured sibling also get individual requests,
even after 2xx, so those derived scalar alternatives are actually planted.

Each pass owns fresh status and handler-entry histograms. The artefact includes
their separate evidence as well as union hits for a later route-coverage gate;
one pass's hits cannot satisfy another pass's assertions. Only returned HTTP
responses are recorded, under the METHOD /template actually driven. Missing
seedable parameters raise. Explicitly unseedable parameters are reported, never
replaced by invented IDs, and stay unentered. A 404 never counts as entry.

The core live passes use the admin as both fixture owner and driver, preserving
ownership across the seeding surface and reaching admin dependencies without a
second route-role inventory. Ordinary-user authorization belongs to Phase 7b.
Destructive operations from that surface run last, each request with a fresh
throwaway admin bearer; even revoking or changing it cannot poison the next one.
No limit resets, Flask session handling, or CSRF retries are introduced here.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from contextlib import nullcontext
from dataclasses import dataclass, field
from urllib.parse import quote
from uuid import uuid4

import requests
from hostile_corpus import REFLECTION_PROBE, fetch_payloads
from openapi_surface import writable_string_fields

from . import client as transport
from .configuration import preserve_configuration
from .hits_path import hits_path
from .identities import PASSWORD
from .seeds import DESTRUCTIVE, METHODS, SPEC, parameter_for


@dataclass(frozen=True)
class Outcome:
    status: int
    entered: bool
    body: str
    reflected: bool = False


@dataclass
class Seeding:
    """One pass's evidence; accepted counts HTTP 2xx, not proven persistence."""

    expected: set[str] = field(default_factory=set)
    accepted: dict[str, int] = field(default_factory=dict)
    statuses: dict[str, dict[int, int]] = field(default_factory=dict)
    entered: dict[str, dict[int, int]] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)
    crashes: dict[str, str] = field(default_factory=dict)
    config_findings: list[dict] = field(default_factory=list)
    body_failures: list[dict] = field(default_factory=list)
    config_restore_verified: bool = False

    @property
    def unentered(self):
        return {
            route: next(iter(self.statuses.get(route, {})), None)
            for route in sorted(self.expected - self.entered.keys())
        }


_PASSES: dict[str, Seeding] = {}


def _count(histogram, route, status):
    counts = histogram.setdefault(route, {})
    counts[status] = counts.get(status, 0) + 1


def record(route_id, status, body, *, pass_name='manual') -> bool:
    """Record a response, delegating entry classification to the client.

    Synthetic callers can pass JSON data or response text. Live callers pass
    the original Response as body to retain the client's bearer-probe evidence.
    Classification always sees the complete body, before diagnostic truncation.
    """
    if isinstance(body, requests.Response):
        response = body
        if response.status_code != status:
            raise ValueError('Recorded status differs from the driven response')
    else:
        response = requests.Response()
        response.status_code = status
        response._content = (body if isinstance(body, str) else json.dumps(body)).encode()
    entered = transport.entered_the_handler(response)
    tally = _PASSES.setdefault(pass_name, Seeding())
    tally.expected.add(route_id)
    _count(tally.statuses, route_id, status)
    if entered:
        _count(tally.entered, route_id, status)
    if 200 <= status < 300:
        tally.accepted[route_id] = tally.accepted.get(route_id, 0) + 1
    if route_id == 'POST /api/v1/integrations/ingest' and 200 <= status < 300:
        payload = transport.json_body(response)
        if isinstance(payload, dict) and isinstance(payload.get('errors'), int) and payload['errors'] > 0:
            tally.body_failures.append({'finding': 'PLANE-002', 'route': route_id, 'status': status, 'body': payload})
    if status >= 500:
        tally.crashes.setdefault(route_id, response.text[:2000])
    return entered


def unentered_routes(pass_name=None):
    if pass_name is not None:
        return _PASSES[pass_name].unentered
    expected = set().union(*(tally.expected for tally in _PASSES.values()))
    entered = set().union(*(tally.entered.keys() for tally in _PASSES.values()))
    return {
        route: next((next(iter(tally.statuses[route])) for tally in _PASSES.values() if route in tally.statuses), None)
        for route in sorted(expected - entered)
    }


def flush_hits():
    """Persist observed statuses and pass-local evidence, including partial runs."""
    path = hits_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    passes = {
        name: {
            'expected': sorted(tally.expected),
            'hits': sorted(tally.entered),
            'statuses': tally.statuses,
            'entered': tally.entered,
            'accepted': tally.accepted,
            'unentered': tally.unentered,
            'skipped': tally.skipped,
            'crashes': tally.crashes,
            'body_failures': tally.body_failures,
            'config_findings': tally.config_findings,
            'config_keys': sorted({item['key'] for item in tally.config_findings}),
            'corpus_config_keys': sorted({item['key'] for item in tally.config_findings if item['corpus']}),
            'config_restore_verified': tally.config_restore_verified,
        }
        for name, tally in _PASSES.items()
    }
    data = {
        'hits': sorted(set().union(*(tally.entered.keys() for tally in _PASSES.values()))),
        'passes': passes,
    }
    temporary = path.with_name(f'{path.name}.{uuid4().hex}.tmp')
    try:
        with open(temporary, 'x', opener=lambda name, flags: os.open(name, flags, 0o600)) as stream:
            stream.write(json.dumps(data, indent=2, sort_keys=True) + '\n')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _tokens(dotted):
    tokens = []
    for segment in dotted.split('.'):
        key = segment.removesuffix('[]')
        if key:
            tokens.append(key)
        if segment.endswith('[]'):
            tokens.append(0)
    return tokens


def _plant(node, tokens, payload):
    if not tokens:
        return node if isinstance(node, (dict, list)) else payload
    key, *rest = tokens
    if key == 0:
        if not isinstance(node, list):
            node = [None]
    elif not isinstance(node, dict):
        node = {}
    existing = node[0] if key == 0 else node.get(key)
    node[key] = _plant(existing, rest, payload)
    return node


def _body_for(fields, payload):
    body = None
    for name in sorted(fields, key=lambda name: (-len(_tokens(name)), name)):
        value = payload[name] if isinstance(payload, dict) else payload
        body = _plant(body, _tokens(name), value)
    return body if body is not None else {}


def _contains(body, name, payload):
    try:
        for token in _tokens(name):
            body = body[token]
        return body == payload
    except (KeyError, IndexError, TypeError):
        return False


def _payload_batches(route_id, fields, payloads, *, full):
    if not payloads:
        raise ValueError('Cannot seed with an empty corpus')
    if full:
        for payload in payloads:
            yield dict.fromkeys(fields, payload)
    else:
        yield {
            name: payloads[
                int.from_bytes(hashlib.sha256(f'{route_id}\0{name}'.encode()).digest(), 'big') % len(payloads)
            ]
            for name in fields
        }


def _read_before_destroy(route_id):
    method = route_id.split(' ', 1)[0]
    group = 0 if method in {'GET', 'HEAD', 'OPTIONS', 'TRACE'} else 2 if method == 'DELETE' else 1
    return (3 if route_id in DESTRUCTIVE else group, route_id)


def operations(spec=SPEC):
    return sorted(
        (f'{method.upper()} {path}' for path, item in spec['paths'].items() for method in item if method in METHODS),
        key=_read_before_destroy,
    )


class UnseedableRoute(ValueError):
    pass


def _fill(path, parameters):
    def substitute(match):
        name = match.group(1)
        declaration = parameter_for(path, name)
        if 'unseedable' in declaration:
            raise UnseedableRoute(declaration['unseedable'])
        value = parameters[path, name]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f'Invalid resolved parameter: {path}: {name}')
        return quote(value, safe='')

    return re.sub(r'\{([^}]+)\}', substitute, path)


def _throwaway(admin):
    email = f'attack-plane-throwaway-{uuid4().hex}@example.com'
    response = admin.request(
        'POST',
        '/api/v1/auths/add',
        json={'email': email, 'password': PASSWORD, 'name': 'Attack throwaway', 'role': 'admin'},
    )
    throwaway = transport.AttackClient(admin.base_url)
    try:
        return throwaway.authenticate(response, email, role='admin')
    except Exception:
        throwaway.close()
        raise
    finally:
        response.close()


def _drive_one(client, route_id, filled, pass_name, **kwargs):
    tally = _PASSES.setdefault(pass_name, Seeding())
    guard = (
        preserve_configuration(client, route=route_id, report=tally.config_findings.append)
        if route_id.split(' ', 1)[0] not in {'GET', 'HEAD', 'OPTIONS', 'TRACE'}
        else nullcontext()
    )
    with guard:
        return _drive_response(client, route_id, filled, pass_name, **kwargs)


def _drive_response(client, route_id, filled, pass_name, **kwargs):
    method, _ = route_id.split(' ', 1)
    actor = _throwaway(client) if route_id in DESTRUCTIVE else client
    try:
        response = actor.request(method, filled, **kwargs)
        try:
            entered = record(route_id, response.status_code, response, pass_name=pass_name)
            return Outcome(
                response.status_code,
                entered,
                response.text[:2000],
                'text/html' in response.headers.get('Content-Type', '').lower() and REFLECTION_PROBE in response.text,
            )
        finally:
            response.close()
    finally:
        if actor is not client:
            actor.close()


def _target(route_id, parameters, tally):
    try:
        return _fill(route_id.split(' ', 1)[1], parameters)
    except UnseedableRoute as error:
        tally.skipped[route_id] = str(error)
        return None


def _seed_route(client, route_id, filled, fields, payloads, full):
    for batch in _payload_batches(route_id, fields, payloads, full=full):
        body = _body_for(fields, batch)
        outcome = _drive_one(client, route_id, filled, 'seeding', json=body)
        for name in fields:
            if (outcome.status >= 400 and len(fields) > 1) or not _contains(body, name, batch[name]):
                _drive_one(client, route_id, filled, 'seeding', json=_body_for([name], batch))
        # Deliberately outside the rejection condition: N requests, including
        # after 201 and on one-field routes (where omission sends an empty body).
        for omitted in fields:
            kept = [name for name in fields if name != omitted]
            _drive_one(client, route_id, filled, 'seeding', json=_body_for(kept, batch))


def _report(name, tally):
    print(
        f'{name}: {len(tally.entered)}/{len(tally.expected)} routes reached; '
        f'{len(tally.statuses)} driven; {len(tally.unentered)} unentered; '
        f'{len(tally.skipped)} explicitly unseedable'
    )


def seed_every_writable_field(client, parameters, *, spec=SPEC, payloads=None, full=None) -> Seeding:
    fields_by_route = {route: fields for route, fields in writable_string_fields(spec).items() if fields}
    tally = _PASSES['seeding'] = Seeding(expected=set(fields_by_route))
    payloads = tuple(fetch_payloads() if payloads is None else payloads)
    full = os.getenv('ATTACK_FULL_CORPUS') == '1' if full is None else full
    try:
        with preserve_configuration(client, route='seeding pass', report=tally.config_findings.append, durable=True):
            for route in sorted(fields_by_route, key=_read_before_destroy):
                filled = _target(route, parameters, tally)
                if filled is not None:
                    _seed_route(client, route, filled, fields_by_route[route], payloads, full)
                flush_hits()
        tally.config_restore_verified = True
    finally:
        flush_hits()
        _report('seeding', tally)
    return tally


def drive_every_route(client, parameters, *, spec=SPEC) -> dict[str, Outcome]:
    routes = operations(spec)
    tally = _PASSES['drive'] = Seeding(expected=set(routes))
    outcomes = {}
    try:
        with preserve_configuration(client, route='drive pass', report=tally.config_findings.append, durable=True):
            for route in routes:
                filled = _target(route, parameters, tally)
                if filled is not None:
                    outcomes[route] = _drive_one(client, route, filled, 'drive')
                flush_hits()
        tally.config_restore_verified = True
    finally:
        flush_hits()
        _report('drive', tally)
        print(f'Combined: {len(unentered_routes())} routes remain unentered')
    return outcomes
