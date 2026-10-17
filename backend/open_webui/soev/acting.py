"""Carry the authenticated OWUI user's external ref through a request's dependency context."""

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

from fastapi import Depends, FastAPI

from open_webui.soev.request_cache import request_cache

_acting_ref: ContextVar[str | None] = ContextVar('soev_acting_ref', default=None)


def acting_ref() -> str | None:
    return _acting_ref.get()


@contextmanager
def acting_as(ref: str):
    """[Gradient] Assert `ref` instead of the requesting user inside the block: an owner's own change that OWUI
    makes for them, such as the grants a folder share adds to their knowledge bases."""
    token = _acting_ref.set(ref)
    try:
        yield
    finally:
        _acting_ref.reset(token)


def install(app: FastAPI) -> None:
    from open_webui.soev import identity
    from open_webui.utils.auth import get_current_user

    identity.validate_config()

    @wraps(get_current_user)
    async def upstream_user(*args, **kwargs):
        return await get_current_user(*args, **kwargs)

    async def with_acting_user(user=Depends(upstream_user)):
        token = _acting_ref.set(identity.external_ref(user))
        try:
            with request_cache():
                yield user
        finally:
            _acting_ref.reset(token)

    app.dependency_overrides[get_current_user] = with_acting_user
