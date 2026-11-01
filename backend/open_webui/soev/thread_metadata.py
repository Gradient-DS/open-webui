"""Owner metadata tasks persist through the same path as manual edits."""

import json
import logging

from starlette.responses import StreamingResponse

from open_webui.models.config import Config
from open_webui.soev import task_turns
from open_webui.soev.chat_routes import paths
from open_webui.soev.threads import ThreadMetadataResponse

log = logging.getLogger(__name__)


async def emit_metadata(user_id: str, chat_id: str, metadata: ThreadMetadataResponse, changes: dict) -> None:
    from open_webui.socket.main import sio

    for field in ('title', 'tags'):
        if field in changes:
            await sio.emit(
                'events',
                {
                    'chat_id': chat_id,
                    'message_id': None,
                    'data': {'type': f'chat:{field}', 'data': getattr(metadata, field)},
                },
                room=f'user:{user_id}',
            )


async def generate(request, kind: str, chat_id: str, messages: list[dict], user) -> dict:
    """Run a post-turn task as a soev.task/1 turn on the chat's thread."""
    setting = 'follow_up' if kind == 'follow_ups' else kind
    if not await Config.get(f'task.{setting}.enable'):
        return {}
    return await task_turns.complete_task(request, kind, {'chat_id': chat_id, 'messages': messages}, user)


def generated_value(response: dict, field: str):
    choices = response.get('choices') or []
    message = choices[0].get('message', {}) if choices else {}
    text = message.get('content') or message.get('reasoning_content') or ''
    try:
        return json.loads(text[text.find('{') : text.rfind('}') + 1]).get(field)
    except (ValueError, AttributeError):
        return None


async def complete(ctx: dict) -> None:
    tasks = ctx.get('tasks') or {}
    if not (tasks.get('title_generation') or tasks.get('tags_generation') or tasks.get('follow_up_generation')):
        return
    service = paths(ctx['request'])
    user, metadata = ctx['user'], ctx['metadata']
    chat_id = metadata.get('chat_id')
    if not chat_id:
        return
    messages = [*ctx['form_data'].get('messages', [])]
    if metadata.get('user_message') and not any(message.get('role') == 'user' for message in messages):
        messages.append({**metadata['user_message'], 'role': 'user'})
    if ctx.get('assistant_message'):
        messages.append(
            {
                'id': metadata.get('message_id'),
                'role': 'assistant',
                'content': ctx['assistant_message'].get('content', ''),
            }
        )
    for field, flag in (
        ('title', 'title_generation'),
        ('tags', 'tags_generation'),
        ('follow_ups', 'follow_up_generation'),
    ):
        if not tasks.get(flag):
            continue
        try:
            if field == 'follow_ups':
                await complete_follow_ups(ctx, messages)
            else:
                await complete_field(ctx['request'], service, user, chat_id, field, messages)
        except Exception as error:
            log.warning('Thread metadata task failed: field=%s status=%s', field, getattr(error, 'status', None))


async def complete_follow_ups(ctx: dict, messages: list[dict]) -> None:
    from open_webui.socket.main import sio

    metadata = ctx['metadata']
    if not metadata.get('message_id'):
        return
    value = generated_value(
        await generate(ctx['request'], 'follow_ups', metadata['chat_id'], messages, ctx['user']), 'follow_ups'
    )
    if value is not None:
        await sio.emit(
            'events',
            {
                'chat_id': metadata['chat_id'],
                'message_id': metadata['message_id'],
                'data': {'type': 'chat:message:follow_ups', 'data': {'follow_ups': value}},
            },
            room=f'user:{ctx["user"].id}',
        )


async def complete_field(request, service, user, chat_id: str, field: str, messages: list[dict]) -> None:
    if field == 'title':
        page = (await service.projection(user.id, chat_id, window=1)).page
        if page.title:
            return
    value = generated_value(await generate(request, field, chat_id, messages, user), field)
    if field == 'title' and not value:
        value = next((message.get('content') for message in messages if message.get('role') == 'user'), '')
        value = value[:200] if isinstance(value, str) else ''
    if value is None or (field == 'title' and not value):
        return
    changes = {field: value}
    metadata = await service.metadata(user.id, chat_id, changes)
    await emit_metadata(user.id, chat_id, metadata, changes)


async def finish_response(response, ctx: dict):
    tasks = ctx.get('tasks') or {}
    if not (tasks.get('title_generation') or tasks.get('tags_generation') or tasks.get('follow_up_generation')):
        return response

    if not isinstance(response, StreamingResponse):
        if isinstance(response, dict) and 'error' not in response:
            choices = response.get('choices') or []
            ctx['assistant_message'] = choices[0].get('message', {}) if choices else {}
            await complete(ctx)
        return response

    return StreamingResponse(
        finish_stream(response, ctx), headers=dict(response.headers), background=response.background
    )


async def finish_stream(response, ctx: dict):
    from open_webui.utils.middleware import update_assistant_message_from_stream

    assistant = {}
    failed = False
    terminal_chunks = []
    async for chunk in response.body_iterator:
        text = chunk.decode() if isinstance(chunk, bytes) else chunk
        terminal = False
        for line in text.splitlines():
            if not line.startswith('data:'):
                continue
            if line[5:].strip() == '[DONE]':
                terminal = True
            else:
                try:
                    failed |= 'error' in json.loads(line[5:])
                except ValueError:
                    pass
        update_assistant_message_from_stream(assistant, chunk)
        # The browser cancels its reader at [DONE]. Finish tasks before
        # publishing that marker so the disconnect cannot cancel them.
        if terminal or terminal_chunks:
            terminal_chunks.append(chunk)
        else:
            yield chunk
    if not failed:
        ctx['assistant_message'] = assistant
        await complete(ctx)
    for chunk in terminal_chunks:
        yield chunk
