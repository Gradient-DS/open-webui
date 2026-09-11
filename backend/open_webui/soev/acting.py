"""Carry the authenticated OWUI user's external ref through a request's dependency context."""

from contextvars import ContextVar
from functools import wraps

from fastapi import Depends, FastAPI

_acting_ref: ContextVar[str | None] = ContextVar('soev_acting_ref', default=None)


def acting_ref() -> str | None:
    return _acting_ref.get()


def install(app: FastAPI) -> None:
    from open_webui.soev import identity
    from open_webui.utils.auth import get_current_user

    @wraps(get_current_user)
    async def upstream_user(*args, **kwargs):
        return await get_current_user(*args, **kwargs)

    async def with_acting_user(user=Depends(upstream_user)):
        _acting_ref.set(identity.external_ref(user))
        return user

    app.dependency_overrides[get_current_user] = with_acting_user
