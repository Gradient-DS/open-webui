"""[Gradient] Data sources: tenant-wide platform collections whose key starts with `ds:`. They stay out of the
knowledge lists; a user switches them on per chat in the composer, and a turn sends them as knowledge."""

from typing import Any

PREFIX = 'ds:'


def is_data_source(key: Any) -> bool:
    return isinstance(key, str) and key.startswith(PREFIX) and len(key) > len(PREFIX)


async def allowed() -> bool:
    """On for the tenant (`ENABLE_DATA_SOURCES`), on the agent route, and offered by soev-api."""
    from open_webui.env import AGENT_API_ENABLED
    from open_webui.models.config import Config
    from open_webui.soev import capabilities

    return (
        AGENT_API_ENABLED
        and bool(await Config.get('data_sources.enable'))
        and await capabilities.has(capabilities.DATA_SOURCES)
    )


async def chosen(features: Any) -> list[str]:
    """The data source keys a turn's `features.data_sources` switches on; none while the gate is closed. Any
    other key is dropped, so this never widens the turn's knowledge beyond data sources."""
    requested = features.get('data_sources') if isinstance(features, dict) else None
    if not isinstance(requested, list) or not requested or not await allowed():
        return []
    return list(dict.fromkeys(key for key in requested if is_data_source(key)))
