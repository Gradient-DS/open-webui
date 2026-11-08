import logging
from collections.abc import Collection
from urllib.parse import urlsplit

from aiohttp import (
    TraceRequestEndParams,
    TraceRequestExceptionParams,
    TraceRequestStartParams,
)
from fastapi import FastAPI, status
from open_webui.utils.telemetry.constants import SpanAttributes
from open_webui.utils.telemetry.routes import route_template, target_service
from opentelemetry.instrumentation.aiohttp_client import AioHttpClientInstrumentor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import (
    HTTPXClientInstrumentor,
    RequestInfo,
    ResponseInfo,
)
from opentelemetry.instrumentation.instrumentor import BaseInstrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.instrumentation.system_metrics import SystemMetricsInstrumentor
from opentelemetry.trace import Span, StatusCode
from redis import Redis
from redis.cluster import RedisCluster
from requests import PreparedRequest, Response
from sqlalchemy import Engine

logger = logging.getLogger(__name__)


def requests_hook(span: Span, request: PreparedRequest):
    """
    Http Request Hook
    """

    _http_request(span, request.method, request.url)


def response_hook(span: Span, request: PreparedRequest, response: Response):
    """
    HTTP Response Hook
    """

    span.set_attributes(
        attributes={
            SpanAttributes.HTTP_STATUS_CODE: response.status_code,
        }
    )
    span.set_status(StatusCode.ERROR if response.status_code >= 400 else StatusCode.OK)


def redis_request_hook(span: Span, instance: Redis | RedisCluster, args, kwargs):
    """
    Redis Request Hook
    """

    span.update_name('db.statement')
    operation = str(args[0]).upper() if args else 'STATEMENT'
    span.set_attribute(
        'db.operation.name',
        operation if operation in {'GET', 'SET', 'DEL', 'EXPIRE', 'PUBLISH', 'SUBSCRIBE'} else 'STATEMENT',
    )


def httpx_request_hook(span: Span, request: RequestInfo):
    """
    HTTPX Request Hook
    """

    _http_request(span, request.method.decode(), str(request.url))


def httpx_response_hook(span: Span, request: RequestInfo, response: ResponseInfo):
    """
    HTTPX Response Hook
    """

    span.set_attribute(SpanAttributes.HTTP_STATUS_CODE, response.status_code)
    span.set_status(StatusCode.ERROR if response.status_code >= status.HTTP_400_BAD_REQUEST else StatusCode.OK)


async def httpx_async_request_hook(span: Span, request: RequestInfo):
    """
    Async Request Hook
    """

    httpx_request_hook(span, request)


async def httpx_async_response_hook(span: Span, request: RequestInfo, response: ResponseInfo):
    """
    Async Response Hook
    """

    httpx_response_hook(span, request, response)


def aiohttp_request_hook(span: Span, request: TraceRequestStartParams):
    """
    Aiohttp Request Hook
    """

    _http_request(span, request.method, str(request.url))


def aiohttp_response_hook(span: Span, response: TraceRequestExceptionParams | TraceRequestEndParams):
    """
    Aiohttp Response Hook
    """

    if isinstance(response, TraceRequestEndParams):
        span.set_attribute(SpanAttributes.HTTP_STATUS_CODE, response.response.status)
        span.set_status(StatusCode.ERROR if response.response.status >= status.HTTP_400_BAD_REQUEST else StatusCode.OK)
    elif isinstance(response, TraceRequestExceptionParams):
        span.set_status(StatusCode.ERROR)


def _http_request(span: Span, method: str | None, url: str | None) -> None:
    target = target_service(urlsplit(url or '').hostname or '')
    route = route_template(url or '', target)
    method = method if method in {'GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'} else 'OTHER'
    span.update_name(f'{method} {route}')
    span.set_attributes({'http.route': route, 'http.request.method': method})


class Instrumentor(BaseInstrumentor):
    """
    Instrument OT
    """

    def __init__(self, app: FastAPI, db_engine: Engine):
        self.app = app
        self.db_engine = db_engine

    def instrumentation_dependencies(self) -> Collection[str]:
        return []

    def _instrument(self, **kwargs):
        FastAPIInstrumentor.instrument_app(app=self.app)
        SQLAlchemyInstrumentor().instrument(engine=self.db_engine)
        RedisInstrumentor().instrument(request_hook=redis_request_hook)
        RequestsInstrumentor().instrument(request_hook=requests_hook, response_hook=response_hook)
        LoggingInstrumentor().instrument()
        HTTPXClientInstrumentor().instrument(
            request_hook=httpx_request_hook,
            response_hook=httpx_response_hook,
            async_request_hook=httpx_async_request_hook,
            async_response_hook=httpx_async_response_hook,
        )
        AioHttpClientInstrumentor().instrument(
            request_hook=aiohttp_request_hook,
            response_hook=aiohttp_response_hook,
        )
        SystemMetricsInstrumentor().instrument()

    def _uninstrument(self, **kwargs):
        if getattr(self, 'instrumentors', None) is None:
            return
        for instrumentor in self.instrumentors:
            instrumentor.uninstrument()
