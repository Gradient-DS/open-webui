import json
import re
from contextlib import nullcontext
from unittest.mock import Mock
from uuid import UUID

import pytest
import requests
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from hostile_corpus import NON_OBJECT_BODIES, whitespace_variants
from pydantic import BaseModel, Field

from . import plane, shapes


@pytest.fixture
def offline(monkeypatch, tmp_path):
    monkeypatch.setattr(plane, '_PASSES', {})
    monkeypatch.setattr(plane, 'preserve_configuration', lambda *a, **kw: nullcontext())
    monkeypatch.setenv('ROUTE_HITS_PATH', str(tmp_path / 'hits.json'))
    monkeypatch.delenv('ATTACK_FULL_CORPUS', raising=False)
    monkeypatch.setattr(requests.Session, 'request', Mock(side_effect=AssertionError('Unexpected HTTP')))


def reply(status=200, body=None):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(body).encode()
    result._content_consumed = True
    return result


class ObjectBody(BaseModel):
    value: str = ''


class PatternBody(BaseModel):
    value: str = Field(pattern=r'^[0-9a-f-]{36}$')


class UUIDBody(BaseModel):
    value: UUID


@pytest.fixture
def structural_client():
    from open_webui.services.model_request_bodies import chat_completion_body

    app = FastAPI()
    app.add_api_route('/model', chat_completion_body, methods=['POST'])

    @app.post('/object')
    def typed(body: ObjectBody):
        return body

    @app.post('/pattern')
    def pattern(body: PatternBody):
        return body

    @app.post('/uuid')
    def uuid(body: UUIDBody):
        return body

    @app.post('/raw')
    async def raw(request: Request):
        return {'received': await request.json()}

    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize('body', NON_OBJECT_BODIES)
def test_fastapi_rejects_nonobjects_including_falsy_but_raw_request_receives_them(body, structural_client):
    for path in ('/object', '/model'):
        response = structural_client.post(path, content=json.dumps(body), headers={'Content-Type': 'application/json'})
        assert response.status_code == 422
        assert isinstance(response.json()['detail'], list)
    response = structural_client.post('/raw', content=json.dumps(body), headers={'Content-Type': 'application/json'})
    assert response.status_code == 200
    assert response.json()['received'] == body


@pytest.mark.parametrize('value', whitespace_variants('12345678-1234-1234-1234-123456789abc'))
def test_plain_strings_accept_whitespace_while_uuid_and_rust_pattern_reject(value, structural_client):
    assert structural_client.post('/object', json={'value': value}).status_code == 200
    for path in ('/pattern', '/uuid'):
        assert structural_client.post(path, json={'value': value}).status_code == 422
    if value.endswith('\n') and not value.endswith('\r\n'):
        assert re.match(r'^[0-9a-f-]{36}$', value)


def test_no_body_routes_are_driven_null_is_literal_and_deletes_are_last(offline):
    spec = {
        'paths': {
            '/z': {'delete': {}},
            '/raw': {'post': {}},
            '/read': {'get': {}},
            '/api/v1/auths/signout': {'post': {}},
        }
    }
    actor = Mock()
    actor.request.side_effect = lambda *a, **k: reply()
    tally = shapes.drive_shapes(actor, {}, spec=spec)
    calls = actor.request.call_args_list
    assert [c.args[0] for c in calls] == ['POST'] * len(NON_OBJECT_BODIES) + ['DELETE'] * len(NON_OBJECT_BODIES)
    assert [json.loads(c.kwargs['data']) for c in calls[: len(NON_OBJECT_BODIES)]] == list(NON_OBJECT_BODIES)
    assert all(c.kwargs['headers']['Content-Type'] == 'application/json' for c in calls)
    assert tally.expected == {'POST /raw', 'DELETE /z'}
    assert tally.config_restore_verified


def test_sampling_and_full_mode_visit_every_whitespace_field(offline, monkeypatch):
    spec = {
        'paths': {
            '/write': {
                'post': {
                    'requestBody': {
                        'content': {
                            'application/json': {
                                'schema': {
                                    'type': 'object',
                                    'properties': {'one': {'type': 'string'}, 'two': {'type': 'string'}},
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    uid = '12345678-1234-1234-1234-123456789abc'
    actor = Mock()
    actor.request.side_effect = lambda *a, **k: reply()
    for full in (False, True):
        actor.reset_mock()
        monkeypatch.setenv('ATTACK_FULL_CORPUS', '1' if full else '0')
        shapes.drive_shapes(actor, {('/seed', 'id'): uid}, spec=spec)
        bodies = [c.kwargs['json'] for c in actor.request.call_args_list if 'json' in c.kwargs]
        assert len(bodies) == 3 * (len(whitespace_variants(uid)) if full else 1)
        for field in ('one', 'two'):
            observed = {b[field] for b in bodies if field in b}
            assert observed <= set(whitespace_variants(uid))
            if full:
                assert observed == set(whitespace_variants(uid))


def test_shapes_cannot_borrow_another_pass_entries_or_hide_a_5xx(offline):
    from .test_live_shapes import test_live_shapes_has_its_own_5xx_assertion

    route = 'POST /raw'
    plane.record(route, 200, {}, pass_name='drive')
    actor = Mock()
    actor.request.side_effect = lambda *a, **kw: reply(422, {'detail': []})
    tally = shapes.drive_shapes(actor, {}, spec={'paths': {'/raw': {'post': {}}}})
    assert not tally.entered
    assert route in tally.unentered
    plane.record(route, 503, 'shape exploded', pass_name='shapes')
    plane.record(route, 200, {}, pass_name='shapes')
    with pytest.raises(AssertionError, match='shape exploded'):
        test_live_shapes_has_its_own_5xx_assertion(tally)
