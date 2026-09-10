import json
import os
import subprocess
import sys
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

from . import configuration, plane, seeds
from .hits_path import hits_path


def response(status=200, body=None):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(body).encode()
    result._content_consumed = True
    return result


@pytest.fixture(autouse=True)
def isolated_unit_test(request, monkeypatch, tmp_path):
    if 'live_seeding' in request.fixturenames:
        return
    monkeypatch.setattr(plane, '_PASSES', {})
    monkeypatch.setattr(plane, 'preserve_configuration', lambda *a, **kw: nullcontext())
    monkeypatch.setenv('ROUTE_HITS_PATH', str(tmp_path / 'hits.json'))
    monkeypatch.delenv('ATTACK_FULL_CORPUS', raising=False)

    def no_http(*args, **kwargs):
        pytest.fail('Offline plane tests must not send HTTP')

    monkeypatch.setattr(requests.Session, 'request', no_http)


@pytest.mark.parametrize(
    ('fields', 'expected'),
    [
        ([], {}),
        (['a'], {'a': 'hostile'}),
        (['a.b'], {'a': {'b': 'hostile'}}),
        (['a[]'], {'a': ['hostile']}),
        (['a[].b'], {'a': [{'b': 'hostile'}]}),
        (['a[].b', 'a[].c'], {'a': [{'b': 'hostile', 'c': 'hostile'}]}),
        (['a.b[].c[]'], {'a': {'b': [{'c': ['hostile']}]}}),
        (['[].a', '[].b'], [{'a': 'hostile', 'b': 'hostile'}]),
        (['a', 'a.b'], {'a': {'b': 'hostile'}}),
        (['a[]', 'a[].b'], {'a': [{'b': 'hostile'}]}),
        (['a', 'a[].b'], {'a': [{'b': 'hostile'}]}),
    ],
)
def test_body_shapes_and_union_container_precedence(fields, expected):
    assert plane._body_for(fields, 'hostile') == expected
    assert plane._body_for(list(reversed(fields)), 'hostile') == expected


def test_body_builder_can_plant_each_of_the_2757_derived_fields():
    fields = plane.writable_string_fields(seeds.SPEC)
    assert (len(fields), sum(map(len, fields.values()))) == (252, 2757)
    for names in fields.values():
        for name in names:
            assert plane._contains(plane._body_for([name], 'hostile'), name, 'hostile'), name


@pytest.mark.parametrize(
    ('status', 'body', 'entered'),
    [
        (200, {'ok': True}, True),
        (201, {'rejectedCount': 63}, True),
        (204, '', True),
        (200, {'requires_2fa': True}, False),
        (200, {'requires_2fa_setup': True}, False),
        (302, {}, False),
        (400, {'detail': 'Application refusal'}, True),
        (401, {'detail': 'Not authenticated'}, False),
        (401, {'detail': 'Invalid token'}, False),
        (401, {'detail': '401 Unauthorized'}, False),
        (403, {'detail': '2FA verification required'}, False),
        (403, {'detail': 'Application refusal'}, True),
        (404, {'detail': 'Resource missing inside the handler'}, False),
        (405, {}, False),
        (422, {'detail': [{'loc': ['body', 'name'], 'msg': 'Required'}]}, False),
        (422, {'detail': 'Application refusal'}, True),
        (429, {'detail': 'API rate limit exceeded'}, False),
        (500, 'Internal Server Error', True),
    ],
)
def test_record_uses_client_classification(status, body, entered):
    route = 'POST /synthetic'
    assert plane.record(route, status, body) is entered
    tally = plane._PASSES['manual']
    assert tally.statuses == {route: {status: 1}}
    assert (route in tally.entered) is entered
    assert (route in plane.unentered_routes('manual')) is not entered


def test_record_delegates_original_response_and_preserves_bearer_probe(monkeypatch):
    result = response(401, {'detail': '401 Unauthorized'})
    result._attack_bearer_valid = True
    classifier = Mock(wraps=plane.transport.entered_the_handler)
    monkeypatch.setattr(plane.transport, 'entered_the_handler', classifier)
    assert plane.record('GET /guarded', 401, result)
    classifier.assert_called_once_with(result)


def test_long_validation_body_is_classified_before_diagnostic_truncation():
    body = json.dumps({'detail': [{'msg': 'x' * 1000}]})
    assert not plane.record('POST /validate', 422, body)


def test_404_never_enters_any_pass_or_hits_file():
    for name in ('seeding', 'drive'):
        assert not plane.record('GET /missing', 404, {}, pass_name=name)
        assert plane.unentered_routes(name) == {'GET /missing': 404}
    plane.flush_hits()
    data = json.loads(hits_path().read_text())
    assert data['hits'] == []
    assert data['passes']['drive']['statuses'] == {'GET /missing': {'404': 1}}


def test_later_pass_cannot_vouch_for_seeding_and_later_entry_clears_only_its_pass():
    plane.record('POST /item', 422, {'detail': []}, pass_name='seeding')
    plane.record('POST /item', 200, {}, pass_name='drive')
    assert plane.unentered_routes('seeding') == {'POST /item': 422}
    assert plane.unentered_routes('drive') == {}
    plane.record('POST /item', 400, {'detail': 'Business refusal'}, pass_name='seeding')
    assert plane.unentered_routes('seeding') == {}


def test_artefact_contains_pass_evidence_without_importing_corpus_for_its_path(tmp_path):
    plane.record('GET /ok', 200, {}, pass_name='drive')
    plane.record('GET /blocked', 401, {'detail': 'Not authenticated'}, pass_name='drive')
    plane.flush_hits()
    data = json.loads(hits_path().read_text())
    assert data['hits'] == ['GET /ok']
    assert data['passes']['drive']['unentered'] == {'GET /blocked': 401}
    module = Path(plane.__file__).with_name('hits_path.py')
    script = """
import runpy, sys
module = runpy.run_path(sys.argv[1])
assert str(module['hits_path']()) == sys.argv[2]
assert 'hostile_corpus' not in sys.modules
assert 'openapi_surface' not in sys.modules
assert 'requests' not in sys.modules
"""
    subprocess.run([sys.executable, '-I', '-S', '-c', script, str(module), str(hits_path())], check=True)


def test_hits_path_defaults_to_tempfile(monkeypatch):
    import tempfile

    monkeypatch.delenv('ROUTE_HITS_PATH')
    assert hits_path() == Path(tempfile.gettempdir()) / 'route-hits.json'


def tiny_spec(routes, fields=('a', 'b', 'c')):
    spec = {'paths': {}}
    for route in routes:
        method, path = route.split(' ', 1)
        spec['paths'].setdefault(path, {})[method.lower()] = {
            'requestBody': {
                'content': {
                    'application/json': {
                        'schema': {
                            'type': 'object',
                            'properties': {name: {'type': 'string'} for name in fields},
                        }
                    }
                }
            },
        }
    return spec


def fake_client(status=200):
    client = Mock(spec=plane.transport.AttackClient)
    client.request.side_effect = lambda *args, **kwargs: response(status)
    return client


@pytest.mark.parametrize('status', [201, 400, 422, 500])
def test_all_fields_single_on_rejection_and_unconditional_leave_one_out(status):
    client = fake_client(status)
    result = plane.seed_every_writable_field(client, {}, spec=tiny_spec(['POST /item']), payloads=['hostile'])
    bodies = [call.kwargs['json'] for call in client.request.call_args_list]
    expected = [{'a': 'hostile', 'b': 'hostile', 'c': 'hostile'}]
    if status >= 400:
        expected += [{name: 'hostile'} for name in ('a', 'b', 'c')]
    expected += [{'b': 'hostile', 'c': 'hostile'}, {'a': 'hostile', 'c': 'hostile'}, {'a': 'hostile', 'b': 'hostile'}]
    assert bodies == expected
    assert result.statuses == {'POST /item': {status: len(expected)}}
    assert bool(result.crashes) is (status == 500)


def test_leave_one_out_runs_even_for_one_field_and_success():
    client = fake_client(201)
    plane.seed_every_writable_field(client, {}, spec=tiny_spec(['POST /item'], ['a']), payloads=['hostile'])
    assert [call.kwargs['json'] for call in client.request.call_args_list] == [{'a': 'hostile'}, {}]


def test_union_scalar_is_driven_individually_even_if_structured_body_succeeds(monkeypatch):
    monkeypatch.setattr(plane, 'writable_string_fields', lambda spec: {'POST /item': ['a', 'a.b']})
    client = fake_client(201)
    plane.seed_every_writable_field(client, {}, payloads=['hostile'])
    assert [call.kwargs['json'] for call in client.request.call_args_list] == [
        {'a': {'b': 'hostile'}},
        {'a': 'hostile'},
        {'a': {'b': 'hostile'}},
        {'a': 'hostile'},
    ]


def test_sampling_is_route_seeded_field_stable_and_full_mode_covers_cross_product():
    payloads = tuple(f'payload-{i}' for i in range(63))
    fields = ['a', 'b', 'c']
    first = list(plane._payload_batches('POST /one', fields, payloads, full=False))
    assert first == list(plane._payload_batches('POST /one', fields[::-1], payloads, full=False))
    assert first != list(plane._payload_batches('POST /two', fields, payloads, full=False))
    assert first[0].keys() == set(fields)
    assert set(first[0].values()) <= set(payloads)
    full = list(plane._payload_batches('POST /one', fields, payloads, full=True))
    assert len(full) == 63
    assert all({batch[name] for batch in full} == set(payloads) for name in fields)


def test_sampling_is_identical_across_process_hash_seeds():
    script = """
import json, sys
sys.path.insert(0, sys.argv[1])
from attack.plane import _payload_batches
print(json.dumps(list(_payload_batches('POST /item', ['a', 'b'], list(range(63)), full=False))))
"""
    outputs = [
        subprocess.check_output(
            [sys.executable, '-c', script, str(Path(plane.__file__).parents[1])],
            env={**os.environ, 'PYTHONHASHSEED': seed},
            text=True,
        )
        for seed in ('1', '42')
    ]
    assert outputs[0] == outputs[1]


def test_full_environment_changes_content_breadth_but_not_route_coverage(monkeypatch):
    spec = tiny_spec(['POST /one', 'POST /two'], ['a', 'b'])
    sampled = plane.seed_every_writable_field(fake_client(), {}, spec=spec, payloads=['x', 'y'])
    monkeypatch.setenv('ATTACK_FULL_CORPUS', '1')
    full = plane.seed_every_writable_field(fake_client(), {}, spec=spec, payloads=['x', 'y'])
    assert sampled.expected == full.expected == sampled.entered.keys() == full.entered.keys()
    assert sampled.statuses == {route: {200: 3} for route in sampled.expected}
    assert full.statuses == {route: {200: 6} for route in full.expected}


def test_empty_corpus_is_an_error():
    with pytest.raises(ValueError, match='empty corpus'):
        plane.seed_every_writable_field(fake_client(), {}, spec=tiny_spec(['POST /item']), payloads=[])


def test_order_reads_then_writes_then_deletes_then_destructive():
    destructive = 'POST /api/v1/auths/signout'
    routes = ['DELETE /a', destructive, 'POST /a', 'PUT /a', 'PATCH /a', 'GET /z', 'HEAD /z', 'OPTIONS /z', 'TRACE /z']
    assert plane.operations(tiny_spec(routes)) == [
        'GET /z',
        'HEAD /z',
        'OPTIONS /z',
        'TRACE /z',
        'PATCH /a',
        'POST /a',
        'PUT /a',
        'DELETE /a',
        destructive,
    ]
    assert len(plane.operations()) == 662
    assert set(plane.operations()[-len(seeds.DESTRUCTIVE) :]) == seeds.DESTRUCTIVE.keys()


@pytest.mark.parametrize('pass_name', ['seeding', 'drive'])
def test_destructive_requests_run_last_with_a_new_throwaway_each_time(monkeypatch, pass_name):
    destructive = 'POST /api/v1/auths/update/password'
    spec = tiny_spec([destructive, 'POST /ordinary'], ['a', 'b'])
    calls, throwaways = [], []
    main = fake_client()

    def send(actor, method, path, **kwargs):
        calls.append((actor, path, kwargs))
        if path == '/api/v1/auths/update/password':
            assert actor is not main
            assert sum(previous is actor for previous, _, _ in calls) == 1
        return response(400)

    def create(admin):
        assert admin is main
        actor = fake_client()
        actor.request.side_effect = lambda *args, **kwargs: send(actor, *args, **kwargs)
        throwaways.append(actor)
        return actor

    main.request.side_effect = lambda *args, **kwargs: send(main, *args, **kwargs)
    monkeypatch.setattr(plane, '_throwaway', create)
    if pass_name == 'seeding':
        plane.seed_every_writable_field(main, {}, spec=spec, payloads=['x', 'y'], full=True)
        assert len(throwaways) == 10  # all + two singles + two omissions, for each payload
    else:
        plane.drive_every_route(main, {}, spec=spec)
        assert len(throwaways) == 1
    first_destructive = next(i for i, (_, path, _) in enumerate(calls) if path != '/ordinary')
    assert all(path != '/ordinary' for _, path, _ in calls[first_destructive:])
    for actor in throwaways:
        actor.close.assert_called_once_with()


def test_throwaway_creation_uses_existing_client_session_validation(monkeypatch):
    admin = fake_client()
    admin.base_url = 'http://stack'
    actor = Mock()
    actor.authenticate.return_value = actor
    monkeypatch.setattr(plane.transport, 'AttackClient', Mock(return_value=actor))
    assert plane._throwaway(admin) is actor
    call = admin.request.call_args
    assert call.args == ('POST', '/api/v1/auths/add')
    assert call.kwargs['json']['role'] == 'admin'
    assert call.kwargs['json']['email'].startswith('attack-plane-throwaway-')
    assert actor.authenticate.call_args.args[1] == call.kwargs['json']['email']
    assert actor.authenticate.call_args.kwargs == {'role': 'admin'}


def test_path_fill_is_route_scoped_and_escapes_slashes():
    path = '/api/v1/chats/{id}'
    assert plane._fill(path, {(path, 'id'): 'a/b?c#d'}) == '/api/v1/chats/a%2Fb%3Fc%23d'
    with pytest.raises(KeyError):
        plane._fill(path, {('/api/v1/knowledge/{id}', 'id'): 'wrong-resource'})


def test_unseedable_is_reported_without_http_or_fabricated_status():
    route = 'DELETE /api/v1/auths/oauth/sessions/{provider}'
    client = fake_client()
    assert plane.drive_every_route(client, {}, spec=tiny_spec([route])) == {}
    client.request.assert_not_called()
    assert plane.unentered_routes('drive') == {route: None}
    assert (
        plane._PASSES['drive'].skipped[route] == seeds.parameter_for(route.split(' ', 1)[1], 'provider')['unseedable']
    )
    assert plane._PASSES['drive'].statuses == {}


def test_transport_failure_flushes_only_completed_responses():
    client = fake_client()
    client.request.side_effect = [response(200), requests.ConnectionError('stack down')]
    with pytest.raises(requests.ConnectionError):
        plane.drive_every_route(client, {}, spec=tiny_spec(['GET /a', 'GET /b']))
    data = json.loads(hits_path().read_text())
    assert data['hits'] == ['GET /a']
    assert data['passes']['drive']['statuses'] == {'GET /a': {'200': 1}}
    assert data['passes']['drive']['unentered'] == {'GET /b': None}


def test_repeated_pass_does_not_inherit_old_entries():
    spec = tiny_spec(['GET /item'])
    plane.drive_every_route(fake_client(), {}, spec=spec)
    plane.drive_every_route(fake_client(404), {}, spec=spec)
    assert plane.unentered_routes('drive') == {'GET /item': 404}
    assert json.loads(hits_path().read_text())['hits'] == []


def test_drive_uses_returned_status_for_crashes_and_html_reflection():
    client = fake_client()
    html = response(500, plane.REFLECTION_PROBE)
    html.headers['Content-Type'] = 'text/html; charset=utf-8'
    client.request.side_effect = [html]
    outcomes = plane.drive_every_route(client, {}, spec=tiny_spec(['GET /item']))
    assert outcomes['GET /item'].status == 500
    assert outcomes['GET /item'].entered
    assert outcomes['GET /item'].reflected
    assert plane._PASSES['drive'].crashes == {'GET /item': html.text}


needs_stack = pytest.mark.skipif(not os.getenv('ATTACK_BASE_URL'), reason='requires ATTACK_BASE_URL and the CI stack')


def _stop_task(admin, parameters):
    path = '/api/tasks/stop/{task_id}'
    result = admin.request('POST', plane._fill(path, parameters))
    assert result.status_code == 200, f'Seed task cleanup: HTTP {result.status_code}'


@pytest.fixture(scope='module')
def live_seeding():
    from .identities import ensure_identities

    plane._PASSES.clear()
    plane.flush_hits()  # A new invocation cannot inherit a stale artefact.
    identities = ensure_identities()
    parameters = None
    try:
        parameters = seeds.resolve_parameters(identities.admin, admin=identities.admin)
        yield plane.seed_every_writable_field(identities.admin, parameters)
    finally:
        try:
            if parameters is not None:
                _stop_task(identities.admin, parameters)
        finally:
            identities.close()


@pytest.fixture(scope='module')
def live_drive(live_seeding):
    from .identities import ensure_identities

    identities = ensure_identities()
    parameters = None
    try:
        parameters = seeds.resolve_parameters(identities.admin, admin=identities.admin)
        yield plane.drive_every_route(identities.admin, parameters)
    finally:
        try:
            if parameters is not None:
                _stop_task(identities.admin, parameters)
        finally:
            identities.close()


@needs_stack
def test_live_seeding_has_its_own_5xx_assertion(live_seeding):
    assert not live_seeding.crashes, f'Seeding found server errors:\n{plane.crash_report(live_seeding)}'


@needs_stack
def test_live_seeding_reports_its_own_reach(live_seeding, record_property):
    accounted = live_seeding.statuses.keys() | live_seeding.skipped.keys() | live_seeding.unanswered.keys()
    assert accounted == live_seeding.expected
    record_property('seeding_reached', len(live_seeding.entered))
    record_property('seeding_unentered', len(live_seeding.unentered))
    record_property('seeding_unanswered', len(live_seeding.unanswered))
    record_property('seeding_config_unverified', len(live_seeding.config_unverified))
    assert live_seeding.entered, 'No seeding request entered a handler'


@needs_stack
def test_live_drive_has_its_own_5xx_assertion(live_drive):
    crashes = {route: outcome for route, outcome in live_drive.items() if outcome.status >= 500}
    assert not crashes, f'Drive found server errors:\n{plane.drive_report(live_drive)}'


def timing_out_client():
    client = Mock(spec=plane.transport.AttackClient)
    client.request.side_effect = requests.exceptions.ReadTimeout('Read timed out. (read timeout=60)')
    return client


def test_a_route_that_never_answers_is_measured_rather_than_ending_the_pass():
    # A hosted runner is slower than a laptop, so a route that merely takes too
    # long must not abort the pass: everything after it would go undriven while
    # the job still reported a result. The route is recorded, never entered.
    outcome = plane._drive_response(timing_out_client(), 'POST /slow', '/slow', 'drive')
    assert outcome.timed_out and not outcome.entered
    tally = plane._PASSES['drive']
    assert 'POST /slow' in tally.expected
    assert 'POST /slow' in tally.unanswered
    assert 'POST /slow' not in tally.entered
    assert 'POST /slow' not in tally.accepted
    assert 'POST /slow' not in tally.statuses


def test_a_dropped_connection_on_a_driven_route_stays_fatal():
    # The opposite of a timeout, deliberately. The client has already spent its
    # retry budget on the drop; a write may have committed unseen, and a stack
    # that has gone away is not something to keep driving hundreds of routes
    # over while reporting them as merely unanswered.
    client = Mock(spec=plane.transport.AttackClient)
    client.request.side_effect = requests.exceptions.ConnectionError('Connection aborted, RemoteDisconnected')
    with pytest.raises(requests.exceptions.ConnectionError):
        plane._drive_response(client, 'POST /silent', '/silent', 'drive')


def test_a_configuration_guard_that_loses_its_connection_leaves_the_pass_unverified(monkeypatch):
    # The guard is the exception: losing its snapshot is losing evidence, and
    # ending the pass there would lose every route after it as well. CI drops
    # this connection where a workstation merely runs slow.
    monkeypatch.setattr(plane, 'preserve_configuration', configuration.preserve_configuration)
    monkeypatch.setattr(
        configuration,
        'snapshot',
        Mock(side_effect=requests.exceptions.ConnectionError('Connection aborted, RemoteDisconnected')),
    )
    client = fake_client()
    tally = plane.seed_every_writable_field(client, {}, spec=tiny_spec(['POST /item']), payloads=['hostile'])
    assert client.request.called, 'the pass stopped instead of driving the route unguarded'
    assert 'POST /item' in tally.config_unverified
    assert not tally.config_restore_verified


def test_a_timed_out_route_is_never_counted_as_covered():
    plane._drive_response(timing_out_client(), 'POST /slow', '/slow', 'drive')
    assert 'POST /slow' in plane.unentered_routes('drive')
    assert 'POST /slow' in plane.unentered_routes()


def test_a_timed_out_route_is_reported_by_the_reach_control():
    # The reach control requires every expected route to be accounted for. A
    # timeout is a third outcome beside a status and an unseedable skip.
    plane._drive_response(timing_out_client(), 'POST /slow', '/slow', 'seeding')
    tally = plane._PASSES['seeding']
    assert tally.statuses.keys() | tally.skipped.keys() | tally.unanswered.keys() == tally.expected


def test_a_timed_out_seed_does_not_spend_the_rest_of_its_payload_batches():
    client = timing_out_client()
    plane._seed_route(client, 'POST /slow', '/slow', ['a', 'b'], ('payload',), False)
    # One request per batch, not the follow-up single-field and leave-one-out
    # drives: they would each pay the full timeout for no new measurement.
    assert client.request.call_count == 1


def test_a_configuration_guard_that_times_out_leaves_the_pass_unverified(monkeypatch):
    # The guard is instrumentation, not a measurement. Losing it for one route
    # must not end the pass, and must not let the pass claim it verified config:
    # config_restore_verified is what later readers trust, so it has to mean
    # "checked", never "attempted".
    monkeypatch.setattr(plane, 'preserve_configuration', configuration.preserve_configuration)
    monkeypatch.setattr(
        configuration,
        'snapshot',
        Mock(side_effect=requests.exceptions.ReadTimeout('Read timed out. (read timeout=300)')),
    )
    client = fake_client()
    tally = plane.seed_every_writable_field(client, {}, spec=tiny_spec(['POST /item']), payloads=['hostile'])
    assert client.request.called, 'the pass stopped instead of driving the route unguarded'
    assert 'POST /item' in tally.config_unverified
    assert not tally.config_restore_verified


def test_a_pass_whose_guards_all_held_still_claims_verification(monkeypatch, tmp_path):
    # The negative control for the above: without it, always-False would pass
    # and the assertion would prove nothing.
    monkeypatch.setenv('ATTACK_CONFIG_STATE_DIR', str(tmp_path / 'state'))
    monkeypatch.setattr(plane, 'preserve_configuration', configuration.preserve_configuration)
    monkeypatch.setattr(configuration, 'snapshot', Mock(return_value={}))
    monkeypatch.setattr(configuration, 'restore', Mock())
    client = fake_client()
    client.base_url = 'http://offline'
    tally = plane.seed_every_writable_field(client, {}, spec=tiny_spec(['POST /item']), payloads=['hostile'])
    assert not tally.config_unverified
    assert tally.config_restore_verified


def test_the_5xx_report_is_the_gate_message_itself_not_pytest_explanation():
    # This control used to read str(AssertionError), which embeds pytest's own
    # repr of the tally -- so it passed under -q and failed under -v, which is
    # what CI runs. Assert on the report the gate builds.
    tally = plane.Seeding()
    plane.record('POST /broken', 422, 'Earlier validation refusal', pass_name='seeding')
    report = plane.crash_report(tally)
    assert report == ''
    tally.crashes['POST /broken'] = 'boom'
    tally.statuses['POST /broken'] = {422: 1, 500: 1}
    assert '500' in plane.crash_report(tally)
    assert '422' not in plane.crash_report(tally)


@pytest.mark.parametrize('pass_name', ['seeding', 'drive'])
def test_5xx_gate_message_includes_every_route_status_and_actionable_body(pass_name):
    bodies = {
        'POST /broken': 'x' * 450 + ' model_type: expected a dictionary',
        'DELETE /proxy/{server_id}/{path}': '<html>Unsupported method DELETE</html>',
        'GET /empty-error': '',
    }
    outcomes = {}
    for route, status in zip(bodies, [500, 501, 503], strict=True):
        plane.record(route, 422, 'Earlier validation refusal', pass_name=pass_name)
        result = response(status)
        result._content = bodies[route].encode()
        client = fake_client()
        client.request.side_effect = [result]
        outcomes[route] = plane._drive_response(client, route, route.split(' ', 1)[1], pass_name)
        # A later success must not clear the seeding failure or hide its status.
        plane.record(route, 200, 'Later success', pass_name=pass_name)
    with pytest.raises(AssertionError):
        if pass_name == 'seeding':
            test_live_seeding_has_its_own_5xx_assertion(plane._PASSES[pass_name])
        else:
            test_live_drive_has_its_own_5xx_assertion(outcomes)
    # The gate's own message, not pytest's explanation of the failed assert:
    # that explanation embeds a repr of the tally, so reading it back asserted
    # something else entirely and flipped with -q/-v.
    message = plane.crash_report(plane._PASSES[pass_name]) if pass_name == 'seeding' else plane.drive_report(outcomes)
    for route, status in zip(bodies, [500, 501, 503], strict=True):
        assert route in message
        assert str(status) in message
        assert repr(bodies[route]) in message
    assert '422' not in message
    assert 'Earlier validation refusal' not in message
    assert 'Later success' not in message


@needs_stack
def test_live_drive_reports_its_own_reach(live_drive, record_property):
    tally = plane._PASSES['drive']
    assert live_drive.keys() | tally.skipped.keys() == set(plane.operations())
    record_property('drive_reached', len(tally.entered))
    record_property('drive_unentered', len(tally.unentered))
    record_property('drive_unanswered', len(tally.unanswered))
    record_property('drive_config_unverified', len(tally.config_unverified))
    record_property('combined_unentered', len(plane.unentered_routes()))
    assert tally.entered, 'No drive request entered a handler'


@needs_stack
def test_live_drive_does_not_reflect_corpus_in_html(live_drive):
    reflected = [route for route, outcome in live_drive.items() if outcome.reflected]
    assert not reflected, reflected
