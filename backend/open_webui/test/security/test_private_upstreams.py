"""Agent chat always requires the configured v2 service."""

from unittest.mock import AsyncMock

import pytest
from open_webui import config
from open_webui.models.config import Config
from open_webui.soev.availability import SoevNotConfigured
from open_webui.utils import agent


@pytest.mark.asyncio
async def test_agent_chat_requires_the_platform(monkeypatch):
    monkeypatch.setattr(Config, 'get', AsyncMock(return_value=None))
    monkeypatch.setattr(config, 'SOEV_API_URL', '')
    with pytest.raises(SoevNotConfigured):
        await agent.call_agent_api(None, {'model': 'test'}, {'user_id': 'owner'})
