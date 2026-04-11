"""Data sovereignty warning audit logging router."""

from fastapi import APIRouter, Depends, Request
from open_webui.models.data_warnings import DataWarningLogForm, DataWarningLogModel, DataWarningLogs
from open_webui.utils.auth import get_verified_user

router = APIRouter()


@router.post('/accept', response_model=DataWarningLogModel)
async def log_data_warning_acceptance(
    request: Request,
    form: DataWarningLogForm,
    user=Depends(get_verified_user),
):
    """Log that a user accepted a data sovereignty warning."""
    if not request.app.state.config.ENABLE_DATA_WARNINGS:
        # Silently succeed — don't break the send flow if feature is off
        return DataWarningLogModel(
            id='noop',
            user_id=user.id,
            chat_id=form.chat_id,
            model_id=form.model_id,
            capabilities=form.capabilities,
            warning_message=form.warning_message,
            created_at=0,
        )
    return DataWarningLogs.insert_log(user_id=user.id, form=form)
