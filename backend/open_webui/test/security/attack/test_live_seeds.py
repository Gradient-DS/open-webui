"""Live sealed-stack checks; run only by runtime-security.yml."""

import re
from urllib.parse import quote

import pytest

from . import seeds
from .test_seeds import LIVE_READS, path_parameters


@pytest.fixture(scope='module')
def live_seeds():
    from .identities import ensure_identities

    identities = ensure_identities()
    resolved = None
    try:
        resolved = seeds.resolve_parameters(identities.user, admin=identities.admin, collect_failures=True)
        yield identities, resolved
    finally:
        identities.close()


def test_live_resolution_returns_every_seedable_route_parameter(live_seeds):
    _, resolved = live_seeds
    assert not resolved.failures, resolved.failures
    expected = {
        (path, name) for _, path, name in path_parameters() if seeds.seed_enabled(seeds.parameter_for(path, name))
    }
    assert resolved.keys() == expected
    assert all(isinstance(value, str) and value.strip() and '{' not in value for value in resolved.values())


@pytest.mark.parametrize('role', ['admin', 'user'])
def test_live_seeded_models_are_visible_and_stub_completes_for_both_identities(live_seeds, role):
    identities, resolved = live_seeds
    client = getattr(identities, role)
    # Look the value up by a path the spec actually contains. The surface entry's
    # `prefix` is a longest-prefix matcher, not a route: resolve_parameters keys its
    # result by real path templates, so using a prefix here raises KeyError.
    model_id = resolved.fixtures['model']
    registry = client.request('GET', '/api/models', params={'refresh': True})
    assert registry.status_code == 200, registry.text
    ids = {model['id'] for model in registry.json()['data']}
    assert model_id in ids
    result = client.request(
        'POST',
        '/api/chat/completions',
        json={
            'model': model_id,
            'messages': [{'role': 'user', 'content': 'Verify the CI model fixture.'}],
            'stream': False,
        },
    )
    assert result.status_code == 200, result.text
    # Drive mode routes every chat to the v2 agent, which streams the stub's answer
    # over its thread events even when the caller asked for one JSON completion.
    expected = 'stub streamed response' if seeds.runtime_soev_backed() == 'drive' else 'stub response'
    assert result.json().get('choices', [{}])[0].get('message', {}).get('content') == expected, result.text


def test_live_second_pass_uses_fresh_resource_fixtures(live_seeds):
    identities, first = live_seeds
    second = seeds.resolve_parameters(identities.user, admin=identities.admin, collect_failures=True)
    for pair, value in first.items():
        p = seeds.parameter_for(*pair)
        if 'same_as' not in p and p.get('fresh', True):
            if seeds.runtime_soev_backed() == 'drive' and p['key'] == 'chat_message':
                # Positions are scoped to a thread; position 7 in a new
                # thread is a different message, never a reused fixture.
                parent = (pair[0], 'id')
                assert (second[parent], second[pair]) != (first[parent], value)
            else:
                assert second[pair] != value, f'{p["key"]} reused a previous pass fixture'


@pytest.mark.parametrize('template', LIVE_READS)
def test_live_ids_address_real_resources(live_seeds, template):
    identities, resolved = live_seeds
    from .crossuser import expected_refusals, refused_as_expected

    if seeds.route_waived(f'GET {template}'):
        pytest.skip('soev-solutions resource is waived in this runtime mode')
    path = re.sub(r'\{([^}]+)\}', lambda m: quote(resolved[template, m[1]], safe='/'), template)
    result = identities.user.request('GET', path)
    refusal = expected_refusals().get(f'GET {template}')
    if refusal:
        assert refused_as_expected(refusal, result.status_code, result.text), (template, result.text)
        return
    assert result.status_code == 200, f'{template}: HTTP {result.status_code}: {result.text[:200]}'


def test_live_chat_history_message_is_in_its_parent(live_seeds):
    identities, resolved = live_seeds
    template = '/api/v1/chats/{id}/messages/{message_id}'
    chat_id, message_id = resolved[template, 'id'], resolved[template, 'message_id']
    result = identities.user.request('GET', f'/api/v1/chats/{chat_id}')
    assert result.status_code == 200
    if seeds.runtime_soev_backed() == 'drive':
        assert any(
            str(message['position']) == message_id and message['finished'] for message in result.json()['messages']
        )
    else:
        assert result.json()['chat']['history']['messages'][message_id]['id'] == message_id


def test_live_image_prompt_refusal_has_a_supported_owner_task_control(live_seeds):
    from . import crossuser, plane

    identities, resolved = live_seeds
    positive = 'POST /api/v1/tasks/title/completions'
    response = identities.user.request(
        'POST',
        positive.split(' ', 1)[1],
        json={
            'model': resolved.fixtures['model'],
            'chat_id': resolved['/api/v1/chats/{id}', 'id'],
            'messages': [{'role': 'user', 'content': 'Fictional title control'}],
        },
    )
    try:
        assert response.status_code == 200, response.text
        assert response.json()['choices'][0]['message']['content']
    finally:
        response.close()
    route = 'POST /api/v1/tasks/image_prompt/completions'
    declaration = crossuser.expected_refusals().get(route)
    if declaration is not None:
        for role in ('admin', 'user', 'intruder'):
            response = getattr(identities, role).request(
                'POST', route.split(' ', 1)[1], **crossuser._benign(route, resolved, seeds.SPEC)
            )
            try:
                plane.record_design_refusal(route, role, response)
            finally:
                response.close()


def test_live_shared_clone_uses_the_share_id_not_the_admin_chat_fallback(live_seeds):
    identities, resolved = live_seeds
    share_id = resolved['/api/v1/chats/{id}/clone/shared', 'id']
    assert share_id == resolved['/api/v1/chats/share/{share_id}', 'share_id']
    if seeds.runtime_soev_backed() == 'drive':
        assert share_id == resolved['/api/v1/chats/{id}', 'id']
        stranger = identities.intruder.request('POST', f'/api/v1/chats/{share_id}/clone/shared', json={})
        assert stranger.status_code == 404, stranger.text
    else:
        assert share_id != resolved['/api/v1/chats/{id}', 'id']
    result = identities.user.request('POST', f'/api/v1/chats/{share_id}/clone/shared', json={})
    assert result.status_code == 200
    assert result.json()['id'] not in (share_id, resolved['/api/v1/chats/{id}', 'id'])


@pytest.mark.parametrize('role', ['admin', 'user', 'intruder'])
def test_live_v2_function_routes_refuse_every_operation(live_seeds, role):
    from . import plane
    from .benign_requests import benign_request
    from .crossuser import expected_refusals, refused_as_expected

    if seeds.runtime_soev_backed() != 'drive':
        pytest.skip('v2 custom-function refusals apply only to the private sweep')
    identities, resolved = live_seeds
    for route, declaration in expected_refusals().items():
        method, template = route.split(' ', 1)
        if not template.startswith('/api/v1/functions/') and not declaration.get('all_actors'):
            continue
        target = re.sub(r'\{([^}]+)\}', lambda m: quote(resolved[template, m[1]], safe=''), template)
        response = getattr(identities, role).request(method, target, **benign_request(route, resolved))
        try:
            assert refused_as_expected(declaration, response.status_code, response.text), (route, response.text)
            plane.record_design_refusal(route, role, response)
        finally:
            response.close()
