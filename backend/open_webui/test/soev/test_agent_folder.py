"""A chat in a folder sends the folder's prompt and items to the agent, whatever the function calling mode."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from open_webui.utils import middleware

FOLDER_FILES = [
    {'type': 'collection', 'id': 'kb-dossier', 'name': 'Dossier'},
    {'type': 'file', 'id': 'f-beleid', 'name': 'beleid.pdf'},
    {'type': 'note', 'id': 'n-afspraken', 'name': 'Afspraken'},
]


@pytest.fixture(autouse=True)
def folder(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    stored = SimpleNamespace(
        id='folder-1',
        user_id='alice',
        parent_id=None,
        data={'system_prompt': 'Antwoord in het kort.', 'files': FOLDER_FILES},
    )
    monkeypatch.setattr(middleware.Folders, 'get_folder_by_id', AsyncMock(return_value=stored))
    monkeypatch.setattr(middleware, 'get_owner_accessible_folder_files', AsyncMock(return_value=FOLDER_FILES))
    monkeypatch.setattr(middleware, 'get_system_oauth_token', AsyncMock(return_value=None))
    monkeypatch.setattr(
        middleware, 'process_pipeline_inlet_filter', AsyncMock(side_effect=lambda r, form_data, *a: form_data)
    )
    monkeypatch.setattr(middleware.Config, 'get', AsyncMock(return_value=None))
    return stored


async def settled(route_to_agent: bool, function_calling: str) -> tuple[dict, dict]:
    """The form data and metadata as the payload pass returns them."""
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(MODELS={})), state=SimpleNamespace())
    form_data = {'model': 'agent', 'messages': [{'role': 'user', 'content': 'Wat staat er in het dossier?'}]}
    metadata = {
        'chat_id': 'local:chat-1',
        'folder_id': 'folder-1',
        'route_to_agent': route_to_agent,
        'params': {'function_calling': function_calling},
    }
    user = SimpleNamespace(id='alice', role='user')
    form_data, metadata, _, _ = await middleware.process_chat_payload(
        request, form_data, user, metadata, {'id': 'agent', 'info': {}}
    )
    return form_data, metadata


@pytest.mark.asyncio
@pytest.mark.parametrize('function_calling', ['legacy', 'native'])
async def test_a_routed_turn_carries_the_folders_prompt_and_items(function_calling: str) -> None:
    _, metadata = await settled(True, function_calling)
    assert metadata['chat_system_prompt'] == 'Antwoord in het kort.'
    assert metadata['folder_knowledge'] == [{**item, 'folder_id': 'folder-1'} for item in FOLDER_FILES]


@pytest.mark.asyncio
async def test_a_legacy_turn_not_routed_keeps_the_folder_for_retrieval() -> None:
    _, metadata = await settled(False, 'legacy')
    assert 'folder_knowledge' not in metadata
    middleware.get_owner_accessible_folder_files.assert_not_awaited()
