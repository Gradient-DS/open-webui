"""Sanitisation of upstream provider / agent error bodies.

Providers routinely echo the request back when they reject it — vLLM answers a
400 with the full `input` object, system prompt and user messages included.
Anything built from that body inherits the content: the browser's error banner,
the feedback report assembled from it, and the Slack card posted from that.

The split here mirrors log levels. An error's *classification* — message, type,
code — is safe to surface anywhere: to the client, in ERROR logs, in Slack. The
*raw body* is content-bearing and belongs only in a DEBUG log that stays on the
server. Call sites pair the two:

    log.error('Agent API rejected the request: %s', safe_error_text(...))
    log.debug('Agent API raw error body: %s', body)

This is an allowlist, not a scrubber: unrecognised keys are dropped rather than
inspected, so a provider inventing a new field to echo the request in cannot
quietly defeat it.
"""

import json
from typing import Any

from starlette.responses import JSONResponse

# Long enough for a real provider diagnostic, short enough that an echoed
# prompt smuggled into `message` cannot travel far.
MAX_ERROR_MESSAGE_CHARS = 300

# The descriptive fields of the OpenAI error envelope. Everything outside this
# set is dropped — notably `input`, `messages`, `prompt` and `body`, which is
# where providers put the echoed request.
_SAFE_ERROR_KEYS = ('message', 'type', 'code', 'param')

_SCALARS = (str, int, float, bool)


def _coerce_to_dict(body: Any) -> dict | None:
    """Best-effort parse of an error body into a dict, or None if it isn't one.

    A body that will not parse is treated as unclassifiable and discarded by
    the caller — it may well be an echoed prompt in plain text.
    """
    if isinstance(body, dict):
        return body

    if isinstance(body, (bytes, bytearray)):
        body = body.decode('utf-8', errors='replace')

    if isinstance(body, str):
        if not body.strip():
            return None
        try:
            parsed = json.loads(body)
        except (ValueError, TypeError):
            return None
        return parsed if isinstance(parsed, dict) else None

    return None


def sanitize_upstream_error(body: Any, *, status: int) -> dict:
    """Reduce an upstream error body to its content-free classification.

    Returns an OpenAI-shaped ``{'error': {...}}`` carrying only allowlisted
    scalar fields. Always returns a usable envelope, falling back to the HTTP
    status when the body yields nothing safe.
    """
    parsed = _coerce_to_dict(body)
    error: dict[str, Any] = {}

    if parsed is not None:
        # Providers use either a flat body or an {'error': {...}} envelope.
        inner = parsed.get('error')
        source = inner if isinstance(inner, dict) else parsed

        for key in _SAFE_ERROR_KEYS:
            value = source.get(key)
            # Scalars only: a nested structure under `message` is exactly how
            # an echoed payload would slip through an otherwise-safe key.
            if isinstance(value, _SCALARS):
                error[key] = value

    message = error.get('message')
    if isinstance(message, str) and len(message) > MAX_ERROR_MESSAGE_CHARS:
        error['message'] = message[:MAX_ERROR_MESSAGE_CHARS] + '…'

    if not error.get('message'):
        error['message'] = f'Upstream error (HTTP {status})'
    error.setdefault('code', status)

    return {'error': error}


def upstream_error_response(body: Any, *, status: int) -> JSONResponse:
    """Client-facing error response carrying only the sanitised classification.

    Replaces passing an upstream error body through verbatim, which is how
    echoed prompts reached the browser.
    """
    return JSONResponse(status_code=status, content=sanitize_upstream_error(body, status=status))


def safe_error_text(body: Any, *, status: int, source: str) -> str:
    """One-line, content-free description for exception messages and ERROR logs."""
    error = sanitize_upstream_error(body, status=status)['error']

    message = ' '.join(str(error['message']).split())
    error_type = error.get('type')
    suffix = f' ({error_type})' if error_type else ''

    return f'{source} returned {status}: {message}{suffix}'
