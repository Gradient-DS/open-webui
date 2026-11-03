"""Tool approvals are retired on the platform until approval tools return."""

from fastapi import Depends

from open_webui.soev.thread_content import refuse_local_content
from open_webui.utils.auth import get_verified_user


async def require_legacy_approval(user=Depends(get_verified_user)) -> None:
    refuse_local_content()
