"""Unit tests for the agent API payload builder.

These tests pin the wire-format contract that the Gradient agent service
depends on. In particular, they guard the retry/regenerate fix: when the
user hits retry, ``parent_message_id`` must be present in the payload so
the agent service can rewind its thread state. On fresh chats it must be
absent so the agent does not attempt to rewind to a non-existent point.
"""

from __future__ import annotations

import json

import pytest

from open_webui.utils import agent as agent_module
from open_webui.utils.agent import (
    SSEEvent,
    _build_streaming_response,
    _error_sse_chunk,
    _resolve_model_citations_enabled,
    _resolve_model_vision_capable,
    build_agent_payload,
)


def _base_kwargs(**overrides):
    kwargs = {
        'model': 'gpt-oss-120b',
        'messages': [{'role': 'user', 'content': 'hi'}],
    }
    kwargs.update(overrides)
    return kwargs


def test_minimal_payload_omits_optional_fields():
    payload = build_agent_payload(**_base_kwargs())
    assert payload['model'] == 'gpt-oss-120b'
    assert payload['messages'] == [{'role': 'user', 'content': 'hi'}]
    assert payload['stream'] is True
    for absent in (
        'parent_message_id',
        'message_id',
        'chat_id',
        'user_id',
        'session_id',
        'agent',
        'system_prompt',
        'chat_system_prompt',
        'skills',
    ):
        assert absent not in payload, f'expected {absent!r} to be absent'


def test_parent_message_id_present_when_set():
    payload = build_agent_payload(
        **_base_kwargs(
            chat_id='chat-1',
            message_id='msg-new',
            parent_message_id='msg-user-prompt',
        )
    )
    assert payload['parent_message_id'] == 'msg-user-prompt'
    assert payload['message_id'] == 'msg-new'
    assert payload['chat_id'] == 'chat-1'


def test_parent_message_id_absent_on_fresh_chat():
    payload = build_agent_payload(
        **_base_kwargs(
            chat_id='chat-1',
            message_id='msg-new',
            parent_message_id=None,
        )
    )
    assert 'parent_message_id' not in payload


def test_model_params_passthrough_strips_none():
    payload = build_agent_payload(
        **_base_kwargs(
            temperature=0.2,
            top_p=None,
            max_tokens=512,
        )
    )
    assert payload['temperature'] == 0.2
    assert payload['max_tokens'] == 512
    assert 'top_p' not in payload


def test_metadata_user_language_forwarded():
    """user_language from the UI locale must reach the wire payload."""
    payload = build_agent_payload(**_base_kwargs(metadata={'user_language': 'nl-NL'}))
    assert payload.get('metadata') == {'user_language': 'nl-NL'}


def test_metadata_none_omitted_from_payload():
    """When no metadata is provided the key must be absent from the payload."""
    payload = build_agent_payload(**_base_kwargs(metadata=None))
    assert 'metadata' not in payload


def test_chat_system_prompt_present_when_set():
    """The merged Chat Controls / folder prompt reaches the wire payload."""
    payload = build_agent_payload(**_base_kwargs(chat_system_prompt='Answer only in Dutch.'))
    assert payload['chat_system_prompt'] == 'Answer only in Dutch.'


def test_chat_system_prompt_absent_when_none():
    payload = build_agent_payload(**_base_kwargs(chat_system_prompt=None))
    assert 'chat_system_prompt' not in payload


def test_skills_present_when_set():
    """Resolved skills reach the wire payload as a top-level list."""
    skills = [
        {
            'name': 'Tone Guide',
            'description': 'how to phrase answers',
            'content': 'Always answer formally.',
            'is_selected': True,
        },
        {
            'name': 'Onboarding',
            'description': 'onboarding steps',
            'content': 'Step 1...',
            'is_selected': False,
        },
    ]
    payload = build_agent_payload(**_base_kwargs(skills=skills))
    assert payload['skills'] == skills


def test_skills_absent_when_none():
    payload = build_agent_payload(**_base_kwargs(skills=None))
    assert 'skills' not in payload


def test_resolve_vision_capable_reads_capability_flag():
    model = {'info': {'meta': {'capabilities': {'vision': False}}}}
    assert _resolve_model_vision_capable(model) is False


def test_resolve_vision_capable_true_when_flag_true():
    model = {'info': {'meta': {'capabilities': {'vision': True}}}}
    assert _resolve_model_vision_capable(model) is True


def test_resolve_vision_capable_defaults_true_when_unset():
    assert _resolve_model_vision_capable({'info': {'meta': {}}}) is True


def test_resolve_vision_capable_defaults_true_when_no_model():
    assert _resolve_model_vision_capable(None) is True
    assert _resolve_model_vision_capable({}) is True


def test_resolve_citations_reads_capability_flag():
    model = {'info': {'meta': {'capabilities': {'citations': False}}}}
    assert _resolve_model_citations_enabled(model) is False


def test_resolve_citations_true_when_flag_true():
    model = {'info': {'meta': {'capabilities': {'citations': True}}}}
    assert _resolve_model_citations_enabled(model) is True


def test_resolve_citations_defaults_true_when_unset():
    assert _resolve_model_citations_enabled({'info': {'meta': {}}}) is True


def test_resolve_citations_defaults_true_when_no_model():
    assert _resolve_model_citations_enabled(None) is True
    assert _resolve_model_citations_enabled({}) is True


async def _run_relay(monkeypatch, events):
    """Drive _build_streaming_response over `events`; return (emitted, body)."""
    emitted: list[dict] = []

    async def fake_emitter(event):
        emitted.append(event)

    async def fake_get_event_emitter(metadata):
        return fake_emitter

    async def fake_stream(base_url, payload):
        for e in events:
            yield e

    monkeypatch.setattr(agent_module, 'get_event_emitter', fake_get_event_emitter)
    monkeypatch.setattr(agent_module, 'stream_agent_response', fake_stream)

    response = _build_streaming_response(None, {'model': 'm'}, {})
    body = ''.join([chunk async for chunk in response.body_iterator])
    return emitted, body


@pytest.mark.asyncio
async def test_source_event_with_identity_fields_relayed_verbatim(monkeypatch):
    """A tagged source frame (n / current_turn) reaches Socket.IO unchanged.

    The frontend derives the per-message source panel from these fields,
    so the relay must forward the payload verbatim — no filtering, no
    reshaping.
    """
    payload = {
        'source': {'name': 'doc.pdf', 'type': 'file', 'id': 'd-1'},
        'document': ['chunk'],
        'metadata': [{'source': 'doc.pdf'}],
        'n': 7,
        'current_turn': True,
    }
    emitted, _body = await _run_relay(
        monkeypatch,
        [SSEEvent(event_type='source', data=payload)],
    )

    assert emitted == [{'type': 'source', 'data': payload}]


@pytest.mark.asyncio
async def test_no_panel_filter_relay(monkeypatch):
    """The panel_filter relay branch is gone.

    The agent service no longer emits the event; if a stale agent does,
    the relay must not forward it to Socket.IO as a panel_filter event.
    """
    emitted, _body = await _run_relay(
        monkeypatch,
        [SSEEvent(event_type='panel_filter', data={'ns': [1, 2]})],
    )

    assert not [e for e in emitted if e['type'] == 'panel_filter']


def test_error_sse_chunk_is_openai_error_shape_without_choices():
    """The error chunk must be {error:{message}} with no `choices` so
    Open WebUI's middleware renders a proper error banner."""
    chunk = _error_sse_chunk('boom')
    assert chunk.startswith('data: ')
    assert chunk.endswith('\n\n')
    payload = json.loads(chunk[len('data: ') :].strip())
    assert payload == {'error': {'message': 'boom'}}
    assert 'choices' not in payload
