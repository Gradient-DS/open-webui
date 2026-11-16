"""The global system prompt every base model without its own uses (soev/system_prompt.py)."""

from types import SimpleNamespace

import pytest

from open_webui.soev import system_prompt


def row(*, system=None, base_model_id=None, absent=False):
    params = {} if absent else {'system': system}
    return SimpleNamespace(base_model_id=base_model_id, params=SimpleNamespace(model_dump=lambda: dict(params)))


@pytest.fixture
def stored(monkeypatch):
    value = {'prompt': 'Globaal.'}

    async def get(key, default=None):
        assert key == 'models.system_prompt'
        return value['prompt']

    monkeypatch.setattr(system_prompt.Config, 'get', get)
    return value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('model_info', 'expected'),
    [
        (row(system='Eigen.'), 'Eigen.'),
        # An empty prompt box is stored as null; it no longer blanks the global prompt.
        (row(system=None), 'Globaal.'),
        (row(system='  '), 'Globaal.'),
        (row(absent=True), 'Globaal.'),
        # A catalog model without a row of its own.
        (None, 'Globaal.'),
        (row(system='Assistent.', base_model_id='glm-5-3'), 'Assistent.'),
        (row(system=None, base_model_id='glm-5-3'), None),
    ],
)
async def test_a_base_model_uses_its_own_prompt_else_the_global_one(stored, model_info, expected):
    assert await system_prompt.for_model(model_info) == expected


@pytest.mark.asyncio
async def test_default_model_params_system_comes_below_the_global_prompt(stored):
    assert await system_prompt.for_model(None, 'Standaard.') == 'Globaal.'
    assert await system_prompt.for_model(row(base_model_id='glm-5-3'), 'Standaard.') == 'Standaard.'
    stored['prompt'] = ''
    assert await system_prompt.for_model(None, 'Standaard.') == 'Standaard.'
    assert await system_prompt.for_model(None) is None


@pytest.mark.asyncio
async def test_the_admin_endpoint_saves_the_prompt_and_audits_only_its_length(monkeypatch):
    from unittest.mock import AsyncMock

    from open_webui.routers import configs

    stored = {}

    async def upsert(updates):
        stored.update(updates)

    async def get(key, default=None):
        return stored.get(key, default)

    audit = AsyncMock()
    monkeypatch.setattr(configs.Config, 'upsert', upsert)
    monkeypatch.setattr(configs.Config, 'get', get)
    monkeypatch.setattr(configs, 'publish_event', audit)
    admin = SimpleNamespace(id='admin', role='admin')
    form = configs.SystemPromptForm(SYSTEM_PROMPT='Je bent de assistent van Kwink.')
    assert await configs.set_system_prompt(SimpleNamespace(), form, user=admin) == {
        'SYSTEM_PROMPT': 'Je bent de assistent van Kwink.'
    }
    assert stored == {'models.system_prompt': 'Je bent de assistent van Kwink.'}
    assert audit.await_args.kwargs['data'] == {'length': 31}
    assert await configs.get_system_prompt(SimpleNamespace(), user=admin) == {
        'SYSTEM_PROMPT': 'Je bent de assistent van Kwink.'
    }
    # Whitespace only clears it.
    await configs.set_system_prompt(SimpleNamespace(), configs.SystemPromptForm(SYSTEM_PROMPT='  \n'), user=admin)
    assert stored == {'models.system_prompt': ''}
