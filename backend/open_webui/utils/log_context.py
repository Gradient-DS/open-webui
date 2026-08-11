"""[Gradient] Structured log context and never-empty error rendering.

Implements the cross-repo schema in ``soev-docs/architecture/logging.md``
without modifying upstream files. Enrichment rides in loguru's ``extra``
dict, which the upstream ``_json_sink`` already serialises wholesale, so
``utils/logger.py`` needs no change and stays merge-clean.

``describe_exception`` exists because ``str(asyncio.TimeoutError())`` is
``''``. aiohttp raises a bare ``TimeoutError`` when its total timeout
expires, so interpolating it produced a log line *and* a user-facing
error banner that both ended at the colon — which is what made the
2026-08-04 investigation impossible (GRA-174 item 1.1).
"""

import os

from loguru import logger


def describe_exception(exc: BaseException) -> str:
    """A human-readable description that can never render empty.

    :param exc: The exception to describe.
    :return: ``"TypeName: message"``, or just ``"TypeName"`` when the
        exception carries no message.
    """
    name = type(exc).__name__
    message = str(exc).strip()
    return f'{name}: {message}' if message else name


def error_fields(exc: BaseException) -> dict:
    """Spec-shaped ``error`` fields for a log record.

    Walks ``__cause__`` / ``__context__``, outermost first. Cycles
    terminate: each exception is visited at most once.

    :param exc: The exception being reported.
    :return: ``{"error": {"type": ..., "chain": [...]}}``
    """
    chain: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(describe_exception(current))
        current = current.__cause__ or current.__context__
    return {'error': {'type': type(exc).__name__, 'chain': chain}}


def install_log_context() -> None:
    """Register a loguru patcher stamping service identity on every record.

    Call once, after ``start_logger()``. ``trace_id`` / ``span_id`` are
    already bound by the upstream ``InterceptHandler`` when OTel is
    enabled, so they are deliberately not duplicated here.

    ``VERSION`` is imported here rather than at module scope so that
    ``describe_exception`` / ``error_fields`` stay importable without
    pulling in ``open_webui.env``, which hard-exits when
    ``WEBUI_SECRET_KEY`` is unset.
    """
    from open_webui.env import VERSION

    tenant = os.environ.get('TENANT_NAME') or os.environ.get('OTEL_SERVICE_NAME', '')

    def _patch(record) -> None:
        record['extra'].setdefault('service', 'open-webui')
        record['extra'].setdefault('version', VERSION)
        if tenant:
            record['extra'].setdefault('tenant', tenant)

    logger.configure(patcher=_patch)
