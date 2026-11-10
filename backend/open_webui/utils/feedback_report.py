"""Attach trace identifiers to HTTP errors without request or response content."""

from typing import Any

from opentelemetry import trace


def get_current_trace_id() -> str | None:
    """Current OTel trace id as a hex string, or None when tracing is inactive."""
    ctx = trace.get_current_span().get_span_context()
    if ctx.is_valid:
        return trace.format_trace_id(ctx.trace_id)
    return None


def build_http_error_body(detail: Any) -> dict:
    """Attach the trace identifier the browser can include in a bug report."""
    body: dict = {'detail': detail}
    trace_id = get_current_trace_id()
    if trace_id:
        body['trace_id'] = trace_id
    return body
