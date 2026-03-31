import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel

from open_webui.config import ENABLE_DATA_EXPORT
from open_webui.services.export.service import ExportService
from open_webui.utils.auth import get_verified_user

log = logging.getLogger(__name__)

router = APIRouter()


class ExportStatusResponse(BaseModel):
    status: str  # 'none', 'ready'
    export_path: str | None = None
    created_at: float | None = None
    size: int | None = None


@router.post('/data')
async def trigger_data_export(
    request: Request,
    background_tasks: BackgroundTasks,
    user=Depends(get_verified_user),
):
    """Trigger an async data export for the current user."""
    if not ENABLE_DATA_EXPORT:
        raise HTTPException(status_code=403, detail='Data export is disabled')

    # Check if there's already an export in progress or ready
    existing = ExportService.get_active_export(user.id)
    if existing:
        return {
            'status': 'ready',
            'message': 'An export is already available for download',
            'export_path': existing['path'],
        }

    # Start background export
    background_tasks.add_task(ExportService.generate_export, user.id)

    return {
        'status': 'processing',
        'message': 'Export started. You will be notified when it is ready.',
    }


@router.get('/data/status')
async def get_export_status(
    request: Request,
    user=Depends(get_verified_user),
) -> ExportStatusResponse:
    """Check the status of the user's data export."""
    if not ENABLE_DATA_EXPORT:
        raise HTTPException(status_code=403, detail='Data export is disabled')

    existing = ExportService.get_active_export(user.id)
    if existing:
        return ExportStatusResponse(
            status='ready',
            export_path=existing['path'],
            created_at=existing['created_at'],
            size=existing['size'],
        )

    return ExportStatusResponse(status='none')


@router.delete('/data')
async def delete_export(
    request: Request,
    user=Depends(get_verified_user),
):
    """Delete the user's existing export file."""
    if not ENABLE_DATA_EXPORT:
        raise HTTPException(status_code=403, detail='Data export is disabled')

    from open_webui.services.export.service import EXPORT_DIR

    user_dir = EXPORT_DIR / user.id
    if user_dir.exists():
        for f in user_dir.glob('export-*.zip'):
            f.unlink()

    return {'status': 'deleted'}
