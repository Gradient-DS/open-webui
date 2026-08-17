"""Regression tests for the chat_message analytics pipeline.

Background (haute-equipe incident, July 2026): the admin analytics read
exclusively from the ``chat_message`` table, populated by a dual-write
in ``Chats.upsert_message_to_chat_by_id_and_message_id``. The confirmed
root cause of the client's "0 tokens" was that the agent route
persisted messages with NULL ``usage`` (the agents-api never requested
token usage on the streaming wire) — so the token columns aggregated to
zero. (The investigation also ruled OUT two things people feared: no
silent write outage occurred — rows were present every day — and the
schema was current; those hypotheses were wrong. See
soev-gitops ``thoughts/shared/commands/2026-07-07-haute-equipe-analytics-empty.md``.)

Nothing exercised the persist → aggregate chain before, which is why
the token regression slipped through. These tests close that gap and
run the REAL code against a real (sqlite) database:

- the middleware's persistence funnel stores role / model / usage /
  timestamp faithfully (including the OpenAI ``usage`` key shape the
  agents-api emits on its terminal chunk, and the legacy ``info.usage``
  shape used by backfill);
- the analytics aggregation queries actually count what was persisted,
  inside an epoch-seconds date window (what the dashboard sends);
- messages without provider usage still count as messages (token
  columns are additive, never a filter on message counts).

The test database is a throwaway sqlite file whose schema is built by
the REAL migration chain — both pinned by this directory's
``conftest.py`` (see its docstring). A model column without its alembic
migration therefore fails these tests, mirroring production.
"""

from __future__ import annotations

import time
import uuid

import pytest

from open_webui.models.chat_messages import ChatMessages, get_usage

# Register the Chat and GroupMember models on Base.metadata: the
# chat_message FK targets the chat table (ORM resolution fails without
# it) and the analytics queries join GroupMember when a group filter is
# passed. The tables themselves come from the migration chain run by
# conftest — no create_all here.
import open_webui.models.chats  # noqa: E402, F401
import open_webui.models.groups  # noqa: E402, F401


def _ids() -> tuple[str, str, str]:
    """Unique (chat_id, user_id, message_id) per test — shared DB file."""
    return (f'chat-{uuid.uuid4()}', f'user-{uuid.uuid4()}', f'msg-{uuid.uuid4()}')


def _window() -> tuple[int, int]:
    """An epoch-seconds [start, end] window around now, dashboard-style."""
    now = int(time.time())
    return now - 3600, now + 3600


# ---------------------------------------------------------------------------
# Persistence: the dual-write funnel stores what analytics needs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_persists_assistant_message_with_openai_usage():
    """The exact message shape the middleware persists after an agent turn.

    The agents-api terminal chunk carries OpenAI-shape usage
    (``prompt_tokens``/``completion_tokens``); ``normalize_usage`` must
    add the standardized ``input_tokens``/``output_tokens`` keys the
    token aggregation queries read.
    """
    chat_id, user_id, message_id = _ids()
    ts = int(time.time())

    saved = await ChatMessages.upsert_message(
        message_id=message_id,
        chat_id=chat_id,
        user_id=user_id,
        data={
            'role': 'assistant',
            'content': 'The answer.',
            'model': 'google/gemma-4-31B-it',
            'timestamp': ts,
            'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15},
        },
    )

    assert saved is not None
    assert saved.role == 'assistant'
    assert saved.model_id == 'google/gemma-4-31B-it'
    assert saved.created_at == ts
    assert saved.usage is not None
    assert saved.usage['input_tokens'] == 10
    assert saved.usage['output_tokens'] == 5
    assert saved.usage['total_tokens'] == 15


@pytest.mark.asyncio
async def test_upsert_extracts_usage_from_info_usage():
    """Legacy/backfill messages carry usage under ``info.usage``."""
    chat_id, user_id, message_id = _ids()

    saved = await ChatMessages.upsert_message(
        message_id=message_id,
        chat_id=chat_id,
        user_id=user_id,
        data={
            'role': 'assistant',
            'content': 'Old message.',
            'model': 'some-model',
            'timestamp': int(time.time()),
            'info': {'usage': {'prompt_tokens': 7, 'completion_tokens': 3}},
        },
    )

    assert saved is not None and saved.usage is not None
    assert saved.usage['input_tokens'] == 7
    assert saved.usage['output_tokens'] == 3


def test_get_usage_prefers_top_level_usage_and_handles_absence():
    assert get_usage({'usage': {'prompt_tokens': 1}, 'info': {'usage': {'prompt_tokens': 9}}})['input_tokens'] == 1
    assert get_usage({'info': {'usage': {'prompt_tokens': 9}}})['input_tokens'] == 9
    assert get_usage({'content': 'no usage here'}) is None


# ---------------------------------------------------------------------------
# Aggregation: persisted rows actually show up in the dashboard queries
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persisted_messages_are_counted_by_the_analytics_queries():
    """End-to-end persist → aggregate, inside a dashboard-style window.

    Seeds one full exchange (user message + assistant reply with usage)
    plus a second assistant reply on another model, then asserts every
    aggregation the dashboard calls sees them: per-model counts,
    per-user counts, per-model token sums, per-user token sums, and the
    daily time series. The user-role message must count nowhere — the
    queries filter ``role='assistant'``.
    """
    chat_id, user_id, _ = _ids()
    model_a = f'model-a-{uuid.uuid4()}'
    model_b = f'model-b-{uuid.uuid4()}'
    ts = int(time.time())

    await ChatMessages.upsert_message(
        message_id=f'msg-{uuid.uuid4()}',
        chat_id=chat_id,
        user_id=user_id,
        data={'role': 'user', 'content': 'Question?', 'timestamp': ts},
    )
    await ChatMessages.upsert_message(
        message_id=f'msg-{uuid.uuid4()}',
        chat_id=chat_id,
        user_id=user_id,
        data={
            'role': 'assistant',
            'content': 'Answer A.',
            'model': model_a,
            'timestamp': ts,
            'usage': {'prompt_tokens': 100, 'completion_tokens': 20},
        },
    )
    await ChatMessages.upsert_message(
        message_id=f'msg-{uuid.uuid4()}',
        chat_id=chat_id,
        user_id=user_id,
        data={
            'role': 'assistant',
            'content': 'Answer B.',
            'model': model_b,
            'timestamp': ts,
            'usage': {'prompt_tokens': 40, 'completion_tokens': 10},
        },
    )

    start, end = _window()

    model_counts = await ChatMessages.get_message_count_by_model(start_date=start, end_date=end)
    assert model_counts.get(model_a) == 1
    assert model_counts.get(model_b) == 1

    user_counts = await ChatMessages.get_message_count_by_user(start_date=start, end_date=end)
    assert user_counts.get(user_id) == 2  # user-role message not counted

    chat_counts = await ChatMessages.get_message_count_by_chat(start_date=start, end_date=end)
    assert chat_counts.get(chat_id) == 2

    token_by_model = await ChatMessages.get_token_usage_by_model(start_date=start, end_date=end)
    assert token_by_model[model_a] == {
        'input_tokens': 100,
        'output_tokens': 20,
        'total_tokens': 120,
        'message_count': 1,
    }

    token_by_user = await ChatMessages.get_token_usage_by_user(start_date=start, end_date=end)
    assert token_by_user[user_id]['input_tokens'] == 140
    assert token_by_user[user_id]['output_tokens'] == 30

    daily = await ChatMessages.get_daily_message_counts_by_model(start_date=start, end_date=end)
    today_models = daily.get(time.strftime('%Y-%m-%d', time.localtime(ts)), {})
    assert today_models.get(model_a) == 1


@pytest.mark.asyncio
async def test_message_without_usage_still_counts_as_message():
    """Token-less messages (e.g. provider sent no usage) must still count.

    This is the exact split the client observed: message counts present,
    tokens zero. Counts must never depend on usage being reported.
    """
    chat_id, user_id, message_id = _ids()
    model = f'model-{uuid.uuid4()}'

    await ChatMessages.upsert_message(
        message_id=message_id,
        chat_id=chat_id,
        user_id=user_id,
        data={'role': 'assistant', 'content': 'No usage.', 'model': model, 'timestamp': int(time.time())},
    )

    start, end = _window()
    model_counts = await ChatMessages.get_message_count_by_model(start_date=start, end_date=end)
    assert model_counts.get(model) == 1

    # Usage-less rows still show in the token aggregation — as 0 tokens.
    # (SQLAlchemy stores Python None in a JSON column as JSON null, not SQL
    # NULL, so the usage-IS-NOT-NULL filter doesn't exclude them.) This is
    # the exact dashboard fingerprint of the July 2026 incident: message
    # counts present, token columns 0.
    token_by_model = await ChatMessages.get_token_usage_by_model(start_date=start, end_date=end)
    assert token_by_model[model] == {
        'input_tokens': 0,
        'output_tokens': 0,
        'total_tokens': 0,
        'message_count': 1,
    }


@pytest.mark.asyncio
async def test_background_task_update_does_not_clear_usage():
    """Follow-up/title/tags updates on the same message keep token counts.

    ``upsert_message`` deep-merges usage on update; a later update
    without usage must not wipe the primary response's counts.
    """
    chat_id, user_id, message_id = _ids()

    await ChatMessages.upsert_message(
        message_id=message_id,
        chat_id=chat_id,
        user_id=user_id,
        data={
            'role': 'assistant',
            'content': 'Answer.',
            'model': 'm',
            'timestamp': int(time.time()),
            'usage': {'prompt_tokens': 10, 'completion_tokens': 5},
        },
    )
    updated = await ChatMessages.upsert_message(
        message_id=message_id,
        chat_id=chat_id,
        user_id=user_id,
        data={'content': 'Answer. (edited)', 'done': True},
    )

    assert updated is not None and updated.usage is not None
    assert updated.usage['input_tokens'] == 10


# ---------------------------------------------------------------------------
# Known latent bug — documented, not yet fixed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason=(
        'upsert_message stores data["timestamp"] raw; a millisecond timestamp '
        'lands as created_at≈1.75e12, which fails the created_at <= end_date '
        'filter (epoch seconds) of every dated dashboard window. '
        '_normalize_timestamp exists (chat_messages.py) but is only applied '
        'on read paths, not on insert. Fix: normalize on insert. When this '
        'xfail starts passing, the bug is fixed — remove the marker.'
    ),
)
async def test_millisecond_timestamp_still_lands_inside_query_window():
    chat_id, user_id, message_id = _ids()
    model = f'model-{uuid.uuid4()}'
    ts_ms = int(time.time() * 1000)  # millisecond timestamp from a buggy client

    await ChatMessages.upsert_message(
        message_id=message_id,
        chat_id=chat_id,
        user_id=user_id,
        data={'role': 'assistant', 'content': 'ms ts.', 'model': model, 'timestamp': ts_ms},
    )

    start, end = _window()
    model_counts = await ChatMessages.get_message_count_by_model(start_date=start, end_date=end)
    assert model_counts.get(model) == 1
