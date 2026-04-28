"""Machine-auth dependencies for service-to-service callers.

Sibling to :mod:`open_webui.utils.auth` for the cases where the caller is
another in-cluster service (e.g. the per-tenant ``gradient-loader-worker``)
rather than a human with a session cookie. The bearer key authenticates the
*machine*; the acting user/provider identity is carried in headers and looked
up against the existing ``users`` table — no DB service-account row.
"""

from __future__ import annotations

import hmac
import logging
import os
from dataclasses import dataclass
from typing import Optional

from fastapi import BackgroundTasks, Depends, Header, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials

from open_webui.constants import ERROR_MESSAGES
from open_webui.models.users import Users, UserModel
from open_webui.utils.auth import bearer_security, get_current_user

log = logging.getLogger(__name__)

# Constant `agent_id` on every AgentPrincipal. We don't distinguish per-agent
# today — there's a single shared `AGENT_API_KEY` for the agent ↔ open-webui
# trust boundary (same key both directions, same per-tenant namespace). Kept
# as a typed field so a future per-agent rotation can re-introduce the
# distinction without changing call sites or log shape.
_AGENT_ID_DEFAULT = 'agent'


@dataclass
class LoaderPrincipal:
    """Machine principal for the loader-worker → ``/ingest`` callback.

    Wraps a real user resolved from ``X-Acting-User-Id``. The bearer
    authenticates the loader-worker; ``provider_slug`` overrides the
    provider that would otherwise come from ``user.info``.
    """

    user: UserModel
    provider_slug: str

    @property
    def id(self) -> str:
        return self.user.id


def _loader_key_matches(token: str) -> bool:
    configured = os.environ.get('LOADER_INGEST_API_KEY', '')
    if not configured:
        return False
    return hmac.compare_digest(token, configured)


async def get_integration_principal(
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    auth_token: Optional[HTTPAuthorizationCredentials] = Depends(bearer_security),
    x_acting_user_id: Optional[str] = Header(default=None, alias='X-Acting-User-Id'),
    x_acting_provider: Optional[str] = Header(default=None, alias='X-Acting-Provider'),
):
    """Resolve the caller as either a ``LoaderPrincipal`` or a regular user.

    Bearer matching ``LOADER_INGEST_API_KEY`` takes the machine path and
    requires both acting headers; anything else falls through to the existing
    user-cookie / API-key path via :func:`get_current_user`. Acting headers are
    ignored on the cookie path.
    """

    if auth_token is not None and auth_token.credentials and _loader_key_matches(auth_token.credentials):
        if not x_acting_user_id or not x_acting_provider:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='X-Acting-User-Id and X-Acting-Provider are required when authenticating with the loader bearer key',
            )
        user = Users.get_user_by_id(x_acting_user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"acting user '{x_acting_user_id}' not found",
            )
        return LoaderPrincipal(user=user, provider_slug=x_acting_provider)

    user = await get_current_user(
        request,
        response,
        background_tasks,
        auth_token=auth_token,
    )
    if user.role not in {'user', 'admin'}:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )
    return user


@dataclass
class AgentPrincipal:
    """Machine principal for an external agent acting on behalf of a user.

    The bearer authenticates the agent against ``AGENT_API_KEY`` (the same
    env var open-webui uses to authenticate its outbound calls to the agent
    service — single shared secret across the per-tenant trust boundary).
    The acting user comes from ``X-Acting-User-Id`` and is resolved against
    the local ``users`` table.
    """

    agent_id: str
    user: UserModel

    @property
    def id(self) -> str:
        return self.user.id


def _agent_key_matches(token: str) -> bool:
    """Constant-time check of the inbound bearer against ``AGENT_API_KEY``."""
    configured = os.environ.get('AGENT_API_KEY', '')
    if not configured:
        return False
    return hmac.compare_digest(token, configured)


async def get_agent_principal(
    request: Request,
    auth_token: Optional[HTTPAuthorizationCredentials] = Depends(bearer_security),
    x_acting_user_id: Optional[str] = Header(default=None, alias='X-Acting-User-Id'),
) -> AgentPrincipal:
    """Resolve the caller as an :class:`AgentPrincipal`.

    Unlike :func:`get_integration_principal`, this dependency has no
    user-cookie fall-through — the agent endpoint is machine-only. A missing
    or invalid bearer is a hard 401; missing ``X-Acting-User-Id`` is a 400.
    """

    if auth_token is None or not auth_token.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='missing bearer token',
        )
    if not _agent_key_matches(auth_token.credentials):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='invalid agent bearer',
        )
    if not x_acting_user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='X-Acting-User-Id header is required',
        )
    user = Users.get_user_by_id(x_acting_user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"acting user '{x_acting_user_id}' not found",
        )
    return AgentPrincipal(agent_id=_AGENT_ID_DEFAULT, user=user)
