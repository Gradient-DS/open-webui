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
    """Run the title or tags task as a soev.task/1 turn on the chat's thread."""
    if not await Config.get(f'task.{kind}.enable'):
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
    if not (tasks.get('title_generation') or tasks.get('tags_generation')):
        return
    service = paths(ctx['request'])
    user, metadata = ctx['user'], ctx['metadata']
    chat_id = metadata.get('chat_id')
    if not chat_id:
        return
    messages = [*ctx['form_data'].get('messages', [])]
    if ctx.get('assistant_message'):
        messages.append({'role': 'assistant', 'content': ctx['assistant_message'].get('content', '')})
    for field in ('title', 'tags'):
        if not tasks.get(f'{field}_generation'):
            continue
        try:
            await complete_field(ctx['request'], service, user, chat_id, field, messages)
        except Exception as error:
            log.warning('Thread metadata task failed: field=%s status=%s', field, getattr(error, 'status', None))


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
    if not (tasks.get('title_generation') or tasks.get('tags_generation')):
        return response
    from open_webui.utils.middleware import update_assistant_message_from_stream

    if not isinstance(response, StreamingResponse):
        if isinstance(response, dict) and 'error' not in response:
            choices = response.get('choices') or []
            ctx['assistant_message'] = choices[0].get('message', {}) if choices else {}
            await complete(ctx)
        return response

    async def stream():
        assistant = {}
        failed = False
        async for chunk in response.body_iterator:
            text = chunk.decode() if isinstance(chunk, bytes) else chunk
            for line in text.splitlines():
                if line.startswith('data:') and line[5:].strip() != '[DONE]':
                    try:
                        failed |= 'error' in json.loads(line[5:])
                    except ValueError:
                        pass
            update_assistant_message_from_stream(assistant, chunk)
            yield chunk
        if not failed:
            ctx['assistant_message'] = assistant
            await complete(ctx)

    return StreamingResponse(stream(), headers=dict(response.headers), background=response.background)
