"""Forward bounded bug reports using the requesting persons platform authority."""

from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends
from open_webui.soev.identity import build_client
from open_webui.utils.auth import get_verified_user
from pydantic import BaseModel, ConfigDict, Field

router = APIRouter()


class FeedbackReportContext(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    route: str | None = Field(default=None, max_length=2048)
    app_version: str | None = Field(default=None, max_length=128)
    user_agent: str | None = Field(default=None, max_length=512)
    error_message: str | None = Field(default=None, max_length=2000)
    error_detail: str | None = Field(default=None, max_length=4000)
    model: str | None = Field(default=None, max_length=256)
    chat_id: str | None = Field(default=None, max_length=128)
    trace_id: str | None = Field(default=None, max_length=128)


class FeedbackReportForm(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    category: Literal['bug', 'idea', 'question', 'error', 'other']
    description: str = Field(min_length=1, max_length=5000)
    context: FeedbackReportContext = Field(default_factory=FeedbackReportContext)


@router.post('/report')
async def submit_feedback_report(form: FeedbackReportForm, user=Depends(get_verified_user)):
    return await build_client(timeout=20).send(
        'POST',
        '/v1/bug-reports',
        form.model_dump(exclude_none=True),
        as_user=f'owui:user:{user.id}',
        idempotency_key=str(uuid4()),
    )
