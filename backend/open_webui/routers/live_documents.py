"""Explicit picker selections attach through the platform's user-authorized path."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from open_webui.constants import ERROR_MESSAGES
from open_webui.models.config import Config
from open_webui.models.files import Files
from open_webui.soev import identity, ingest
from open_webui.soev.client import SoevApiError
from open_webui.soev.live_documents import AttachmentMismatch, chat_file, register_attachment
from open_webui.utils.auth import get_verified_user
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

router = APIRouter()


class PickerAttachment(BaseModel):
    model_config = ConfigDict(extra='forbid')

    grant_id: str = Field(min_length=1, max_length=256)
    drive_id: str = Field(min_length=1, max_length=1024)
    item_id: str = Field(min_length=1, max_length=1024)
    etag: str = Field(min_length=1, max_length=1024)
    name: str = Field(min_length=1, max_length=1024)
    web_url: HttpUrl
    size: int = Field(ge=0, strict=True)

    @field_validator('web_url')
    @classmethod
    def secure_url(cls, value: HttpUrl) -> HttpUrl:
        if value.scheme != 'https' or value.username or value.password:
            raise ValueError('An HTTPS document URL is required')
        return value


@router.post('/onedrive/attach', status_code=201)
async def attach_onedrive(
    body: PickerAttachment,
    idempotency_key: Annotated[UUID, Header(alias='Idempotency-Key')],
    user=Depends(get_verified_user),
):
    if user.role != 'admin':
        cap = await Config.get('rag.file.max_count_per_user')
        if cap and int(cap) > 0 and await Files.count_files_by_user_id(user.id) >= int(cap):
            raise HTTPException(status_code=403, detail=ERROR_MESSAGES.FILE_LIMIT_EXCEEDED(int(cap)))
    client = identity.build_client()
    try:
        subject = await identity.acting_ref(user, client)
        collection = await ingest.ensure_attachments_collection(user.id, client)
        ref = body.model_dump(include={'grant_id', 'drive_id', 'item_id', 'etag'})
        result = await client.send(
            'POST',
            '/v1/attach',
            {'collection_key': collection, 'ref': ref},
            as_user=subject,
            idempotency_key=f'picker:{user.id}:{idempotency_key}',
        )
    except SoevApiError as error:
        raise HTTPException(
            status_code=error.status,
            detail={
                'code': error.code,
                'detail': error.detail,
                **({'provider': error.provider} if error.provider is not None else {}),
            },
            headers={'Retry-After': error.retry_after} if error.retry_after is not None else None,
        ) from None
    try:
        file = await register_attachment(
            user.id,
            {
                **result,
                'name': body.name,
                'web_url': str(body.web_url),
                'provider': 'onedrive',
                'provider_ref': ref,
                'attached_by': 'user',
            },
        )
    except AttachmentMismatch as error:
        raise HTTPException(
            status_code=error.status, detail={'code': 'attachment_mismatch', 'detail': str(error)}
        ) from None
    return chat_file(file)
