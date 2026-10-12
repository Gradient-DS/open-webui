from typing import Any

from open_webui.models.config import Config
from open_webui.utils.agent_v2 import call_agent_v2


async def call_agent_api(
    request,
    form_data: dict[str, Any],
    metadata: dict[str, Any],
):
    model_info = (metadata.get('model') or {}).get('info') or {}
    llm_model = model_info.get('base_model_id') or form_data.get('model', '')
    selected_agent = await Config.get('agent_api.selected_agent') or None
    return await call_agent_v2(form_data, metadata, agent=selected_agent, model=llm_model or None)
