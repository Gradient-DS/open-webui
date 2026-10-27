"""Validate soev.task/1 at the product boundary without importing the agent runtime."""

import hashlib
import json
from collections.abc import AsyncIterator
from typing import Annotated, Any, Literal
from uuid import uuid4

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from starlette.responses import StreamingResponse

from open_webui.soev import identity, threads
from open_webui.soev.client import SoevApiError
from open_webui.utils.chat_id import is_temporary_chat_id

TaskKind = Literal['title', 'tags', 'follow_ups', 'queries', 'autocomplete', 'emoji', 'moa']
TaskText = Annotated[str, Field(strict=True, min_length=1, max_length=16384)]


class TaskInput(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)


class TaskTextInput(TaskInput):
    text: TaskText


class TaskQueriesInput(TaskTextInput):
    purpose: Literal['web_search', 'retrieval']


class TaskMoaInput(TaskTextInput):
    responses: list[TaskText] = Field(min_length=2, max_length=16)


INPUTS: dict[TaskKind, type[TaskInput]] = {
    'title': TaskInput,
    'tags': TaskInput,
    'follow_ups': TaskInput,
    'queries': TaskQueriesInput,
    'autocomplete': TaskTextInput,
    'emoji': TaskTextInput,
    'moa': TaskMoaInput,
}


class TaskTurnRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    contract: Literal['soev.task/1'] = 'soev.task/1'
    task: TaskKind
    source_thread: str | None = Field(default=None, pattern=r'^[A-Za-z0-9_.-]{1,128}$')
    input: TaskInput | TaskTextInput | TaskQueriesInput | TaskMoaInput
    language: str | None = Field(
        default=None, min_length=2, max_length=128, pattern=r'^[A-Za-z]{1,8}(?:-[A-Za-z0-9]{1,8})*$'
    )
    idempotency_key: str = Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9_.:-]+$')

    @model_validator(mode='after')
    def valid_input(self) -> 'TaskTurnRequest':
        expected = INPUTS[self.task]
        if type(self.input) is not expected or (expected is TaskInput and self.source_thread is None):
            raise ValueError('Invalid task input')
        return self


class TaskTitleOutput(TaskInput):
    title: Annotated[str, Field(strict=True, min_length=1, max_length=256)]


class TaskTagsOutput(TaskInput):
    tags: list[Annotated[str, Field(strict=True, min_length=1, max_length=64)]] = Field(max_length=16)


class TaskFollowUpsOutput(TaskInput):
    follow_ups: list[Annotated[str, Field(strict=True, min_length=1, max_length=512)]] = Field(max_length=8)


class TaskQueriesOutput(TaskInput):
    queries: list[Annotated[str, Field(strict=True, min_length=1, max_length=200)]] = Field(max_length=8)


class TaskCompletionOutput(TaskInput):
    completion: Annotated[str, Field(strict=True, min_length=1, max_length=4096)]


class TaskEmojiOutput(TaskInput):
    emoji: Annotated[str, Field(strict=True, min_length=1, max_length=32)]


class TaskMoaOutput(TaskInput):
    response: TaskText


OUTPUTS: dict[TaskKind, type[TaskInput]] = {
    'title': TaskTitleOutput,
    'tags': TaskTagsOutput,
    'follow_ups': TaskFollowUpsOutput,
    'queries': TaskQueriesOutput,
    'autocomplete': TaskCompletionOutput,
    'emoji': TaskEmojiOutput,
    'moa': TaskMoaOutput,
}


class TaskTurnResult(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    contract: Literal['soev.task/1']
    task: TaskKind
    output: (
        TaskTitleOutput
        | TaskTagsOutput
        | TaskFollowUpsOutput
        | TaskQueriesOutput
        | TaskCompletionOutput
        | TaskEmojiOutput
        | TaskMoaOutput
    )
    model: str = Field(min_length=1, max_length=256)

    @model_validator(mode='after')
    def valid_output(self) -> 'TaskTurnResult':
        if type(self.output) is not OUTPUTS[self.task]:
            raise ValueError('Invalid task output')
        return self


def task_request(request: Request, task: TaskKind, form: dict) -> TaskTurnRequest:
    values = {} if INPUTS[task] is TaskInput else {'text': form.get('prompt')}
    if task == 'queries':
        values = {'text': form.get('prompt') or _last_text(form.get('messages') or []), 'purpose': form.get('type')}
    elif task == 'moa':
        values['responses'] = form.get('responses')
    language = form.get('language')
    if language is None:
        language = request.headers.get('Accept-Language', '').split(',')[0].split(';')[0].strip() or None
    source = form.get('chat_id')
    if isinstance(source, str) and is_temporary_chat_id(source):
        source = None
    messages = form.get('messages') or []
    message_id = form.get('message_id') or next((item['id'] for item in reversed(messages) if item.get('id')), None)
    # A thread reference alone cannot distinguish later turns from retries.
    anchor = message_id
    if anchor is None and INPUTS[task] is TaskInput:
        if not messages:
            raise HTTPException(status_code=422)
        anchor = messages
    replay = json.dumps([source, task, anchor, values, language], sort_keys=True, ensure_ascii=False)
    try:
        return TaskTurnRequest(
            task=task,
            source_thread=source,
            input=INPUTS[task].model_validate(values),
            language=language,
            idempotency_key=hashlib.sha256(replay.encode()).hexdigest(),
        )
    except ValidationError:
        raise HTTPException(status_code=422) from None


def _last_text(messages: list[dict]) -> str:
    content = next((item.get('content') for item in reversed(messages) if item.get('role') == 'user'), '')
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get('type') == 'text':
                text = part.get('text')
                if not isinstance(text, str):
                    raise HTTPException(status_code=422)
                parts.append(text)
        return '\n'.join(parts)
    return ''


def completion_response(result: TaskTurnResult) -> dict:
    output = result.output.model_dump()
    if result.task == 'autocomplete':
        output = {'text': output['completion']}
    content = (
        json.dumps(output, ensure_ascii=False)
        if result.task in {'title', 'tags', 'follow_ups', 'queries', 'autocomplete'}
        else next(iter(output.values()))
    )
    return {
        'id': f'chatcmpl-{uuid4().hex}',
        'object': 'chat.completion',
        'model': result.model,
        'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': content}, 'finish_reason': 'stop'}],
    }


async def _source_thread(request: Request, user: Any, chat_id: str) -> str:
    if threads.enabled():
        from open_webui.soev.chat_routes import paths

        return (await paths(request).record(user.id, chat_id)).thread_id

    from open_webui.models.chats import Chats

    chat = await Chats.get_chat_by_id_and_user_id(chat_id, user.id)
    if chat is not None:
        messages = await Chats.get_messages_map_by_chat_id(chat_id) or {}
        current = chat.current_message_id or Chats.get_current_message_id(chat.chat)
        seen = set()
        while current and current not in seen:
            seen.add(current)
            message = messages.get(current) or {}
            bookmark = (message.get('meta') or {}).get('agent_v2') or {}
            if bookmark.get('thread_id'):
                return bookmark['thread_id']
            current = message.get('parentId')
    raise SoevApiError(404, 'not_found', 'Source thread unavailable')


async def complete_task(request: Request, task: TaskKind, form: dict, user: Any) -> dict | StreamingResponse:
    source = form.get('chat_id')
    if isinstance(source, str) and source and not is_temporary_chat_id(source):
        source = await _source_thread(request, user, source)
    turn = task_request(request, task, {**form, 'chat_id': source})
    client = identity.build_client(timeout=120.0)
    raw = await client.submit_task(turn, as_user=identity.external_ref(user))
    try:
        result = TaskTurnResult.model_validate(raw)
        if result.task != task:
            raise ValueError('Unexpected task result')
    except (ValidationError, ValueError):
        raise SoevApiError(502, 'upstream_error', 'Invalid task result') from None
    response = completion_response(result)
    if task != 'moa' or not form.get('stream'):
        return response

    async def chunks() -> AsyncIterator[str]:
        # MOA's UI consumes SSE even though task turns return one JSON response.
        yield (
            'data: '
            + json.dumps(
                {
                    'id': response['id'],
                    'object': 'chat.completion.chunk',
                    'model': result.model,
                    'choices': [{'index': 0, 'delta': response['choices'][0]['message'], 'finish_reason': None}],
                }
            )
            + '\n\n'
        )
        yield (
            'data: '
            + json.dumps(
                {
                    'id': response['id'],
                    'object': 'chat.completion.chunk',
                    'model': result.model,
                    'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}],
                }
            )
            + '\n\n'
        )
        yield 'data: [DONE]\n\n'

    return StreamingResponse(chunks(), media_type='text/event-stream')
