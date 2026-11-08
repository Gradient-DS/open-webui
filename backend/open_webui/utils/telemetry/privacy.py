"""Limit built-in instrumentation export to operational metadata."""

from collections.abc import Sequence
from urllib.parse import urlsplit

from open_webui.utils.telemetry.routes import route_template, target_service
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.trace import Link, SpanContext, Status


class PrivateSpanExporter(SpanExporter):
    """Drop content-bearing default attributes, events and status descriptions."""

    def __init__(self, exporter: SpanExporter, *, service: str) -> None:
        self.exporter = exporter
        self.resource = Resource({'service.name': service})

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        return self.exporter.export([self._private(span) for span in spans])

    def shutdown(self) -> None:
        self.exporter.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self.exporter.force_flush(timeout_millis)

    def _private(self, span: ReadableSpan) -> ReadableSpan:
        source = span.attributes or {}
        attributes: dict[str, str | int | float] = {'outcome': span.status.status_code.name.lower()}
        name = 'operation'
        method = source.get('http.request.method', source.get('http.method'))
        if method is not None:
            method = (
                method if method in {'GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS', 'CONNECT'} else 'OTHER'
            )
            # Server http.route comes from the router. Client URLs are reduced
            # through the declared platform templates, never a segment heuristic.
            route = source.get('http.route')
            url = source.get('url.full', source.get('http.url', ''))
            if not isinstance(route, str) or '?' in route:
                route = route_template(str(url))
            attributes['http.route'] = route
            attributes['http.request.method'] = str(method)
            status = source.get('http.response.status_code', source.get('http.status_code'))
            if isinstance(status, int):
                attributes['http.response.status_code'] = status
            attributes['peer.service'] = target_service(urlsplit(str(url)).hostname or '')
            name = f'{method} {route}'
        elif 'db.system' in source or 'db.system.name' in source:
            statement = str(source.get('db.statement', '')).split(None, 1)
            operation = str(
                source.get('db.operation.name', source.get('db.operation', statement[0] if statement else 'STATEMENT'))
            ).upper()
            attributes['db.operation.name'] = (
                operation
                if operation in {'SELECT', 'INSERT', 'UPDATE', 'DELETE', 'COMMIT', 'ROLLBACK', 'BEGIN', 'SET', 'GET'}
                else 'STATEMENT'
            )
            name = 'db.statement'
        if span.start_time is not None and span.end_time is not None:
            attributes['duration_ms'] = (span.end_time - span.start_time) / 1_000_000
        return ReadableSpan(
            name=name,
            context=_context(span.context) if span.context is not None else None,
            parent=_context(span.parent) if span.parent is not None else None,
            resource=self.resource,
            attributes=attributes,
            kind=span.kind,
            status=Status(span.status.status_code),
            start_time=span.start_time,
            end_time=span.end_time,
            links=[Link(_context(link.context)) for link in span.links],
        )


def _context(context: SpanContext) -> SpanContext:
    return SpanContext(context.trace_id, context.span_id, context.is_remote, context.trace_flags)
