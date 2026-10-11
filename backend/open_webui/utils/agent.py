from typing import Any

from open_webui.models.agent_configs import AgentConfigs
from open_webui.models.config import Config
from open_webui.utils.agent_v2 import call_agent_v2


async def call_agent_api(
    request,
    form_data: dict[str, Any],
    metadata: dict[str, Any],
    override_agent: str | None = None,
):
    model_info = (metadata.get('model') or {}).get('info') or {}
    llm_model = model_info.get('base_model_id') or form_data.get('model', '')
    selected_agent = override_agent or await Config.get('agent_api.selected_agent') or None
    agent_config = await AgentConfigs.get_agent_config_by_id(selected_agent) if selected_agent else None
    agent_meta = agent_config.meta if agent_config else {}
    if agent_config and agent_meta.get('runtime') != 'v2':
        selected_agent = await Config.get('agent_api.selected_agent') or None
        agent_meta = {}
    return await call_agent_v2(form_data, metadata, agent=selected_agent, model=llm_model or agent_meta.get('model'))
