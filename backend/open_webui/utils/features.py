"""
Feature flag utilities for SaaS tier-based feature control.

These flags apply to ALL users including admins and cannot be overridden
via the admin panel. Use environment variables to control features per deployment.
"""

from typing import Literal
from fastapi import HTTPException, status

from open_webui.config import (
    FEATURE_CHAT_CONTROLS,
    FEATURE_CAPTURE,
    FEATURE_ARTIFACTS,
    FEATURE_PLAYGROUND,
    FEATURE_CHAT_OVERVIEW,
    FEATURE_NOTES_AI_CONTROLS,
    FEATURE_VOICE,
    FEATURE_CHANGELOG,
    FEATURE_SYSTEM_PROMPT,
    FEATURE_MODELS,
    FEATURE_KNOWLEDGE,
    FEATURE_PROMPTS,
    FEATURE_TOOLS,
    FEATURE_ADMIN_EVALUATIONS,
    FEATURE_ADMIN_FUNCTIONS,
    FEATURE_ADMIN_SETTINGS,
)

Feature = Literal[
    "chat_controls",
    "capture",
    "artifacts",
    "playground",
    "chat_overview",
    "notes_ai_controls",
    "voice",
    "changelog",
    "system_prompt",
    "models",
    "knowledge",
    "prompts",
    "tools",
    "admin_evaluations",
    "admin_functions",
    "admin_settings",
]

FEATURE_FLAGS: dict[Feature, bool] = {
    "chat_controls": FEATURE_CHAT_CONTROLS,
    "capture": FEATURE_CAPTURE,
    "artifacts": FEATURE_ARTIFACTS,
    "playground": FEATURE_PLAYGROUND,
    "chat_overview": FEATURE_CHAT_OVERVIEW,
    "notes_ai_controls": FEATURE_NOTES_AI_CONTROLS,
    "voice": FEATURE_VOICE,
    "changelog": FEATURE_CHANGELOG,
    "system_prompt": FEATURE_SYSTEM_PROMPT,
    "models": FEATURE_MODELS,
    "knowledge": FEATURE_KNOWLEDGE,
    "prompts": FEATURE_PROMPTS,
    "tools": FEATURE_TOOLS,
    "admin_evaluations": FEATURE_ADMIN_EVALUATIONS,
    "admin_functions": FEATURE_ADMIN_FUNCTIONS,
    "admin_settings": FEATURE_ADMIN_SETTINGS,
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


def require_feature(feature: Feature):
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
    def check_feature():
        if not is_feature_enabled(feature):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Feature '{feature}' is not available in your plan"
            )
        return True
    return check_feature
