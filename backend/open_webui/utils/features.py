"""Feature gates bind every user; platform values come from product configuration."""

from typing import Literal

from fastapi import HTTPException, status
from open_webui.config import (
    FEATURE_ARTIFACTS,
    FEATURE_BUILTIN_TOOLS,
    FEATURE_CAPTURE,
    FEATURE_CHANGELOG,
    FEATURE_CHAT_CONTROLS,
    FEATURE_CHAT_OVERVIEW,
    FEATURE_DOCUMENT_WRITER,
    FEATURE_KNOWLEDGE,
    FEATURE_MEETINGS,
    FEATURE_MODELS,
    FEATURE_NOTES_AI_CONTROLS,
    FEATURE_PLAYGROUND,
    FEATURE_PROMPTS,
    FEATURE_READ_ALOUD,
    FEATURE_SKILLS,
    FEATURE_SYSTEM_PROMPT,
    FEATURE_TERMINAL_SERVERS,
    FEATURE_TOOL_SERVERS,
    FEATURE_TOOLS,
    FEATURE_VOICE,
    FEATURE_VOICE_CALL,
)

Feature = Literal[
    'chat_controls',
    'capture',
    'artifacts',
    'document_writer',
    'playground',
    'chat_overview',
    'notes_ai_controls',
    'voice',
    'voice_call',
    'read_aloud',
    'changelog',
    'meetings',
    'system_prompt',
    'models',
    'knowledge',
    'prompts',
    'tools',
    'skills',
    'tool_servers',
    'terminal_servers',
    'builtin_tools',
]

FEATURE_FLAGS: dict[Feature, bool] = {
    'chat_controls': FEATURE_CHAT_CONTROLS,
    'capture': FEATURE_CAPTURE,
    'artifacts': FEATURE_ARTIFACTS,
    'document_writer': FEATURE_DOCUMENT_WRITER,
    'playground': FEATURE_PLAYGROUND,
    'chat_overview': FEATURE_CHAT_OVERVIEW,
    'notes_ai_controls': FEATURE_NOTES_AI_CONTROLS,
    'voice': FEATURE_VOICE,
    'voice_call': FEATURE_VOICE and FEATURE_VOICE_CALL,
    'read_aloud': FEATURE_VOICE and FEATURE_READ_ALOUD,
    'changelog': FEATURE_CHANGELOG,
    'meetings': FEATURE_MEETINGS,
    'system_prompt': FEATURE_SYSTEM_PROMPT,
    'models': FEATURE_MODELS,
    'knowledge': FEATURE_KNOWLEDGE,
    'prompts': FEATURE_PROMPTS,
    'tools': FEATURE_TOOLS,
    'skills': FEATURE_SKILLS,
    'tool_servers': FEATURE_TOOL_SERVERS,
    'terminal_servers': FEATURE_TERMINAL_SERVERS,
    'builtin_tools': FEATURE_BUILTIN_TOOLS,
}


def is_feature_enabled(feature: Feature) -> bool:
    """
    Check if a feature is enabled globally.

    Args:
        feature: The feature identifier to check

    Returns:
        True if the feature is enabled, False otherwise
    """
    return FEATURE_FLAGS.get(feature, True)


async def feature_enabled(feature: str, legacy: bool | list[str] | None = None) -> bool | list[str]:
    from open_webui.soev import product_config

    if product_config.v2_enabled():
        return await product_config.get(f'feature.{feature}')
    if legacy is not None:
        return legacy
    if feature not in FEATURE_FLAGS:
        from open_webui import config

        return getattr(config, f'FEATURE_{feature.upper()}')
    return is_feature_enabled(feature)


def require_platform_feature(key: str):
    from fastapi import Depends
    from open_webui.utils.auth import get_verified_user

    async def check(user=Depends(get_verified_user)):
        from open_webui.soev import product_config

        if product_config.v2_enabled() and not await product_config.get(key):
            raise HTTPException(status_code=403, detail='Feature is unavailable')

    return check


def require_feature(feature: str):
    """
    FastAPI dependency that raises 403 if feature is disabled.

    Usage:
        @router.get("/some-endpoint")
        async def endpoint(
            user=Depends(get_current_user),
            _=Depends(require_feature("playground"))
        ):
            ...

    Args:
        feature: The feature identifier to require

    Returns:
        A dependency function that raises HTTPException if feature is disabled
    """

    async def check_feature():
        if not await feature_enabled(feature):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Feature '{feature}' is not available in your plan",
            )
        return True

    return check_feature
