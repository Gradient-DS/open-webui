"""Forward approvals as platform inputs without rebuilding local message output."""

from open_webui.soev import threads
from open_webui.soev.chat_paths import ChatPaths, Organizations
from open_webui.utils.tool_approval import ResolveToolCallForm


async def resolve(organizations: Organizations, chat_id: str, form: ResolveToolCallForm, user_id: str) -> dict:
    client = threads.build_client()
    record = await ChatPaths(client, organizations).record(user_id, chat_id)
    await client.projection(record.thread_id, window=1)
    if form.action == 'answer':
        payload = {'answers': form.answers or {}, 'timed_out': form.timed_out}
    else:
        payload = {'approve': form.action == 'approve'}
    await client.answer(record.thread_id, answering=form.call_id, input=payload)
    return {'status': True, 'chat_id': chat_id}
