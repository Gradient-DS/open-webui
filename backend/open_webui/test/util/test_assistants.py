"""Pure assistant composition preserves the chosen provider and parameter layers."""

from copy import deepcopy

import pytest
from open_webui.models.models import ModelModel
from open_webui.utils.assistants import build_assistant_model, effective_capabilities, merged_model_info


def row(id='assistant', base_model_id='legacy-llm', **kwargs):
    """Build a persisted-model value without database I/O."""
    return ModelModel(
        id=id,
        name=id,
        user_id='owner',
        base_model_id=base_model_id,
        params=kwargs.get('params', {}),
        meta=kwargs.get('meta', {}),
        is_active=True,
        created_at=1,
        updated_at=1,
    )


@pytest.mark.parametrize('llm', [False, True])
@pytest.mark.parametrize('assistant', [False, True])
def test_vision_requires_both(llm, assistant):
    """Neither side can enable vision when the other forbids it."""
    assert effective_capabilities({'vision': llm}, {'vision': assistant})['vision'] is (llm and assistant)


@pytest.mark.parametrize(
    'llm,assistant,expected', [(None, None, True), ({}, {'vision': False}, False), ({'vision': False}, {}, False)]
)
def test_unset_vision_defaults_true(llm, assistant, expected):
    """Missing capability maps and keys default to enabled vision."""
    assert effective_capabilities(llm, assistant)['vision'] is expected


def test_other_capabilities_use_assistant_then_llm():
    """Assistant choices win while absent choices inherit from the LLM."""
    assert effective_capabilities(
        {'citations': False, 'usage': True, 'nested': [1]}, {'citations': True, 'search': False}
    ) == {
        'vision': True,
        'citations': True,
        'usage': True,
        'search': False,
        'nested': [1],
    }


def test_composition_copies_dispatch_fields_and_assistant_metadata():
    """LLM dispatch identity survives while assistant features become effective."""
    assistant = row(
        params={'system': 'Assistant prompt'},
        meta={'capabilities': {'vision': True}, 'knowledge': [{'id': 'kb'}], 'skillIds': ['skill']},
    )
    llm = {
        'id': 'picked',
        'name': 'Picked',
        'owned_by': 'ollama',
        'provider': 'provider',
        'pipe': {'type': 'x'},
        'connection_type': 'external',
        'connection_host': 'host',
        'loaded': False,
        'info': {'meta': {'capabilities': {'vision': False, 'usage': True}}},
    }
    original = deepcopy(llm), assistant.model_dump()
    model = build_assistant_model(assistant, llm)
    for key, value in llm.items():
        if key != 'info':
            assert model[key] == value
    assert model['assistant_id'] == model['info']['id'] == 'assistant'
    assert model['info']['base_model_id'] is None
    assert 'params' not in model['info']
    assert model['info']['meta']['capabilities'] == {'vision': False, 'usage': True}
    assert model['info']['meta']['skillIds'] == ['skill']
    model['pipe']['type'] = 'changed'
    model['info']['meta']['knowledge'][0]['id'] = 'changed'
    assert (llm, assistant.model_dump()) == original


def test_params_merge_llm_then_assistant_without_mutation():
    """Assistant params overlay LLM params including custom parameter keys."""
    llm = row('llm', None, params={'temperature': 0.1, 'seed': 4, 'custom_params': {'a': 1, 'b': 2}})
    assistant = row(params={'system': 'Prompt', 'temperature': 0.8, 'custom_params': {'b': 3}})
    before = llm.model_dump(), assistant.model_dump()
    merged = merged_model_info(llm, assistant)
    assert merged.base_model_id is None
    assert merged.params.model_dump() == {
        'system': 'Prompt',
        'temperature': 0.8,
        'seed': 4,
        'custom_params': {'a': 1, 'b': 3},
    }
    merged.params.custom_params['a'] = 9
    assert (llm.model_dump(), assistant.model_dump()) == before
    assert merged_model_info(None, assistant).params == assistant.params


@pytest.mark.asyncio
@pytest.mark.parametrize('split,expected', [(True, 'picked'), (False, 'legacy-llm')])
async def test_v1_wire_resolution_preserves_legacy_and_split(monkeypatch, split, expected):
    """The unchanged resolver uses the split picker or the legacy stored default."""
    from open_webui import env
    from open_webui.utils import agent

    async def config(*args):
        return None

    async def capture(payload):
        return payload

    monkeypatch.setattr(env, 'AGENT_API_RUNTIME', 'v1')
    monkeypatch.setattr(agent.Config, 'get', config)
    monkeypatch.setattr(agent, '_call_agent_api_non_streaming', capture)
    assistant = row(meta={'capabilities': {'vision': False, 'citations': False}})
    model = build_assistant_model(assistant, {'id': 'picked'}) if split else {'info': assistant.model_dump()}
    payload = await agent.call_agent_api(
        None,
        {'model': 'picked' if split else 'assistant', 'stream': False, 'messages': []},
        {
            'model': model,
            'system_prompt': 'Assistant prompt',
            'knowledge': [{'id': 'kb'}],
            'skills': [{'name': 'skill', 'description': 'test', 'content': 'instructions', 'is_selected': True}],
        },
        {},
    )
    assert payload['model'] == expected
    assert payload['system_prompt'] == 'Assistant prompt'
    assert payload['metadata']['vision_capable'] is False
    assert payload['features']['citations'] is False
    assert payload['knowledge'] == [{'id': 'kb'}]
    assert payload['skills'][0]['content'] == 'instructions'
    assert 'assistant_id' not in payload
