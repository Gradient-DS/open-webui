import re
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from . import crossuser, plane, seeds, test_shapes
from .authorization_inventory import admin_gated_operations, source_operations
from .identities import ensure_identities
from .pass_support import crash_details
from .test_shapes import needs_stack, reply

offline = test_shapes.offline


def test_inventory_resolves_every_committed_operation_including_named_methods_and_duplicate_functions():
    inventory = source_operations()
    assert set(plane.operations()) <= inventory.keys()
    assert len(admin_gated_operations()) == 223
    assert 'GET /api/v1/functions/' in inventory
    assert 'GET /api/v1/files/{id}/content' in inventory
    assert 'PATCH /api/v1/terminals/{server_id}/{path}' in inventory
    routes, admins = crossuser.targets()
    # Operations whose id the surface records as unseedable are out of scope: an
    # ownership check needs a resource that exists, and counting them would make the
    # pass look as though it left ownership unverified rather than unseedable.
    expected = admins | {r for r in plane.operations() if '{' in r}
    unseedable = {r for r in expected if not crossuser._is_seedable(r, seeds.SURFACE)}
    assert unseedable, 'expected at least one unseedable operation to be excluded'
    assert set(routes) == expected - unseedable


@pytest.mark.parametrize('status', [200, 400, 401, 403, 404, 500])
def test_marker_in_even_a_refusal_or_long_body_is_disclosure(status):
    result = crossuser.Authorization(plane.Seeding())
    stranger = plane.Outcome(status, False, 'x' * 4000 + 'unique-owner-marker')
    crossuser._check(
        result, 'GET /private/{id}', stranger, None, ['unique-owner-marker'], admin_only=False, control=True
    )
    assert result.violations
    assert result.tally.body_failures


def test_same_nonempty_object_without_marker_is_disclosure_but_empty_list_is_not():
    for body, exposed in [('[]', False), ('{"id":"private-row"}', True), ('private bytes', True), ('true', False)]:
        result = crossuser.Authorization(plane.Seeding())
        outcome = plane.Outcome(200, True, body)
        crossuser._check(result, 'GET /r/{id}', outcome, outcome, ['marker'], admin_only=False, control=True)
        assert bool(result.violations) is exposed


def test_partial_object_disclosure_does_not_need_the_marker_or_identical_owner_body():
    result = crossuser.Authorization(plane.Seeding())
    crossuser._check(
        result,
        'GET /r/{id}',
        plane.Outcome(200, True, '{"items":[{"id":"owner-row","title":"redacted"}]}'),
        plane.Outcome(200, True, '{"id":"owner-row","title":"private marker"}'),
        ['private marker'],
        admin_only=False,
        control=True,
        resource_ids=['owner-row'],
    )
    assert result.violations


def test_both_404_cannot_pass_the_positive_control_gate():
    result = crossuser.Authorization(plane.Seeding())
    missing = plane.Outcome(404, False, '{}')
    crossuser._check(result, 'DELETE /r/{id}', missing, missing, ['marker'], admin_only=False, control=True)
    with pytest.raises(AssertionError, match='404'):
        test_live_crossuser_owner_controls_are_positive(result)


@pytest.mark.parametrize('status', [200, 302, 400, 404, 422, 429, 500])
def test_admin_gate_requires_an_authorization_refusal(status):
    result = crossuser.Authorization(plane.Seeding())
    crossuser._check(
        result, 'POST /admin', plane.Outcome(status, False, '{}'), None, ['marker'], admin_only=True, control=True
    )
    assert result.violations


def setup_pass(monkeypatch, spec, replies=None):
    order = []
    identities = SimpleNamespace(admin=Mock(), user=Mock(), intruder=Mock())
    for name in ('admin', 'user', 'intruder'):
        actor = getattr(identities, name)
        actor.identity = {'role': 'admin' if name == 'admin' else 'user'}
        actor.request.side_effect = lambda method, path, name=name, **kw: (
            order.append((name, method, path, kw)) or reply(*(replies or {}).get(name, (200, {'ok': True})))
        )

    @contextmanager
    def fixtures(*a, **kw):
        parameters = {(path, name): 'owned' for path in spec['paths'] for name in re.findall(r'\{([^}]+)\}', path)}
        yield parameters, ['marker'], {}, {'admin': identities.admin}

    monkeypatch.setattr(crossuser, 'own_fixtures', fixtures)
    monkeypatch.setattr(crossuser, '_owner_key', lambda *a: 'admin')
    monkeypatch.setattr(plane, '_target', lambda route, *a: route.split(' ', 1)[1].replace('{id}', 'owned'))
    monkeypatch.setattr(crossuser, 'targets', lambda spec: (plane.operations(spec), {'POST /admin'}))
    return identities, order


def test_second_account_first_reads_writes_child_deletes_then_parent_and_destructive_last(offline, monkeypatch):
    spec = {
        'paths': {
            '/r/{id}': {'delete': {}, 'post': {}, 'get': {}},
            '/r/{id}/child': {'delete': {}},
            '/api/v1/users/{user_id}': {'delete': {}},
            '/admin': {'post': {}},
        }
    }
    identities, order = setup_pass(monkeypatch, spec, {'intruder': (403, {'detail': 'refused'})})
    actors = []

    @contextmanager
    def disposable(admin, actor, destructive):
        actors.append((actor, destructive))
        yield actor

    monkeypatch.setattr(crossuser, '_actor', disposable)
    result = crossuser.drive_crossuser(identities, spec=spec, payloads=['one'])
    pairs = [entry for entry in order if entry[2] != '/admin']
    assert [entry[0] for entry in pairs] == ['intruder', 'admin'] * 5
    assert [(entry[1], entry[2]) for entry in pairs[::2]] == [
        ('GET', '/r/owned'),
        ('POST', '/r/owned'),
        ('DELETE', '/r/owned/child'),
        ('DELETE', '/r/owned'),
        ('DELETE', '/api/v1/users/{user_id}'),
    ]
    assert [flag for _, flag in actors][-2:] == [True, True]
    assert not result.violations
    assert result.tally.config_restore_verified


@pytest.mark.parametrize('full', [False, True])
def test_corpus_uses_shared_sampling_and_resets_module_tallies(offline, monkeypatch, full):
    schema = {'type': 'object', 'properties': {'name': {'type': 'string'}}}
    spec = {'paths': {'/r/{id}': {'post': {'requestBody': {'content': {'application/json': {'schema': schema}}}}}}}
    identities, order = setup_pass(monkeypatch, spec, {'intruder': (403, {'detail': 'refused'})})
    monkeypatch.setenv('ATTACK_FULL_CORPUS', '1' if full else '0')
    result = crossuser.drive_crossuser(identities, spec=spec, payloads=['one', 'two'])
    assert len(order) == 2 * (1 + (2 if full else 1))
    first = list(order)
    order.clear()
    again = crossuser.drive_crossuser(identities, spec=spec, payloads=['one', 'two'])
    assert order == first
    assert result.tally is not again.tally
    assert result.tally.statuses == again.tally.statuses


def test_fixture_resolution_is_private_and_tracks_both_seed_markers(offline, monkeypatch):
    identities = SimpleNamespace(admin=Mock(), user=Mock())
    maps = []

    def resolve(client, *, admin, surface, spec):
        assert client is identities.user and admin is identities.admin
        maps.append(surface)
        return {('/api/v1/configs/namespace/{namespace}', 'namespace'): 'attack_procedural-token'}

    monkeypatch.setattr(seeds, 'resolve_parameters', resolve)
    monkeypatch.setattr(crossuser, 'login', Mock(return_value=Mock()))
    for _ in range(2):
        with crossuser.own_fixtures(identities) as (_, markers, surface, owners):
            assert markers[1] == 'procedural-token'
            assert markers[0] in str(surface)
            assert owners['user'] is identities.user
    assert maps[0] != maps[1] and maps[0] is not seeds.SURFACE
    assert '{token}' in str(seeds.SURFACE)


def test_owner_mapping_includes_aliases_integrations_task_and_disposable_export():
    for path, expected in [
        ('/api/v1/chats/folder/{folder_id}', 'admin'),
        ('/api/v1/files/{id}/attachments/{attachment_id}', 'user'),
        ('/api/tasks/stop/{task_id}', 'admin'),
        ('/cache/{path}', 'exporter'),
    ]:
        assert crossuser._owner_key(path, seeds.SURFACE) == expected


def test_destructive_ordinary_probe_never_acquires_admin_role(offline, monkeypatch):
    admin, ordinary, disposable = Mock(), Mock(), Mock()
    ordinary.identity = {'role': 'user'}
    admin.request.return_value = reply(200, {})
    factory = Mock(return_value=disposable)
    monkeypatch.setattr(crossuser, 'AttackClient', factory)
    for _ in range(2):
        with crossuser._actor(admin, ordinary, True) as actor:
            assert actor is disposable
    assert factory.call_count == disposable.close.call_count == 2
    assert all(call.kwargs['json']['role'] == 'user' for call in admin.request.call_args_list)
    assert all(call.kwargs['role'] == 'user' for call in disposable.authenticate.call_args_list)
    emails = [call.kwargs['json']['email'] for call in admin.request.call_args_list]
    assert emails[0] != emails[1]


def test_ordinary_writes_restore_configuration_using_admin_even_when_request_fails(offline, monkeypatch):
    admin, ordinary = Mock(), Mock()
    ordinary.request.side_effect = RuntimeError('request failed')
    events = []

    @contextmanager
    def guard(actor, **kw):
        assert actor is admin
        events.append('snapshot')
        try:
            yield
        finally:
            events.append('restore')

    monkeypatch.setattr(plane, 'preserve_configuration', guard)
    with pytest.raises(RuntimeError, match='request failed'):
        crossuser._request(admin, ordinary, 'POST /r', '/r', plane.Seeding(), {})
    assert events == ['snapshot', 'restore']


def test_crossuser_5xx_and_entries_are_independent(offline, monkeypatch):
    spec = {'paths': {'/r/{id}': {'get': {}}}}
    identities, _ = setup_pass(monkeypatch, spec, {'intruder': (404, {}), 'admin': (404, {})})
    plane.record('GET /r/{id}', 200, {}, pass_name='drive')
    result = crossuser.drive_crossuser(identities, spec=spec, payloads=['one'])
    assert not result.tally.entered
    plane.record('GET /r/{id}', 500, 'authorization crash', pass_name='crossuser')
    plane.record('GET /r/{id}', 200, {}, pass_name='crossuser')
    with pytest.raises(AssertionError, match='authorization crash'):
        test_live_crossuser_has_its_own_5xx_assertion(result)


@pytest.fixture(scope='module')
def live_crossuser():
    identities = ensure_identities()
    try:
        yield crossuser.drive_crossuser(identities)
    finally:
        identities.close()


def test_a_route_that_never_answers_is_not_an_authorization_violation():
    # Status 0 with admin_only would otherwise read as "not refused" and report
    # an authorization finding for a route that simply did not answer.
    result = crossuser.Authorization(tally=plane.Seeding())
    silence = plane.Outcome(0, False, '', timed_out=True)
    crossuser._check(result, 'DELETE /api/v1/users/{user_id}', silence, silence, (), admin_only=True, control=True)
    assert result.violations == []
    assert result.tally.body_failures == []


@needs_stack
def test_live_crossuser_has_its_own_5xx_assertion(live_crossuser):
    assert not live_crossuser.tally.crashes, crash_details(live_crossuser.tally)


@needs_stack
def test_live_crossuser_refuses_other_users_and_admin_operations(live_crossuser):
    assert not live_crossuser.violations, live_crossuser.violations


@needs_stack
def test_live_crossuser_owner_controls_are_positive(live_crossuser):
    gaps = {route: status for route, status in live_crossuser.controls.items() if not 200 <= status < 300}
    assert not gaps, f'Owner positive controls did not succeed; isolation remains unproven: {gaps}'
    assert live_crossuser.controls, 'No owner positive controls ran'


@needs_stack
def test_live_crossuser_reports_its_own_reach(live_crossuser, record_property):
    tally = live_crossuser.tally
    assert tally.statuses.keys() | tally.skipped.keys() == tally.expected == set(crossuser.targets()[0])
    assert tally.config_restore_verified
    record_property('crossuser_unentered', tally.unentered)
    assert tally.entered, 'No authorization request entered a handler'
