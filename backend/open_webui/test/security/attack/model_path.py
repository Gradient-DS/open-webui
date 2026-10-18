"""Drain model responses with chunk and absolute deadlines, then judge the body.

RUN-TAG: owui-phase7b-authorization-and-model-passes

The sealed Linux CI and macOS reviewer run pytest serially on the main thread.
SIGALRM bounds blocking reads as well as endless trickles; a requests read
timeout alone cannot bound a peer that keeps sending bytes. Restore the prior
signal handler and reject an already-owned timer rather than stealing it.
HTTP status and handler entries remain the shared engine's pass-local evidence.
Successful SSE requires a dispatched terminal event AND clean EOF; a finish
reason delta or HTTP 200 alone is not completion. Tasks that deliberately return
ordinary JSON are recorded separately from SSE.
"""

import json
import os
import re
import signal
import threading
import time
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field

import requests
from hostile_corpus import fetch_payloads
from openapi_surface import writable_string_fields

from . import plane
from .pass_support import pass_run
from .seeds import SPEC

CHUNK_TIMEOUT = 5.0
WALL_TIMEOUT = 60.0
CONNECT_TIMEOUT = 10.0


class StreamDeadline(Exception):
    pass


@contextmanager
def deadlines(chunk_timeout, wall_timeout):
    if chunk_timeout <= 0 or wall_timeout <= 0:
        raise ValueError('Streaming deadlines must be positive')
    if threading.current_thread() is not threading.main_thread() or not hasattr(signal, 'setitimer'):
        raise RuntimeError('Model streaming coverage requires POSIX timers on the main thread')
    if signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
        raise RuntimeError('An existing alarm owns the streaming deadline timer')
    previous = signal.getsignal(signal.SIGALRM)
    end = time.monotonic() + wall_timeout

    def expired(*_):
        reason = 'wall-clock deadline' if time.monotonic() >= end else 'per-chunk timeout'
        raise StreamDeadline(reason)

    def arm(*, chunk=False):
        remaining = end - time.monotonic()
        if remaining <= 0:
            raise StreamDeadline('wall-clock deadline')
        signal.setitimer(signal.ITIMER_REAL, min(chunk_timeout, remaining) if chunk else remaining)

    signal.signal(signal.SIGALRM, expired)
    try:
        arm()
        yield arm
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


@dataclass
class ModelRun:
    tally: plane.Seeding
    responses: list = field(default_factory=list)


def targets(spec=SPEC):
    return [
        route
        for route in plane.operations(spec)
        if route == 'POST /api/v1/chat/completions' or re.fullmatch(r'POST /api/v1/tasks/[^/]+/completions', route)
    ]


def _terminal(data):
    return isinstance(data, dict) and (data.get('done') is True or data.get('type') == 'response.completed')


def stream_events(body):
    terminal, errors = False, []
    # Split only complete SSE events. A final data line without its blank-line
    # dispatch boundary is truncated even if its text happens to say [DONE].
    for event in re.split(r'\r\n\r\n|\n\n|\r\r', body)[:-1]:
        lines = event.splitlines()
        data = '\n'.join(line[5:].lstrip(' ') for line in lines if line.startswith('data:'))
        event_type = next((line[6:].strip() for line in lines if line.startswith('event:')), '')
        if data == '[DONE]':
            terminal = True
            continue
        if not data:
            continue
        try:
            payload = json.loads(data)
        except ValueError:
            errors.append('malformed SSE data')
            continue
        terminal |= _terminal(payload) or event_type == 'response.completed'
        if isinstance(payload, dict) and (
            payload.get('error')
            or payload.get('type') in {'error', 'response.failed'}
            or event_type in {'error', 'response.failed'}
        ):
            errors.append(f'stream error event: {data[:1000]}')
    return terminal, errors


def _body_result(response, item):
    content_type = response.headers.get('Content-Type', '').lower()
    is_sse = 'text/event-stream' in content_type or response.text.lstrip().startswith(('data:', 'event:', ':'))
    item['streamed'] = is_sse
    if is_sse:
        terminal, errors = stream_events(response.text)
        item['terminal'] = terminal
        item['errors'].extend(errors)
        if not terminal:
            item['errors'].append('stream ended with no terminal event (truncated)')
    elif 200 <= response.status_code < 300:
        try:
            payload = response.json()
        except ValueError:
            item['errors'].append('successful response is neither complete JSON nor terminal SSE')
        else:
            item['json_response'] = True
            item['model_output'] = isinstance(payload, dict) and bool(payload.get('choices') or payload.get('output'))
            if isinstance(payload, dict) and payload.get('error'):
                item['errors'].append(f'error under successful HTTP status: {response.text[:1000]}')


def _drive(admin, route, body, result, *, probe, chunk_timeout, wall_timeout):
    item = {'route': route, 'probe': probe, 'status': None, 'bytes': 0, 'terminal': False, 'errors': []}
    response, chunks = None, bytearray()
    with plane.preserve_configuration(
        admin,
        route=route,
        report=result.tally.config_findings.append,
        unverified=lambda reason: result.tally.config_unverified.setdefault(route, reason),
    ):
        try:
            with deadlines(chunk_timeout, wall_timeout) as arm:
                response = admin.request(
                    'POST',
                    route.split(' ', 1)[1],
                    json=body,
                    stream=True,
                    timeout=(min(CONNECT_TIMEOUT, wall_timeout), chunk_timeout),
                )
                item['status'] = response.status_code
                iterator = response.iter_content(chunk_size=1)
                while True:
                    arm(chunk=True)
                    try:
                        chunk = next(iterator)
                    except StopIteration:
                        break
                    chunks.extend(chunk)
                arm()
                response._content = bytes(chunks)
                response._content_consumed = True
                _body_result(response, item)
        except (StreamDeadline, requests.RequestException) as error:
            item['errors'].append(f'{type(error).__name__}: {error}')
        finally:
            item['bytes'] = len(chunks)
            if response is not None:
                # Classify the complete body (or actual partial bytes on failure)
                # without triggering a second read of a truncated generator.
                response._content = bytes(chunks)
                response._content_consumed = True
                try:
                    plane.record(route, response.status_code, response, pass_name='model_path')
                finally:
                    response.close()
            result.responses.append(item)
            if item['errors']:
                result.tally.body_failures.append(item)
            plane.flush_hits()


def _base(model):
    return {
        'model': model,
        'stream': True,
        'messages': [{'role': 'user', 'content': 'Complete the local regression control.'}],
        'prompt': 'Complete the local regression control.',
        'responses': ['Local control response.'],
        'type': 'web_search',
    }


def payload_bodies(route, fields, model, payloads, full):
    yield 'control', _base(model)
    # A content-only control reaches the configured model for every sampled
    # corpus value. The derived-field probes also cover model selectors, role,
    # tools and nested union alternatives without claiming those must validate.
    for batch in plane._payload_batches(route, ['messages[].content'], payloads, full=full):
        body = _base(model)
        body['messages'][0]['content'] = batch['messages[].content']
        body['prompt'] = batch['messages[].content']
        body['responses'] = [batch['messages[].content']]
        yield 'content', body
    for batch in plane._payload_batches(route, fields, payloads, full=full):
        yield 'all-fields', {**_base(model), **plane._body_for(fields, batch), 'stream': True}
        for name in fields:
            body = deepcopy(_base(model))
            body = plane._plant(body, plane._tokens(name), batch[name])
            yield name, body


def drive_model_paths(
    admin, parameters, *, spec=SPEC, payloads=None, full=None, chunk_timeout=CHUNK_TIMEOUT, wall_timeout=WALL_TIMEOUT
):
    routes = targets(spec)
    model = parameters['/api/v1/analytics/models/{model_id}/chats', 'model_id']
    fields = writable_string_fields(spec)
    payloads = tuple(fetch_payloads() if payloads is None else payloads)
    full = os.getenv('ATTACK_FULL_CORPUS') == '1' if full is None else full
    with pass_run('model_path', routes, admin) as tally:
        result = ModelRun(tally)
        for route in routes:
            for probe, body in payload_bodies(route, fields.get(route, []), model, payloads, full):
                _drive(admin, route, body, result, probe=probe, chunk_timeout=chunk_timeout, wall_timeout=wall_timeout)
    return result
