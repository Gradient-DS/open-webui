import logging
import random
import sys
from typing import Any

from fastapi import HTTPException, Request
from open_webui.env import BYPASS_MODEL_ACCESS_CONTROL, GLOBAL_LOG_LEVEL
from open_webui.functions import generate_function_chat_completion
from open_webui.models.users import UserModel
from open_webui.routers.ollama import (
    generate_chat_completion as generate_ollama_chat_completion,
)
from open_webui.socket.main import (
    get_event_call,
    get_event_emitter,
)
from open_webui.soev import model_catalog
from open_webui.utils.filter import (
    get_filter_functions,
    process_filter_functions,
)
from open_webui.utils.json_codec import JSONCodec
from open_webui.utils.models import check_model_access, get_all_models
from open_webui.utils.payload import convert_payload_openai_to_ollama
from open_webui.utils.response import (
    convert_response_ollama_to_openai,
    convert_streaming_response_ollama_to_openai,
)
from starlette.responses import StreamingResponse

logging.basicConfig(stream=sys.stdout, level=GLOBAL_LOG_LEVEL)
log = logging.getLogger(__name__)


# When the question has been asked, let silence not be the
# answer. But if the answer must wait, let it come honest.


async def generate_chat_completion(
    request: Request,
    form_data: dict,
    user: Any,
    bypass_filter: bool = False,
    bypass_system_prompt: bool = False,
):
    if model_catalog.is_v2():
        raise HTTPException(status_code=400)
    log.debug('generate_chat_completion: %s', form_data)
    if BYPASS_MODEL_ACCESS_CONTROL:
        bypass_filter = True

    # Propagate bypass_filter and bypass_system_prompt via request.state so that
    # downstream route handlers (openai/ollama) can read them without exposing
    # them as query parameters.
    request.state.bypass_filter = bypass_filter
    request.state.bypass_system_prompt = bypass_system_prompt

    if hasattr(request.state, 'metadata'):
        if 'metadata' not in form_data:
            form_data['metadata'] = request.state.metadata
        else:
            form_data['metadata'] = {
                **form_data['metadata'],
                **request.state.metadata,
            }

    models = request.app.state.MODELS

    model_id = form_data['model']
    # Single lookup — membership check plus getitem would be two Redis
    # round trips on a Redis-backed model pool.
    model = models.get(model_id)
    if model is None:
        raise Exception('Model not found')

    if not bypass_filter and user.role == 'user':
        try:
            await check_model_access(user, model)
        except Exception as e:
            raise e
    metadata = form_data.get('metadata', {})
    selected_model_id = metadata.pop('selected_model_id', None)
    if hasattr(request.state, 'metadata'):
        request.state.metadata.pop('selected_model_id', None)
    if not selected_model_id and model.get('owned_by') == 'arena':
        model_ids = model.get('info', {}).get('meta', {}).get('model_ids')
        filter_mode = model.get('info', {}).get('meta', {}).get('filter_mode')
        if model_ids and filter_mode == 'exclude':
            model_ids = [
                available_model['id']
                for available_model in list(request.app.state.MODELS.values())
                if available_model.get('owned_by') != 'arena' and available_model['id'] not in model_ids
            ]

        if isinstance(model_ids, list) and model_ids:
            selected_model_id = random.choice(model_ids)
        else:
            model_ids = [
                available_model['id']
                for available_model in list(request.app.state.MODELS.values())
                if available_model.get('owned_by') != 'arena'
            ]
            selected_model_id = random.choice(model_ids)

        form_data['model'] = selected_model_id

        # bypass_filter recursion below skips the line-200 check; gate the resolved model here.
        if not bypass_filter and user.role == 'user':
            selected_model = request.app.state.MODELS.get(selected_model_id)
            if selected_model:
                await check_model_access(user, selected_model)
    if selected_model_id:
        if form_data.get('stream') == True:

            async def stream_wrapper(stream):
                yield f'data: {JSONCodec.dumps({"selected_model_id": selected_model_id})}\n\n'
                async for chunk in stream:
                    yield chunk

            response = await generate_chat_completion(
                request,
                form_data,
                user,
                bypass_filter=True,
                bypass_system_prompt=bypass_system_prompt,
            )
            return StreamingResponse(
                stream_wrapper(response.body_iterator),
                media_type='text/event-stream',
                background=response.background,
            )
        else:
            return {
                **(
                    await generate_chat_completion(
                        request,
                        form_data,
                        user,
                        bypass_filter=True,
                        bypass_system_prompt=bypass_system_prompt,
                    )
                ),
                'selected_model_id': selected_model_id,
            }
    if model.get('pipe'):
        return await generate_function_chat_completion(request, form_data, user=user, models=models)
    if model.get('owned_by') == 'ollama':
        # Using /ollama/api/chat endpoint
        form_data = convert_payload_openai_to_ollama(form_data)
        response = await generate_ollama_chat_completion(
            request=request,
            form_data=form_data,
            user=user,
        )
        if form_data.get('stream'):
            response.headers['content-type'] = 'text/event-stream'
            return StreamingResponse(
                convert_streaming_response_ollama_to_openai(response),
                headers=dict(response.headers),
                background=response.background,
            )
        else:
            return convert_response_ollama_to_openai(response)
    else:
        raise HTTPException(status_code=404, detail='Model not found')


chat_completion = generate_chat_completion


async def chat_completed(request: Request, form_data: dict, user: Any):
    if not request.app.state.MODELS:
        await get_all_models(request, user=user)

    models = request.app.state.MODELS

    data = form_data

    if not data.get('id'):
        raise Exception('Missing message id')

    model_id = data['model']
    if model_id not in models:
        raise Exception('Model not found')

    model = models[model_id]

    if not data.get('id'):
        raise Exception('Missing message id')

    metadata = {
        'chat_id': data['chat_id'],
        'message_id': data['id'],
        'filter_ids': data.get('filter_ids', []),
        'session_id': data['session_id'],
        'user_id': user.id,
    }

    extra_params = {
        '__event_emitter__': await get_event_emitter(metadata),
        '__event_call__': await get_event_call(metadata),
        '__user__': user.model_dump() if isinstance(user, UserModel) else {},
        '__metadata__': metadata,
        '__request__': request,
        '__model__': model,
    }

    try:
        filter_functions = await get_filter_functions(request, model, metadata.get('filter_ids', []))

        result, _ = await process_filter_functions(
            request=request,
            filter_context=None,
            filter_functions=filter_functions,
            filter_type='outlet',
            form_data=data,
            extra_params=extra_params,
        )
        return result
    except Exception as e:
        raise Exception(f'Error: {e}')
