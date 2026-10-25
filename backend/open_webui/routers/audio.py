"""Speech and dictation use the platform's inference roles."""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from open_webui.constants import ERROR_MESSAGES
from open_webui.events import EVENTS, publish_event
from open_webui.models.config import Config
from open_webui.soev import primitives
from open_webui.utils.access_control import has_permission
from open_webui.utils.auth import get_verified_user
from open_webui.utils.features import is_feature_enabled
from pydantic import BaseModel, ConfigDict, Field

router = APIRouter()


class SpeechForm(BaseModel):
    model_config = ConfigDict(extra='forbid')

    input: str = Field(min_length=1)
    voice: str | None = None
    response_format: Literal['mp3', 'opus', 'aac', 'flac', 'wav', 'pcm'] = 'mp3'


@router.post('/speech')
async def speech(request: Request, form_data: SpeechForm, user=Depends(get_verified_user)):
    if not (is_feature_enabled('read_aloud') or is_feature_enabled('voice_call')):
        raise HTTPException(status_code=403, detail="Feature 'read_aloud' is not available in your plan")
    if user.role != 'admin' and not await has_permission(user.id, 'chat.tts', await Config.get('user.permissions')):
        raise HTTPException(status_code=403, detail=ERROR_MESSAGES.ACCESS_PROHIBITED)

    response = await primitives.speech(form_data.model_dump(exclude_none=True), user)
    await publish_event(
        request,
        EVENTS.AUDIO_SPEECH_REQUESTED,
        actor=user,
        subject_id=str(uuid.uuid4()),
        data={'engine': 'soev'},
    )
    return response


@router.post('/transcriptions')
async def transcription(
    request: Request,
    file: UploadFile = File(...),
    language: str | None = Form(None),
    user=Depends(get_verified_user),
):
    # [Gradient] Dictation is tenant-gated; the gate binds admins too.
    if not is_feature_enabled('voice'):
        raise HTTPException(
            status_code=403,
            detail="Feature 'voice' is not available in your plan",
        )

    if user.role != 'admin' and not await has_permission(user.id, 'chat.stt', await Config.get('user.permissions')):
        raise HTTPException(
            status_code=403,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )
    result = await primitives.transcribe(file, language, user)
    await publish_event(
        request,
        EVENTS.AUDIO_TRANSCRIPTION_REQUESTED,
        actor=user,
        subject_id=str(uuid.uuid4()),
        data={'content_type': file.content_type, 'language': language},
    )
    return result
