import re
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from . import crossuser, plane, seeds, test_shapes
from .authorization_inventory import admin_gated_operations, platform_operations, source_operations
from .test_shapes import reply

offline = test_shapes.offline


def test_inventory_resolves_every_committed_operation_including_named_methods_and_duplicate_functions():
    inventory = source_operations()
    assert set(plane.operations()) <= inventory.keys()
    assert len(admin_gated_operations()) == 0
    assert 'GET /api/v1/functions/' in inventory
    assert 'GET /api/v1/files/{id}/content' in inventory
    assert 'POST /api/v1/files/onedrive/attach' in inventory
    assert 'PATCH /api/v1/terminals/{server_id}/{path}' in inventory
    routes, admins = crossuser.targets()
    # Operations whose id the surface records as unseedable are out of scope: an
    # ownership check needs a resource that exists, and counting them would make the
    # pass look as though it left ownership unverified rather than unseedable.
    expected = admins | {r for r in plane.operations() if '{' in r} | set(platform_operations())
    unseedable = {r for r in expected if not crossuser._is_seedable(r, seeds.SURFACE)}
    assert unseedable, 'expected at least one unseedable operation to be excluded'
    assert set(routes) == expected - unseedable


def test_skill_file_routes_require_owner_controls_and_have_no_sharing_allowance():
    routes, admins = crossuser.targets()
    expected = {
        'GET /api/v1/skills/id/{id}/files',
        'POST /api/v1/skills/id/{id}/files',
        'PUT /api/v1/skills/id/{id}/files',
        'GET /api/v1/skills/id/{id}/files/content',
        'POST /api/v1/skills/id/{id}/files/move',
        'POST /api/v1/skills/id/{id}/files/remove',
    }
    assert expected <= set(routes)
    assert not expected & admins
    assert not expected & crossuser._shared().keys()
    assert not expected & crossuser.expected_refusals().keys()
    for route in expected:
        assert route in seeds.ordinary_targets()
        path = route.split(' ', 1)[1]
        assert seeds.parameter_for(path, 'id')['seeded_by'] == 'seed_skill_bundle'


@pytest.mark.parametrize('path', ['/api/v1/chats/{id}/share', '/api/v1/chats/shared/{id}/access'])
def test_chat_share_access_requires_an_owner_control_without_sharing_allowances(path):
    route = f'GET {path}'
    routes, admins = crossuser.targets()
    assert route in routes and route not in admins
    assert crossuser._owner_key(path, seeds.SURFACE) == 'user'
    assert route not in crossuser._shared()
    assert route not in crossuser.expected_refusals()
    assert route not in seeds.SOEV_BACKED
    inventory = source_operations()
    assert inventory[route][1] is inventory['GET /api/v1/chats/shared/{id}/access'][1]
    result = crossuser.Authorization(plane.Seeding())
    grants = '[{"id":"owner-grant","principal_id":"owner-principal","permission":"read"}]'
    owner = plane.Outcome(200, True, grants)
    crossuser._check(
        result,
        route,
        plane.Outcome(404, False, '{"detail":"Not found"}'),
        owner,
        ['owner-principal'],
        admin_only=False,
        control=True,
    )
    assert result.controls[route] == 200
    assert not result.violations
    crossuser._check(result, route, owner, owner, ['owner-principal'], admin_only=False, control=True)
    assert result.violations


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


def test_head_success_discloses_access_even_without_a_response_body():
    result = crossuser.Authorization(plane.Seeding())
    crossuser._check(
        result,
        'HEAD /api/v1/files/{id}/content',
        plane.Outcome(200, True, ''),
        plane.Outcome(200, True, ''),
        [],
        admin_only=False,
        control=True,
    )
    assert result.violations[0]['reason'].endswith('through HEAD')


def test_picker_control_satisfies_the_real_body_and_required_header_contract():
    from uuid import UUID

    from open_webui.routers.live_documents import PickerAttachment

    request = crossuser._benign('POST /api/v1/files/onedrive/attach', {}, seeds.SPEC)
    PickerAttachment.model_validate(request['json'])
    UUID(request['headers']['Idempotency-Key'])


def test_schedule_control_keeps_the_declared_provider_scope():
    route = 'POST /api/v1/cloud-sync/knowledge/{knowledge_id}/schedules'
    scope = {'drive_id': 'ci-drive', 'item_id': 'root', 'include_descendants': True, 'single_file': False}
    parameters = seeds.Resolved(fields={route: {'json': {'connection_id': 'owned', 'scope': scope}}})
    request = crossuser._benign(route, parameters, seeds.SPEC)
    assert request['json']['scope'] == scope
    assert request['json']['connection_id'] == 'owned'
    assert plane._body_for(['scope'], 'hostile', {'scope': scope}) == {'scope': scope}


def test_meeting_audio_control_sends_a_valid_wav_body(monkeypatch):
    monkeypatch.setenv('RUNTIME_SOEV_BACKED', 'drive')
    import io
    import wave

    route = 'POST /api/v1/meetings/audio'
    entry = next(item for item in seeds.SURFACE['field'] if item['route'] == route)
    context = seeds.SeedContext(None, None, seeds.SURFACE, token='audio')
    context.values['meeting'] = 'fictional-meeting'
    parameters = seeds.Resolved(fields=seeds.resolve_fields(context, entries=[entry]))
    request = crossuser._benign(route, parameters, seeds.SPEC)
    with wave.open(io.BytesIO(request['data']), 'rb') as audio:
        assert audio.getnchannels() == 1
        assert audio.getframerate() == 16000
        assert audio.readframes(1) == b'\x00\x00'
    assert request['params']['name'] == 'attack-audio.wav'


def test_reset_control_uses_the_actual_owner(offline, monkeypatch):
    spec = {'paths': {'/api/v1/knowledge/{id}/reset': {'post': {}}}}
    identities, order = setup_pass(monkeypatch, spec, {'intruder': (403, {'detail': 'refused'})})
    flags = []

    @contextmanager
    def actor(admin, owner, disposable):
        flags.append(disposable)
        yield owner

    monkeypatch.setattr(crossuser, '_actor', actor)
    crossuser.drive_crossuser(identities, spec=spec, payloads=['one'])
    assert flags == [False, False]
    assert [entry[0] for entry in order] == ['intruder', 'admin']


def test_both_404_cannot_pass_the_positive_control_gate():
    from .test_live_crossuser import test_live_crossuser_owner_controls_are_positive

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
            '/api/v1/auths/oauth/sessions/{provider}': {'delete': {}},
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
        ('DELETE', '/api/v1/auths/oauth/sessions/{provider}'),
    ]
    assert [flag for _, flag in actors][-2:] == [True, True]
    assert not result.violations
    assert result.tally.config_restore_verified


def test_file_owner_can_delete_before_its_knowledge_parent_disappears(offline, monkeypatch):
    from .test_live_crossuser import test_live_crossuser_owner_controls_are_positive as check_controls

    monkeypatch.setenv('RUNTIME_SOEV_BACKED', 'drive')
    spec = {
        'paths': {
            '/api/v1/knowledge/{id}/dirs/{dir_id}/delete': {'delete': {}},
            '/api/v1/knowledge/{id}/delete': {'delete': {}},
            '/api/v1/files/{id}': {'delete': {}},
        }
    }
    identities, order = setup_pass(monkeypatch, spec, {'intruder': (403, {'detail': 'refused'})})
    parent_available = True

    def owner(method, path, **kwargs):
        nonlocal parent_available
        order.append(('admin', method, path, kwargs))
        if path == '/api/v1/files/owned' and not parent_available:
            return reply(404, {'detail': {'code': 'upstream_error'}})
        if path == '/api/v1/knowledge/owned/delete':
            parent_available = False
        return reply(200, {'ok': True})

    identities.admin.request.side_effect = owner
    result = crossuser.drive_crossuser(identities, spec=spec, payloads=[])
    assert [path for actor, _, path, _ in order if actor == 'admin'] == [
        '/api/v1/knowledge/owned/dirs/{dir_id}/delete',
        '/api/v1/files/owned',
        '/api/v1/knowledge/owned/delete',
    ]
    assert not parent_available and not result.violations
    check_controls(result)
    assert all(status == 200 for status in result.controls.values())


@pytest.mark.parametrize('terminal', ['completed', 'failed'])
def test_content_mutation_settles_before_the_next_owner_file_add(offline, monkeypatch, terminal):
    from .test_live_crossuser import test_live_crossuser_owner_controls_are_positive as check_controls

    monkeypatch.setenv('RUNTIME_SOEV_BACKED', 'drive')
    monkeypatch.setattr(seeds.time, 'sleep', Mock())
    spec = {
        'paths': {
            '/api/v1/files/{id}/data/content/update': {'post': {}},
            '/api/v1/knowledge/{id}/file/add': {'post': {}},
        }
    }
    identities, order = setup_pass(monkeypatch, spec, {'intruder': (403, {'detail': 'refused'})})
    pending = False
    statuses = iter(
        [
            {'status': 'processing', 'soev_job': {'job_id': 'job'}},
            {'status': 'completed', 'soev_job': {'job_id': 'job'}},
            {'status': terminal, 'soev_job': None, 'error': 'publication refused'},
        ]
    )

    def owner(method, path, **kwargs):
        nonlocal pending
        order.append(('admin', method, path, kwargs))
        if path.endswith('/data/content/update'):
            pending = True
            return reply(200, {'content': 'updated'})
        if method == 'GET' and path == '/api/v1/files/owned':
            meta = next(statuses) if pending else {'status': 'completed', 'soev_job': None}
            if meta['soev_job'] is None:
                pending = False
            return reply(200, {'meta': meta})
        assert not pending, 'owner add raced unfinished file processing'
        return reply(200, {'files': [{'id': 'owned'}]})

    identities.admin.request.side_effect = owner
    monkeypatch.setattr(crossuser, '_requests', lambda *args: iter([(True, {'json': {'file_id': 'owned'}})]))
    if terminal == 'failed':
        with pytest.raises(RuntimeError, match='publication refused'):
            crossuser.drive_crossuser(identities, spec=spec, payloads=[])
        assert not any(path.endswith('/file/add') for _, _, path, _ in order)
    else:
        result = crossuser.drive_crossuser(identities, spec=spec, payloads=[])
        check_controls(result)
        assert not result.violations
        assert all(status == 200 for status in result.controls.values())
        owner_paths = [path for actor, _, path, _ in order if actor == 'admin']
        assert owner_paths[:5] == [
            '/api/v1/files/owned/data/content/update',
            *['/api/v1/files/owned'] * 3,
            '/api/v1/knowledge/owned/file/add',
        ]


def test_folder_owner_control_supplies_the_owned_folder(monkeypatch):
    monkeypatch.setenv('RUNTIME_SOEV_BACKED', 'drive')
    route = 'POST /api/v1/chats/{id}/folder'
    entry = next(item for item in seeds.SURFACE['field'] if item['route'] == route)
    context = seeds.SeedContext(None, None, seeds.SURFACE)
    context.values['folder'] = 'fictional-folder'
    parameters = seeds.Resolved(fields=seeds.resolve_fields(context, entries=[entry]))
    assert crossuser._benign(route, parameters, seeds.SPEC)['json'] == {'folder_id': 'fictional-folder'}
    assert route not in crossuser.expected_refusals()
    assert seeds.parameter_for('/api/v1/folders/{id}', 'id')['actor'] == 'user'
    assert crossuser._owner_key('/api/v1/chats/{id}', seeds.SURFACE) == 'user'


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

    def resolve(client, *, admin, surface, spec, collect_failures):
        assert collect_failures
        assert client is identities.user and admin is identities.admin
        maps.append(surface)
        return {('/api/v1/configs/namespace/{namespace}', 'namespace'): 'attack_procedural-token'}

    monkeypatch.setattr(seeds, 'resolve_parameters', resolve)
    monkeypatch.setattr(crossuser, 'login', Mock(return_value=Mock()))
    for _ in range(2):
        with crossuser.own_fixtures(identities) as (_, markers, surface, owners):
            assert len(markers) == 1
            assert markers[0] in str(surface)
            assert owners['user'] is identities.user
    assert maps[0] != maps[1] and maps[0] is not seeds.SURFACE
    assert '{token}' in str(seeds.SURFACE)


def test_owner_mapping_includes_aliases_integrations_task_and_disposable_export():
    for path, expected in [
        ('/api/v1/chats/folder/{folder_id}', 'user'),
    ]:
        assert crossuser._owner_key(path, seeds.SURFACE) == expected


def test_destructive_ordinary_probe_never_acquires_admin_role(offline, monkeypatch):
    admin, ordinary, disposable = Mock(), Mock(), Mock()
    ordinary.identity = {'role': 'user'}
    factory = Mock(return_value=disposable)
    monkeypatch.setattr(plane, 'create_oidc_identity', factory)
    for _ in range(2):
        with crossuser._actor(admin, ordinary, True) as actor:
            assert actor is disposable
    assert factory.call_count == disposable.close.call_count == 2
    assert all(call.args == (admin,) for call in factory.call_args_list)
    assert all(call.kwargs['role'] == 'user' for call in factory.call_args_list)
    emails = [call.kwargs['email'] for call in factory.call_args_list]
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
    from .test_live_crossuser import test_live_crossuser_has_its_own_5xx_assertion

    spec = {'paths': {'/r/{id}': {'get': {}}}}
    identities, _ = setup_pass(monkeypatch, spec, {'intruder': (404, {}), 'admin': (404, {})})
    plane.record('GET /r/{id}', 200, {}, pass_name='drive')
    result = crossuser.drive_crossuser(identities, spec=spec, payloads=['one'])
    assert not result.tally.entered
    plane.record('GET /r/{id}', 500, 'authorization crash', pass_name='crossuser')
    plane.record('GET /r/{id}', 200, {}, pass_name='crossuser')
    with pytest.raises(AssertionError, match='authorization crash'):
        test_live_crossuser_has_its_own_5xx_assertion(result)


def test_a_route_that_never_answers_is_not_an_authorization_violation():
    # Status 0 with admin_only would otherwise read as "not refused" and report
    # an authorization finding for a route that simply did not answer.
    result = crossuser.Authorization(tally=plane.Seeding())
    silence = plane.Outcome(0, False, '', timed_out=True)
    crossuser._check(result, 'DELETE /api/v1/users/{user_id}', silence, silence, (), admin_only=True, control=True)
    assert result.violations == []
    assert result.tally.body_failures == []


def _answer(status, body):
    return plane.Outcome(status, 200 <= status < 300, body)


def test_a_declared_sharing_kind_is_allowed_and_only_that_kind():
    result = crossuser.Authorization(tally=plane.Seeding(), shared={'GET /x/{id}': frozenset({'same-object'})})
    body = '{"id": "r1", "name": "owner marker-1"}'
    crossuser._check(
        result,
        'GET /x/{id}',
        _answer(200, body),
        _answer(200, body),
        ('marker-1',),
        admin_only=False,
        control=True,
        resource_ids=('r1',),
    )
    reasons = {violation['reason'] for violation in result.violations}
    assert 'second account received the same nonempty object as the owner' not in reasons
    assert 'second account received an object containing the seeded resource id' in reasons
    assert 'seeded owner marker in second-account response (including refusals)' in reasons


@pytest.mark.parametrize(
    'body, accepted', [('false', False), ('null', False), ('true', True), ('{"id": "r1"}', True), ('', True)]
)
def test_a_2xx_that_reports_nothing_happened_is_not_an_accepted_write(body, accepted):
    # folders/{id}/update/expanded answers 200 null and memories DELETE 200 false
    # for a folder or memory the caller does not own: nothing was written.
    result = crossuser.Authorization(tally=plane.Seeding())
    crossuser._check(
        result,
        'POST /x/{id}/update',
        _answer(200, body),
        _answer(200, '{"ok": 1}'),
        (),
        admin_only=False,
        control=False,
    )
    wrote = any(v['reason'] == 'second account accepted a write to the owner resource' for v in result.violations)
    assert wrote is accepted


def test_every_sharing_declaration_names_a_route_allowed_kinds_and_a_reason():
    operations = set(plane.operations(seeds.SPEC))
    for entry in seeds.SURFACE.get('shared', []):
        assert entry['route'] in operations, entry['route']
        assert entry['reason'].strip(), entry['route']
        assert entry['allow'] and set(entry['allow']) <= crossuser.SHAREABLE, entry


def test_the_owner_control_carries_declared_fields():
    # prompts/.../history/diff and chats/.../resolve 404 without the ids [[field]]
    # declares, so without them the owner control never succeeded and isolation
    # on those routes stayed unproven.
    schema = {'type': 'object', 'properties': {'file_id': {'type': 'string'}, 'directory_id': {'type': 'string'}}}
    spec = {
        'paths': {
            '/x': {'get': {}},
            '/y': {'post': {'requestBody': {'content': {'application/json': {'schema': schema}}}}},
        }
    }
    parameters = seeds.Resolved(
        fields={'GET /x': {'params': {'from_id': 'h1'}}, 'POST /y': {'json': {'file_id': 'f1'}}}
    )
    [(control, get)] = list(crossuser._requests('GET /x', parameters, spec, {}, (), False))
    assert control and get['params'] == {'from_id': 'h1'}
    [(_, post)] = list(crossuser._requests('POST /y', parameters, spec, {}, (), False))
    assert post['json']['file_id'] == 'f1'


def test_a_service_route_is_driven_as_the_service_acting_for_each_side(monkeypatch):
    # The agents-api and the sync daemon call in with a service key and an acting
    # user. Isolation there means: acting for the intruder must not reach the
    # owner's resource, and acting for the owner must.
    from types import SimpleNamespace

    import requests

    sent = []

    def fake(self, method, url, **kwargs):
        sent.append((method, url, kwargs['headers']))
        answer = requests.Response()
        answer.status_code = 200
        answer._content = b'{}'
        return answer

    monkeypatch.setattr(crossuser.requests.Session, 'request', fake)
    declaration = {'prefix': '/svc', 'token': 'k', 'headers': {'X-Acting-Provider': 'p'}}
    users = [SimpleNamespace(identity={'id': 'intruder-id'}), SimpleNamespace(identity={'id': 'owner-id'})]
    with crossuser._as_service(declaration, 'http://app', *users) as (intruder, owner):
        intruder.request('GET', '/svc/x')
        owner.request('GET', '/svc/x')
    assert [headers['X-Acting-User-Id'] for _, _, headers in sent] == ['intruder-id', 'owner-id']
    assert all(h['Authorization'] == 'Bearer k' and h['X-Acting-Provider'] == 'p' for _, _, h in sent)
    assert sent[0][1] == 'http://app/svc/x'


def test_an_ordinary_route_keeps_its_users():
    with crossuser._as_service(None, 'http://app', 'intruder', 'owner') as actors:
        assert actors == ('intruder', 'owner')
    assert crossuser._service('/api/v1/chats/x') is None


def test_every_service_declaration_covers_a_route_and_names_its_key():
    paths = {route.split(' ', 1)[1] for route in plane.operations(seeds.SPEC)}
    declarations = seeds.SURFACE.get('service', [])
    for entry in declarations:
        assert any(p == entry['prefix'] or p.startswith(entry['prefix'] + '/') for p in paths), entry['prefix']
        assert entry['token'] and entry['reason'].strip(), entry


def test_a_failed_owner_control_keeps_the_reason():
    # 59 owner controls failed with a bare status; the body says why, so a live
    # run explains every gap at once instead of one replay per route.
    result = crossuser.Authorization(tally=plane.Seeding())
    crossuser._check(
        result,
        'POST /x/{id}',
        _answer(403, '{"detail": "not a member"}'),
        _answer(403, '{"detail": "not a member"}'),
        (),
        admin_only=False,
        control=True,
    )
    assert result.controls['POST /x/{id}'] == 403
    assert 'not a member' in result.control_bodies['POST /x/{id}']


def test_a_marker_the_request_carried_is_not_a_disclosure():
    # chat/actions refused with "Action not available: attack_function_<marker>": the
    # id is in the path the second account sent, not something the owner disclosed.
    assert crossuser._markers_outside('/api/chat/actions/attack_function_m1', ('m1', 'ns1')) == ('ns1',)


def test_a_declared_refusal_matches_only_its_status_and_detail():
    declaration = {'route': 'DELETE /x', 'status': 400, 'detail': 'in use', 'reason': 'guard'}
    assert crossuser.refused_as_expected(declaration, 400, '{"detail": "still in use"}')
    assert not crossuser.refused_as_expected(declaration, 403, '{"detail": "still in use"}')
    assert not crossuser.refused_as_expected(declaration, 400, '{"detail": "other"}')
    assert not crossuser.refused_as_expected(None, 400, 'in use')


def test_every_declared_refusal_names_a_route_a_4xx_a_detail_and_a_reason():
    operations = set(plane.operations(seeds.SPEC))
    declarations = seeds.SURFACE.get('control', [])
    assert declarations
    for entry in declarations:
        assert entry['route'] in operations, entry['route']
        assert 400 <= entry['status'] < 500, entry
        assert entry['detail'].strip() and entry['reason'].strip(), entry


@pytest.mark.parametrize('method', ['GET', 'POST'])
def test_subject_scoped_results_allow_independent_objects_but_still_catch_disclosure(method):
    route = f'{method} /api/v1/meetings'
    result = crossuser.Authorization(plane.Seeding())
    own_response = plane.Outcome(200, True, '{"data": []}')
    crossuser._check(
        result,
        route,
        own_response,
        own_response,
        ['owner-marker'],
        admin_only=False,
        control=True,
        subject_scoped=True,
    )
    assert not result.violations
    leaked = plane.Outcome(200, True, '{"id": "owner-resource", "title": "owner-marker"}')
    crossuser._check(
        result,
        route,
        leaked,
        own_response,
        ['owner-marker'],
        admin_only=False,
        control=True,
        subject_scoped=True,
        resource_ids=['owner-resource'],
    )
    assert len(result.violations) == 2


def test_seeded_task_list_distinguishes_owner_from_second_account_and_detects_leaks():
    route = 'GET /api/tasks/chat/{chat_id}'
    owner = plane.Outcome(200, True, '{"task_ids":["fictional-live-task"]}')
    result = crossuser.Authorization(plane.Seeding())
    crossuser._check(
        result,
        route,
        plane.Outcome(200, True, '{"task_ids":[]}'),
        owner,
        (),
        admin_only=False,
        control=True,
    )
    assert not result.violations
    assert result.controls[route] == 200
    crossuser._check(result, route, owner, owner, (), admin_only=False, control=True)
    assert result.violations[0]['reason'] == 'second account received the same nonempty object as the owner'


@pytest.mark.parametrize('suffix,method', [('versions', 'GET'), ('read', 'POST')])
def test_conversation_routes_require_an_owner_control_without_stranger_allowances(monkeypatch, suffix, method):
    monkeypatch.setenv('RUNTIME_SOEV_BACKED', 'drive')
    path = '/api/v1/threads/{thread_id}/' + suffix
    route = f'{method} {path}'
    routes, admins = crossuser.targets()
    assert route in routes and route not in admins
    assert crossuser._owner_key(path, seeds.SURFACE) == 'user'
    assert (route in seeds.SOEV_BACKED) is (suffix == 'versions')
    assert route in seeds.ordinary_targets() and route not in seeds.DESTRUCTIVE
    assert route not in crossuser._shared() and route not in crossuser.expected_refusals()
    assert route in source_operations()
    result = crossuser.Authorization(plane.Seeding())
    owner = plane.Outcome(
        200,
        True,
        '{"items":[{"thread_id":"owner-version"}]}'
        if suffix == 'versions'
        else '{"chat_id":"owner-version","last_read_at":1792922400.75}',
    )
    crossuser._check(
        result,
        route,
        plane.Outcome(404, False, '{"state":"unavailable"}'),
        owner,
        ['owner-version'],
        admin_only=False,
        control=True,
    )
    assert result.controls[route] == 200 and not result.violations
    crossuser._check(result, route, owner, owner, ['owner-version'], admin_only=False, control=True)
    assert result.violations


def test_conversation_mark_read_crossuser_probe_reaches_authorization_without_sending_a_body(monkeypatch):
    monkeypatch.setenv('RUNTIME_SOEV_BACKED', 'drive')
    route = 'POST /api/v1/threads/{thread_id}/read'
    entry = next(entry for entry in seeds.SURFACE['field'] if entry['route'] == route)
    fields = seeds.resolve_fields(seeds.SeedContext(Mock(), Mock(), seeds.SURFACE), entries=[entry])
    parameters = seeds.Resolved(fields=fields)
    assert crossuser._benign(route, parameters, seeds.SPEC) == {'headers': {'Content-Type': 'application/json'}}
    result = crossuser.Authorization(plane.Seeding())
    crossuser._check(
        result,
        route,
        plane.Outcome(200, True, '{"chat_id":"owner-thread","last_read_at":2}'),
        plane.Outcome(200, True, '{"chat_id":"owner-thread","last_read_at":1}'),
        (),
        admin_only=False,
        control=True,
        resource_ids=['owner-thread'],
    )
    assert {item['reason'] for item in result.violations} == {
        'second account received an object containing the seeded resource id',
        'second account accepted a write to the owner resource',
    }


def test_batched_view_reads_keep_owner_controls_and_cross_user_oracles(monkeypatch):
    monkeypatch.setenv('RUNTIME_SOEV_BACKED', 'drive')
    routes, _ = crossuser.targets()
    expected = {
        'GET /api/v1/chats/',
        'GET /api/v1/chats/list',
        'GET /api/v1/chats/{id}',
        'GET /api/v1/knowledge/{id}/files',
    }
    assert expected <= set(routes) & set(seeds.ordinary_targets())
    assert expected <= set(seeds.SURFACE['required_drive_routes'])
    assert not expected & set(crossuser.expected_refusals())


@pytest.mark.parametrize('kind', ['params', 'json', 'form'])
@pytest.mark.parametrize('mode', ['drive', 'waive'])
def test_field_owner_resolves_query_body_and_alias_references(monkeypatch, kind, mode):
    monkeypatch.setenv('RUNTIME_SOEV_BACKED', mode)
    surface = {
        'parameter': [{'key': 'resource', 'actor': 'user'}, {'key': 'alias', 'same_as': 'resource'}],
        'field': [
            {'route': 'POST /resource', kind: {'nested': ['prefix-{alias}']}, 'v2': {kind: {'id': '{resource}'}}}
        ],
    }
    assert crossuser._field_owner_key('POST /resource', surface) == 'user'
    assert crossuser._field_owner_key('POST /other', surface) is None


def test_meeting_audio_control_uses_meeting_owner_and_keeps_both_refusal_probes(offline, monkeypatch):
    monkeypatch.setenv('RUNTIME_SOEV_BACKED', 'drive')
    route = 'POST /api/v1/meetings/audio'
    spec = {'paths': {'/api/v1/meetings/audio': {'post': {}}}}
    identities, order = setup_pass(
        monkeypatch, spec, {'admin': (403, {'detail': 'refused'}), 'intruder': (403, {'detail': 'refused'})}
    )
    entry = next(item for item in seeds.SURFACE['field'] if item['route'] == route)
    ctx = seeds.SeedContext(None, None, seeds.SURFACE, values={'meeting': 'fictional-meeting'})
    parameters = seeds.Resolved(fields=seeds.resolve_fields(ctx, entries=[entry]))

    @contextmanager
    def fixtures(*args, **kwargs):
        yield parameters, (), seeds.SURFACE, {'admin': identities.admin, 'user': identities.user}

    monkeypatch.setattr(crossuser, 'own_fixtures', fixtures)
    result = crossuser.drive_crossuser(identities, spec=spec, payloads=[])
    assert [actor for actor, _, _, _ in order] == ['intruder', 'admin', 'user']
    assert all(kwargs['params']['thread_id'] == 'fictional-meeting' for _, _, _, kwargs in order)
    assert result.controls == {route: 200}
    assert not result.violations


@pytest.mark.parametrize(
    'status,body,expected',
    [
        (403, '', True),
        (401, '', False),
        (404, '', False),
        (200, '', False),
        (403, '{"detail":"Feature is unavailable"}', False),
        (403, ' ', False),
    ],
)
def test_head_refusal_requires_exact_status_and_empty_body(status, body, expected):
    route = 'HEAD /api/v1/terminals/{server_id}/{path}'
    declaration = crossuser.expected_refusals()[route]
    assert crossuser.refused_as_expected(declaration, status, body) is expected


@pytest.mark.parametrize(
    'body,expected',
    [
        ('', False),
        ('{"detail":"Feature is unavailable"}', True),
        ('{"detail":"Other feature is unavailable"}', False),
        ('{"detail":"Feature is unavailable today"}', False),
    ],
)
def test_terminal_get_still_requires_the_exact_detail(body, expected):
    declaration = crossuser.expected_refusals()['GET /api/v1/terminals/{server_id}/{path}']
    assert crossuser.refused_as_expected(declaration, 403, body) is expected
