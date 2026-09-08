"""
Pure-ASGI replacements for the project's previous
`@app.middleware('http')` / `BaseHTTPMiddleware` middlewares.

Why this matters
----------------
Starlette's `BaseHTTPMiddleware` (which `@app.middleware('http')` is
sugar for) runs the downstream app inside an `anyio` task group. When
the wrapper exits — for any reason: response complete, client
disconnect, an outer middleware bailing out — the task group cancels
the inner task. That `CancelledError` then propagates into whatever
the inner task was doing, including in-flight DB queries, embedding
calls and disk I/O.

In Open WebUI this surfaces as:

* SQLAlchemy logging multi-page `NotImplementedError:
  terminate_force_close()` tracebacks at ERROR every time a request is
  cancelled mid-DB-call (the aiosqlite connector cleanup path).
* Spurious cancellations cascading through the four stacked
  `@app.middleware('http')` wrappers.

Pure ASGI middleware does not introduce a cancel scope around the
downstream app, so client disconnects propagate the way ASGI was
designed to (via `receive()` returning `http.disconnect`) instead of
being injected as `CancelledError` into arbitrary `await` points.

Reference: https://www.starlette.io/middleware/#limitations
"""

from __future__ import annotations

import logging
import os
import re
import time
from urllib.parse import parse_qs, urlencode

from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials
from open_webui.env import CUSTOM_API_KEY_HEADER
from open_webui.internal.db import ScopedSession
from open_webui.utils.auth import get_http_authorization_cred
from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

log = logging.getLogger(__name__)


class AppHTTPMiddleware:
    """Open WebUI's pure-ASGI HTTP middleware.

    Keeps the app's request-wide behavior in one middleware layer without
    hiding the old concerns behind a stack of wrappers:

    * reject malformed `/ws/socket.io` upgrade requests
    * stash bearer/cookie/API-key credentials on `request.state.token`
    * stamp `X-Process-Time` and configured security headers
    * serve the legacy `/watch` and `?shared=` redirects
    * commit and release the thread-local sync `ScopedSession`

    Most requests now use the async session; the sync ScopedSession is
    only touched by startup, healthchecks, and a handful of legacy
    helpers (notably the pgvector / opengauss vector-DB clients). The
    middleware exists so that PostgreSQL connections do not accumulate
    as "idle in transaction" and so that any pending sync work made
    inside the request is durably persisted.

    Failure semantics
    -----------------
    * Downstream raised → roll back any pending sync work, release the
      connection, and re-raise so the outer exception middleware can
      turn it into an error response. We never commit work on a
      request that did not complete successfully.
    * Downstream returned → commit pending sync work; on commit
      failure, log loudly, roll back, and re-raise. Note that in pure
      ASGI the response messages have already been emitted by the
      time `await self.app(...)` returns, so a commit failure cannot
      retroactively change what the client sees on the wire — but
      re-raising still surfaces the error in logs and to ASGI servers
      that expose it. We deliberately do not buffer the response to
      gate it on commit success, because that would defeat streaming
      responses (chat completions, SSE) which are core to the app.

    For request paths where commit-before-send is required, manage the
    sync session explicitly inside the handler instead of relying on
    this middleware.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        # Headers derive only from env vars, which are static for the process
        # lifetime — compute them once instead of per response.
        self._security_headers = list(set_security_headers().items())

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return

        if await self._reject_invalid_websocket(scope, receive, send):
            return

        start_time = time.monotonic()
        request = Request(scope)
        self._set_token(request)
        send_with_headers = self._send_with_headers(send, start_time)

        try:
            if await self._redirect_legacy_url(scope, receive, send_with_headers):
                pass
            # Keep health probes independent from sync session commit/remove so DB
            # pressure cannot delay or fail probe responses.
            elif scope.get('path', '') in {'/health', '/ready', '/health/db'}:
                await self.app(scope, receive, send_with_headers)
                return
            else:
                await self.app(scope, receive, send_with_headers)
        except BaseException:
            self._rollback_session('AppHTTPMiddleware: rollback failed after downstream error')
            raise

        self._commit_session()

    def _set_token(self, request: Request) -> None:
        token = get_http_authorization_cred(request.headers.get('Authorization'))
        if token is None and (cookie_token := request.cookies.get('token')):
            token = HTTPAuthorizationCredentials(scheme='Bearer', credentials=cookie_token)
        # [Gradient] Limit the legacy API-key header to message endpoints.
        if (
            token is None
            and request.url.path.startswith(('/api/message', '/api/v1/messages', '/ollama/v1/messages'))
            and (api_key := request.headers.get(CUSTOM_API_KEY_HEADER))
        ):
            token = HTTPAuthorizationCredentials(scheme='Bearer', credentials=api_key)
        request.state.token = token

    def _send_with_headers(self, send: Send, start_time: float) -> Send:
        async def send_with_headers(message: Message) -> None:
            if message['type'] == 'http.response.start':
                headers = MutableHeaders(scope=message)
                headers['X-Process-Time'] = f'{time.monotonic() - start_time:.6f}'
                for key, value in self._security_headers:
                    headers[key] = value
            await send(message)

        return send_with_headers

    async def _reject_invalid_websocket(self, scope: Scope, receive: Receive, send: Send) -> bool:
        path = scope.get('path', '')
        if '/ws/socket.io' not in path:
            return False

        query_params = parse_qs(scope.get('query_string', b'').decode('latin-1', errors='replace'))
        if query_params.get('transport', [''])[0] != 'websocket':
            return False

        headers = _scope_headers(scope)
        upgrade = headers.get('upgrade', '').lower()
        connection_tokens = [token.strip() for token in headers.get('connection', '').lower().split(',')]
        if upgrade == 'websocket' and 'upgrade' in connection_tokens:
            return False

        response = JSONResponse(status_code=400, content={'detail': 'Invalid WebSocket upgrade request'})
        await response(scope, receive, send)
        return True

    async def _redirect_legacy_url(self, scope: Scope, receive: Receive, send: Send) -> bool:
        if scope.get('method', '').upper() != 'GET':
            return False

        path = scope.get('path', '')
        raw_query = scope.get('query_string', b'')
        # This middleware only acts on /watch?v= and ?shared= URLs; skip the
        # decode + parse_qs work for every other GET. (A false positive on the
        # substring check just falls through to the full parse below.)
        if not (path.endswith('/watch') or b'shared' in raw_query):
            return False

        query_params = parse_qs(raw_query.decode('latin-1', errors='replace'))

        redirect_params: dict[str, str] = {}
        if path.endswith('/watch') and 'v' in query_params and query_params['v']:
            redirect_params['youtube'] = query_params['v'][0]

        if 'shared' in query_params and query_params['shared']:
            text = query_params['shared'][0]
            if text:
                url_match = re.match(r'https://\S+', text)
                if url_match:
                    # Local import: youtube loader pulls heavy deps and is
                    # only needed when a share-target actually contains a
                    # YouTube URL.
                    from open_webui.retrieval.loaders.youtube import _parse_video_id

                    youtube_video_id = _parse_video_id(url_match[0])
                    if youtube_video_id:
                        redirect_params['youtube'] = youtube_video_id
                    else:
                        redirect_params['load-url'] = url_match[0]
                else:
                    redirect_params['q'] = text

        if redirect_params:
            redirect_url = f'/?{urlencode(redirect_params)}'
            response = RedirectResponse(url=redirect_url)
            await response(scope, receive, send)
            return True

        return False

    def _rollback_session(self, message: str) -> None:
        if not ScopedSession.registry.has():
            return

        try:
            ScopedSession.rollback()
        except Exception:
            log.exception(message)
        finally:
            ScopedSession.remove()

    def _commit_session(self) -> None:
        # Nothing in this request touched the sync session: committing would
        # only instantiate one to run an empty transaction.
        if not ScopedSession.registry.has():
            return

        try:
            ScopedSession.commit()
        except Exception:
            log.exception('AppHTTPMiddleware: post-request commit failed; response was already sent to client')
            try:
                ScopedSession.rollback()
            except Exception:
                log.exception('AppHTTPMiddleware: rollback failed after commit failure')
            raise
        finally:
            # CRITICAL: remove() returns the connection to the pool.
            # Without this, connections remain "checked out" and
            # accumulate as "idle in transaction" in PostgreSQL.
            ScopedSession.remove()


def _scope_headers(scope: Scope) -> dict[str, str]:
    """Return ASGI scope headers as a lower-cased str→str dict.

    ASGI delivers headers as a list of (bytes, bytes) pairs. For
    convenience, fold duplicate keys with comma-joining (matching
    HTTP/1.1 semantics).
    """
    decoded: dict[str, str] = {}
    for raw_key, raw_value in scope.get('headers', []):
        key = raw_key.decode('latin-1').lower()
        value = raw_value.decode('latin-1')
        if key in decoded:
            decoded[key] = f'{decoded[key]}, {value}'
        else:
            decoded[key] = value
    return decoded


# [Gradient] Keep the env-driven headers here with the consolidated middleware (Q1).
def set_security_headers() -> dict[str, str]:
    """
    Sets security headers based on environment variables.

    This function reads specific environment variables and uses their values
    to set corresponding security headers. The headers that can be set are:
    - cache-control
    - permissions-policy
    - strict-transport-security
    - referrer-policy
    - x-content-type-options
    - x-download-options
    - x-frame-options
    - x-permitted-cross-domain-policies
    - content-security-policy
    - content-security-policy-report-only
    - cross-origin-embedder-policy
    - cross-origin-opener-policy
    - cross-origin-resource-policy
    - reporting-endpoints

    Each environment variable is associated with a specific setter function
    that constructs the header. If the environment variable is set, the
    corresponding header is added to the options dictionary.

    Returns:
        dict: A dictionary containing the security headers and their values.
    """
    options = {}
    header_setters = {
        'CACHE_CONTROL': set_cache_control,
        'HSTS': set_hsts,
        'PERMISSIONS_POLICY': set_permissions_policy,
        'REFERRER_POLICY': set_referrer,
        'XCONTENT_TYPE': set_xcontent_type,
        'XDOWNLOAD_OPTIONS': set_xdownload_options,
        'XFRAME_OPTIONS': set_xframe,
        'XPERMITTED_CROSS_DOMAIN_POLICIES': set_xpermitted_cross_domain_policies,
        'CONTENT_SECURITY_POLICY': set_content_security_policy,
        'CONTENT_SECURITY_POLICY_REPORT_ONLY': set_content_security_policy_report_only,
        'CROSS_ORIGIN_EMBEDDER_POLICY': set_cross_origin_embedder_policy,
        'CROSS_ORIGIN_OPENER_POLICY': set_cross_origin_opener_policy,
        'CROSS_ORIGIN_RESOURCE_POLICY': set_cross_origin_resource_policy,
        'REPORTING_ENDPOINTS': set_reporting_endpoints,
    }

    for env_var, setter in header_setters.items():
        value = os.environ.get(env_var, None)
        if value:
            header = setter(value)
            if header:
                options.update(header)

    return options


# Set HTTP Strict Transport Security(HSTS) response header
def set_hsts(value: str):
    pattern = r'^max-age=(\d+)(;includeSubDomains)?(;preload)?$'
    match = re.match(pattern, value, re.IGNORECASE)
    if not match:
        value = 'max-age=31536000;includeSubDomains'
    return {'Strict-Transport-Security': value}


# Set X-Frame-Options response header
def set_xframe(value: str):
    pattern = r'^(DENY|SAMEORIGIN)$'
    match = re.match(pattern, value, re.IGNORECASE)
    if not match:
        value = 'DENY'
    return {'X-Frame-Options': value}


# Set Permissions-Policy response header
def set_permissions_policy(value: str):
    pattern = r'^(?:(accelerometer|autoplay|camera|clipboard-read|clipboard-write|fullscreen|geolocation|gyroscope|magnetometer|microphone|midi|payment|picture-in-picture|sync-xhr|usb|xr-spatial-tracking)=\((self)?\),?)*$'
    match = re.match(pattern, value, re.IGNORECASE)
    if not match:
        value = 'none'
    return {'Permissions-Policy': value}


# Set Referrer-Policy response header
def set_referrer(value: str):
    pattern = r'^(no-referrer|no-referrer-when-downgrade|origin|origin-when-cross-origin|same-origin|strict-origin|strict-origin-when-cross-origin|unsafe-url)$'
    match = re.match(pattern, value, re.IGNORECASE)
    if not match:
        value = 'no-referrer'
    return {'Referrer-Policy': value}


# Set Cache-Control response header
def set_cache_control(value: str):
    pattern = r'^(public|private|no-cache|no-store|must-revalidate|proxy-revalidate|max-age=\d+|s-maxage=\d+|no-transform|immutable)(,\s*(public|private|no-cache|no-store|must-revalidate|proxy-revalidate|max-age=\d+|s-maxage=\d+|no-transform|immutable))*$'
    match = re.match(pattern, value, re.IGNORECASE)
    if not match:
        value = 'no-store, max-age=0'

    return {'Cache-Control': value}


# Set X-Download-Options response header
def set_xdownload_options(value: str):
    if value != 'noopen':
        value = 'noopen'
    return {'X-Download-Options': value}


# Set X-Content-Type-Options response header
def set_xcontent_type(value: str):
    if value != 'nosniff':
        value = 'nosniff'
    return {'X-Content-Type-Options': value}


# Set X-Permitted-Cross-Domain-Policies response header
def set_xpermitted_cross_domain_policies(value: str):
    pattern = r'^(none|master-only|by-content-type|by-ftp-filename)$'
    match = re.match(pattern, value, re.IGNORECASE)
    if not match:
        value = 'none'
    return {'X-Permitted-Cross-Domain-Policies': value}


# Set Content-Security-Policy response header
def set_content_security_policy(value: str):
    return {'Content-Security-Policy': value}


# Set Content-Security-Policy-Report-Only response header
def set_content_security_policy_report_only(value: str):
    return {'Content-Security-Policy-Report-Only': value}


# Set Cross-Origin-Embedder-Policy response header
def set_cross_origin_embedder_policy(value: str):
    pattern = r'^(unsafe-none|require-corp|credentialless)$'
    match = re.match(pattern, value, re.IGNORECASE)
    if not match:
        value = 'require-corp'
    return {'Cross-Origin-Embedder-Policy': value}


# Set Cross-Origin-Opener-Policy response header
def set_cross_origin_opener_policy(value: str):
    pattern = r'^(unsafe-none|same-origin-allow-popups|same-origin)$'
    match = re.match(pattern, value, re.IGNORECASE)
    if not match:
        value = 'same-origin'
    return {'Cross-Origin-Opener-Policy': value}


# Set Cross-Origin-Resource-Policy response header
def set_cross_origin_resource_policy(value: str):
    pattern = r'^(same-site|same-origin|cross-origin)$'
    match = re.match(pattern, value, re.IGNORECASE)
    if not match:
        value = 'same-origin'
    return {'Cross-Origin-Resource-Policy': value}


# Set Reporting-Endpoints response header
def set_reporting_endpoints(value: str):
    return {'Reporting-Endpoints': value}
