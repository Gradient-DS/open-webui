from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status, Request
from pydantic import BaseModel

from open_webui.models.users import Users, UserModel
from open_webui.models.feedbacks import (
    FeedbackIdResponse,
    FeedbackModel,
    FeedbackResponse,
    FeedbackForm,
    FeedbackUserResponse,
    FeedbackListResponse,
    Feedbacks,
)

from open_webui.constants import ERROR_MESSAGES
from open_webui.utils.auth import get_admin_user, get_verified_user

router = APIRouter()


############################
# GetConfig
############################


@router.get("/config")
async def get_config(request: Request, user=Depends(get_admin_user)):
    return {
        "ENABLE_EVALUATION_ARENA_MODELS": request.app.state.config.ENABLE_EVALUATION_ARENA_MODELS,
        "EVALUATION_ARENA_MODELS": request.app.state.config.EVALUATION_ARENA_MODELS,
        "ENABLE_MESSAGE_RATING": request.app.state.config.ENABLE_MESSAGE_RATING,
        "ENABLE_FEEDBACK_LAYER2": request.app.state.config.ENABLE_FEEDBACK_LAYER2,
        "FEEDBACK_LAYER2_POSITIVE_TAGS": request.app.state.config.FEEDBACK_LAYER2_POSITIVE_TAGS,
        "FEEDBACK_LAYER2_NEGATIVE_TAGS": request.app.state.config.FEEDBACK_LAYER2_NEGATIVE_TAGS,
        "ENABLE_FEEDBACK_LAYER3": request.app.state.config.ENABLE_FEEDBACK_LAYER3,
        "FEEDBACK_LAYER3_PROMPT": request.app.state.config.FEEDBACK_LAYER3_PROMPT,
        "ENABLE_FEEDBACK_CATEGORY_TAGS": request.app.state.config.ENABLE_FEEDBACK_CATEGORY_TAGS,
        "ENABLE_CONVERSATION_FEEDBACK": request.app.state.config.ENABLE_CONVERSATION_FEEDBACK,
        "CONVERSATION_FEEDBACK_SCALE_MAX": request.app.state.config.CONVERSATION_FEEDBACK_SCALE_MAX,
        "CONVERSATION_FEEDBACK_HEADER": request.app.state.config.CONVERSATION_FEEDBACK_HEADER,
        "CONVERSATION_FEEDBACK_PLACEHOLDER": request.app.state.config.CONVERSATION_FEEDBACK_PLACEHOLDER,
    }


############################
# UpdateConfig
############################


class UpdateConfigForm(BaseModel):
    ENABLE_EVALUATION_ARENA_MODELS: Optional[bool] = None
    EVALUATION_ARENA_MODELS: Optional[list[dict]] = None
    ENABLE_MESSAGE_RATING: Optional[bool] = None
    ENABLE_FEEDBACK_LAYER2: Optional[bool] = None
    FEEDBACK_LAYER2_POSITIVE_TAGS: Optional[list[dict]] = None
    FEEDBACK_LAYER2_NEGATIVE_TAGS: Optional[list[dict]] = None
    ENABLE_FEEDBACK_LAYER3: Optional[bool] = None
    FEEDBACK_LAYER3_PROMPT: Optional[str] = None
    ENABLE_FEEDBACK_CATEGORY_TAGS: Optional[bool] = None
    ENABLE_CONVERSATION_FEEDBACK: Optional[bool] = None
    CONVERSATION_FEEDBACK_SCALE_MAX: Optional[int] = None
    CONVERSATION_FEEDBACK_HEADER: Optional[str] = None
    CONVERSATION_FEEDBACK_PLACEHOLDER: Optional[str] = None


@router.post("/config")
async def update_config(
    request: Request,
    form_data: UpdateConfigForm,
    user=Depends(get_admin_user),
):
    config = request.app.state.config
    if form_data.ENABLE_EVALUATION_ARENA_MODELS is not None:
        config.ENABLE_EVALUATION_ARENA_MODELS = form_data.ENABLE_EVALUATION_ARENA_MODELS
    if form_data.EVALUATION_ARENA_MODELS is not None:
        config.EVALUATION_ARENA_MODELS = form_data.EVALUATION_ARENA_MODELS
    if form_data.ENABLE_MESSAGE_RATING is not None:
        config.ENABLE_MESSAGE_RATING = form_data.ENABLE_MESSAGE_RATING
    if form_data.ENABLE_FEEDBACK_LAYER2 is not None:
        config.ENABLE_FEEDBACK_LAYER2 = form_data.ENABLE_FEEDBACK_LAYER2
    if form_data.FEEDBACK_LAYER2_POSITIVE_TAGS is not None:
        config.FEEDBACK_LAYER2_POSITIVE_TAGS = form_data.FEEDBACK_LAYER2_POSITIVE_TAGS
    if form_data.FEEDBACK_LAYER2_NEGATIVE_TAGS is not None:
        config.FEEDBACK_LAYER2_NEGATIVE_TAGS = form_data.FEEDBACK_LAYER2_NEGATIVE_TAGS
    if form_data.ENABLE_FEEDBACK_LAYER3 is not None:
        config.ENABLE_FEEDBACK_LAYER3 = form_data.ENABLE_FEEDBACK_LAYER3
    if form_data.FEEDBACK_LAYER3_PROMPT is not None:
        config.FEEDBACK_LAYER3_PROMPT = form_data.FEEDBACK_LAYER3_PROMPT
    if form_data.ENABLE_FEEDBACK_CATEGORY_TAGS is not None:
        config.ENABLE_FEEDBACK_CATEGORY_TAGS = form_data.ENABLE_FEEDBACK_CATEGORY_TAGS
    if form_data.ENABLE_CONVERSATION_FEEDBACK is not None:
        config.ENABLE_CONVERSATION_FEEDBACK = form_data.ENABLE_CONVERSATION_FEEDBACK
    if form_data.CONVERSATION_FEEDBACK_SCALE_MAX is not None:
        config.CONVERSATION_FEEDBACK_SCALE_MAX = form_data.CONVERSATION_FEEDBACK_SCALE_MAX
    if form_data.CONVERSATION_FEEDBACK_HEADER is not None:
        config.CONVERSATION_FEEDBACK_HEADER = form_data.CONVERSATION_FEEDBACK_HEADER
    if form_data.CONVERSATION_FEEDBACK_PLACEHOLDER is not None:
        config.CONVERSATION_FEEDBACK_PLACEHOLDER = form_data.CONVERSATION_FEEDBACK_PLACEHOLDER
    return {
        "ENABLE_EVALUATION_ARENA_MODELS": config.ENABLE_EVALUATION_ARENA_MODELS,
        "EVALUATION_ARENA_MODELS": config.EVALUATION_ARENA_MODELS,
        "ENABLE_MESSAGE_RATING": config.ENABLE_MESSAGE_RATING,
        "ENABLE_FEEDBACK_LAYER2": config.ENABLE_FEEDBACK_LAYER2,
        "FEEDBACK_LAYER2_POSITIVE_TAGS": config.FEEDBACK_LAYER2_POSITIVE_TAGS,
        "FEEDBACK_LAYER2_NEGATIVE_TAGS": config.FEEDBACK_LAYER2_NEGATIVE_TAGS,
        "ENABLE_FEEDBACK_LAYER3": config.ENABLE_FEEDBACK_LAYER3,
        "FEEDBACK_LAYER3_PROMPT": config.FEEDBACK_LAYER3_PROMPT,
        "ENABLE_FEEDBACK_CATEGORY_TAGS": config.ENABLE_FEEDBACK_CATEGORY_TAGS,
        "ENABLE_CONVERSATION_FEEDBACK": config.ENABLE_CONVERSATION_FEEDBACK,
        "CONVERSATION_FEEDBACK_SCALE_MAX": config.CONVERSATION_FEEDBACK_SCALE_MAX,
        "CONVERSATION_FEEDBACK_HEADER": config.CONVERSATION_FEEDBACK_HEADER,
        "CONVERSATION_FEEDBACK_PLACEHOLDER": config.CONVERSATION_FEEDBACK_PLACEHOLDER,
    }


@router.get("/feedbacks/all", response_model=list[FeedbackResponse])
async def get_all_feedbacks(user=Depends(get_admin_user)):
    feedbacks = Feedbacks.get_all_feedbacks()
    return feedbacks


@router.get("/feedbacks/all/ids", response_model=list[FeedbackIdResponse])
async def get_all_feedback_ids(user=Depends(get_admin_user)):
    feedbacks = Feedbacks.get_all_feedbacks()
    return feedbacks


@router.delete("/feedbacks/all")
async def delete_all_feedbacks(user=Depends(get_admin_user)):
    success = Feedbacks.delete_all_feedbacks()
    return success


@router.get("/feedbacks/all/export", response_model=list[FeedbackModel])
async def export_all_feedbacks(user=Depends(get_admin_user)):
    feedbacks = Feedbacks.get_all_feedbacks()
    return feedbacks


@router.get("/feedbacks/user", response_model=list[FeedbackUserResponse])
async def get_feedbacks(user=Depends(get_verified_user)):
    feedbacks = Feedbacks.get_feedbacks_by_user_id(user.id)
    return feedbacks


@router.delete("/feedbacks", response_model=bool)
async def delete_feedbacks(user=Depends(get_verified_user)):
    success = Feedbacks.delete_feedbacks_by_user_id(user.id)
    return success


PAGE_ITEM_COUNT = 30


@router.get("/feedbacks/list", response_model=FeedbackListResponse)
async def get_feedbacks(
    order_by: Optional[str] = None,
    direction: Optional[str] = None,
    page: Optional[int] = 1,
    user=Depends(get_admin_user),
):
    limit = PAGE_ITEM_COUNT

    page = max(1, page)
    skip = (page - 1) * limit

    filter = {}
    if order_by:
        filter["order_by"] = order_by
    if direction:
        filter["direction"] = direction

    result = Feedbacks.get_feedback_items(filter=filter, skip=skip, limit=limit)
    return result


@router.get("/feedback/conversation/{chat_id}")
async def get_conversation_feedback(chat_id: str, user=Depends(get_verified_user)):
    feedback = Feedbacks.get_conversation_feedback_by_chat_id_and_user_id(
        chat_id=chat_id, user_id=user.id
    )
    return feedback


@router.post("/feedback", response_model=FeedbackModel)
async def create_feedback(
    request: Request,
    form_data: FeedbackForm,
    user=Depends(get_verified_user),
):
    feedback = Feedbacks.insert_new_feedback(user_id=user.id, form_data=form_data)
    if not feedback:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(),
        )

    return feedback


@router.get("/feedback/{id}", response_model=FeedbackModel)
async def get_feedback_by_id(id: str, user=Depends(get_verified_user)):
    if user.role == "admin":
        feedback = Feedbacks.get_feedback_by_id(id=id)
    else:
        feedback = Feedbacks.get_feedback_by_id_and_user_id(id=id, user_id=user.id)

    if not feedback:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND
        )

    return feedback


@router.post("/feedback/{id}", response_model=FeedbackModel)
async def update_feedback_by_id(
    id: str, form_data: FeedbackForm, user=Depends(get_verified_user)
):
    if user.role == "admin":
        feedback = Feedbacks.update_feedback_by_id(id=id, form_data=form_data)
    else:
        feedback = Feedbacks.update_feedback_by_id_and_user_id(
            id=id, user_id=user.id, form_data=form_data
        )

    if not feedback:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND
        )

    return feedback


@router.delete("/feedback/{id}")
async def delete_feedback_by_id(id: str, user=Depends(get_verified_user)):
    if user.role == "admin":
        success = Feedbacks.delete_feedback_by_id(id=id)
    else:
        success = Feedbacks.delete_feedback_by_id_and_user_id(id=id, user_id=user.id)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND
        )

    return success
