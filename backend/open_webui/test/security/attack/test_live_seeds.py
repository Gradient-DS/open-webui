"""Live sealed-stack checks; run only by runtime-security.yml."""

import re
from urllib.parse import quote
import pytest
from . import seeds

from .test_seeds import LIVE_READS, path_parameters, stop_seed_task


@pytest.fixture(scope='module')
def live_seeds():
    from .identities import ensure_identities

    identities = ensure_identities()
    resolved = None
    try:
        resolved = seeds.resolve_parameters(identities.user, admin=identities.admin, collect_failures=True)
        yield identities, resolved
    finally:
        try:
            if resolved is not None:
                stop_seed_task(identities.admin, resolved)
        finally:
            identities.close()


def test_live_resolution_returns_every_seedable_route_parameter(live_seeds):
    _, resolved = live_seeds
    assert not resolved.failures, resolved.failures
    expected = {
        (path, name) for _, path, name in path_parameters() if 'unseedable' not in seeds.parameter_for(path, name)
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
    model_id = resolved['/api/v1/analytics/models/{model_id}/chats', 'model_id']
    registry = client.request('GET', '/api/models', params={'refresh': True})
    assert registry.status_code == 200, registry.text
    ids = {model['id'] for model in registry.json()['data']}
    assert model_id in ids
    assert any(model.startswith('attack_task_') for model in ids)
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
    assert result.json()['choices'][0]['message']['content'] == 'stub response'


def test_live_second_pass_uses_fresh_resource_fixtures(live_seeds):
    identities, first = live_seeds
    second = seeds.resolve_parameters(identities.user, admin=identities.admin, collect_failures=True)
    try:
        for pair, value in first.items():
            p = seeds.parameter_for(*pair)
            if 'same_as' not in p and p.get('fresh', True):
                assert second[pair] != value, f'{p["key"]} reused a previous pass fixture'
    finally:
        stop_seed_task(identities.admin, second)


@pytest.mark.parametrize('template', LIVE_READS)
def test_live_ids_address_real_resources(live_seeds, template):
    identities, resolved = live_seeds
    path = re.sub(r'\{([^}]+)\}', lambda m: quote(resolved[template, m[1]], safe='/'), template)
    result = identities.admin.request('GET', path)
    assert result.status_code == 200, f'{template}: HTTP {result.status_code}: {result.text[:200]}'
    if template.endswith('/validate'):
        assert result.json()['email'].startswith('attack-invite-token-')


def test_live_chat_history_message_is_in_its_parent(live_seeds):
    identities, resolved = live_seeds
    template = '/api/v1/chats/{id}/messages/{message_id}'
    chat_id, message_id = resolved[template, 'id'], resolved[template, 'message_id']
    result = identities.admin.request('GET', f'/api/v1/chats/{chat_id}')
    assert result.status_code == 200
    assert result.json()['chat']['history']['messages'][message_id]['id'] == message_id


def test_live_task_still_exists_after_the_rest_of_seeding(live_seeds):
    identities, resolved = live_seeds
    task_id = resolved['/api/tasks/stop/{task_id}', 'task_id']
    result = identities.admin.request('GET', '/api/tasks')
    assert result.status_code == 200
    assert task_id in result.json()['tasks']


def test_live_shared_clone_uses_the_share_id_not_the_admin_chat_fallback(live_seeds):
    identities, resolved = live_seeds
    share_id = resolved['/api/v1/chats/{id}/clone/shared', 'id']
    assert share_id == resolved['/api/v1/chats/share/{share_id}', 'share_id']
    assert share_id != resolved['/api/v1/chats/{id}', 'id']
    result = identities.admin.request('POST', f'/api/v1/chats/{share_id}/clone/shared')
    assert result.status_code == 200
    assert result.json()['id'] not in (share_id, resolved['/api/v1/chats/{id}', 'id'])
