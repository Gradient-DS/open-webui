"""Carry the authenticated OWUI user's external ref and OIDC access token through the request."""

import asyncio
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from functools import wraps

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials

from open_webui.soev.request_cache import request_cache

_requester_ref: ContextVar[str | None] = ContextVar('soev_requester_ref', default=None)
_token_resolver: ContextVar[Callable[[], Awaitable[str | None]] | None] = ContextVar(
    'soev_token_resolver', default=None
)


def requester_ref() -> str | None:
    return _requester_ref.get()


def require_owner(owner_id: str) -> None:
    """[Gradient] Raise unless the request is `owner_id`'s own: sharing work never runs for someone else."""
    if requester_ref() != f'owui:user:{owner_id}':
        raise PermissionError('Sharing reconciliation requires the owner request')


async def user_token(as_user: str | None) -> str | None:
    """[Gradient] The requesting user's OIDC access token, only when the call acts as that same user."""
    resolver = _token_resolver.get()
    if resolver is None or as_user is None or as_user != _requester_ref.get():
        return None
    return await resolver()


def oidc_token_resolver(request: Request, user, auth_token) -> Callable[[], Awaitable[str | None]]:
    """Fetch (and refresh) the user's `oidc` session token on first use, once per request.

    Concurrent soev calls in one request share the single lookup instead of seeing it unfinished.
    """
    cached: dict[str, str | None] = {}
    lock = asyncio.Lock()

    async def fetch() -> str | None:
        from open_webui.models.oauth_sessions import OAuthSessions

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
        return oauth_token.get('access_token') if oauth_token else None

    async def resolve() -> str | None:
        async with lock:
            if 'token' not in cached:
                cached['token'] = await fetch()
            return cached['token']

    return resolve


def install(app: FastAPI) -> None:
    from open_webui.soev import identity
    from open_webui.utils.auth import bearer_security, get_current_user

    identity.validate_config()

    @wraps(get_current_user)
    async def upstream_user(*args, **kwargs):
        return await get_current_user(*args, **kwargs)

    async def with_acting_user(
        request: Request,
        background_tasks: BackgroundTasks,
        user=Depends(upstream_user),
        auth_token: HTTPAuthorizationCredentials = Depends(bearer_security),
    ):
        ref = identity.external_ref(user)
        requester = _requester_ref.set(ref)
        resolver = _token_resolver.set(oidc_token_resolver(request, user, auth_token))
        try:
            with request_cache():
                if identity.config.SOEV_API_URL and user.person_id is None:
                    access_token = await user_token(ref)
                    if access_token:
                        from open_webui.soev.persons import PersonMismatchError, remember_person

                        try:
                            await remember_person(user, access_token)
                        except PersonMismatchError:
                            raise HTTPException(status_code=401, detail='Identity could not be verified') from None
                from open_webui.soev import jobs, pending_sync

                await jobs.complete_references(user.id)
                await pending_sync.on_request(user.id, background_tasks)
                yield user
        finally:
            _token_resolver.reset(resolver)
            _requester_ref.reset(requester)

    app.dependency_overrides[get_current_user] = with_acting_user
