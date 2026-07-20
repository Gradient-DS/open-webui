"""Upstream error sanitisation — the guarantee that chat content never leaves
the server inside an error string.

The fixtures below are modelled on the real 2026-07-03 incident: vLLM answers a
400 by echoing the entire request back under `input`, so the system prompt and
every user message ride along in the error body. That body reached the browser,
the feedback report built from it, and finally the Slack card.
"""

import json

from open_webui.utils.upstream_errors import (
    MAX_ERROR_MESSAGE_CHARS,
    safe_error_text,
    sanitize_upstream_error,
)

# The secret that must never appear in any sanitised output.
SECRET = 'Mijn BSN is 123456789 en ik heb een vraag over mijn ontslagprocedure'

VLLM_ECHOING_400 = json.dumps(
    {
        'object': 'error',
        'message': "This model's maximum context length is 8192 tokens.",
        'type': 'BadRequestError',
        'code': 400,
        'input': {
            'model': 'gpt-oss-120b',
            'messages': [
                {'role': 'system', 'content': 'You are a helpful assistant for Haute Equipe.'},
                {'role': 'user', 'content': SECRET},
            ],
        },
    }
)

NESTED_ENVELOPE_400 = json.dumps(
    {
        'error': {
            'message': 'Invalid value for encoding_format.',
            'type': 'invalid_request_error',
            'param': 'encoding_format',
            'code': 'invalid_value',
            'messages': [{'role': 'user', 'content': SECRET}],
        }
    }
)


def _flatten(value) -> str:
    """Every scalar in the structure, as one searchable string."""
    return json.dumps(value, ensure_ascii=False)


def test_drops_echoed_request_payload():
    result = sanitize_upstream_error(VLLM_ECHOING_400, status=400)

    assert SECRET not in _flatten(result)
    assert 'You are a helpful assistant' not in _flatten(result)
    assert 'input' not in result['error']


def test_keeps_error_classification():
    result = sanitize_upstream_error(VLLM_ECHOING_400, status=400)

    assert result['error']['message'] == "This model's maximum context length is 8192 tokens."
    assert result['error']['type'] == 'BadRequestError'
    assert result['error']['code'] == 400


def test_nested_error_envelope_drops_echoed_messages():
    result = sanitize_upstream_error(NESTED_ENVELOPE_400, status=400)

    assert SECRET not in _flatten(result)
    assert 'messages' not in result['error']
    assert result['error']['message'] == 'Invalid value for encoding_format.'
    assert result['error']['param'] == 'encoding_format'


def test_non_json_body_is_never_surfaced():
    """A plain-text body may be an echoed prompt — it cannot be classified, so it is dropped."""
    result = sanitize_upstream_error(f'Traceback ... prompt was: {SECRET}', status=500)

    assert SECRET not in _flatten(result)
    assert result['error']['code'] == 500


def test_message_is_length_capped():
    body = json.dumps({'message': 'x' * 5000, 'code': 400})

    result = sanitize_upstream_error(body, status=400)

    assert len(result['error']['message']) <= MAX_ERROR_MESSAGE_CHARS + 1


def test_non_scalar_message_is_dropped():
    """Some providers nest structures under `message` — only scalars are safe."""
    body = json.dumps({'message': {'echoed': SECRET}, 'code': 400})

    result = sanitize_upstream_error(body, status=400)

    assert SECRET not in _flatten(result)


def test_empty_body_falls_back_to_status():
    result = sanitize_upstream_error('', status=502)

    assert result['error']['code'] == 502
    assert result['error']['message']


def test_safe_error_text_is_content_free_and_single_line():
    text = safe_error_text(VLLM_ECHOING_400, status=400, source='Agent API')

    assert SECRET not in text
    assert '\n' not in text
    assert 'Agent API' in text
    assert '400' in text


def test_safe_error_text_on_unparseable_body():
    text = safe_error_text(f'boom {SECRET}', status=503, source='Agent API')

    assert SECRET not in text
    assert '503' in text


# --- response builder --------------------------------------------------------


def test_upstream_error_response_body_is_sanitised():
    from open_webui.utils.upstream_errors import upstream_error_response

    resp = upstream_error_response(VLLM_ECHOING_400, status=400)

    assert resp.status_code == 400
    assert SECRET not in resp.body.decode('utf-8')


def test_upstream_error_response_keeps_classification():
    from open_webui.utils.upstream_errors import upstream_error_response

    resp = upstream_error_response(VLLM_ECHOING_400, status=400)
    payload = json.loads(resp.body)

    assert payload['error']['type'] == 'BadRequestError'
    assert 'input' not in payload['error']
