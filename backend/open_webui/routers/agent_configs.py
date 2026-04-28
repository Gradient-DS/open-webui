"""[Gradient] AgentConfig CRUD + detection endpoints.

Admin-only endpoints under ``/api/v1/agent-configs/`` manage display
metadata and access grants for slugs declared in ``AGENT_API_AGENTS``.
The user-facing list endpoint returns only is_active rows the user has
read access to (or [] when the master flag ``FEATURE_AGENT_PICKER`` or
``AGENT_API_ENABLED`` is off).
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from open_webui.env import (
    AGENT_API_AGENTS,
    AGENT_API_ENABLED,
    FEATURE_AGENT_PICKER,
)
from open_webui.models.agent_configs import (
    AgentConfigForm,
    AgentConfigModel,
    AgentConfigUserResponse,
    AgentConfigs,
)
from open_webui.models.groups import Groups
from open_webui.utils.auth import get_admin_user, get_verified_user

router = APIRouter()
log = logging.getLogger(__name__)


class AgentConfigDetectionRow(BaseModel):
    slug: str
    in_env: bool
    configured: bool
    config: Optional[AgentConfigModel] = None


@router.get('/detect', response_model=list[AgentConfigDetectionRow])
async def list_detected_agents(user=Depends(get_admin_user)):
    """Admin: env slugs joined with configured rows.

    Returns one row per known slug — either present in ``AGENT_API_AGENTS``
    or persisted as a config row but no longer in env (orphaned).
    """
    env_slugs = set(AGENT_API_AGENTS)
    configured = {c.id: c for c in AgentConfigs.list_all()}
    all_slugs = sorted(env_slugs | set(configured.keys()))
    return [
        AgentConfigDetectionRow(
            slug=s,
            in_env=s in env_slugs,
            configured=s in configured,
            config=configured.get(s),
        )
        for s in all_slugs
    ]


@router.get('/', response_model=list[AgentConfigUserResponse])
async def list_visible_agents(user=Depends(get_verified_user)):
    """User-facing: only is_active rows the user has read access to.

    If ``FEATURE_AGENT_PICKER`` or ``AGENT_API_ENABLED`` is off, returns [].
    Admins see every is_active row regardless of access grants — same
    bypass as ``BYPASS_ADMIN_ACCESS_CONTROL`` on models/knowledge.
    """
    if not FEATURE_AGENT_PICKER or not AGENT_API_ENABLED:
        return []
    if user.role == 'admin':
        rows = [r for r in AgentConfigs.list_all() if r.is_active]
    else:
        user_group_ids = {g.id for g in Groups.get_groups_by_member_id(user.id)}
        rows = AgentConfigs.list_visible_to_user(user.id, user_group_ids)
    return [
        AgentConfigUserResponse(
            id=r.id,
            name=r.name,
            description=r.description,
            profile_image_url=r.profile_image_url,
            cta_copy=r.cta_copy,
            is_beta=r.is_beta,
        )
        for r in rows
    ]


@router.post('/{slug}', response_model=AgentConfigModel)
async def create_agent_config(slug: str, form: AgentConfigForm, user=Depends(get_admin_user)):
    if slug not in AGENT_API_AGENTS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f'{slug!r} is not in AGENT_API_AGENTS',
        )
    if AgentConfigs.get_agent_config_by_id(slug):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f'AgentConfig for {slug!r} already exists',
        )
    return AgentConfigs.insert_new_agent_config(user.id, slug, form)


@router.post('/{slug}/update', response_model=AgentConfigModel)
async def update_agent_config(slug: str, form: AgentConfigForm, user=Depends(get_admin_user)):
    res = AgentConfigs.update_agent_config(slug, form)
    if not res:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return res


@router.delete('/{slug}')
async def delete_agent_config(slug: str, user=Depends(get_admin_user)):
    ok = AgentConfigs.delete_agent_config(slug)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return {'ok': True}


@router.get('/{slug}', response_model=AgentConfigModel)
async def get_agent_config(slug: str, user=Depends(get_admin_user)):
    res = AgentConfigs.get_agent_config_by_id(slug)
    if not res:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return res
