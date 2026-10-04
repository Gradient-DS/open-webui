"""Agent document writer configuration and stored-message compatibility."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from open_webui.routers import configs
from open_webui.utils.middleware import serialize_output


@pytest.mark.asyncio
@pytest.mark.parametrize('enabled', [False, True])
async def test_document_writer_admin_setting_survives_a_save(monkeypatch: pytest.MonkeyPatch, enabled: bool) -> None:
    """The code execution settings endpoint saves and reads back the document writer switch."""
    request = SimpleNamespace()
    values = await configs.get_code_execution_config(request)
    values['ENABLE_DOCUMENT_WRITER'] = enabled
    form = configs.CodeInterpreterConfigForm.model_validate(values)
    stored = {}

    async def upsert(updates: dict) -> None:
        stored.update(updates)

    async def get_many(*keys: str) -> dict:
        return {key: stored[key] for key in keys}

    monkeypatch.setattr(configs.Config, 'upsert', upsert)
    monkeypatch.setattr(configs.Config, 'get_many', get_many)
    monkeypatch.setattr(configs, 'publish_event', AsyncMock())
    result = await configs.set_code_execution_config(request, form, user=SimpleNamespace(id='admin', role='admin'))
    assert stored['document_writer.enable'] is enabled
    assert result['ENABLE_DOCUMENT_WRITER'] is enabled
    assert (await configs.get_code_execution_config(request))['ENABLE_DOCUMENT_WRITER'] is enabled
    assert 'document_writer.prompt_template' not in stored
    assert 'DOCUMENT_WRITER_PROMPT_TEMPLATE' not in result


def test_stored_document_output_still_serializes() -> None:
    """Legacy document output items still render when an existing message is serialized."""
    output = [{'type': 'open_webui:document', 'title': 'A & B', 'markdown': '# Body', 'status': 'completed'}]
    assert serialize_output(output) == (
        '<details type="document" done="true" title="A &amp; B">\n<summary>Document</summary>\n# Body\n</details>'
    )
