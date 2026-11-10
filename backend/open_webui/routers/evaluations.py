import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from open_webui.soev.chat_routes import EvaluationRoute
from open_webui.constants import ERROR_MESSAGES
from open_webui.events import EVENTS, publish_event
from open_webui.internal.db import get_async_session
from open_webui.models.config import Config
from open_webui.models.feedbacks import (
    FeedbackForm,
    FeedbackIdResponse,
    FeedbackListResponse,
    FeedbackModel,
    Feedbacks,
    LeaderboardFeedbackData,
    ModelHistoryEntry,
    ModelHistoryResponse,
)
from open_webui.models.users import UserModel, Users
from open_webui.utils.auth import get_admin_user, get_verified_user
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from open_webui import config
from open_webui.utils.access_control import can_access_admin_feedback

log = logging.getLogger(__name__)


router = APIRouter(route_class=EvaluationRoute)

EVALUATION_CONFIG_KEYS = {
    'ENABLE_EVALUATION_ARENA_MODELS': 'evaluation.arena.enable',
    'EVALUATION_ARENA_MODELS': 'evaluation.arena.models',
    # [Gradient] Fork feedback config surfaced to the admin UI. Every field must
    # be mapped here (GET) and written in update_config (POST); unmapped keys
    # silently vanish from the API response.
    'ENABLE_MESSAGE_RATING': 'ui.enable_message_rating',
    'ENABLE_FEEDBACK_LAYER2': 'evaluation.feedback.layer2.enable',
    'FEEDBACK_REASONS': 'evaluation.feedback.reasons',
    'FEEDBACK_TAGS': 'evaluation.feedback.tags',
    'ENABLE_FEEDBACK_LAYER3': 'evaluation.feedback.layer3.enable',
    'FEEDBACK_LAYER3_PROMPT': 'evaluation.feedback.layer3.prompt',
    'ENABLE_FEEDBACK_CATEGORY_TAGS': 'evaluation.feedback.category_tags.enable',
    'ENABLE_CONVERSATION_FEEDBACK': 'evaluation.feedback.conversation.enable',
    'CONVERSATION_FEEDBACK_SCALE_MAX': 'evaluation.feedback.conversation.scale_max',
    'CONVERSATION_FEEDBACK_HEADER': 'evaluation.feedback.conversation.header',
    'CONVERSATION_FEEDBACK_PLACEHOLDER': 'evaluation.feedback.conversation.placeholder',
}


async def get_config_values(key_map: dict[str, str]) -> dict:
    values = await Config.get_many(*key_map.values())
    return {field: values[storage_key] for field, storage_key in key_map.items() if storage_key in values}


# Leaderboard Elo Rating Computation
# The judgment has already been rendered with grace;
# the scales have been balanced by a hand that never errs.
#
# How it works:
# 1. Each model starts with a rating of 1000
# 2. When a user picks a winner between two models, ratings are adjusted:
#    - Winner gains points, loser loses points
#    - The amount depends on expected outcome (upset = bigger change)
# 3. The Elo formula: new_rating = old_rating + K * (actual - expected)
#    - K=32 controls how much ratings can change per match
#    - expected = probability of winning based on current ratings
#
# Query-based re-ranking (optional):
#    When a user searches for a topic (e.g., "coding"), we want to show
#    which models perform best FOR THAT TOPIC. We do this by:
#    1. Computing semantic similarity between the query and each feedback's tags
#    2. Using that similarity as a weight in the Elo calculation
#    3. Feedbacks about "coding" contribute more to the final ranking
#    4. Feedbacks about unrelated topics (e.g., "cooking") contribute less
#    This gives topic-specific leaderboards without needing separate data.

import os

EMBEDDING_MODEL_NAME = os.environ.get('AUXILIARY_EMBEDDING_MODEL', 'TaylorAI/bge-micro-v2')
_embedding_model = None


def _get_embedding_model():
    global _embedding_model
    if _embedding_model is None:
        try:
            from sentence_transformers import SentenceTransformer

            _embedding_model = SentenceTransformer(EMBEDDING_MODEL_NAME)
        except Exception as e:
            log.error(f'Embedding model load failed: {e}')
    return _embedding_model


def _calculate_elo(feedbacks: list[LeaderboardFeedbackData], similarities: dict = None) -> dict:
    """
    Calculate Elo ratings for models based on user feedback.

    Each feedback represents a comparison where a user rated one model
    against its opponents (sibling_model_ids). Rating=1 means the model won,
    rating=-1 means it lost.

    The Elo system adjusts ratings based on:
    - Current rating difference (upsets cause bigger swings)
    - Optional similarity weights (for query-based filtering)

    Returns: {model_id: {"rating": float, "won": int, "lost": int}}
    """
    K_FACTOR = 32  # Standard Elo K-factor for rating volatility
    model_stats = {}

    def get_or_create_stats(model_id):
        if model_id not in model_stats:
            model_stats[model_id] = {'rating': 1000.0, 'won': 0, 'lost': 0}
        return model_stats[model_id]

    for feedback in feedbacks:
        data = feedback.data or {}
        winner_id = data.get('model_id')
        rating_value = str(data.get('rating', ''))
        if not winner_id or rating_value not in ('1', '-1'):
            continue

        won = rating_value == '1'
        weight = similarities.get(feedback.id, 1.0) if similarities else 1.0

        for opponent_id in data.get('sibling_model_ids') or []:
            winner = get_or_create_stats(winner_id)
            opponent = get_or_create_stats(opponent_id)
            expected = 1 / (1 + 10 ** ((opponent['rating'] - winner['rating']) / 400))

            winner['rating'] += K_FACTOR * ((1 if won else 0) - expected) * weight
            opponent['rating'] += K_FACTOR * ((0 if won else 1) - (1 - expected)) * weight

            if won:
                winner['won'] += 1
                opponent['lost'] += 1
            else:
                winner['lost'] += 1
                opponent['won'] += 1

    return model_stats


def _get_top_tags(feedbacks: list[LeaderboardFeedbackData], limit: int = 5) -> dict:
    """
    Count tag occurrences per model and return the most frequent ones.

    Each feedback can have tags describing the conversation topic.
    This aggregates those tags per model to show what topics each model
    is commonly used for.

    Returns: {model_id: [{"tag": str, "count": int}, ...]}
    """
    from collections import defaultdict

    tag_counts = defaultdict(lambda: defaultdict(int))

    for feedback in feedbacks:
        data = feedback.data or {}
        model_id = data.get('model_id')
        if model_id:
            for tag in data.get('tags', []):
                tag_counts[model_id][tag] += 1

    return {
        model_id: [{'tag': tag, 'count': count} for tag, count in sorted(tags.items(), key=lambda x: -x[1])[:limit]]
        for model_id, tags in tag_counts.items()
    }


def _compute_similarities(feedbacks: list[LeaderboardFeedbackData], query: str) -> dict:
    """
    Compute how relevant each feedback is to a search query.

    Uses embeddings to find semantic similarity between the query and
    each feedback's tags. Higher similarity means the feedback is more
    relevant to what the user searched for.

    This is used to weight Elo calculations - feedbacks matching the
    query have more influence on the final rankings.

    Returns: {feedback_id: similarity_score (0-1)}
    """
    import numpy as np

    embedding_model = _get_embedding_model()
    if not embedding_model:
        return {}

    all_tags = list({tag for feedback in feedbacks if feedback.data for tag in feedback.data.get('tags', [])})
    if not all_tags:
        return {}

    try:
        tag_embeddings = embedding_model.encode(all_tags)
        query_embedding = embedding_model.encode([query])[0]
    except Exception as e:
        log.error(f'Embedding error: {e}')
        return {}

    # Vectorized cosine similarity
    tag_norms = np.linalg.norm(tag_embeddings, axis=1)
    query_norm = np.linalg.norm(query_embedding)
    similarities = np.dot(tag_embeddings, query_embedding) / (tag_norms * query_norm + 1e-9)
    tag_similarity_map = dict(zip(all_tags, similarities.tolist()))

    return {
        feedback.id: max(
            (tag_similarity_map.get(tag, 0) for tag in (feedback.data or {}).get('tags', [])),
            default=0,
        )
        for feedback in feedbacks
    }


class LeaderboardEntry(BaseModel):
    model_id: str
    rating: int
    won: int
    lost: int
    count: int
    top_tags: list[dict]


class LeaderboardResponse(BaseModel):
    entries: list[LeaderboardEntry]


############################
# GetConfig
############################


############################
# UpdateConfig
############################


class UpdateConfigForm(BaseModel):
    ENABLE_EVALUATION_ARENA_MODELS: Optional[bool] = None
    EVALUATION_ARENA_MODELS: Optional[list[dict]] = None
    ENABLE_MESSAGE_RATING: Optional[bool] = None
    ENABLE_FEEDBACK_LAYER2: Optional[bool] = None
    FEEDBACK_REASONS: list[dict] | None = None
    FEEDBACK_TAGS: list[dict] | None = None
    ENABLE_FEEDBACK_LAYER3: Optional[bool] = None
    FEEDBACK_LAYER3_PROMPT: Optional[str] = None
    ENABLE_FEEDBACK_CATEGORY_TAGS: Optional[bool] = None
    ENABLE_CONVERSATION_FEEDBACK: Optional[bool] = None
    CONVERSATION_FEEDBACK_SCALE_MAX: Optional[int] = None
    CONVERSATION_FEEDBACK_HEADER: Optional[str] = None
    CONVERSATION_FEEDBACK_PLACEHOLDER: Optional[str] = None


PAGE_ITEM_COUNT = 30


@router.get('/feedbacks/user', response_model=FeedbackListResponse)
async def get_user_feedbacks(
    page: Optional[int] = 1,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    limit = PAGE_ITEM_COUNT
    page = max(1, page)
    skip = (page - 1) * limit
    return await Feedbacks.get_feedbacks_by_user_id(user.id, skip=skip, limit=limit, db=db)


@router.delete('/feedbacks', response_model=bool)
async def delete_feedbacks(
    request: Request,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    success = await Feedbacks.delete_feedbacks_by_user_id(user.id, db=db)
    if success:
        await publish_event(
            request,
            EVENTS.FEEDBACK_DELETED_ALL,
            actor=user,
            subject_id=user.id,
            subject_type='user',
        )
    return success


@router.get('/feedback/conversation/{chat_id}')
async def get_conversation_feedback(chat_id: str, user=Depends(get_verified_user)):
    feedback = await Feedbacks.get_conversation_feedback_by_chat_id_and_user_id(chat_id=chat_id, user_id=user.id)
    return feedback


@router.post('/feedback', response_model=FeedbackModel)
async def create_feedback(
    request: Request,
    form_data: FeedbackForm,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    feedback = await Feedbacks.insert_new_feedback(user_id=user.id, form_data=form_data, db=db)
    if not feedback:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(),
        )

    await publish_event(
        request,
        EVENTS.FEEDBACK_CREATED,
        actor=user,
        subject_id=feedback.id,
        data={'rating': (feedback.data or {}).get('rating')},
    )
    return feedback


@router.get('/feedback/{id}', response_model=FeedbackModel)
async def get_feedback_by_id(id: str, user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)):
    if can_access_admin_feedback(user):
        feedback = await Feedbacks.get_feedback_by_id(id=id, db=db)
    else:
        feedback = await Feedbacks.get_feedback_by_id_and_user_id(id=id, user_id=user.id, db=db)

    if not feedback:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND)

    return feedback


@router.post('/feedback/{id}', response_model=FeedbackModel)
async def update_feedback_by_id(
    request: Request,
    id: str,
    form_data: FeedbackForm,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    if can_access_admin_feedback(user):
        feedback = await Feedbacks.update_feedback_by_id(id=id, form_data=form_data, db=db)
    else:
        feedback = await Feedbacks.update_feedback_by_id_and_user_id(id=id, user_id=user.id, form_data=form_data, db=db)

    if not feedback:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND)

    await publish_event(
        request,
        EVENTS.FEEDBACK_UPDATED,
        actor=user,
        subject_id=feedback.id,
        data={'rating': (feedback.data or {}).get('rating')},
    )
    return feedback


@router.delete('/feedback/{id}')
async def delete_feedback_by_id(
    request: Request,
    id: str,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    if can_access_admin_feedback(user):
        success = await Feedbacks.delete_feedback_by_id(id=id, db=db)
    else:
        success = await Feedbacks.delete_feedback_by_id_and_user_id(id=id, user_id=user.id, db=db)

    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND)

    await publish_event(
        request,
        EVENTS.FEEDBACK_DELETED,
        actor=user,
        subject_id=id,
    )
    return success
