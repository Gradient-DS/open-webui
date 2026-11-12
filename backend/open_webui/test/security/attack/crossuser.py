"""Probe resource isolation with ordinary callers and platform authority."""

import json
import os
import re
from contextlib import contextmanager, nullcontext
from copy import deepcopy
from dataclasses import dataclass, field
from urllib.parse import quote
from uuid import uuid4

import requests
from hostile_corpus import fetch_payloads
from openapi_surface import writable_string_fields

from . import plane, seeds
from .authorization_inventory import admin_gated_operations, platform_operations
from .benign_requests import benign_request
from .client import AttackClient, login
from .pass_support import pass_run

# Violation kinds a [[shared]] declaration in the attack surface may allow for
# one route. An administrator operation that is not refused is never sharing.
SHAREABLE = frozenset({'owner-marker', 'resource-id', 'same-object', 'write'})


@dataclass
class Authorization:
    tally: plane.Seeding
    controls: dict = field(default_factory=dict)
    control_bodies: dict = field(default_factory=dict)
    violations: list = field(default_factory=list)
    shared: dict = field(default_factory=dict)


def _shared(surface=None):
    """Route -> the violation kinds its [[shared]] declaration allows.

    Read from the unfilled surface: own_fixtures fills {token} throughout its
    copy, which would rewrite a route such as invites/{token}/validate.
    """
    surface = seeds.SURFACE if surface is None else surface
    return {
        entry['route']: frozenset(entry['allow'])
        for entry in surface.get('shared', [])
        if entry.get('runtime_soev_backed', seeds.runtime_soev_backed()) == seeds.runtime_soev_backed()
    }


class ServiceClient:
    """A service bearer acting for one user, as the agents-api and sync daemon call in.

    Not an AttackClient: a service key has no session, so a 401 is an answer to
    record rather than an identity to probe and lose.
    """

    def __init__(self, base_url, token, acting_user_id, headers=None):
        self.base_url = base_url.rstrip('/')
        self.identity = {'id': acting_user_id, 'role': 'service'}
        self._session = requests.Session()
        self._session.trust_env = False
        self._headers = {'Authorization': f'Bearer {token}', 'X-Acting-User-Id': acting_user_id, **(headers or {})}

    def request(self, method, path, **kwargs):
        headers = {**self._headers, **kwargs.pop('headers', {})}
        return self._session.request(
            method, self.base_url + path, headers=headers, allow_redirects=False, timeout=60, **kwargs
        )

    def close(self):
        self._session.close()


def _service(path, surface=None):
    """The [[service]] declaration whose prefix covers this path, if any."""
    surface = seeds.SURFACE if surface is None else surface
    for entry in surface.get('service', []):
        if path == entry['prefix'] or path.startswith(entry['prefix'] + '/'):
            return entry
    return None


@contextmanager
def _as_service(declaration, base_url, *users):
    """The service acting for each user on a [[service]] route; the users themselves elsewhere."""
    if declaration is None:
        yield users
        return
    clients = [
        ServiceClient(base_url, declaration['token'], user.identity['id'], declaration.get('headers')) if user else None
        for user in users
    ]
    try:
        yield clients
    finally:
        for client in clients:
            if client:
                client.close()


def _markers_outside(target, markers):
    """Markers the request itself did not carry: echoing a path the caller sent discloses nothing."""
    return tuple(marker for marker in markers if marker not in target)


def expected_refusals(surface=None):
    """Route -> the [[control]] declaring an owner refusal that is a guard working, not a gap."""
    surface = seeds.SURFACE if surface is None else surface
    return {
        entry['route']: entry
        for entry in surface.get('control', [])
        if entry.get('runtime_soev_backed', seeds.runtime_soev_backed()) == seeds.runtime_soev_backed()
    }


def refused_as_expected(declaration, status, body):
    if not declaration or status != declaration['status'] or declaration['detail'] not in body:
        return False
    if 'json' in declaration:
        try:
            return json.loads(body) == declaration['json']
        except ValueError:
            return False
    return True


def _nothing_happened(answer):
    # A handler that reports its own no-op -- `false` from a delete, `null` from an
    # update that matched no row the caller owns -- wrote nothing.
    return answer.body.strip() in {'false', 'null'}


def _is_seedable(route, surface):
    """An operation whose id cannot be seeded is out of scope for an ownership check.

    seeds.parameter_for raises KeyError for a parameter the surface records as
    unseedable, and _owner_key reaches it before plane._target gets the chance to
    skip the route. Filter here instead, so those operations never enter the
    denominator: a route nobody can seed is not a route whose ownership went
    unchecked.
    """
    path = route.split(' ', 1)[1]
    for name in re.findall(r'\{([^}]+)\}', path):
        try:
            declaration = seeds.parameter_for(path, name, surface)
        except KeyError:
            return False
        if not seeds.seed_enabled(declaration):
            return False
    return True


def targets(spec=seeds.SPEC, *, surface=None):
    admins = set(admin_gated_operations(spec))
    platform = set(platform_operations(spec))
    routes = [
        route
        for route in plane.operations(spec)
        if ('{' in route or route in admins or route in platform) and _is_seedable(route, surface or seeds.SURFACE)
    ]
    return routes, admins


def _owner_key(path, surface):
    entries = {entry['key']: entry for entry in surface['parameter']}
    declaration = seeds.parameter_for(path, re.findall(r'\{([^}]+)\}', path)[-1], surface)
    while 'same_as' in declaration:
        declaration = entries[declaration['same_as']]
    return 'user'


def _field_owner_key(route, surface):
    entries = [*surface.get('parameter', []), *surface.get('fixture', [])]
    by_key = {key: entry for entry in entries for key in [entry['key'], *entry.get('provides', [])]}
    owners = set()
    for declaration in surface.get('field', []):
        if declaration['route'] != route or seeds.backed_seed_waived(declaration):
            continue
        if seeds.runtime_soev_backed() == 'drive':
            declaration = {**declaration, **declaration.get('v2', {})}
        fields = {key: declaration[key] for key in ('params', 'json', 'form', 'files') if key in declaration}
        for key in re.findall(r'\{([A-Za-z_][A-Za-z0-9_]*)\}', json.dumps(fields)):
            if key not in by_key:
                continue
            entry = by_key[key]
            while 'same_as' in entry:
                entry = by_key[entry['same_as']]
            owners.add(entry.get('actor', 'user'))
    if len(owners) > 1:
        raise ValueError(f'{route}: referenced fixtures have different owners: {sorted(owners)}')
    return next(iter(owners), None)


@contextmanager
def own_fixtures(identities, *, spec=seeds.SPEC):
    # Pin a marker into every declarative seed as well as capturing the token
    # generated by procedural seeders. No suite fixture dictionary is accepted.
    marker = f'crossuser_{uuid4().hex}'
    surface = seeds._fill_token(deepcopy(seeds.SURFACE), marker)
    parameters = seeds.resolve_parameters(
        identities.user, admin=identities.admin, surface=surface, spec=spec, collect_failures=True
    )
    yield parameters, (marker,), surface, {'admin': identities.admin, 'user': identities.user}


@contextmanager
def _actor(admin, actor, destructive):
    if not destructive:
        yield actor
        return
    disposable = plane._throwaway(admin)
    try:
        yield disposable
    finally:
        disposable.close()


def _request(admin, actor, route, target, tally, kwargs):
    from .diagnostics import timed

    guard = (
        plane.preserve_configuration(
            admin,
            route=route,
            report=tally.config_findings.append,
            unverified=lambda reason: tally.config_unverified.setdefault(route, reason),
        )
        if route.split(' ', 1)[0] not in {'GET', 'HEAD', 'OPTIONS', 'TRACE'}
        else nullcontext()
    )
    # Resource deletion needs its actual owner; only credential/account deletion
    # needs a disposable identity. Platform admins cannot impersonate a KB owner.
    invalidates_identity = route in seeds.DESTRUCTIVE and route.split(' ', 1)[1].startswith(
        ('/api/v1/auths/', '/api/v1/users/')
    )
    with timed(f'crossuser {route}'), guard, _actor(admin, actor, invalidates_identity) as client:
        response = client.request(route.split(' ', 1)[0], target, **deepcopy(kwargs))
        try:
            # The shared driver's Outcome truncates diagnostics; authorization
            # needs the complete original body, and an admin configuration guard
            # even when the actor is an ordinary user.
            entered = plane.record(route, response.status_code, response, pass_name='crossuser')
            if 200 <= response.status_code < 300:
                seeds.SeedContext(client, admin, seeds.SURFACE).wait_for_ingestion(
                    route.split(' ', 1)[0], target, seeds.json_body(response), kwargs.get('json')
                )
            return plane.Outcome(response.status_code, entered, response.text)
        finally:
            response.close()


def _object_ids(body):
    if isinstance(body, dict):
        for key, value in body.items():
            if (key == 'id' or key.endswith('_id')) and isinstance(value, str):
                yield value
            yield from _object_ids(value)
    elif isinstance(body, list):
        for value in body:
            yield from _object_ids(value)


def _json_or_text(body):
    try:
        return json.loads(body)
    except ValueError:
        return body


def _same_object(stranger, owner):
    body = _json_or_text(owner.body)
    return (
        200 <= stranger.status < 300
        and 200 <= owner.status < 300
        and bool(body)
        and body is not True
        and owner.body == stranger.body
    )


def _administrator_refused(route, answer):
    return answer.status in {401, 403} or refused_as_expected(
        expected_refusals().get(route), answer.status, answer.body
    )


def _check(result, route, stranger, owner, markers, *, admin_only, control, resource_ids=(), subject_scoped=False):
    reasons = []
    if stranger.timed_out or (owner is not None and owner.timed_out):
        # No response is not a refusal, a disclosure, or an accepted write.
        # Status 0 would read as "not refused" and invent an authorization
        # violation out of a slow route.
        return
    if any(marker in stranger.body for marker in markers):
        reasons.append(('owner-marker', 'seeded owner marker in second-account response (including refusals)'))
    if admin_only and not _administrator_refused(route, stranger):
        reasons.append(('admin', f'administrator operation was not refused: HTTP {stranger.status}'))
    if 200 <= stranger.status < 300:
        disclosed = set(_object_ids(_json_or_text(stranger.body))) & set(resource_ids)
        if disclosed:
            reasons.append(('resource-id', 'second account received an object containing the seeded resource id'))
    if owner is not None:
        if control:
            result.controls[route] = owner.status
            result.control_bodies[route] = owner.body[:300]
        if not subject_scoped and _same_object(stranger, owner):
            reasons.append(('same-object', 'second account received the same nonempty object as the owner'))
        if route == 'HEAD /api/v1/files/{id}/content' and 200 <= stranger.status < 300 and 200 <= owner.status < 300:
            reasons.append(('resource-access', 'second account accessed the owner resource through HEAD'))
        write = route.split(' ', 1)[0] not in {'GET', 'HEAD', 'OPTIONS', 'TRACE'}
        if write and not subject_scoped and 200 <= stranger.status < 300 and not _nothing_happened(stranger):
            reasons.append(('write', 'second account accepted a write to the owner resource'))
    allowed = result.shared.get(route, frozenset())
    for kind, reason in reasons:
        if kind in allowed:
            continue
        item = {'route': route, 'reason': reason, 'status': stranger.status, 'body': stranger.body[:2000]}
        result.violations.append(item)
        result.tally.body_failures.append(item)


def _benign(route, parameters, spec):
    """The schema's benign request, carrying the ids [[field]] declares for the route.

    Without them a route that needs a real id in a request field answers 404 to
    the owner too, and an owner control that fails proves nothing about isolation.
    """
    request = benign_request(route, parameters, spec)
    declared = plane._declared(parameters, route)
    if declared.get('json'):
        variant = next((entry.get('v2', {}) for entry in seeds.SURFACE.get('field', []) if entry['route'] == route), {})
        request['json'] = (
            deepcopy(declared['json'])
            if seeds.runtime_soev_backed() == 'drive' and 'json' in variant
            else plane._hold_declared(request.get('json'), (), declared['json'])[0]
        )
    if declared.get('params'):
        request['params'] = {**request.get('params', {}), **declared['params']}
    if declared.get('headers'):
        request['headers'] = {**request.get('headers', {}), **declared['headers']}
    if 'data' in declared:
        request['data'] = declared['data']
    return request


def _requests(route, parameters, spec, fields, payloads, full):
    yield True, _benign(route, parameters, spec)
    names = fields.get(route, [])
    if names:
        for batch in plane._payload_batches(route, names, payloads, full=full):
            yield False, {**benign_request(route, parameters, spec), 'json': plane._body_for(names, batch)}


def drive_crossuser(identities, *, spec=seeds.SPEC, payloads=None, full=None):
    routes, admins = targets(spec)
    payloads = tuple(fetch_payloads() if payloads is None else payloads)
    full = os.getenv('ATTACK_FULL_CORPUS') == '1' if full is None else full
    fields = writable_string_fields(spec)
    platform = set(platform_operations(spec))
    with own_fixtures(identities, spec=spec) as (parameters, markers, surface, owners):
        with pass_run('crossuser', routes, identities.admin) as tally:
            result = Authorization(tally, shared=_shared())
            # File cleanup detaches KB documents, so its KB must still be available.
            # Path depth alone cannot express that cross-resource dependency.
            ordered = sorted(
                routes,
                key=lambda r: (
                    plane._read_before_destroy(r)[0],
                    r == 'DELETE /api/v1/knowledge/{id}/delete',
                    -r.count('/') if r.startswith('DELETE ') else 0,
                    r,
                ),
            )
            for route in ordered:
                target = plane._target(route, parameters, tally)
                if target is None:
                    continue
                path = route.split(' ', 1)[1]
                field_owner = _field_owner_key(route, surface) if '{' not in path else None
                subject_scoped = route in platform and '{' not in path
                owner = (
                    owners[_owner_key(path, surface)]
                    if '{' in path
                    else owners[field_owner]
                    if field_owner
                    else owners['admin']
                    if subject_scoped
                    else None
                )
                resource_ids = (
                    list(parameters.values())
                    if subject_scoped and route.startswith('GET ')
                    else [parameters[path, name] for name in re.findall(r'\{([^}]+)\}', path)]
                )
                service = _service(path)
                other_actors = [identities.intruder]
                if field_owner == 'user':
                    other_actors.append(identities.admin)
                with _as_service(service, identities.admin.base_url, *other_actors, owner) as clients:
                    *stranger_clients, owner = clients
                    for control, kwargs in _requests(route, parameters, spec, fields, payloads, full):
                        answers = [
                            _request(identities.admin, actor, route, target, tally, kwargs)
                            for actor in stranger_clients
                        ]
                        positive = _request(identities.admin, owner, route, target, tally, kwargs) if owner else None
                        for stranger in answers:
                            _check(
                                result,
                                route,
                                stranger,
                                positive,
                                _markers_outside(target + (repr(kwargs) if subject_scoped else ''), markers),
                                admin_only=route in admins,
                                control=control,
                                resource_ids=resource_ids,
                                subject_scoped=subject_scoped,
                            )
                plane.flush_hits()
    return result
