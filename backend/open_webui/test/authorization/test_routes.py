"""Every registered HTTP surface must declare authentication or a public contract."""

import importlib.util

import pytest
from fastapi import Depends, FastAPI
from starlette.routing import Mount, WebSocketRoute

PUBLIC_ROUTES = {
    'MOUNT /ws': 'Socket.IO transport handshake is public; socket events authenticate their tokens.',
    'GET /ollama/': 'Constant compatibility health status; no backend call.',
    'HEAD /ollama/': 'Constant compatibility health status; no backend call.',
    'POST /api/v1/auths/password/forgot': 'Starts password recovery before login.',
    'GET /api/v1/auths/password/reset/{token}/validate': 'Validates a recovery capability before login.',
    'POST /api/v1/auths/password/reset': 'Authenticates the reset capability inside the handler.',
    'POST /api/v1/auths/ldap': 'Authenticates LDAP credentials to establish a session.',
    'POST /api/v1/auths/signin': 'Authenticates credentials to establish a session.',
    'POST /api/v1/auths/signup': 'Creates an account when registration policy allows it.',
    'POST /api/v1/auths/signout': 'Clears session cookies, including expired sessions.',
    'POST /api/v1/auths/oauth/{provider}/token/exchange': 'Authenticates an external OAuth token to establish a session.',
    'POST /api/v1/auths/2fa/verify': 'Consumes a partial-login token and second factor before a full session exists.',
    'POST /api/v1/channels/webhooks/{webhook_id}/{token}': 'Webhook capability token is validated inside the handler.',
    'GET /api/v1/chats/share/{share_id}': 'Anyone-read shares are anonymous; other shares check identity.',
    'WEBSOCKET /api/v1/terminals/{server_id}/api/terminals/{session_id}': 'Public upgrade; first message authenticates before connecting upstream.',
    'GET /api/v1/invites/{token}/validate': 'Validates an invitation capability before account creation.',
    'POST /api/v1/invites/{token}/accept': 'Consumes an invitation capability to create an account.',
    'GET /api/config': 'Login screen bootstrap exposes the public configuration subset.',
    'GET /api/version': 'Public application version metadata.',
    'GET /api/changelog': 'Public release notes.',
    'GET /oauth/clients/{client_id}/callback': 'Completes external-client OAuth with callback state validation.',
    'GET /oauth/{provider}/login': 'Starts federated login before a session exists.',
    'GET /oauth/{provider}/callback': 'Completes federated login with OAuth state validation.',
    'GET /oauth/{provider}/login/callback': 'Alternate federated-login callback with OAuth state validation.',
    'POST /oauth/backchannel-logout': 'Validates the identity provider logout token inside the handler.',
    'GET /manifest.json': 'Public PWA installation metadata.',
    'GET /opensearch.xml': 'Public browser search integration metadata.',
    'GET /health': 'Unauthenticated liveness probe.',
    'GET /ready': 'Unauthenticated readiness probe.',
    'GET /health/db': 'Unauthenticated database health probe.',
    'MOUNT /static': 'Public static frontend assets needed before login.',
}

# Registered only when ENV == 'dev' (main.py docs_url/openapi_url).
DEV_PUBLIC_ROUTES = {
    'GET /openapi.json': 'Development API schema for API clients.',
    'HEAD /openapi.json': 'Metadata for the development API schema.',
    'GET /docs': 'Development API documentation UI.',
    'HEAD /docs': 'Metadata for the development documentation UI.',
    'GET /docs/oauth2-redirect': 'OAuth callback used by the development documentation UI.',
    'HEAD /docs/oauth2-redirect': 'Metadata for the documentation OAuth callback.',
}


def authentication_dependencies():
    from open_webui.routers.scim import get_scim_auth
    from open_webui.routers.totp import get_2fa_setup_user
    from open_webui.utils.auth import get_admin_user, get_current_user, get_verified_user
    from open_webui.utils.service_auth import get_agent_principal

    return {
        get_verified_user,
        get_admin_user,
        get_current_user,  # Real session authentication, also allows pending accounts.
        get_2fa_setup_user,  # Session or signed, purpose-scoped enrollment token.
        get_agent_principal,  # Service bearer plus a database-resolved acting user.
        get_scim_auth,  # Configured SCIM service bearer.
    }


def walk_routes(routes, prefix=''):
    for route in routes:
        path = prefix + route.path
        if isinstance(route, Mount) and getattr(route, 'routes', None):
            yield from walk_routes(route.routes, path)
        else:
            fallback = ('WEBSOCKET',) if isinstance(route, WebSocketRoute) else ('MOUNT',)
            for method in sorted(getattr(route, 'methods', None) or fallback):
                yield f'{method} {path}', route


def dependency_calls(dependant):
    if dependant is not None:
        yield dependant.call
        for dependency in dependant.dependencies:
            yield from dependency_calls(dependency)


def audit_routes(routes, public, authenticated):
    seen, unguarded = set(), []
    for key, route in walk_routes(routes):
        seen.add(key)
        calls = set(dependency_calls(getattr(route, 'dependant', None)))
        if key not in public and not calls.intersection(authenticated):
            unguarded.append(key)
    assert not public.keys() - seen, f'Stale public routes: {sorted(public.keys() - seen)}'
    assert not unguarded, f'Routes without authentication: {unguarded}'


def test_route_authentication_inventory(application):
    from open_webui.env import ENV

    public = PUBLIC_ROUTES | (DEV_PUBLIC_ROUTES if ENV == 'dev' else {})
    audit_routes(application.routes, public, authentication_dependencies())


@pytest.fixture
def dev_retrieval_router(monkeypatch):
    """The retrieval router as built under ENV=dev, where it adds the /ef probe."""
    import open_webui.config
    import open_webui.routers.retrieval as retrieval

    monkeypatch.setattr(open_webui.config, 'ENV', 'dev')
    spec = importlib.util.spec_from_file_location('retrieval_dev', retrieval.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.router


def test_dev_only_routes_require_authentication(dev_retrieval_router):
    """Dev-only routes are invisible to the ENV=prod sweep, so audit them directly."""
    audit_routes(dev_retrieval_router.routes, {}, authentication_dependencies())


@pytest.mark.parametrize('principal', (None, 'owner', 'admin'))
def test_embedding_probe_requires_admin_before_embedding(seeded, dev_retrieval_router, principal):
    probe = FastAPI()
    probe.include_router(dev_retrieval_router, prefix='/api/v1/retrieval')
    route = next(route for route in probe.routes if getattr(route, 'path', '') == '/api/v1/retrieval/ef/{text}')
    seeded.client.app.router.routes.insert(0, route)
    calls = []

    async def embed(text, **kwargs):
        calls.append(text)
        return [1.0]

    seeded.client.app.state.EMBEDDING_FUNCTION = embed
    response = seeded.client.get('/api/v1/retrieval/ef/probe', headers=seeded.headers(principal) if principal else {})
    assert response.status_code == (200 if principal == 'admin' else 401)
    assert calls == (['probe'] if principal == 'admin' else [])


def test_sweep_rejects_new_unprotected_mounted_route():
    app, child = FastAPI(), FastAPI()
    child.add_api_route('/private', lambda: {})
    app.mount('/child', child)
    public = {key: 'Framework documentation.' for key, route in walk_routes(app.routes) if key != 'GET /child/private'}
    with pytest.raises(AssertionError, match='/child/private'):
        audit_routes(app.routes, public, set())


def test_sweep_rejects_stale_allowlist():
    with pytest.raises(AssertionError, match='Stale public routes'):
        audit_routes([], {'GET /removed': 'Removed public endpoint.'}, set())


def test_sweep_follows_transitive_dependencies():
    def authentication():
        return 'user'

    def nested(user=Depends(authentication)):
        return user

    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
    app.add_api_route('/private', lambda: {}, dependencies=[Depends(nested)])
    audit_routes(app.routes, {}, {authentication})
