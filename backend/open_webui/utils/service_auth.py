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

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials
from open_webui.models.users import UserModel, Users
from open_webui.utils.auth import bearer_security

log = logging.getLogger(__name__)

# Constant `agent_id` on every AgentPrincipal. We don't distinguish per-agent
# today — there's a single shared `AGENT_API_KEY` for the agent ↔ open-webui
# trust boundary (same key both directions, same per-tenant namespace). Kept
# as a typed field so a future per-agent rotation can re-introduce the
# distinction without changing call sites or log shape.
_AGENT_ID_DEFAULT = 'agent'


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
    auth_token: HTTPAuthorizationCredentials | None = Depends(bearer_security),
    x_acting_user_id: str | None = Header(default=None, alias='X-Acting-User-Id'),
) -> AgentPrincipal:
    """Resolve the caller as an :class:`AgentPrincipal`.

    The agent endpoint is machine-only. A missing
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
    user = await Users.get_user_by_id(x_acting_user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"acting user '{x_acting_user_id}' not found",
        )
    return AgentPrincipal(agent_id=_AGENT_ID_DEFAULT, user=user)
