"""[Gradient] Database and access-control seam for opt-in split completions."""

from fastapi import HTTPException
from open_webui.models.chat_messages import ChatMessages
from open_webui.models.chats import Chats
from open_webui.models.models import ModelModel, Models
from open_webui.models.users import UserModel
from open_webui.utils.agent_routing import AssistantBindingConflict, resolve_assistant_binding
from open_webui.utils.assistants import build_assistant_model, merged_model_info
from open_webui.utils.models import check_model_access


def _check_split_message_models(model_id: str, message_ids: list | dict | None) -> None:
    """Keep saved-chat fanout from overriding the independently authorized LLM."""
    if isinstance(message_ids, dict):
        targets = message_ids.keys()
    elif isinstance(message_ids, list):
        targets = [entry.get('model_id') for entry in message_ids]
    else:
        return
    if any(target != model_id for target in targets):
        raise HTTPException(status_code=400, detail='Split requests must use the selected LLM for every message.')


async def resolve_assistant_request(
    assistant_id: str,
    model: dict,
    model_info: ModelModel | None,
    user: UserModel,
    *,
    check_access: bool,
    chat_id: str | None,
    message_ids: list | dict | None = None,
) -> tuple[dict, ModelModel, bool]:
    """Authorize both identities and preflight binding before message placeholders exist."""
    if not isinstance(assistant_id, str) or (model_info and model_info.base_model_id is not None):
        raise ValueError('Model not found')
    _check_split_message_models(model['id'], message_ids)
    assistant = await Models.get_model_by_id(assistant_id)
    if not assistant or not assistant.is_active or assistant.base_model_id is None:
        raise ValueError('Model not found')
    if check_access:
        # Check the assistant's grants without following its obsolete default LLM.
        await check_model_access(
            user, {'id': assistant.id}, model_info=assistant.model_copy(update={'base_model_id': None})
        )

    bind = True
    if chat_id and not chat_id.startswith('local:'):
        row = await Chats.get_chat_by_id_and_user_id(chat_id, user.id)
        if row:
            messages = await ChatMessages.get_messages_by_chat_id(chat_id)
            has_message = any(message.role == 'assistant' for message in messages)
            has_message = has_message or any(
                message.get('role') == 'assistant'
                for message in ((row.chat or {}).get('history') or {}).get('messages', {}).values()
            )
            try:
                bind = (
                    resolve_assistant_binding((row.meta or {}).get('assistant_id'), assistant_id, has_message) == 'bind'
                )
            except AssistantBindingConflict as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
    return build_assistant_model(assistant, model), merged_model_info(model_info, assistant), bind
