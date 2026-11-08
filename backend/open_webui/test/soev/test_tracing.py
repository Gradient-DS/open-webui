"""Platform timings, actual HTTPX propagation and private built-in OTel export."""

import logging
import os
import subprocess
import sys

import httpx
import pytest
from open_webui.soev.timing import instrument_client
from open_webui.utils.telemetry.privacy import PrivateSpanExporter
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Event, ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanContext, SpanKind, Status, StatusCode, TraceState


@pytest.mark.asyncio
@pytest.mark.parametrize('stream', [False, True])
async def test_httpx_child_header_and_timing_never_contain_content(caplog, stream):
    headers = []
    output = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource({'service.name': 'fictional'}))
    provider.add_span_processor(SimpleSpanProcessor(PrivateSpanExporter(output, service='open-webui')))

    def handle(request):
        headers.append(request.headers['traceparent'])
        return httpx.Response(200, content=b'private document title', headers={'X-Request-ID': 'request-123'})

    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            HTTPXClientInstrumentor.instrument_client(client, tracer_provider=provider)
            instrument_client(client)
            with (
                caplog.at_level(logging.INFO, logger='open_webui.soev.client'),
                provider.get_tracer('test').start_as_current_span('request') as parent,
            ):
                request = client.build_request(
                    'GET',
                    'https://demo-api/v1/chat/threads/private-title?token=secret',
                    headers={'Authorization': 'Bearer private-key'},
                )
                response = await client.send(request, stream=stream)
                await response.aclose()
        client_span = next(span for span in output.get_finished_spans() if span.kind == SpanKind.CLIENT)
        assert client_span.parent.span_id == parent.get_span_context().span_id
        assert headers == [
            f'00-{client_span.context.trace_id:032x}-{client_span.context.span_id:016x}-{int(client_span.context.trace_flags):02x}'
        ]
        [record] = [record for record in caplog.records if record.name == 'open_webui.soev.client']
        assert record.elapsed_ms >= 0
        assert record.route == '/v1/chat/threads/{thread_id}'
        assert 'elapsed_ms=' in record.getMessage()
        exposed = repr(vars(record)) + repr([(s.name, dict(s.attributes)) for s in output.get_finished_spans()])
        for value in ('private-title', 'private-key', 'token=secret', 'Authorization', 'private document title'):
            assert value not in exposed
    finally:
        provider.shutdown()


@pytest.mark.asyncio
async def test_transport_error_has_elapsed_without_exception_text(caplog):
    def fail(request):
        raise httpx.ConnectError('private-email@example.test token=secret')

    async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as client:
        instrument_client(client)
        with caplog.at_level(logging.INFO, logger='open_webui.soev.client'), pytest.raises(httpx.ConnectError):
            await client.get('http://demo-api/private-title?token=secret')
    [record] = [record for record in caplog.records if record.name == 'open_webui.soev.client']
    assert record.elapsed_ms >= 0
    assert record.route == 'unmatched'
    assert 'private' not in repr(vars(record))
    assert 'secret' not in repr(vars(record))


@pytest.mark.parametrize(
    'attributes',
    [
        {
            'http.method': 'GET',
            'http.url': 'https://demo-api/v1/chat/threads/private-title?token=secret',
            'http.status_code': 403,
            'http.request.header.authorization': 'Bearer token',
        },
        {
            'db.system': 'postgresql',
            'db.statement': "SELECT 'private-title', 'email@example.test'",
            'db.parameters': 'secret',
            'db.operation': 'SELECT',
        },
        {'db.system': 'redis', 'db.statement': 'SET private-token private-prompt', 'db.operation': 'SET'},
    ],
)
def test_builtin_export_drops_urls_sql_credentials_and_exception_events(attributes):
    target = InMemorySpanExporter()
    exporter = PrivateSpanExporter(target, service='open-webui')
    raw = ReadableSpan(
        name='private-title token=secret',
        context=SpanContext(1, 2, False, trace_state=TraceState([('vendor', 'email@example.test')])),
        attributes=attributes,
        resource=Resource({'service.name': 'open-webui', 'email': 'email@example.test'}),
        status=Status(StatusCode.ERROR, 'private failure'),
        events=[Event('private failure', attributes={'exception.message': 'private-title token=secret'})],
        start_time=1,
        end_time=1000001,
    )
    exporter.export([raw])
    [safe] = target.get_finished_spans()
    assert safe.attributes['duration_ms'] == 1
    assert safe.status.status_code == StatusCode.ERROR
    assert safe.status.description is None
    assert not safe.events
    assert not safe.context.trace_state
    assert dict(safe.resource.attributes) == {'service.name': 'open-webui'}
    exported = repr((safe.name, dict(safe.attributes), safe.events))
    for value in ('private-title', 'token=secret', 'email@example.test', 'Bearer', 'private-token', 'private-prompt'):
        assert value not in exported


def test_builtin_tracing_defaults_are_disabled():
    environment = dict(os.environ)
    for key in ('ENABLE_OTEL', 'ENABLE_OTEL_TRACES', 'ENABLE_OTEL_METRICS', 'ENABLE_OTEL_LOGS'):
        environment.pop(key, None)
    result = subprocess.run(
        [
            sys.executable,
            '-c',
            'from open_webui import env; assert not env.ENABLE_OTEL; assert not env.ENABLE_OTEL_TRACES',
        ],
        env=environment,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
