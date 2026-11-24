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
        'workspace': False,
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
    assert model['info']['meta']['capabilities'] == {'vision': False, 'workspace': False, 'usage': True}
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


# [Gradient] Use the same assembled LLM capabilities as a plain turn, including catalog overrides.
@pytest.mark.asyncio
@pytest.mark.parametrize('llm_workspace', [None, False, True, 'true', 1])
@pytest.mark.parametrize('assistant_workspace', [None, False, True, 'true', 1])
@pytest.mark.parametrize('allowed', [False, True])
async def test_workspace_composition_and_permission(llm_workspace, assistant_workspace, allowed):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    from open_webui.soev.model_catalog import apply_catalog, owui_model
    from open_webui.utils.features import gate_workspace_features

    base = owui_model({'id': 'catalog-llm', 'capabilities': {'vision': 'supported'}})
    llm = deepcopy(base)
    llm['info'] = row('catalog-llm', None, meta={'capabilities': {'workspace': llm_workspace}}).model_dump()
    apply_catalog([llm], [base])
    assistant = row(meta={'capabilities': {'workspace': assistant_workspace}})
    before = deepcopy(llm), assistant.model_dump()
    model = build_assistant_model(assistant, llm)
    capabilities = model['info']['meta']['capabilities']
    with patch('open_webui.utils.features.workspace_allowed', AsyncMock(return_value=allowed)):
        result = await gate_workspace_features(
            {'workspace': True, 'web_search': True}, capabilities, SimpleNamespace(id='alice', role='user')
        )
    assert result == {
        # The assistant's own choice is a chat's starting state, never a limit.
        'workspace': llm_workspace is True and allowed,
        'web_search': True,
    }
    assert (llm, assistant.model_dump()) == before


@pytest.mark.parametrize('llm', [None, False, True])
@pytest.mark.parametrize('assistant', [None, False, True])
@pytest.mark.parametrize(
    'capability',
    ['web_search', 'image_generation', 'code_interpreter', 'document_writer', 'live_documents', 'live_mail'],
)
def test_switchable_capabilities_follow_the_llm(capability, llm, assistant):
    """A functionality the assistant leaves off can still be switched on in a chat with it."""
    llm_caps = {} if llm is None else {capability: llm}
    assistant_caps = {} if assistant is None else {capability: assistant}
    assert effective_capabilities(llm_caps, assistant_caps).get(capability) is llm


@pytest.mark.asyncio
async def test_catalog_without_admin_workspace_capability_cannot_run_assistant_workspace():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    from open_webui.soev.model_catalog import owui_model
    from open_webui.utils.features import gate_workspace_features

    llm = owui_model({'id': 'catalog-only', 'capabilities': {'tools': 'supported'}})
    model = build_assistant_model(row(meta={'capabilities': {'workspace': True}}), llm)
    with patch('open_webui.utils.features.workspace_allowed', AsyncMock(return_value=True)):
        result = await gate_workspace_features(
            {'workspace': True}, model['info']['meta']['capabilities'], SimpleNamespace(id='alice', role='user')
        )
    assert result['workspace'] is False


# [Gradient] Create/update use this nested form; optional assistant preferences stay free-form meta.
def test_default_model_meta_survives_model_form_roundtrip():
    from open_webui.models.models import ModelForm

    data = row(meta={'defaultModelId': 'catalog/llm', 'unshown': {'preserve': True}}).model_dump()
    first = ModelForm(**data)
    updated = ModelForm(**first.model_dump())
    assert updated.meta.model_dump()['defaultModelId'] == 'catalog/llm'
    assert updated.meta.model_dump()['unshown'] == {'preserve': True}
    del data['meta']['defaultModelId']
    assert 'defaultModelId' not in ModelForm(**data).meta.model_dump()
