import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from open_webui.internal.db import get_async_session
from open_webui.models.chat_messages import ChatMessages
from open_webui.models.chats import Chats
from open_webui.models.feedbacks import Feedbacks
from open_webui.models.groups import Groups
from open_webui.models.users import Users
from open_webui.soev.thread_content import refuse_local_content
from open_webui.utils.auth import get_admin_user, get_verified_user
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)


router = APIRouter()


async def require_local_analytics(user=Depends(get_verified_user)) -> None:
    refuse_local_content()


####################
# Response Models
####################


class ModelAnalyticsEntry(BaseModel):
    model_id: str
    count: int
    unique_users: int = 0
    unique_chats: int = 0


class ModelAnalyticsResponse(BaseModel):
    models: list[ModelAnalyticsEntry]


class UserAnalyticsEntry(BaseModel):
    user_id: str
    name: Optional[str] = None
    email: Optional[str] = None
    count: int
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class UserAnalyticsResponse(BaseModel):
    users: list[UserAnalyticsEntry]


####################
# Endpoints
####################


class SummaryResponse(BaseModel):
    total_messages: int
    total_chats: int
    total_models: int
    total_users: int


class DailyStatsEntry(BaseModel):
    date: str
    models: dict[str, int]


class DailyStatsResponse(BaseModel):
    data: list[DailyStatsEntry]


class TokenUsageEntry(BaseModel):
    model_id: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    message_count: int


class TokenUsageResponse(BaseModel):
    models: list[TokenUsageEntry]
    total_input_tokens: int
    total_output_tokens: int
    total_tokens: int


####################
# Model Overview
####################


class HistoryEntry(BaseModel):
    date: str
    won: int = 0
    lost: int = 0


class TagEntry(BaseModel):
    tag: str
    count: int


class ModelOverviewResponse(BaseModel):
    history: list[HistoryEntry]
    tags: list[TagEntry]
