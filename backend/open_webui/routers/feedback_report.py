"""Product-feedback router — POST /api/v1/feedback/report.

Thin HTTP layer: authentication, request validation (with a strict context
allowlist), and the runtime enable check. All enrichment, logging, and Slack
delivery live in utils/feedback_report.py.

Mounted unconditionally in main.py and gated in-handler on the
ENABLE_FEEDBACK_REPORTING PersistentConfig flag — the fork's preference for
runtime-toggleable features (no pod restart to flip the flag).
"""

from typing import Literal

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from open_webui.models.config import Config
from open_webui.utils.auth import get_verified_user
from open_webui.utils.feedback_report import (
    build_feedback_event,
    emit_feedback_log,
    post_feedback_to_slack,
)

router = APIRouter()


class FeedbackReportContext(BaseModel):
    # extra='forbid' is the hard guarantee that no chat content can be smuggled through
    model_config = ConfigDict(extra='forbid')
    route: str | None = None
    app_version: str | None = None
    user_agent: str | None = None
    error_message: str | None = None
    error_detail: str | None = None
    model: str | None = None
    chat_id: str | None = None
    trace_id: str | None = None


class FeedbackReportForm(BaseModel):
    category: Literal['bug', 'idea', 'question', 'error', 'other']
    description: str = Field(min_length=1, max_length=5000)
    context: FeedbackReportContext = FeedbackReportContext()


@router.post('/report')
async def submit_feedback_report(request: Request, form: FeedbackReportForm, user=Depends(get_verified_user)):
    config = await Config.get_many(
        'feedback_report.enable',
        'feedback_report.include_user_identity',
        'feedback_report.slack_webhook_url',
        'feedback_report.trace_url_template',
    )
    if not config.get('feedback_report.enable'):
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={'detail': 'Feedback reporting is disabled.'},
        )

    event = build_feedback_event(
        category=form.category,
        description=form.description,
        context=form.context.model_dump(),
        user=user,
        include_identity=config.get('feedback_report.include_user_identity'),
    )
    emit_feedback_log(event)  # the record — always happens
    await post_feedback_to_slack(  # best-effort notification
        event,
        config.get('feedback_report.slack_webhook_url'),
        config.get('feedback_report.trace_url_template', ''),
    )
    return {'status': True}
