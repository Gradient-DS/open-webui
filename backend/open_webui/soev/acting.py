"""Carry the authenticated OWUI user's external ref and OIDC access token through the request."""

import asyncio
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from functools import wraps

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials

from open_webui.soev.request_cache import request_cache
from open_webui.soev.sessions import platform_mode

_requester_ref: ContextVar[str | None] = ContextVar('soev_requester_ref', default=None)
_token_resolver: ContextVar[Callable[..., Awaitable[str | None]] | None] = ContextVar(
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


async def refresh_user_token(as_user: str | None, rejected_token: str) -> str | None:
    resolver = _token_resolver.get()
    if resolver is None or as_user is None or as_user != _requester_ref.get():
        return None
    return await resolver(rejected_token=rejected_token)


def oidc_token_resolver(request: Request, user, auth_token) -> Callable[..., Awaitable[str | None]]:
    """Share owned OIDC token lookups and refreshes under one request-local lock."""
    cached: dict[str, str | None] = {}
    lock = asyncio.Lock()

    async def fetch(rejected_token: str | None = None) -> str | None:
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
        manager = request.app.state.oauth_manager
        if rejected_token is not None and session.token.get('access_token') == rejected_token:
            oauth_token = await manager.get_oauth_token(user.id, session_id, force_refresh=True)
        else:
            oauth_token = await manager.get_oauth_token(user.id, session_id)
        return oauth_token.get('access_token') if oauth_token else None

    async def resolve(*, rejected_token: str | None = None) -> str | None:
        async with lock:
            if 'token' not in cached or (rejected_token is not None and cached['token'] == rejected_token):
                cached['token'] = await fetch(rejected_token)
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
        from open_webui.soev.client import SoevApiError

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

                if request.method == 'GET' and request.url.path.startswith(
                    ('/api/v1/knowledge/', '/api/v1/chats/', '/api/v1/folders/')
                ):
                    await pending_sync.on_request(user.id, background_tasks)
                    with jobs.defer_owner_jobs(user.id):
                        yield user
                else:
                    await jobs.reconcile_owner_jobs(user.id)
                    await pending_sync.on_request(user.id, background_tasks)
                    yield user
        except (SoevApiError, HTTPException) as error:
            status = error.status if isinstance(error, SoevApiError) else error.status_code
            if platform_mode() and status == 401:
                # A valid OWUI JWT cannot extend refused platform login authority.
                raise HTTPException(
                    401,
                    detail={'state': 'sign_in'},
                    headers={'Cache-Control': 'private, no-store', 'Vary': 'Authorization, Cookie'},
                ) from None
            raise
        finally:
            _token_resolver.reset(resolver)
            _requester_ref.reset(requester)

    app.dependency_overrides[get_current_user] = with_acting_user
