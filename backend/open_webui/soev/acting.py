"""Carry the authenticated OWUI user's external ref and OIDC access token through the request."""

from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Awaitable, Callable
from functools import wraps

from fastapi import Depends, FastAPI, Request
from fastapi.security import HTTPAuthorizationCredentials

from open_webui.soev.request_cache import request_cache

_acting_ref: ContextVar[str | None] = ContextVar('soev_acting_ref', default=None)
_requester_ref: ContextVar[str | None] = ContextVar('soev_requester_ref', default=None)
_token_resolver: ContextVar[Callable[[], Awaitable[str | None]] | None] = ContextVar(
    'soev_token_resolver', default=None
)


def acting_ref() -> str | None:
    return _acting_ref.get()


async def user_token(as_user: str | None) -> str | None:
    """[Gradient] The requesting user's OIDC access token, only when the call acts as that same user."""
    resolver = _token_resolver.get()
    if resolver is None or as_user is None or as_user != _requester_ref.get():
        return None
    return await resolver()


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
    from open_webui.models.oauth_sessions import OAuthSessions
    from open_webui.soev import identity
    from open_webui.utils.auth import bearer_security, get_current_user

    identity.validate_config()

    @wraps(get_current_user)
    async def upstream_user(*args, **kwargs):
        return await get_current_user(*args, **kwargs)

    async def with_acting_user(
        request: Request,
        user=Depends(upstream_user),
        auth_token: HTTPAuthorizationCredentials = Depends(bearer_security),
    ):
        ref = identity.external_ref(user)
        token = _acting_ref.set(ref)
        requester = _requester_ref.set(ref)
        resolver = _token_resolver.set(_oidc_token_resolver(request, user, auth_token))
        try:
            with request_cache():
                yield user
        finally:
            _token_resolver.reset(resolver)
            _requester_ref.reset(requester)
            _acting_ref.reset(token)

    def _oidc_token_resolver(request: Request, user, auth_token):
        """Fetch (and refresh) the user's `oidc` session token on first use, once per request."""
        cached: dict[str, str | None] = {}

        async def resolve() -> str | None:
            if 'token' in cached:
                return cached['token']
            cached['token'] = None
            # Match upstream's credential precedence, including x-api-key middleware.
            credential = auth_token.credentials if auth_token is not None else request.cookies.get('token')
            if credential is None and getattr(request.state, 'token', None):
                credential = request.state.token.credentials
            session_id = request.cookies.get('oauth_session_id')
            if not session_id or (credential or '').startswith('sk-'):
                return None
            session = await OAuthSessions.get_session_by_id_and_user_id(session_id, user.id)
            if session is None or session.provider != 'oidc':
                return None
            oauth_token = await request.app.state.oauth_manager.get_oauth_token(user.id, session_id)
            cached['token'] = oauth_token.get('access_token') if oauth_token else None
            return cached['token']

        return resolve

    app.dependency_overrides[get_current_user] = with_acting_user
