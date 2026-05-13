"""Unit tests for the agent API payload builder.

These tests pin the wire-format contract that the Gradient agent service
depends on. In particular, they guard the retry/regenerate fix: when the
user hits retry, ``parent_message_id`` must be present in the payload so
the agent service can rewind its thread state. On fresh chats it must be
absent so the agent does not attempt to rewind to a non-existent point.
"""

from __future__ import annotations

from open_webui.utils.agent import build_agent_payload


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
