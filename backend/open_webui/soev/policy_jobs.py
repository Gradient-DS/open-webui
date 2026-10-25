"""Product boundaries for work that needs a person's live request."""

from open_webui import config, env
from open_webui.soev.client import SoevApiError


def v2_enabled() -> bool:
    return bool(env.AGENT_API_ENABLED and config.SOEV_API_URL)


def refuse_offboarding() -> None:
    if v2_enabled():
        raise SoevApiError(403, 'policy_forbids', 'Console offboarding required', constraint='offboarding:console')


def refuse_automations() -> None:
    if v2_enabled():
        raise SoevApiError(403, 'policy_forbids', 'Automations unavailable', constraint='automations:unavailable')


async def require_thread_owner(user_id: str) -> None:
    from open_webui.soev import acting

    ref = f'owui:user:{user_id}'
    if acting.requester_ref() != ref or not await acting.user_token(ref):
        raise SoevApiError(403, 'policy_forbids', 'Owner request required', constraint='thread:owner_request')
