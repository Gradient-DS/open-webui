import json
import signal
import time
from unittest.mock import Mock

import pytest
import requests

from . import model_path, plane, test_shapes
from .pass_support import crash_details, fresh_surface
from .test_shapes import needs_stack

offline = test_shapes.offline

CHAT = 'POST /api/v1/chat/completions'
PARAMETERS = {('/api/v1/analytics/models/{model_id}/chats', 'model_id'): 'live-model'}


def response(chunks, *, status=200, content_type='text/event-stream'):
    result = requests.Response()
    result.status_code = status
    result.headers['Content-Type'] = content_type
    result.encoding = 'utf-8'
    result.iter_content = Mock(return_value=iter(chunks))
    result.close = Mock()
    return result


def spec():
    schema = {'type': 'object', 'properties': {'model': {'type': 'string'}, 'prompt': {'type': 'string'}}}
    return {
        'paths': {
            '/api/v1/chat/completions': {
                'post': {'requestBody': {'content': {'application/json': {'schema': schema}}}}
            },
            '/api/v1/tasks/title/completions': {'post': {}},
            '/unrelated/completions': {'post': {}},
            '/api/v1/tasks/config': {'get': {}},
        }
    }


def drive_one(offline, chunks, **kwargs):
    result = model_path.ModelRun(plane.Seeding(expected={CHAT}))
    plane._PASSES['model_path'] = result.tally
    actor = Mock()
    actor.request.return_value = response(chunks, **kwargs)
    model_path._drive(actor, CHAT, {}, result, probe='control', chunk_timeout=0.2, wall_timeout=1)
    return result, actor


def test_committed_model_surface():
    routes = model_path.targets()
    assert len(routes) == 9
    assert CHAT in routes
    assert all(route.startswith('POST /api/v1/tasks/') for route in routes if route != CHAT)


@pytest.mark.parametrize(
    'body,terminal',
    [
        ('data: [DONE]\n\n', True),
        ('data: [DONE]\r\n\r\n', True),
        ('data: {"done":true}\n\n', True),
        ('event: response.completed\ndata: {"type":"response.completed"}\n\n', True),
        ('data: {"choices":[{"finish_reason":"stop"}]}\n\n', False),
        ('data: [DONE]\n', False),
        (': heartbeat\n\n', False),
        ('data: {"delta":"[DONE]"}\n\n', False),
        ('', False),
    ],
)
def test_terminal_events_are_dispatched_protocol_events_not_status_or_substrings(body, terminal):
    assert model_path.stream_events(body)[0] is terminal


def test_drain_continues_past_terminal_and_preserves_complete_utf8_body(offline):
    body = 'data: {"delta":"λ"}\n\ndata: [DONE]\n\n: tail\n\n'.encode()
    result, actor = drive_one(offline, [body[i : i + 1] for i in range(len(body))])
    item = result.responses[0]
    assert item['terminal'] and item['bytes'] == len(body) and not item['errors']
    assert result.tally.statuses == {CHAT: {200: 1}}
    assert result.tally.entered == {CHAT: {200: 1}}
    assert actor.request.call_args.kwargs['stream'] is True
    assert actor.request.call_args.kwargs['timeout'] == (1, 0.2)
    assert actor.request.return_value.text.endswith(': tail\n\n')
    actor.request.return_value.close.assert_called_once()


@pytest.mark.parametrize('chunks', [[], [b'data: {"delta":"partial"}\n\n'], [b'data: [DONE]\n']])
def test_200_with_empty_or_unterminated_stream_is_a_failed_body(offline, chunks):
    result, _ = drive_one(offline, chunks)
    assert result.responses[0]['status'] == 200
    assert not result.responses[0]['terminal']
    with pytest.raises(AssertionError, match='truncated'):
        test_live_model_streams_finish_without_body_failures(result)


@pytest.mark.parametrize('status', [200, 500])
def test_midstream_exception_after_terminal_is_still_failure_and_response_is_closed(offline, status):
    def chunks():
        yield b'data: [DONE]\n\n'
        raise requests.exceptions.ChunkedEncodingError('broken framing')

    result, actor = drive_one(offline, chunks(), status=status)
    assert 'broken framing' in result.responses[0]['errors'][0]
    assert result.tally.statuses == {CHAT: {status: 1}}
    assert bool(result.tally.crashes) is (status >= 500)
    actor.request.return_value.close.assert_called_once()


def test_error_events_and_malformed_data_fail_even_with_a_terminal(offline):
    result, _ = drive_one(offline, [b'data: invalid\n\ndata: {"error":"generator failed"}\n\ndata: [DONE]\n\n'])
    assert result.responses[0]['terminal']
    assert len(result.responses[0]['errors']) == 2


@pytest.mark.parametrize(
    'body,output',
    [
        ({'choices': [{'message': {'content': 'done'}, 'finish_reason': 'stop'}]}, True),
        ({'detail': 'Title generation is disabled'}, False),
    ],
)
def test_json_tasks_are_distinguished_from_successful_model_execution(offline, body, output):
    result, _ = drive_one(offline, [json.dumps(body).encode()], content_type='application/json')
    item = result.responses[0]
    assert item['json_response'] and not item['streamed'] and not item['terminal']
    assert item['model_output'] is output
    assert not item['errors']


@pytest.mark.parametrize('kind', ['stalled', 'trickle', 'headers'])
def test_real_blocking_iterator_and_slow_trickle_obey_both_deadlines(offline, kind):
    previous = signal.getsignal(signal.SIGALRM)
    result = model_path.ModelRun(plane.Seeding(expected={CHAT}))
    plane._PASSES['model_path'] = result.tally

    def chunks():
        while True:
            time.sleep(0.2 if kind == 'stalled' else 0.005)
            yield b': heartbeat\n\n'

    actor = Mock()
    reply = response(chunks())
    if kind == 'headers':

        def headers(*a, **kw):
            time.sleep(0.5)
            return reply

        actor.request.side_effect = headers
    else:
        actor.request.return_value = reply
    started = time.monotonic()
    model_path._drive(actor, CHAT, {}, result, probe='control', chunk_timeout=0.03, wall_timeout=0.08)
    assert time.monotonic() - started < 0.4
    assert ('per-chunk' if kind == 'stalled' else 'wall-clock') in result.responses[0]['errors'][0]
    assert signal.getsignal(signal.SIGALRM) == previous
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
    if kind == 'headers':
        assert not result.tally.statuses, 'No synthetic HTTP status when no headers arrived'
    else:
        reply.close.assert_called_once()


@pytest.mark.parametrize('full', [False, True])
def test_fields_content_controls_sampling_full_mode_and_pass_local_entries(offline, monkeypatch, full):
    monkeypatch.setenv('ATTACK_FULL_CORPUS', '1' if full else '0')
    actor = Mock()
    actor.request.side_effect = lambda *a, **kw: response([b'data: [DONE]\n\n'])
    plane.record(CHAT, 200, {}, pass_name='crossuser')
    result = model_path.drive_model_paths(actor, PARAMETERS, spec=spec(), payloads=['one', 'two'])
    calls = actor.request.call_args_list
    assert result.tally.expected == {CHAT, 'POST /api/v1/tasks/title/completions'}
    assert len(calls) == 2 + 6 * (2 if full else 1)
    for item, call in zip(result.responses, calls, strict=True):
        body = call.kwargs['json']
        assert body['stream'] is True
        if item['probe'] in {'control', 'content', 'prompt'}:
            assert body['model'] == 'live-model'
        if item['probe'] == 'content':
            assert body['messages'][0]['content'] in {'one', 'two'}
    actor.reset_mock()
    again = model_path.drive_model_paths(actor, PARAMETERS, spec=spec(), payloads=['one', 'two'])
    assert actor.request.call_args_list == calls
    assert again.tally is not result.tally
    assert again.tally.statuses == result.tally.statuses
    assert again.tally.config_restore_verified


def test_validation_rejection_does_not_borrow_entries_and_5xx_assertion_is_independent(offline):
    plane.record(CHAT, 200, {}, pass_name='shapes')
    result, _ = drive_one(offline, [b'{"detail":[]}'], status=422, content_type='application/json')
    assert not result.tally.entered
    plane.record(CHAT, 500, 'model crash', pass_name='model_path')
    plane.record(CHAT, 200, {}, pass_name='model_path')
    with pytest.raises(AssertionError, match='model crash'):
        test_live_model_path_has_its_own_5xx_assertion(result)


@pytest.fixture(scope='module')
def live_model_paths():
    with fresh_surface() as (identities, parameters):
        yield model_path.drive_model_paths(identities.admin, parameters)


@needs_stack
def test_live_model_path_has_its_own_5xx_assertion(live_model_paths):
    assert not live_model_paths.tally.crashes, crash_details(live_model_paths.tally)


@needs_stack
def test_live_model_streams_finish_without_body_failures(live_model_paths):
    assert not live_model_paths.tally.body_failures, live_model_paths.tally.body_failures


@needs_stack
def test_live_model_controls_produce_output(live_model_paths):
    controls = [item for item in live_model_paths.responses if item['probe'] == 'control']
    assert {item['route'] for item in controls} == set(model_path.targets())

    # The control exists to prove this pass reached the model path at all. If it is
    # wrong or weak every other assertion in the module passes vacuously, so report
    # per probe what was required and what arrived rather than dumping the records.
    def _why(item):
        if item['status'] is None:
            return 'no response (request never completed)'
        if not 200 <= item['status'] < 300:
            return f'status {item["status"]}, wanted 2xx'
        if item['errors']:
            return f'stream errors: {item["errors"]}'
        if not (item['terminal'] or item.get('model_output')):
            return (
                f'neither a terminal event nor model output; read {item.get("bytes")} bytes. '
                'The stub answers SSE for OpenAI-shaped streaming and NDJSON for Ollama, '
                'so a body with no terminal event is a truncated stream, not a success.'
            )
        return None

    broken = [(item['route'], _why(item)) for item in controls]
    broken = [(route, reason) for route, reason in broken if reason]
    assert not broken, 'model-path controls produced no usable output:\n' + '\n'.join(
        f'  {route}: {reason}' for route, reason in broken
    )


@needs_stack
def test_live_model_path_reports_its_own_reach(live_model_paths, record_property):
    tally = live_model_paths.tally
    assert tally.expected == set(model_path.targets()) == tally.statuses.keys()
    assert tally.config_restore_verified
    record_property('model_path_unentered', tally.unentered)
    record_property('model_path_responses', live_model_paths.responses)
    assert tally.entered, 'No model-path request entered a handler'
