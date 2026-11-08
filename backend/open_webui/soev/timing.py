"""Content-free timings and trace propagation for both platform clients."""

import logging
import re
import time
from typing import Any

import httpx
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from open_webui.utils.telemetry.routes import route_template

log = logging.getLogger('open_webui.soev.client')


def instrument_client(client: httpx.AsyncClient) -> None:
    """Time each send, including streams, thread calls and transport failures."""
    for name in ('httpx', 'httpcore'):
        logging.getLogger(name).setLevel(logging.WARNING)
    original = client.send

    async def send(request: httpx.Request, **kwargs: Any) -> httpx.Response:
        started = time.perf_counter()
        response = None
        # The built-in HTTPX instrumentor replaces this with its child span's
        # parent on transport entry. This also propagates for custom transports.
        TraceContextTextMapPropagator().inject(request.headers)
        try:
            response = await original(request, **kwargs)
            return response
        finally:
            elapsed = (time.perf_counter() - started) * 1000
            status = response.status_code if response is not None else None
            request_id = response.headers.get('X-Request-ID') if response is not None else None
            if request_id is not None and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', request_id) is None:
                request_id = None
            route = route_template(request.url.path)
            log.log(
                logging.INFO if status is not None and status < 400 else logging.WARNING,
                'soev-api response %s %s %s request_id=%s elapsed_ms=%.3f',
                request.method
                if request.method in {'GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'}
                else 'OTHER',
                route,
                status,
                request_id,
                elapsed,
                extra={'route': route, 'status': status, 'request_id': request_id, 'elapsed_ms': elapsed},
            )

    client.send = send  # type: ignore[method-assign]
