"""Platform-provisioned collections selected in the chat composer."""

from typing import Any


def is_provisioned(row: dict[str, Any]) -> bool:
    return row.get('provisioning') == 'provisioned'


async def allowed() -> bool:
    """On for the tenant (`ENABLE_PROVISIONED_KNOWLEDGE`), on the agent route, and offered by soev-api."""
    from open_webui.env import AGENT_API_ENABLED
    from open_webui.models.config import Config
    from open_webui.soev import capabilities

    return (
        AGENT_API_ENABLED
        and bool(await Config.get('provisioned_knowledge.enable'))
        and await capabilities.has(capabilities.PROVISIONED_KNOWLEDGE)
    )


async def chosen(features: Any, *, user_id: str | None = None) -> list[str]:
    """Recheck provisioned status and caller access for every requested collection."""
    from open_webui.models.knowledge import Knowledges

    requested = features.get('provisioned_knowledge') if isinstance(features, dict) else None
    if not isinstance(requested, list) or not requested or not await allowed():
        return []
    keys = list(dict.fromkeys(key for key in requested if isinstance(key, str) and key))
    if not keys:
        return []
    available = {row['key'] for row in await Knowledges.provisioned_knowledge(user_id)}
    return [key for key in keys if key in available]
