from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

from . import plane, query
from .pass_support import crash_details, fresh_surface
from .test_shapes import needs_stack, offline, reply


def query_spec():
    return {
        'paths': {
            '/query': {
                'get': {
                    'parameters': [
                        {'in': 'query', 'name': name, 'schema': {'type': 'string'}} for name in ('start', 'end')
                    ]
                }
            }
        }
    }


def test_committed_query_surface():
    targets = query.query_targets()
    assert (len(targets), sum(map(len, targets.values()))) == (47, 94)


@pytest.mark.parametrize('full', [False, True])
def test_pairs_and_individuals_use_identical_deterministic_values(offline, full):
    actor = Mock()
    actor.request.side_effect = lambda *a, **k: reply()
    corpus = ['&?#\nλ', 'two', 'three']
    tally = query.drive_query_parameters(actor, {}, spec=query_spec(), payloads=corpus, full=full)
    calls = actor.request.call_args_list
    assert len(calls) == 3 * (len(corpus) if full else 1)
    for index in range(0, len(calls), 3):
        together, end, start = [call.kwargs['params'] for call in calls[index : index + 3]]
        assert together == {**start, **end}
        assert set(start) == {'start'} and set(end) == {'end'}
        prepared = requests.Request('GET', 'http://fixture.invalid/query', params=together).prepare()
        assert parse_qs(urlsplit(prepared.url).query) == {k: [v] for k, v in together.items()}
    assert tally.config_restore_verified
    assert tally.entered == {'GET /query': {200: len(calls)}}
    previous = [c.kwargs for c in calls]
    actor.reset_mock()
    query.drive_query_parameters(actor, {}, spec=query_spec(), payloads=corpus, full=full)
    assert [c.kwargs for c in actor.request.call_args_list] == previous


def test_environment_full_mode_and_single_parameter_is_not_duplicated(offline, monkeypatch):
    spec = query_spec()
    spec['paths']['/query']['get']['parameters'].pop()
    actor = Mock()
    actor.request.side_effect = lambda *a, **k: reply()
    monkeypatch.setenv('ATTACK_FULL_CORPUS', '1')
    query.drive_query_parameters(actor, {}, spec=spec, payloads=['a', 'b'])
    assert actor.request.call_count == 2


def test_query_pass_entries_are_local_and_5xx_gate_is_independent(offline):
    plane.record('GET /query', 200, {}, pass_name='shapes')
    actor = Mock()
    actor.request.side_effect = lambda *a, **k: reply(422, {'detail': []})
    tally = query.drive_query_parameters(actor, {}, spec=query_spec(), payloads=['bad'])
    assert tally.unentered == {'GET /query': 422}
    plane.record('GET /query', 500, 'query failure', pass_name='query')
    plane.record('GET /query', 200, {}, pass_name='query')
    with pytest.raises(AssertionError, match='query failure'):
        test_live_query_has_its_own_5xx_assertion(tally)


def test_destructive_query_uses_throwaway_and_follows_reads(offline, monkeypatch):
    spec = query_spec()
    spec['paths']['/api/v1/auths/signout'] = {'post': spec['paths']['/query']['get']}
    actor, disposable = Mock(), Mock()
    actor.request.side_effect = disposable.request.side_effect = lambda *a, **k: reply()
    order = []
    actor.request.side_effect = lambda *a, **k: order.append('owner') or reply()
    disposable.request.side_effect = lambda *a, **k: order.append('throwaway') or reply()
    factory = Mock(return_value=disposable)
    monkeypatch.setattr(plane, '_throwaway', factory)
    query.drive_query_parameters(actor, {}, spec=spec, payloads=['x'])
    assert order == ['owner'] * 3 + ['throwaway'] * 3
    assert factory.call_count == disposable.close.call_count == 3


@pytest.fixture(scope='module')
def live_query():
    with fresh_surface() as (identities, parameters):
        yield query.drive_query_parameters(identities.admin, parameters)


@needs_stack
def test_live_query_has_its_own_5xx_assertion(live_query):
    assert not live_query.crashes, crash_details(live_query)


@needs_stack
def test_live_query_reports_its_own_reach(live_query, record_property):
    assert live_query.statuses.keys() | live_query.skipped.keys() == set(query.query_targets())
    assert live_query.config_restore_verified
    record_property('query_unentered', live_query.unentered)
    assert live_query.entered, 'No query request entered a handler'
